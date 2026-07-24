"""Engine-agnostic persistent-process isolation for native engine adapters.

The module deliberately imports no engine implementation.  Engine adapters
provide small, module-level bootstrap and request callables so ``spawn`` can
load heavyweight native dependencies inside the killable child only.
"""

from __future__ import annotations

import faulthandler
import multiprocessing
import os
import signal
import queue
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, Optional

# Env-gated lifecycle trace (2026-07-24): the recursive-spawn churn class
# was invisible because every failure path was silently absorbed into
# engine fallbacks. REGOLITH_ENGINE_POOL_DEBUG=1 prints starts, ready
# latencies, and every discard with its reason to stderr.
_POOL_DEBUG = os.environ.get('REGOLITH_ENGINE_POOL_DEBUG') == '1'


def _pool_debug(msg: str) -> None:
    if _POOL_DEBUG:
        print(f'[engine-pool {os.getpid()} {time.monotonic():.2f}] {msg}',
              file=sys.stderr, flush=True)


WorkerBootstrap = Callable[..., tuple[Any, Any]]
WorkerHandler = Callable[[Any, Any, Any], Any]
INHERIT_PROCESS_GROUP_ENV = 'REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP'


class EngineWorkerRemoteError(RuntimeError):
    """An engine request raised inside its isolated worker."""

    def __init__(self, exc_name: str, detail: str, remote_traceback: str):
        super().__init__(detail)
        self.exc_name = exc_name
        self.detail = detail
        self.remote_traceback = remote_traceback


class EngineWorkerTimeout(TimeoutError):
    """Typed hard-wall expiry for an isolated engine worker."""

    def __init__(self, worker_name: str, timeout_s: float, *, phase: str):
        self.worker_name = str(worker_name)
        self.timeout_s = float(timeout_s)
        self.phase = str(phase)
        super().__init__(
            f'{self.worker_name} {self.phase} exceeded hard timeout of '
            f'{self.timeout_s:g}s'
        )


def _run_engine_worker(
    connection: Any,
    bootstrap: WorkerBootstrap,
    handler: WorkerHandler,
    bootstrap_args: tuple[Any, ...],
    diagnostic_log_path: Optional[str],
    diagnostic_signal: int,
) -> None:
    """Run one initialized engine resource until clean shutdown or failure."""
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


