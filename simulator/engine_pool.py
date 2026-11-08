"""Engine-agnostic persistent-process isolation for native engine adapters.

The module deliberately imports no engine implementation.  Engine adapters
provide small, module-level bootstrap and request callables so ``spawn`` can
load heavyweight native dependencies inside the killable child only.
"""

from __future__ import annotations

import atexit
import faulthandler
import hashlib
import json
import logging
import math
import multiprocessing
import multiprocessing.context
import os
import re
import signal
import queue
import selectors
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
import weakref
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

ENGINE_WORKER_CANCEL_POLL_S = 0.05
ENGINE_WORKER_WATCHDOG_GRACE_S = 0.25
ENGINE_POOL_AUTOMATIC_PER_POOL_BUDGET_S = 3.0
ENGINE_POOL_AUTOMATIC_TOTAL_BUDGET_S = 5.0
# Keep only 256 normalized keys; overflow shares one count bucket.
MAX_NATIVE_OUTPUT_DISTINCT_LINES = 256
# Flush an unterminated line in marked fragments before its buffer exceeds 64 KiB.
MAX_NATIVE_OUTPUT_PENDING_BYTES = 64 * 1024
# Cap each summary payload and its identifying sample to bound shutdown output.
MAX_NATIVE_OUTPUT_SUMMARY_BYTES = 1024
MAX_NATIVE_OUTPUT_SUMMARY_EXAMPLE_BYTES = 192
# Cap per-key shutdown summaries even when error keys are exempt from the key cap.
MAX_NATIVE_OUTPUT_SUMMARY_LINES = 256
# Bound each worker's native log contribution to 4 MiB while retaining room
# for many distinct diagnostics before repeat output is suppressed.
MAX_NATIVE_OUTPUT_FORWARD_BYTES = 4 * 1024 * 1024
# Never make worker cleanup wait indefinitely for the parent stderr sink.
ENGINE_NATIVE_OUTPUT_READER_JOIN_S = 0.25
# _stop_pair on the start() handshake-timeout path (diagnostic=False):
# killpg + join(0.1) + kill + join(1.0). These joins sit AFTER the
# handshake wall and BEFORE _start_and_consume's ready.put.
ENGINE_WORKER_STOP_PAIR_JOIN_S = 0.1 + 1.0
# process.start() has no timeout API. One close-join quantum covers a
# healthy spawn-context Process.start() (OS create + pickle + exec) so a
# handshake timeout after a slow-but-finite start() can still land as
# the typed start() error. A wedged start() is not covered by this slack
# — that is the publish-before-start path.
ENGINE_WORKER_SPAWN_SLACK_S = 1.0

# Env-gated lifecycle trace (2026-07-24): the recursive-spawn churn class
# was invisible because every failure path was silently absorbed into
# engine fallbacks. REGOLITH_ENGINE_POOL_DEBUG=1 prints starts, ready
# latencies, and every discard with its reason to stderr.
_POOL_DEBUG = os.environ.get('REGOLITH_ENGINE_POOL_DEBUG') == '1'


def _pool_debug(msg: str) -> None:
    if _POOL_DEBUG:
        print(f'[engine-pool {os.getpid()} {time.monotonic():.2f}] {msg}',
              file=sys.stderr, flush=True)


def _normalise_native_output_line(line: bytes) -> bytes:
    line = _NATIVE_OUTPUT_HEX_RE.sub(b'<address>', line)
    line = _NATIVE_OUTPUT_SPECIAL_NUMBER_RE.sub(b'<number>', line)
    return _NATIVE_OUTPUT_NUMBER_RE.sub(b'<number>', line)


def _preserve_python_output_streams() -> None:
    """Keep Python stream output on the inherited scorer descriptors.

    Python handlers added later that explicitly write to fd 2 or
    ``sys.__stderr__`` enter native capture; its byte budget and dedupe still
    bound those writes.
    """
    global _WORKER_PYTHON_STREAMS

    original_stdout, original_stderr = sys.stdout, sys.stderr
    python_stdout = os.fdopen(
        os.dup(1),
        'w',
        buffering=1,
        encoding=original_stdout.encoding,
        errors=original_stdout.errors,
    )
    python_stderr = os.fdopen(
        os.dup(2),
        'w',
        buffering=1,
        encoding=original_stderr.encoding,
        errors=original_stderr.errors,
    )
    loggers = [logging.root]
    loggers.extend(
        logger
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    )
    for logger in loggers:
        for handler in logger.handlers:
            if not isinstance(handler, logging.StreamHandler):
                continue
            if handler.stream is original_stdout:
                handler.setStream(python_stdout)
            elif handler.stream is original_stderr:
                handler.setStream(python_stderr)
    sys.stdout, sys.stderr = python_stdout, python_stderr
    _WORKER_PYTHON_STREAMS = (original_stdout, original_stderr)


WorkerBootstrap = Callable[..., tuple[Any, Any]]
WorkerHandler = Callable[[Any, Any, Any], Any]
INHERIT_PROCESS_GROUP_ENV = 'REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP'
_LIVE_ENGINE_POOLS: weakref.WeakSet[Any] = weakref.WeakSet()
_ENGINE_POOL_REGISTRY_LOCK = threading.Lock()
_ENGINE_POOL_ATEXIT_REGISTERED = False
_WORKER_PYTHON_STREAMS: tuple[Any, Any] | None = None
# Monotonic per-process pool counter, for thread naming only. Reuses the
# registry lock rather than adding a second one: both guard the same
# "a pool is coming into existence" moment, and the increment is O(1).
_ENGINE_POOL_SEQ = 0
_NATIVE_OUTPUT_HEX_RE = re.compile(rb'0[xX][0-9a-fA-F]+')
_NATIVE_OUTPUT_SPECIAL_NUMBER_RE = re.compile(
    rb'(?<![A-Za-z0-9_])[-+]?(?:nan|inf)\b',
    re.IGNORECASE,
)
_NATIVE_OUTPUT_NUMBER_RE = re.compile(
    rb'(?<![A-Za-z0-9_])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
)


def _next_engine_pool_seq() -> int:
    """Hand out the next per-process pool number (thread naming only)."""
    global _ENGINE_POOL_SEQ

    with _ENGINE_POOL_REGISTRY_LOCK:
        _ENGINE_POOL_SEQ += 1
        return _ENGINE_POOL_SEQ


def _register_engine_pool(pool: 'EngineWorkerPool') -> None:
    global _ENGINE_POOL_ATEXIT_REGISTERED

    with _ENGINE_POOL_REGISTRY_LOCK:
        _LIVE_ENGINE_POOLS.add(pool)
        if not _ENGINE_POOL_ATEXIT_REGISTERED:
            atexit.register(close_all_engine_pools)
            _ENGINE_POOL_ATEXIT_REGISTERED = True


def _discard_engine_pool(pool: 'EngineWorkerPool') -> None:
    with _ENGINE_POOL_REGISTRY_LOCK:
        _LIVE_ENGINE_POOLS.discard(pool)


class EngineWorkerRemoteError(RuntimeError):
    """An engine request raised inside its isolated worker."""

    def __init__(
        self,
        exc_name: str,
        detail: str,
        remote_traceback: str,
        failure_disposition: Optional[str] = None,
    ):
        super().__init__(detail)
        self.exc_name = exc_name
        self.detail = detail
        self.remote_traceback = remote_traceback
        self.failure_disposition = failure_disposition


class EngineWorkerPoolLifecycleError(RuntimeError):
    """The pool cannot accept work because its lifecycle has ended."""


class EngineWorkerUnavailable(RuntimeError):
    """Parent-side: the isolated worker handle is absent after start.

    Token-bearing. Consumers must key on the type or ``reason_code``,
    never on the human string. This is not by itself engine-absence
    after a live adapter has already answered — sequential mid-run
    death is ``not_attempted`` on later rows.
    """

    reason_code = 'engine_worker_unavailable'

    def __init__(self, worker_name: str) -> None:
        self.worker_name = str(worker_name)
        super().__init__(f'{self.worker_name} worker is unavailable')


class EngineWorkerDied(RuntimeError):
    """The worker exited or hit EOF before returning a result.

    Raised by the pool itself, only for that event. ``phase`` is
    ``start`` (pre-ready during construction), ``restart`` (pre-ready
    inside ``call``), or ``call`` (died while a request was in flight).
    Other exceptions are not rewritten into this type.
    """

    def __init__(
        self,
        phase: str,
        detail: str,
        *,
        returncode: Optional[int] = None,
    ) -> None:
        self.phase = phase
        self.detail = str(detail)
        self.returncode = None if returncode is None else int(returncode)
        super().__init__(self.detail)


class EngineWorkerTimeout(TimeoutError):
    """Typed hard-wall expiry for an isolated engine worker."""

    def __init__(
        self,
        worker_name: str,
        timeout_s: float,
        *,
        phase: str,
        failed_slots: tuple[int, ...] | list[int] = (),
    ):
        self.worker_name = str(worker_name)
        self.timeout_s = float(timeout_s)
        self.phase = str(phase)
        self.failed_slots = tuple(int(slot) for slot in failed_slots)
        super().__init__(
            f'{self.worker_name} {self.phase} exceeded hard timeout of '
            f'{self.timeout_s:g}s'
        )


def _worker_startup_timeout_s(worker: Any) -> float:
    raw = getattr(worker, 'startup_timeout_s', None)
    if raw is None:
        raw = 30.0
    timeout_s = float(raw)
    if not math.isfinite(timeout_s) or timeout_s <= 0.0:
        raise ValueError(
            f'worker startup_timeout_s must be finite and positive, got {raw!r}'
        )
    return timeout_s


def _process_pid(process: Any) -> int | None:
    pid = getattr(process, 'pid', None)
    if pid is None:
        return None
    return int(pid)


def _signal_worker_process(process: Any, signum: int) -> None:
    pid = _process_pid(process)
    if pid is None:
        return
    if os.environ.get(INHERIT_PROCESS_GROUP_ENV) != '1':
        try:
            os.killpg(pid, signum)
            if signum != signal.SIGKILL:
                return
            if not process.is_alive():
                return
        except (AttributeError, OSError):
            pass
    try:
        if signum == signal.SIGTERM:
            process.terminate()
        elif signum == signal.SIGKILL:
            process.kill()
        else:
            os.kill(pid, signum)
    except (AttributeError, OSError):
        pass


