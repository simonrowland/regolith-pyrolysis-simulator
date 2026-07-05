"""Fast-vs-high fidelity correlation harness for Phase-O recipe optimization."""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import time
from dataclasses import dataclass, replace
from functools import lru_cache
import inspect
from pathlib import Path
from queue import Empty
from typing import Any, Callable, Mapping, Sequence

from scipy.stats import spearmanr

from simulator.optimize.doe import (
    DoeSpec,
    FIDELITY_CORRELATION_METRICS,
    FidelityCorrelationProtocol,
    FidelityCorrelationResult,
    sample_recipe_patches,
)
from simulator.backend_names import canonical_backend_name
from simulator.backends import requires_stage0_subprocess
from simulator.config import DEFAULT_DATA_DIR, load_config_bundle
from simulator.optimize.evaluate import EvaluationAbort, ScoredResult
from simulator.optimize.objective import ObjectiveValue
from simulator.fidelity_vocabulary import (
    CANONICAL_EVIDENCE_CLASSES,
    FidelityVocabularyTranslationError,
    backend_name_denies_authority,
    canonicalize_fidelity_emission,
    translate_legacy_token,
)

EvaluateFn = Callable[..., ScoredResult]
EvalOutcome = tuple[ScoredResult | None, Mapping[str, Any] | None]
Pair = tuple[int, ScoredResult, ScoredResult]
STUB_DIAGNOSTIC_REASON = "stub-vs-stub diagnostic, not authoritative"


@dataclass(frozen=True)
class _FidelityTask:
    index: int
    tier: str
    fn: EvaluateFn
    patch: Any
    feedstock_id: str
    fidelity: str
    profile: Mapping[str, Any]
    candidate_id: str
    kwargs: Mapping[str, Any]


@dataclass(frozen=True)
class _FidelityWarmRuntimeSpec:
    backend_name: str
    feedstock_id: str
    stage0_subprocess_required: bool


DEFAULT_THRESHOLD_PROFILE: Mapping[str, Mapping[str, Any]] = {
    "spearman_min": {
        "value": 0.80,
        "source_type": "literature",
        "source": (
            "Akoglu 2018 correlation-coefficient guide, DOI 10.1016/j.tjem.2018.08.001: "
            "0.80-1.00 is a very "
            "strong monotonic association; Phase-O requires very strong rank "
            "preservation before fast-screen gating."
        ),
    },
    "top_k_recall_min": {
        "value": 0.80,
        "source_type": "engineering_envelope",
        "source": "Phase-O gate: at least 80% of high-fidelity top-K survives stub-screen.",
    },
    "feasible_agreement_min": {
        "value": 0.95,
        "source_type": "engineering_envelope",
        "source": "Phase-O gate: feasibility misclassification above 5% is not acceptable.",
    },
    "min_compared_fraction": {
        "value": 1.00,
        "source_type": "engineering_envelope",
        "source": "Phase-O default withholds trust verdict for partial DOE data.",
    },
}


