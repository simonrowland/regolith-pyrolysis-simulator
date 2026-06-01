"""Phase-O recipe optimizer study loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import csv
import inspect
import json
import math
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Any

import yaml

from simulator.config import DEFAULT_DATA_DIR, load_config_bundle
from simulator.optimize.evaluate import (
    EvaluationAbort,
    FailureCategory,
    RunReference,
    ScoredResult,
    _build_eval_inputs,
    evaluate,
)
from simulator.optimize.objective import (
    ObjectiveComputationError,
    ObjectiveDefinition,
    ObjectiveProfileError,
    ObjectiveVector,
    objective_definitions,
    objective_scores,
    pareto_front,
)
from simulator.optimize.pool import PoolEvaluationRequest, evaluate_batch
from simulator.optimize.physics import FeasibilityResult, GateMargin, ThresholdSpec
from simulator.optimize.recipe import RecipePatch, RecipeSchema, RecipeValidationError
from simulator.optimize.results_store import ResultStore
from simulator.optimize.strategy import (
    Candidate,
    MorrisScreenStrategy,
    RandomStrategy,
    Strategy,
)

VALID_FIDELITIES = ("stub", "fast", "high", "auto")
STRATEGY_CLASS_NAMES = {
    "random": "RandomStrategy",
    "screen": "MorrisScreenStrategy",
    "bayes": "OptunaTPEStrategy",
    "nsga2": "OptunaNSGA2Strategy",
    "staged": "StagedStrategy",
}
WINNER_SELECTION_RULE = (
    "choose the feasible Pareto point with the best primary objective "
    "(profile objective ordinal 0); ties compare the remaining profile "
    "objectives in order using their declared directions, then cache_key, "
    "then candidate_id"
)
DEFAULT_PROFILE_NAME = "default"
DEFAULT_PROFILES: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        DEFAULT_PROFILE_NAME: MappingProxyType(
            {
                "profile_id": "phase-o-default",
                "profile_schema_version": "profile-schema-v1",
                "objectives": (
                    {"metric": "oxygen_kg", "sense": "maximize", "units": "kg"},
                    {"metric": "energy_kWh", "sense": "minimize", "units": "kWh"},
                    {"metric": "duration_h", "sense": "minimize", "units": "h"},
                ),
                "run": {
                    "campaign": "C0",
                    "hours": 1,
                    "mass_kg": 1000.0,
                    "backend_name": "stub",
                },
                "study_constraints": "stub_smoke",
                "fidelities": {
                    "stub": {"backend_name": "stub", "hours": 1},
                    "fast": {"backend_name": "stub", "hours": 1},
                    "high": {"backend_name": "stub", "hours": 1},
                    "auto": {"backend_name": "stub", "hours": 1},
                },
            }
        )
    }
)

EvaluateFn = Callable[..., ScoredResult]


class StudyError(RuntimeError):
    """Raised when the study loop cannot produce honest artifacts."""


class StudyAbort(StudyError):
    """Raised when a study result represents an abort category."""


class StudyNoFeasibleError(StudyError):
    """Raised when no feasible Pareto winner exists."""


class StubSmokeConstraintSet:
    """Explicit built-in smoke gate for offline CLI wiring tests."""

    def evaluate(self, trace: Any) -> FeasibilityResult:
        threshold = ThresholdSpec(
            id="stub_smoke_feasible",
            value=1.0,
            units="boolean",
            source="engineering_envelope",
            source_ref="Phase-O default profile smoke constraint",
        )
        return FeasibilityResult(
            feasible=True,
            margins={
                "stub_smoke": GateMargin(
                    gate="stub_smoke",
                    feasible=True,
                    margin=1.0,
                    threshold=threshold,
                    observed=1.0,
                    detail="built-in default profile smoke gate",
                )
            },
        )


@dataclass(frozen=True)
class StudyConfig:
    profile: str | Mapping[str, Any]
    feedstock: str
    strategy: str | Strategy
    fidelity: str
    parallel: int = 1
    budget: int = 1
    out_dir: str | Path | None = None
    seed: int = 0


@dataclass(frozen=True)
class StudyRecord:
    candidate_id: str
    patch: RecipePatch
    feasible: bool
    status: str
    objectives: Mapping[str, float]
    feasibility_margins: Mapping[str, Mapping[str, Any]]
    cache_key: str | None = None
    failure_category: str | None = None
    failing_gates: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    cache_hit: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "objectives", MappingProxyType(dict(self.objectives)))
        object.__setattr__(
            self,
            "feasibility_margins",
            MappingProxyType(
                {
                    str(key): MappingProxyType(dict(value))
                    for key, value in self.feasibility_margins.items()
                }
            ),
        )
        object.__setattr__(self, "failing_gates", tuple(self.failing_gates))
        object.__setattr__(self, "notes", tuple(self.notes))


@dataclass(frozen=True)
class StudyResult:
    out_dir: Path
    store_path: Path
    artifacts: Mapping[str, Path]
    records: tuple[StudyRecord, ...]
    leaderboard: tuple[StudyRecord, ...]
    pareto: tuple[StudyRecord, ...]
    winner: StudyRecord
    winner_selection_rule: str = WINNER_SELECTION_RULE

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", MappingProxyType(dict(self.artifacts)))


def run(
    profile: str | Mapping[str, Any],
    feedstock: str,
    strategy: str | Strategy,
    fidelity: str,
    parallel: int,
    budget: int,
    out_dir: str | Path | None = None,
    *,
    seed: int = 0,
    evaluator: EvaluateFn = evaluate,
    schema: RecipeSchema | None = None,
    result_store: ResultStore | None = None,
    constraints: Any = None,
) -> StudyResult:
    """Run one ask/evaluate/tell study and write Phase-O artifacts."""

    active_schema = schema or RecipeSchema()
    config = StudyConfig(
        profile=profile,
        feedstock=feedstock,
        strategy=strategy,
        fidelity=fidelity,
        parallel=parallel,
        budget=budget,
        out_dir=out_dir,
        seed=seed,
    )
    resolved_profile = resolve_profile(config.profile)
    definitions = objective_definitions(resolved_profile)
    _validate_inputs(config, resolved_profile)
    active_constraints = (
        _constraints_for_profile(resolved_profile)
        if constraints is None
        else constraints
    )
    out = _resolve_out_dir(config.out_dir)
    _prepare_out_dir(out)
    store = result_store or ResultStore(out / "cache.sqlite")
    active_strategy = resolve_strategy(
        config.strategy,
        profile=resolved_profile,
        seed=config.seed,
        schema=active_schema,
    )

    records: list[StudyRecord] = []
    provenance_path = out / "provenance.jsonl"
    evaluated = 0
    with provenance_path.open("w", encoding="utf-8") as provenance:
        while evaluated < config.budget:
            batch_size = min(config.parallel, config.budget - evaluated)
            candidates = active_strategy.ask(batch_size)
            if not candidates:
                break
            results = _evaluate_candidates(
                candidates,
                profile=resolved_profile,
                feedstock=config.feedstock,
                fidelity=config.fidelity,
                parallel=config.parallel,
                out_dir=out,
                evaluator=evaluator,
                schema=active_schema,
                constraints=active_constraints,
                store=store,
            )
            tell_batch: list[tuple[Candidate, ScoredResult]] = []
            for candidate, scored, cache_hit in results:
                _assert_honest_result(scored, definitions)
                light_scored = _strip_heavy_result(scored)
                if scored.eval_spec is not None:
                    store.store(
                        scored.eval_spec,
                        light_scored,
                        created_at=datetime.now(UTC).isoformat(),
                    )
                record = _to_record(candidate, scored, cache_hit=cache_hit)
                records.append(record)
                provenance.write(
                    json.dumps(
                        _record_payload(record, active_schema),
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n"
                )
                tell_batch.append((candidate, light_scored))
            active_strategy.tell(tell_batch)
            evaluated += len(candidates)

    feasible = tuple(record for record in records if record.feasible)
    leaderboard = tuple(sorted(feasible, key=lambda row: _rank_key(row, definitions)))
    pareto = pareto_front(
        feasible,
        definitions,
        objective_getter=lambda row: row.objectives,
    )
    pareto_ranked = tuple(sorted(pareto, key=lambda row: _rank_key(row, definitions)))
    if not pareto_ranked:
        _write_empty_artifacts(
            out,
            profile=resolved_profile,
            feedstock=feedstock,
            fidelity=fidelity,
            definitions=definitions,
        )
        raise StudyNoFeasibleError("no feasible candidates; winner.recipe.yaml not written")
    winner = pareto_ranked[0]
    artifacts = _write_artifacts(
        out,
        profile=resolved_profile,
        feedstock=feedstock,
        fidelity=fidelity,
        definitions=definitions,
        pareto=pareto_ranked,
        leaderboard=leaderboard,
        winner=winner,
        schema=active_schema,
    )
    artifacts["provenance"] = provenance_path
    artifacts["store"] = store.path
    return StudyResult(
        out_dir=out,
        store_path=store.path,
        artifacts=artifacts,
        records=tuple(records),
        leaderboard=leaderboard,
        pareto=pareto_ranked,
        winner=winner,
    )


def resolve_profile(profile: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(profile, str):
        try:
            return MappingProxyType(dict(DEFAULT_PROFILES[profile]))
        except KeyError as exc:
            raise ValueError(f"unknown profile {profile!r}") from exc
    if not isinstance(profile, Mapping):
        raise TypeError("profile must be a profile name or mapping")
    return MappingProxyType(dict(profile))


def resolve_strategy(
    strategy: str | Strategy,
    *,
    profile: Mapping[str, Any],
    seed: int,
    schema: RecipeSchema,
) -> Strategy:
    if not isinstance(strategy, str):
        return strategy
    if strategy == "random":
        return RandomStrategy(schema, seed=seed)
    if strategy == "screen":
        return MorrisScreenStrategy(schema, seed=seed)
    if strategy == "staged":
        raise StudyError("staged strategy is O-P5b, not implemented in O-P5a")
    if strategy == "bayes":
        from simulator.optimize.strategy import OptunaTPEStrategy

        return OptunaTPEStrategy(schema, seed=seed, objective_profile=profile)
    if strategy == "nsga2":
        from simulator.optimize.strategy import OptunaNSGA2Strategy

        return OptunaNSGA2Strategy(schema, seed=seed, objective_profile=profile)
    raise ValueError(f"unknown strategy {strategy!r}")


def _constraints_for_profile(profile: Mapping[str, Any]) -> Any:
    selector = profile.get("study_constraints")
    if selector is None:
        return None
    if selector == "stub_smoke":
        return StubSmokeConstraintSet()
    raise ValueError(f"unknown study_constraints {selector!r}")


def _validate_inputs(config: StudyConfig, profile: Mapping[str, Any]) -> None:
    if isinstance(config.budget, bool) or not isinstance(config.budget, int) or config.budget <= 0:
        raise ValueError("budget must be a positive int")
    if (
        isinstance(config.parallel, bool)
        or not isinstance(config.parallel, int)
        or config.parallel <= 0
    ):
        raise ValueError("parallel must be a positive int")
    if isinstance(config.seed, bool) or not isinstance(config.seed, int) or config.seed < 0:
        raise ValueError("seed must be a non-negative int")
    if config.fidelity not in VALID_FIDELITIES:
        raise ValueError(f"unknown fidelity {config.fidelity!r}")
    fidelities = profile.get("fidelities")
    if isinstance(fidelities, Mapping) and config.fidelity not in fidelities:
        raise ValueError(
            f"profile {profile_id(profile)!r} has no fidelity {config.fidelity!r}"
        )
    bundle = load_config_bundle(DEFAULT_DATA_DIR)
    if config.feedstock not in bundle.feedstocks:
        raise ValueError(f"unknown feedstock {config.feedstock!r}")


def _resolve_out_dir(out_dir: str | Path | None) -> Path:
    if out_dir is not None:
        return Path(out_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("runs") / stamp


def _prepare_out_dir(out: Path) -> None:
    if out.exists() and not out.is_dir():
        raise StudyError(f"output path exists and is not a directory: {out}")
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StudyError(f"could not create output directory {out}: {exc}") from exc


def _evaluate_candidates(
    candidates: Sequence[Candidate],
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    parallel: int,
    out_dir: Path,
    evaluator: EvaluateFn,
    schema: RecipeSchema,
    constraints: Any,
    store: ResultStore,
) -> tuple[tuple[Candidate, ScoredResult, bool], ...]:
    results: list[tuple[Candidate, ScoredResult, bool] | None] = [None] * len(candidates)
    misses: list[tuple[int, Candidate]] = []
    for index, candidate in enumerate(candidates):
        cached = _lookup_cached(candidate, profile, feedstock, fidelity, schema, store)
        if cached is None:
            misses.append((index, candidate))
        else:
            results[index] = (candidate, cached, True)

    if misses:
        if parallel == 1:
            for index, candidate in misses:
                scored = _evaluate_one(
                    candidate,
                    profile=profile,
                    feedstock=feedstock,
                    fidelity=fidelity,
                    out_dir=out_dir,
                    evaluator=evaluator,
                    schema=schema,
                    constraints=constraints,
                )
                results[index] = (candidate, scored, False)
        else:
            requests = [
                PoolEvaluationRequest(
                    candidate.patch,
                    feedstock,
                    fidelity,
                    profile=profile,
                    candidate_id=candidate.id,
                    output_dir=out_dir / "worker-output" / candidate.id,
                )
                for _, candidate in misses
            ]
            batch = evaluate_batch(
                requests,
                profile=profile,
                max_workers=parallel,
                output_root=out_dir / "worker-output",
                evaluate_fn=evaluator,
                schema=schema,
                constraints=constraints,
            )
            for (index, candidate), scored in zip(misses, batch):
                results[index] = (candidate, _with_candidate_id(scored, candidate.id), False)

    completed = tuple(result for result in results if result is not None)
    if len(completed) != len(candidates):
        raise RuntimeError("study evaluation ended without all candidate results")
    return completed


def _lookup_cached(
    candidate: Candidate,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    schema: RecipeSchema,
    store: ResultStore,
) -> ScoredResult | None:
    try:
        validated = candidate.patch.validated(schema)
        spec, _ = _build_eval_inputs(validated, feedstock, fidelity, profile, schema)
    except RecipeValidationError:
        return None
    cached = store.lookup(spec)
    if cached is None:
        return None
    return _with_candidate_id(cached, candidate.id)


def _evaluate_one(
    candidate: Candidate,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    out_dir: Path,
    evaluator: EvaluateFn,
    schema: RecipeSchema,
    constraints: Any,
) -> ScoredResult:
    try:
        scored = _call_evaluator(
            evaluator,
            candidate.patch,
            feedstock,
            fidelity,
            profile=profile,
            candidate_id=candidate.id,
            schema=schema,
            constraints=constraints,
            output_dir=out_dir / "evals" / candidate.id,
        )
    except EvaluationAbort:
        raise
    if not isinstance(scored, ScoredResult):
        raise TypeError("evaluator must return ScoredResult")
    return _with_candidate_id(scored, candidate.id)


def _call_evaluator(
    evaluator: EvaluateFn,
    patch: RecipePatch,
    feedstock: str,
    fidelity: str,
    **kwargs: Any,
) -> ScoredResult:
    signature = inspect.signature(evaluator)
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    accepted = {
        key: value
        for key, value in kwargs.items()
        if accepts_kwargs or key in signature.parameters
    }
    return evaluator(patch, feedstock, fidelity, **accepted)


def _with_candidate_id(scored: ScoredResult, candidate_id: str) -> ScoredResult:
    if scored.candidate_id == candidate_id:
        return scored
    if scored.candidate_id is not None:
        raise ValueError(
            "ScoredResult.candidate_id must match Candidate.id "
            f"({scored.candidate_id!r} != {candidate_id!r})"
        )
    return replace(scored, candidate_id=candidate_id)


def _assert_honest_result(
    scored: ScoredResult,
    definitions: Sequence[ObjectiveDefinition],
) -> None:
    _assert_finite_margins(scored)
    if scored.failure_category in {
        FailureCategory.ENGINE_BUG,
        FailureCategory.BACKEND_UNAVAILABLE,
    }:
        raise StudyAbort(f"aborting study on {scored.failure_category.value} result")
    if not scored.feasible:
        return
    if scored.eval_spec is None or scored.cache_key is None:
        raise StudyAbort("feasible result missing eval_spec/cache_key")
    if scored.objectives is None:
        raise StudyAbort("feasible result missing objective vector")
    try:
        objective_scores(scored.objectives, definitions)
    except ObjectiveComputationError as exc:
        raise StudyAbort(str(exc)) from exc


def _assert_finite_margins(scored: ScoredResult) -> None:
    for name, margin in scored.feasibility_margins.items():
        prefix = f"feasibility margin {name!r}"
        _finite(getattr(margin, "margin", None), f"{prefix}.margin")
        _finite(getattr(margin, "observed", None), f"{prefix}.observed")


def _strip_heavy_result(scored: ScoredResult) -> ScoredResult:
    reference = scored.run_reference
    if reference is None:
        return scored
    light_reference = RunReference(
        status=reference.status,
        error_message=reference.error_message,
        reason=reference.reason,
        trace=None,
        product_summary=reference.product_summary,
    )
    return replace(scored, run_reference=light_reference)


def _to_record(candidate: Candidate, scored: ScoredResult, *, cache_hit: bool) -> StudyRecord:
    failure = scored.failure_category
    objectives = _objective_mapping(scored.objectives)
    return StudyRecord(
        candidate_id=candidate.id,
        patch=candidate.patch,
        feasible=bool(scored.feasible),
        status=_status(scored),
        objectives=objectives,
        feasibility_margins=_margin_mapping(scored.feasibility_margins),
        cache_key=scored.cache_key,
        failure_category=failure.value if failure is not None else None,
        failing_gates=scored.failing_gates,
        notes=scored.notes,
        cache_hit=cache_hit,
    )


def _objective_mapping(objectives: ObjectiveVector | None) -> Mapping[str, float]:
    if objectives is None:
        return MappingProxyType({})
    mapping = objectives.as_mapping()
    return MappingProxyType({str(key): _finite(value, str(key)) for key, value in mapping.items()})


def _margin_mapping(margins: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    return MappingProxyType({str(key): _margin_payload(value) for key, value in margins.items()})


def _margin_payload(margin: Any) -> Mapping[str, Any]:
    threshold = getattr(margin, "threshold", None)
    payload: dict[str, Any] = {
        "gate": str(getattr(margin, "gate", "")),
        "feasible": bool(getattr(margin, "feasible", False)),
        "margin": _json_number(getattr(margin, "margin", 0.0), "margin"),
        "observed": _json_number(getattr(margin, "observed", 0.0), "observed"),
        "detail": str(getattr(margin, "detail", "")),
    }
    if threshold is not None:
        payload["threshold"] = {
            "id": str(getattr(threshold, "id", "")),
            "value": _finite(getattr(threshold, "value", 0.0), "threshold.value"),
            "units": str(getattr(threshold, "units", "")),
            "source": str(getattr(threshold, "source", "")),
            "source_ref": str(getattr(threshold, "source_ref", "")),
            "tolerance": _finite(
                getattr(threshold, "tolerance", 0.0),
                "threshold.tolerance",
            ),
        }
    return MappingProxyType(payload)


def _status(scored: ScoredResult) -> str:
    if scored.failure_category is not None:
        return scored.failure_category.value
    if scored.run_reference is not None:
        return scored.run_reference.status
    return "ok" if scored.feasible else "unknown"


def _finite(value: Any, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise StudyAbort(f"{label} is not numeric") from exc
    if not math.isfinite(numeric):
        raise StudyAbort(f"{label} is non-finite")
    return numeric


def _json_number(value: Any, label: str) -> float:
    return _finite(value, label)


def _rank_key(
    record: StudyRecord,
    definitions: Sequence[ObjectiveDefinition],
) -> tuple[Any, ...]:
    scores = objective_scores(record.objectives, definitions)
    return (*(-score for score in scores), record.cache_key or "", record.candidate_id)


def _write_artifacts(
    out: Path,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    definitions: Sequence[ObjectiveDefinition],
    pareto: Sequence[StudyRecord],
    leaderboard: Sequence[StudyRecord],
    winner: StudyRecord,
    schema: RecipeSchema,
) -> dict[str, Path]:
    pareto_path = out / "pareto.json"
    leaderboard_path = out / "leaderboard.csv"
    winner_path = out / "winner.recipe.yaml"
    pareto_path.write_text(
        json.dumps(
            _pareto_payload(profile, feedstock, fidelity, definitions, pareto, winner, schema),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_leaderboard(leaderboard_path, leaderboard, pareto, winner, definitions, schema)
    winner_path.write_text(
        yaml.safe_dump(schema.to_setpoints_patch(winner.patch), sort_keys=True),
        encoding="utf-8",
    )
    return {
        "pareto": pareto_path,
        "leaderboard": leaderboard_path,
        "winner": winner_path,
    }


def _write_empty_artifacts(
    out: Path,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    definitions: Sequence[ObjectiveDefinition],
) -> None:
    (out / "pareto.json").write_text(
        json.dumps(
            {
                "feedstock": feedstock,
                "fidelity": fidelity,
                "objectives": [_definition_payload(definition) for definition in definitions],
                "pareto": [],
                "profile": profile_id(profile),
                "selection_rule": WINNER_SELECTION_RULE,
                "winner_candidate_id": None,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_leaderboard(out / "leaderboard.csv", (), (), None, definitions, RecipeSchema())


def _pareto_payload(
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    definitions: Sequence[ObjectiveDefinition],
    pareto: Sequence[StudyRecord],
    winner: StudyRecord,
    schema: RecipeSchema,
) -> Mapping[str, Any]:
    return {
        "feedstock": feedstock,
        "fidelity": fidelity,
        "objectives": [_definition_payload(definition) for definition in definitions],
        "pareto": [_record_payload(record, schema) for record in pareto],
        "profile": profile_id(profile),
        "selection_rule": WINNER_SELECTION_RULE,
        "winner_candidate_id": winner.candidate_id,
    }


def _definition_payload(definition: ObjectiveDefinition) -> Mapping[str, Any]:
    return {
        "metric": definition.metric,
        "sense": definition.sense,
        "units": definition.units,
        "ordinal": definition.ordinal,
    }


def _write_leaderboard(
    path: Path,
    leaderboard: Sequence[StudyRecord],
    pareto: Sequence[StudyRecord],
    winner: StudyRecord | None,
    definitions: Sequence[ObjectiveDefinition],
    schema: RecipeSchema,
) -> None:
    pareto_ids = {record.candidate_id for record in pareto}
    margin_names = sorted({name for record in leaderboard for name in record.feasibility_margins})
    fieldnames = [
        "rank",
        "candidate_id",
        "cache_key",
        "is_pareto",
        "is_winner",
        *(definition.metric for definition in definitions),
        *(f"margin_{name}" for name in margin_names),
        "patch_json",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for rank, record in enumerate(leaderboard, start=1):
            row: dict[str, Any] = {
                "rank": rank,
                "candidate_id": record.candidate_id,
                "cache_key": record.cache_key or "",
                "is_pareto": record.candidate_id in pareto_ids,
                "is_winner": bool(winner and record.candidate_id == winner.candidate_id),
                "patch_json": json.dumps(
                    schema.to_setpoints_patch(record.patch),
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            }
            row.update({definition.metric: record.objectives[definition.metric] for definition in definitions})
            row.update(
                {
                    f"margin_{name}": record.feasibility_margins[name]["margin"]
                    for name in margin_names
                    if name in record.feasibility_margins
                }
            )
            writer.writerow(row)


def _record_payload(record: StudyRecord, schema: RecipeSchema) -> Mapping[str, Any]:
    return {
        "cache_hit": record.cache_hit,
        "cache_key": record.cache_key,
        "candidate_id": record.candidate_id,
        "failure_category": record.failure_category,
        "failing_gates": list(record.failing_gates),
        "feasibility_margins": {
            key: dict(value) for key, value in record.feasibility_margins.items()
        },
        "feasible": record.feasible,
        "notes": list(record.notes),
        "objectives": dict(record.objectives),
        "patch": schema.to_setpoints_patch(record.patch),
        "status": record.status,
    }


def profile_id(profile: Mapping[str, Any]) -> str:
    return str(profile.get("profile_id") or profile.get("id") or "inline-profile")


def _default_out_dir_for_tests() -> Path:
    return Path(tempfile.mkdtemp(prefix="regolith-optimizer-study-"))
