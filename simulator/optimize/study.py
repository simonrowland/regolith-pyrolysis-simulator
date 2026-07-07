"""Phase-O recipe optimizer study loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import copy
import csv
import inspect
import json
import logging
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from simulator.backends import CACHE_TIER_CEILINGS, DEFAULT_CACHE_TIER_CEILING
from simulator.config import DEFAULT_DATA_DIR, load_config_bundle
from simulator.diagnostics import coating_summary_with_grounded_authority
from simulator.optimize.doe import active_sampler_name
from simulator.optimize.evaluate import (
    EvaluationAbort,
    FailureCategory,
    RunReference,
    ScoredResult,
    _build_eval_inputs,
    _is_stale_profile_refusal,
    _stale_profile_result,
    evaluate,
)
from simulator.optimize.evalspec import PrefixEvalSpec, cache_key
from simulator.optimize.objective import (
    ObjectiveComputationError,
    ObjectiveDefinition,
    ObjectiveProfileError,
    ObjectiveVector,
    objective_definitions,
    objective_scores,
    pareto_front,
)
from simulator.optimize.pool import (
    DEFAULT_EVAL_TIMEOUT_SECONDS,
    PoolEvaluationRequest,
    evaluate_batch,
    evaluate_request_supervised,
    resolve_eval_timeout_seconds,
)
from simulator.optimize.profiles import (
    ProfileValidationError,
    physics_constraints_from_profile,
    validate_profile,
)
from simulator.optimize.recipe import RecipePatch, RecipeSchema, RecipeValidationError
from simulator.optimize.results_store import (
    ResultStore,
    ResultStoreWriteRejected,
    reground_scored_result,
)
from simulator.optimize.strategy import (
    Candidate,
    MorrisScreenStrategy,
    RandomStrategy,
    Strategy,
)
from simulator.optimize.strategy.staged import (
    StagedBeamStateError,
    StagedReplayViolation,
    StagedStrategy,
    TopologyChoice,
    assert_prefix_replay_equal,
    enumerate_topologies,
    make_prefix_eval_spec,
)

VALID_FIDELITIES = ("stub", "fast", "high", "auto")
_LOGGER = logging.getLogger(__name__)
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
COMPLETED_STATUS = "completed"
COMPLETED_NO_FEASIBLE_WINNER_STATUS = "completed-no-feasible-winner"
CERTIFIED_CACHE_STATES = frozenset({"cached_exact", "live_fill"})
EXPLORE_CACHE_TIER_CEILING = "cached_interpolated"
CERTIFY_CACHE_TIER_CEILING = "cached_exact"
DEFAULT_TWO_PHASE_TOP_K = 10
_TWO_PHASE_CERTIFICATION_NAME = "two_phase_certification.json"
_SSO2_OBJECTIVE_METRIC = "sso2_pn2_fe_drain_silica"
_SSO2_OBJECTIVE_TRACE_KEY = "sso2_objective_evidence"
_TAP_COATING_PRODUCT_SUMMARY_FIELDS = frozenset(
    {
        "campaigns_to_resinter",
        "wall_deposit_kg_by_segment_species",
        "wall_deposit_kg_by_zone_species",
        "wall_deposit_kg",
        "fouling_rate",
        "coating_status",
        "coating_authoritative",
        "coating_output_status",
        "coating_status_reason",
        "lifespan_cost_status",
        "lifespan_cost_status_reason",
        "furnace_lifespan_consumed_fraction",
        "wall_deposit_total_kg",
        "wall_deposit_kg_by_species",
        "wall_deposit_sticking_authority",
    }
)
_COATING_LEADERBOARD_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "campaigns_to_resinter": "campaigns_to_resinter",
        "wall_deposit_kg_by_segment_species": "wall_deposit_kg_by_segment_species_json",
        "wall_deposit_kg_by_zone_species": "wall_deposit_kg_by_zone_species_json",
        "coating_status": "coating_status",
        "coating_authoritative": "coating_authoritative",
        "coating_output_status": "coating_output_status",
        "coating_status_reason": "coating_status_reason",
        "lifespan_cost_status": "lifespan_cost_status",
        "lifespan_cost_status_reason": "lifespan_cost_status_reason",
        "furnace_lifespan_consumed_fraction": "furnace_lifespan_consumed_fraction",
        "wall_deposit_total_kg": "wall_deposit_total_kg",
        "wall_deposit_kg_by_species": "wall_deposit_kg_by_species_json",
    }
)
DEFAULT_PROFILE_NAME = "default"
DEFAULT_PROFILES: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        DEFAULT_PROFILE_NAME: MappingProxyType(
            {
                "profile_id": "phase-o-default",
                "profile_schema_version": "profile-schema-v1",
                "feedstock": "lunar_mare_low_ti",
                "objectives": [
                    {
                        "metric": "oxygen_kg",
                        "sense": "maximize",
                        "units": "kg",
                        "weight": 0.5,
                        "rationale": "default oxygen objective evidence",
                    },
                    {
                        "metric": "energy_kWh",
                        "sense": "minimize",
                        "units": "kWh",
                        "weight": 0.25,
                        "rationale": "default energy objective evidence",
                    },
                    {
                        "metric": "duration_h",
                        "sense": "minimize",
                        "units": "h",
                        "weight": 0.25,
                        "rationale": "default duration objective evidence",
                    },
                ],
                "constraints": {"gates": ["delivered_stream_purity"]},
                "run": {
                    "campaign": "C0",
                    "hours": 1,
                    "mass_kg": 1000.0,
                    "backend_name": "stub",
                },
                "fidelities": {
                    "stub": {"backend_name": "stub", "hours": 1},
                    "fast": {"backend_name": "stub", "hours": 1},
                    "high": {"backend_name": "stub", "hours": 1},
                    "auto": {"backend_name": "stub", "hours": 1},
                },
                "seed_recipes": [
                    {
                        "id": "phase-o-default-c0-seed",
                        "source_campaign": "C0",
                        "patch": {
                            "campaigns": {
                                "C0": {"temp_range_C": (900, 950)},
                            }
                        },
                    },
                ],
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


_NO_FEASIBLE_CONFIG_FAILURE_CATEGORIES = frozenset(
    {
        FailureCategory.INVALID_PATCH.value,
        FailureCategory.INVALID_RECIPE.value,
        FailureCategory.NON_FINITE_PAYLOAD.value,
        FailureCategory.STALE_PROFILE.value,
        FailureCategory.ZERO_INPUT_BASIS_BREACH.value,
    }
)


@dataclass(frozen=True)
class StagedReplay:
    prefix_result: ScoredResult
    stage_id: str | None
    stage_patch: RecipePatch


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
    per_eval_timeout_seconds: float | None = None


@dataclass(frozen=True)
class StudyRecord:
    candidate_id: str
    patch: RecipePatch
    feasible: bool
    status: str
    objectives: Mapping[str, float | None]
    feasibility_margins: Mapping[str, Mapping[str, Any]]
    cache_key: str | None = None
    failure_category: str | None = None
    failing_gates: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    cache_hit: bool = False
    product_summary: Mapping[str, Any] = field(default_factory=dict)
    trace_summary: Mapping[str, Any] = field(default_factory=dict)

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
        object.__setattr__(
            self,
            "product_summary",
            MappingProxyType(dict(self.product_summary)),
        )
        object.__setattr__(
            self,
            "trace_summary",
            MappingProxyType(dict(self.trace_summary)),
        )


@dataclass(frozen=True)
class TwoPhaseConfig:
    enabled: bool = False
    top_k: int = DEFAULT_TWO_PHASE_TOP_K
    disagreement_threshold: float | None = None


@dataclass(frozen=True)
class StudyResult:
    out_dir: Path
    store_path: Path
    artifacts: Mapping[str, Path]
    records: tuple[StudyRecord, ...]
    leaderboard: tuple[StudyRecord, ...]
    pareto: tuple[StudyRecord, ...]
    winner: StudyRecord | None = None
    status: str = COMPLETED_STATUS
    reason: str = COMPLETED_STATUS
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
    topologies: Sequence[Any] | None = None,
    two_phase_certify: bool | Mapping[str, Any] | None = None,
    pinned_paths: Sequence[str] | None = None,
    per_eval_timeout_seconds: float | None = None,
) -> StudyResult:
    """Run one ask/evaluate/tell study and write Phase-O artifacts."""

    base_schema = schema or RecipeSchema()
    config = StudyConfig(
        profile=profile,
        feedstock=feedstock,
        strategy=strategy,
        fidelity=fidelity,
        parallel=parallel,
        budget=budget,
        out_dir=out_dir,
        seed=seed,
        per_eval_timeout_seconds=resolve_eval_timeout_seconds(per_eval_timeout_seconds),
    )
    try:
        resolved_profile = resolve_profile(
            config.profile,
            expected_feedstock=config.feedstock,
            schema=base_schema,
        )
    except ProfileValidationError as exc:
        if not _is_stale_profile_refusal(exc) or not isinstance(config.profile, Mapping):
            raise
        resolved_profile = dict(config.profile)
    active_schema = _schema_with_pinned_paths(
        base_schema,
        resolved_profile,
        cli_pinned_paths=pinned_paths,
    )
    search_space_identity = _search_space_identity(
        active_schema,
        profile_pinned_paths=_profile_pinned_paths(resolved_profile),
        cli_pinned_paths=_cli_pinned_paths(pinned_paths),
    )
    definitions = objective_definitions(resolved_profile)
    two_phase = _resolve_two_phase_config(resolved_profile, two_phase_certify)
    _validate_inputs(config, resolved_profile)
    try:
        active_constraints = (
            _constraints_for_profile(resolved_profile)
            if constraints is None
            else constraints
        )
    except ProfileValidationError as exc:
        if not _is_stale_profile_refusal(exc):
            raise
        active_constraints = None
    out = _resolve_out_dir(config.out_dir)
    _prepare_out_dir(out)
    store = result_store or ResultStore(out / "cache.sqlite")
    requested_topologies = _requested_staged_topologies(resolved_profile, topologies)
    if requested_topologies is None:
        active_strategy = resolve_strategy(
            config.strategy,
            profile=resolved_profile,
            seed=config.seed,
            schema=active_schema,
        )
        staged_strategies: tuple[StagedStrategy, ...] = ()
    else:
        if not (
            config.strategy == "staged"
            or isinstance(config.strategy, StagedStrategy)
        ):
            raise ValueError("topologies are only supported for staged strategy")
        single_topology_profile = _profile_without_staged_topologies(resolved_profile)
        staged_strategies = tuple(
            StagedStrategy(
                active_schema,
                seed=config.seed,
                objective_profile=single_topology_profile,
                topology=topology,
            )
            for topology in requested_topologies
        )
        active_strategy = staged_strategies[0]

    records: list[StudyRecord] = []
    provenance_path = out / "provenance.jsonl"
    evaluated = 0
    topology_cursor = 0
    prefix_replay_cache: dict[str, ScoredResult] = {}
    loop_profile = (
        _profile_for_cache_phase(
            resolved_profile,
            config.fidelity,
            cache_tier_ceiling=EXPLORE_CACHE_TIER_CEILING,
        )
        if two_phase.enabled
        else resolved_profile
    )
    with provenance_path.open("w", encoding="utf-8") as provenance:
        while evaluated < config.budget:
            batch_size = min(config.parallel, config.budget - evaluated)
            owners: dict[str, StagedStrategy] = {}
            if staged_strategies:
                candidates, topology_cursor, owners = _ask_staged_topology_candidates(
                    staged_strategies,
                    cursor=topology_cursor,
                    batch_size=batch_size,
                )
            else:
                candidates = active_strategy.ask(batch_size)
                if not candidates and isinstance(active_strategy, StagedStrategy):
                    if active_strategy.run_backward_pass() or active_strategy.joint_refine():
                        candidates = active_strategy.ask(batch_size)
            if not candidates:
                break
            results = _evaluate_candidates(
                candidates,
                profile=loop_profile,
                feedstock=config.feedstock,
                fidelity=config.fidelity,
                parallel=config.parallel,
                out_dir=out,
                evaluator=evaluator,
                schema=active_schema,
                constraints=active_constraints,
                store=store,
                definitions=definitions,
                prefix_replay_cache=prefix_replay_cache,
                per_eval_timeout_seconds=config.per_eval_timeout_seconds,
            )
            tell_batch: list[tuple[Candidate, ScoredResult]] = []
            for candidate, scored, cache_hit in results:
                _assert_honest_result(scored, definitions)
                light_scored = _strip_heavy_result(scored)
                if scored.eval_spec is not None:
                    try:
                        store.store(
                            scored.eval_spec,
                            light_scored,
                            created_at=datetime.now(UTC).isoformat(),
                        )
                    except ResultStoreWriteRejected as exc:
                        _LOGGER.warning(
                            "result_store_write_rejected candidate_id=%s cache_key=%s reasons=%s",
                            scored.candidate_id,
                            scored.cache_key,
                            ",".join(exc.reasons),
                        )
                record = _to_record(candidate, scored, cache_hit=cache_hit)
                records.append(record)
                provenance.write(
                    json.dumps(
                        _record_payload(
                            record,
                            active_schema,
                            resolved_profile,
                            search_space_identity=search_space_identity,
                        ),
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n"
                )
                tell_batch.append((candidate, light_scored))
            if staged_strategies:
                grouped: dict[StagedStrategy, list[tuple[Candidate, ScoredResult]]] = {}
                for candidate, scored in tell_batch:
                    grouped.setdefault(owners[candidate.id], []).append((candidate, scored))
                for owner, owner_batch in grouped.items():
                    owner.tell(owner_batch)
            else:
                active_strategy.tell(tell_batch)
            evaluated += len(candidates)

    failure_counts = _failure_counts(records)
    feasible = tuple(record for record in records if record.feasible)
    explore_leaderboard = tuple(sorted(feasible, key=lambda row: _rank_key(row, definitions)))
    explore_pareto = pareto_front(
        feasible,
        definitions,
        objective_getter=lambda row: row.objectives,
    )
    explore_pareto_ranked = tuple(
        sorted(explore_pareto, key=lambda row: _rank_key(row, definitions))
    )
    non_finite_count = failure_counts.get(FailureCategory.NON_FINITE_PAYLOAD.value, 0)
    if not records:
        _write_empty_artifacts(
            out,
            profile=resolved_profile,
            feedstock=feedstock,
            fidelity=fidelity,
            definitions=definitions,
            failure_counts=failure_counts,
        )
        raise StudyNoFeasibleError("no candidates were evaluated")
    if records and non_finite_count == len(records):
        _write_empty_artifacts(
            out,
            profile=resolved_profile,
            feedstock=feedstock,
            fidelity=fidelity,
            definitions=definitions,
            failure_counts=failure_counts,
        )
        raise StudyNoFeasibleError(
            "all candidates failed with non_finite_payload; "
            f"failure_counts={dict(failure_counts)}"
        )
    if not explore_pareto_ranked:
        if _no_feasible_config_failure(failure_counts, len(records)):
            _write_empty_artifacts(
                out,
                profile=resolved_profile,
                feedstock=feedstock,
                fidelity=fidelity,
                definitions=definitions,
                failure_counts=failure_counts,
            )
            raise StudyNoFeasibleError(
                "no feasible candidates due to config/runtime failure; "
                f"failure_counts={dict(failure_counts)}"
            )
        leaderboard = tuple(records)
        artifacts = _write_artifacts(
            out,
            profile=resolved_profile,
            feedstock=feedstock,
            fidelity=fidelity,
            definitions=definitions,
            pareto=(),
            leaderboard=leaderboard,
            winner=None,
            schema=active_schema,
            failure_counts=failure_counts,
            search_space_identity=search_space_identity,
        )
        artifacts["provenance"] = provenance_path
        artifacts["store"] = store.path
        return StudyResult(
            out_dir=out,
            store_path=store.path,
            artifacts=artifacts,
            records=tuple(records),
            leaderboard=leaderboard,
            pareto=(),
            winner=None,
            status=COMPLETED_NO_FEASIBLE_WINNER_STATUS,
            reason=COMPLETED_NO_FEASIBLE_WINNER_STATUS,
        )

    result_status = COMPLETED_STATUS
    result_reason = COMPLETED_STATUS
    certification_artifact: dict[str, Any] | None = None
    if two_phase.enabled:
        certify_profile = _profile_for_cache_phase(
            resolved_profile,
            config.fidelity,
            cache_tier_ceiling=CERTIFY_CACHE_TIER_CEILING,
            miss_policy="live-fill",
        )
        certification = _run_exact_certification(
            explore_leaderboard[: two_phase.top_k],
            records=records,
            profile=certify_profile,
            feedstock=config.feedstock,
            fidelity=config.fidelity,
            parallel=config.parallel,
            out_dir=out,
            evaluator=evaluator,
            schema=active_schema,
            constraints=active_constraints,
            store=store,
            definitions=definitions,
            config=config,
            active_strategy=active_strategy,
            staged_strategies=staged_strategies,
        )
        leaderboard = certification.leaderboard
        pareto_ranked = certification.pareto
        winner = certification.winner
        certification_artifact = certification.artifact
        result_status = certification.status
        result_reason = certification.reason
    else:
        leaderboard = explore_leaderboard
        pareto_ranked = explore_pareto_ranked
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
        failure_counts=failure_counts,
        search_space_identity=search_space_identity,
    )
    artifacts["provenance"] = provenance_path
    artifacts["store"] = store.path
    if certification_artifact is not None:
        certification_path = out / _TWO_PHASE_CERTIFICATION_NAME
        certification_path.write_text(
            json.dumps(certification_artifact, indent=2, sort_keys=True, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        artifacts["two_phase_certification"] = certification_path
    return StudyResult(
        out_dir=out,
        store_path=store.path,
        artifacts=artifacts,
        records=tuple(records),
        leaderboard=leaderboard,
        pareto=pareto_ranked,
        winner=winner,
        status=result_status,
        reason=result_reason,
    )


def run_certify(
    profile: str | Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    source_store: str | Path,
    certify_cache_key: str,
    out_dir: str | Path | None = None,
    *,
    evaluator: EvaluateFn = evaluate,
    schema: RecipeSchema | None = None,
    pinned_paths: Sequence[str] | None = None,
    per_eval_timeout_seconds: float | None = None,
) -> StudyResult:
    """Re-evaluate one stored optimizer result with exact live-fill certification."""

    base_schema = schema or RecipeSchema()
    config = StudyConfig(
        profile=profile,
        feedstock=feedstock,
        strategy="random",
        fidelity=fidelity,
        parallel=1,
        budget=1,
        out_dir=out_dir,
        seed=0,
        per_eval_timeout_seconds=resolve_eval_timeout_seconds(per_eval_timeout_seconds),
    )
    try:
        resolved_profile = resolve_profile(
            config.profile,
            expected_feedstock=config.feedstock,
            schema=base_schema,
        )
    except ProfileValidationError as exc:
        if not _is_stale_profile_refusal(exc) or not isinstance(config.profile, Mapping):
            raise
        resolved_profile = dict(config.profile)
    active_schema = _schema_with_pinned_paths(
        base_schema,
        resolved_profile,
        cli_pinned_paths=pinned_paths,
    )
    search_space_identity = _search_space_identity(
        active_schema,
        profile_pinned_paths=_profile_pinned_paths(resolved_profile),
        cli_pinned_paths=_cli_pinned_paths(pinned_paths),
    )
    definitions = objective_definitions(resolved_profile)
    _validate_inputs(config, resolved_profile)
    try:
        active_constraints = _constraints_for_profile(resolved_profile)
    except ProfileValidationError as exc:
        if not _is_stale_profile_refusal(exc):
            raise
        active_constraints = None
    out = _resolve_out_dir(config.out_dir)
    _prepare_out_dir(out)
    store = ResultStore(out / "cache.sqlite")
    source = ResultStore(Path(source_store))
    stored = source.fetch(certify_cache_key)
    if stored is None:
        raise StudyError(f"certify cache_key not found: {certify_cache_key!r}")
    if stored.eval_spec is None:
        raise StudyError(f"stored result {certify_cache_key!r} missing eval_spec")
    if stored.eval_spec.feedstock_id != config.feedstock:
        raise StudyError(
            "certify feedstock mismatch: "
            f"requested {config.feedstock!r}, stored {stored.eval_spec.feedstock_id!r}"
        )
    certify_profile = _profile_for_cache_phase(
        resolved_profile,
        config.fidelity,
        cache_tier_ceiling=CERTIFY_CACHE_TIER_CEILING,
        miss_policy="live-fill",
    )
    patch = RecipePatch.from_nested(dict(stored.eval_spec.runtime_campaign_overrides))
    candidate = Candidate(id=stored.candidate_id, patch=patch)
    scored = _evaluate_one_supervised(
        candidate,
        profile=certify_profile,
        feedstock=config.feedstock,
        fidelity=config.fidelity,
        out_dir=out,
        evaluator=evaluator,
        schema=active_schema,
        constraints=active_constraints,
        per_eval_timeout_seconds=config.per_eval_timeout_seconds,
    )
    _assert_honest_result(scored, definitions)
    if scored.eval_spec is None:
        category = (
            scored.failure_category.value
            if scored.failure_category is not None
            else "missing_eval_spec"
        )
        raise StudyNoFeasibleError(
            "certification candidate failed; "
            f"failure_counts={dict({category: 1})}"
        )
    record = _to_record(candidate, scored, cache_hit=False)
    # Grind-infra sweep Q2 (completeness): the certify store sink must degrade
    # like the main loop — an inadmissible-but-recordable row (e.g. feasible+OOD)
    # is rejected by the admission gate; skip the cache write and keep the
    # certified result flowing rather than aborting the certification.
    try:
        store.store(
            scored.eval_spec,
            _strip_heavy_result(scored),
            created_at=datetime.now(UTC).isoformat(),
        )
    except ResultStoreWriteRejected as exc:
        _LOGGER.warning(
            "result_store_write_rejected candidate_id=%s cache_key=%s reasons=%s",
            scored.candidate_id,
            scored.cache_key,
            ",".join(exc.reasons),
        )
    # Grind-infra sweep Finding 4: run_certify() previously labelled the
    # provenance artifact but never wrote it (unlike run() at the loop sink),
    # so certified/cached results pointed at a non-existent audit trail. Emit
    # the certification record in the same JSONL format run() uses.
    provenance_path = out / "provenance.jsonl"
    with provenance_path.open("w", encoding="utf-8") as provenance:
        provenance.write(
            json.dumps(
                _record_payload(
                    record,
                    active_schema,
                    resolved_profile,
                    search_space_identity=search_space_identity,
                ),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
    artifacts = _write_artifacts(
        out,
        profile=resolved_profile,
        feedstock=feedstock,
        fidelity=fidelity,
        definitions=definitions,
        pareto=(record,),
        leaderboard=(record,),
        winner=record,
        schema=active_schema,
        failure_counts=MappingProxyType({}),
        search_space_identity=search_space_identity,
    )
    artifacts["provenance"] = provenance_path
    artifacts["store"] = store.path
    artifacts["certify_source_store"] = str(Path(source_store))
    artifacts["certify_cache_key"] = certify_cache_key
    return StudyResult(
        out_dir=out,
        store_path=store.path,
        artifacts=artifacts,
        records=(record,),
        leaderboard=(record,),
        pareto=(record,),
        winner=record,
    )


@dataclass(frozen=True)
class _CertificationPassResult:
    leaderboard: tuple[StudyRecord, ...]
    pareto: tuple[StudyRecord, ...]
    winner: StudyRecord | None
    artifact: dict[str, Any]
    status: str = COMPLETED_STATUS
    reason: str = COMPLETED_STATUS


def _resolve_two_phase_config(
    profile: Mapping[str, Any],
    override: bool | Mapping[str, Any] | None,
) -> TwoPhaseConfig:
    if override is not None:
        if isinstance(override, bool):
            return TwoPhaseConfig(enabled=override)
        if isinstance(override, Mapping):
            return TwoPhaseConfig(
                enabled=bool(override.get("enabled", True)),
                top_k=int(override.get("top_k", DEFAULT_TWO_PHASE_TOP_K)),
                disagreement_threshold=(
                    float(override["disagreement_threshold"])
                    if override.get("disagreement_threshold") is not None
                    else None
                ),
            )
        raise TypeError("two_phase_certify must be a bool or mapping")
    block = profile.get("two_phase_certify")
    if block is None:
        return TwoPhaseConfig(enabled=False)
    if not isinstance(block, Mapping):
        raise StudyError("two_phase_certify must be a mapping when present in profile")
    return TwoPhaseConfig(
        enabled=bool(block.get("enabled", False)),
        top_k=int(block.get("top_k", DEFAULT_TWO_PHASE_TOP_K)),
        disagreement_threshold=(
            float(block["disagreement_threshold"])
            if block.get("disagreement_threshold") is not None
            else None
        ),
    )


def _profile_for_cache_phase(
    profile: Mapping[str, Any],
    fidelity: str,
    *,
    cache_tier_ceiling: str,
    miss_policy: str | None = None,
) -> dict[str, Any]:
    if cache_tier_ceiling not in CACHE_TIER_CEILINGS:
        raise StudyError(
            f"unsupported cache_tier_ceiling {cache_tier_ceiling!r}; "
            f"expected one of {', '.join(CACHE_TIER_CEILINGS)}"
        )
    result = copy.deepcopy(dict(profile))
    fidelities = dict(result.get("fidelities", {}) or {})
    fid_opts = dict(fidelities.get(fidelity, {}) or {})
    run = dict(result.get("run", {}) or {})
    backend_name = str(fid_opts.get("backend_name", run.get("backend_name", "stub")))
    fid_opts["cache_tier_ceiling"] = cache_tier_ceiling
    if miss_policy is not None:
        fid_opts["miss_policy"] = miss_policy
    if backend_name == "cached-real":
        cache = dict(fid_opts.get("reduced_real_cache") or run.get("reduced_real_cache") or {})
        cache["cache_tier_ceiling"] = cache_tier_ceiling
        if miss_policy is not None:
            cache["miss_policy"] = miss_policy
        if miss_policy == "live-fill":
            cache["strict_vapor_gate"] = True
        fid_opts["reduced_real_cache"] = cache
        if str(run.get("backend_name", "")) == "cached-real":
            run["reduced_real_cache"] = cache
    fidelities[fidelity] = fid_opts
    result["fidelities"] = fidelities
    result["run"] = run
    return result


def _resolved_strategy_sampler(strategy: Strategy | str) -> str:
    if isinstance(strategy, str):
        return active_sampler_name()
    sampler_name = getattr(strategy, "sampler_name", None)
    return str(sampler_name) if sampler_name is not None else active_sampler_name()


def _strategy_label(strategy: Strategy | str) -> str:
    if isinstance(strategy, str):
        return STRATEGY_CLASS_NAMES.get(strategy, strategy)
    return type(strategy).__name__


def _primary_objective_metric(definitions: Sequence[ObjectiveDefinition]) -> str:
    if not definitions:
        raise StudyError("objective definitions required for certification")
    return definitions[0].metric


def _objective_value(record: StudyRecord, metric: str) -> float | None:
    if metric not in record.objectives:
        raise StudyError(f"record {record.candidate_id!r} missing objective {metric!r}")
    if record.objectives[metric] is None:
        return None
    return float(record.objectives[metric])


def _cache_state_from_record(record: StudyRecord) -> str | None:
    trace = record.trace_summary
    if isinstance(trace, Mapping):
        state = trace.get("reduced_real_cache_state")
        if state is not None:
            return str(state)
    return None


def _cache_state_from_scored(scored: ScoredResult) -> str | None:
    reference = scored.run_reference
    if reference is not None and reference.cache_state is not None:
        return str(reference.cache_state)
    trace = reference.trace if reference is not None else None
    if isinstance(trace, Mapping):
        per_hour = trace.get("per_hour_summary")
        if isinstance(per_hour, Sequence) and per_hour:
            last = per_hour[-1]
            if isinstance(last, Mapping):
                state = last.get("reduced_real_cache_state")
                if state is not None:
                    return str(state)
    return None


def _is_certified_cache_state(cache_state: str | None) -> bool:
    return cache_state in CERTIFIED_CACHE_STATES


def _disagreement_aggregate(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"max": 0.0, "p95": 0.0}
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return {"max": ordered[-1], "p95": ordered[index]}


def _run_exact_certification(
    top_k_records: Sequence[StudyRecord],
    *,
    records: list[StudyRecord],
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    parallel: int,
    out_dir: Path,
    evaluator: EvaluateFn,
    schema: RecipeSchema,
    constraints: Any,
    store: ResultStore,
    definitions: Sequence[ObjectiveDefinition],
    config: "StudyConfig",
    active_strategy: Strategy,
    staged_strategies: tuple[StagedStrategy, ...],
) -> _CertificationPassResult:
    if not top_k_records:
        raise StudyNoFeasibleError("two-phase certification received no explore candidates")
    primary_metric = _primary_objective_metric(definitions)
    explore_by_id = {record.candidate_id: record for record in records}
    certification_rows: list[dict[str, Any]] = []
    certified_records: list[StudyRecord] = []
    replay_code_version: str | None = None
    replay_data_digests: Mapping[str, str] | None = None

    for explore_record in top_k_records:
        candidate = Candidate(id=explore_record.candidate_id, patch=explore_record.patch)
        results = _evaluate_candidates(
            [candidate],
            profile=profile,
            feedstock=feedstock,
            fidelity=fidelity,
            parallel=parallel,
            out_dir=out_dir,
            evaluator=evaluator,
            schema=schema,
            constraints=constraints,
            store=store,
            definitions=definitions,
            prefix_replay_cache={},
            skip_store_lookup=True,
            per_eval_timeout_seconds=config.per_eval_timeout_seconds,
        )
        _, scored, _ = results[0]
        _assert_honest_result(scored, definitions)
        certified = _to_record(candidate, scored, cache_hit=False)
        certified_records.append(certified)
        cache_state = _cache_state_from_scored(scored)
        explore_objective = _objective_value(explore_record, primary_metric)
        certified_objective = (
            _objective_value(certified, primary_metric) if certified.feasible else None
        )
        disagreement = (
            abs(explore_objective - certified_objective)
            if explore_objective is not None and certified_objective is not None
            else None
        )
        certification_rows.append(
            {
                "candidate_id": explore_record.candidate_id,
                "explore_objective": explore_objective,
                "certified_objective": certified_objective,
                "disagreement": disagreement,
                "explore_cache_state": _cache_state_from_record(explore_record),
                "certified_cache_state": cache_state,
            }
        )
        if scored.eval_spec is not None:
            replay_code_version = scored.eval_spec.code_version
            replay_data_digests = scored.eval_spec.data_digests
            # Grind-infra sweep Q2 (completeness): two-phase certification store
            # sink degrades like the main loop — skip the cache write for an
            # inadmissible row, keep certifying the rest (the record was already
            # appended for the leaderboard).
            try:
                store.store(
                    scored.eval_spec,
                    _strip_heavy_result(scored),
                    created_at=datetime.now(UTC).isoformat(),
                )
            except ResultStoreWriteRejected as exc:
                _LOGGER.warning(
                    "result_store_write_rejected candidate_id=%s cache_key=%s reasons=%s",
                    scored.candidate_id,
                    scored.cache_key,
                    ",".join(exc.reasons),
                )

    disagreements = [
        float(row["disagreement"])
        for row in certification_rows
        if row["disagreement"] is not None
    ]
    strategy_name = (
        config.strategy
        if isinstance(config.strategy, str)
        else type(active_strategy).__name__
    )
    artifact = {
        "candidates": certification_rows,
        "aggregate_disagreement": _disagreement_aggregate(disagreements),
        "replay_metadata": {
            "seed": config.seed,
            "strategy": _strategy_label(config.strategy),
            "strategy_name": strategy_name,
            "parallel": config.parallel,
            "sampler": _resolved_strategy_sampler(config.strategy),
            "objective_names": [definition.metric for definition in definitions],
            "code_version": replay_code_version,
            "data_digests": dict(replay_data_digests or {}),
        },
        "top_k": len(top_k_records),
        "disagreement_threshold": None,
    }
    certified_feasible = tuple(record for record in certified_records if record.feasible)
    if not certified_feasible:
        return _CertificationPassResult(
            leaderboard=tuple(certified_records),
            pareto=(),
            winner=None,
            artifact=artifact,
            status=COMPLETED_NO_FEASIBLE_WINNER_STATUS,
            reason=COMPLETED_NO_FEASIBLE_WINNER_STATUS,
        )
    leaderboard = tuple(sorted(certified_feasible, key=lambda row: _rank_key(row, definitions)))
    pareto = pareto_front(
        certified_feasible,
        definitions,
        objective_getter=lambda row: row.objectives,
    )
    pareto_ranked = tuple(sorted(pareto, key=lambda row: _rank_key(row, definitions)))
    winner = pareto_ranked[0]
    return _CertificationPassResult(
        leaderboard=leaderboard,
        pareto=pareto_ranked,
        winner=winner,
        artifact=artifact,
    )


def _schema_with_pinned_paths(
    schema: RecipeSchema,
    profile: Mapping[str, Any],
    *,
    cli_pinned_paths: Sequence[str] | None,
) -> RecipeSchema:
    pins = _profile_pinned_paths(profile) + _cli_pinned_paths(cli_pinned_paths)
    return schema.with_pinned_paths(pins)


def _profile_pinned_paths(profile: Mapping[str, Any]) -> tuple[str, ...]:
    raw = profile.get("pinned_paths", ())
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise ProfileValidationError(
            "profile.pinned_paths must be a list of dotted paths"
        )
    pins: list[str] = []
    for index, path in enumerate(raw):
        if not isinstance(path, str):
            raise ProfileValidationError(
                f"profile.pinned_paths[{index}] must be a dotted path string"
            )
        pins.append(path)
    return tuple(pins)


def _cli_pinned_paths(raw: Sequence[str] | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise ValueError("--pin must be provided as dotted path strings")
    pins: list[str] = []
    for index, path in enumerate(raw):
        if not isinstance(path, str):
            raise ValueError(f"--pin entry {index} must be a dotted path string")
        pins.append(path)
    return tuple(pins)


def _search_space_identity(
    schema: RecipeSchema,
    *,
    profile_pinned_paths: Sequence[str],
    cli_pinned_paths: Sequence[str],
) -> Mapping[str, Any]:
    return MappingProxyType(
        {
            "recipe_schema_version": schema.recipe_schema_version,
            "allowlist_version": schema.allowlist_version,
            "profile_pinned_paths": list(profile_pinned_paths),
            "cli_pinned_paths": list(cli_pinned_paths),
            "resolved_pinned_paths": [
                ".".join(path) for path in getattr(schema, "pinned_paths", ())
            ],
            "search_knob_paths": [".".join(spec.path) for spec in schema.search_allowlist],
        }
    )


def resolve_profile(
    profile: str | Mapping[str, Any],
    *,
    expected_feedstock: str | None = None,
    schema: RecipeSchema | None = None,
) -> Mapping[str, Any]:
    if isinstance(profile, str):
        try:
            resolved = dict(DEFAULT_PROFILES[profile])
        except KeyError as exc:
            raise ValueError(f"unknown profile {profile!r}") from exc
        if expected_feedstock is not None:
            resolved["feedstock"] = expected_feedstock
        return validate_profile(
            resolved,
            expected_feedstock=expected_feedstock,
            source=f"<profile:{profile}>",
            schema=schema,
        )
    if not isinstance(profile, Mapping):
        raise TypeError("profile must be a profile name or mapping")
    return validate_profile(
        profile,
        expected_feedstock=expected_feedstock,
        source=getattr(profile, "source", "<profile>"),
        schema=schema,
    )


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
        return StagedStrategy(schema, seed=seed, objective_profile=profile)
    if strategy == "bayes":
        from simulator.optimize.strategy import OptunaTPEStrategy

        return OptunaTPEStrategy(schema, seed=seed, objective_profile=profile)
    if strategy == "nsga2":
        from simulator.optimize.strategy import OptunaNSGA2Strategy

        return OptunaNSGA2Strategy(schema, seed=seed, objective_profile=profile)
    raise ValueError(f"unknown strategy {strategy!r}")


def _requested_staged_topologies(
    profile: Mapping[str, Any],
    topologies: Sequence[Any] | None,
) -> tuple[TopologyChoice, ...] | None:
    if topologies is not None:
        return enumerate_topologies(topologies)
    for key in ("staged", "staged_strategy"):
        options = profile.get(key)
        if isinstance(options, Mapping) and "topologies" in options:
            return enumerate_topologies(options["topologies"])
    return None


def _profile_without_staged_topologies(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    copy: dict[str, Any] = dict(profile)
    for key in ("staged", "staged_strategy"):
        options = profile.get(key)
        if isinstance(options, Mapping):
            cleaned = dict(options)
            cleaned.pop("topologies", None)
            copy[key] = cleaned
    return MappingProxyType(copy)


def _ask_staged_topology_candidates(
    strategies: Sequence[StagedStrategy],
    *,
    cursor: int,
    batch_size: int,
) -> tuple[tuple[Candidate, ...], int, dict[str, StagedStrategy]]:
    if not strategies:
        return (), cursor, {}
    candidates: list[Candidate] = []
    owners: dict[str, StagedStrategy] = {}
    misses = 0
    next_cursor = cursor % len(strategies)
    while len(candidates) < batch_size and misses < len(strategies):
        strategy = strategies[next_cursor]
        batch = strategy.ask(1)
        if not batch and (strategy.run_backward_pass() or strategy.joint_refine()):
            batch = strategy.ask(1)
        next_cursor = (next_cursor + 1) % len(strategies)
        if not batch:
            misses += 1
            continue
        candidate = batch[0]
        candidates.append(candidate)
        owners[candidate.id] = strategy
        misses = 0
    return tuple(candidates), next_cursor, owners


def _constraints_for_profile(profile: Mapping[str, Any]) -> Any:
    selector = profile.get("study_constraints")
    if selector is None:
        return physics_constraints_from_profile(profile)
    if selector == "physics":
        return physics_constraints_from_profile(profile)
    if selector == "stub_smoke":
        raise ValueError(
            "study_constraints 'stub_smoke' is retired; use physics constraints; "
            "regenerate with FORCE_PROFILES=1"
        )
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
    definitions: Sequence[ObjectiveDefinition],
    prefix_replay_cache: dict[str, ScoredResult],
    skip_store_lookup: bool = False,
    per_eval_timeout_seconds: float | None = None,
) -> tuple[tuple[Candidate, ScoredResult, bool], ...]:
    results: list[tuple[Candidate, ScoredResult, bool] | None] = [None] * len(candidates)
    misses: list[tuple[int, Candidate]] = []
    staged_prefixes: dict[str, ScoredResult] = {}
    for index, candidate in enumerate(candidates):
        cached = None
        if not skip_store_lookup:
            cached = _lookup_cached(
                candidate,
                profile,
                feedstock,
                fidelity,
                schema,
                store,
                constraints,
            )
        if cached is None:
            prefix = _ensure_staged_prefix_replay(
                candidate,
                profile=profile,
                feedstock=feedstock,
                fidelity=fidelity,
                out_dir=out_dir,
                evaluator=evaluator,
                schema=schema,
                constraints=constraints,
                store=store,
                definitions=definitions,
                prefix_replay_cache=prefix_replay_cache,
                per_eval_timeout_seconds=per_eval_timeout_seconds,
            )
            if prefix is not None:
                if prefix.eval_spec is None:
                    results[index] = (
                        candidate,
                        replace(prefix, candidate_id=candidate.id),
                        False,
                    )
                    continue
                staged_prefixes[candidate.id] = prefix
            misses.append((index, candidate))
        else:
            cached = reground_scored_result(cached)
            results[index] = (candidate, cached, True)

    if misses:
        requests: list[PoolEvaluationRequest] = []
        for _, candidate in misses:
            call_patch = candidate.patch
            evaluator_kwargs: dict[str, Any] = {}
            staged_prefix = staged_prefixes.get(candidate.id)
            if staged_prefix is not None:
                stage_patch = _stage_patch_from_metadata(candidate, schema)
                if stage_patch is None:
                    raise StagedReplayViolation(
                        f"staged candidate {candidate.id!r} missing stage patch for replay"
                    )
                if not _evaluator_accepts_staged_replay(evaluator):
                    raise StagedReplayViolation(
                        f"evaluator {evaluator!r} does not support explicit staged replay"
                    )
                evaluator_kwargs["staged_replay"] = StagedReplay(
                    prefix_result=staged_prefix,
                    stage_id=(
                        candidate.metadata.get("stage_id")
                        if isinstance(candidate.metadata.get("stage_id"), str)
                        else None
                    ),
                    stage_patch=stage_patch,
                )
                call_patch = stage_patch
            requests.append(
                PoolEvaluationRequest(
                    call_patch,
                    feedstock,
                    fidelity,
                    profile=profile,
                    candidate_id=candidate.id,
                    output_dir=out_dir / "worker-output" / candidate.id,
                    evaluator_kwargs=evaluator_kwargs,
                )
            )
        batch = evaluate_batch(
            requests,
            profile=profile,
            max_workers=parallel,
            output_root=out_dir / "worker-output",
            evaluate_fn=evaluator,
            schema=schema,
            constraints=constraints,
            per_eval_timeout_seconds=per_eval_timeout_seconds,
        )
        for (index, candidate), scored in zip(misses, batch):
            scored = _with_candidate_id(scored, candidate.id)
            staged_prefix = staged_prefixes.get(candidate.id)
            if staged_prefix is not None and scored.eval_spec is not None:
                try:
                    spec, _ = _build_eval_inputs(
                        candidate.patch.validated(schema),
                        feedstock,
                        fidelity,
                        profile,
                        schema,
                        constraints=constraints,
                    )
                except ProfileValidationError as exc:
                    if _is_stale_profile_refusal(exc):
                        scored = _stale_profile_result(candidate.id, str(exc))
                    else:
                        raise
                else:
                    scored = replace(scored, eval_spec=spec, cache_key=cache_key(spec))
            results[index] = (candidate, scored, False)

    completed = tuple(result for result in results if result is not None)
    if len(completed) != len(candidates):
        raise RuntimeError("study evaluation ended without all candidate results")
    return completed


def _ensure_staged_prefix_replay(
    candidate: Candidate,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    out_dir: Path,
    evaluator: EvaluateFn,
    schema: RecipeSchema,
    constraints: Any,
    store: ResultStore,
    definitions: Sequence[ObjectiveDefinition],
    prefix_replay_cache: dict[str, ScoredResult],
    per_eval_timeout_seconds: float | None,
) -> ScoredResult | None:
    if not _is_staged_candidate(candidate):
        return None
    prefix_depth = candidate.metadata.get("prefix_depth", 0)
    if not isinstance(prefix_depth, int):
        raise StagedBeamStateError("staged prefix_depth metadata must be an int")
    if prefix_depth <= 0:
        return None

    prefix_patch = _prefix_patch_from_metadata(candidate, schema)
    base_spec, _ = _build_eval_inputs(
        prefix_patch,
        feedstock,
        fidelity,
        profile,
        schema,
        constraints=constraints,
    )
    prefix_spec = make_prefix_eval_spec(
        base_spec,
        prefix_stage_ids=_string_tuple_metadata(candidate, "prefix_stage_ids"),
        prefix_recipe_ids=_string_tuple_metadata(candidate, "prefix_recipe_ids"),
        topology_id=_topology_id_metadata(candidate),
    )
    if not isinstance(prefix_spec, PrefixEvalSpec):
        raise StagedBeamStateError("staged prefix spec was not a PrefixEvalSpec")
    prefix_key = cache_key(prefix_spec)
    if prefix_key in prefix_replay_cache:
        cached = store.lookup(prefix_spec)
        if cached is None:
            raise StagedBeamStateError(f"verified staged prefix vanished: {prefix_key}")
        return cached

    cached = store.lookup(prefix_spec)
    if cached is not None:
        prefix_replay_cache[prefix_key] = cached
        return cached

    fresh = _evaluate_prefix_one(
        candidate,
        prefix_patch,
        prefix_spec,
        prefix_key,
        profile=profile,
        feedstock=feedstock,
        fidelity=fidelity,
        out_dir=out_dir,
        evaluator=evaluator,
        schema=schema,
        constraints=constraints,
        per_eval_timeout_seconds=per_eval_timeout_seconds,
    )
    if fresh.eval_spec is None:
        return fresh
    _assert_honest_result(fresh, definitions)
    light_fresh = _strip_heavy_result(fresh)
    # Grind-infra sweep Q2 (completeness): unlike the main/certify sinks, the
    # staged prefix REQUIRES the row to be cached (it is read back immediately
    # for replay-equality), so an admission rejection cannot be silently skipped
    # — surface it loudly with the reason instead of a bare ResultStoreWriteRejected.
    try:
        store.store(
            prefix_spec,
            light_fresh,
            created_at=datetime.now(UTC).isoformat(),
        )
    except ResultStoreWriteRejected as exc:
        raise StagedBeamStateError(
            f"staged prefix cache write rejected: {prefix_key} "
            f"reasons={','.join(exc.reasons)}"
        ) from exc
    cached = store.lookup(prefix_spec)
    if cached is None:
        raise StagedBeamStateError(f"staged prefix cache write failed: {prefix_key}")
    assert_prefix_replay_equal(cached, light_fresh)
    prefix_replay_cache[prefix_key] = cached
    return cached


def _evaluate_prefix_one(
    candidate: Candidate,
    prefix_patch: RecipePatch,
    prefix_spec: PrefixEvalSpec,
    prefix_key: str,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    out_dir: Path,
    evaluator: EvaluateFn,
    schema: RecipeSchema,
    constraints: Any,
    per_eval_timeout_seconds: float | None,
) -> ScoredResult:
    prefix_id = f"staged-prefix-{prefix_key[:16]}"
    scored = evaluate_request_supervised(
        PoolEvaluationRequest(
            prefix_patch,
            feedstock,
            fidelity,
            profile=profile,
            candidate_id=prefix_id,
            output_dir=out_dir / "evals" / candidate.id / "prefix",
        ),
        profile=profile,
        output_root=out_dir / "evals" / candidate.id / "prefix",
        evaluate_fn=evaluator,
        schema=schema,
        constraints=constraints,
        per_eval_timeout_seconds=per_eval_timeout_seconds,
    )
    if not isinstance(scored, ScoredResult):
        raise TypeError("evaluator must return ScoredResult")
    scored = _with_candidate_id(scored, prefix_id)
    if scored.eval_spec is None:
        return scored
    return reground_scored_result(
        replace(scored, eval_spec=prefix_spec, cache_key=prefix_key)
    )


def _prefix_patch_from_metadata(candidate: Candidate, schema: RecipeSchema) -> RecipePatch:
    raw = candidate.metadata.get("prefix_patch_values")
    if not isinstance(raw, Mapping):
        raise StagedBeamStateError("staged candidate missing prefix_patch_values metadata")
    values: dict[tuple[str, ...], Any] = {}
    for raw_path, value in raw.items():
        if isinstance(raw_path, tuple):
            path = raw_path
        elif isinstance(raw_path, list):
            path = tuple(raw_path)
        elif isinstance(raw_path, str):
            path = tuple(raw_path.split("."))
        else:
            raise StagedBeamStateError("staged prefix patch path must be tuple/list/str")
        values[path] = value
    return RecipePatch(values).validated(schema)


def _stage_patch_from_metadata(candidate: Candidate, schema: RecipeSchema) -> RecipePatch | None:
    raw = candidate.metadata.get("stage_patch_values")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise StagedBeamStateError("staged candidate stage_patch_values metadata must be a mapping")
    values: dict[tuple[str, ...], Any] = {}
    for raw_path, value in raw.items():
        if isinstance(raw_path, tuple):
            path = raw_path
        elif isinstance(raw_path, list):
            path = tuple(raw_path)
        elif isinstance(raw_path, str):
            path = tuple(raw_path.split("."))
        else:
            raise StagedBeamStateError("staged stage patch path must be tuple/list/str")
        values[path] = value
    return RecipePatch(values).validated(schema)


def _string_tuple_metadata(candidate: Candidate, key: str) -> tuple[str, ...]:
    raw = candidate.metadata.get(key, ())
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise StagedBeamStateError(f"staged {key} metadata must be a sequence")
    values = tuple(raw)
    if not all(isinstance(value, str) for value in values):
        raise StagedBeamStateError(f"staged {key} metadata must contain strings")
    return values


def _topology_id_metadata(candidate: Candidate) -> str:
    topology = candidate.metadata.get("topology")
    if isinstance(topology, Mapping):
        topology_id = topology.get("id")
    else:
        topology_id = topology
    if not isinstance(topology_id, str) or not topology_id:
        raise StagedBeamStateError("staged topology metadata must include id")
    return topology_id


def _is_staged_candidate(candidate: Candidate) -> bool:
    return candidate.metadata.get("strategy") == "staged"


def _lookup_cached(
    candidate: Candidate,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    schema: RecipeSchema,
    store: ResultStore,
    constraints: Any,
    *,
    require_certified_cache_state: bool = False,
) -> ScoredResult | None:
    try:
        validated = candidate.patch.validated(schema)
        spec, _ = _build_eval_inputs(
            validated,
            feedstock,
            fidelity,
            profile,
            schema,
            constraints=constraints,
        )
    except RecipeValidationError:
        return None
    except ProfileValidationError as exc:
        if not _is_stale_profile_refusal(exc):
            raise
        return None
    cached = store.lookup(spec)
    if cached is None:
        return None
    if require_certified_cache_state and not _is_certified_cache_state(
        _cache_state_from_scored(cached)
    ):
        return None
    return replace(cached, candidate_id=candidate.id)


def _evaluate_one_supervised(
    candidate: Candidate,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    out_dir: Path,
    evaluator: EvaluateFn,
    schema: RecipeSchema,
    constraints: Any,
    staged_prefix: ScoredResult | None = None,
    per_eval_timeout_seconds: float | None,
) -> ScoredResult:
    stage_patch = _stage_patch_from_metadata(candidate, schema)
    call_patch = candidate.patch
    evaluator_kwargs: dict[str, Any] = {}
    if staged_prefix is not None:
        if stage_patch is None:
            raise StagedReplayViolation(
                f"staged candidate {candidate.id!r} missing stage patch for replay"
            )
        if not _evaluator_accepts_staged_replay(evaluator):
            raise StagedReplayViolation(
                f"evaluator {evaluator!r} does not support explicit staged replay"
            )
        evaluator_kwargs["staged_replay"] = StagedReplay(
            prefix_result=staged_prefix,
            stage_id=(
                candidate.metadata.get("stage_id")
                if isinstance(candidate.metadata.get("stage_id"), str)
                else None
            ),
            stage_patch=stage_patch,
        )
        call_patch = stage_patch

    scored = evaluate_request_supervised(
        PoolEvaluationRequest(
            call_patch,
            feedstock,
            fidelity,
            profile=profile,
            candidate_id=candidate.id,
            output_dir=out_dir / "evals" / candidate.id,
            evaluator_kwargs=evaluator_kwargs,
        ),
        profile=profile,
        output_root=out_dir / "evals" / candidate.id,
        evaluate_fn=evaluator,
        schema=schema,
        constraints=constraints,
        per_eval_timeout_seconds=per_eval_timeout_seconds,
    )
    if not isinstance(scored, ScoredResult):
        raise TypeError("evaluator must return ScoredResult")
    scored = _with_candidate_id(scored, candidate.id)
    if staged_prefix is not None and scored.eval_spec is not None:
        try:
            spec, _ = _build_eval_inputs(
                candidate.patch.validated(schema),
                feedstock,
                fidelity,
                profile,
                schema,
                constraints=constraints,
            )
        except ProfileValidationError as exc:
            if _is_stale_profile_refusal(exc):
                return _stale_profile_result(candidate.id, str(exc))
            raise
        scored = replace(scored, eval_spec=spec, cache_key=cache_key(spec))
    return scored


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
    staged_prefix: ScoredResult | None = None,
) -> ScoredResult:
    stage_patch = _stage_patch_from_metadata(candidate, schema)
    call_patch = candidate.patch
    staged_replay: StagedReplay | None = None
    if staged_prefix is not None:
        if stage_patch is None:
            raise StagedReplayViolation(
                f"staged candidate {candidate.id!r} missing stage patch for replay"
            )
        if not _evaluator_accepts_staged_replay(evaluator):
            raise StagedReplayViolation(
                f"evaluator {evaluator!r} does not support explicit staged replay"
            )
        staged_replay = StagedReplay(
            prefix_result=staged_prefix,
            stage_id=(
                candidate.metadata.get("stage_id")
                if isinstance(candidate.metadata.get("stage_id"), str)
                else None
            ),
            stage_patch=stage_patch,
        )
        call_patch = stage_patch
    try:
        scored = _call_evaluator(
            evaluator,
            call_patch,
            feedstock,
            fidelity,
            profile=profile,
            candidate_id=candidate.id,
            schema=schema,
            constraints=constraints,
            output_dir=out_dir / "evals" / candidate.id,
            staged_replay=staged_replay,
        )
    except ProfileValidationError as exc:
        if _is_stale_profile_refusal(exc):
            return _stale_profile_result(candidate.id, str(exc))
        raise
    except EvaluationAbort:
        raise
    if not isinstance(scored, ScoredResult):
        raise TypeError("evaluator must return ScoredResult")
    scored = _with_candidate_id(scored, candidate.id)
    if staged_prefix is not None:
        try:
            spec, _ = _build_eval_inputs(
                candidate.patch.validated(schema),
                feedstock,
                fidelity,
                profile,
                schema,
                constraints=constraints,
            )
        except ProfileValidationError as exc:
            if _is_stale_profile_refusal(exc):
                return _stale_profile_result(candidate.id, str(exc))
            raise
        scored = replace(scored, eval_spec=spec, cache_key=cache_key(spec))
    return scored


def _evaluator_accepts_staged_replay(evaluator: EvaluateFn) -> bool:
    signature = inspect.signature(evaluator)
    return "staged_replay" in signature.parameters


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
    accepted = {}
    for key, value in kwargs.items():
        if key == "staged_replay":
            if key in signature.parameters:
                accepted[key] = value
            continue
        if accepts_kwargs or key in signature.parameters:
            accepted[key] = value
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
    if scored.failure_category is FailureCategory.STALE_PROFILE:
        if scored.feasible:
            raise StudyAbort("stale_profile result cannot be feasible")
        if not scored.notes:
            raise StudyAbort("stale_profile result missing refusal message")
        return
    if scored.failure_category is FailureCategory.TIMEOUT:
        if scored.feasible:
            raise StudyAbort("timeout result cannot be feasible")
        if not scored.notes:
            raise StudyAbort("timeout result missing reason-coded note")
        return
    _assert_result_artifact_floor(scored)
    if scored.failure_category in {
        FailureCategory.ENGINE_BUG,
        FailureCategory.BACKEND_UNAVAILABLE,
    }:
        raise StudyAbort(f"aborting study on {scored.failure_category.value} result")
    if not scored.feasible:
        if scored.failure_category is None:
            raise StudyAbort("infeasible result missing failure_category")
        return
    if scored.objectives is None:
        raise StudyAbort("feasible result missing objective vector")
    try:
        objective_scores(scored.objectives, definitions)
    except ObjectiveComputationError as exc:
        raise StudyAbort(str(exc)) from exc


def _assert_result_artifact_floor(scored: ScoredResult) -> None:
    if scored.eval_spec is None or scored.cache_key is None:
        raise StudyAbort("result artifact missing eval_spec/cache_key")
    if scored.cache_key != cache_key(scored.eval_spec):
        raise StudyAbort("result artifact cache_key does not match eval_spec")
    if not scored.feasibility_margins:
        raise StudyAbort("result artifact missing feasibility_margins")
    _assert_finite_margins(scored)
    if _result_backend_status(scored) is None:
        raise StudyAbort("result artifact missing backend_status")


def _assert_finite_margins(scored: ScoredResult) -> None:
    for name, margin in scored.feasibility_margins.items():
        prefix = f"feasibility margin {name!r}"
        _finite_or_infinite(getattr(margin, "margin", None), f"{prefix}.margin")
        _finite_or_infinite(getattr(margin, "observed", None), f"{prefix}.observed")


def _result_backend_status(scored: ScoredResult) -> str | None:
    reference = getattr(scored, "run_reference", None)
    if reference is None:
        return None
    status = getattr(reference, "backend_status", None)
    if status is None:
        status = _backend_status_from_trace(getattr(reference, "trace", None))
    if status is None and getattr(getattr(scored, "eval_spec", None), "backend_name", None) == "stub":
        return "diagnostic_stub"
    return str(status) if status is not None else None


def _strip_heavy_result(scored: ScoredResult) -> ScoredResult:
    reference = scored.run_reference
    if reference is None:
        return scored
    light_reference = RunReference(
        status=reference.status,
        error_message=reference.error_message,
        reason=reference.reason,
        trace=_light_backend_status_trace(scored),
        product_summary=reference.product_summary,
        backend_status=_result_backend_status(scored),
        backend_authoritative=reference.backend_authoritative,
    )
    return replace(scored, run_reference=light_reference)


def _light_backend_status_trace(scored: ScoredResult) -> Mapping[str, Any] | None:
    reference = scored.run_reference
    status = _result_backend_status(scored)
    trace = getattr(reference, "trace", None) if reference is not None else None
    payload: dict[str, Any] = {}
    if status is not None:
        payload["backend_status"] = status
    if reference is not None and reference.backend_authoritative is not None:
        payload["backend_authoritative"] = reference.backend_authoritative
    if isinstance(trace, Mapping):
        for key in (
            "backend_diagnostics",
            "out_of_domain_crash_point",
            "rump_terminal",
            "terminal_rump_by_species_kg",
            "composition_target",
            "vapor_pressure_source_report",
            "vapor_pressure_provider_id",
            "vapor_pressure_fallback_provider_id",
            "allow_fallback_vapor",
            "force_builtin_vapor_pressure",
            "kernel_fallback_used",
            "knob_saturation",
            "interpolation_feasibility_verdict",
        ):
            if key in trace:
                payload[key] = _jsonable_value(trace[key])
        _project_interpolation_ranked_drain_summary(trace, payload)
    return MappingProxyType(payload) if payload else None


def _backend_status_from_trace(trace: Any) -> str | None:
    if isinstance(trace, Mapping):
        raw = trace.get("backend_status")
        if raw is not None:
            return str(raw)
        for key in ("per_hour", "hours"):
            status = _latest_backend_status(trace.get(key))
            if status is not None:
                return status
        return None
    raw = getattr(trace, "backend_status", None)
    if raw is not None:
        return str(raw)
    for attr in ("per_hour", "hours"):
        status = _latest_backend_status(getattr(trace, attr, None))
        if status is not None:
            return status
    return None


def _latest_backend_status(value: Any) -> str | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        return None
    return _backend_status_from_trace(value[-1])


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
        product_summary=_product_summary_mapping(scored.run_reference),
        trace_summary=_trace_summary_mapping(scored),
    )


def _objective_mapping(objectives: ObjectiveVector | None) -> Mapping[str, float | None]:
    if objectives is None:
        return MappingProxyType({})
    mapping = objectives.as_mapping()
    return MappingProxyType({
        str(key): None if value is None else _finite(value, str(key))
        for key, value in mapping.items()
    })


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
    if str(getattr(margin, "status", "available")) != "available":
        payload["status"] = str(getattr(margin, "status"))
    if not bool(getattr(margin, "authoritative", True)):
        payload["authoritative"] = False
    if str(getattr(margin, "output_status", "authoritative")) != "authoritative":
        payload["output_status"] = str(getattr(margin, "output_status"))
    if str(getattr(margin, "status_reason", "")):
        payload["status_reason"] = str(getattr(margin, "status_reason"))
    status_payload = getattr(margin, "status_payload", {})
    if isinstance(status_payload, Mapping) and status_payload:
        payload["status_payload"] = _jsonable_value(status_payload)
    return MappingProxyType(payload)


def _product_summary_mapping(reference: RunReference | None) -> Mapping[str, Any]:
    if reference is None:
        return MappingProxyType({})
    summary = dict(reference.product_summary)
    if _tap_truncated_composition_payload(reference):
        _apply_tap_coating_product_summary(
            summary,
            _tap_truncated_product_summary(reference),
        )
    summary = coating_summary_with_grounded_authority(summary)
    return MappingProxyType(summary)


def _tap_truncated_product_summary(reference: RunReference) -> Mapping[str, Any]:
    payload = _tap_truncated_composition_payload(reference)
    if not payload:
        return MappingProxyType({})
    summary = payload.get("tap_coating_product_summary")
    return summary if isinstance(summary, Mapping) else MappingProxyType({})


def _tap_truncated_composition_payload(reference: RunReference) -> Mapping[str, Any]:
    trace = reference.trace
    if not isinstance(trace, Mapping):
        return MappingProxyType({})
    payload = trace.get("composition_target")
    if not isinstance(payload, Mapping) or not _is_tap_truncated(payload):
        return MappingProxyType({})
    return payload


def _apply_tap_coating_product_summary(
    summary: dict[str, Any],
    tap_summary: Mapping[str, Any],
) -> None:
    terminal_fields = sorted(
        key for key in _TAP_COATING_PRODUCT_SUMMARY_FIELDS if key in summary
    )
    missing = [key for key in terminal_fields if key not in tap_summary]
    if missing:
        raise StudyAbort(
            "tap-truncated coating projection missing hour-basis field(s): "
            + ", ".join(missing)
        )
    for key in _TAP_COATING_PRODUCT_SUMMARY_FIELDS:
        if key in tap_summary:
            summary[key] = tap_summary[key]


def _trace_summary_mapping(scored: ScoredResult | RunReference | None) -> Mapping[str, Any]:
    if not isinstance(scored, ScoredResult):
        return _run_reference_trace_summary(scored)
    payload: dict[str, Any] = dict(_run_reference_trace_summary(scored.run_reference))
    payload.update(_objective_evidence_trace_summary(scored.objectives))
    return MappingProxyType(payload)


def _run_reference_trace_summary(reference: RunReference | None) -> Mapping[str, Any]:
    if reference is None or not isinstance(reference.trace, Mapping):
        return MappingProxyType({})
    return _light_backend_status_trace_for_reference(reference)


def _light_backend_status_trace_for_reference(
    reference: RunReference,
) -> Mapping[str, Any]:
    payload: dict[str, Any] = {}
    status = getattr(reference, "backend_status", None)
    if status is None:
        status = _backend_status_from_trace(reference.trace)
    if status is not None:
        payload["backend_status"] = str(status)
    if reference.backend_authoritative is not None:
        payload["backend_authoritative"] = reference.backend_authoritative
    if reference.cache_state is not None:
        payload["reduced_real_cache_state"] = str(reference.cache_state)
    elif isinstance(reference.trace, Mapping):
        per_hour = reference.trace.get("per_hour_summary")
        if isinstance(per_hour, Sequence) and per_hour:
            last = per_hour[-1]
            if isinstance(last, Mapping) and last.get("reduced_real_cache_state") is not None:
                payload["reduced_real_cache_state"] = str(last["reduced_real_cache_state"])
    if isinstance(reference.trace, Mapping):
        for key in (
            "backend_diagnostics",
            "out_of_domain_crash_point",
            "rump_terminal",
            "terminal_rump_by_species_kg",
            "composition_target",
            "vapor_pressure_source_report",
            "vapor_pressure_provider_id",
            "vapor_pressure_fallback_provider_id",
            "allow_fallback_vapor",
            "force_builtin_vapor_pressure",
            "kernel_fallback_used",
            "knob_saturation",
            "interpolation_feasibility_verdict",
        ):
            if key in reference.trace:
                payload[key] = _jsonable_value(reference.trace[key])
        _project_interpolation_ranked_drain_summary(reference.trace, payload)
    return MappingProxyType(payload)


def _objective_evidence_trace_summary(objectives: ObjectiveVector | None) -> Mapping[str, Any]:
    if objectives is None:
        return MappingProxyType({})
    objective_values = objectives.as_mapping()
    if _SSO2_OBJECTIVE_METRIC not in objective_values:
        return MappingProxyType({})
    evidence = objectives.evidence.get(_SSO2_OBJECTIVE_METRIC)
    if not isinstance(evidence, Mapping):
        return MappingProxyType({
            _SSO2_OBJECTIVE_TRACE_KEY: {
                "reader": _SSO2_OBJECTIVE_METRIC,
                "status": "evidence_absent",
                "status_reason": "stored objective row has no SSO-2 evidence payload",
                "consumed_fields": [],
            }
        })
    return MappingProxyType({_SSO2_OBJECTIVE_TRACE_KEY: _sso2_evidence_projection(evidence)})


def _sso2_evidence_projection(reader: Mapping[str, Any]) -> Mapping[str, Any]:
    payload: dict[str, Any] = {
        "reader": str(reader.get("reader", _SSO2_OBJECTIVE_METRIC)),
        "status": str(reader.get("status", "")),
        "status_reason": str(reader.get("status_reason", "")),
        "consumed_fields": _jsonable_value(reader.get("consumed_fields", [])),
    }
    if "score" in reader:
        payload["score"] = _jsonable_value(reader["score"])
    score_components = reader.get("score_components")
    if isinstance(score_components, Mapping):
        payload["score_components"] = _jsonable_value(score_components)
    evidence = reader.get("evidence")
    if isinstance(evidence, Mapping):
        if "status" in evidence:
            payload["evidence_status"] = _jsonable_value(evidence["status"])
        if "status_reason" in evidence:
            payload["evidence_status_reason"] = _jsonable_value(
                evidence["status_reason"]
            )
        for key in (
            "certified_sso_r_surface",
            "delivered_stream_purity",
            "stage_3",
            "fe_tap",
            "wall_coating",
            "mass_balance",
        ):
            if key in evidence:
                payload[key] = _jsonable_value(evidence[key])
    return MappingProxyType(payload)


def _project_interpolation_ranked_drain_summary(
    trace: Mapping[str, Any],
    payload: dict[str, Any],
) -> None:
    reduced_real_cache = trace.get("reduced_real_cache")
    if not isinstance(reduced_real_cache, Mapping):
        return
    drain = reduced_real_cache.get("interpolation_uncertainty_ranked_table_drain")
    if not isinstance(drain, Mapping):
        return
    selected = drain.get("selected")
    selected_count = (
        len(selected)
        if isinstance(selected, Sequence) and not isinstance(selected, (str, bytes))
        else 0
    )
    payload["interpolation_uncertainty_ranked_drain"] = {
        "schema_version": drain.get("schema_version"),
        "present": True,
        "selected_count": selected_count,
    }


def _failure_counts(records: Sequence[StudyRecord]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if record.failure_category is None:
            continue
        counts[record.failure_category] = counts.get(record.failure_category, 0) + 1
    return MappingProxyType(dict(sorted(counts.items())))


def _no_feasible_config_failure(
    failure_counts: Mapping[str, int],
    record_count: int,
) -> bool:
    if record_count <= 0 or not failure_counts:
        return False
    config_failures = sum(
        count
        for category, count in failure_counts.items()
        if category in _NO_FEASIBLE_CONFIG_FAILURE_CATEGORIES
    )
    return config_failures == record_count


def _coating_leaderboard_fields(records: Sequence[StudyRecord]) -> tuple[str, ...]:
    fields: list[str] = []
    for summary_key, csv_key in _COATING_LEADERBOARD_FIELDS.items():
        if any(summary_key in record.product_summary for record in records):
            fields.append(csv_key)
    return tuple(fields)


def _coating_leaderboard_row(
    record: StudyRecord,
    fields: Sequence[str],
) -> dict[str, Any]:
    summary = coating_summary_with_grounded_authority(record.product_summary)
    row: dict[str, Any] = {}
    if "campaigns_to_resinter" in fields:
        row["campaigns_to_resinter"] = summary.get("campaigns_to_resinter", "")
    if "wall_deposit_kg_by_segment_species_json" in fields:
        row["wall_deposit_kg_by_segment_species_json"] = _json_dump_value(
            summary.get("wall_deposit_kg_by_segment_species", {})
        )
    if "wall_deposit_kg_by_zone_species_json" in fields:
        row["wall_deposit_kg_by_zone_species_json"] = _json_dump_value(
            summary.get("wall_deposit_kg_by_zone_species", {})
        )
    if "wall_deposit_kg_by_species_json" in fields:
        row["wall_deposit_kg_by_species_json"] = _json_dump_value(
            summary.get("wall_deposit_kg_by_species", {})
        )
    for key in (
        "coating_status",
        "coating_authoritative",
        "coating_output_status",
        "coating_status_reason",
        "lifespan_cost_status",
        "lifespan_cost_status_reason",
        "furnace_lifespan_consumed_fraction",
        "wall_deposit_total_kg",
    ):
        if key in fields:
            row[key] = summary.get(key, "")
    return row


def _composition_target_leaderboard_fields(records: Sequence[StudyRecord]) -> tuple[str, ...]:
    if not any(isinstance(record.trace_summary.get("composition_target"), Mapping) for record in records):
        return ()
    return (
        "certification_tier",
        "certified_envelope_json",
        "preference_score",
        "target_spec_digest",
        "best_tap_enabled",
        "tap_hour",
        "tap_provenance",
        "tap_certified",
        "tap_knife_edge",
    )


def _composition_target_leaderboard_row(
    record: StudyRecord,
    fields: Sequence[str],
) -> dict[str, Any]:
    if not fields:
        return {}
    payload = record.trace_summary.get("composition_target")
    if not isinstance(payload, Mapping):
        return {field: "" for field in fields}
    return {
        "certification_tier": str(payload.get("certification_tier", "")),
        "certified_envelope_json": _json_dump_value(payload.get("certified_envelope", [])),
        "preference_score": (
            ""
            if payload.get("preference_score") is None
            else payload["preference_score"]
        ),
        "target_spec_digest": str(payload.get("target_spec_digest", "")),
        "best_tap_enabled": bool(payload.get("best_tap_enabled", False)),
        "tap_hour": payload.get("tap_hour", ""),
        "tap_provenance": str(payload.get("tap_provenance", "")),
        "tap_certified": (
            "" if "certified" not in payload else bool(payload.get("certified"))
        ),
        "tap_knife_edge": (
            "" if "knife_edge" not in payload else bool(payload.get("knife_edge"))
        ),
    }


def _sso2_objective_leaderboard_fields(records: Sequence[StudyRecord]) -> tuple[str, ...]:
    if not any(isinstance(record.trace_summary.get(_SSO2_OBJECTIVE_TRACE_KEY), Mapping) for record in records):
        return ()
    return (
        "sso2_reader_status",
        "sso2_reader_status_reason",
        "sso2_consumed_fields_json",
        "sso2_certified_surface_json",
    )


def _sso2_objective_leaderboard_row(
    record: StudyRecord,
    fields: Sequence[str],
) -> dict[str, Any]:
    if not fields:
        return {}
    payload = record.trace_summary.get(_SSO2_OBJECTIVE_TRACE_KEY)
    if not isinstance(payload, Mapping):
        return {field: "" for field in fields}
    return {
        "sso2_reader_status": str(payload.get("status", "")),
        "sso2_reader_status_reason": str(payload.get("status_reason", "")),
        "sso2_consumed_fields_json": _json_dump_value(payload.get("consumed_fields", [])),
        "sso2_certified_surface_json": _json_dump_value(
            payload.get("certified_sso_r_surface", {})
        ),
    }


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


def _finite_or_infinite(value: Any, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise StudyAbort(f"{label} is not numeric") from exc
    if math.isnan(numeric):
        raise StudyAbort(f"{label} is NaN")
    return numeric


def _json_number(value: Any, label: str) -> float | str:
    numeric = _finite_or_infinite(value, label)
    if math.isinf(numeric):
        return "+inf" if numeric > 0.0 else "-inf"
    return numeric


def _jsonable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable_value(item) for item in value]
    if isinstance(value, list):
        return [_jsonable_value(item) for item in value]
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        return _json_number(value, "json value")
    json.dumps(value)
    return value


def _json_dump_value(value: Any) -> str:
    return json.dumps(
        _jsonable_value(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _rank_key(
    record: StudyRecord,
    definitions: Sequence[ObjectiveDefinition],
) -> tuple[Any, ...]:
    scores = objective_scores(record.objectives, definitions)
    return (*_rank_score_components(scores), record.cache_key or "", record.candidate_id)


def _rank_score_components(
    scores: Sequence[float | None],
) -> tuple[tuple[int, float], ...]:
    return tuple((1, 0.0) if score is None else (0, -score) for score in scores)


def _write_artifacts(
    out: Path,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    definitions: Sequence[ObjectiveDefinition],
    pareto: Sequence[StudyRecord],
    leaderboard: Sequence[StudyRecord],
    winner: StudyRecord | None,
    schema: RecipeSchema,
    failure_counts: Mapping[str, int],
    search_space_identity: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    pareto_path = out / "pareto.json"
    leaderboard_path = out / "leaderboard.csv"
    winner_path = out / "winner.recipe.yaml"
    tap_sidecar_path = out / "winner.tap-truncated.json"
    pareto_payload = dict(
        _pareto_payload(
            profile,
            feedstock,
            fidelity,
            definitions,
            pareto,
            winner,
            schema,
            search_space_identity=search_space_identity,
        )
    )
    pareto_payload["failure_counts"] = dict(failure_counts)
    pareto_path.write_text(
        json.dumps(
            pareto_payload,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_leaderboard(
        leaderboard_path,
        leaderboard,
        pareto,
        winner,
        definitions,
        schema,
        profile=profile,
    )
    artifacts = {
        "pareto": pareto_path,
        "leaderboard": leaderboard_path,
    }
    if winner is not None:
        winner_patch = _materialized_winner_patch(winner, schema, profile)
        winner_path.write_text(
            yaml.safe_dump(winner_patch, sort_keys=True),
            encoding="utf-8",
        )
        artifacts["winner"] = winner_path
        tap_sidecar = _tap_truncated_sidecar(
            winner,
            winner_patch,
            schema.to_setpoints_patch(winner.patch),
        )
        if tap_sidecar is not None:
            tap_sidecar_path.write_text(
                json.dumps(tap_sidecar, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            artifacts["winner_tap_truncated"] = tap_sidecar_path
    return artifacts


_TAP_TRUNCATION_DURATION_PATHS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "C0B": ("campaigns", "C0b_p_cleanup", "duration_h"),
        "C0b_p_cleanup": ("campaigns", "C0b_p_cleanup", "duration_h"),
        "C2A": ("campaigns", "C2A_continuous", "duration_h"),
        "C2A_continuous": ("campaigns", "C2A_continuous", "duration_h"),
    }
)
_UNSUPPORTED_TAP_TRUNCATION_CAMPAIGNS = frozenset(
    {
        "C2A_STAGED",
        "C2A_staged",
        "C3",
        "C3_K",
        "C3_NA",
        "C5",
    }
)


def _materialized_winner_patch(
    winner: StudyRecord,
    schema: RecipeSchema,
    profile: Mapping[str, Any],
) -> Mapping[str, Any]:
    return _materialized_record_patch(winner, schema, profile)


def _materialized_record_patch(
    record: StudyRecord,
    schema: RecipeSchema,
    profile: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    parent_patch = schema.to_setpoints_patch(record.patch)
    payload = _composition_target_payload(record)
    if not _is_tap_truncated(payload):
        return parent_patch
    if profile is None:
        raise StudyAbort(
            "tap-truncated row cannot be serialized without profile context"
        )
    return _tap_truncated_materialized_patch(record, schema, profile, payload)


def _tap_truncated_materialized_patch(
    record: StudyRecord,
    schema: RecipeSchema,
    profile: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    tap_hour = _positive_int(payload.get("tap_hour"), "tap_hour")
    path = _tap_duration_path(payload, profile)
    values = dict(record.patch.values)
    values[path] = float(tap_hour)
    return schema.to_setpoints_patch(RecipePatch(values))


def _tap_truncated_sidecar(
    winner: StudyRecord,
    recipe_patch: Mapping[str, Any],
    parent_patch: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    payload = _composition_target_payload(winner)
    if not _is_tap_truncated(payload):
        return None
    return {
        "candidate_id": winner.candidate_id,
        "provenance": "tap_truncated",
        "recipe": _jsonable_value(recipe_patch),
        "materialized_patch": _jsonable_value(recipe_patch),
        "parent_trajectory_patch": _jsonable_value(parent_patch),
        "operator_instruction": _jsonable_value(payload.get("operator_instruction", {})),
        "tap_grade_report": _jsonable_value(payload.get("tap_grade_report", {})),
        "tap_coating_product_summary": _jsonable_value(
            payload.get("tap_coating_product_summary", {})
        ),
        "tap_hour": payload.get("tap_hour"),
        "configured_hours": payload.get("configured_hours"),
        "tap_score_curve": _jsonable_value(payload.get("tap_score_curve", [])),
    }


def _composition_target_payload(record: StudyRecord) -> Mapping[str, Any]:
    payload = record.trace_summary.get("composition_target")
    return payload if isinstance(payload, Mapping) else MappingProxyType({})


def _is_tap_truncated(payload: Mapping[str, Any]) -> bool:
    return bool(payload) and str(payload.get("tap_provenance", "")) == "tap_truncated"


def _tap_duration_path(
    payload: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> tuple[str, ...]:
    instruction = payload.get("operator_instruction", {})
    candidates: list[str] = []
    if isinstance(instruction, Mapping):
        candidates.extend(
            str(instruction.get(key, ""))
            for key in ("configured_campaign", "phase_at_tap")
            if instruction.get(key)
        )
    run = profile.get("run", {}) if isinstance(profile.get("run"), Mapping) else {}
    if run.get("campaign"):
        candidates.append(str(run["campaign"]))
    for candidate in candidates:
        path = _TAP_TRUNCATION_DURATION_PATHS.get(candidate)
        if path is not None:
            return path
    unsupported = [candidate for candidate in candidates if candidate in _UNSUPPORTED_TAP_TRUNCATION_CAMPAIGNS]
    if unsupported:
        raise StudyAbort(
            "tap-truncated winner cannot be faithfully materialized via the "
            f"recipe schema for staged/dosed campaign {unsupported[0]!r}; "
            "refusing to emit a non-reproducing recipe"
        )
    raise StudyAbort(
        "tap-truncated winner cannot be materialized as a schema-valid recipe "
        f"for campaign candidates {candidates!r}"
    )


def _positive_int(value: Any, label: str) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise StudyAbort(f"{label} must be an integer") from exc
    if numeric <= 0:
        raise StudyAbort(f"{label} must be positive")
    return numeric


def _write_empty_artifacts(
    out: Path,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    definitions: Sequence[ObjectiveDefinition],
    failure_counts: Mapping[str, int],
) -> None:
    (out / "pareto.json").write_text(
        json.dumps(
            {
                "feedstock": feedstock,
                "fidelity": fidelity,
                "failure_counts": dict(failure_counts),
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
    winner: StudyRecord | None,
    schema: RecipeSchema,
    search_space_identity: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    payload = {
        "feedstock": feedstock,
        "fidelity": fidelity,
        "objectives": [_definition_payload(definition) for definition in definitions],
        "pareto": [_record_payload(record, schema, profile) for record in pareto],
        "profile": profile_id(profile),
        "selection_rule": WINNER_SELECTION_RULE,
        "status": (
            COMPLETED_STATUS
            if winner is not None
            else COMPLETED_NO_FEASIBLE_WINNER_STATUS
        ),
        "winner_candidate_id": winner.candidate_id if winner is not None else None,
        "winner_knob_saturation": (
            _winner_knob_saturation_payload(winner) if winner is not None else None
        ),
    }
    if search_space_identity is not None:
        payload["search_space_identity"] = _jsonable_value(search_space_identity)
    return payload


def _winner_knob_saturation_payload(winner: StudyRecord) -> Any:
    payload = winner.trace_summary.get("knob_saturation")
    return _jsonable_value(payload) if isinstance(payload, Mapping) else None


def _definition_payload(definition: ObjectiveDefinition) -> Mapping[str, Any]:
    return {
        "metric": definition.metric,
        "sense": definition.sense,
        "units": definition.units,
        "ordinal": definition.ordinal,
    }


def _materialized_patch_leaderboard_fields(
    records: Sequence[StudyRecord],
) -> tuple[str, ...]:
    if not any(_is_tap_truncated(_composition_target_payload(record)) for record in records):
        return ()
    return ("materialized_patch_json", "parent_trajectory_patch_json")


def _materialized_patch_leaderboard_row(
    record: StudyRecord,
    schema: RecipeSchema,
    profile: Mapping[str, Any] | None,
    fields: Sequence[str],
) -> dict[str, Any]:
    if not fields:
        return {}
    if not _is_tap_truncated(_composition_target_payload(record)):
        return {field: "" for field in fields}
    materialized = _materialized_record_patch(record, schema, profile)
    # Tap rows make the tap-hour patch primary; parent_trajectory_patch_json keeps the full-run metadata.
    return {
        "materialized_patch_json": _json_dump_value(materialized),
        "parent_trajectory_patch_json": _json_dump_value(
            schema.to_setpoints_patch(record.patch)
        ),
    }


def _write_leaderboard(
    path: Path,
    leaderboard: Sequence[StudyRecord],
    pareto: Sequence[StudyRecord],
    winner: StudyRecord | None,
    definitions: Sequence[ObjectiveDefinition],
    schema: RecipeSchema,
    *,
    profile: Mapping[str, Any] | None = None,
) -> None:
    pareto_ids = {record.candidate_id for record in pareto}
    margin_names = sorted({name for record in leaderboard for name in record.feasibility_margins})
    coating_fields = _coating_leaderboard_fields(leaderboard)
    composition_target_fields = _composition_target_leaderboard_fields(leaderboard)
    sso2_objective_fields = _sso2_objective_leaderboard_fields(leaderboard)
    materialized_patch_fields = _materialized_patch_leaderboard_fields(leaderboard)
    fieldnames = [
        "rank",
        "candidate_id",
        "cache_key",
        "is_pareto",
        "is_winner",
        *(definition.metric for definition in definitions),
        *(f"margin_{name}" for name in margin_names),
        *coating_fields,
        *composition_target_fields,
        *sso2_objective_fields,
        *materialized_patch_fields,
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
                # For tap rows, patch_json is the materialized tap-hour patch, not the parent trajectory.
                "patch_json": _json_dump_value(
                    _materialized_record_patch(record, schema, profile)
                ),
            }
            row.update({
                definition.metric: (
                    ""
                    if record.objectives.get(definition.metric) is None
                    else record.objectives[definition.metric]
                )
                for definition in definitions
            })
            row.update(
                {
                    f"margin_{name}": record.feasibility_margins[name]["margin"]
                    for name in margin_names
                    if name in record.feasibility_margins
                }
            )
            row.update(_coating_leaderboard_row(record, coating_fields))
            row.update(_composition_target_leaderboard_row(record, composition_target_fields))
            row.update(_sso2_objective_leaderboard_row(record, sso2_objective_fields))
            row.update(
                _materialized_patch_leaderboard_row(
                    record,
                    schema,
                    profile,
                    materialized_patch_fields,
                )
            )
            writer.writerow(row)


def _record_payload(
    record: StudyRecord,
    schema: RecipeSchema,
    profile: Mapping[str, Any],
    *,
    search_space_identity: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    patch = _materialized_record_patch(record, schema, profile)
    payload = {
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
        "patch": patch,
        "product_summary": _jsonable_value(record.product_summary),
        "trace_summary": _jsonable_value(record.trace_summary),
        "status": record.status,
    }
    if search_space_identity is not None:
        payload["search_space_identity"] = _jsonable_value(search_space_identity)
    if _is_tap_truncated(_composition_target_payload(record)):
        # Tap rows serialize materialized patch as primary; parent_trajectory_patch preserves metadata.
        payload["materialized_patch"] = patch
        payload["parent_trajectory_patch"] = schema.to_setpoints_patch(record.patch)
    return payload


def profile_id(profile: Mapping[str, Any]) -> str:
    return str(profile.get("profile_id") or profile.get("id") or "inline-profile")