def run_fidelity_correlation(
    doe_spec: DoeSpec,
    evaluate_fn_fast: EvaluateFn,
    evaluate_fn_high: EvaluateFn,
    *,
    top_k: Sequence[int] = (5, 10, 20),
    per_eval_timeout_s: float,
    feedstock_id: str,
    profile: Mapping[str, Any] | None = None,
    objective_names: Sequence[str] | None = None,
    fast_fidelity_name: str = "fast",
    high_fidelity_name: str = "high",
    thresholds: Mapping[str, Mapping[str, Any]] | None = None,
    artifact_dir: str | Path | None = None,
    max_samples: int | None = None,
    evaluator_kwargs: Mapping[str, Any] | None = None,
) -> FidelityCorrelationResult:
    """Evaluate DOE patches at both fidelities and score rank-preservation trust."""

    if per_eval_timeout_s <= 0:
        raise ValueError("per_eval_timeout_s must be positive")
    top_k_values = _top_k(top_k)
    threshold_profile = _thresholds(thresholds or DEFAULT_THRESHOLD_PROFILE)
    if max_samples is None:
        n_total = doe_spec.n_samples
    else:
        if isinstance(max_samples, bool) or not isinstance(max_samples, int):
            raise ValueError("max_samples must be a positive int when provided")
        n_total = min(doe_spec.n_samples, max_samples)
    if n_total <= 0:
        raise ValueError("max_samples must be positive when provided")

    patches = sample_recipe_patches(
        doe_spec.schema,
        n_samples=n_total,
        seed=doe_spec.seed,
        sampler_name=doe_spec.sampler_name,
        anchor=doe_spec.anchor,
        delta_fraction=doe_spec.delta_fraction,
    )
    pairs: list[Pair] = []
    drops: list[Mapping[str, Any]] = []
    kwargs = dict(evaluator_kwargs or {})
    prof = dict(profile or {})

    fast_tasks: list[_FidelityTask] = []
    high_tasks: list[_FidelityTask] = []
    for index, patch in enumerate(patches):
        fast_id = f"fidelity-doe-{index:06d}-fast"
        high_id = f"fidelity-doe-{index:06d}-high"
        fast_tasks.append(
            _FidelityTask(
                index=index,
                tier="fast",
                fn=evaluate_fn_fast,
                patch=patch,
                feedstock_id=feedstock_id,
                fidelity=fast_fidelity_name,
                profile=prof,
                candidate_id=fast_id,
                kwargs=kwargs,
            )
        )
        high_tasks.append(
            _FidelityTask(
                index=index,
                tier="high",
                fn=evaluate_fn_high,
                patch=patch,
                feedstock_id=feedstock_id,
                fidelity=high_fidelity_name,
                profile=prof,
                candidate_id=high_id,
                kwargs=kwargs,
            )
        )

    outcomes = {
        **_run_eval_batch(
            fast_tasks,
            per_eval_timeout_s,
            max_workers=_fidelity_worker_count(fast_tasks, n_total),
        ),
        **_run_eval_batch(
            high_tasks,
            per_eval_timeout_s,
            max_workers=_fidelity_worker_count(high_tasks, n_total),
        ),
    }
    for index in range(n_total):
        fast, fast_drop = outcomes.get(
            ("fast", index),
            (
                None,
                _drop(
                    index,
                    "fast",
                    f"fidelity-doe-{index:06d}-fast",
                    "engine_bug",
                    "fidelity pool ended without result",
                ),
            ),
        )
        high, high_drop = outcomes.get(
            ("high", index),
            (
                None,
                _drop(
                    index,
                    "high",
                    f"fidelity-doe-{index:06d}-high",
                    "engine_bug",
                    "fidelity pool ended without result",
                ),
            ),
        )
        drops.extend(drop for drop in (fast_drop, high_drop) if drop is not None)
        if fast is not None and high is not None:
            pairs.append((index, fast, high))

    objectives = _objective_names(objective_names, pairs)
    protocol = FidelityCorrelationProtocol(
        doe=DoeSpec(
            schema=doe_spec.schema,
            n_samples=n_total,
            seed=doe_spec.seed,
            sampler_name=doe_spec.sampler_name,
            anchor=doe_spec.anchor,
            delta_fraction=doe_spec.delta_fraction,
        ),
        fast_fidelity_name=fast_fidelity_name,
        high_fidelity_name=high_fidelity_name,
        objective_names=objectives,
        top_k_values=top_k_values,
        metrics=FIDELITY_CORRELATION_METRICS,
    )
    spearman = {name: _spearman(pairs, name) for name in objectives}
    agreement = _agreement(pairs)
    primary = _primary(pairs, objectives)
    recalls = {k: _recall(pairs, primary, k) for k in top_k_values}
    backend_arms = {
        "fast": _arm_backend_authority(
            "fast",
            fast_fidelity_name,
            fast_tasks,
            [fast for _, fast, _ in pairs],
        ),
        "high": _arm_backend_authority(
            "high",
            high_fidelity_name,
            high_tasks,
            [high for _, _, high in pairs],
        ),
    }
    missing_objective_notes = _missing_declared_objective_notes(pairs, objectives)
    verdict, confidence, notes = _verdict(
        spearman, agreement, recalls, len(pairs), n_total, len(drops), threshold_profile, primary
    )
    if missing_objective_notes:
        verdict = False
        confidence = "inconclusive"
        notes = (*notes, *missing_objective_notes)
    authority_reason = _high_arm_authority_reason(backend_arms)
    if verdict and authority_reason:
        verdict = False
        confidence = "inconclusive"
        notes = (*notes, authority_reason)
    reason = _payload_reason(verdict, notes, authority_reason)
    artifacts = _artifacts(
        artifact_dir,
        protocol,
        spearman,
        agreement,
        recalls,
        doe_spec.n_samples,
        n_total,
        len(pairs),
        drops,
        threshold_profile,
        verdict,
        confidence,
        notes,
        primary,
        backend_arms,
        reason,
    )
    return FidelityCorrelationResult(
        protocol=protocol,
        spearman_by_objective=spearman,
        feasible_infeasible_agreement=agreement,
        top_k_recall=recalls,
        n_samples_compared=len(pairs),
        notes=notes,
        fast_screen_trustworthy=verdict,
        n_samples_total=n_total,
        n_samples_dropped=len(drops),
        confidence=confidence,
        thresholds=threshold_profile,
        dropped_evaluations=tuple(drops),
        artifact_paths=artifacts,
    )


