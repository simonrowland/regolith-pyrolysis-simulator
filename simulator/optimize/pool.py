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
import multiprocessing as mp
import logging
import os
from pathlib import Path
import pickle
import signal
import subprocess
import tempfile
import time
from typing import Any

from simulator.backend_names import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    canonical_backend_name,
)
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
    _is_stale_profile_refusal,
    _stale_profile_result,
)
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.profiles import ProfileValidationError
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.results_store import ResultStore, ResultStoreWriteRejected
from simulator.optimize.worker_runtime import (
    clear_worker_runtime,
    get_worker_runtime,
    warm_worker_runtime,
    warm_workers_enabled,
)

_WORKER_OUTPUT_ENV = "REGOLITH_OPTIMIZER_WORKER_OUTPUT_DIR"
EVAL_TIMEOUT_ENV = "REGOLITH_OPTIMIZER_EVAL_TIMEOUT_SECONDS"
_CHILD_PID_LOG_ENV = "REGOLITH_OPTIMIZER_CHILD_PID_LOG"
# Grounded on repo notes: live AlphaMELTS high eval ~=7 min, prior 900 s
# cap could be tight for precompute; 45 min is >6x observed and configurable.
DEFAULT_EVAL_TIMEOUT_SECONDS = 45 * 60
_INFLIGHT_PER_WORKER = 1
_POOL_POLL_SECONDS = 0.1
_WORKER_TERMINATE_GRACE_SECONDS = 5.0
_DESCENDANT_SNAPSHOT_SECONDS = 0.25
_DESCENDANT_PGREP_TIMEOUT_SECONDS = 1.0
_DESCENDANT_PGREP_MAX_SECONDS = 0.05
_DESCENDANT_MAX_PROCESSES = 4096
_LOGGER = logging.getLogger(__name__)
_POOL_UNAVAILABLE_ERRNOS = {errno.EACCES, errno.EPERM, errno.ENOSYS}
_ORIGINAL_POPEN = subprocess.Popen
_CHILD_PID_TRACKING_INSTALLED = False


@dataclass(frozen=True)
class PoolEvaluationRequest:
    patch: RecipePatch | Mapping[str, Any]
    feedstock_id: str
    fidelity: str
    profile: Mapping[str, Any] | None = None
    candidate_id: str | None = None
    output_dir: str | Path | None = None
    evaluator_kwargs: Mapping[str, Any] | None = None


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
    evaluator_kwargs: Mapping[str, Any] | None = None
    child_pid_log: str | None = None


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
    if results_store is not None:
        results_store.initialize()

    use_serial_supervisor = not _is_picklable(evaluate_fn)
    if use_serial_supervisor:
        completed = _evaluate_tasks_serial(
            tasks,
            evaluate_fn,
            per_eval_timeout_seconds=timeout_seconds,
        )
    else:
        _assert_picklable(evaluate_fn, "evaluate_fn")
        _assert_picklable(constraints, "constraints")
        _assert_picklable(schema, "schema")
        for task in tasks:
            _assert_picklable(task, _task_label(task))
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
            completed = _evaluate_tasks_serial(
                tasks,
                evaluate_fn,
                per_eval_timeout_seconds=timeout_seconds,
            )
    if results_store is not None:
        timestamp = created_at or datetime.now(UTC).isoformat()
        stored_results: list[ScoredResult] = []
        for task, result in zip(tasks, completed):
            stored_result = _ensure_pool_backend_provenance(result, task)
            if result.eval_spec is not None:
                try:
                    results_store.store(
                        result.eval_spec,
                        stored_result,
                        created_at=timestamp,
                    )
                except ResultStoreWriteRejected as exc:
                    _LOGGER.warning(
                        "result_store_write_rejected candidate_id=%s cache_key=%s reasons=%s",
                        result.candidate_id,
                        result.cache_key,
                        ",".join(exc.reasons),
                    )
            stored_results.append(stored_result)
        completed = tuple(stored_results)
    return completed


evaluate_in_process_pool = evaluate_batch


