from __future__ import annotations

import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

import conftest as project_conftest
import _pytest_session_safety as session_safety
from _pytest_session_safety import (
    CHILD_HANDSHAKE_TIMEOUT_SECONDS,
    HANDSHAKE_EXIT_CODE,
    ProcessTreeSnapshot,
    SessionChildHandshakeTimeout,
    SessionWatchdog,
    _bounded_bootstrap_read,
    _parse_process_tree,
    _terminate_process_tree,
    bounded_gateway_rinfo,
    install_bounded_execnet_bootstrap,
    write_watchdog_diagnostic,
)


class _Clock:
    def __init__(self) -> None:
        self.wall = 0.0
        self.cpu = 0.0

    def wall_time(self) -> float:
        return self.wall


class _NeverExitProcess:
    pid = 4242

    def __init__(self) -> None:
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1

    def wait(self, timeout: float) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired("synthetic-child", timeout)
        return -9


class _BrokenTerminateProcess(_NeverExitProcess):
    def terminate(self) -> None:
        self.terminate_calls += 1
        raise OSError("synthetic terminate failure")

    def wait(self, timeout: float) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise OSError("synthetic wait failure")
        return -9


class _NeverRespondingChannel:
    class TimeoutError(Exception):
        pass

    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.closed = False
        self.receive_timeout: float | None = None
        self.waitclose_timeout: float | None = None

    def receive(self, timeout: float | None = None) -> dict[str, object]:
        self.receive_timeout = timeout
        if self.phase == "receive":
            raise self.TimeoutError
        return {}

    def waitclose(self, timeout: float | None = None) -> None:
        self.waitclose_timeout = timeout
        if self.phase == "waitclose":
            raise self.TimeoutError

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("phase", "message_phase"),
    [
        ("receive", "execnet-rinfo-receive"),
        ("waitclose", "execnet-rinfo-close"),
    ],
)
def test_never_responding_session_child_times_out_and_is_killed(
    phase: str,
    message_phase: str,
) -> None:
    process = _NeverExitProcess()
    channel = _NeverRespondingChannel(phase)
    gateway = SimpleNamespace(
        id="gw-never",
        _io=SimpleNamespace(popen=process),
        remote_exec=lambda _source: channel,
    )
    events: list[str] = []

    def _preserve(_reason: BaseException, _process: object) -> Path:
        events.append("evidence")
        return Path("/tmp/synthetic-watchdog.log")

    def _terminate(child: object, grace_seconds: float) -> None:
        events.append("cleanup")
        session_safety._terminate_process(child, grace_seconds)

    with pytest.raises(SessionChildHandshakeTimeout) as exc_info:
        bounded_gateway_rinfo(
            gateway,
            timeout_seconds=0.01,
            terminate_grace_seconds=0.01,
            evidence_preserver=_preserve,
            process_tree_terminator=_terminate,
        )

    message = str(exc_info.value)
    assert "gw-never" in message
    assert "pid=4242" in message
    assert message_phase in message
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert channel.closed is True
    assert events == ["evidence", "cleanup"]


def test_never_responding_execnet_bootstrap_read_is_bounded() -> None:
    read_fd, write_fd = os.pipe()
    infile = os.fdopen(read_fd, "rb", buffering=0)
    process = _NeverExitProcess()
    io = SimpleNamespace(infile=infile, popen=process)
    spec = SimpleNamespace(id="gw-bootstrap")
    events: list[str] = []

    def _preserve(_reason: BaseException, _process: object) -> Path:
        events.append("evidence")
        return Path("/tmp/synthetic-watchdog.log")

    def _terminate(child: object, grace_seconds: float) -> None:
        events.append("cleanup")
        session_safety._terminate_process(child, grace_seconds)

    try:
        with pytest.raises(SessionChildHandshakeTimeout) as exc_info:
            _bounded_bootstrap_read(
                io,
                infile.read,
                1,
                spec=spec,
                timeout_seconds=0.01,
                terminate_grace_seconds=0.01,
                evidence_preserver=_preserve,
                process_tree_terminator=_terminate,
            )
    finally:
        os.close(write_fd)
        infile.close()

    assert "xdist-gw-bootstrap pid=4242" in str(exc_info.value)
    assert "execnet-bootstrap-read" in str(exc_info.value)
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert events == ["evidence", "cleanup"]


