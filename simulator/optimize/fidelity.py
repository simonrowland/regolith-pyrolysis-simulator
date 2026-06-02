"""Fast-vs-high fidelity correlation harness for Phase-O recipe optimization."""

from __future__ import annotations

import json
import math
import multiprocessing as mp
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
from simulator.optimize.evaluate import EvaluationAbort, ScoredResult
from simulator.optimize.objective import ObjectiveValue

EvaluateFn = Callable[..., ScoredResult]
EvalOutcome = tuple[ScoredResult | None, Mapping[str, Any] | None]
Pair = tuple[int, ScoredResult, ScoredResult]

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
    n_total = min(doe_spec.n_samples, int(max_samples)) if max_samples is not None else doe_spec.n_samples
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

    for index, patch in enumerate(patches):
        fast_id = f"fidelity-doe-{index:06d}-fast"
        high_id = f"fidelity-doe-{index:06d}-high"
        fast, fast_drop = _run_eval(
            evaluate_fn_fast,
            patch,
            feedstock_id,
            fast_fidelity_name,
            prof,
            fast_id,
            per_eval_timeout_s,
            kwargs,
            index,
            "fast",
        )
        high, high_drop = _run_eval(
            evaluate_fn_high,
            patch,
            feedstock_id,
            high_fidelity_name,
            prof,
            high_id,
            per_eval_timeout_s,
            kwargs,
            index,
            "high",
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
    verdict, confidence, notes = _verdict(
        spearman, agreement, recalls, len(pairs), n_total, len(drops), threshold_profile, primary
    )
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


def _run_eval(
    fn: EvaluateFn,
    patch: Any,
    feedstock_id: str,
    fidelity: str,
    profile: Mapping[str, Any],
    candidate_id: str,
    timeout_s: float,
    kwargs: Mapping[str, Any],
    index: int,
    tier: str,
) -> EvalOutcome:
    ctx = mp.get_context("fork" if "fork" in mp.get_all_start_methods() else "spawn")
    queue: mp.Queue[Any] = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_worker,
        args=(queue, fn, patch, feedstock_id, fidelity, dict(profile), candidate_id, dict(kwargs)),
    )
    process.start()
    process.join(timeout_s)
    if process.is_alive():
        process.terminate()
        process.join(1.0)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(1.0)
        return None, _drop(index, tier, candidate_id, "timeout", "per-eval timeout")
    try:
        status, payload = queue.get_nowait()
    except Empty:
        return None, _drop(
            index,
            tier,
            candidate_id,
            "engine_bug",
            f"worker exited without result; exitcode={process.exitcode}",
        )
    if status == "ok":
        return payload, None
    return None, _drop(index, tier, candidate_id, payload["category"], payload["message"])


def _worker(
    queue: Any,
    fn: EvaluateFn,
    patch: Any,
    feedstock_id: str,
    fidelity: str,
    profile: Mapping[str, Any],
    candidate_id: str,
    kwargs: Mapping[str, Any],
) -> None:
    try:
        result = fn(
            patch,
            feedstock_id,
            fidelity,
            profile=profile,
            candidate_id=candidate_id,
            **dict(kwargs),
        )
    except EvaluationAbort as exc:
        queue.put(
            (
                "error",
                {
                    "category": getattr(exc.category, "value", str(exc.category)),
                    "message": str(exc),
                },
            )
        )
    except BaseException as exc:
        queue.put(("error", {"category": "error", "message": f"{type(exc).__name__}: {exc}"}))
    else:
        queue.put(("ok", result))


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