def _run_eval_batch(
    tasks: Sequence[_FidelityTask],
    timeout_s: float,
    *,
    max_workers: int,
) -> Mapping[tuple[str, int], EvalOutcome]:
    worker_count = min(max_workers, len(tasks), os.cpu_count() or 1)
    warm_runtime_spec = _warm_runtime_spec(tasks)
    ctx = mp.get_context(_start_method())
    task_queue: mp.Queue[Any] = ctx.Queue()
    result_queue: mp.Queue[Any] = ctx.Queue()
    workers = [
        ctx.Process(
            target=_fidelity_worker_loop,
            args=(task_queue, result_queue, warm_runtime_spec),
        )
        for _ in range(worker_count)
    ]
    outcomes: dict[tuple[str, int], EvalOutcome] = {}
    pending = {(task.tier, task.index): task for task in tasks}
    started: dict[tuple[str, int], float] = {}
    try:
        for worker in workers:
            worker.start()
        for task in tasks:
            task_queue.put(task)
        for _ in workers:
            task_queue.put(None)
        while pending:
            try:
                message = result_queue.get(timeout=0.05)
            except Empty:
                message = None
            if message is not None:
                key = (message["tier"], int(message["index"]))
                if key in pending:
                    if message["kind"] == "started":
                        started[key] = time.monotonic()
                    elif message["kind"] == "done":
                        outcomes[key] = _payload_outcome(message["payload"])
                        pending.pop(key, None)
                        started.pop(key, None)
            now = time.monotonic()
            expired = [
                key for key, start in started.items() if key in pending and now - start >= timeout_s
            ]
            if expired:
                for key in expired:
                    task = pending.pop(key)
                    started.pop(key, None)
                    outcomes[key] = (
                        None,
                        _drop(task.index, task.tier, task.candidate_id, "timeout", "per-eval timeout"),
                    )
                _terminate_workers(workers)
                for key, task in list(pending.items()):
                    outcomes[key] = (
                        None,
                        _drop(
                            task.index,
                            task.tier,
                            task.candidate_id,
                            "timeout",
                            "fidelity pool aborted after per-eval timeout",
                        ),
                    )
                pending.clear()
            elif not any(worker.is_alive() for worker in workers):
                while True:
                    try:
                        message = result_queue.get_nowait()
                    except Empty:
                        break
                    key = (message["tier"], int(message["index"]))
                    if key in pending and message["kind"] == "done":
                        outcomes[key] = _payload_outcome(message["payload"])
                        pending.pop(key, None)
                        started.pop(key, None)
                for key, task in list(pending.items()):
                    outcomes[key] = (
                        None,
                        _drop(
                            task.index,
                            task.tier,
                            task.candidate_id,
                            "engine_bug",
                            "fidelity worker pool exited without result",
                        ),
                    )
                pending.clear()
    finally:
        _join_or_stop_workers(workers)
        task_queue.close()
        result_queue.close()
    return outcomes


def _fidelity_worker_loop(
    task_queue: Any,
    result_queue: Any,
    warm_runtime_spec: _FidelityWarmRuntimeSpec | str | None,
) -> None:
    _initialize_fidelity_worker(warm_runtime_spec)
    while True:
        task = task_queue.get()
        if task is None:
            return
        result_queue.put({"kind": "started", "tier": task.tier, "index": task.index})
        result_queue.put(
            {
                "kind": "done",
                "tier": task.tier,
                "index": task.index,
                "payload": _evaluate_fidelity_task(task),
            }
        )


