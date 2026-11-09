"""Ordered process mapping with bounded task and worker-start deadlines."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from functools import partial
import os
import signal
import time
from typing import Any, TypeVar

Task = TypeVar("Task")
Result = TypeVar("Result")
_POLL_SECONDS = 0.1
DEFAULT_PROCESS_TASK_TIMEOUT_SECONDS = 45 * 60


def startup_timeout_seconds(task_timeout_seconds: float) -> float:
    """Bound submit to task-start time, leaving the full timeout for execution."""
    return min(300.0, max(30.0, float(task_timeout_seconds)))


def pool_wait_timeout(
    futures: Mapping[Future[Any], Task],
    submitted_at: Mapping[Future[Any], float],
    started_at: Mapping[Future[Any], float],
    timeout_seconds: float | None,
) -> float | None:
    if timeout_seconds is None:
        return None
    if not futures:
        return _POLL_SECONDS
    now = time.monotonic()
    startup_timeout = startup_timeout_seconds(timeout_seconds)
    remaining: list[float] = []
    for future in futures:
        start = started_at.get(future)
        if start is not None:
            remaining.append(max(0.0, timeout_seconds - (now - start)))
        else:
            submitted = submitted_at.get(future, now)
            remaining.append(max(0.0, startup_timeout - (now - submitted)))
    return min(_POLL_SECONDS, min(remaining, default=_POLL_SECONDS))


def expired_futures(
    futures: Mapping[Future[Any], Task],
    submitted_at: Mapping[Future[Any], float],
    started_at: Mapping[Future[Any], float],
    timeout_seconds: float | None,
) -> tuple[Future[Any], ...]:
    if timeout_seconds is None:
        return ()
    now = time.monotonic()
    startup_timeout = startup_timeout_seconds(timeout_seconds)
    expired: list[Future[Any]] = []
    for future in futures:
        start = started_at.get(future)
        if start is not None:
            if now - start >= timeout_seconds:
                expired.append(future)
            continue
        submitted = submitted_at.get(future, now)
        if now - submitted >= startup_timeout:
            expired.append(future)
    return tuple(expired)


def _initialize_worker(
    initializer: Callable[..., Any] | None,
    initargs: tuple[Any, ...],
) -> None:
    if os.name == "posix" and hasattr(os, "setsid"):
        try:
            os.setsid()
        except OSError:
            pass
    if initializer is not None:
        initializer(*initargs)


class SupervisedProcessPoolUnavailable(RuntimeError):
    """The process pool could not be created or accept submitted work."""


def supervised_process_map(
    tasks: Sequence[Task],
    worker: Callable[..., Result],
    *,
    max_workers: int | None,
    initializer: Callable[..., Any] | None = None,
    initargs: tuple[Any, ...] = (),
    worker_args: tuple[Any, ...] = (),
    wait_fn: Callable[..., Any] = wait,
    mp_context: Any = None,
    max_inflight_per_worker: int = 1,
    timeout_seconds: float | None = None,
    has_started: Callable[[Task], bool] | None = None,
    before_submit: Callable[[Task], None] | None = None,
    handle_result: Callable[[Task, Result], Any] | None = None,
    handle_timeout: Callable[[Task, float], Any] | None = None,
    handle_worker_error: Callable[[Task, BaseException], BaseException] | None = None,
    task_label: Callable[[Task], str] = repr,
    requeue_key: Callable[[Task], Any] | None = None,
    executor_factory: Callable[
        [int | None, Callable[..., Any] | None, Any], ProcessPoolExecutor
    ]
    | None = None,
    abort_executor: Callable[
        [ProcessPoolExecutor, Mapping[Future[Any], Task], BaseException, Sequence[Task]],
        bool,
    ]
    | None = None,
    is_unavailable: Callable[[BaseException], bool] | None = None,
) -> tuple[Any, ...]:
    """Run tasks in an ordered, supervised ``ProcessPoolExecutor``.

    ``has_started`` lets callers identify the worker's actual task-start
    marker, so execution time excludes executor startup. Timed-out tasks may
    be converted by ``handle_timeout``; remaining in-flight tasks are requeued
    in input order after their worker processes are stopped.
    """
    if not tasks:
        return ()
    if max_inflight_per_worker < 1:
        raise ValueError("max_inflight_per_worker must be at least 1")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    worker_count = max_workers or (os.cpu_count() or 1)
    max_inflight = max(1, worker_count * max_inflight_per_worker)
    pending_tasks = deque(enumerate(tasks))
    results: list[Any] = [None] * len(tasks)
    executor: ProcessPoolExecutor | None = None
    futures: dict[Future[Any], tuple[int, Task]] = {}
    submitted_at: dict[Future[Any], float] = {}
    started_at: dict[Future[Any], float] = {}
    executor_closed = True
    pending_abort: BaseException | None = None

    def create_executor() -> ProcessPoolExecutor:
        try:
            worker_initializer = partial(_initialize_worker, initializer, initargs)
            if executor_factory is not None:
                return executor_factory(
                    max_workers,
                    worker_initializer,
                    mp_context,
                )
            return ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=worker_initializer,
                mp_context=mp_context,
            )
        except BaseException as exc:
            if is_unavailable is not None and is_unavailable(exc):
                raise SupervisedProcessPoolUnavailable(
                    "could not create process pool"
                ) from exc
            raise

    def submit_until_full() -> None:
        assert executor is not None
        while len(futures) < max_inflight and pending_tasks:
            index, task = pending_tasks.popleft()
            if before_submit is not None:
                before_submit(task)
            try:
                future = executor.submit(worker, task, *worker_args)
            except BaseException as exc:
                if is_unavailable is not None and is_unavailable(exc):
                    raise SupervisedProcessPoolUnavailable(
                        "could not submit process-pool task"
                    ) from exc
                raise
            futures[future] = (index, task)
            submitted_at[future] = time.monotonic()

    def abort(
        cause: BaseException,
        active_futures: Mapping[Future[Any], tuple[int, Task]],
        affected_tasks: Sequence[Task],
    ) -> bool:
        if executor is None:
            return True
        if abort_executor is not None:
            return abort_executor(
                executor,
                {future: task for future, (_index, task) in active_futures.items()},
                cause,
                affected_tasks,
            )
        for future in active_futures:
            if not future.done():
                future.cancel()
        processes = getattr(executor, "_processes", None)
        if processes is not None:
            for process in tuple(processes.values()):
                if not process.is_alive():
                    continue
                pid = getattr(process, "pid", None)
                process_group_leader = False
                if os.name == "posix" and isinstance(pid, int):
                    try:
                        process_group_leader = os.getpgid(pid) == pid
                    except OSError:
                        pass
                if process_group_leader:
                    try:
                        assert isinstance(pid, int)
                        os.killpg(pid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        process.terminate()
                else:
                    process.terminate()
                process.join(timeout=1.0)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1.0)
        executor.shutdown(wait=False, cancel_futures=True)
        return True

    def deadlines() -> tuple[float | None, tuple[Future[Any], ...]]:
        if timeout_seconds is None or not futures:
            return (None if timeout_seconds is None else _POLL_SECONDS, ())
        now = time.monotonic()
        for future, (_index, task) in futures.items():
            if future not in started_at and has_started is not None:
                try:
                    if has_started(task):
                        started_at[future] = now
                except OSError:
                    pass
            elif future not in started_at:
                started_at[future] = submitted_at[future]
        return (
            pool_wait_timeout(futures, submitted_at, started_at, timeout_seconds),
            expired_futures(futures, submitted_at, started_at, timeout_seconds),
        )

    try:
        executor = create_executor()
        executor_closed = False
        submit_until_full()
        while futures or pending_tasks:
            if not futures:
                executor = create_executor()
                executor_closed = False
                submit_until_full()
                continue
            wait_timeout, expired = deadlines()
            done, _ = wait_fn(
                futures,
                timeout=wait_timeout,
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                index, task = futures.pop(future)
                submitted_at.pop(future, None)
                started_at.pop(future, None)
                try:
                    raw_result = future.result()
                except BaseException as exc:
                    error = (
                        handle_worker_error(task, exc)
                        if handle_worker_error is not None
                        else RuntimeError(
                            f"process-pool task failed for {task_label(task)}"
                        )
                    )
                    pending_abort = error
                    executor_closed = abort(
                        error,
                        futures,
                        (task, *(pending[1] for pending in futures.values())),
                    )
                    raise error from exc
                try:
                    result = (
                        handle_result(task, raw_result)
                        if handle_result is not None
                        else raw_result
                    )
                except BaseException as exc:
                    pending_abort = exc
                    executor_closed = abort(
                        exc,
                        futures,
                        (task, *(pending[1] for pending in futures.values())),
                    )
                    raise
                results[index] = result
                submit_until_full()

            wait_timeout, expired = deadlines()
            if expired:
                expired_tasks: list[tuple[int, Task]] = []
                for future in expired:
                    task_index, task = futures.pop(future)
                    start = started_at.pop(future, None)
                    submitted = submitted_at.pop(future, time.monotonic())
                    origin = start if start is not None else submitted
                    elapsed = max(0.0, time.monotonic() - origin)
                    expired_tasks.append((task_index, task))
                    if handle_timeout is None:
                        error = TimeoutError(
                            f"process-pool task timed out for {task_label(task)}"
                        )
                        pending_abort = error
                        executor_closed = abort(
                            error,
                            futures,
                            (
                                *(item[1] for item in expired_tasks),
                                *(item[1] for item in futures.values()),
                            ),
                        )
                        raise error
                    results[task_index] = handle_timeout(task, elapsed)
                requeue = sorted(
                    futures.values(),
                    key=(
                        (lambda item: requeue_key(item[1]))
                        if requeue_key is not None
                        else (lambda item: item[0])
                    ),
                )
                pending_tasks.extendleft(
                    (index, task) for index, task in reversed(requeue)
                )
                affected = (
                    *(task for _index, task in expired_tasks),
                    *(task for _index, task in requeue),
                )
                futures.clear()
                submitted_at.clear()
                started_at.clear()
                timeout_error = RuntimeError("process-pool worker timed out")
                executor_closed = abort(timeout_error, {}, affected)
                executor = create_executor()
                executor_closed = False
                submit_until_full()
    finally:
        if executor is not None and not executor_closed:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except BaseException as exc:
                if pending_abort is None:
                    raise
                if hasattr(pending_abort, "add_note"):
                    pending_abort.add_note(
                        "process-pool teardown also failed: "
                        f"{type(exc).__name__}: {exc}"
                    )

    return tuple(results)


__all__ = (
    "DEFAULT_PROCESS_TASK_TIMEOUT_SECONDS",
    "SupervisedProcessPoolUnavailable",
    "expired_futures",
    "pool_wait_timeout",
    "startup_timeout_seconds",
    "supervised_process_map",
)
