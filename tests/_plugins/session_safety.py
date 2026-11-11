from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import faulthandler
import os
from pathlib import Path
import queue
import selectors
import signal
import subprocess
import tempfile
import threading
import time
from types import MethodType
from typing import Any, Callable, NoReturn


# PROTOCOL: pytest session safety
# - Bound xdist execnet/bootstrap and worker-ready handshakes. Ten minutes is
#   deliberately generous for cold engine-stack import on a loaded 28-core
#   Studio: this catches an infinite wait, not a slow healthy start.
# - Preserve Python stacks, the ps tree, and a native sample where available
#   before terminating the timed-out child's complete descendant tree.
# - No collection-stall or RSS killer is installed. Controller CPU cannot
#   discriminate healthy xdist worker progress, and no healthy full-run RSS
#   headroom measurement exists yet.
CHILD_HANDSHAKE_TIMEOUT_SECONDS = 600.0
CHILD_TERMINATE_GRACE_SECONDS = 5.0
PROCESS_TREE_READ_TIMEOUT_SECONDS = 10.0
HANDSHAKE_EXIT_CODE = 87


class SessionChildHandshakeTimeout(TimeoutError):
    def __init__(self, *, child: str, phase: str, timeout_seconds: float) -> None:
        self.child = child
        self.phase = phase
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"child={child} phase={phase} exceeded {timeout_seconds:.1f}s; "
            "diagnostic-first process-tree terminate/kill escalation attempted"
        )


@dataclass(frozen=True)
class ProcessTreeSnapshot:
    rss_bytes: int
    text: str


@dataclass(frozen=True)
class _ChildHandshake:
    child: str
    phase: str
    process: Any
    deadline: float
    timeout_seconds: float


def _terminate_process(process: Any, grace_seconds: float) -> None:
    if process is None:
        return
    if process.poll() is not None:
        raise RuntimeError(
            "gateway root exited before process-group cleanup; "
            "refusing an unverified PGID kill"
        )
    try:
        process.terminate()
    except BaseException:
        pass
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    except BaseException:
        pass
    try:
        process.kill()
    except BaseException:
        return
    try:
        process.wait(timeout=grace_seconds)
    except BaseException:
        pass


def _isolated_process_group(process: Any) -> int | None:
    if not isinstance(process, subprocess.Popen):
        return None
    pid = process.pid
    retained_pgid = getattr(process, "_regolith_isolated_pgid", None)
    if retained_pgid is not None:
        if (
            not isinstance(retained_pgid, int)
            or retained_pgid != pid
            or retained_pgid == os.getpgrp()
        ):
            return None
        try:
            live_pgid = os.getpgid(pid)
        except ProcessLookupError:
            return retained_pgid
        except OSError:
            return None
        return retained_pgid if live_pgid == retained_pgid else None
    try:
        pgid = os.getpgid(pid)
    except (OSError, ProcessLookupError):
        return None
    if pgid != pid or pgid == os.getpgrp():
        return None
    return pgid


def _process_group_exists(pgid: int) -> bool | None:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    return True


def _terminate_process_group(
    process: subprocess.Popen[Any],
    pgid: int,
    grace_seconds: float,
) -> None:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    group_exists = _process_group_exists(pgid)
    while time.monotonic() < deadline and group_exists is True:
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        group_exists = _process_group_exists(pgid)
    if group_exists is not False:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            if group_exists is not None:
                raise
    try:
        process.wait(timeout=grace_seconds)
    except BaseException:
        pass
    deadline = time.monotonic() + grace_seconds
    group_exists = _process_group_exists(pgid)
    while time.monotonic() < deadline and group_exists is True:
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        group_exists = _process_group_exists(pgid)
    if group_exists is True:
        raise RuntimeError(f"process group pgid={pgid} survived SIGKILL")


def _terminate_process_tree(process: Any, grace_seconds: float) -> None:
    if process is None:
        return

    pgid = _isolated_process_group(process)
    if pgid is None:
        raise RuntimeError(
            "refusing single-PID fatal cleanup for a non-isolated process"
        )
    _terminate_process_group(process, pgid, grace_seconds)


def _gateway_process(gateway: Any) -> Any:
    return getattr(getattr(gateway, "_io", None), "popen", None)


def gateway_child_label(gateway: Any) -> str:
    process = _gateway_process(gateway)
    pid = getattr(process, "pid", "unknown")
    return f"xdist-{getattr(gateway, 'id', 'unknown')} pid={pid}"