def _pool_ready_wait_s(workers: list[Any] | tuple[Any, ...]) -> float:
    # Premise: constructor first-start on each slot thread, concurrent.
    # start() order before _start_and_consume can ready.put:
    #   1. self.close() — first-start process is None and _pending_reap
    #      is empty, so this is 0 s. (A later cold restart is after the
    #      constructor wait is already over.)
    #   2. Pipe() + Process() — in-process, negligible.
    #   3. process.start() — no timeout API. A wedged start() never puts
    #      (missing-slot path; the Process is published first so
    #      request_disable can reap). A healthy start() is finite; cover
    #      it with one close-join quantum so a handshake timeout that
    #      follows a slow-but-finite spawn still lands as the typed error.
    #   4. handshake poll — startup_timeout_s (already in the budget).
    #   5. on handshake TimeoutError: _discard_current → _stop_pair
    #      (diagnostic=False, so no watchdog sleep):
    #        join(0.1) + join(1.0) = ENGINE_WORKER_STOP_PAIR_JOIN_S
    #      THEN _start_and_consume ready.put((index, exc)).
    #   6. constructor: N timed ready.get hops after the last put.
    # Algebra (starts are concurrent, so max not sum of startup_i):
    #   ready_wait_s = max(startup_i)
    #                + ENGINE_WORKER_SPAWN_SLACK_S
    #                + ENGINE_WORKER_STOP_PAIR_JOIN_S
    #                + N * ENGINE_WORKER_CANCEL_POLL_S
    # Unit check: every term is seconds; result is seconds.
    # Sanity (N=1, startup=30): 30 + 1.0 + 1.1 + 0.05 = 32.15 s.
    #   Old formula was 30.05 s; T_discard - N*0.05 ≈ 1.05 s short at
    #   default size=1. This budget covers that 1.1 s plus 1.0 s spawn.
    #   N=1, startup=1 (silent pin): 1 + 1.0 + 1.1 + 0.05 = 3.15 s.
    n = len(workers)
    if n < 1:
        raise ValueError('engine worker pool requires at least one worker')
    budgets = [_worker_startup_timeout_s(worker) for worker in workers]
    ready_wait_s = (
        max(budgets)
        + ENGINE_WORKER_SPAWN_SLACK_S
        + ENGINE_WORKER_STOP_PAIR_JOIN_S
        + n * ENGINE_WORKER_CANCEL_POLL_S
    )
    if not math.isfinite(ready_wait_s) or ready_wait_s <= 0.0:
        raise ValueError(
            f'pool ready wait must be finite and positive, got {ready_wait_s!r}'
        )
    return ready_wait_s