def _evaluate_fidelity_task(task: _FidelityTask) -> Mapping[str, Any]:
    try:
        result = _call_evaluate_fn(task)
    except EvaluationAbort as exc:
        return {
            "kind": "drop",
            "drop": _drop(
                task.index,
                task.tier,
                task.candidate_id,
                getattr(exc.category, "value", str(exc.category)),
                str(exc),
            ),
        }
    except BaseException as exc:
        return {
            "kind": "drop",
            "drop": _drop(
                task.index,
                task.tier,
                task.candidate_id,
                "error",
                f"{type(exc).__name__}: {exc}",
            ),
        }
    return {"kind": "result", "result": _queue_safe_result(result)}


def _queue_safe_result(result: ScoredResult) -> ScoredResult:
    if isinstance(result, ScoredResult):
        return replace(result, run_reference=_queue_safe_run_reference(result))
    return result


def _queue_safe_run_reference(result: ScoredResult) -> Any | None:
    ref = result.run_reference
    if ref is None:
        return None
    backend_status = _result_backend_status(result)
    safe_trace = {"backend_status": backend_status} if backend_status is not None else None
    try:
        return replace(ref, trace=safe_trace, product_summary={})
    except TypeError:
        return None


def _call_evaluate_fn(task: _FidelityTask) -> ScoredResult:
    from simulator.optimize.worker_runtime import get_worker_runtime

    call_kwargs: dict[str, Any] = {
        "profile": dict(task.profile),
        "candidate_id": task.candidate_id,
        "worker_runtime": get_worker_runtime(
            feedstock_id=task.feedstock_id,
            stage0_subprocess_required=_task_stage0_subprocess_required(task),
        ),
    }
    call_kwargs.update(dict(task.kwargs))
    signature = inspect.signature(task.fn)
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if not accepts_kwargs:
        call_kwargs = {
            key: value for key, value in call_kwargs.items() if key in signature.parameters
        }
    return task.fn(
        task.patch,
        task.feedstock_id,
        task.fidelity,
        **call_kwargs,
    )


def _payload_outcome(payload: Mapping[str, Any]) -> EvalOutcome:
    if payload.get("kind") == "result":
        return payload["result"], None
    return None, payload["drop"]


def _initialize_fidelity_worker(
    warm_runtime: _FidelityWarmRuntimeSpec | str | None,
) -> None:
    from simulator.optimize.determinism import pin_worker_env
    from simulator.optimize.worker_runtime import clear_worker_runtime, warm_worker_runtime

    pin_worker_env()
    if warm_runtime is None:
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


def _warm_runtime_spec(tasks: Sequence[_FidelityTask]) -> _FidelityWarmRuntimeSpec | None:
    from simulator.optimize.worker_runtime import warm_workers_enabled

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
    subprocess_required = any(_task_stage0_subprocess_required(task) for task in tasks)
    return _FidelityWarmRuntimeSpec(
        backend_name=name,
        feedstock_id=feedstock_id,
        stage0_subprocess_required=subprocess_required,
    )


def _task_stage0_subprocess_required(task: _FidelityTask) -> bool:
    return requires_stage0_subprocess(task.feedstock_id, _default_feedstocks())


@lru_cache(maxsize=1)
def _default_feedstocks() -> Mapping[str, Any]:
    return load_config_bundle(DEFAULT_DATA_DIR).feedstocks


def _fidelity_worker_count(tasks: Sequence[_FidelityTask], n_total: int) -> int:
    if not tasks:
        return 1
    backend_name = _task_backend_name(tasks[0])
    if backend_name not in {"stub", "auto", "cached-real"}:
        return 1
    return max(1, min(n_total, 2))


def _task_backend_name(task: _FidelityTask) -> str:
    # Read raw from the profile (not the EvalSpec), so fold the
    # `internal-analytical` display alias onto the stable `stub` token here too,
    # to stay consistent with the canonicalized EvalSpec.backend_name (otherwise
    # a new `internal-analytical` profile would spuriously read as a `mixed:`
    # backend against the canonicalized spec name).
    return canonical_backend_name(
        str(_task_run_options(task).get("backend_name", "stub") or "stub")
    )