def _bootstrap_child_label(io: Any, spec: Any) -> str:
    process = getattr(io, "popen", None)
    pid = getattr(process, "pid", "unknown")
    return f"xdist-{getattr(spec, 'id', 'unknown')} pid={pid}"


def _append_native_sample(path: Path, process: Any) -> None:
    sample = Path("/usr/bin/sample")
    pid = getattr(process, "pid", None)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\nNATIVE PROCESS SAMPLE\n")
        if not sample.is_file() or not isinstance(pid, int):
            handle.write("sample-unavailable\n")
            return
        sampler = subprocess.Popen(
            [str(sample), str(pid), "1", "1"],
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            sampler.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            _terminate_process(sampler, 1.0)
            handle.write("sample-timeout\n")


def preserve_handshake_evidence(
    reason: BaseException,
    process: Any,
) -> Path:
    try:
        snapshot = read_process_tree()
    except BaseException:
        snapshot = None
    path = write_watchdog_diagnostic(reason, snapshot)
    try:
        _append_native_sample(path, process)
    except BaseException as exc:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\nsample-unavailable={type(exc).__name__}: {exc}\n"
            )
    return path


def _raise_handshake_timeout(
    *,
    child: str,
    phase: str,
    process: Any,
    timeout_seconds: float,
    terminate_grace_seconds: float,
    evidence_preserver: Callable[[BaseException, Any], Path],
    process_tree_terminator: Callable[[Any, float], None],
    cause: BaseException | None = None,
) -> NoReturn:
    reason = SessionChildHandshakeTimeout(
        child=child,
        phase=phase,
        timeout_seconds=timeout_seconds,
    )
    diagnostic_path: Path | None = None
    try:
        diagnostic_path = evidence_preserver(reason, process)
    except BaseException as exc:
        reason.add_note(
            f"diagnostic preservation failed: {type(exc).__name__}: {exc}"
        )
    try:
        process_tree_terminator(process, terminate_grace_seconds)
    except BaseException as exc:
        reason.add_note(
            f"process-tree cleanup failed: {type(exc).__name__}: {exc}"
        )
        if diagnostic_path is not None:
            try:
                with diagnostic_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"\nprocess-tree-cleanup-failed="
                        f"{type(exc).__name__}: {exc}\n"
                    )
            except BaseException:
                pass
    if diagnostic_path is not None:
        reason.add_note(f"diagnostic={diagnostic_path}")
    if cause is None:
        raise reason
    raise reason from cause


def _bounded_bootstrap_read(
    io: Any,
    original_read: Callable[[int], bytes],
    numbytes: int,
    *,
    spec: Any,
    timeout_seconds: float = CHILD_HANDSHAKE_TIMEOUT_SECONDS,
    terminate_grace_seconds: float = CHILD_TERMINATE_GRACE_SECONDS,
    evidence_preserver: Callable[
        [BaseException, Any], Path
    ] = preserve_handshake_evidence,
    process_tree_terminator: Callable[
        [Any, float], None
    ] = _terminate_process_tree,
) -> bytes:
    child = _bootstrap_child_label(io, spec)
    process = getattr(io, "popen", None)
    infile = getattr(io, "infile", None)
    if infile is not None and hasattr(infile, "fileno"):
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(infile, selectors.EVENT_READ)
                ready = selector.select(timeout_seconds)
        except (OSError, ValueError):
            ready = None
        if ready == []:
            _raise_handshake_timeout(
                child=child,
                phase="execnet-bootstrap-read",
                process=process,
                timeout_seconds=timeout_seconds,
                terminate_grace_seconds=terminate_grace_seconds,
                evidence_preserver=evidence_preserver,
                process_tree_terminator=process_tree_terminator,
            )
        if ready is not None:
            return original_read(numbytes)

    result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def _read() -> None:
        try:
            result.put((True, original_read(numbytes)))
        except BaseException as exc:
            result.put((False, exc))

    reader = threading.Thread(
        target=_read,
        name=f"execnet-bootstrap-{getattr(spec, 'id', 'unknown')}",
        daemon=True,
    )
    reader.start()
    try:
        ok, value = result.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        _raise_handshake_timeout(
            child=child,
            phase="execnet-bootstrap-read",
            process=process,
            timeout_seconds=timeout_seconds,
            terminate_grace_seconds=terminate_grace_seconds,
            evidence_preserver=evidence_preserver,
            process_tree_terminator=process_tree_terminator,
            cause=exc,
        )
    if ok:
        return value
    raise value


