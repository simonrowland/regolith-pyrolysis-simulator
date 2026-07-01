"""Process-pool evaluation for recipe optimizer batches."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
import copy
import errno
from functools import lru_cache, partial
import inspect
import logging
import os
from pathlib import Path
import pickle
import signal
import tempfile
import time
from typing import Any

from simulator.backend_names import canonical_backend_name
from simulator.backends import requires_stage0_subprocess
from simulator.config import DEFAULT_DATA_DIR, load_config_bundle
from simulator.optimize.evaluate import (
    BackendUnavailableAbort,
    EngineBugAbort,
    EvaluationAbort,
    FailureCategory,
    RunReference,
    ScoredResult,
    evaluate,
)
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.results_store import ResultStore
from simulator.optimize.worker_runtime import (
    clear_worker_runtime,
    get_worker_runtime,
    warm_worker_runtime,
    warm_workers_enabled,
)

_WORKER_OUTPUT_ENV = "REGOLITH_OPTIMIZER_WORKER_OUTPUT_DIR"
EVAL_TIMEOUT_ENV = "REGOLITH_OPTIMIZER_EVAL_TIMEOUT_SECONDS"
# Grounded on repo notes: live AlphaMELTS high eval ~=7 min, prior 900 s
# cap could be tight for precompute; 45 min is >6x observed and configurable.
DEFAULT_EVAL_TIMEOUT_SECONDS = 45 * 60
_INFLIGHT_PER_WORKER = 2
_POOL_POLL_SECONDS = 0.1
_WORKER_TERMINATE_GRACE_SECONDS = 5.0
_LOGGER = logging.getLogger(__name__)
_POOL_UNAVAILABLE_ERRNOS = {errno.EACCES, errno.EPERM, errno.ENOSYS}


@dataclass(frozen=True)
class PoolEvaluationRequest:
    patch: RecipePatch | Mapping[str, Any]
    feedstock_id: str
    fidelity: str
    profile: Mapping[str, Any] | None = None
    candidate_id: str | None = None
    output_dir: str | Path | None = None


@dataclass(frozen=True)
class _PoolTask:
    index: int
    patch: RecipePatch
    feedstock_id: str
    fidelity: str
    profile: Mapping[str, Any]
    candidate_id: str | None
    output_dir: str
    stage0_subprocess_required: bool | None = None
    constraints: Any = None
    schema: Any = None


class _PoolUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class _WarmRuntimeSpec:
    backend_name: str
    feedstock_id: str
    stage0_subprocess_required: bool


def evaluate_batch(
    requests: Sequence[PoolEvaluationRequest | tuple[Any, ...]],
    *,
    profile: Mapping[str, Any] | None = None,
    max_workers: int | None = None,
    output_root: str | Path | None = None,
    results_store: ResultStore | None = None,
    evaluate_fn: Callable[..., ScoredResult] = evaluate,
    constraints: Any = None,
    schema: Any = None,
    created_at: str | None = None,
    per_eval_timeout_seconds: float | None = None,
) -> tuple[ScoredResult, ...]:
    """Evaluate requests in worker processes; parent owns result-store writes.

    Empty batches are a no-touch operation: no store initialization, no output
    root creation, and no worker pool.
    """
    if not requests:
        return ()
    _assert_picklable(evaluate_fn, "evaluate_fn")
    _assert_picklable(constraints, "constraints")
    _assert_picklable(schema, "schema")

    root = Path(output_root) if output_root is not None else Path(
        tempfile.mkdtemp(prefix="regolith-optimizer-pool-")
    )
    root.mkdir(parents=True, exist_ok=True)
    tasks = tuple(
        replace(
            _task_from_request(index, request, profile=profile, output_root=root),
            constraints=constraints,
            schema=schema,
        )
        for index, request in enumerate(requests)
    )
    timeout_seconds = resolve_eval_timeout_seconds(per_eval_timeout_seconds)
    for task in tasks:
        _assert_picklable(task, _task_label(task))
    if results_store is not None:
        results_store.initialize()

    try:
        completed = _evaluate_tasks_in_pool(
            tasks,
            evaluate_fn,
            max_workers=max_workers,
            per_eval_timeout_seconds=timeout_seconds,
        )
    except _PoolUnavailableError as exc:
        _LOGGER.warning(
            "process pool unavailable; falling back to serial optimizer "
            "evaluation: %s",
            exc.__cause__ or exc,
        )
        completed = _evaluate_tasks_serial(tasks, evaluate_fn)
    if results_store is not None:
        timestamp = created_at or datetime.now(UTC).isoformat()
        stored_results: list[ScoredResult] = []
        for task, result in zip(tasks, completed):
            stored_result = _ensure_pool_backend_provenance(result, task)
            if result.eval_spec is not None:
                results_store.store(
                    result.eval_spec,
                    stored_result,
                    created_at=timestamp,
                )
            stored_results.append(stored_result)
        completed = tuple(stored_results)
    return completed


evaluate_in_process_pool = evaluate_batch


def _evaluate_tasks_in_pool(
    tasks: Sequence[_PoolTask],
    evaluate_fn: Callable[..., ScoredResult],
    *,
    max_workers: int | None,
    per_eval_timeout_seconds: float | None,
) -> tuple[ScoredResult, ...]:
    results: list[ScoredResult | None] = [None] * len(tasks)
    worker_count = max_workers or (os.cpu_count() or 1)
    max_inflight = max(1, worker_count * _INFLIGHT_PER_WORKER)
    task_queue = deque(tasks)
    warm_runtime_spec = _warm_runtime_spec(tasks)
    initializer = partial(_initialize_worker, warm_runtime_spec)
    executor = _create_executor(max_workers, initializer)
    futures: dict[Future[Any], _PoolTask] = {}
    started_at: dict[Future[Any], float] = {}
    pending_abort: BaseException | None = None
    executor_closed = False
    try:
        _submit_until_full(
            executor,
            futures,
            started_at,
            task_queue,
            evaluate_fn,
            max_inflight,
        )
        while futures or task_queue:
            if not futures:
                executor = _create_executor(max_workers, initializer)
                executor_closed = False
                _submit_until_full(
                    executor,
                    futures,
                    started_at,
                    task_queue,
                    evaluate_fn,
                    max_inflight,
                )
                continue
            wait_timeout = _pool_wait_timeout(futures, started_at, per_eval_timeout_seconds)
            done, _ = wait(futures, timeout=wait_timeout, return_when=FIRST_COMPLETED)
            for future in done:
                task = futures.pop(future)
                started_at.pop(future, None)
                try:
                    outcome = future.result()
                except BaseException as exc:
                    abort = RuntimeError(
                        f"process-pool evaluation failed for {_task_label(task)}"
                    )
                    pending_abort = abort
                    executor_closed = _best_effort_abort_executor(
                        executor, futures, abort
                    )
                    raise abort from exc
                if outcome["kind"] == "abort":
                    abort_payload = dict(outcome["abort"])
                    abort_payload.setdefault("candidate_id", task.candidate_id)
                    abort = _rebuild_abort(abort_payload)
                    pending_abort = abort
                    executor_closed = _best_effort_abort_executor(
                        executor, futures, abort
                    )
                    raise abort
                results[int(outcome["index"])] = outcome["result"]
                _submit_until_full(
                    executor,
                    futures,
                    started_at,
                    task_queue,
                    evaluate_fn,
                    max_inflight,
                )
            expired = _expired_futures(futures, started_at, per_eval_timeout_seconds)
            if expired:
                expired_tasks = [futures.pop(future) for future in expired]
                for future, task in zip(expired, expired_tasks):
                    start = started_at.pop(future, time.monotonic())
                    results[task.index] = _timeout_result(
                        task,
                        timeout_seconds=per_eval_timeout_seconds or 0.0,
                        elapsed_seconds=max(0.0, time.monotonic() - start),
                    )
                requeue = sorted(
                    (task for future, task in futures.items() if future not in expired),
                    key=lambda item: item.index,
                )
                task_queue.extendleft(reversed(requeue))
                futures.clear()
                started_at.clear()
                executor_closed = _best_effort_abort_executor(
                    executor,
                    {},
                    RuntimeError("process-pool worker timed out"),
                )
                executor = _create_executor(max_workers, initializer)
                executor_closed = False
                _submit_until_full(
                    executor,
                    futures,
                    started_at,
                    task_queue,
                    evaluate_fn,
                    max_inflight,
                )
    finally:
        if not executor_closed:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except BaseException as exc:
                if pending_abort is None:
                    raise
                _attach_teardown_error(pending_abort, exc)

    completed = tuple(result for result in results if result is not None)
    if len(completed) != len(tasks):
        raise RuntimeError("process-pool evaluation ended without all results")
    return completed


def _evaluate_tasks_serial(
    tasks: Sequence[_PoolTask],
    evaluate_fn: Callable[..., ScoredResult],
) -> tuple[ScoredResult, ...]:
    env_names = _serial_fallback_env_names()
    env_snapshot = {name: os.environ.get(name) for name in env_names}
    warm_runtime_spec = _warm_runtime_spec(tasks)
    try:
        _initialize_worker(warm_runtime_spec)
        results: list[ScoredResult] = []
        for task in tasks:
            try:
                outcome = _evaluate_pool_task(task, evaluate_fn)
            except BaseException as exc:
                raise RuntimeError(
                    f"process-pool evaluation failed for {_task_label(task)}"
                ) from exc
            if outcome["kind"] == "abort":
                abort_payload = dict(outcome["abort"])
                abort_payload.setdefault("candidate_id", task.candidate_id)
                raise _rebuild_abort(abort_payload)
            results.append(outcome["result"])
        return tuple(results)
    finally:
        clear_worker_runtime()
        _restore_env(env_snapshot)


def _submit_until_full(
    executor: ProcessPoolExecutor,
    futures: dict[Future[Any], _PoolTask],
    started_at: dict[Future[Any], float],
    task_queue: deque[_PoolTask],
    evaluate_fn: Callable[..., ScoredResult],
    max_inflight: int,
) -> None:
    while len(futures) < max_inflight:
        try:
            task = task_queue.popleft()
        except IndexError:
            return
        try:
            future = executor.submit(_evaluate_pool_task, task, evaluate_fn)
        except BaseException as exc:
            if _is_pool_unavailable(exc):
                raise _PoolUnavailableError("could not submit process-pool task") from exc
            raise
        futures[future] = task
        started_at[future] = time.monotonic()


def _initialize_worker(warm_runtime: _WarmRuntimeSpec | str | None = None) -> None:
    from simulator.optimize.determinism import pin_worker_env
    _isolate_worker_process_group()
    pin_worker_env()
    if warm_runtime is None or not warm_workers_enabled():
        clear_worker_runtime()
        return
    if isinstance(warm_runtime, str):
        warm_worker_runtime(warm_runtime)
        return
    warm_worker_runtime(
        warm_runtime.backend_name,
        feedstock_id=warm_runtime.feedstock_id,
        stage0_subprocess_required=warm_runtime.stage0_subprocess_required,
    )


def _evaluate_pool_task(
    task: _PoolTask,
    evaluate_fn: Callable[..., ScoredResult],
) -> dict[str, Any]:
    output_dir = Path(task.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ[_WORKER_OUTPUT_ENV] = str(output_dir)
    try:
        result = _call_evaluate_fn(
            evaluate_fn,
            copy.deepcopy(task.patch),
            task.feedstock_id,
            task.fidelity,
            profile=copy.deepcopy(task.profile),
            candidate_id=task.candidate_id,
            constraints=task.constraints,
            schema=task.schema,
            output_dir=str(output_dir),
            worker_runtime=get_worker_runtime(
                feedstock_id=task.feedstock_id,
                stage0_subprocess_required=task.stage0_subprocess_required,
            ),
        )
    except EvaluationAbort as exc:
        return {"kind": "abort", "index": task.index, "abort": _abort_payload(exc)}
    return {"kind": "result", "index": task.index, "result": result}


def _call_evaluate_fn(
    evaluate_fn: Callable[..., ScoredResult],
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    **kwargs: Any,
) -> ScoredResult:
    signature = inspect.signature(evaluate_fn)
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    accepted = {
        key: value
        for key, value in kwargs.items()
        if accepts_kwargs or key in signature.parameters
    }
    return evaluate_fn(patch, feedstock_id, fidelity, **accepted)


def _ensure_pool_backend_provenance(
    result: ScoredResult,
    task: _PoolTask,
) -> ScoredResult:
    ref = getattr(result, "run_reference", None)
    if ref is None or _result_backend_status(result) is not None:
        return result
    if _task_backend_name(task) != "stub":
        return result

    return replace(
        result,
        run_reference=replace(
            ref,
            backend_status="diagnostic_stub",
            backend_authoritative=False,
        ),
    )


def _result_backend_status(result: ScoredResult) -> str | None:
    ref = getattr(result, "run_reference", None)
    if ref is None:
        return None
    for carrier in (ref, getattr(ref, "trace", None)):
        status = _extract_backend_status(carrier)
        if status is not None:
            return status
    return None


def _extract_backend_status(carrier: Any) -> str | None:
    if carrier is None:
        return None
    if isinstance(carrier, Mapping):
        raw = carrier.get("backend_status")
        if raw is not None:
            return str(raw)
        for key in ("per_hour", "hours"):
            status = _latest_backend_status(carrier.get(key))
            if status is not None:
                return status
        return None
    raw = getattr(carrier, "backend_status", None)
    if raw is not None:
        return str(raw)
    for attr in ("per_hour", "hours"):
        status = _latest_backend_status(getattr(carrier, attr, None))
        if status is not None:
            return status
    return None


def _latest_backend_status(value: Any) -> str | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    return _extract_backend_status(value[-1])


def _warm_runtime_spec(tasks: Sequence[_PoolTask]) -> _WarmRuntimeSpec | None:
    if not warm_workers_enabled():
        return None
    names = {_task_backend_name(task) for task in tasks}
    if len(names) != 1:
        return None
    feedstocks = {str(task.feedstock_id) for task in tasks}
    if len(feedstocks) != 1:
        return None
    name = next(iter(names))
    if name in {"auto", "cached-real"}:
        return None
    feedstock_id = next(iter(feedstocks))
    subprocess_required = any(
        bool(task.stage0_subprocess_required) for task in tasks
    )
    return _WarmRuntimeSpec(
        backend_name=name,
        feedstock_id=feedstock_id,
        stage0_subprocess_required=subprocess_required,
    )


def _task_backend_name(task: _PoolTask) -> str:
    merged: dict[str, Any] = {}
    profile = task.profile
    run_options = profile.get("run", {}) if isinstance(profile, Mapping) else {}
    if isinstance(run_options, Mapping):
        merged.update(run_options)
    fidelities = profile.get("fidelities", {}) if isinstance(profile, Mapping) else {}
    if isinstance(fidelities, Mapping):
        selected = fidelities.get(task.fidelity, {})
        if isinstance(selected, Mapping):
            merged.update(selected)
    # Fold the `internal-analytical` display alias onto the stable `stub` token
    # (read raw from the profile; mirrors EvalSpec.backend_name canonicalization).
    return canonical_backend_name(str(merged.get("backend_name", "stub") or "stub"))


def _task_stage0_subprocess_required(feedstock_id: str) -> bool:
    return requires_stage0_subprocess(feedstock_id, _default_feedstocks())


@lru_cache(maxsize=1)
def _default_feedstocks() -> Mapping[str, Any]:
    return load_config_bundle(DEFAULT_DATA_DIR).feedstocks


def _task_from_request(
    index: int,
    request: PoolEvaluationRequest | tuple[Any, ...],
    *,
    profile: Mapping[str, Any] | None,
    output_root: Path,
) -> _PoolTask:
    normalized = _normalize_request(request)
    active_profile = normalized.profile if normalized.profile is not None else profile
    if active_profile is None:
        raise ValueError("pool evaluation requires a profile")
    output_dir = (
        Path(normalized.output_dir)
        if normalized.output_dir is not None
        else output_root / f"eval-{index:06d}"
    )
    return _PoolTask(
        index=index,
        patch=_normalize_patch(normalized.patch),
        feedstock_id=normalized.feedstock_id,
        fidelity=normalized.fidelity,
        profile=_plain_value_for_process_pool(active_profile),
        candidate_id=normalized.candidate_id,
        output_dir=str(output_dir),
        stage0_subprocess_required=_task_stage0_subprocess_required(
            normalized.feedstock_id
        ),
    )


def _normalize_request(request: PoolEvaluationRequest | tuple[Any, ...]) -> PoolEvaluationRequest:
    if isinstance(request, PoolEvaluationRequest):
        return request
    if len(request) == 3:
        patch, feedstock_id, fidelity = request
        return PoolEvaluationRequest(patch, feedstock_id, fidelity)
    if len(request) == 4:
        patch, feedstock_id, fidelity, candidate_id = request
        return PoolEvaluationRequest(patch, feedstock_id, fidelity, candidate_id=candidate_id)
    raise ValueError("pool requests must be PoolEvaluationRequest or (patch, feedstock, fidelity)")


def _normalize_patch(patch: RecipePatch | Mapping[str, Any]) -> RecipePatch:
    if isinstance(patch, RecipePatch):
        return RecipePatch(dict(patch.values))
    return RecipePatch.from_nested(copy.deepcopy(dict(patch)))


def _plain_value_for_process_pool(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            copy.deepcopy(key): _plain_value_for_process_pool(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_plain_value_for_process_pool(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_plain_value_for_process_pool(child) for child in value)
    if isinstance(value, set):
        return {_plain_value_for_process_pool(child) for child in value}
    if isinstance(value, frozenset):
        return frozenset(_plain_value_for_process_pool(child) for child in value)
    return copy.deepcopy(value)


def _abort_payload(exc: EvaluationAbort) -> dict[str, Any]:
    return {
        "message": str(exc),
        "category": exc.category,
        "patch": exc.patch,
        "candidate_id": exc.candidate_id,
        "eval_spec": exc.eval_spec,
        "cache_key": exc.cache_key,
    }


def _rebuild_abort(payload: Mapping[str, Any]) -> EvaluationAbort:
    category = FailureCategory(payload["category"])
    kwargs = {
        "patch": payload["patch"],
        "candidate_id": payload.get("candidate_id"),
        "eval_spec": payload.get("eval_spec"),
        "cache_key_value": payload.get("cache_key"),
    }
    message = str(payload["message"])
    if category is FailureCategory.ENGINE_BUG:
        return EngineBugAbort(message, **kwargs)
    if category is FailureCategory.BACKEND_UNAVAILABLE:
        return BackendUnavailableAbort(message, **kwargs)
    return EvaluationAbort(message, category=category, **kwargs)


def _assert_picklable(value: Any, label: str) -> None:
    try:
        pickle.dumps(value)
    except Exception as exc:  # pragma: no cover - defensive message path
        field_path = _first_unpicklable_path(value)
        location = f" {field_path}" if field_path else ""
        raise TypeError(
            f"{label}{location} must be picklable for process-pool evaluation"
        ) from exc


def _first_unpicklable_path(value: Any, path: str = "") -> str:
    for child_path, child in _picklable_children(value, path):
        try:
            pickle.dumps(child)
        except Exception:
            return _first_unpicklable_path(child, child_path)
    return path


def _picklable_children(value: Any, path: str) -> list[tuple[str, Any]]:
    if is_dataclass(value) and not isinstance(value, type):
        return [
            (_join_attr(path, field.name), getattr(value, field.name))
            for field in fields(value)
        ]
    if isinstance(value, Mapping):
        return [
            (f"{path}[{key!r}]" if path else f"[{key!r}]", child)
            for key, child in value.items()
        ]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            (f"{path}[{index}]" if path else f"[{index}]", child)
            for index, child in enumerate(value)
        ]
    if hasattr(value, "__dict__"):
        return [
            (_join_attr(path, str(key)), child)
            for key, child in vars(value).items()
        ]
    return []


def _join_attr(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def _cancel_pending(futures: Mapping[Future[Any], _PoolTask]) -> None:
    for future in futures:
        if not future.done():
            future.cancel()


def _abort_executor(
    executor: ProcessPoolExecutor,
    futures: Mapping[Future[Any], _PoolTask],
) -> None:
    _cancel_pending(futures)
    # Fail-fast path must not wait for long-running workers; Python 3.12 lacks
    # public terminate_workers(), so terminate live child processes before the
    # nonblocking shutdown.
    processes = getattr(executor, "_processes", None)
    if processes is not None:
        for process in list(processes.values()):
            if process.is_alive():
                _terminate_pool_process(process)
    executor.shutdown(wait=False, cancel_futures=True)


def _best_effort_abort_executor(
    executor: ProcessPoolExecutor,
    futures: Mapping[Future[Any], _PoolTask],
    abort: BaseException,
) -> bool:
    try:
        _abort_executor(executor, futures)
    except BaseException as exc:
        _attach_teardown_error(abort, exc)
        return False
    return True


def _attach_teardown_error(abort: BaseException, teardown_error: BaseException) -> None:
    if hasattr(abort, "add_note"):
        abort.add_note(
            "process-pool teardown also failed: "
            f"{type(teardown_error).__name__}: {teardown_error}"
        )
    if abort.__cause__ is None and abort.__context__ is None:
        abort.__context__ = teardown_error


def resolve_eval_timeout_seconds(value: float | int | str | None = None) -> float | None:
    raw = value if value is not None else os.environ.get(EVAL_TIMEOUT_ENV)
    if raw is None:
        return float(DEFAULT_EVAL_TIMEOUT_SECONDS)
    seconds = float(raw)
    if seconds <= 0.0:
        raise ValueError("per-eval timeout seconds must be positive")
    return seconds


def _create_executor(
    max_workers: int | None,
    initializer: Any,
) -> ProcessPoolExecutor:
    try:
        return ProcessPoolExecutor(max_workers=max_workers, initializer=initializer)
    except BaseException as exc:
        if _is_pool_unavailable(exc):
            raise _PoolUnavailableError("could not create process pool") from exc
        raise


def _pool_wait_timeout(
    futures: Mapping[Future[Any], _PoolTask],
    started_at: Mapping[Future[Any], float],
    timeout_seconds: float | None,
) -> float | None:
    if timeout_seconds is None:
        return None
    if not futures:
        return _POOL_POLL_SECONDS
    now = time.monotonic()
    remaining = [
        max(0.0, timeout_seconds - (now - started_at.get(future, now)))
        for future in futures
    ]
    return min(_POOL_POLL_SECONDS, min(remaining, default=_POOL_POLL_SECONDS))


def _expired_futures(
    futures: Mapping[Future[Any], _PoolTask],
    started_at: Mapping[Future[Any], float],
    timeout_seconds: float | None,
) -> tuple[Future[Any], ...]:
    if timeout_seconds is None:
        return ()
    now = time.monotonic()
    return tuple(
        future
        for future in futures
        if now - started_at.get(future, now) >= timeout_seconds
    )


def _timeout_result(
    task: _PoolTask,
    *,
    timeout_seconds: float,
    elapsed_seconds: float,
) -> ScoredResult:
    threshold = ThresholdSpec(
        id="optimizer_eval_wall_timeout_seconds",
        value=float(timeout_seconds),
        units="s",
        source="code_default",
        source_ref=(
            "simulator.optimize.pool.DEFAULT_EVAL_TIMEOUT_SECONDS; "
            f"override with {EVAL_TIMEOUT_ENV}"
        ),
    )
    margin = GateMargin(
        gate="optimizer_eval_wall_timeout",
        feasible=False,
        margin=float(timeout_seconds) - float(elapsed_seconds),
        threshold=threshold,
        observed=float(elapsed_seconds),
        detail="optimizer eval exceeded wall-clock timeout",
        status_reason="optimizer_eval_timeout",
        status_payload={
            "timeout_seconds": float(timeout_seconds),
            "elapsed_seconds": float(elapsed_seconds),
        },
    )
    backend_name = _task_backend_name(task)
    message = (
        f"optimizer eval timed out after {elapsed_seconds:.3f}s "
        f"(limit {timeout_seconds:.3f}s)"
    )
    return ScoredResult(
        candidate_id=task.candidate_id,
        eval_spec=None,
        cache_key=None,
        feasible=False,
        failure_category=FailureCategory.TIMEOUT,
        feasibility_margins={"optimizer_eval_wall_timeout": margin},
        failing_gates=("optimizer_eval_wall_timeout",),
        run_reference=RunReference(
            status="timeout",
            error_message=message,
            reason="optimizer_eval_timeout",
            trace={
                "backend_name": backend_name,
                "backend_status": "unavailable",
                "backend_status_reason": "optimizer_eval_timeout",
                "elapsed_seconds": float(elapsed_seconds),
                "timeout_seconds": float(timeout_seconds),
            },
            backend_name=backend_name,
            backend_status="unavailable",
            backend_authoritative=False,
            backend_status_reason="optimizer_eval_timeout",
        ),
        notes=("optimizer_eval_timeout", message),
    )


def _isolate_worker_process_group() -> None:
    if os.name == "nt" or not hasattr(os, "setsid"):
        return
    try:
        os.setsid()
    except OSError:
        return


def _terminate_pool_process(process: Any) -> None:
    pid = getattr(process, "pid", None)
    if pid is not None and os.name != "nt":
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            pass
        else:
            process.join(_WORKER_TERMINATE_GRACE_SECONDS)
            if not process.is_alive():
                return
            try:
                os.killpg(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                return
            except OSError:
                pass
            else:
                process.join()
                return
    process.terminate()
    process.join(_WORKER_TERMINATE_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
        process.join()


def _task_label(task: _PoolTask) -> str:
    candidate = task.candidate_id if task.candidate_id is not None else "<none>"
    return f"candidate_id={candidate!r} index={task.index}"


def _is_pool_unavailable(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return isinstance(exc, OSError) and exc.errno in _POOL_UNAVAILABLE_ERRNOS


def _serial_fallback_env_names() -> tuple[str, ...]:
    from simulator.optimize.determinism import THREAD_ENV_VARS

    return (*THREAD_ENV_VARS, _WORKER_OUTPUT_ENV)


def _restore_env(snapshot: Mapping[str, str | None]) -> None:
    for name, value in snapshot.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