def _task_run_options(task: _FidelityTask) -> dict[str, Any]:
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
    return merged


def _arm_backend_authority(
    tier: str,
    fidelity_name: str,
    tasks: Sequence[_FidelityTask],
    results: Sequence[ScoredResult],
) -> Mapping[str, Any]:
    backend_names = {name for name in (_task_backend_name(task) for task in tasks) if name}
    for result in results:
        spec = getattr(result, "eval_spec", None)
        raw_name = getattr(spec, "backend_name", None)
        if raw_name:
            backend_names.add(str(raw_name))
    if not backend_names:
        backend_names.add("stub")
    ordered_names = tuple(sorted(backend_names))
    backend_name = ordered_names[0] if len(ordered_names) == 1 else "mixed:" + ",".join(ordered_names)

    statuses = [_result_backend_status(result) for result in results]
    missing_statuses = sum(1 for status in statuses if status is None)
    present_statuses = [status for status in statuses if status is not None]
    if len(ordered_names) != 1:
        backend_status = "mixed_backend"
        authoritative = False
    elif backend_name == "stub":
        backend_status = "diagnostic_stub"
        authoritative = False
    elif not results:
        backend_status = "not_run"
        authoritative = False
    elif missing_statuses:
        backend_status = "missing"
        authoritative = False
    elif all(status == "ok" for status in present_statuses):
        backend_status = "ok"
        authoritative = True
    else:
        backend_status = "mixed:" + ",".join(sorted(set(present_statuses)))
        authoritative = False
    inherited_evidence_class = (
        _arm_inherited_evidence_class(tasks, results)
        if backend_name == "cached-real"
        else None
    )
    if backend_name == "cached-real" and authoritative and inherited_evidence_class is None:
        authoritative = False
    if authoritative and backend_name_denies_authority(backend_name):
        authoritative = False
    canonical = canonicalize_fidelity_emission(
        backend_name=backend_name,
        backend_status=backend_status,
        backend_authoritative=authoritative,
        contributors=ordered_names,
        inherited_evidence_class=inherited_evidence_class,
        certification_shape=authoritative and not (
            backend_name == "cached-real" and inherited_evidence_class is None
        ),
    )
    return {
        "tier": tier,
        "fidelity_name": fidelity_name,
        "backend_name": backend_name,
        "backend_status": backend_status,
        "authoritative": authoritative,
        **canonical,
    }


def _arm_inherited_evidence_class(
    tasks: Sequence[_FidelityTask],
    results: Sequence[ScoredResult],
) -> str | None:
    for result in results:
        token = _result_inherited_evidence_token(result)
        if token is not None:
            return _inherited_evidence_class_from_token(token)
    for task in tasks:
        cache_config = _task_run_options(task).get("reduced_real_cache")
        if isinstance(cache_config, Mapping):
            token = cache_config.get("authorized_backend_name")
            if token is not None:
                return _inherited_evidence_class_from_token(token)
    return None


def _result_inherited_evidence_token(result: ScoredResult) -> object | None:
    ref = getattr(result, "run_reference", None)
    carriers = (
        ref,
        getattr(ref, "trace", None),
        getattr(result, "eval_spec", None),
    )
    for carrier in carriers:
        token = _carrier_value(carrier, "evidence_class")
        if token is not None:
            return token
        cache_config = _carrier_value(carrier, "reduced_real_cache")
        if isinstance(cache_config, Mapping):
            token = cache_config.get("authorized_backend_name")
            if token is not None:
                return token
    return None


def _carrier_value(carrier: Any, key: str) -> Any:
    if carrier is None:
        return None
    if isinstance(carrier, Mapping):
        return carrier.get(key)
    return getattr(carrier, key, None)


def _inherited_evidence_class_from_token(token: object) -> str:
    value = str(token).strip()
    if value in CANONICAL_EVIDENCE_CLASSES:
        return value
    try:
        mapped = translate_legacy_token("backend/status alias", value)
    except FidelityVocabularyTranslationError:
        return value
    return mapped.evidence_class or value


