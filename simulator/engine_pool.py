"""Engine-agnostic persistent-process isolation for native engine adapters.

The module deliberately imports no engine implementation.  Engine adapters
provide small, module-level bootstrap and request callables so ``spawn`` can
load heavyweight native dependencies inside the killable child only.
"""

from __future__ import annotations

import atexit
import faulthandler
import multiprocessing
import os
import signal
import queue
import sys
import threading
import time
import traceback
import weakref
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, Optional

ENGINE_WORKER_CANCEL_POLL_S = 0.05
ENGINE_POOL_AUTOMATIC_PER_POOL_BUDGET_S = 3.0
ENGINE_POOL_AUTOMATIC_TOTAL_BUDGET_S = 5.0

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
_LIVE_ENGINE_POOLS: weakref.WeakSet[Any] = weakref.WeakSet()
_ENGINE_POOL_REGISTRY_LOCK = threading.Lock()
_ENGINE_POOL_ATEXIT_REGISTERED = False


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

    def __init__(self, exc_name: str, detail: str, remote_traceback: str):
        super().__init__(detail)
        self.exc_name = exc_name
        self.detail = detail
        self.remote_traceback = remote_traceback


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
        self._state_lock = threading.Lock()
        self._pending_reap: list[Any] = []

    def start(self, *, timeout_s: Optional[float] = None) -> Any:
        """Start a fresh worker and wait for its bootstrap acknowledgement."""
        self.close()
        with self._state_lock:
            if self.disabled:
                raise RuntimeError(f'{self.name} worker is disabled')
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
        with self._state_lock:
            if self.disabled:
                cancelled = True
            else:
                self.process = process
                self.connection = parent
                self.ready_payload = None
                cancelled = False
        if cancelled:
            self._stop_pair(
                process,
                parent,
                diagnostic=False,
                cleanup_group=True,
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
                    )
                if not current:
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
            message = parent.recv()
        except (EOFError, OSError, TimeoutError):
            self._discard_current(
                diagnostic=False,
                expected_process=process,
                expected_connection=parent,
            )
            raise
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
            _tag, exc_name, detail, remote_traceback = message
            raise EngineWorkerRemoteError(exc_name, detail, remote_traceback)
        with self._state_lock:
            if (
                self.disabled
                or self.process is not process
                or self.connection is not parent
            ):
                raise RuntimeError(f'{self.name} worker is disabled')
            self.ready_payload = message[1]
            self.start_count += 1
            return self.ready_payload

    def call(self, request: Any, *, timeout_s: Optional[float] = None) -> Any:
        """Run one request; replace a dead or timed-out worker before return."""
        with self._state_lock:
            if self.disabled:
                raise RuntimeError(f'{self.name} worker is disabled')
            process = self.process
            connection = self.connection
        timeout = self.call_timeout_s if timeout_s is None else max(
            0.001, float(timeout_s))
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
            self.start()
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
            connection.send(request)
            deadline = time.monotonic() + remaining
            while True:
                with self._state_lock:
                    current = (
                        not self.disabled
                        and self.process is process
                        and self.connection is connection
                    )
                if not current:
                    raise RuntimeError(
                        f'{self.name} worker exited without a result'
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
            message = connection.recv()
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
            raise RuntimeError(
                f'{self.name} worker exited without a result'
            ) from exc
        with self._state_lock:
            current = (
                not self.disabled
                and self.process is process
                and self.connection is connection
            )
        if not current:
            raise RuntimeError(f'{self.name} worker exited without a result')
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
        _tag, exc_name, detail, remote_traceback = message
        _pool_debug(
            f'{self.name}: remote error {exc_name}: {str(detail)[:120]}'
        )
        self._discard_current(
            diagnostic=False,
            expected_process=process,
            expected_connection=connection,
        )
        raise EngineWorkerRemoteError(exc_name, detail, remote_traceback)

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
        with self._state_lock:
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
        self._reap_pending(timeout_s=1.0)

    def request_disable(self) -> None:
        """Reject calls and synchronously signal the active process group."""
        with self._state_lock:
            self.disabled = True
            process, connection = self.process, self.connection
            self.process = None
            self.connection = None
            self.ready_payload = None
            if process is not None:
                self._pending_reap.append(process)
        if connection is not None:
            connection.close()
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, OSError):
            try:
                process.kill()
            except OSError:
                pass

    def _reap_pending(self, *, timeout_s: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        with self._state_lock:
            processes = tuple(self._pending_reap)
            self._pending_reap.clear()
        remaining_processes = []
        for process in processes:
            remaining = max(0.0, deadline - time.monotonic())
            process.join(timeout=remaining)
            if process.is_alive():
                try:
                    process.kill()
                except OSError:
                    pass
                process.join(timeout=max(0.0, deadline - time.monotonic()))
            if process.is_alive():
                remaining_processes.append(process)
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
        _register_engine_pool(self)
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