def _run_engine_worker(
    connection: Any,
    bootstrap: WorkerBootstrap,
    handler: WorkerHandler,
    bootstrap_args: tuple[Any, ...],
    diagnostic_log_path: Optional[str],
    diagnostic_signal: int,
    native_output_connection: Any = None,
) -> None:
    """Run one initialized engine resource until clean shutdown or failure."""
    if native_output_connection is not None:
        _preserve_python_output_streams()
        native_output_fd = native_output_connection.fileno()
        os.dup2(native_output_fd, 1)
        os.dup2(native_output_fd, 2)
        native_output_connection.close()
    if os.environ.get(INHERIT_PROCESS_GROUP_ENV) != '1':
        try:
            os.setsid()
        except (AttributeError, OSError):
            pass
    faulthandler.enable()
    errlog = None
    resource = None
    try:
        if diagnostic_log_path:
            path = Path(diagnostic_log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            errlog = path.open('a', encoding='utf-8', buffering=1)
            faulthandler.register(
                diagnostic_signal,
                file=errlog,
                all_threads=True,
            )
        resource, ready_payload = bootstrap(*bootstrap_args)
        connection.send(('ready', ready_payload))
        while True:
            try:
                request = connection.recv()
            except EOFError:
                break
            if request is None:
                break
            try:
                result = handler(resource, request, errlog)
                connection.send(('ok', result))
            except EngineWorkerTimeout as exc:
                connection.send((
                    'timeout',
                    exc.worker_name,
                    exc.timeout_s,
                    exc.phase,
                ))
            except BaseException as exc:  # pragma: no cover - native boundary
                connection.send((
                    'error', type(exc).__name__, str(exc),
                    traceback.format_exc(),
                    getattr(exc, 'failure_disposition', None),
                ))
    except EngineWorkerTimeout as exc:  # pragma: no cover - nested bootstrap
        try:
            connection.send((
                'timeout',
                exc.worker_name,
                exc.timeout_s,
                exc.phase,
            ))
        except BaseException:
            pass
    except BaseException as exc:  # pragma: no cover - bootstrap/native faults
        try:
            connection.send((
                'error', type(exc).__name__, str(exc), traceback.format_exc(),
                getattr(exc, 'failure_disposition', None),
            ))
        except BaseException:
            pass
    finally:
        close_resource = getattr(resource, 'close', None)
        if callable(close_resource):
            try:
                close_resource()
            except BaseException:
                pass
        if errlog is not None:
            try:
                faulthandler.unregister(diagnostic_signal)
            finally:
                errlog.close()
        connection.close()


def _read_native_output(connection: Any, *, stderr_fd: int = 2) -> None:
    """Forward bounded native output and close the reader fd on thread exit."""
    # Ordinary keys are capped; error keys stay countable outside that cap.
    counts: dict[bytes, int] = {}
    examples: dict[bytes, bytes] = {}
    pending = bytearray()
    fragment_error: bool | None = None
    forward_error_continuations = False
    ordinary_key_count = 0
    overflow_count = 0
    ordinary_truncated_lines = 0
    forwarded_bytes = 0
    budget_reached = False
    sink_failed = False

    def write_forwarded(payload: bytes) -> None:
        nonlocal forwarded_bytes, budget_reached, sink_failed
        while payload:
            if sink_failed or budget_reached:
                return
            remaining = MAX_NATIVE_OUTPUT_FORWARD_BYTES - forwarded_bytes
            if remaining <= 0:
                budget_reached = True
                return
            chunk = payload[:remaining]
            try:
                written = os.write(stderr_fd, chunk)
            except OSError:
                sink_failed = True
                return
            if written <= 0:
                sink_failed = True
                return
            forwarded_bytes += written
            payload = payload[written:]
        if forwarded_bytes >= MAX_NATIVE_OUTPUT_FORWARD_BYTES:
            budget_reached = True

    def write_summary(payload: bytes) -> None:
        if sink_failed:
            return
        if len(payload) > MAX_NATIVE_OUTPUT_SUMMARY_BYTES:
            payload = payload[:MAX_NATIVE_OUTPUT_SUMMARY_BYTES - 1] + b'\n'
        while payload:
            try:
                written = os.write(stderr_fd, payload)
            except OSError:
                return
            if written <= 0:
                return
            payload = payload[written:]

    def is_error_line(line: bytes) -> bool:
        lowered = line.lower()
        return any(
            token in lowered
            for token in (b'error', b'abort', b'fatal', b'segmentation')
        )

    def record_line(
        line: bytes,
        *,
        is_error: bool | None = None,
        output: bytes | None = None,
    ) -> bool:
        nonlocal ordinary_key_count, overflow_count
        if is_error is None:
            is_error = is_error_line(line)
        key = hashlib.sha256(_normalise_native_output_line(line)).digest()
        previous_count = counts.get(key)
        if previous_count is None:
            if (
                not is_error
                and ordinary_key_count >= MAX_NATIVE_OUTPUT_DISTINCT_LINES
            ):
                overflow_count += 1
                return False
            counts[key] = 0
            examples[key] = line[:MAX_NATIVE_OUTPUT_SUMMARY_EXAMPLE_BYTES]
            if not is_error:
                ordinary_key_count += 1
            previous_count = 0
        counts[key] = previous_count + 1
        first_occurrence = previous_count == 0
        if first_occurrence:
            write_forwarded(line if output is None else output)
        return first_occurrence

    def flush_first_fragment(line: bytes) -> tuple[bool, bool]:
        nonlocal ordinary_truncated_lines
        is_error = is_error_line(line)
        output = line if is_error else line + b'...[truncated]\n'
        first_occurrence = record_line(
            line,
            is_error=is_error,
            output=output,
        )
        if not is_error:
            ordinary_truncated_lines += 1
        return is_error, is_error and first_occurrence

    def forward_continuation(line: bytes) -> None:
        if fragment_error and forward_error_continuations:
            write_forwarded(line)

    try:
        while True:
            chunk = os.read(connection.fileno(), 65536)
            if not chunk:
                break
            while chunk:
                if fragment_error is not None:
                    newline = chunk.find(b'\n')
                    if newline < 0:
                        forward_continuation(chunk)
                        chunk = b''
                        continue
                    forward_continuation(chunk[:newline + 1])
                    chunk = chunk[newline + 1:]
                    fragment_error = None
                    forward_error_continuations = False
                    continue

                newline = chunk.find(b'\n')
                if newline >= 0:
                    line = bytes(pending) + chunk[:newline + 1]
                    pending.clear()
                    if len(line) <= MAX_NATIVE_OUTPUT_PENDING_BYTES:
                        record_line(line)
                    else:
                        first_fragment = line[:MAX_NATIVE_OUTPUT_PENDING_BYTES]
                        continuation = line[MAX_NATIVE_OUTPUT_PENDING_BYTES:]
                        line_is_error, forward_error_continuations = (
                            flush_first_fragment(first_fragment)
                        )
                        if line_is_error and forward_error_continuations:
                            write_forwarded(continuation)
                    chunk = chunk[newline + 1:]
                    continue

                available = MAX_NATIVE_OUTPUT_PENDING_BYTES - len(pending)
                if len(chunk) <= available:
                    pending.extend(chunk)
                    break
                first_fragment = bytes(pending) + chunk[:available]
                pending.clear()
                fragment_error, forward_error_continuations = (
                    flush_first_fragment(first_fragment)
                )
                chunk = chunk[available:]
        if pending:
            record_line(bytes(pending))
    except OSError:
        pass
    finally:
        try:
            connection.close()
        except OSError:
            pass

    # Error keys bypass the key cap, so cap per-key summaries at 256 lines.
    summary_omitted = max(
        0,
        len(counts) - MAX_NATIVE_OUTPUT_SUMMARY_LINES,
    )
    for index, (key, count) in enumerate(counts.items()):
        if index >= MAX_NATIVE_OUTPUT_SUMMARY_LINES:
            break
        example = examples[key]
        summary = (
            f'[engine-pool native output] count={count}: {example!r}\n'
        ).encode()
        write_summary(summary)
    if (
        overflow_count
        or ordinary_truncated_lines
        or summary_omitted
        or budget_reached
    ):
        details = [f'other_count={overflow_count}']
        if ordinary_truncated_lines:
            details.append(
                f'ordinary_truncated_lines={ordinary_truncated_lines}'
            )
        if summary_omitted:
            details.append(f'summary_omitted={summary_omitted}')
        if budget_reached:
            details.append(
                f'forwarding budget reached at '
                f'{MAX_NATIVE_OUTPUT_FORWARD_BYTES} bytes'
            )
        write_summary(
            f'[engine-pool native output] {" ".join(details)}\n'.encode()
        )


class WarmEngineWorker:
    """Persistent spawned worker with deadlines and crash/hang replacement."""

    @staticmethod
    def _caller_future_timeout_s(
        call_timeout_s: float,
        startup_timeout_s: float,
        watchdog_grace_s: float = ENGINE_WORKER_WATCHDOG_GRACE_S,
    ) -> float:
        """Bound a caller wait across cold start, call, and timeout cleanup.

        Premise: ``start()`` spawns before its handshake wall begins, the job
        wall begins after READY, and a job timeout is reported only after the
        watchdog grace and bounded stop joins. In seconds, the caller envelope
        is call + startup + healthy-spawn slack + watchdog grace + stop joins
        + one consumer poll tick. Default MAGEMin is 15 + 30 + 1 + 0.25 +
        1.1 + 0.05 = 47.4 seconds. The adapter may cap this by its remaining
        aggregate budget, which remains the stronger deadline.
        """
        call_s = float(call_timeout_s)
        startup_s = float(startup_timeout_s)
        grace_s = float(watchdog_grace_s)
        if not math.isfinite(call_s) or call_s <= 0.0:
            raise ValueError('worker call timeout must be finite and positive')
        if not math.isfinite(startup_s) or startup_s <= 0.0:
            raise ValueError(
                'worker startup timeout must be finite and positive'
            )
        if not math.isfinite(grace_s) or grace_s < 0.0:
            raise ValueError(
                'worker watchdog grace must be finite and nonnegative'
            )
        return (
            call_s
            + startup_s
            + ENGINE_WORKER_SPAWN_SLACK_S
            + grace_s
            + ENGINE_WORKER_STOP_PAIR_JOIN_S
            + ENGINE_WORKER_CANCEL_POLL_S
        )

    def __init__(
        self,
        *,
        name: str,
        bootstrap: WorkerBootstrap,
        handler: WorkerHandler,
        bootstrap_args: tuple[Any, ...] = (),
        startup_timeout_s: float = 30.0,
        call_timeout_s: float = 60.0,
        diagnostic_log_path: str | os.PathLike[str] | None = None,
        diagnostic_signal: int = signal.SIGUSR1,
        watchdog_grace_s: float = ENGINE_WORKER_WATCHDOG_GRACE_S,
        daemon: bool = True,
    ) -> None:
        self.name = str(name)
        self._bootstrap = bootstrap
        self._handler = handler
        self._bootstrap_args = tuple(bootstrap_args)
        self.startup_timeout_s = max(0.001, float(startup_timeout_s))
        self.call_timeout_s = max(0.001, float(call_timeout_s))
        self.diagnostic_log_path = (
            Path(diagnostic_log_path) if diagnostic_log_path else None
        )
        self.diagnostic_signal = int(diagnostic_signal)
        self.watchdog_grace_s = max(0.0, float(watchdog_grace_s))
        self.daemon = bool(daemon)
        self.process = None
        self.connection = None
        self.ready_payload = None
        self.start_count = 0
        self.disabled = False
        self._state_lock = threading.Lock()
        self._pending_reap: list[Any] = []
        self._native_output_capture: tuple[
            Any, Any, Any | None, threading.Thread
        ] | None = None
        self._pool_call_abort_event: threading.Event | None = None
        self._active_frame_readers: dict[int, int] = {}
        self._close_after_frame_read: set[int] = set()

    def _begin_pool_call(self) -> threading.Event:
        """Install the cancellation token before the pool enters ``call``."""
        abort_event = threading.Event()
        with self._state_lock:
            if self._pool_call_abort_event is not None:
                raise RuntimeError(f'{self.name} already has an active pool call')
            self._pool_call_abort_event = abort_event
        return abort_event

    def _end_pool_call(self, abort_event: threading.Event) -> None:
        with self._state_lock:
            if self._pool_call_abort_event is abort_event:
                self._pool_call_abort_event = None

    def _finish_frame_reader(self, connection: Any) -> None:
        connection_id = id(connection)
        with self._state_lock:
            active = self._active_frame_readers.get(connection_id, 0)
            if active > 1:
                self._active_frame_readers[connection_id] = active - 1
                return
            self._active_frame_readers.pop(connection_id, None)
            if connection_id in self._close_after_frame_read:
                self._close_after_frame_read.remove(connection_id)
                try:
                    connection.close()
                except OSError:
                    pass

    def _close_connection_or_defer(self, connection: Any) -> None:
        connection_id = id(connection)
        with self._state_lock:
            if self._active_frame_readers.get(connection_id, 0):
                self._close_after_frame_read.add(connection_id)
                self._shutdown_connection_reader(connection)
                return
            else:
                try:
                    connection.close()
                except OSError:
                    pass
                return

    def _shutdown_connection_reader(self, connection: Any) -> None:
        """Try to wake a POSIX Pipe reader without closing its descriptor."""
        if os.name != 'posix':
            return
        shutdown_socket = None
        try:
            fd = connection.fileno()
            if not isinstance(fd, int) or fd < 0:
                return
            shutdown_socket = socket.socket(fileno=fd)
            shutdown_socket.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        finally:
            if shutdown_socket is not None:
                try:
                    shutdown_socket.detach()
                except (OSError, ValueError):
                    pass

    def _close_native_output_writer(self, process: Any) -> None:
        with self._state_lock:
            capture = self._native_output_capture
            if capture is None:
                return
            captured_process, reader, writer, thread = capture
            if (
                captured_process is not process
                or writer is None
            ):
                return
            self._native_output_capture = (
                captured_process, reader, None, thread,
            )
        try:
            writer.close()
        except OSError:
            pass

    def _finish_native_output_capture(
        self,
        process: Any,
        *,
        process_start_failed: bool = False,
    ) -> None:
        with self._state_lock:
            capture = self._native_output_capture
            if capture is None:
                return
            captured_process, _reader, _writer, thread = capture
            if captured_process is not process:
                return
        if thread.ident is not None:
            pid = _process_pid(process)
            if (
                pid is None and not process_start_failed
            ) or (
                pid is not None and process.is_alive()
            ):
                return
        self._close_native_output_writer(process)
        with self._state_lock:
            capture = self._native_output_capture
            if capture is None or capture[0] is not process:
                return
            self._native_output_capture = None
        # The daemon reader may outlive this join while stderr is blocked; it
        # resumes when the sink unblocks and closes its own read fd in finally.
        thread.join(timeout=ENGINE_NATIVE_OUTPUT_READER_JOIN_S)

    def _receive_frame_with_deadline(
        self,
        connection: Any,
        process: Any,
        *,
        deadline: float,
        timeout_s: float,
        phase: str,
        abort_event: threading.Event | None = None,
        debug_timeout_s: float | None = None,
    ) -> Any:
        """Receive one complete frame without letting ``recv`` overrun its wall."""
        reply_lock = threading.Lock()
        reply: tuple[float, Any, BaseException | None] | None = None
        reply_ready = threading.Event()
        connection_id = id(connection)

        with self._state_lock:
            aborted = (
                abort_event is not None and abort_event.is_set()
            )
            current = (
                not self.disabled
                and self.process is process
                and self.connection is connection
            )
            if (
                current
                and not aborted
                and connection_id not in self._close_after_frame_read
            ):
                self._active_frame_readers[connection_id] = (
                    self._active_frame_readers.get(connection_id, 0) + 1
                )
                registered = True
            else:
                registered = False
        if not registered:
            if aborted:
                self._discard_current(
                    diagnostic=False,
                    expected_process=process,
                    expected_connection=connection,
                )
                raise EngineWorkerTimeout(
                    self.name,
                    timeout_s if phase == 'job' else self.call_timeout_s,
                    phase='job',
                )
            if phase == 'job':
                raise EngineWorkerDied(
                    'call',
                    f'{self.name} worker exited without a result',
                    returncode=getattr(process, 'exitcode', None),
                )
            raise RuntimeError(f'{self.name} worker is disabled')

        def receive_frame() -> None:
            nonlocal reply
            try:
                try:
                    message = connection.recv()
                except BaseException as exc:
                    outcome = (time.monotonic(), None, exc)
                else:
                    outcome = (time.monotonic(), message, None)
                with reply_lock:
                    reply = outcome
                reply_ready.set()
            finally:
                self._finish_frame_reader(connection)

        receiver = threading.Thread(
            target=receive_frame,
            name=f'{self.name} {phase} reader',
            daemon=True,
        )
        try:
            receiver.start()
        except BaseException:
            self._finish_frame_reader(connection)
            raise
        try:
            while True:
                with reply_lock:
                    outcome = reply
                if outcome is not None:
                    receiver.join()
                    completed_at, message, receive_error = outcome
                    # A complete receipt at the deadline is on time.
                    if completed_at <= deadline:
                        if receive_error is not None:
                            raise receive_error
                        return message
                    break
                with self._state_lock:
                    aborted = (
                        abort_event is not None and abort_event.is_set()
                    )
                    current = (
                        not self.disabled
                        and self.process is process
                        and self.connection is connection
                    )
                if aborted:
                    raise EngineWorkerTimeout(
                        self.name,
                        timeout_s if phase == 'job' else self.call_timeout_s,
                        phase='job',
                    )
                if phase == 'job' and not current:
                    raise EngineWorkerDied(
                        'call',
                        f'{self.name} worker exited without a result',
                        returncode=getattr(process, 'exitcode', None),
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    # Recheck after reading the clock so an outcome
                    # published while the caller resumed is not lost.
                    with reply_lock:
                        outcome = reply
                    if outcome is not None:
                        continue
                    break
                reply_ready.wait(min(
                    ENGINE_WORKER_CANCEL_POLL_S,
                    remaining,
                ))
            if debug_timeout_s is not None:
                _pool_debug(
                    f'{self.name}: reply receive timeout after '
                    f'{debug_timeout_s:.2f}s (wall {timeout_s:.2f}s) — '
                    'discarding worker'
                )
            self._discard_current(
                diagnostic=debug_timeout_s is not None,
                expected_process=process,
                expected_connection=connection,
            )
            raise EngineWorkerTimeout(
                self.name,
                timeout_s,
                phase=phase,
            )
        finally:
            if receiver.is_alive():
                self._discard_current(
                    diagnostic=False,
                    expected_process=process,
                    expected_connection=connection,
                )
                # The worker group is reaped before this join. If recv has
                # not returned, the reader keeps the old fd and closes it
                # from _finish_frame_reader after recv returns.
                receiver.join(
                    timeout=max(0.0, deadline - time.monotonic())
                    + ENGINE_WORKER_CANCEL_POLL_S
                )

    def start(self, *, timeout_s: Optional[float] = None) -> Any:
        """Start a fresh worker and wait for its bootstrap acknowledgement."""
        self.close()
        with self._state_lock:
            if self.disabled:
                raise RuntimeError(f'{self.name} worker is disabled')
            abort_event = self._pool_call_abort_event
            if abort_event is not None and abort_event.is_set():
                raise EngineWorkerTimeout(
                    self.name,
                    self.call_timeout_s,
                    phase='job',
                )
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe(duplex=True)
        native_output_capture = None
        native_output_reader = None
        native_output_writer = None
        native_output_thread = None
        try:
            # Injected test contexts do not spawn this worker target.
            if isinstance(context, multiprocessing.context.BaseContext):
                native_output_reader, native_output_writer = context.Pipe(
                    duplex=False,
                )
                native_output_thread = threading.Thread(
                    target=_read_native_output,
                    args=(native_output_reader,),
                    name=f'{self.name} native output reader',
                    daemon=True,
                )
                native_output_thread.start()
            process_args = (
                child,
                self._bootstrap,
                self._handler,
                self._bootstrap_args,
                str(self.diagnostic_log_path) if self.diagnostic_log_path else None,
                self.diagnostic_signal,
            )
            if native_output_writer is not None:
                process_args += (native_output_writer,)
            process = context.Process(
                target=_run_engine_worker,
                args=process_args,
                daemon=self.daemon,
            )
        except BaseException:
            if native_output_writer is not None:
                native_output_writer.close()
            if native_output_thread is not None:
                if native_output_thread.ident is None:
                    # No reader thread owns this fd when Thread.start fails.
                    if native_output_reader is not None:
                        native_output_reader.close()
                else:
                    native_output_thread.join(
                        timeout=ENGINE_NATIVE_OUTPUT_READER_JOIN_S,
                    )
            child.close()
            parent.close()
            raise
        if native_output_thread is not None:
            native_output_capture = (
                process,
                native_output_reader,
                native_output_writer,
                native_output_thread,
            )
        # Publish before Process.start() so request_disable / ctor-failure
        # close can reap a child whose start() has not returned. pid is
        # None until the OS create assigns _popen; request_disable then
        # records the Process on _pending_reap and close() kills once
        # pid exists. Chose publish-on-the-worker (self.process +
        # _pending_reap) over a spawn helper thread or a pool-wide
        # child-pid scan: request_disable already kills self.process,
        # and _pending_reap is what ctor-failure close already joins.
        with self._state_lock:
            if self.disabled:
                if native_output_writer is not None:
                    native_output_writer.close()
                if native_output_thread is not None:
                    native_output_thread.join(
                        timeout=ENGINE_NATIVE_OUTPUT_READER_JOIN_S,
                    )
                child.close()
                parent.close()
                raise RuntimeError(f'{self.name} worker is disabled')
            if abort_event is not None and abort_event.is_set():
                if native_output_writer is not None:
                    native_output_writer.close()
                if native_output_thread is not None:
                    native_output_thread.join(
                        timeout=ENGINE_NATIVE_OUTPUT_READER_JOIN_S,
                    )
                child.close()
                parent.close()
                raise EngineWorkerTimeout(
                    self.name,
                    self.call_timeout_s,
                    phase='job',
                )
            self.process = process
            self.connection = parent
            self.ready_payload = None
            self._native_output_capture = native_output_capture
            if process not in self._pending_reap:
                self._pending_reap.append(process)
        with self._state_lock:
            still_ours = (
                not self.disabled
                and self.process is process
                and not (
                    abort_event is not None and abort_event.is_set()
                )
            )
        if not still_ours:
            self._finish_native_output_capture(
                process,
                process_start_failed=True,
            )
            child.close()
            parent.close()
            if abort_event is not None and abort_event.is_set():
                raise EngineWorkerTimeout(
                    self.name,
                    self.call_timeout_s,
                    phase='job',
                )
            raise RuntimeError(f'{self.name} worker is disabled')
        try:
            process.start()
        except BaseException:
            self._finish_native_output_capture(
                process,
                process_start_failed=True,
            )
            child.close()
            parent.close()
            with self._state_lock:
                if self.process is process:
                    self.process = None
                    self.connection = None
            raise
        child.close()
        self._close_native_output_writer(process)
        with self._state_lock:
            if (
                self.disabled
                or self.process is not process
                or (abort_event is not None and abort_event.is_set())
            ):
                cancelled = True
            else:
                cancelled = False
                try:
                    self._pending_reap.remove(process)
                except ValueError:
                    pass
        if cancelled:
            self._stop_pair(
                process,
                parent,
                diagnostic=False,
                cleanup_group=True,
            )
            if abort_event is not None and abort_event.is_set():
                raise EngineWorkerTimeout(
                    self.name,
                    self.call_timeout_s,
                    phase='job',
                )
            raise RuntimeError(f'{self.name} worker is disabled')
        try:
            startup_timeout = self.startup_timeout_s
            if timeout_s is not None:
                startup_timeout = min(
                    startup_timeout,
                    max(0.001, float(timeout_s)),
                )
            deadline = time.monotonic() + startup_timeout
            while True:
                with self._state_lock:
                    current = (
                        not self.disabled
                        and self.process is process
                        and self.connection is parent
                        and not (
                            abort_event is not None and abort_event.is_set()
                        )
                    )
                if not current:
                    if abort_event is not None and abort_event.is_set():
                        raise EngineWorkerTimeout(
                            self.name,
                            self.call_timeout_s,
                            phase='job',
                        )
                    raise RuntimeError(f'{self.name} worker is disabled')
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise EngineWorkerTimeout(
                        self.name,
                        startup_timeout,
                        phase='initialization',
                    )
                if parent.poll(min(ENGINE_WORKER_CANCEL_POLL_S, remaining)):
                    break
            message = self._receive_frame_with_deadline(
                parent,
                process,
                deadline=deadline,
                timeout_s=startup_timeout,
                phase='initialization',
                abort_event=abort_event,
            )
        except (EOFError, OSError, TimeoutError) as exc:
            self._discard_current(
                diagnostic=False,
                expected_process=process,
                expected_connection=parent,
            )
            # EngineWorkerTimeout stays itself. EOF and a dead pipe are
            # the worker exiting before ready.
            if isinstance(exc, TimeoutError):
                raise
            raise EngineWorkerDied(
                'start',
                f'{type(exc).__name__}: {exc}',
                returncode=getattr(process, 'exitcode', None),
            ) from exc
        if message[0] == 'timeout':
            self._discard_current(
                diagnostic=False,
                expected_process=process,
                expected_connection=parent,
            )
            _tag, worker_name, timeout_s, phase = message
            raise EngineWorkerTimeout(
                worker_name,
                timeout_s,
                phase=phase,
            )
        if message[0] != 'ready':
            self._discard_current(
                diagnostic=False,
                expected_process=process,
                expected_connection=parent,
            )
            _tag, exc_name, detail, remote_traceback, *metadata = message
            failure_disposition = metadata[0] if metadata else None
            raise EngineWorkerRemoteError(
                exc_name, detail, remote_traceback, failure_disposition,
            )
        with self._state_lock:
            if (
                self.disabled
                or self.process is not process
                or self.connection is not parent
            ):
                if abort_event is not None and abort_event.is_set():
                    raise EngineWorkerTimeout(
                        self.name,
                        self.call_timeout_s,
                        phase='job',
                    )
                raise RuntimeError(f'{self.name} worker is disabled')
            self.ready_payload = message[1]
            self.start_count += 1
            return self.ready_payload

    def call(self, request: Any, *, timeout_s: Optional[float] = None) -> Any:
        """Run one request; replace a dead or timed-out worker before return."""
        timeout = self.call_timeout_s if timeout_s is None else max(
            0.001, float(timeout_s))
        with self._state_lock:
            if self.disabled:
                raise RuntimeError(f'{self.name} worker is disabled')
            abort_event = self._pool_call_abort_event
            if abort_event is not None and abort_event.is_set():
                raise EngineWorkerTimeout(
                    self.name,
                    timeout,
                    phase='job',
                )
            process = self.process
            connection = self.connection
        if process is not None and not process.is_alive():
            _pool_debug(f'{self.name}: worker pid found dead at call entry')
            self._discard_current(
                diagnostic=False,
                expected_process=process,
                expected_connection=connection,
            )
            process = None
            connection = None
        if process is None:
            # Cold (re)start is bounded by startup_timeout_s, NOT the
            # per-call wall. 2026-07-24 (B1 gate-3 churn class): the old
            # `self.start(timeout_s=timeout)` clamped the startup wall to
            # min(startup_timeout_s, call wall) — for ThermoEngine that
            # meant min(30, 3.0) = 3.0 s against a measured bootstrap of
            # ~2.2 s bare (import thermoengine + MELTS model build) that
            # grows past 3 s under any sim/xdist parent (heavier __main__
            # re-import in the spawn child, sibling load). Once one worker
            # died, EVERY cold restart was killed at the wall mid-import
            # and retried forever: ~10 s of hot spawn churn per call while
            # the sim limped on fallback curves (~2x runtime, the gate-3
            # timeout class). Warm-call walls measure CALLS; startup has
            # its own budget — never let the tighter one strangle the
            # other. The call wall below still bounds the job itself.
            t_start = time.monotonic()
            try:
                self.start()
            except EngineWorkerDied as exc:
                # Construction and this restart share start(); the phase
                # records which caller observed the pre-ready death.
                raise EngineWorkerDied(
                    'restart', exc.detail, returncode=exc.returncode
                ) from exc.__cause__
            _pool_debug(
                f'{self.name}: cold start ok in '
                f'{time.monotonic() - t_start:.2f}s pid='
                f'{getattr(self.process, "pid", None)}'
            )
        started_at = time.monotonic()
        with self._state_lock:
            process = self.process
            connection = self.connection
            disabled = self.disabled
        if process is None or connection is None:
            raise EngineWorkerUnavailable(self.name)
        if disabled:
            self._discard_current(
                diagnostic=False,
                expected_process=process,
                expected_connection=connection,
            )
            raise RuntimeError(f'{self.name} worker is disabled')
        remaining = timeout - max(0.0, time.monotonic() - started_at)
        if remaining <= 0.0:
            self._discard_current(
                diagnostic=True,
                expected_process=process,
                expected_connection=connection,
            )
            raise EngineWorkerTimeout(
                self.name,
                timeout,
                phase='job',
            )
        try:
            with self._state_lock:
                if abort_event is not None and abort_event.is_set():
                    raise EngineWorkerTimeout(
                        self.name,
                        timeout,
                        phase='job',
                    )
                if (
                    self.disabled
                    or self.process is not process
                    or self.connection is not connection
                ):
                    raise EngineWorkerDied(
                        'call',
                        f'{self.name} worker exited without a result',
                    )
                # Passing this locked check admits this request to the send.
                # It may reach the child and briefly run its handler before an
                # abort kills it; the post-send token and slot checks discard
                # its result. Keep transport I/O outside the state lock so
                # disable and abort can still kill the child if send blocks.
            connection.send(request)
            with self._state_lock:
                if abort_event is not None and abort_event.is_set():
                    raise EngineWorkerTimeout(
                        self.name,
                        timeout,
                        phase='job',
                    )
                if (
                    self.disabled
                    or self.process is not process
                    or self.connection is not connection
                ):
                    raise EngineWorkerDied(
                        'call',
                        f'{self.name} worker exited without a result',
                    )
            deadline = time.monotonic() + remaining
            while True:
                with self._state_lock:
                    current = (
                        not self.disabled
                        and self.process is process
                        and self.connection is connection
                    )
                if not current:
                    # In-flight state loss is worker death, not a local error.
                    raise EngineWorkerDied(
                        'call',
                        f'{self.name} worker exited without a result',
                        returncode=getattr(process, 'exitcode', None),
                    )
                poll_remaining = deadline - time.monotonic()
                if poll_remaining <= 0.0:
                    _pool_debug(
                        f'{self.name}: job poll timeout after {remaining:.2f}s '
                        f'(wall {timeout:.2f}s) — discarding worker'
                    )
                    self._discard_current(
                        diagnostic=True,
                        expected_process=process,
                        expected_connection=connection,
                    )
                    raise EngineWorkerTimeout(
                        self.name,
                        timeout,
                        phase='job',
                    )
                if connection.poll(
                    min(ENGINE_WORKER_CANCEL_POLL_S, poll_remaining)
                ):
                    break
            message = self._receive_frame_with_deadline(
                connection,
                process,
                deadline=deadline,
                timeout_s=timeout,
                phase='job',
                abort_event=abort_event,
                debug_timeout_s=remaining,
            )
        except TimeoutError:
            raise
        except (BrokenPipeError, EOFError, OSError) as exc:
            _pool_debug(
                f'{self.name}: worker died mid-call ({type(exc).__name__})'
            )
            self._discard_current(
                diagnostic=False,
                expected_process=process,
                expected_connection=connection,
            )
            raise EngineWorkerDied(
                'call',
                f'{self.name} worker exited without a result',
                returncode=getattr(process, 'exitcode', None),
            ) from exc
        with self._state_lock:
            current = (
                not self.disabled
                and self.process is process
                and self.connection is connection
            )
        if not current:
            # The result arrived after the slot was dropped. Same death
            # as a mid-call EOF: the pool owns it.
            raise EngineWorkerDied(
                'call',
                f'{self.name} worker exited without a result',
                returncode=getattr(process, 'exitcode', None),
            )
        if message[0] == 'ok':
            return message[1]
        if message[0] == 'timeout':
            _tag, worker_name, timeout_s, phase = message
            _pool_debug(
                f'{self.name}: worker-side timeout phase={phase} '
                f'wall={timeout_s}'
            )
            self._discard_current(
                diagnostic=False,
                expected_process=process,
                expected_connection=connection,
            )
            raise EngineWorkerTimeout(
                worker_name,
                timeout_s,
                phase=phase,
            )
        _tag, exc_name, detail, remote_traceback, *metadata = message
        failure_disposition = metadata[0] if metadata else None
        _pool_debug(
            f'{self.name}: remote error {exc_name}: {str(detail)[:120]}'
        )
        self._discard_current(
            diagnostic=False,
            expected_process=process,
            expected_connection=connection,
        )
        raise EngineWorkerRemoteError(
            exc_name, detail, remote_traceback, failure_disposition,
        )

    def _discard_current(
        self,
        *,
        diagnostic: bool,
        expected_process: Any = None,
        expected_connection: Any = None,
    ) -> None:
        with self._state_lock:
            if (
                expected_process is not None
                and (
                    self.process is not expected_process
                    or self.connection is not expected_connection
                )
            ):
                return
            process, connection = self.process, self.connection
            self.process = None
            self.connection = None
            self.ready_payload = None
        self._stop_pair(
            process,
            connection,
            diagnostic=diagnostic,
            cleanup_group=True,
        )

    def _abort_current_call(
        self,
        abort_event: threading.Event | None = None,
    ) -> bool:
        """Kill and reap the current worker without retiring this slot."""
        with self._state_lock:
            if abort_event is None:
                abort_event = self._pool_call_abort_event
            elif abort_event is not self._pool_call_abort_event:
                return False
            if abort_event is not None:
                abort_event.set()
            process, connection = self.process, self.connection
        if process is None and connection is None:
            return False
        self._discard_current(
            diagnostic=False,
            expected_process=process,
            expected_connection=connection,
        )
        return True

    def _stop_pair(
        self,
        process: Any,
        connection: Any,
        *,
        diagnostic: bool,
        cleanup_group: bool = False,
    ) -> None:
        try:
            pid = _process_pid(process)
            if pid is None:
                pass
            else:
                if process is not None and process.is_alive() and diagnostic:
                    try:
                        os.kill(pid, self.diagnostic_signal)
                    except OSError:
                        pass
                    time.sleep(self.watchdog_grace_s)

                # The diagnostic signal may have killed the leader while its
                # descendants remain in the session. Reap the group regardless
                # of the leader's liveness before falling back to Process.kill.
                if cleanup_group or (
                    process is not None and process.is_alive()
                ):
                    _signal_worker_process(process, signal.SIGKILL)
                    process.join(timeout=0.1)
                if process is not None and process.is_alive():
                    _signal_worker_process(process, signal.SIGKILL)
                if process is not None:
                    process.join(timeout=1.0)
        finally:
            if connection is not None:
                self._close_connection_or_defer(connection)
            self._finish_native_output_capture(process)

    def close(self) -> None:
        """Idempotently stop the worker and release its pipe."""
        with self._state_lock:
            process, connection = self.process, self.connection
            active_reader = (
                connection is not None
                and self._active_frame_readers.get(id(connection), 0) > 0
            )
            self.process = None
            self.connection = None
            self.ready_payload = None
        if active_reader:
            self._stop_pair(
                process,
                connection,
                diagnostic=False,
                cleanup_group=True,
            )
            self._reap_pending(timeout_s=1.0)
            return
        try:
            if connection is not None:
                try:
                    if (
                        process is not None
                        and _process_pid(process) is not None
                        and process.is_alive()
                    ):
                        connection.send(None)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                finally:
                    connection.close()
        finally:
            process_pid = _process_pid(process)
            if process is not None and process_pid is not None:
                process.join(timeout=1.0)
                if process.is_alive():
                    _signal_worker_process(process, signal.SIGTERM)
                    process.join(timeout=1.0)
                if process.is_alive():
                    _signal_worker_process(process, signal.SIGKILL)
                    process.join(timeout=1.0)
                _signal_worker_process(process, signal.SIGKILL)
            self._finish_native_output_capture(process)
        self._reap_pending(timeout_s=1.0)

    def request_disable(self) -> None:
        """Reject calls and synchronously signal the active process group."""
        with self._state_lock:
            self.disabled = True
            process, connection = self.process, self.connection
            active_reader = (
                connection is not None
                and self._active_frame_readers.get(id(connection), 0) > 0
            )
            self.process = None
            self.connection = None
            self.ready_payload = None
            if process is not None and process not in self._pending_reap:
                self._pending_reap.append(process)
        if active_reader:
            self._stop_pair(
                process,
                connection,
                diagnostic=False,
                cleanup_group=True,
            )
            return
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process is not None:
            pid = _process_pid(process)
            if pid is not None:
                _signal_worker_process(process, signal.SIGKILL)
                self._close_native_output_writer(process)

    def _reap_pending(self, *, timeout_s: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        with self._state_lock:
            processes = tuple(dict.fromkeys(self._pending_reap))
            self._pending_reap.clear()
        remaining_processes = []
        for process in processes:
            if _process_pid(process) is None:
                remaining_processes.append(process)
                continue
            remaining = max(0.0, deadline - time.monotonic())
            process.join(timeout=remaining)
            if process.is_alive():
                try:
                    process.kill()
                except (AttributeError, OSError):
                    pass
                process.join(timeout=max(0.0, deadline - time.monotonic()))
            if process.is_alive():
                remaining_processes.append(process)
            else:
                self._finish_native_output_capture(process)
        if remaining_processes:
            with self._state_lock:
                self._pending_reap.extend(remaining_processes)
            return False
        return True

    def disable(self) -> None:
        """Permanently reject new calls and kill any active request."""
        self.request_disable()
        self._reap_pending(timeout_s=1.0)


EngineWorkerTransport = WarmEngineWorker


REMOTE_ENGINE_PROTOCOL_VERSION = 1
REMOTE_ENGINE_MAX_LINE_BYTES = 64 * 1024
_REMOTE_ENGINE_STDERR_BYTES = 64 * 1024
_REMOTE_ENGINE_ERROR_DETAIL_CHARS = 1024
_REMOTE_ENGINE_CLOSE_WAIT_S = 0.2


class _RemoteEngineProtocolFailure(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.failure_disposition = code
        super().__init__(str(detail)[:_REMOTE_ENGINE_ERROR_DETAIL_CHARS])


def _encode_remote_engine_line(
    value: Mapping[str, Any],
    max_line_bytes: int,
) -> bytes:
    try:
        line = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(',', ':'),
        ).encode('utf-8') + b'\n'
    except (TypeError, ValueError, OverflowError) as exc:
        raise _RemoteEngineProtocolFailure(
            'invalid_request', f'request is not JSON-safe: {exc}',
        ) from exc
    if len(line) > max_line_bytes:
        raise _RemoteEngineProtocolFailure(
            'line_too_large',
            f'request line exceeds {max_line_bytes} bytes',
        )
    return line


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'duplicate JSON field {key!r}')
        value[key] = item
    return value


class _RemoteEngineStdioProcess:
    """One framed stdio process; stderr is drained and retained with a cap."""

    def __init__(self, process: subprocess.Popen[bytes], max_line_bytes: int):
        self.process = process
        self.max_line_bytes = max_line_bytes
        self.ready: dict[str, Any] = {}
        self._stderr_lock = threading.Lock()
        self._stderr = bytearray()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name='remote-engine-stderr',
            daemon=True,
        )
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        stream = self.process.stderr
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with self._stderr_lock:
                    room = _REMOTE_ENGINE_STDERR_BYTES - len(self._stderr)
                    if room > 0:
                        self._stderr.extend(chunk[:room])
        except (OSError, ValueError):
            return

    def _closed_stream_failure(self) -> _RemoteEngineProtocolFailure:
        returncode = self.process.poll()
        with self._stderr_lock:
            stderr = bytes(self._stderr).decode('utf-8', errors='replace').strip()
        detail = 'remote worker closed stdout before a complete response'
        if stderr:
            detail = f'{detail}; stderr: {stderr[-_REMOTE_ENGINE_ERROR_DETAIL_CHARS:]}'
        if returncode not in (None, 0):
            return _RemoteEngineProtocolFailure(
                'child_crash',
                f'remote worker exited with status {returncode}; {detail}',
            )
        return _RemoteEngineProtocolFailure('eof', detail)

    def read_json_line(self) -> dict[str, Any]:
        stream = self.process.stdout
        if stream is None:
            raise _RemoteEngineProtocolFailure(
                'startup_failure', 'remote worker stdout is unavailable',
            )
        pending = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(stream, selectors.EVENT_READ)
            while True:
                selector.select()
                chunk = os.read(
                    stream.fileno(),
                    min(4096, self.max_line_bytes + 1 - len(pending)),
                )
                if not chunk:
                    raise self._closed_stream_failure()
                newline = chunk.find(b'\n')
                if newline >= 0:
                    if len(pending) + newline + 1 > self.max_line_bytes:
                        raise _RemoteEngineProtocolFailure(
                            'line_too_large',
                            f'remote line exceeds {self.max_line_bytes} bytes',
                        )
                    if newline + 1 != len(chunk):
                        raise _RemoteEngineProtocolFailure(
                            'malformed_response',
                            'remote worker wrote more than one response line',
                        )
                    raw = bytes(pending) + chunk[:newline]
                    try:
                        value = json.loads(
                            raw.decode('utf-8'),
                            object_pairs_hook=_unique_json_object,
                        )
                    except (
                        UnicodeDecodeError,
                        json.JSONDecodeError,
                        ValueError,
                    ) as exc:
                        raise _RemoteEngineProtocolFailure(
                            'malformed_json', f'invalid remote JSON line: {exc}',
                        ) from exc
                    if not isinstance(value, dict):
                        raise _RemoteEngineProtocolFailure(
                            'malformed_response',
                            'remote response must be a JSON object',
                        )
                    return value
                if len(pending) + len(chunk) > self.max_line_bytes:
                    raise _RemoteEngineProtocolFailure(
                        'line_too_large',
                        f'remote line exceeds {self.max_line_bytes} bytes',
                    )
                pending.extend(chunk)

    def write_line(self, line: bytes) -> None:
        stream = self.process.stdin
        if stream is None:
            raise self._closed_stream_failure()
        view = memoryview(line)
        try:
            while view:
                written = os.write(stream.fileno(), view)
                if written <= 0:
                    raise self._closed_stream_failure()
                view = view[written:]
        except (BrokenPipeError, OSError) as exc:
            raise self._closed_stream_failure() from exc

    def close(self) -> None:
        stream = self.process.stdin
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=_REMOTE_ENGINE_CLOSE_WAIT_S)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except OSError:
            pass
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=_REMOTE_ENGINE_CLOSE_WAIT_S)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._stderr_thread.join(timeout=_REMOTE_ENGINE_CLOSE_WAIT_S)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass


def _remote_engine_stdio_bootstrap(
    argv: tuple[str, ...],
    protocol_version: int,
    max_line_bytes: int,
    pgid_sender: Any,
) -> tuple[_RemoteEngineStdioProcess, dict[str, Any]]:
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            close_fds=True,
            shell=False,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        raise _RemoteEngineProtocolFailure(
            'startup_failure', f'could not launch remote worker: {exc}',
        ) from exc
    try:
        pgid_sender.send(process.pid)
    except (BrokenPipeError, EOFError, OSError) as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.wait()
        raise _RemoteEngineProtocolFailure(
            'startup_failure', f'could not report remote process group: {exc}',
        ) from exc
    finally:
        pgid_sender.close()
    client = _RemoteEngineStdioProcess(process, max_line_bytes)
    try:
        ready = client.read_json_line()
        expected = {
            'protocol_version', 'engine_version', 'datafile_fingerprint',
        }
        if set(ready) != expected:
            raise _RemoteEngineProtocolFailure(
                'malformed_response', 'READY fields do not match the protocol',
            )
        ready_version = ready['protocol_version']
        if (
            isinstance(ready_version, bool)
            or not isinstance(ready_version, int)
            or ready_version != protocol_version
        ):
            raise _RemoteEngineProtocolFailure(
                'protocol_version_mismatch',
                f'expected protocol {protocol_version}, got '
                f'{ready["protocol_version"]!r}',
            )
        if any(
            not isinstance(ready[key], str)
            for key in ('engine_version', 'datafile_fingerprint')
        ):
            raise _RemoteEngineProtocolFailure(
                'malformed_response', 'READY identity fields must be strings',
            )
        client.ready = ready
        return client, ready
    except _RemoteEngineProtocolFailure as exc:
        client.close()
        if exc.failure_disposition in ('eof', 'child_crash'):
            raise _RemoteEngineProtocolFailure(
                'startup_failure', f'remote worker failed before READY: {exc}',
            ) from exc
        raise
    except BaseException:
        client.close()
        raise