def _result_backend_status(result: ScoredResult) -> str | None:
    ref = getattr(result, "run_reference", None)
    if ref is None:
        return None
    for carrier in (getattr(ref, "trace", None), ref):
        status = _extract_backend_status(carrier)
        if status is not None:
            return status
    return None


def _extract_backend_status(carrier: Any) -> str | None:
    if carrier is None:
        return None
    if isinstance(carrier, Mapping):
        per_hour = carrier.get("per_hour")
        raw = carrier.get("backend_status")
    else:
        per_hour = getattr(carrier, "per_hour", None)
        raw = getattr(carrier, "backend_status", None)
    if isinstance(per_hour, (list, tuple)) and per_hour:
        status = _extract_backend_status(per_hour[-1])
        if status is not None:
            return status
    if raw is not None:
        return str(raw)
    return None


def _start_method() -> str:
    methods = mp.get_all_start_methods()
    if "PYTEST_CURRENT_TEST" in os.environ and "fork" in methods:
        return "fork"
    if "spawn" in methods:
        return "spawn"
    return methods[0]


def _terminate_workers(workers: Sequence[Any]) -> None:
    for worker in workers:
        if worker.is_alive():
            worker.terminate()


def _join_or_stop_workers(workers: Sequence[Any]) -> None:
    for worker in workers:
        worker.join(1.0)
        if worker.is_alive():
            worker.terminate()
            worker.join(1.0)
        if worker.is_alive() and hasattr(worker, "kill"):
            worker.kill()
            worker.join(1.0)


def _spearman(pairs: Sequence[Pair], objective: str) -> float | None:
    fast_scores: list[float] = []
    high_scores: list[float] = []
    for _, fast, high in pairs:
        if not (fast.feasible and high.feasible):
            continue
        fast_value = _value(fast, objective)
        high_value = _value(high, objective)
        if fast_value is None or high_value is None or fast_value.sense != high_value.sense:
            continue
        fast_scores.append(_score(fast_value))
        high_scores.append(_score(high_value))
    if len(fast_scores) < 2:
        return None
    if len(set(fast_scores)) < 2 or len(set(high_scores)) < 2:
        return None
    rho = spearmanr(fast_scores, high_scores, nan_policy="omit").statistic
    return float(rho) if rho is not None and math.isfinite(float(rho)) else None


def _agreement(pairs: Sequence[Pair]) -> float | None:
    if not pairs:
        return None
    feasibility_classes = {result.feasible for _, fast, high in pairs for result in (fast, high)}
    if len(feasibility_classes) < 2:
        return None
    return sum(f.feasible == h.feasible for _, f, h in pairs) / len(pairs)


def _recall(pairs: Sequence[Pair], objective: str | None, k: int) -> float | None:
    if objective is None:
        return None
    fast = _ranked(pairs, objective, use_high=False)
    high = _ranked(pairs, objective, use_high=True)
    if len(fast) < k or len(high) < k:
        return None
    return len(set(fast[:k]) & set(high[:k])) / k


def _ranked(pairs: Sequence[Pair], objective: str, *, use_high: bool) -> list[int]:
    scored: list[tuple[float, int]] = []
    for index, fast, high in pairs:
        result = high if use_high else fast
        value = _value(result, objective) if result.feasible else None
        if value is not None:
            scored.append((_score(value), index))
    return [index for _, index in sorted(scored, key=lambda item: (-item[0], item[1]))]


def _value(result: ScoredResult, objective: str) -> ObjectiveValue | None:
    if result.objectives is None:
        return None
    return next((value for value in result.objectives.values if value.metric == objective), None)


def _score(value: ObjectiveValue) -> float:
    return value.value if value.sense == "maximize" else -value.value


def _objective_names(requested: Sequence[str] | None, pairs: Sequence[Pair]) -> tuple[str, ...]:
    if requested:
        return tuple(str(name) for name in requested)
    seen: dict[str, int] = {}
    for _, fast, high in pairs:
        for result in (high, fast):
            if result.objectives:
                for value in result.objectives.values:
                    seen.setdefault(value.metric, value.ordinal)
        if seen:
            break
    return tuple(name for name, _ in sorted(seen.items(), key=lambda item: item[1]))