def install_bounded_execnet_bootstrap() -> None:
    import execnet.gateway_bootstrap as gateway_bootstrap
    import execnet.gateway_io as gateway_io

    if not getattr(gateway_io, "_regolith_isolated_process_group", False):
        # execnet exposes no Popen kwargs. An isolated session makes the PGID
        # a sound cleanup identity for the gateway and every engine descendant.
        def _isolated_popen_init(
            self: Any,
            args: Any,
            execmodel: Any,
        ) -> None:
            pipe = execmodel.subprocess.PIPE
            self.popen = execmodel.subprocess.Popen(
                args,
                stdout=pipe,
                stdin=pipe,
                start_new_session=True,
            )
            pgid = os.getpgid(self.popen.pid)
            if pgid != self.popen.pid or pgid == os.getpgrp():
                raise RuntimeError(
                    f"gateway pid={self.popen.pid} did not enter its own "
                    f"process group (pgid={pgid})"
                )
            self.popen._regolith_isolated_pgid = pgid
            gateway_io.Popen2IO.__init__(
                self,
                self.popen.stdin,
                self.popen.stdout,
                execmodel=execmodel,
            )

        gateway_io.Popen2IOMaster.__init__ = _isolated_popen_init
        gateway_io._regolith_isolated_process_group = True

    if getattr(gateway_bootstrap, "_regolith_bounded_bootstrap", False):
        return
    original_bootstrap = gateway_bootstrap.bootstrap

    def _bounded_bootstrap(io: Any, spec: Any) -> Any:
        original_read = io.read

        def _bounded_read(numbytes: int) -> bytes:
            return _bounded_bootstrap_read(
                io,
                original_read,
                numbytes,
                spec=spec,
            )

        io.read = _bounded_read
        try:
            return original_bootstrap(io, spec)
        finally:
            io.read = original_read

    gateway_bootstrap.bootstrap = _bounded_bootstrap
    gateway_bootstrap._regolith_bounded_bootstrap = True


def bounded_gateway_rinfo(
    gateway: Any,
    update: bool = False,
    *,
    timeout_seconds: float = CHILD_HANDSHAKE_TIMEOUT_SECONDS,
    terminate_grace_seconds: float = CHILD_TERMINATE_GRACE_SECONDS,
    evidence_preserver: Callable[
        [BaseException, Any], Path
    ] = preserve_handshake_evidence,
    process_tree_terminator: Callable[
        [Any, float], None
    ] = _terminate_process_tree,
) -> Any:
    from execnet.gateway import RInfo, rinfo_source

    if not update and hasattr(gateway, "_cache_rinfo"):
        return gateway._cache_rinfo

    child = gateway_child_label(gateway)
    process = _gateway_process(gateway)
    channel = gateway.remote_exec(rinfo_source)
    deadline = time.monotonic() + timeout_seconds
    try:
        try:
            payload = channel.receive(timeout=max(0.0, deadline - time.monotonic()))
        except channel.TimeoutError as exc:
            _raise_handshake_timeout(
                child=child,
                phase="execnet-rinfo-receive",
                process=process,
                timeout_seconds=timeout_seconds,
                terminate_grace_seconds=terminate_grace_seconds,
                evidence_preserver=evidence_preserver,
                process_tree_terminator=process_tree_terminator,
                cause=exc,
            )

        gateway._cache_rinfo = RInfo(payload)
        try:
            channel.waitclose(timeout=max(0.0, deadline - time.monotonic()))
        except channel.TimeoutError as exc:
            _raise_handshake_timeout(
                child=child,
                phase="execnet-rinfo-close",
                process=process,
                timeout_seconds=timeout_seconds,
                terminate_grace_seconds=terminate_grace_seconds,
                evidence_preserver=evidence_preserver,
                process_tree_terminator=process_tree_terminator,
                cause=exc,
            )
    except SessionChildHandshakeTimeout:
        try:
            channel.close()
        except BaseException:
            pass
        raise
    return gateway._cache_rinfo


def install_bounded_gateway_rinfo(gateway: Any) -> None:
    if getattr(gateway, "_regolith_bounded_rinfo", False):
        return

    def _bounded_rinfo(self: Any, update: bool = False) -> Any:
        return bounded_gateway_rinfo(self, update)

    gateway._rinfo = MethodType(_bounded_rinfo, gateway)
    gateway._regolith_bounded_rinfo = True