def _remote_engine_stdio_handler(
    client: _RemoteEngineStdioProcess,
    call: tuple[dict[str, Any], bytes],
    _errlog: Any,
) -> dict[str, Any]:
    request, line = call
    client.write_line(line)
    response = client.read_json_line()
    if response.get('request_id') != request['request_id'] or response.get(
        'observation_id'
    ) != request['observation_id']:
        raise _RemoteEngineProtocolFailure(
            'wrong_id', 'remote response ids do not match the request',
        )
    status = response.get('status')
    if status == 'refused':
        code = response.get('code')
        detail = response.get('detail')
        if set(response) != {
            'request_id', 'observation_id', 'status', 'code', 'detail',
        } or not isinstance(code, str) or not code or not isinstance(detail, str):
            raise _RemoteEngineProtocolFailure(
                'malformed_response',
                'refused response fields do not match the protocol',
            )
        return response
    if status != 'produced' or set(response) != {
        'request_id', 'observation_id', 'status', 'quantity', 'scalar', 'unit',
        'engine_version', 'datafile_fingerprint',
    }:
        raise _RemoteEngineProtocolFailure(
            'malformed_response', 'produced response fields do not match the protocol',
        )
    if response['quantity'] != request['quantity']:
        raise _RemoteEngineProtocolFailure(
            'malformed_response', 'response quantity does not match the request',
        )
    if any(
        not isinstance(response[key], str)
        for key in ('unit', 'engine_version', 'datafile_fingerprint')
    ):
        raise _RemoteEngineProtocolFailure(
            'malformed_response', 'produced identity and unit fields must be strings',
        )
    if (
        response['engine_version'] != client.ready['engine_version']
        or response['datafile_fingerprint'] != client.ready['datafile_fingerprint']
    ):
        raise _RemoteEngineProtocolFailure(
            'malformed_response', 'response identity differs from READY',
        )
    scalar = response['scalar']
    if isinstance(scalar, bool) or not isinstance(scalar, (int, float)):
        raise _RemoteEngineProtocolFailure(
            'invalid_scalar', 'produced scalar must be one JSON number',
        )
    try:
        finite = math.isfinite(float(scalar))
    except (OverflowError, ValueError):
        finite = False
    if not finite:
        raise _RemoteEngineProtocolFailure(
            'non_finite_value', 'produced scalar must be finite',
        )
    return response