def _primary(pairs: Sequence[Pair], names: Sequence[str]) -> str | None:
    ordinals = {name: position for position, name in enumerate(names)}
    for _, fast, high in pairs:
        for result in (high, fast):
            if result.objectives:
                for value in result.objectives.values:
                    if value.metric in names:
                        ordinals[value.metric] = value.ordinal
    return min(ordinals.items(), key=lambda item: item[1])[0] if ordinals else None


def _missing_declared_objective_notes(
    pairs: Sequence[Pair],
    names: Sequence[str],
) -> tuple[str, ...]:
    notes: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for _, fast, high in pairs:
        for tier, result in (("fast", fast), ("high", high)):
            if not result.feasible:
                continue
            present = (
                {value.metric for value in result.objectives.values}
                if result.objectives is not None
                else set()
            )
            for name in names:
                if name in present:
                    continue
                candidate = result.candidate_id or "<unknown>"
                key = (tier, candidate, name)
                if key in seen:
                    continue
                seen.add(key)
                notes.append(
                    f"declared objective {name!r} missing in {tier} arm "
                    f"for candidate {candidate}"
                )
    return tuple(notes)


def _verdict(
    spearman: Mapping[str, float | None],
    agreement: float | None,
    recalls: Mapping[int, float | None],
    n_compared: int,
    n_total: int,
    n_dropped: int,
    thresholds: Mapping[str, Mapping[str, Any]],
    primary: str | None,
) -> tuple[bool, str, tuple[str, ...]]:
    notes: list[str] = []
    compared_fraction = n_compared / n_total if n_total else 0.0
    min_compared_fraction = thresholds["min_compared_fraction"]["value"]
    checks = [
        agreement is not None and agreement >= thresholds["feasible_agreement_min"]["value"],
        compared_fraction >= min_compared_fraction,
        primary is not None,
    ]
    checks += [rho is not None and rho >= thresholds["spearman_min"]["value"] for rho in spearman.values()]
    checks += [recall is not None and recall >= thresholds["top_k_recall_min"]["value"] for recall in recalls.values()]
    if agreement is None:
        notes.append("feasibility agreement unavailable; compared samples do not span both feasibility classes")
    if n_compared < n_total:
        notes.append(
            f"partial DOE data: compared fraction {compared_fraction:.3f} "
            f"(minimum {min_compared_fraction:.3f})"
        )
        if compared_fraction < min_compared_fraction:
            notes.append("verdict withheld for partial DOE below min_compared_fraction")
    if n_compared < 2:
        notes.append("fewer than two compared samples; rank correlation undefined")
    if primary is None:
        notes.append("top-K recall unavailable; no primary objective found")
    elif any(recall is None for recall in recalls.values()):
        notes.append("top-K recall unavailable for one or more requested K values")
    verdict = bool(checks and all(checks))
    if not verdict and not notes:
        notes.append("one or more fidelity-correlation thresholds failed")
    return verdict, "high" if verdict else "low", tuple(notes)


def _high_arm_authority_reason(backend_arms: Mapping[str, Mapping[str, Any]]) -> str:
    high = backend_arms.get("high", {})
    fast = backend_arms.get("fast", {})
    high_backend = str(high.get("backend_name", "stub") or "stub")
    fast_backend = str(fast.get("backend_name", "stub") or "stub")
    if high_backend == "stub":
        if fast_backend == "stub":
            return STUB_DIAGNOSTIC_REASON
        return "high arm stub diagnostic, not authoritative"
    if high.get("evidence_class") is not None and not high.get(
        "certification_allowed", False
    ):
        return (
            "high arm evidence_class="
            f"{high.get('evidence_class')!r}, not certifiable"
        )
    if not bool(high.get("authoritative", False)):
        status = str(high.get("backend_status", "missing") or "missing")
        return f"high arm backend_status={status!r}, not authoritative"
    return ""


def _payload_reason(
    verdict: bool,
    notes: Sequence[str],
    authority_reason: str,
) -> str:
    if verdict:
        return ""
    if authority_reason:
        return authority_reason
    return str(notes[0]) if notes else ""