class WarmEngineWorker:
    """Persistent spawned worker with deadlines and crash/hang replacement."""

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
        watchdog_grace_s: float = 0.25,
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

    def start(self, *, timeout_s: Optional[float] = None) -> Any:
        """Start a fresh worker and wait for its bootstrap acknowledgement."""
        self.close()
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_run_engine_worker,
            args=(
                child,
                self._bootstrap,
                self._handler,
                self._bootstrap_args,
                str(self.diagnostic_log_path) if self.diagnostic_log_path else None,
                self.diagnostic_signal,
            ),
            daemon=self.daemon,
        )
        try:
            process.start()
        except BaseException:
            child.close()
            parent.close()
            raise
        child.close()
        try:
            startup_timeout = self.startup_timeout_s
            if timeout_s is not None:
                startup_timeout = min(
                    startup_timeout,
                    max(0.001, float(timeout_s)),
                )
            if not parent.poll(startup_timeout):
                raise EngineWorkerTimeout(
                    self.name,
                    startup_timeout,
                    phase='initialization',
                )
            message = parent.recv()
        except (EOFError, OSError, TimeoutError):
            self._stop_pair(
                process,
                parent,
                diagnostic=False,
                cleanup_group=True,
            )
            raise
        if message[0] == 'timeout':
            self._stop_pair(
                process,
                parent,
                diagnostic=False,
                cleanup_group=True,
            )
            _tag, worker_name, timeout_s, phase = message
            raise EngineWorkerTimeout(
                worker_name,
                timeout_s,
                phase=phase,
            )
        if message[0] != 'ready':
            self._stop_pair(
                process,
                parent,
                diagnostic=False,
                cleanup_group=True,
            )
            _tag, exc_name, detail, remote_traceback = message
            raise EngineWorkerRemoteError(exc_name, detail, remote_traceback)
        self.process = process
        self.connection = parent
        self.ready_payload = message[1]
        self.start_count += 1
        return self.ready_payload

    def call(self, request: Any, *, timeout_s: Optional[float] = None) -> Any:
        """Run one request; replace a dead or timed-out worker before return."""
        if self.disabled:
            raise RuntimeError(f'{self.name} worker is disabled')
        timeout = self.call_timeout_s if timeout_s is None else max(
            0.001, float(timeout_s))
        if self.process is not None and not self.process.is_alive():
            _pool_debug(f'{self.name}: worker pid found dead at call entry')
            self._discard_current(diagnostic=False)
        if self.process is None:
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
            self.start()
            _pool_debug(
                f'{self.name}: cold start ok in '
                f'{time.monotonic() - t_start:.2f}s pid={self.process.pid}'
            )
        started_at = time.monotonic()
        process = self.process
        connection = self.connection
        if process is None or connection is None:
            raise RuntimeError(f'{self.name} worker is unavailable')
        if self.disabled:
            self._discard_current(diagnostic=False)
            raise RuntimeError(f'{self.name} worker is disabled')
        remaining = timeout - max(0.0, time.monotonic() - started_at)
        if remaining <= 0.0:
            self._discard_current(diagnostic=True)
            raise EngineWorkerTimeout(
                self.name,
                timeout,
                phase='job',
            )
        try:
            connection.send(request)
            if not connection.poll(remaining):
                _pool_debug(
                    f'{self.name}: job poll timeout after {remaining:.2f}s '
                    f'(wall {timeout:.2f}s) — discarding worker'
                )
                self._discard_current(diagnostic=True)
                raise EngineWorkerTimeout(
                    self.name,
                    timeout,
                    phase='job',
                )
            message = connection.recv()
        except TimeoutError:
            raise
        except (BrokenPipeError, EOFError, OSError) as exc:
            _pool_debug(
                f'{self.name}: worker died mid-call ({type(exc).__name__})'
            )
            self._discard_current(diagnostic=False)
            raise RuntimeError(
                f'{self.name} worker exited without a result'
            ) from exc
        if message[0] == 'ok':
            return message[1]
        if message[0] == 'timeout':
            _tag, worker_name, timeout_s, phase = message
            _pool_debug(
                f'{self.name}: worker-side timeout phase={phase} '
                f'wall={timeout_s}'
            )
            self._discard_current(diagnostic=False)
            raise EngineWorkerTimeout(
                worker_name,
                timeout_s,
                phase=phase,
            )
        _tag, exc_name, detail, remote_traceback = message
        _pool_debug(
            f'{self.name}: remote error {exc_name}: {str(detail)[:120]}'
        )
        self._discard_current(diagnostic=False)
        raise EngineWorkerRemoteError(exc_name, detail, remote_traceback)

    def _discard_current(self, *, diagnostic: bool) -> None:
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

    def _stop_pair(
        self,
        process: Any,
        connection: Any,
        *,
        diagnostic: bool,
        cleanup_group: bool = False,
    ) -> None:
        try:
            if cleanup_group and process is not None and not process.is_alive():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (AttributeError, OSError):
                    pass
            if process is not None and process.is_alive():
                if diagnostic:
                    try:
                        os.kill(process.pid, self.diagnostic_signal)
                    except OSError:
                        pass
                    time.sleep(self.watchdog_grace_s)
                if process.is_alive():
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.join(timeout=0.1)
                    except (AttributeError, OSError):
                        pass
                if process.is_alive():
                    try:
                        process.kill()
                    except OSError:
                        pass
                process.join(timeout=1.0)
        finally:
            if connection is not None:
                connection.close()

    def close(self) -> None:
        """Idempotently stop the worker and release its pipe."""
        process, connection = self.process, self.connection
        self.process = None
        self.connection = None
        self.ready_payload = None
        try:
            if connection is not None:
                try:
                    if process is not None and process.is_alive():
                        connection.send(None)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                finally:
                    connection.close()
        finally:
            if process is not None:
                process_pid = getattr(process, 'pid', None)
                process.join(timeout=1.0)
                if process.is_alive():
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except (AttributeError, OSError):
                        process.terminate()
                    process.join(timeout=1.0)
                if process.is_alive():
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (AttributeError, OSError):
                        process.kill()
                    process.join(timeout=1.0)
                if process_pid is not None:
                    try:
                        os.killpg(process_pid, signal.SIGKILL)
                    except (AttributeError, OSError):
                        pass

    def disable(self) -> None:
        """Permanently reject new calls and kill any active request."""
        self.disabled = True
        self._discard_current(diagnostic=False)


EngineWorkerTransport = WarmEngineWorker


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
        self._closed = False
        self._lifecycle_lock = threading.Lock()
        self._close_complete = threading.Event()
        self._cancel_pending = threading.Event()
        ready: queue.Queue[tuple[int, Optional[BaseException]]] = queue.Queue()
        for index, worker in enumerate(self._workers):
            thread = threading.Thread(
                target=self._start_and_consume,
                args=(index, worker, ready),
                name=f'engine-worker-slot-{index}',
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        errors = []
        for _worker in self._workers:
            index, error = ready.get()
            if error is not None:
                errors.append((index, error))
        if errors:
            self.close(cancel_pending=True)
            raise errors[0][1]

    @property
    def workers(self) -> tuple[WarmEngineWorker, ...]:
        return tuple(self._workers)

    def submit(self, request: Any, *, timeout_s: Optional[float] = None) -> Future:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError('engine worker pool is closed')
            future: Future = Future()
            self._queue.put((future, request, timeout_s))
        return future

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
                if self._cancel_pending.is_set():
                    future.cancel()
                    continue
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(worker.call(request, timeout_s=timeout_s))
                except BaseException as exc:
                    future.set_exception(exc)
            finally:
                self._queue.task_done()

    def close(self, *, cancel_pending: bool = False) -> None:
        _pool_debug(f'pool: close(cancel_pending={cancel_pending}) called')
        owns_close = False
        with self._lifecycle_lock:
            if not self._closed:
                self._closed = True
                owns_close = True
                if cancel_pending:
                    self._cancel_pending.set()
        if not owns_close:
            self._close_complete.wait()
            return
        try:
            if cancel_pending:
                while True:
                    try:
                        item = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        if item is not None:
                            future, _request, _timeout_s = item
                            future.cancel()
                    finally:
                        self._queue.task_done()
                for worker in self._workers:
                    worker.disable()
            live_threads = [
                thread for thread in self._threads if thread.is_alive()
            ]
            for _thread in live_threads:
                self._queue.put(None)
            for thread in live_threads:
                thread.join()
            for worker in self._workers:
                worker.close()
        finally:
            self._close_complete.set()

    def __enter__(self) -> 'EngineWorkerPool':
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