def _remote_engine_refusal(
    request_id: str,
    observation_id: str,
    code: str,
    detail: str,
) -> dict[str, str]:
    return {
        'request_id': request_id,
        'observation_id': observation_id,
        'status': 'refused',
        'code': code,
        'detail': str(detail)[:_REMOTE_ENGINE_ERROR_DETAIL_CHARS],
    }


def _remote_engine_failure_code(exc: BaseException, *, phase: str) -> str:
    if isinstance(exc, EngineWorkerTimeout):
        return 'ready_timeout' if exc.phase == 'initialization' else 'call_timeout'
    if isinstance(exc, EngineWorkerRemoteError):
        if exc.failure_disposition:
            return exc.failure_disposition
        return 'startup_failure' if phase == 'start' else 'child_crash'
    if isinstance(exc, EngineWorkerDied):
        return 'startup_failure' if exc.phase in ('start', 'restart') else 'child_crash'
    if isinstance(exc, EngineWorkerUnavailable):
        return 'startup_failure' if phase == 'start' else 'child_crash'
    return 'startup_failure' if phase == 'start' else 'child_crash'


class StdioRemoteEngineWorker(WarmEngineWorker):
    """Persistent argv-launched NDJSON worker with typed refusal results.

    The first stdout line is READY with exactly ``protocol_version``,
    ``engine_version``, and ``datafile_fingerprint``. Each slot owns one
    subprocess through the existing WarmEngineWorker lifecycle. Calls accept
    only observation_id, quantity, and conditions; result values are never
    cached.
    """

    def __init__(
        self,
        *,
        name: str,
        argv: Sequence[str],
        startup_timeout_s: float = 30.0,
        call_timeout_s: float = 60.0,
        max_line_bytes: int = REMOTE_ENGINE_MAX_LINE_BYTES,
    ) -> None:
        command = tuple(argv)
        if not command or not command[0] or any(
            not isinstance(part, str) for part in command
        ):
            raise ValueError('remote worker argv must be a nonempty string sequence')
        if isinstance(max_line_bytes, bool) or int(max_line_bytes) < 1:
            raise ValueError('remote worker max_line_bytes must be positive')
        self.max_line_bytes = int(max_line_bytes)
        self._pending_start_failure: BaseException | None = None
        spawn_context = multiprocessing.get_context('spawn')
        self._remote_pid_receive, self._remote_pid_sender = spawn_context.Pipe(
            duplex=False,
        )
        self._remote_process_group_id: int | None = None
        super().__init__(
            name=name,
            bootstrap=_remote_engine_stdio_bootstrap,
            handler=_remote_engine_stdio_handler,
            bootstrap_args=(
                command,
                REMOTE_ENGINE_PROTOCOL_VERSION,
                self.max_line_bytes,
                self._remote_pid_sender,
            ),
            startup_timeout_s=startup_timeout_s,
            call_timeout_s=call_timeout_s,
        )

    def start(self, *, timeout_s: Optional[float] = None) -> Any:
        try:
            ready = super().start(timeout_s=timeout_s)
        except Exception as exc:
            self._pending_start_failure = exc
            return None
        if not self._remote_pid_receive.poll(0.1):
            process, connection = self.process, self.connection
            self._discard_current(
                diagnostic=False,
                expected_process=process,
                expected_connection=connection,
            )
            self._pending_start_failure = _RemoteEngineProtocolFailure(
                'startup_failure', 'remote worker did not report its process group',
            )
            return None
        try:
            self._remote_process_group_id = int(
                self._remote_pid_receive.recv()
            )
        except (EOFError, OSError, TypeError, ValueError) as exc:
            process, connection = self.process, self.connection
            self._discard_current(
                diagnostic=False,
                expected_process=process,
                expected_connection=connection,
            )
            self._pending_start_failure = _RemoteEngineProtocolFailure(
                'startup_failure', f'invalid remote process group report: {exc}',
            )
            return None
        self._pending_start_failure = None
        return ready

    def _stop_remote_process_tree(
        self,
        process: Any,
        connection: Any,
    ) -> None:
        groups = []
        if self._remote_process_group_id is not None:
            groups.append(self._remote_process_group_id)
            self._remote_process_group_id = None
        receiver = self._remote_pid_receive
        deadline = time.monotonic() + (0.1 if process is not None else 0.0)
        while True:
            remaining = deadline - time.monotonic()
            if remaining > 0.0 and not receiver.poll(remaining):
                break
            if not receiver.poll():
                break
            try:
                groups.append(int(receiver.recv()))
            except (EOFError, OSError, TypeError, ValueError):
                break
        for group_id in dict.fromkeys(groups):
            if group_id <= 1 or group_id == os.getpgrp():
                continue
            try:
                os.killpg(group_id, signal.SIGKILL)
            except OSError:
                pass
        if (
            process is not None
            and process.is_alive()
            and connection is not None
        ):
            try:
                connection.send(None)
            except (BrokenPipeError, EOFError, OSError):
                pass
            process.join(timeout=_REMOTE_ENGINE_CLOSE_WAIT_S)

    def _stop_pair(
        self,
        process: Any,
        connection: Any,
        *,
        diagnostic: bool,
        cleanup_group: bool = False,
    ) -> None:
        self._stop_remote_process_tree(process, connection)
        super()._stop_pair(
            process,
            connection,
            diagnostic=diagnostic,
            cleanup_group=cleanup_group,
        )

    def request_disable(self) -> None:
        with self._state_lock:
            process, connection = self.process, self.connection
        self._stop_remote_process_tree(process, connection)
        super().request_disable()

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._remote_process_group_id = None

    def call(self, request: Any, *, timeout_s: Optional[float] = None) -> Any:
        request_id = uuid.uuid4().hex
        observation_id = ''
        if isinstance(request, Mapping):
            raw_observation_id = request.get('observation_id')
            if isinstance(raw_observation_id, str):
                observation_id = raw_observation_id
        expected = {'observation_id', 'quantity', 'conditions'}
        if not isinstance(request, Mapping) or set(request) != expected:
            return _remote_engine_refusal(
                request_id, observation_id, 'invalid_request',
                'call requires observation_id, quantity, and conditions',
            )
        if (
            not isinstance(request['observation_id'], str)
            or not request['observation_id']
            or not isinstance(request['quantity'], str)
            or not request['quantity']
            or not isinstance(request['conditions'], Mapping)
        ):
            return _remote_engine_refusal(
                request_id, observation_id, 'invalid_request',
                'request identity, quantity, and conditions have invalid types',
            )
        wire_request = {
            'request_id': request_id,
            'observation_id': request['observation_id'],
            'quantity': request['quantity'],
            'conditions': request['conditions'],
        }
        try:
            encoded = _encode_remote_engine_line(
                {
                    'protocol_version': REMOTE_ENGINE_PROTOCOL_VERSION,
                    **wire_request,
                },
                self.max_line_bytes,
            )
        except _RemoteEngineProtocolFailure as exc:
            return _remote_engine_refusal(
                request_id,
                request['observation_id'],
                exc.failure_disposition,
                str(exc),
            )
        if self._pending_start_failure is not None:
            exc = self._pending_start_failure
            self._pending_start_failure = None
            return _remote_engine_refusal(
                request_id,
                request['observation_id'],
                _remote_engine_failure_code(exc, phase='start'),
                str(exc),
            )
        try:
            return super().call((wire_request, encoded), timeout_s=timeout_s)
        except Exception as exc:
            failure = self._pending_start_failure or exc
            self._pending_start_failure = None
            return _remote_engine_refusal(
                request_id,
                request['observation_id'],
                _remote_engine_failure_code(failure, phase='call'),
                str(failure),
            )