def _artifacts(
    artifact_dir: str | Path | None,
    protocol: FidelityCorrelationProtocol,
    spearman: Mapping[str, float | None],
    agreement: float | None,
    recalls: Mapping[int, float | None],
    n_requested: int,
    n_total: int,
    n_compared: int,
    drops: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Mapping[str, Any]],
    verdict: bool,
    confidence: str,
    notes: Sequence[str],
    primary: str | None,
    backend_arms: Mapping[str, Mapping[str, Any]],
    reason: str,
) -> Mapping[str, str]:
    if artifact_dir is None:
        return {}
    out = Path(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "fidelity_correlation.json"
    md_path = out / "fidelity_correlation.md"
    payload = {
        "protocol": protocol.to_dict(),
        "spearman_by_objective": dict(spearman),
        "feasible_infeasible_agreement": agreement,
        "top_k_recall": {str(k): v for k, v in recalls.items()},
        "primary_objective": primary,
        "n_requested": n_requested,
        "n_samples_total": n_total,
        "n_samples_compared": n_compared,
        "n_samples_dropped": len(drops),
        "dropped_evaluations": [dict(drop) for drop in drops],
        "thresholds": dict(thresholds),
        "fast_screen_trustworthy": verdict,
        "confidence": confidence,
        "reason": reason,
        "backend_arms": {arm: dict(metadata) for arm, metadata in backend_arms.items()},
        "notes": list(notes),
        "artifact_paths": {"json": str(json_path), "markdown": str(md_path)},
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    md_path.write_text(_markdown(payload))
    return {"json": str(json_path), "markdown": str(md_path)}


def _markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Fidelity Correlation Report",
        "",
        f"- Verdict: {payload['fast_screen_trustworthy']}",
        f"- Confidence: {payload['confidence']}",
        f"- Reason: {payload['reason']}",
        f"- Compared: {payload['n_samples_compared']} / {payload['n_samples_total']}",
        f"- Dropped: {payload['n_samples_dropped']}",
        "",
        "## Metrics",
        f"- Feasible/infeasible agreement: {payload['feasible_infeasible_agreement']}",
        f"- Primary objective: {payload['primary_objective']}",
    ]
    lines += [f"- Spearman {name}: {value}" for name, value in payload["spearman_by_objective"].items()]
    lines += [f"- Top-{k} recall: {value}" for k, value in payload["top_k_recall"].items()]
    lines += ["", "## Thresholds"]
    lines += [
        f"- {name}: {item['value']} ({item['source_type']}; {item['source']})"
        for name, item in payload["thresholds"].items()
    ]
    lines += ["", "## Backend Arms"]
    for arm, metadata in payload["backend_arms"].items():
        lines.append(
            "- "
            f"{arm}: backend_name={metadata['backend_name']}; "
            f"backend_status={metadata['backend_status']}; "
            f"authoritative={metadata['authoritative']}"
        )
    if payload["notes"]:
        lines += ["", "## Notes", *(f"- {note}" for note in payload["notes"])]
    return "\n".join(lines) + "\n"


def _top_k(values: Sequence[int]) -> tuple[int, ...]:
    normalized = tuple(dict.fromkeys(int(value) for value in values))
    if not normalized or any(value <= 0 for value in normalized):
        raise ValueError("top_k must contain positive integers")
    return normalized


def _thresholds(source: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Mapping[str, Any]]:
    required = ("spearman_min", "top_k_recall_min", "feasible_agreement_min", "min_compared_fraction")
    normalized: dict[str, Mapping[str, Any]] = {}
    for name in required:
        item = dict(source.get(name, {}))
        if item.get("value") is None:
            raise ValueError(f"{name} threshold value is required")
        if item.get("source_type") not in {"literature", "engineering_envelope", "profile"}:
            raise ValueError(f"{name} threshold source_type is required")
        if not item.get("source"):
            raise ValueError(f"{name} threshold source is required")
        if item.get("source_type") == "literature" and not _has_checkable_reference(
            str(item.get("source", ""))
        ):
            raise ValueError(f"{name} literature threshold source must include DOI, PMID, or URL")
        normalized[name] = item
    return normalized


def _has_checkable_reference(source: str) -> bool:
    lowered = source.lower()
    return any(token in lowered for token in ("doi", "pmid", "http://", "https://", "www."))


def _drop(index: int, tier: str, candidate_id: str, reason: str, message: str) -> Mapping[str, Any]:
    return {
        "sample_index": index,
        "tier": tier,
        "candidate_id": candidate_id,
        "reason": reason,
        "message": message,
    }
