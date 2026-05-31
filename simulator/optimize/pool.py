"""Process-pool evaluation for recipe optimizer batches."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
import copy
import inspect
import os
from pathlib import Path
import pickle
import tempfile
from typing import Any

from simulator.optimize.evaluate import (
    BackendUnavailableAbort,
    EngineBugAbort,
    EvaluationAbort,
    FailureCategory,
    ScoredResult,
    evaluate,
)
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.results_store import ResultStore

_WORKER_OUTPUT_ENV = "REGOLITH_OPTIMIZER_WORKER_OUTPUT_DIR"
_INFLIGHT_PER_WORKER = 2


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
    constraints: Any = None
    schema: Any = None


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
    for task in tasks:
        _assert_picklable(task, _task_label(task))
    if results_store is not None:
        results_store.initialize()

    results: list[ScoredResult | None] = [None] * len(tasks)
    worker_count = max_workers or (os.cpu_count() or 1)
    max_inflight = max(1, worker_count * _INFLIGHT_PER_WORKER)
    task_iter = iter(tasks)
    executor = ProcessPoolExecutor(max_workers=max_workers, initializer=_initialize_worker)
    futures: dict[Future[Any], _PoolTask] = {}
    pending_abort: BaseException | None = None
    executor_closed = False
    try:
        _submit_until_full(executor, futures, task_iter, evaluate_fn, max_inflight)
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                task = futures.pop(future)
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
                _submit_until_full(executor, futures, task_iter, evaluate_fn, max_inflight)
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
    if results_store is not None:
        timestamp = created_at or datetime.now(UTC).isoformat()
        for result in completed:
            if result.eval_spec is not None:
                results_store.store(result.eval_spec, result, created_at=timestamp)
    return completed


evaluate_in_process_pool = evaluate_batch


def _submit_until_full(
    executor: ProcessPoolExecutor,
    futures: dict[Future[Any], _PoolTask],
    task_iter: Any,
    evaluate_fn: Callable[..., ScoredResult],
    max_inflight: int,
) -> None:
    while len(futures) < max_inflight:
        try:
            task = next(task_iter)
        except StopIteration:
            return
        futures[executor.submit(_evaluate_pool_task, task, evaluate_fn)] = task


def _initialize_worker() -> None:
    from simulator.optimize.determinism import pin_worker_env
    pin_worker_env()


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
        profile=copy.deepcopy(dict(active_profile)),
        candidate_id=normalized.candidate_id,
        output_dir=str(output_dir),
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
                process.terminate()
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


def _task_label(task: _PoolTask) -> str:
    candidate = task.candidate_id if task.candidate_id is not None else "<none>"
    return f"candidate_id={candidate!r} index={task.index}"