class EngineWorkerPool:
    """Queue-fed work-stealing pool of independent warm engine transports."""

    def __init__(
        self,
        worker_factory: Callable[[int], WarmEngineWorker],
        *,
        size: int,
    ) -> None:
        self._queue: queue.Queue[Any] = queue.Queue()
        self._workers = [worker_factory(index) for index in range(max(1, size))]
        self._threads: list[threading.Thread] = []
        self._active_requests: dict[
            Future,
            tuple[WarmEngineWorker, threading.Event, threading.Event],
        ] = {}
        self._request_workers: weakref.WeakKeyDictionary[
            Future, WarmEngineWorker
        ] = weakref.WeakKeyDictionary()
        self._diagnostic_written_futures: weakref.WeakSet[Future] = (
            weakref.WeakSet()
        )
        self._active_requests_lock = threading.Lock()
        self._closed = False
        self._lifecycle_lock = threading.Lock()
        self._close_complete = threading.Event()
        self._cancel_pending = threading.Event()
        self._pool_seq = _next_engine_pool_seq()
        _register_engine_pool(self)
        ready: queue.Queue[tuple[int, Optional[BaseException]]] = queue.Queue()
        ready_wait_s = _pool_ready_wait_s(self._workers)
        deadline = time.monotonic() + ready_wait_s
        for index, worker in enumerate(self._workers):
            thread = threading.Thread(
                target=self._start_and_consume,
                args=(index, worker, ready),
                # Name carries POOL identity, not just the slot index.
                # 2026-08-29: the old `engine-worker-slot-{index}` was not
                # unique across pools, and three production sites build pools
                # in one process (melt_backend/magemin.py, melt_backend/
                # vaporock.py, optimize/pool.py) — so two warm backends always
                # yield two live threads both named `engine-worker-slot-0`.
                # A `sample` of a hung process therefore reads as "two threads
                # on one slot sharing one queue" when it is in fact two
                # independent pools with a `queue.Queue` each; that misreading
                # happened to a careful investigator within an hour of looking.
                # The name is set here and read NOWHERE else in the tree, so
                # its ONLY job is stack forensics — which is precisely the job
                # it was failing. Mirror `_pool_debug`'s `pool[{worker.name}#
                # {index}]` shape so a stack and a debug line name the same
                # thing, and add the per-process pool sequence so two pools of
                # the SAME backend stay distinguishable.
                name=(
                    f'engine-pool{self._pool_seq}'
                    f'-{worker.name}-slot-{index}'
                ),
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        reported: dict[int, Optional[BaseException]] = {}
        for _worker in self._workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            try:
                index, error = ready.get(timeout=remaining)
            except queue.Empty:
                break
            reported[index] = error
        # P2-1: a report already queued when remaining hits 0 must still
        # be taken. The early break would otherwise classify a successful
        # or typed-error put as missing.
        while True:
            try:
                index, error = ready.get_nowait()
            except queue.Empty:
                break
            reported[index] = error
        missing = [
            index
            for index in range(len(self._workers))
            if index not in reported
        ]
        errors = [
            (index, error)
            for index, error in reported.items()
            if error is not None
        ]
        if missing or errors:
            close_error: BaseException | None = None
            try:
                self.close(cancel_pending=True)
            except BaseException as exc:
                close_error = exc
            # Prefer a slot's own typed error over the generic missing-slot
            # refusal. Losing start()'s worker_name is the defect: a
            # consumer discarding what the producer typed.
            if errors:
                if close_error is not None:
                    raise errors[0][1] from close_error
                raise errors[0][1]
            refusal = EngineWorkerTimeout(
                f'engine worker pool slots {missing}',
                ready_wait_s,
                phase='initialization',
                failed_slots=missing,
            )
            if close_error is not None:
                raise refusal from close_error
            raise refusal

    @property
    def workers(self) -> tuple[WarmEngineWorker, ...]:
        return tuple(self._workers)

    def submit(self, request: Any, *, timeout_s: Optional[float] = None) -> Future:
        with self._lifecycle_lock:
            if self._closed:
                raise EngineWorkerPoolLifecycleError(
                    'engine worker pool is closed'
                )
            # A queued item MUST have someone who will run it. `timeout_s` is
            # stashed in the queue item and enforced later by `_consume` ->
            # `worker.call`; callers that need a whole-wait wall must also
            # bound `Future.result()` in case a consumer exits after this
            # check.
            #
            # 2026-08-29, reproduced: retire the only consumer, then submit --
            # `_closed` is still False so the old guard passed, and the future
            # was still `pending` after 5x its 1 s wall, `done()` False. That
            # is the shape of a 15-minute 0.0%-CPU block observed on a quiet
            # box by the peer controller. Load cannot produce a 0.0% CPU stop:
            # a starved process still burns CPU when scheduled, so a condvar
            # wait at 0.0% is a logical block, not contention.
            #
            # Fail fast and typed instead. This cannot fire on a merely BUSY
            # pool -- a busy consumer is an alive consumer, and a legitimately
            # long queue wait keeps every thread alive. It fires only when no
            # thread remains that could ever complete the future.
            #
            # This is a pool lifecycle refusal. It is NOT
            # EngineWorkerUnavailable: that type is read
            # downstream as engine ABSENCE and routes to a fallback, which
            # would convert this loud refusal into a quiet substitution of
            # analytical curves for native results. A hang is bad; wrong
            # numbers that nobody notices are worse.
            if not any(thread.is_alive() for thread in self._threads):
                raise EngineWorkerPoolLifecycleError(
                    f'engine worker pool has no live consumer to run this '
                    f'request ({len(self._threads)} slot(s), all exited); '
                    f'submitting would leave the future permanently pending'
                )
            future: Future = Future()
            self._queue.put((future, request, timeout_s))
        return future

    def _abort_request(
        self,
        future: Future,
        *,
        diagnostic_detail: str | None = None,
    ) -> bool:
        """Abort the worker currently running ``future`` and keep its slot.

        The active-request lock prevents the consumer from releasing this
        worker for its next request until the current process has been killed
        and reaped. Diagnostic detail is appended to the assigned worker's
        existing diagnostic log after the lock is released. Pending futures
        have no assigned worker and return False.
        """
        diagnostic_path = None
        should_log = False
        aborted = False
        with self._active_requests_lock:
            active = self._active_requests.get(future)
            if active is None:
                worker = self._request_workers.get(future)
            else:
                worker, call_finished, abort_event = active
                if not call_finished.is_set():
                    aborted = worker._abort_current_call(abort_event)
            if (
                diagnostic_detail is not None
                and worker is not None
                and future not in self._diagnostic_written_futures
            ):
                self._diagnostic_written_futures.add(future)
                diagnostic_path = worker.diagnostic_log_path
                should_log = diagnostic_path is not None

        if should_log and diagnostic_path is not None:
            try:
                diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
                with diagnostic_path.open('a', encoding='utf-8') as log:
                    log.write(f'{diagnostic_detail}\n')
            except OSError:
                # Logging must not prevent worker cleanup or replace the
                # typed timeout returned to the caller.
                pass
        return aborted

    def _start_and_consume(
        self,
        index: int,
        worker: WarmEngineWorker,
        ready: queue.Queue[tuple[int, Optional[BaseException]]],
    ) -> None:
        try:
            t0 = time.monotonic()
            _pool_debug(f'pool[{worker.name}#{index}]: starting worker')
            worker.start()
            _pool_debug(
                f'pool[{worker.name}#{index}]: ready in '
                f'{time.monotonic() - t0:.2f}s pid='
                f'{getattr(worker.process, "pid", None)}'
            )
        except BaseException as exc:
            _pool_debug(
                f'pool[{worker.name}#{index}]: start FAILED '
                f'{type(exc).__name__}: {str(exc)[:120]}'
            )
            ready.put((index, exc))
            return
        ready.put((index, None))
        self._consume(worker)

    def _consume(self, worker: WarmEngineWorker) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                future, request, timeout_s = item
                cancel_pending = False
                with self._active_requests_lock:
                    if self._cancel_pending.is_set():
                        cancel_pending = True
                    elif not future.set_running_or_notify_cancel():
                        continue
                    else:
                        call_finished = threading.Event()
                        abort_event = worker._begin_pool_call()
                        self._request_workers[future] = worker
                        self._active_requests[future] = (
                            worker,
                            call_finished,
                            abort_event,
                        )
                if cancel_pending:
                    future.cancel()
                    continue
                try:
                    result = worker.call(request, timeout_s=timeout_s)
                except BaseException as exc:
                    call_finished.set()
                    with self._active_requests_lock:
                        self._active_requests.pop(future, None)
                        worker._end_pool_call(abort_event)
                    future.set_exception(exc)
                else:
                    call_finished.set()
                    with self._active_requests_lock:
                        self._active_requests.pop(future, None)
                        worker._end_pool_call(abort_event)
                    future.set_result(result)
            finally:
                self._queue.task_done()

    def _request_cancel_pending(self) -> None:
        self._cancel_pending.set()
        sentinels = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                if item is None:
                    sentinels += 1
                else:
                    future, _request, _timeout_s = item
                    future.cancel()
            finally:
                self._queue.task_done()
        for _sentinel in range(sentinels):
            self._queue.put(None)
        for worker in self._workers:
            worker.request_disable()

    def close(
        self,
        *,
        cancel_pending: bool = False,
        timeout_s: Optional[float] = None,
    ) -> None:
        _pool_debug(f'pool: close(cancel_pending={cancel_pending}) called')
        if cancel_pending and timeout_s is None:
            timeout_s = ENGINE_POOL_AUTOMATIC_PER_POOL_BUDGET_S
        deadline = (
            None
            if timeout_s is None
            else time.monotonic() + max(0.001, float(timeout_s))
        )
        owns_close = False
        with self._lifecycle_lock:
            if cancel_pending:
                self._cancel_pending.set()
            if not self._closed:
                self._closed = True
                owns_close = True
        if cancel_pending:
            self._request_cancel_pending()
        if not owns_close:
            wait_timeout = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            if not self._close_complete.wait(timeout=wait_timeout):
                raise EngineWorkerTimeout(
                    'engine worker pool cleanup',
                    max(0.001, float(timeout_s)),
                    phase='shutdown',
                )
            _discard_engine_pool(self)
            return
        close_timeout: EngineWorkerTimeout | None = None
        try:
            live_threads = [
                thread for thread in self._threads if thread.is_alive()
            ]
            for _thread in live_threads:
                self._queue.put(None)
            for thread in live_threads:
                join_timeout = (
                    None
                    if deadline is None
                    else max(0.0, deadline - time.monotonic())
                )
                thread.join(timeout=join_timeout)
            if any(thread.is_alive() for thread in live_threads):
                self._request_cancel_pending()
                close_timeout = EngineWorkerTimeout(
                    'engine worker pool cleanup',
                    max(0.001, float(timeout_s)),
                    phase='shutdown',
                )
            if cancel_pending:
                for worker in self._workers:
                    reap_timeout = max(
                        0.0,
                        (deadline or time.monotonic()) - time.monotonic(),
                    )
                    if not worker._reap_pending(timeout_s=reap_timeout):
                        close_timeout = close_timeout or EngineWorkerTimeout(
                            'engine worker pool cleanup',
                            max(0.001, float(timeout_s)),
                            phase='shutdown',
                        )
            else:
                for worker in self._workers:
                    worker.close()
        finally:
            self._close_complete.set()
            _discard_engine_pool(self)
        if close_timeout is not None:
            raise close_timeout

    def __enter__(self) -> 'EngineWorkerPool':
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()


def close_all_engine_pools(
    *,
    cancel_pending: bool = True,
    per_pool_timeout_s: float = ENGINE_POOL_AUTOMATIC_PER_POOL_BUDGET_S,
    total_timeout_s: float = ENGINE_POOL_AUTOMATIC_TOTAL_BUDGET_S,
) -> int:
    """Close live pools within 3 s per pool and a 5 s process-wide wait budget."""
    with _ENGINE_POOL_REGISTRY_LOCK:
        pools = tuple(_LIVE_ENGINE_POOLS)

    outcomes: dict[EngineWorkerPool, BaseException | None] = {}
    outcomes_lock = threading.Lock()

    def close_pool(pool: EngineWorkerPool) -> None:
        error: BaseException | None = None
        try:
            pool.close(
                cancel_pending=cancel_pending,
                timeout_s=max(0.001, float(per_pool_timeout_s)),
            )
        except BaseException as exc:
            error = exc
        with outcomes_lock:
            outcomes[pool] = error

    threads = [
        threading.Thread(
            target=close_pool,
            args=(pool,),
            name='engine-pool-automatic-cleanup',
            daemon=True,
        )
        for pool in pools
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + max(0.001, float(total_timeout_s))
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))

    first_error: BaseException | None = None
    for pool, thread in zip(pools, threads):
        if thread.is_alive():
            pool._request_cancel_pending()
            error: BaseException | None = EngineWorkerTimeout(
                'all engine pool cleanup',
                max(0.001, float(total_timeout_s)),
                phase='shutdown',
            )
        else:
            error = outcomes.get(pool)
        if first_error is None and error is not None:
            first_error = error
    closed = sum(
        1
        for pool, thread in zip(pools, threads)
        if not thread.is_alive() and outcomes.get(pool) is None
    )
    if first_error is not None:
        raise first_error
    return closed