def test_termination_failure_does_not_mask_typed_handshake_error() -> None:
    process = _BrokenTerminateProcess()
    channel = _NeverRespondingChannel("receive")
    gateway = SimpleNamespace(
        id="gw-broken-terminate",
        _io=SimpleNamespace(popen=process),
        remote_exec=lambda _source: channel,
    )

    with pytest.raises(SessionChildHandshakeTimeout) as exc_info:
        bounded_gateway_rinfo(
            gateway,
            timeout_seconds=0.01,
            terminate_grace_seconds=0.01,
            evidence_preserver=lambda _reason, _process: Path(
                "/tmp/synthetic-watchdog.log"
            ),
            process_tree_terminator=session_safety._terminate_process,
        )

    assert "gw-broken-terminate" in str(exc_info.value)
    assert process.kill_calls == 1


def _watchdog(
    clock: _Clock,
    *,
    loud: list[bytes] | None = None,
) -> tuple[SessionWatchdog, list[BaseException], list[int]]:
    reasons: list[BaseException] = []
    exits: list[int] = []
    watchdog = SessionWatchdog(
        wall_clock=clock.wall_time,
        process_tree_reader=lambda: ProcessTreeSnapshot(1, "ps tree"),
        diagnostic_writer=lambda reason, _snapshot: reasons.append(reason)
        or Path("/tmp/synthetic-watchdog.log"),
        process_tree_terminator=session_safety._terminate_process,
        abort=lambda code: exits.append(code),
        loud_writer=(loud.append if loud is not None else lambda _data: None),
    )
    return watchdog, reasons, exits


def test_controller_cpu_silence_never_trips_without_worker_handshake_timeout() -> None:
    clock = _Clock()
    watchdog, reasons, exits = _watchdog(clock)

    clock.wall = 24 * 60 * 60
    clock.cpu = 0.0
    watchdog.check_once()

    assert reasons == []
    assert exits == []


def test_uncalibrated_high_rss_is_not_an_abort_signal() -> None:
    clock = _Clock()
    process_tree_reads: list[bool] = []
    reasons: list[BaseException] = []
    exits: list[int] = []
    watchdog = SessionWatchdog(
        wall_clock=clock.wall_time,
        process_tree_reader=lambda: process_tree_reads.append(True)
        or ProcessTreeSnapshot(256 * 1024**3, "ps tree"),
        diagnostic_writer=lambda reason, _snapshot: reasons.append(reason)
        or Path("/tmp/synthetic-watchdog.log"),
        abort=lambda code: exits.append(code),
        loud_writer=lambda _data: None,
    )

    clock.wall = 24 * 60 * 60
    watchdog.check_once()

    assert process_tree_reads == []
    assert reasons == []
    assert exits == []


def test_only_expired_worker_handshake_trips() -> None:
    clock = _Clock()
    watchdog, reasons, exits = _watchdog(clock)
    expired = _NeverExitProcess()
    waiting = _NeverExitProcess()
    watchdog.arm_child_handshake(
        key="gw-expired",
        child="xdist-gw-expired pid=4242",
        phase="worker-ready",
        process=expired,
        timeout_seconds=1.0,
    )
    watchdog.arm_child_handshake(
        key="gw-waiting",
        child="xdist-gw-waiting pid=4242",
        phase="worker-ready",
        process=waiting,
        timeout_seconds=10.0,
    )

    clock.wall = 2.0
    watchdog.check_once()

    assert len(reasons) == 1
    assert isinstance(reasons[0], SessionChildHandshakeTimeout)
    assert expired.terminate_calls == 1
    assert waiting.terminate_calls == 0
    assert exits == [HANDSHAKE_EXIT_CODE]


def test_process_tree_rss_counts_only_controller_descendants() -> None:
    snapshot = _parse_process_tree(
        "100 1 100 00:01 0:00 S pytest\n"
        "101 100 200 00:01 0:00 S python worker\n"
        "102 101 300 00:01 0:00 S engine child\n"
        "999 1 9000 00:01 0:00 S unrelated\n",
        root_pid=100,
    )

    assert snapshot.rss_bytes == 600 * 1024
    assert "100 1 100" in snapshot.text
    assert "101 100 200" in snapshot.text
    assert "102 101 300" in snapshot.text
    assert "999 1 9000" not in snapshot.text


def test_watchdog_diagnostic_contains_thread_stacks_and_process_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        session_safety.tempfile,
        "gettempdir",
        lambda: str(tmp_path),
    )
    reason = SessionChildHandshakeTimeout(
        child="xdist-gw0 pid=100",
        phase="worker-ready",
        timeout_seconds=CHILD_HANDSHAKE_TIMEOUT_SECONDS,
    )

    path = write_watchdog_diagnostic(
        reason,
        ProcessTreeSnapshot(1234, "PID PPID RSS_KIB\n100 1 1234\n"),
    )

    diagnostic = path.read_text(encoding="utf-8")
    assert "reason=SessionChildHandshakeTimeout" in diagnostic
    assert "PYTHON THREAD STACKS" in diagnostic
    assert "PROCESS TREE" in diagnostic
    assert "tree_rss_bytes=1234" in diagnostic
    assert "100 1 1234" in diagnostic