def evaluate_request_supervised(
    request: PoolEvaluationRequest | tuple[Any, ...],
    *,
    profile: Mapping[str, Any] | None = None,
    output_root: str | Path | None = None,
    evaluate_fn: Callable[..., ScoredResult] = evaluate,
    constraints: Any = None,
    schema: Any = None,
    per_eval_timeout_seconds: float | None = None,
) -> ScoredResult:
    """Evaluate one request in a killable child process with a wall deadline."""

    root = Path(output_root) if output_root is not None else Path(
        tempfile.mkdtemp(prefix="regolith-optimizer-supervised-")
    )
    root.mkdir(parents=True, exist_ok=True)
    task = replace(
        _task_from_request(0, request, profile=profile, output_root=root),
        constraints=constraints,
        schema=schema,
    )
    timeout_seconds = resolve_eval_timeout_seconds(per_eval_timeout_seconds)
    outcome = _evaluate_task_supervised(
        task,
        evaluate_fn,
        warm_runtime_spec=_warm_runtime_spec((task,)),
        per_eval_timeout_seconds=timeout_seconds,
    )
    if outcome["kind"] == "abort":
        abort_payload = dict(outcome["abort"])
        abort_payload.setdefault("candidate_id", task.candidate_id)
        raise _rebuild_abort(abort_payload)
    return outcome["result"]


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
                        executor,
                        futures,
                        abort,
                        extra_pids=_tracked_child_pids_for_tasks(
                            (task, *tuple(futures.values()))
                        ),
                    )
                    raise abort from exc
                if outcome["kind"] == "abort":
                    abort_payload = dict(outcome["abort"])
                    abort_payload.setdefault("candidate_id", task.candidate_id)
                    abort = _rebuild_abort(abort_payload)
                    pending_abort = abort
                    executor_closed = _best_effort_abort_executor(
                        executor,
                        futures,
                        abort,
                        extra_pids=_tracked_child_pids_for_tasks(
                            (task, *tuple(futures.values()))
                        ),
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
                abort_tasks = (*expired_tasks, *tuple(requeue))
                task_queue.extendleft(reversed(requeue))
                futures.clear()
                started_at.clear()
                executor_closed = _best_effort_abort_executor(
                    executor,
                    {},
                    RuntimeError("process-pool worker timed out"),
                    extra_pids=_tracked_child_pids_for_tasks(abort_tasks),
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
    *,
    per_eval_timeout_seconds: float | None,
) -> tuple[ScoredResult, ...]:
    env_names = _serial_fallback_env_names()
    env_snapshot = {name: os.environ.get(name) for name in env_names}
    warm_runtime_spec = _warm_runtime_spec(tasks)
    try:
        results: list[ScoredResult] = []
        for task in tasks:
            try:
                outcome = _evaluate_task_supervised(
                    task,
                    evaluate_fn,
                    warm_runtime_spec=warm_runtime_spec,
                    per_eval_timeout_seconds=per_eval_timeout_seconds,
                )
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


def _evaluate_task_supervised(
    task: _PoolTask,
    evaluate_fn: Callable[..., ScoredResult],
    *,
    warm_runtime_spec: _WarmRuntimeSpec | None,
    per_eval_timeout_seconds: float | None,
) -> dict[str, Any]:
    output_dir = Path(task.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / ".supervised-result.pickle"
    child_pid_log = _child_pid_log_path(task)
    for path in (result_path, child_pid_log):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    ctx = _supervised_process_context(task, evaluate_fn, warm_runtime_spec)
    process = ctx.Process(
        target=_supervised_pool_task_main,
        args=(task, evaluate_fn, warm_runtime_spec, str(result_path), str(child_pid_log)),
    )
    start = time.monotonic()
    process.start()
    process.join(per_eval_timeout_seconds)
    elapsed = max(0.0, time.monotonic() - start)
    if process.is_alive():
        _terminate_pool_process(
            process,
            extra_pids=_read_pid_log(child_pid_log),
        )
        return {
            "kind": "result",
            "index": task.index,
            "result": _timeout_result(
                task,
                timeout_seconds=per_eval_timeout_seconds or 0.0,
                elapsed_seconds=elapsed,
            ),
        }
    if result_path.exists():
        with result_path.open("rb") as handle:
            outcome = pickle.load(handle)
        if outcome.get("kind") == "exception":
            raise RuntimeError(
                f"{outcome.get('exc_type', 'Exception')}: "
                f"{outcome.get('message', '<missing message>')}"
            )
        return outcome
    raise RuntimeError(
        f"ChildProcessExit: child exited without result (exitcode={process.exitcode})"
    )


def _supervised_process_context(
    task: _PoolTask,
    evaluate_fn: Callable[..., ScoredResult],
    warm_runtime_spec: _WarmRuntimeSpec | None,
) -> mp.context.BaseContext:
    try:
        pickle.dumps((task, evaluate_fn, warm_runtime_spec))
    except Exception:
        if os.name != "nt":
            return mp.get_context("fork")
    return mp.get_context("spawn")


def _supervised_pool_task_main(
    task: _PoolTask,
    evaluate_fn: Callable[..., ScoredResult],
    warm_runtime_spec: _WarmRuntimeSpec | None,
    result_path: str,
    child_pid_log: str,
) -> None:
    try:
        _initialize_worker(warm_runtime_spec)
        outcome = _evaluate_pool_task(
            replace(task, child_pid_log=child_pid_log),
            evaluate_fn,
        )
    except BaseException as exc:
        outcome = {
            "kind": "exception",
            "index": task.index,
            "exc_type": type(exc).__name__,
            "message": str(exc),
        }
    finally:
        clear_worker_runtime()
    path = Path(result_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(outcome, handle)


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
    child_pid_log = task.child_pid_log or str(_child_pid_log_path(task))
    previous_output = os.environ.get(_WORKER_OUTPUT_ENV)
    previous_pid_log = os.environ.get(_CHILD_PID_LOG_ENV)
    os.environ[_WORKER_OUTPUT_ENV] = str(output_dir)
    os.environ[_CHILD_PID_LOG_ENV] = child_pid_log
    _install_child_pid_tracking()
    try:
        evaluator_kwargs = dict(task.evaluator_kwargs or {})
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
            **evaluator_kwargs,
        )
    except EvaluationAbort as exc:
        return {"kind": "abort", "index": task.index, "abort": _abort_payload(exc)}
    except ProfileValidationError as exc:
        if not _is_stale_profile_refusal(exc):
            raise
        return {
            "kind": "result",
            "index": task.index,
            "result": _stale_profile_result(task.candidate_id, str(exc)),
        }
    finally:
        if previous_output is None:
            os.environ.pop(_WORKER_OUTPUT_ENV, None)
        else:
            os.environ[_WORKER_OUTPUT_ENV] = previous_output
        if previous_pid_log is None:
            os.environ.pop(_CHILD_PID_LOG_ENV, None)
        else:
            os.environ[_CHILD_PID_LOG_ENV] = previous_pid_log
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
    if _task_backend_name(task) != ANALYTICAL_BACKEND_SERIALIZATION_TOKEN:
        return result

    return replace(
        result,
        run_reference=replace(
            ref,
            backend_status="unavailable",
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
    # Fold legacy `stub` onto canonical `internal-analytical` when reading the
    # profile boundary; mirrors EvalSpec.backend_name canonicalization.
    return canonical_backend_name(
        str(
            merged.get("backend_name", ANALYTICAL_BACKEND_SERIALIZATION_TOKEN)
            or ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
        )
    )


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
        evaluator_kwargs=_plain_value_for_process_pool(normalized.evaluator_kwargs or {}),
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


def _is_picklable(value: Any) -> bool:
    try:
        pickle.dumps(value)
    except Exception:
        return False
    return True


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
    *,
    extra_pids: Sequence[int] = (),
) -> None:
    _cancel_pending(futures)
    # Fail-fast path must not wait for long-running workers; Python 3.12 lacks
    # public terminate_workers(), so terminate live child processes before the
    # nonblocking shutdown.
    tracked_pids = tuple(extra_pids)
    processes = getattr(executor, "_processes", None)
    if processes is not None:
        for process in list(processes.values()):
            if process.is_alive():
                _terminate_pool_process(process, extra_pids=tracked_pids)
    executor.shutdown(wait=False, cancel_futures=True)


def _best_effort_abort_executor(
    executor: ProcessPoolExecutor,
    futures: Mapping[Future[Any], _PoolTask],
    abort: BaseException,
    *,
    extra_pids: Sequence[int] = (),
) -> bool:
    try:
        _abort_executor(executor, futures, extra_pids=extra_pids)
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


def _terminate_pool_process(process: Any, *, extra_pids: Sequence[int] = ()) -> None:
    pid = getattr(process, "pid", None)
    pid_int = int(pid) if pid is not None else None
    if pid_int is not None and os.name != "nt":
        _stop_process_group_or_process(pid_int)
        descendant_pids = _snapshot_descendant_pids(
            pid_int,
            extra_pids=extra_pids,
        )
        _signal_pids(descendant_pids, signal.SIGKILL)
        try:
            os.killpg(pid_int, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            pass
        else:
            process.join(_WORKER_TERMINATE_GRACE_SECONDS)
            _signal_pids(descendant_pids, signal.SIGKILL)
            if not process.is_alive():
                return
        _signal_pids(descendant_pids, signal.SIGKILL)
        try:
            os.kill(pid_int, signal.SIGKILL)
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
    _signal_pids(tuple(extra_pids), signal.SIGKILL)


def _stop_process_group_or_process(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGSTOP)
        return
    except ProcessLookupError:
        pass
    except OSError:
        pass
    try:
        os.kill(pid, signal.SIGSTOP)
    except OSError:
        return


def _snapshot_descendant_pids(
    root_pid: int,
    *,
    extra_pids: Sequence[int] = (),
) -> tuple[int, ...]:
    deadline = time.monotonic() + _DESCENDANT_SNAPSHOT_SECONDS
    captured = _merge_pids(tuple(extra_pids), exclude={root_pid, os.getpid()})
    while True:
        if time.monotonic() >= deadline:
            return captured
        captured = _merge_pids(
            (
                *captured,
                *_process_tree_pids(root_pid, deadline=deadline),
                *tuple(extra_pids),
            ),
            exclude={root_pid, os.getpid()},
        )
        if time.monotonic() >= deadline:
            return captured
        remaining = _remaining_deadline_seconds(deadline)
        if remaining <= 0.0:
            return captured
        time.sleep(min(0.02, remaining))


def _merge_pids(
    pids: Sequence[int],
    *,
    exclude: set[int] | None = None,
) -> tuple[int, ...]:
    excluded = exclude or set()
    merged: list[int] = []
    seen: set[int] = set()
    for raw_pid in pids:
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid in excluded or pid in seen:
            continue
        seen.add(pid)
        merged.append(pid)
    return tuple(merged)


def _remaining_deadline_seconds(deadline: float | None) -> float:
    if deadline is None:
        return float("inf")
    return max(0.0, deadline - time.monotonic())


def _process_tree_pids(root_pid: int, *, deadline: float | None = None) -> tuple[int, ...]:
    try:
        import psutil  # type: ignore[import-not-found]

        found: list[int] = []
        seen: set[int] = set()
        pending = [root_pid]
        while pending and len(found) < _DESCENDANT_MAX_PROCESSES:
            if _remaining_deadline_seconds(deadline) <= 0.0:
                break
            parent = pending.pop()
            # psutil exposes no timeout for this synchronous OS query, so an
            # individual call cannot be preempted; bound the traversal between
            # calls by both the shared deadline and a maximum process count.
            children = psutil.Process(parent).children(recursive=False)
            for child_process in children:
                if _remaining_deadline_seconds(deadline) <= 0.0:
                    break
                child = int(child_process.pid)
                if child not in seen:
                    seen.add(child)
                    found.append(child)
                    pending.append(child)
                    if len(found) >= _DESCENDANT_MAX_PROCESSES:
                        break
        return tuple(found)
    except Exception:
        return _process_tree_pids_via_pgrep(root_pid, deadline=deadline)


def _process_tree_pids_via_pgrep(
    root_pid: int,
    *,
    deadline: float | None = None,
) -> tuple[int, ...]:
    found: list[int] = []
    seen: set[int] = set()
    pending = [root_pid]
    while pending and len(found) < _DESCENDANT_MAX_PROCESSES:
        remaining = _remaining_deadline_seconds(deadline)
        if remaining <= 0.0:
            break
        timeout = _DESCENDANT_PGREP_TIMEOUT_SECONDS
        if deadline is not None:
            timeout = min(_DESCENDANT_PGREP_MAX_SECONDS, remaining)
        parent = pending.pop()
        try:
            completed = subprocess.run(
                ["pgrep", "-P", str(parent)],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            if deadline is not None:
                pending.insert(0, parent)
            continue
        except Exception:
            continue
        for line in completed.stdout.splitlines():
            if (
                len(found) >= _DESCENDANT_MAX_PROCESSES
                or _remaining_deadline_seconds(deadline) <= 0.0
            ):
                break
            try:
                child = int(line.strip())
            except ValueError:
                continue
            if child not in seen:
                seen.add(child)
                found.append(child)
                pending.append(child)
    return tuple(found)


def _signal_pids(pids: Sequence[int], sig: signal.Signals) -> None:
    for pid in pids:
        try:
            os.kill(int(pid), sig)
        except ProcessLookupError:
            continue
        except OSError:
            continue


def _child_pid_log_path(task: _PoolTask) -> Path:
    return Path(task.output_dir) / ".child-pids"


def _tracked_child_pids_for_tasks(tasks: Sequence[_PoolTask]) -> tuple[int, ...]:
    pids: list[int] = []
    for task in tasks:
        pids.extend(_read_pid_log(_child_pid_log_path(task)))
        if task.child_pid_log is not None:
            pids.extend(_read_pid_log(Path(task.child_pid_log)))
    return _merge_pids(tuple(pids), exclude={os.getpid()})


def _read_pid_log(path: Path) -> tuple[int, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ()
    except OSError:
        return ()
    pids: list[int] = []
    for line in lines:
        parts = line.strip().split("\t")
        if not parts or not parts[0]:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        # Grind-infra sweep Finding 5 (shared-studio wrong-kill guard): a tracked
        # child PID recorded here can exit and have its number recycled by the OS
        # for an UNRELATED process before the timeout reaper SIGKILLs it. When a
        # start-time was recorded at spawn, verify the live process's start time
        # still matches; drop the entry if the process is gone (already dead) or
        # its start time differs (recycled → not ours). Legacy bare-PID lines
        # (no recorded start time) fall back to the prior unverified behavior.
        if len(parts) >= 2 and parts[1]:
            try:
                recorded_create_time = float(parts[1])
            except ValueError:
                recorded_create_time = None
            if recorded_create_time is not None:
                current_create_time = _process_create_time(pid)
                if current_create_time is None or (
                    abs(current_create_time - recorded_create_time)
                    > _PID_RECYCLE_CREATE_TIME_TOLERANCE_S
                ):
                    continue
        pids.append(pid)
    return tuple(pids)


# Tolerance (seconds) when comparing a tracked child's recorded start time
# against its live start time to detect PID recycling. psutil.create_time() is
# deterministic per-process, so the same process compares equal; a recycled PID
# (original exited, number reused) differs by far more than this. Small non-zero
# value only absorbs float round-trip through the on-disk log.
_PID_RECYCLE_CREATE_TIME_TOLERANCE_S = 1.0


def _process_create_time(pid: int) -> float | None:
    """Best-effort process start time (epoch seconds) for PID-recycle guards.

    Returns ``None`` when the process is gone or start time is unavailable
    (e.g. psutil absent) — callers treat ``None`` as "cannot confirm identity".
    Used by the timeout reaper to avoid SIGKILLing an unrelated process that
    inherited a recycled PID on a shared machine (grind-infra sweep Finding 5).
    """
    try:
        import psutil  # type: ignore[import-not-found]

        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return None


def _append_child_pid(path: str, pid: int) -> None:
    try:
        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Record the child's start time alongside its PID so the reaper can
        # verify identity before SIGKILL (guards against PID recycling on shared
        # machines — grind-infra sweep Finding 5). Falls back to a bare PID line
        # when the start time is unavailable.
        create_time = _process_create_time(int(pid))
        if create_time is not None:
            record = f"{int(pid)}\t{create_time:.6f}\n"
        else:
            record = f"{int(pid)}\n"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(record)
    except OSError:
        return


def _install_child_pid_tracking() -> None:
    global _CHILD_PID_TRACKING_INSTALLED
    if _CHILD_PID_TRACKING_INSTALLED:
        return

    def _tracked_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        process = _ORIGINAL_POPEN(*args, **kwargs)
        log_path = os.environ.get(_CHILD_PID_LOG_ENV)
        if log_path:
            _append_child_pid(log_path, int(process.pid))
        return process

    subprocess.Popen = _tracked_popen  # type: ignore[assignment]
    _CHILD_PID_TRACKING_INSTALLED = True


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