def read_process_tree(
    root_pid: int | None = None,
    *,
    timeout_seconds: float = PROCESS_TREE_READ_TIMEOUT_SECONDS,
) -> ProcessTreeSnapshot:
    root_pid = os.getpid() if root_pid is None else root_pid
    process = subprocess.Popen(
        [
            "ps",
            "-axo",
            "pid=,ppid=,rss=,etime=,time=,stat=,command=",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process, CHILD_TERMINATE_GRACE_SECONDS)
        raise SessionChildHandshakeTimeout(
            child=f"ps-process-tree pid={process.pid}",
            phase="process-tree-read",
            timeout_seconds=timeout_seconds,
        ) from exc
    if process.returncode != 0:
        raise RuntimeError(
            f"ps process-tree inspection failed rc={process.returncode}: {stderr.strip()}"
        )

    return _parse_process_tree(stdout, root_pid)


def _parse_process_tree(stdout: str, root_pid: int) -> ProcessTreeSnapshot:
    rows: dict[int, tuple[int, int, str]] = {}
    for raw_line in stdout.splitlines():
        fields = raw_line.strip().split(None, 6)
        if len(fields) != 7:
            continue
        try:
            pid, ppid, rss_kib = map(int, fields[:3])
        except ValueError:
            continue
        rows[pid] = (ppid, rss_kib, raw_line)

    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (ppid, _rss_kib, _line) in rows.items():
            if pid not in selected and ppid in selected:
                selected.add(pid)
                changed = True
    if root_pid not in rows:
        raise RuntimeError(f"ps process-tree inspection omitted root pid={root_pid}")

    lines = [rows[pid][2] for pid in sorted(selected) if pid in rows]
    rss_bytes = sum(rows[pid][1] for pid in selected if pid in rows) * 1024
    return ProcessTreeSnapshot(
        rss_bytes=rss_bytes,
        text=(
            "PID PPID RSS_KIB ELAPSED CPU STAT COMMAND\n"
            + "\n".join(lines)
            + "\n"
        ),
    )


def write_watchdog_diagnostic(
    reason: BaseException,
    snapshot: ProcessTreeSnapshot | None,
) -> Path:
    path = (
        Path(tempfile.gettempdir())
        / f"regolith-pytest-watchdog-{os.getpid()}.log"
    )
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            f"timestamp={datetime.now(timezone.utc).isoformat()}\n"
            f"pid={os.getpid()}\n"
            f"reason={type(reason).__name__}: {reason}\n\n"
            "PYTHON THREAD STACKS\n"
        )
        faulthandler.dump_traceback(file=handle, all_threads=True)
        handle.write("\nPROCESS TREE\n")
        if snapshot is None:
            try:
                snapshot = read_process_tree()
            except BaseException as exc:
                handle.write(
                    f"process-tree-unavailable={type(exc).__name__}: {exc}\n"
                )
        if snapshot is not None:
            handle.write(
                f"tree_rss_bytes={snapshot.rss_bytes}\n{snapshot.text}"
            )
    return path