def test_worker_ready_handshake_deadline_kills_child_and_aborts() -> None:
    clock = _Clock()
    loud: list[bytes] = []
    watchdog, reasons, exits = _watchdog(clock, loud=loud)
    process = _NeverExitProcess()
    watchdog.arm_child_handshake(
        key="gw-never",
        child="xdist-gw-never pid=4242",
        phase="worker-ready",
        process=process,
        timeout_seconds=1.0,
    )

    clock.wall = 1.1
    watchdog.check_once()

    assert len(reasons) == 1
    assert isinstance(reasons[0], SessionChildHandshakeTimeout)
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert exits == [HANDSHAKE_EXIT_CODE]
    assert b"SessionChildHandshakeTimeout" in loud[0]
    assert b"xdist-gw-never pid=4242" in loud[0]


def test_worker_ready_preserves_native_sample_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock = _Clock()
    events: list[str] = []
    diagnostic_path = tmp_path / "worker-ready.log"

    def _write_diagnostic(
        _reason: BaseException,
        _snapshot: ProcessTreeSnapshot | None,
    ) -> Path:
        events.append("diagnostic")
        diagnostic_path.write_text("diagnostic\n", encoding="utf-8")
        return diagnostic_path

    monkeypatch.setattr(
        session_safety,
        "_append_native_sample",
        lambda _path, _process: events.append("native-sample"),
    )
    watchdog = SessionWatchdog(
        wall_clock=clock.wall_time,
        process_tree_reader=lambda: ProcessTreeSnapshot(1, "ps tree"),
        diagnostic_writer=_write_diagnostic,
        process_tree_terminator=lambda _process, _grace: events.append(
            "cleanup"
        ),
        abort=lambda _code: events.append("abort"),
        loud_writer=lambda _data: None,
    )
    watchdog.arm_child_handshake(
        key="gw-native-sample",
        child="xdist-gw-native-sample pid=4242",
        phase="worker-ready",
        process=_NeverExitProcess(),
        timeout_seconds=1.0,
    )

    clock.wall = 1.1
    watchdog.check_once()

    assert events == ["diagnostic", "native-sample", "cleanup", "abort"]


def test_worker_ready_handshake_is_disarmed_on_ready() -> None:
    clock = _Clock()
    watchdog, reasons, exits = _watchdog(clock)
    process = _NeverExitProcess()
    watchdog.arm_child_handshake(
        key="gw-ready",
        child="xdist-gw-ready pid=4242",
        phase="worker-ready",
        process=process,
        timeout_seconds=1.0,
    )
    watchdog.disarm_child_handshake("gw-ready")

    clock.wall = 2.0
    watchdog.check_once()

    assert reasons == []
    assert process.terminate_calls == 0
    assert process.kill_calls == 0
    assert exits == []


def test_session_finish_suppresses_in_flight_watchdog_trip() -> None:
    clock = _Clock()
    entered = threading.Event()
    release = threading.Event()
    reasons: list[BaseException] = []
    exits: list[int] = []

    def _blocked_process_tree_read() -> ProcessTreeSnapshot:
        entered.set()
        release.wait(timeout=2.0)
        return ProcessTreeSnapshot(48 * 1024**3, "ps tree")

    watchdog = SessionWatchdog(
        wall_clock=clock.wall_time,
        process_tree_reader=_blocked_process_tree_read,
        diagnostic_writer=lambda reason, _snapshot: reasons.append(reason)
        or Path("/tmp/synthetic-watchdog.log"),
        process_tree_terminator=session_safety._terminate_process,
        abort=lambda code: exits.append(code),
        loud_writer=lambda _data: None,
    )
    process = _NeverExitProcess()
    watchdog.arm_child_handshake(
        key="gw-stop-during-tree-read",
        child="xdist-gw-stop-during-tree-read pid=4242",
        phase="worker-ready",
        process=process,
        timeout_seconds=1.0,
    )
    clock.wall = 1.1
    checker = threading.Thread(
        target=watchdog.check_once,
        daemon=True,
    )
    checker.start()
    assert entered.wait(timeout=1.0)
    watchdog.stop()
    release.set()
    checker.join(timeout=1.0)

    assert checker.is_alive() is False
    assert reasons == []
    assert process.terminate_calls == 0
    assert exits == []


def test_session_finish_during_diagnostic_suppresses_late_abort() -> None:
    clock = _Clock()
    exits: list[int] = []
    holder: list[SessionWatchdog] = []

    def _finish_session(
        _reason: BaseException,
        _snapshot: ProcessTreeSnapshot | None,
    ) -> Path:
        holder[0].stop()
        return Path("/tmp/synthetic-watchdog.log")

    watchdog = SessionWatchdog(
        wall_clock=clock.wall_time,
        process_tree_reader=lambda: ProcessTreeSnapshot(1, "ps tree"),
        diagnostic_writer=_finish_session,
        process_tree_terminator=session_safety._terminate_process,
        abort=lambda code: exits.append(code),
        loud_writer=lambda _data: None,
    )
    holder.append(watchdog)
    process = _NeverExitProcess()
    watchdog.arm_child_handshake(
        key="gw-stop-during-diagnostic",
        child="xdist-gw-stop-during-diagnostic pid=4242",
        phase="worker-ready",
        process=process,
        timeout_seconds=1.0,
    )
    clock.wall = 1.1
    watchdog.check_once()

    assert process.terminate_calls == 0
    assert exits == []


def test_stop_return_disarms_abort_blocked_in_loud_writer() -> None:
    clock = _Clock()
    loud_entered = threading.Event()
    loud_release = threading.Event()
    exits: list[int] = []
    watchdog = SessionWatchdog(
        wall_clock=clock.wall_time,
        process_tree_reader=lambda: ProcessTreeSnapshot(1, "ps tree"),
        diagnostic_writer=lambda _reason, _snapshot: Path(
            "/tmp/synthetic-watchdog.log"
        ),
        process_tree_terminator=session_safety._terminate_process,
        abort=lambda code: exits.append(code),
        loud_writer=lambda _data: (
            loud_entered.set(),
            loud_release.wait(timeout=2.0),
        ),
    )
    process = _NeverExitProcess()
    watchdog.arm_child_handshake(
        key="gw-loud-writer",
        child="xdist-gw-loud-writer pid=4242",
        phase="worker-ready",
        process=process,
        timeout_seconds=1.0,
    )
    clock.wall = 1.1
    checker = threading.Thread(target=watchdog.check_once, daemon=True)
    checker.start()
    assert loud_entered.wait(timeout=1.0)

    watchdog.stop()
    loud_release.set()
    checker.join(timeout=1.0)

    assert checker.is_alive() is False
    assert exits == []


def test_process_tree_cleanup_escalates_and_reaps_synthetic_gateway_descendant() -> None:
    script = (
        "import signal,subprocess,sys,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "child=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "time.sleep(60)']);"
        "print(child.pid,flush=True);"
        "time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    child_pid = int(process.stdout.readline().strip())
    try:
        _terminate_process_tree(process, 0.2)
        assert process.returncode == -signal.SIGKILL
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=2.0)
        assert process.stdout.read() == ""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1.0)
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_process_tree_cleanup_reaps_group_after_gateway_leader_exits() -> None:
    script = (
        "import subprocess,sys;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "print(child.pid,flush=True)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    process._regolith_isolated_pgid = os.getpgid(process.pid)
    assert process._regolith_isolated_pgid == process.pid
    assert process.stdout is not None
    child_pid = int(process.stdout.readline().strip())
    process.wait(timeout=2.0)
    try:
        _terminate_process_tree(process, 1.0)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=2.0)
        assert process.stdout.read() == ""
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_real_execnet_child_timeout_reaps_isolated_gateway() -> None:
    import execnet

    install_bounded_execnet_bootstrap()
    gateway = execnet.makegateway("popen//id=gw-synthetic-timeout")
    process = gateway._io.popen
    assert process._regolith_isolated_pgid == process.pid
    remote_exec = gateway.remote_exec
    gateway.remote_exec = lambda _source: remote_exec(
        "import time; time.sleep(60)"
    )
    try:
        with pytest.raises(SessionChildHandshakeTimeout):
            bounded_gateway_rinfo(
                gateway,
                timeout_seconds=0.2,
                terminate_grace_seconds=0.2,
                evidence_preserver=lambda _reason, _process: Path(
                    "/tmp/synthetic-watchdog.log"
                ),
            )
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            _terminate_process_tree(process, 0.2)


def test_handshake_bound_is_deliberately_generous() -> None:
    assert CHILD_HANDSHAKE_TIMEOUT_SECONDS >= 600.0


def test_verbose_xdist_gateway_hook_installs_rinfo_bound_first() -> None:
    hook_options = project_conftest.pytest_xdist_newgateway.pytest_impl

    assert hook_options["tryfirst"] is True