class SessionWatchdog:
    def __init__(
        self,
        *,
        wall_clock: Callable[[], float] = time.monotonic,
        process_tree_reader: Callable[[], ProcessTreeSnapshot] = read_process_tree,
        diagnostic_writer: Callable[
            [BaseException, ProcessTreeSnapshot | None], Path
        ] = write_watchdog_diagnostic,
        process_tree_terminator: Callable[
            [Any, float], None
        ] = _terminate_process_tree,
        abort: Callable[[int], Any] = os._exit,
        loud_writer: Callable[[bytes], Any] | None = None,
    ) -> None:
        self._wall_clock = wall_clock
        self._process_tree_reader = process_tree_reader
        self._diagnostic_writer = diagnostic_writer
        self._process_tree_terminator = process_tree_terminator
        self._abort = abort
        self._loud_writer = loud_writer or (lambda data: os.write(2, data))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._handshakes: dict[str, _ChildHandshake] = {}
        self._tripped = False
        self._trip_pending = False
        self._stopped = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="pytest-session-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._trip_pending = False
            self._handshakes.clear()
        self._stop.set()
        self._wake.set()
        if (
            self._thread is not None
            and self._thread is not threading.current_thread()
        ):
            self._thread.join(timeout=1.0)

    def arm_child_handshake(
        self,
        *,
        key: str,
        child: str,
        phase: str,
        process: Any,
        timeout_seconds: float = CHILD_HANDSHAKE_TIMEOUT_SECONDS,
    ) -> None:
        with self._lock:
            if self._stopped or self._tripped:
                return
            self._handshakes[key] = _ChildHandshake(
                child=child,
                phase=phase,
                process=process,
                deadline=self._wall_clock() + timeout_seconds,
                timeout_seconds=timeout_seconds,
            )
        self._wake.set()

    def disarm_child_handshake(self, key: str) -> None:
        with self._lock:
            self._handshakes.pop(key, None)
        self._wake.set()

    def check_once(self) -> BaseException | None:
        now = self._wall_clock()
        with self._lock:
            if self._tripped or self._trip_pending or self._stopped:
                return None
            expired = next(
                (
                    (key, handshake)
                    for key, handshake in self._handshakes.items()
                    if handshake.deadline <= now
                ),
                None,
            )
            if expired is not None:
                self._handshakes.pop(expired[0], None)

        if expired is not None:
            handshake = expired[1]
            reason = SessionChildHandshakeTimeout(
                child=handshake.child,
                phase=handshake.phase,
                timeout_seconds=handshake.timeout_seconds,
            )
            try:
                snapshot = self._process_tree_reader()
            except BaseException:
                snapshot = None
            self._trip(
                reason,
                snapshot,
                HANDSHAKE_EXIT_CODE,
                cleanup=lambda: self._process_tree_terminator(
                    handshake.process,
                    CHILD_TERMINATE_GRACE_SECONDS,
                ),
                sample_process=handshake.process,
            )
            return reason
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            now = self._wall_clock()
            with self._lock:
                next_deadline = min(
                    (
                        handshake.deadline
                        for handshake in self._handshakes.values()
                    ),
                    default=None,
                )
            timeout = (
                None
                if next_deadline is None
                else max(0.0, next_deadline - now)
            )
            self._wake.wait(timeout=timeout)
            self._wake.clear()
            if self._stop.is_set():
                return
            self.check_once()

    def _trip(
        self,
        reason: BaseException,
        snapshot: ProcessTreeSnapshot | None,
        exit_code: int,
        cleanup: Callable[[], None] | None = None,
        sample_process: Any = None,
    ) -> None:
        with self._lock:
            if self._tripped or self._trip_pending or self._stopped:
                return
            self._trip_pending = True
        diagnostic_line: str
        diagnostic_path: Path | None = None
        try:
            diagnostic_path = self._diagnostic_writer(reason, snapshot)
            diagnostic_line = f"diagnostic={diagnostic_path}"
        except BaseException as exc:
            diagnostic_line = (
                f"diagnostic-unavailable={type(exc).__name__}: {exc}"
            )
        if (
            diagnostic_path is not None
            and diagnostic_path.is_file()
            and sample_process is not None
        ):
            try:
                _append_native_sample(diagnostic_path, sample_process)
            except BaseException as exc:
                try:
                    with diagnostic_path.open("a", encoding="utf-8") as handle:
                        handle.write(
                            f"\nsample-unavailable="
                            f"{type(exc).__name__}: {exc}\n"
                        )
                except BaseException:
                    pass
        with self._lock:
            if self._stopped:
                self._trip_pending = False
                return
        if cleanup is not None:
            try:
                cleanup()
            except BaseException as exc:
                cleanup_error = f"{type(exc).__name__}: {exc}"
                diagnostic_line += f" cleanup-failed={cleanup_error}"
                if diagnostic_path is not None:
                    try:
                        with diagnostic_path.open("a", encoding="utf-8") as handle:
                            handle.write(
                                f"\nprocess-tree-cleanup-failed="
                                f"{cleanup_error}\n"
                            )
                    except BaseException:
                        pass
        with self._lock:
            if self._stopped:
                self._trip_pending = False
                return
        try:
            self._loud_writer(
                (
                    f"\n{type(reason).__name__}: {reason}\n"
                    f"{diagnostic_line}\n"
                ).encode("utf-8", errors="replace")
            )
        except BaseException:
            pass
        with self._lock:
            if self._stopped:
                self._trip_pending = False
                return
            self._tripped = True
            self._trip_pending = False
            self._abort(exit_code)
