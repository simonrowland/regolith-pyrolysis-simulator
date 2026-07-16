"""Phase-O recipe optimizer study loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import copy
import csv
import hashlib
import importlib.metadata
import inspect
import json
import logging
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

import yaml

from simulator.backend_names import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    canonical_backend_name,
)
from simulator.backends import CACHE_TIER_CEILINGS, DEFAULT_CACHE_TIER_CEILING
from simulator.ceramic_classifier import (
    CeramicMatch,
    CeramicServiceTemperature,
)
from simulator.config import DEFAULT_DATA_DIR, load_config_bundle
from simulator.cost_parameters import (
    RECIPE_COST_PARAMETERS_KEY,
)
from simulator.corpus_version import current_corpus_version
from simulator.diagnostics import coating_summary_with_grounded_authority
from simulator.optimize.canonical import canonical_json_dumps
from simulator.optimize.doe import active_sampler_name
from simulator.optimize.determinism import pin_seeds
from simulator.optimize.evaluate import (
    EvaluationAbort,
    FailureCategory,
    RunReference,
    ScoredResult,
    _build_eval_inputs,
    _build_prefix_eval_inputs,
    _is_stale_profile_refusal,
    _stale_profile_result,
    evaluate,
)
from simulator.optimize.evalspec import (
    EvalSpec,
    PrefixEvalSpec,
    cache_key,
    canonical_evalspec_json,
    current_code_version,
)
from simulator.optimize.honesty import optimizer_tier_label
from simulator.optimize.objective import (
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    ObjectiveComputationError,
    ObjectiveDefinition,
    ObjectiveProfileError,
    ObjectiveVector,
    canonical_objective_mapping,
    objective_definitions,
    objective_scores,
    objective_value_for_metric,
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
    canonicalize_profile_backend_names,
    physics_constraints_from_profile,
    validate_profile,
)
from simulator.optimize.recipe import (
    RecipePatch,
    RecipeSchema,
    RecipeValidationError,
    allowlist_version,
    recipe_schema_version,
    conditional_context_from_metadata,
    c5_sampler_context,
)
from simulator.optimize.result_scope import result_scope_json, result_scope_payload
from simulator.optimize.save_bundle import MEMBER_SCHEMA_VERSION, SAVE_SCHEMA_VERSION
from simulator.optimize.results_store import (
    ResultStore,
    ResultStoreWriteRejected,
    _deserialize_eval_spec,
    _deserialize_margins,
    _deserialize_objectives,
    _deserialize_run_reference,
    _result_blob,
    _serialize_eval_spec,
    _serialize_margins,
    _serialize_objectives,
    _serialize_run_reference,
    reground_scored_result,
)
from simulator.optimize.strategy import (
    Candidate,
    MorrisScreenStrategy,
    RandomStrategy,
    Strategy,
    WarmStartSeed,
)
from simulator.optimize.strategy.staged import (
    StagedBeamStateError,
    StagedReplayViolation,
    StagedStrategy,
    TopologyChoice,
    authoritative_prefix_stage_ids,
    assert_prefix_replay_equal,
    enumerate_topologies,
    make_prefix_eval_spec,
)
from web.advisory import ceramic_rump_payload

VALID_FIDELITIES = (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    "fast",
    "high",
    "auto",
)
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
ABORTED_STATUS = "aborted"
CERTIFIED_CACHE_STATES = frozenset({"cached_exact", "live_fill"})
EXPLORE_CACHE_TIER_CEILING = "cached_interpolated"
CERTIFY_CACHE_TIER_CEILING = "cached_exact"
DEFAULT_TWO_PHASE_TOP_K = 10
_TWO_PHASE_CERTIFICATION_POOL_MAX_MULTIPLIER = 4
_TWO_PHASE_CERTIFICATION_NAME = "two_phase_certification.json"
_SSO2_OBJECTIVE_METRIC = "sso2_pn2_fe_drain_silica"
_SSO2_OBJECTIVE_TRACE_KEY = "sso2_objective_evidence"
_TAP_COATING_PRODUCT_SUMMARY_FIELDS = frozenset(
    {
        "campaigns_to_resinter",
        "aggregate_campaigns_to_resinter",
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
        "wall_deposit_cumulative_total_kg",
        "wall_deposit_cumulative_kg_by_species",
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
_OXIDE_FORMULA_RE = re.compile(r"^(?:[A-Z][a-z]?\d*)*O\d*$")
_TERMINAL_RUMP_PRODUCT_CLASS_KEYS = frozenset(
    {"refractory_ceramic_rump", "terminal_rump"}
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
                        "metric": ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
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
                    "backend_name": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
                },
                "fidelities": {
                    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN: {
                        "backend_name": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
                        "hours": 1,
                    },
                    "fast": {
                        "backend_name": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
                        "hours": 1,
                    },
                    "high": {
                        "backend_name": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
                        "hours": 1,
                    },
                    "auto": {
                        "backend_name": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
                        "hours": 1,
                    },
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
    warm_start_from: str | Path | Mapping[str, Any] | None = None
    per_eval_timeout_seconds: float | None = None


@dataclass(frozen=True)
class _WarmStartSource:
    store: ResultStore
    pareto_path: Path


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
    result_blob: Mapping[str, Any] = field(default_factory=dict)
    proposal_source: str = "unknown"
    seed_lineage: bool = False
    search_provenance: Mapping[str, Any] = field(default_factory=dict)
    eval_spec: EvalSpec | None = None

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
        object.__setattr__(
            self,
            "result_blob",
            MappingProxyType(dict(self.result_blob)),
        )
        object.__setattr__(self, "proposal_source", str(self.proposal_source))
        object.__setattr__(self, "seed_lineage", bool(self.seed_lineage))
        object.__setattr__(
            self,
            "search_provenance",
            MappingProxyType(dict(self.search_provenance)),
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
    prefix_evals_run: int = 0
    winner_selection_rule: str = WINNER_SELECTION_RULE

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", MappingProxyType(dict(self.artifacts)))


STUDY_EVENTS_NAME = "study.events.jsonl"
STRATEGY_STATE_NAME = "strategy_state.jsonl"
JOURNAL_EVENT_VERSION = 1
REPLAY_RELEVANT_EVENT_KINDS = frozenset({"candidate_asked", "candidate_evaluated"})
EPOCH_IDENTITY_KEYS = (
    "code_version",
    "corpus_version",
    "allowlist_version",
    "bounds_digest",
)


class StudyReplayError(StudyError):
    """Raised when a study journal cannot be replayed exactly."""


class StudyManifestMismatchError(StudyReplayError):
    """Raised when a journal replay crosses the locked identity epoch."""


@dataclass(frozen=True)
class StudyReplayResult:
    run_dir: Path
    manifest: Mapping[str, Any]
    strategy: Strategy
    records: tuple[StudyRecord, ...]
    leaderboard: tuple[StudyRecord, ...]
    pareto: tuple[StudyRecord, ...]
    pending_candidates: tuple[Candidate, ...] = ()
    warnings: tuple[str, ...] = ()
    consumed_rows: int = 0
    strategy_state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest", MappingProxyType(dict(self.manifest)))
        object.__setattr__(self, "records", tuple(self.records))
        object.__setattr__(self, "leaderboard", tuple(self.leaderboard))
        object.__setattr__(self, "pareto", tuple(self.pareto))
        object.__setattr__(self, "pending_candidates", tuple(self.pending_candidates))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "strategy_state", MappingProxyType(dict(self.strategy_state)))


def _study_write_lock_path(out: Path) -> Path:
    """Shared commit lock for cache.sqlite plus materialized study artifacts.

    Evaluations run outside this lock. Final artifact files are latest snapshots
    for the run directory, so concurrent finishers serialize and the later
    completed artifact commit wins.
    """

    return out / ".study-results.write.lock"


class _LockedLineWriter:
    def __init__(self, path: Path, mode: str, store: ResultStore) -> None:
        if mode not in {"a", "w", "r"}:
            raise ValueError(f"unsupported line writer mode: {mode!r}")
        self.path = path
        self._mode = mode
        self._store = store

    def write(self, text: str) -> None:
        with self._store.write_lock():
            self.write_with_write_lock(text)

    def initialize(self) -> None:
        if self._mode == "r":
            with self.path.open("r", encoding="utf-8"):
                return
        with self._store.write_lock():
            if self._mode == "w":
                self.path.write_text("", encoding="utf-8")
                self._mode = "a"
            else:
                self.path.touch(exist_ok=True)

    def write_with_write_lock(self, text: str) -> None:
        if self._mode == "r":
            raise ValueError(f"line writer is read-only: {self.path}")
        with self.path.open(self._mode, encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
        if self._mode == "w":
            self._mode = "a"

    def flush(self) -> None:
        return None


def _store_write_context(store: ResultStore | None) -> Any:
    return store.write_lock() if store is not None else nullcontext()


@dataclass(frozen=True)
class _JournalResumePosition:
    event_seq: int = 0
    ask_seq: int = 0
    tell_seq: int = 0
    batch_seq: int = 0
    pending_batch_seq: int | None = None
    ask_seq_by_id: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ask_seq_by_id", MappingProxyType(dict(self.ask_seq_by_id)))


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
    warm_start_from: str | Path | Mapping[str, Any] | None = None,
    pinned_paths: Sequence[str] | None = None,
    per_eval_timeout_seconds: float | None = None,
) -> StudyResult:
    """Run one ask/evaluate/tell study and write Phase-O artifacts."""

    fidelity = str(canonical_backend_name(fidelity))
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
        warm_start_from=warm_start_from,
        per_eval_timeout_seconds=resolve_eval_timeout_seconds(per_eval_timeout_seconds),
    )
    pin_seeds(config.seed)
    try:
        resolved_profile = resolve_profile(
            config.profile,
            expected_feedstock=config.feedstock,
            schema=base_schema,
        )
    except ProfileValidationError as exc:
        if not _is_stale_profile_refusal(exc) or not isinstance(config.profile, Mapping):
            raise
        resolved_profile = canonicalize_profile_backend_names(
            config.profile,
            source="<stale-profile>",
        )
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
    store = result_store or ResultStore(
        out / "cache.sqlite",
        write_lock_path=_study_write_lock_path(out),
    )
    warm_start_source = _resolve_warm_start_source(
        resolved_profile,
        config.warm_start_from,
        current_store_path=store.path,
    )
    requested_topologies = _requested_staged_topologies(resolved_profile, topologies)
    _validate_multi_topology_seed_recipes(resolved_profile, requested_topologies)
    warm_start_seeds = _resolve_warm_start_seeds(
        resolved_profile,
        feedstock=config.feedstock,
        fidelity=config.fidelity,
        schema=active_schema,
        warm_start_source=warm_start_source,
        constraints=active_constraints,
        search_space_identity=search_space_identity,
        requested_topologies=requested_topologies,
    )
    if requested_topologies is None:
        active_strategy = resolve_strategy(
            config.strategy,
            profile=resolved_profile,
            seed=config.seed,
            schema=active_schema,
            warm_start_seeds=warm_start_seeds,
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
                seed=_staged_topology_seed(config.seed, topology, requested_topologies),
                objective_profile=single_topology_profile,
                topology=topology,
                stage0_seed_candidates=warm_start_seeds,
            )
            for topology in requested_topologies
        )
        active_strategy = staged_strategies[0]

    records: list[StudyRecord] = []
    provenance_path = out / "provenance.jsonl"
    events_path = out / STUDY_EVENTS_NAME
    strategy_state_path = out / STRATEGY_STATE_NAME
    evaluated = 0
    prefix_evals_run = 0
    topology_cursor = 0
    batch_seq = 0
    ask_seq_by_id: dict[str, int] = {}
    pending_resume_candidates: list[Candidate] = []
    pending_resume_batch_seq: int | None = None
    journal_event_seq = 0
    journal_ask_seq = 0
    journal_tell_seq = 0
    journal_mode = "w"
    provenance_mode = "w"
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
    if events_path.is_file():
        if requested_topologies is not None:
            raise StudyReplayError(
                "staged study resume is not yet supported; refusing to overwrite existing journal"
            )
        resumed = load_study_resume_state(out, schema=active_schema)
        _assert_resume_manifest_matches_invocation(
            resumed.manifest,
            config=config,
            profile=resolved_profile,
            feedstock=config.feedstock,
            fidelity=config.fidelity,
            search_space_identity=search_space_identity,
            strategy=active_strategy,
        )
        resume_events = _load_replay_events(events_path)
        resume_position = _journal_resume_position(
            resume_events,
            pending_candidates=resumed.pending_candidates,
        )
        records = list(resumed.records)
        evaluated = len(records)
        if evaluated > config.budget:
            raise StudyReplayError(
                "study resume journal already exceeds requested budget: "
                f"evaluated={evaluated} budget={config.budget}"
            )
        if evaluated + len(resumed.pending_candidates) > config.budget:
            raise StudyReplayError(
                "study resume pending asks exceed requested budget: "
                f"evaluated={evaluated} pending={len(resumed.pending_candidates)} "
                f"budget={config.budget}"
            )
        active_strategy = resumed.strategy
        staged_strategies = ()
        batch_seq = resume_position.batch_seq
        ask_seq_by_id = dict(resume_position.ask_seq_by_id)
        pending_resume_candidates = list(resumed.pending_candidates)
        pending_resume_batch_seq = resume_position.pending_batch_seq
        journal_event_seq = resume_position.event_seq
        journal_ask_seq = resume_position.ask_seq
        journal_tell_seq = resume_position.tell_seq
        journal_mode = "a"
        provenance_mode = "a"
        if not pending_resume_candidates and evaluated == config.budget:
            provenance_mode = "r"
    try:
        provenance_writer = _LockedLineWriter(provenance_path, provenance_mode, store)
        events_writer = _LockedLineWriter(events_path, journal_mode, store)
        strategy_state_writer = _LockedLineWriter(strategy_state_path, journal_mode, store)
        provenance_writer.initialize()
        events_writer.initialize()
        strategy_state_writer.initialize()
        journal = _StudyJournalWriter(
            events_writer,
            strategy_state_writer,
            schema=active_schema,
            strategy_name=_strategy_label(active_strategy),
            event_seq=journal_event_seq,
            ask_seq=journal_ask_seq,
            tell_seq=journal_tell_seq,
        )
        while evaluated < config.budget:
            owners: dict[str, StagedStrategy] = {}
            if pending_resume_candidates:
                candidates = pending_resume_candidates
                pending_resume_candidates = []
                if pending_resume_batch_seq is None:
                    raise StudyReplayError("study resume pending asks are missing batch_seq")
                current_batch_seq = pending_resume_batch_seq
                pending_resume_batch_seq = None
            else:
                batch_size = min(config.parallel, config.budget - evaluated)
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
                batch_seq += 1
                current_batch_seq = batch_seq
                ask_seq_by_id.update(
                    journal.write_ask_batch(
                        batch_seq=batch_seq,
                        candidates=candidates,
                        owners=owners,
                    )
                )
                journal.write_strategy_state(
                    batch_seq=batch_seq,
                    strategy=active_strategy,
                    staged_strategies=staged_strategies,
                )
            results, prefix_evals_in_batch = _evaluate_candidates(
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
            # Owner decision deferred: debit config.budget here if prefix evals join it.
            prefix_evals_run += prefix_evals_in_batch
            tell_batch: list[tuple[Candidate, ScoredResult]] = []
            for candidate, scored, cache_hit in results:
                _assert_honest_result(scored, definitions)
                light_scored = _strip_heavy_result(scored)
                record = _to_record(candidate, scored, cache_hit=cache_hit)
                with store.write_lock():
                    if scored.eval_spec is not None:
                        try:
                            store.store_with_write_lock(
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
                    records.append(record)
                    provenance_writer.write_with_write_lock(
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
                    journal.write_tell_with_write_lock(
                        batch_seq=current_batch_seq,
                        ask_seq=ask_seq_by_id[candidate.id],
                        candidate=candidate,
                        scored=light_scored,
                        source_scored=scored,
                        cache_hit=cache_hit,
                    )
            if staged_strategies:
                grouped: dict[StagedStrategy, list[tuple[Candidate, ScoredResult]]] = {}
                for candidate, scored in tell_batch:
                    grouped.setdefault(owners[candidate.id], []).append((candidate, scored))
                for owner, owner_batch in grouped.items():
                    owner.tell(owner_batch)
            else:
                active_strategy.tell(tell_batch)
            journal.write_strategy_state(
                batch_seq=current_batch_seq,
                strategy=active_strategy,
                staged_strategies=staged_strategies,
            )
            evaluated += len(candidates)
    except (KeyboardInterrupt, StudyAbort):
        _write_aborted_artifacts_from_cache(
            out,
            profile=resolved_profile,
            feedstock=feedstock,
            fidelity=fidelity,
            definitions=definitions,
            fallback_records=records,
            schema=active_schema,
            failure_counts=_failure_counts(records),
            search_space_identity=search_space_identity,
            strategy_provenance=_strategy_provenance_payload(
                active_strategy,
                *staged_strategies,
            ),
            config=config,
            strategy=active_strategy,
            strategy_name=_strategy_label(active_strategy),
            sampler_name=_resolved_strategy_sampler(active_strategy),
            constraints=active_constraints,
            write_store=store,
            prefix_evals_run=prefix_evals_run,
        )
        raise

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
            config=config,
            constraints=active_constraints,
            write_store=store,
            prefix_evals_run=prefix_evals_run,
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
            config=config,
            constraints=active_constraints,
            write_store=store,
            prefix_evals_run=prefix_evals_run,
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
                config=config,
                constraints=active_constraints,
                write_store=store,
                prefix_evals_run=prefix_evals_run,
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
            strategy_provenance=_strategy_provenance_payload(
                active_strategy,
                *staged_strategies,
            ),
            config=config,
            strategy=active_strategy,
            strategy_name=_strategy_label(active_strategy),
            sampler_name=_resolved_strategy_sampler(active_strategy),
            study_status=COMPLETED_NO_FEASIBLE_WINNER_STATUS,
            write_store=store,
            prefix_evals_run=prefix_evals_run,
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
            prefix_evals_run=prefix_evals_run,
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
            _two_phase_certification_pool(
                leaderboard=explore_leaderboard,
                pareto=explore_pareto_ranked,
                top_k=two_phase.top_k,
                include_pareto_extras=len(definitions) > 1,
            ),
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
            requested_top_k=two_phase.top_k,
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
        strategy_provenance=_strategy_provenance_payload(
            active_strategy,
            *staged_strategies,
        ),
        config=config,
        strategy=active_strategy,
        strategy_name=_strategy_label(active_strategy),
        sampler_name=_resolved_strategy_sampler(active_strategy),
        study_status=result_status,
        write_store=store,
        prefix_evals_run=prefix_evals_run,
    )
    artifacts["provenance"] = provenance_path
    artifacts["store"] = store.path
    if certification_artifact is not None:
        certification_path = out / _TWO_PHASE_CERTIFICATION_NAME
        certification_artifact = _json_member_payload(certification_artifact)
        with store.write_lock():
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
        prefix_evals_run=prefix_evals_run,
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
    store = ResultStore(
        out / "cache.sqlite",
        write_lock_path=_study_write_lock_path(out),
    )
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
    # Grind-infra sweep Finding 4: run_certify() previously labelled the
    # provenance artifact but never wrote it (unlike run() at the loop sink),
    # so certified/cached results pointed at a non-existent audit trail. Emit
    # the certification record in the same JSONL format run() uses.
    provenance_path = out / "provenance.jsonl"
    with store.write_lock():
        try:
            store.store_with_write_lock(
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
        _LockedLineWriter(provenance_path, "w", store).write_with_write_lock(
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
        config=config,
        strategy_name="certify",
        sampler_name=_resolved_strategy_sampler(config.strategy),
        study_status=COMPLETED_STATUS,
        write_store=store,
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
    backend_name = str(
        fid_opts.get(
            "backend_name",
            run.get("backend_name", ANALYTICAL_BACKEND_SERIALIZATION_TOKEN),
        )
    )
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


def _package_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _strategy_label(strategy: Strategy | str) -> str:
    if isinstance(strategy, str):
        return STRATEGY_CLASS_NAMES.get(strategy, strategy)
    return type(strategy).__name__


def _primary_objective_metric(definitions: Sequence[ObjectiveDefinition]) -> str:
    if not definitions:
        raise StudyError("objective definitions required for certification")
    return definitions[0].metric


def _objective_value(record: StudyRecord, metric: str) -> float | None:
    objectives = canonical_objective_mapping(record.objectives)
    if metric not in objectives:
        raise StudyError(f"record {record.candidate_id!r} missing objective {metric!r}")
    if objectives[metric] is None:
        return None
    return float(objectives[metric])


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
    for carrier in _cache_trace_carriers(trace):
        for key in ("reduced_real_cache_state", "cache_state"):
            state = carrier.get(key)
            if state is not None:
                return str(state)
    return None


def _cache_rung_from_scored(scored: ScoredResult) -> str | None:
    reference = scored.run_reference
    trace = reference.trace if reference is not None else None
    for carrier in _cache_trace_carriers(trace):
        for key in (
            "physics_bucket_rung",
            "reduced_real_cache_rung",
            "cache_rung",
            "rung",
        ):
            value = carrier.get(key)
            if value is not None:
                return str(value)
    return None


def _cache_trace_carriers(trace: Any) -> tuple[Mapping[str, Any], ...]:
    carriers: list[Mapping[str, Any]] = []
    if isinstance(trace, Mapping):
        carriers.append(trace)
        per_hour = trace.get("per_hour_summary", trace.get("per_hour"))
        if isinstance(per_hour, Sequence) and per_hour:
            last = per_hour[-1]
            if isinstance(last, Mapping):
                carriers.append(last)
    return tuple(carriers)


def _is_certified_cache_state(cache_state: str | None) -> bool:
    return cache_state in CERTIFIED_CACHE_STATES


class _StudyJournalWriter:
    def __init__(
        self,
        events: _LockedLineWriter,
        strategy_state: _LockedLineWriter,
        *,
        schema: RecipeSchema,
        strategy_name: str,
        event_seq: int = 0,
        ask_seq: int = 0,
        tell_seq: int = 0,
    ) -> None:
        self._events = events
        self._strategy_state = strategy_state
        self._schema = schema
        self._strategy_name = strategy_name
        self._event_seq = event_seq
        self._ask_seq = ask_seq
        self._tell_seq = tell_seq

    def write_ask_batch(
        self,
        *,
        batch_seq: int,
        candidates: Sequence[Candidate],
        owners: Mapping[str, StagedStrategy],
    ) -> dict[str, int]:
        ask_seq_by_id: dict[str, int] = {}
        with self._events._store.write_lock():
            for candidate_index, candidate in enumerate(candidates):
                self._ask_seq += 1
                ask_seq_by_id[candidate.id] = self._ask_seq
                owner = owners.get(candidate.id)
                self._write_event(
                    {
                        "event_kind": "candidate_asked",
                        "replay_relevant": True,
                        "batch_seq": batch_seq,
                        "ask_seq": self._ask_seq,
                        "candidate_index": candidate_index,
                        "candidate_id": candidate.id,
                        "strategy": self._strategy_name,
                        "owner_strategy": _owner_strategy_payload(owner),
                        "candidate": _candidate_journal_payload(candidate, self._schema),
                        "cache_state": None,
                        "rung": None,
                        "cache_tier": None,
                    },
                    lock_already_held=True,
                )
        return ask_seq_by_id

    def write_tell(
        self,
        *,
        batch_seq: int,
        ask_seq: int,
        candidate: Candidate,
        scored: ScoredResult,
        source_scored: ScoredResult,
        cache_hit: bool,
    ) -> None:
        with self._events._store.write_lock():
            self.write_tell_with_write_lock(
                batch_seq=batch_seq,
                ask_seq=ask_seq,
                candidate=candidate,
                scored=scored,
                source_scored=source_scored,
                cache_hit=cache_hit,
            )

    def write_tell_with_write_lock(
        self,
        *,
        batch_seq: int,
        ask_seq: int,
        candidate: Candidate,
        scored: ScoredResult,
        source_scored: ScoredResult,
        cache_hit: bool,
    ) -> None:
        self._tell_seq += 1
        cache_state = _cache_state_from_scored(source_scored)
        rung = _cache_rung_from_scored(source_scored)
        self._write_event(
            {
                "event_kind": "candidate_evaluated",
                "replay_relevant": True,
                "batch_seq": batch_seq,
                "ask_seq": ask_seq,
                "tell_seq": self._tell_seq,
                "candidate_id": candidate.id,
                "cache_hit": bool(cache_hit),
                "cache_state": cache_state,
                "rung": rung,
                "cache_tier": rung if rung is not None else cache_state,
                "status": _result_status(scored),
                "failure_category": (
                    scored.failure_category.value
                    if scored.failure_category is not None
                    else None
                ),
                "objectives": _serialize_objectives(scored.objectives),
                "feasibility_margins": _serialize_margins(scored.feasibility_margins),
                "failing_gates": list(scored.failing_gates),
                "scored_result": _scored_result_journal_payload(scored),
            },
            lock_already_held=True,
        )

    def write_strategy_state(
        self,
        *,
        batch_seq: int,
        strategy: Strategy,
        staged_strategies: Sequence[StagedStrategy],
    ) -> None:
        with self._strategy_state._store.write_lock():
            self.write_strategy_state_with_write_lock(
                batch_seq=batch_seq,
                strategy=strategy,
                staged_strategies=staged_strategies,
            )

    def write_strategy_state_with_write_lock(
        self,
        *,
        batch_seq: int,
        strategy: Strategy,
        staged_strategies: Sequence[StagedStrategy],
    ) -> None:
        payload = {
            "member_schema_version": MEMBER_SCHEMA_VERSION,
            "event_version": JOURNAL_EVENT_VERSION,
            "event_kind": "strategy_state",
            "batch_seq": batch_seq,
            "journal_metadata": {"created_at": datetime.now(UTC).isoformat()},
            "strategy_state": _strategy_replay_state(strategy, *staged_strategies),
        }
        self._strategy_state.write_with_write_lock(_json_dump_value(payload) + "\n")

    def _write_event(
        self,
        payload: Mapping[str, Any],
        *,
        lock_already_held: bool = False,
    ) -> None:
        self._event_seq += 1
        row = {
            "member_schema_version": MEMBER_SCHEMA_VERSION,
            "event_version": JOURNAL_EVENT_VERSION,
            "event_seq": self._event_seq,
            "journal_metadata": {"created_at": datetime.now(UTC).isoformat()},
            **payload,
        }
        text = _json_dump_value(row) + "\n"
        if lock_already_held:
            self._events.write_with_write_lock(text)
        else:
            self._events.write(text)


def replay_study(run_dir: str | Path, *, schema: RecipeSchema | None = None) -> StudyReplayResult:
    """Replay a saved study by re-asking and re-telling recorded journal results."""

    return _load_study_journal(run_dir, schema=schema, allow_pending=False)


def load_study_resume_state(
    run_dir: str | Path,
    *,
    schema: RecipeSchema | None = None,
) -> StudyReplayResult:
    """Load an interrupted study journal, replay completed tells, and expose pending asks."""

    return _load_study_journal(run_dir, schema=schema, allow_pending=True)


def _load_study_journal(
    run_dir: str | Path,
    *,
    schema: RecipeSchema | None,
    allow_pending: bool,
) -> StudyReplayResult:
    root = Path(run_dir)
    manifest_path = root / "study.manifest.json"
    events_path = root / STUDY_EVENTS_NAME
    profile_path = root / "study.profile.yaml"
    if not events_path.is_file():
        raise StudyReplayError(f"study journal not found: {events_path}")
    manifest = _load_json_object_file(manifest_path)
    profile_payload = _load_yaml_mapping(profile_path)
    base_schema = schema or RecipeSchema()
    feedstock = str(manifest.get("feedstock_id") or profile_payload.get("feedstock") or "")
    if not feedstock:
        raise StudyReplayError("study manifest missing feedstock_id")
    resolved_profile = resolve_profile(
        profile_payload,
        expected_feedstock=feedstock,
        schema=base_schema,
    )
    manifest_identity = _mapping_or_empty(manifest.get("search_space_identity"))
    active_schema = _schema_with_pinned_paths(
        base_schema,
        resolved_profile,
        cli_pinned_paths=tuple(
            str(path) for path in manifest_identity.get("cli_pinned_paths", ())
        ),
    )
    search_space_identity = _search_space_identity(
        active_schema,
        profile_pinned_paths=_profile_pinned_paths(resolved_profile),
        cli_pinned_paths=_cli_pinned_paths(manifest_identity.get("cli_pinned_paths", ())),
    )
    warnings_seen = list(
        _assert_replay_manifest_current(
            manifest,
            schema=active_schema,
            search_space_identity=search_space_identity,
        )
    )
    strategy_key = _strategy_key_from_manifest(manifest)
    config_payload = _mapping_or_empty(_mapping_or_empty(manifest.get("strategy")).get("config"))
    if config_payload.get("warm_start_from") is not None:
        raise StudyReplayError(
            "journal replay for warm_start_from requires bundled warm-start seed state"
        )
    config = StudyConfig(
        profile=resolved_profile,
        feedstock=feedstock,
        strategy=strategy_key,
        fidelity=str(canonical_backend_name(str(manifest.get("fidelity")))),
        parallel=int(manifest.get("parallel", 1)),
        budget=int(manifest.get("budget", 0)),
        out_dir=root,
        seed=int(manifest.get("seed", 0)),
    )
    pin_seeds(config.seed)
    requested_topologies = _requested_staged_topologies(resolved_profile, None)
    try:
        active_constraints = _constraints_for_profile(resolved_profile)
    except ProfileValidationError as exc:
        if not _is_stale_profile_refusal(exc):
            raise
        active_constraints = None
    warm_start_seeds = _resolve_warm_start_seeds(
        resolved_profile,
        feedstock=config.feedstock,
        fidelity=config.fidelity,
        schema=active_schema,
        warm_start_source=None,
        constraints=active_constraints,
        search_space_identity=search_space_identity,
        requested_topologies=requested_topologies,
    )
    if requested_topologies is None:
        active_strategy = resolve_strategy(
            strategy_key,
            profile=resolved_profile,
            seed=config.seed,
            schema=active_schema,
            warm_start_seeds=warm_start_seeds,
        )
        staged_strategies: tuple[StagedStrategy, ...] = ()
    else:
        single_topology_profile = _profile_without_staged_topologies(resolved_profile)
        staged_strategies = tuple(
            StagedStrategy(
                active_schema,
                seed=_staged_topology_seed(config.seed, topology, requested_topologies),
                objective_profile=single_topology_profile,
                topology=topology,
                stage0_seed_candidates=warm_start_seeds,
            )
            for topology in requested_topologies
        )
        active_strategy = staged_strategies[0]

    warnings_seen.extend(_strategy_manifest_warnings(manifest, active_strategy))
    events = _load_replay_events(events_path)
    definitions = objective_definitions(resolved_profile)
    records: list[StudyRecord] = []
    pending_candidates: list[Candidate] = []
    topology_cursor = 0
    for batch in _journal_batches(events):
        ask_rows = batch["asked"]
        tell_rows = batch["told"]
        if not ask_rows:
            raise StudyReplayError(f"journal batch {batch['batch_seq']} has tells without asks")
        if pending_candidates:
            raise StudyReplayError("journal asks a new batch while prior asks remain pending")
        candidates, topology_cursor, owners = _replay_ask_batch(
            active_strategy,
            staged_strategies=staged_strategies,
            topology_cursor=topology_cursor,
            batch_size=len(ask_rows),
        )
        _assert_replayed_candidates_match(ask_rows, candidates)
        candidate_by_id = {candidate.id: candidate for candidate in candidates}
        told_ids: set[str] = set()
        tell_batch: list[tuple[Candidate, ScoredResult]] = []
        for row in tell_rows:
            candidate_id = str(row.get("candidate_id"))
            candidate = candidate_by_id.get(candidate_id)
            if candidate is None:
                raise StudyReplayError(
                    f"tell row references candidate outside batch: {candidate_id!r}"
                )
            scored_payload = _mapping_or_empty(row.get("scored_result"))
            _assert_journal_objectives_consistent(row, scored_payload)
            scored = _scored_result_from_journal_payload(
                scored_payload
            )
            tell_batch.append((candidate, scored))
            told_ids.add(candidate_id)
            records.append(_to_record(candidate, scored, cache_hit=bool(row.get("cache_hit"))))
        if tell_batch:
            _replay_tell_batch(
                active_strategy,
                staged_strategies=staged_strategies,
                owners=owners,
                tell_batch=tell_batch,
            )
        for row, candidate in zip(ask_rows, candidates, strict=True):
            if candidate.id not in told_ids:
                pending_candidates.append(candidate)
        if pending_candidates and not allow_pending:
            raise StudyReplayError(
                "study journal has asked candidates without recorded tell rows"
            )

    feasible = tuple(record for record in records if record.feasible)
    leaderboard = (
        tuple(sorted(feasible, key=lambda row: _rank_key(row, definitions)))
        if feasible
        else tuple(records)
    )
    pareto = tuple(
        sorted(
            pareto_front(
                feasible,
                definitions,
                objective_getter=lambda row: row.objectives,
            ),
            key=lambda row: _rank_key(row, definitions),
        )
    )
    replay_state = _strategy_replay_state(active_strategy, *staged_strategies)
    stored_state = _load_latest_strategy_state(root / STRATEGY_STATE_NAME)
    if stored_state is not None and stored_state != replay_state:
        raise StudyReplayError(
            "strategy_state snapshot differs from journal replay; refusing replay"
        )
    return StudyReplayResult(
        run_dir=root,
        manifest=manifest,
        strategy=active_strategy,
        records=tuple(records),
        leaderboard=leaderboard,
        pareto=pareto,
        pending_candidates=tuple(pending_candidates),
        warnings=tuple(warnings_seen),
        consumed_rows=len(events),
        strategy_state=replay_state,
    )


def _replay_ask_batch(
    active_strategy: Strategy,
    *,
    staged_strategies: Sequence[StagedStrategy],
    topology_cursor: int,
    batch_size: int,
) -> tuple[list[Candidate], int, dict[str, StagedStrategy]]:
    owners: dict[str, StagedStrategy] = {}
    if staged_strategies:
        candidates, topology_cursor, owners = _ask_staged_topology_candidates(
            staged_strategies,
            cursor=topology_cursor,
            batch_size=batch_size,
        )
        return candidates, topology_cursor, owners
    candidates = active_strategy.ask(batch_size)
    if not candidates and isinstance(active_strategy, StagedStrategy):
        if active_strategy.run_backward_pass() or active_strategy.joint_refine():
            candidates = active_strategy.ask(batch_size)
    return candidates, topology_cursor, owners


def _replay_tell_batch(
    active_strategy: Strategy,
    *,
    staged_strategies: Sequence[StagedStrategy],
    owners: Mapping[str, StagedStrategy],
    tell_batch: Sequence[tuple[Candidate, ScoredResult]],
) -> None:
    if staged_strategies:
        grouped: dict[StagedStrategy, list[tuple[Candidate, ScoredResult]]] = {}
        for candidate, scored in tell_batch:
            grouped.setdefault(owners[candidate.id], []).append((candidate, scored))
        for owner, owner_batch in grouped.items():
            owner.tell(owner_batch)
        return
    active_strategy.tell(tell_batch)


def _load_replay_events(path: Path) -> tuple[Mapping[str, Any], ...]:
    events: list[Mapping[str, Any]] = []
    previous_event_seq: int | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise StudyReplayError(f"{path.name}:{line_no} is not valid JSON") from exc
            if not isinstance(payload, Mapping):
                raise StudyReplayError(f"{path.name}:{line_no} is not a JSON object")
            event_kind = str(payload.get("event_kind", ""))
            replay_relevant = bool(payload.get("replay_relevant", False))
            if event_kind not in REPLAY_RELEVANT_EVENT_KINDS:
                if replay_relevant:
                    raise StudyReplayError(
                        f"unknown replay-relevant event kind {event_kind!r}"
                    )
                continue
            version = payload.get("event_version")
            if version != JOURNAL_EVENT_VERSION:
                raise StudyReplayError(
                    f"unsupported journal event_version {version!r} at {path.name}:{line_no}"
                )
            try:
                event_seq = int(payload["event_seq"])
            except (KeyError, TypeError, ValueError) as exc:
                raise StudyReplayError(
                    f"{path.name}:{line_no} missing integer event_seq"
                ) from exc
            if previous_event_seq is not None and event_seq <= previous_event_seq:
                raise StudyReplayError(
                    "journal event_seq must be strictly increasing: "
                    f"{event_seq} after {previous_event_seq} at {path.name}:{line_no}"
                )
            previous_event_seq = event_seq
            events.append(dict(payload))
    return tuple(events)


def _load_latest_strategy_state(path: Path) -> Mapping[str, Any] | None:
    if not path.is_file():
        return None
    latest: Mapping[str, Any] | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise StudyReplayError(f"{path.name}:{line_no} is not valid JSON") from exc
            if not isinstance(payload, Mapping):
                raise StudyReplayError(f"{path.name}:{line_no} is not a JSON object")
            latest = _mapping_or_empty(payload.get("strategy_state"))
    return latest


def _journal_batches(events: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    batches: list[dict[str, Any]] = []
    by_seq: dict[int, dict[str, Any]] = {}
    asked_keys: set[tuple[int, int]] = set()
    asked_ids: set[tuple[int, str]] = set()
    told_ids: set[tuple[int, str]] = set()
    for row in events:
        try:
            batch_seq = int(row["batch_seq"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StudyReplayError("journal event missing integer batch_seq") from exc
        batch = by_seq.get(batch_seq)
        if batch is None:
            batch = {"batch_seq": batch_seq, "asked": [], "told": []}
            by_seq[batch_seq] = batch
            batches.append(batch)
        event_kind = row.get("event_kind")
        if event_kind == "candidate_asked":
            try:
                ask_seq = int(row["ask_seq"])
            except (KeyError, TypeError, ValueError) as exc:
                raise StudyReplayError("journal ask event missing integer ask_seq") from exc
            candidate_id = str(row.get("candidate_id"))
            key = (batch_seq, ask_seq)
            id_key = (batch_seq, candidate_id)
            if key in asked_keys or id_key in asked_ids:
                raise StudyReplayError(
                    f"journal batch {batch_seq} has duplicate ask for {candidate_id!r}"
                )
            asked_keys.add(key)
            asked_ids.add(id_key)
            batch["asked"].append(row)
        elif event_kind == "candidate_evaluated":
            try:
                ask_seq = int(row["ask_seq"])
            except (KeyError, TypeError, ValueError) as exc:
                raise StudyReplayError("journal tell event missing integer ask_seq") from exc
            candidate_id = str(row.get("candidate_id"))
            key = (batch_seq, ask_seq)
            id_key = (batch_seq, candidate_id)
            if key not in asked_keys or id_key not in asked_ids:
                raise StudyReplayError(
                    f"journal tell for {candidate_id!r} appears before its ask"
                )
            if id_key in told_ids:
                raise StudyReplayError(
                    f"journal batch {batch_seq} has duplicate tell for {candidate_id!r}"
                )
            told_ids.add(id_key)
            batch["told"].append(row)
        else:
            raise StudyReplayError(f"unsupported journal event kind {event_kind!r}")
    return tuple(sorted(batches, key=lambda batch: batch["batch_seq"]))


def _journal_resume_position(
    events: Sequence[Mapping[str, Any]],
    *,
    pending_candidates: Sequence[Candidate],
) -> _JournalResumePosition:
    event_seq = 0
    ask_seq = 0
    tell_seq = 0
    batch_seq = 0
    ask_seq_by_id: dict[str, int] = {}
    pending_ids = {candidate.id for candidate in pending_candidates}
    pending_batches: set[int] = set()
    for row in events:
        event_seq = max(event_seq, int(row["event_seq"]))
        batch_seq = max(batch_seq, int(row["batch_seq"]))
        event_kind = row.get("event_kind")
        if event_kind == "candidate_asked":
            row_ask_seq = int(row["ask_seq"])
            ask_seq = max(ask_seq, row_ask_seq)
            candidate_id = str(row.get("candidate_id"))
            ask_seq_by_id[candidate_id] = row_ask_seq
            if candidate_id in pending_ids:
                pending_batches.add(int(row["batch_seq"]))
        elif event_kind == "candidate_evaluated":
            tell_seq = max(tell_seq, int(row.get("tell_seq", 0)))
    if pending_ids:
        missing_ids = sorted(pending_ids - set(ask_seq_by_id))
        if missing_ids:
            raise StudyReplayError(f"study resume pending asks missing rows: {missing_ids}")
        if len(pending_batches) != 1:
            raise StudyReplayError(
                "study resume pending asks must belong to exactly one batch"
            )
    return _JournalResumePosition(
        event_seq=event_seq,
        ask_seq=ask_seq,
        tell_seq=tell_seq,
        batch_seq=batch_seq,
        pending_batch_seq=next(iter(pending_batches)) if pending_batches else None,
        ask_seq_by_id=ask_seq_by_id,
    )


def _assert_replayed_candidates_match(
    ask_rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Candidate],
) -> None:
    if len(ask_rows) != len(candidates):
        raise StudyReplayError(
            f"strategy ask count mismatch: journal={len(ask_rows)} replay={len(candidates)}"
        )
    for row, candidate in zip(ask_rows, candidates, strict=True):
        expected = _mapping_or_empty(row.get("candidate"))
        expected_id = str(row.get("candidate_id"))
        if candidate.id != expected_id:
            raise StudyReplayError(
                f"candidate_id replay mismatch: {candidate.id!r} != {expected_id!r}"
            )
        expected_patch = expected.get("patch_canonical_json")
        if candidate.patch.canonical_json() != expected_patch:
            raise StudyReplayError(f"candidate patch replay mismatch: {candidate.id!r}")
        expected_metadata = _jsonable_value(expected.get("metadata", {}))
        if _jsonable_value(candidate.metadata) != expected_metadata:
            raise StudyReplayError(f"candidate metadata replay mismatch: {candidate.id!r}")


def _candidate_journal_payload(candidate: Candidate, schema: RecipeSchema) -> Mapping[str, Any]:
    return {
        "id": candidate.id,
        "patch": _jsonable_value(candidate.patch.to_nested()),
        "patch_canonical_json": candidate.patch.canonical_json(),
        "recipe_id": candidate.patch.recipe_id(schema),
        "metadata": _jsonable_value(candidate.metadata),
    }


def _owner_strategy_payload(owner: StagedStrategy | None) -> Mapping[str, Any] | None:
    if owner is None:
        return None
    return {
        "class": type(owner).__name__,
        "name": owner.name,
        "seed": owner.seed,
        "topology_id": owner.topology.id,
    }


def _scored_result_journal_payload(scored: ScoredResult) -> Mapping[str, Any]:
    return {
        "candidate_id": scored.candidate_id,
        "eval_spec": (
            _serialize_eval_spec(scored.eval_spec)
            if scored.eval_spec is not None
            else None
        ),
        "cache_key": scored.cache_key,
        "feasible": bool(scored.feasible),
        "failure_category": (
            scored.failure_category.value if scored.failure_category is not None else None
        ),
        "objectives": _serialize_objectives(scored.objectives),
        "feasibility_margins": _serialize_margins(scored.feasibility_margins),
        "failing_gates": list(scored.failing_gates),
        "run_reference": _serialize_run_reference(scored.run_reference),
        "result_blob": _result_blob(scored),
        "notes": list(scored.notes),
    }


def _assert_journal_objectives_consistent(
    row: Mapping[str, Any],
    scored_payload: Mapping[str, Any],
) -> None:
    if "objectives" not in row:
        return
    if _json_dump_value(row.get("objectives")) != _json_dump_value(
        scored_payload.get("objectives", ())
    ):
        raise StudyReplayError("journal objectives mismatch with scored_result")


def _scored_result_from_journal_payload(payload: Mapping[str, Any]) -> ScoredResult:
    failure_category = payload.get("failure_category")
    failure = FailureCategory(failure_category) if failure_category is not None else None
    objectives = _deserialize_objectives(
        _sequence_of_mappings(payload.get("objectives", ()))
    )
    return ScoredResult(
        candidate_id=(
            str(payload["candidate_id"]) if payload.get("candidate_id") is not None else None
        ),
        eval_spec=(
            _deserialize_eval_spec(_mapping_or_empty(payload.get("eval_spec")))
            if payload.get("eval_spec") is not None
            else None
        ),
        cache_key=str(payload["cache_key"]) if payload.get("cache_key") is not None else None,
        feasible=bool(payload.get("feasible", False)),
        failure_category=failure,
        objectives=objectives if bool(payload.get("feasible", False)) else None,
        feasibility_margins=_deserialize_margins(
            _mapping_of_mappings(payload.get("feasibility_margins", {}))
        ),
        failing_gates=tuple(str(item) for item in payload.get("failing_gates", ())),
        run_reference=_deserialize_run_reference(
            _mapping_or_none(payload.get("run_reference")),
            payload.get("result_blob"),
        ),
        notes=tuple(str(item) for item in payload.get("notes", ())),
    )


def _strategy_replay_state(strategy: Strategy, *staged_strategies: Strategy) -> Mapping[str, Any]:
    strategies = (strategy, *staged_strategies) if staged_strategies else (strategy,)
    return {
        "strategies": [
            _single_strategy_replay_state(item, index=index)
            for index, item in enumerate(strategies)
        ]
    }


def _single_strategy_replay_state(strategy: Strategy, *, index: int) -> Mapping[str, Any]:
    results = tuple(getattr(strategy, "results", ()))
    payload: dict[str, Any] = {
        "index": index,
        "class": type(strategy).__name__,
        "name": getattr(strategy, "name", type(strategy).__name__),
        "seed": getattr(strategy, "seed", None),
        "ask_cursor": _strategy_ask_cursor(strategy),
        "tell_count": getattr(strategy, "tell_count", len(results)),
        "results": [
            {
                "candidate_id": candidate.id,
                "cache_key": scored.cache_key,
                "feasible": bool(scored.feasible),
                "failure_category": (
                    scored.failure_category.value
                    if scored.failure_category is not None
                    else None
                ),
                "objectives": _serialize_objectives(scored.objectives),
            }
            for candidate, scored in results
        ],
    }
    if isinstance(strategy, StagedStrategy):
        payload.update(
            {
                "stage_index": getattr(strategy, "_stage_index", None),
                "mode": getattr(strategy, "_mode", None),
                "pending_ids": [candidate.id for candidate in getattr(strategy, "_pending", ())],
                "expected_stage_ids": sorted(getattr(strategy, "_expected_stage_ids", ())),
                "archive_ids": [
                    member.candidate.id for member in getattr(strategy, "_archive", ())
                ],
                "archive_cache_keys": [
                    member.scored.cache_key for member in getattr(strategy, "_archive", ())
                ],
                "frontier_cache_keys": [
                    getattr(node, "cache_key", None)
                    for node in getattr(strategy, "_frontier", ())
                ],
                "backward_passes_completed": getattr(
                    strategy, "backward_passes_completed", None
                ),
                "joint_refines_completed": getattr(
                    strategy, "joint_refines_completed", None
                ),
                "topology_id": strategy.topology.id,
            }
        )
    return _jsonable_value(payload)


def _strategy_ask_cursor(strategy: Strategy) -> int | None:
    if hasattr(strategy, "_asked"):
        return int(getattr(strategy, "_asked"))
    planned_by_id = getattr(strategy, "_planned_by_id", None)
    if isinstance(planned_by_id, Mapping):
        return len(planned_by_id)
    asked_by_id = getattr(strategy, "_asked_by_id", None)
    if isinstance(asked_by_id, Mapping):
        return len(asked_by_id)
    return None


def _result_status(scored: ScoredResult) -> str:
    if scored.run_reference is not None:
        return str(scored.run_reference.status)
    if scored.feasible:
        return "ok"
    if scored.failure_category is not None:
        return scored.failure_category.value
    return "unknown"


def _assert_replay_manifest_current(
    manifest: Mapping[str, Any],
    *,
    schema: RecipeSchema,
    search_space_identity: Mapping[str, Any],
) -> tuple[str, ...]:
    stored = _manifest_epoch_identity(manifest)
    current = {
        "code_version": current_code_version(),
        "corpus_version": current_corpus_version(),
        "allowlist_version": schema.allowlist_version,
        "bounds_digest": search_space_identity.get("bounds_digest"),
    }
    mismatches = [
        key for key in EPOCH_IDENTITY_KEYS if stored.get(key) != current.get(key)
    ]
    if mismatches:
        details = ", ".join(
            f"{key}: stored={stored.get(key)!r} current={current.get(key)!r}"
            for key in mismatches
        )
        raise StudyManifestMismatchError(f"study replay identity mismatch: {details}")
    strategy_name = _manifest_strategy_class(manifest)
    warnings_seen: list[str] = []
    if strategy_name in {"StagedStrategy", "RandomStrategy"}:
        stored_sampler = manifest.get("sampler_name")
        current_sampler = active_sampler_name()
        if stored_sampler != current_sampler:
            raise StudyManifestMismatchError(
                "study replay sampler mismatch: "
                f"stored={stored_sampler!r} current={current_sampler!r}"
            )
        if str(stored_sampler).startswith("scipy"):
            stored_scipy = manifest.get("scipy_version")
            current_scipy = _package_version("scipy")
            if stored_scipy != current_scipy:
                raise StudyManifestMismatchError(
                    "study replay SciPy mismatch: "
                    f"stored={stored_scipy!r} current={current_scipy!r}"
                )
    elif strategy_name in {"OptunaTPEStrategy", "OptunaNSGA2Strategy"}:
        stored_optuna = manifest.get("optuna_version")
        current_optuna = _package_version("optuna")
        if stored_optuna != current_optuna:
            raise StudyManifestMismatchError(
                "study replay Optuna mismatch: "
                f"stored={stored_optuna!r} current={current_optuna!r}"
            )
    return tuple(warnings_seen)


def _assert_resume_manifest_matches_invocation(
    manifest: Mapping[str, Any],
    *,
    config: StudyConfig,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    search_space_identity: Mapping[str, Any],
    strategy: Strategy,
) -> None:
    mismatches: list[str] = []

    def note(key: str, stored: Any, current: Any) -> None:
        if stored != current:
            mismatches.append(f"{key}: stored={stored!r} current={current!r}")

    note("feedstock_id", manifest.get("feedstock_id"), feedstock)
    note(
        "fidelity",
        canonical_backend_name(str(manifest.get("fidelity"))),
        canonical_backend_name(fidelity),
    )
    note("seed", int(manifest.get("seed", 0)), int(config.seed))
    note("budget", int(manifest.get("budget", 0)), int(config.budget))
    note("parallel", int(manifest.get("parallel", 1)), int(config.parallel))
    stored_profile = _mapping_or_empty(manifest.get("profile"))
    note("profile.content_hash", stored_profile.get("content_hash"), _profile_content_hash(profile))
    note(
        "search_space_identity",
        _jsonable_value(_mapping_or_empty(manifest.get("search_space_identity"))),
        _jsonable_value(search_space_identity),
    )
    note(
        "strategy.class",
        _manifest_strategy_class(manifest),
        _canonical_strategy_class_name(_strategy_label(strategy)),
    )
    if mismatches:
        raise StudyReplayError("study resume config mismatch: " + "; ".join(mismatches))


def _strategy_manifest_warnings(
    manifest: Mapping[str, Any],
    strategy: Strategy,
) -> tuple[str, ...]:
    expected_class = _manifest_strategy_class(manifest)
    actual_class = type(strategy).__name__
    if expected_class and expected_class != actual_class:
        raise StudyManifestMismatchError(
            f"strategy class mismatch: stored={expected_class!r} current={actual_class!r}"
        )
    if expected_class in {"OptunaTPEStrategy", "OptunaNSGA2Strategy"}:
        stored_config = _mapping_or_empty(_mapping_or_empty(manifest.get("strategy")).get("config"))
        current_config = _strategy_config_payload(
            strategy,
            config=None,
            sampler_name=_resolved_strategy_sampler(strategy),
        )
        config_mismatches = [
            key
            for key in ("strategy", "seed", "sampler_name")
            if stored_config.get(key) != current_config.get(key)
        ]
        if config_mismatches:
            details = ", ".join(
                f"{key}: stored={stored_config.get(key)!r} current={current_config.get(key)!r}"
                for key in config_mismatches
            )
            raise StudyManifestMismatchError(f"optuna strategy config mismatch: {details}")
    return ()


def _manifest_epoch_identity(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    search_identity = _mapping_or_empty(manifest.get("search_space_identity"))
    return {
        "code_version": manifest.get("code_version"),
        "corpus_version": manifest.get("corpus_version"),
        "allowlist_version": manifest.get("allowlist_version"),
        "bounds_digest": search_identity.get("bounds_digest"),
    }


def _strategy_key_from_manifest(manifest: Mapping[str, Any]) -> str:
    strategy_payload = _mapping_or_empty(manifest.get("strategy"))
    config = _mapping_or_empty(strategy_payload.get("config"))
    raw = config.get("strategy") or strategy_payload.get("name") or strategy_payload.get("class")
    aliases = {
        "RandomStrategy": "random",
        "random": "random",
        "MorrisScreenStrategy": "screen",
        "morris-screen": "screen",
        "screen": "screen",
        "StagedStrategy": "staged",
        "staged": "staged",
        "OptunaTPEStrategy": "bayes",
        "optuna-tpe": "bayes",
        "bayes": "bayes",
        "OptunaNSGA2Strategy": "nsga2",
        "optuna-nsga2": "nsga2",
        "nsga2": "nsga2",
    }
    try:
        return aliases[str(raw)]
    except KeyError as exc:
        raise StudyReplayError(f"unsupported replay strategy {raw!r}") from exc


def _manifest_strategy_class(manifest: Mapping[str, Any]) -> str | None:
    strategy_payload = _mapping_or_empty(manifest.get("strategy"))
    raw = strategy_payload.get("class") or strategy_payload.get("name")
    if raw is None:
        return None
    return _canonical_strategy_class_name(raw)


def _canonical_strategy_class_name(value: Any) -> str:
    aliases = {
        "random": "RandomStrategy",
        "screen": "MorrisScreenStrategy",
        "morris-screen": "MorrisScreenStrategy",
        "staged": "StagedStrategy",
        "bayes": "OptunaTPEStrategy",
        "optuna-tpe": "OptunaTPEStrategy",
        "nsga2": "OptunaNSGA2Strategy",
        "optuna-nsga2": "OptunaNSGA2Strategy",
    }
    text = str(value)
    return aliases.get(text, text)


def _load_json_object_file(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyReplayError(f"could not read {path.name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StudyReplayError(f"{path.name} is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise StudyReplayError(f"{path.name} must be a JSON object")
    return dict(payload)


def _load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyReplayError(f"could not read {path.name}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise StudyReplayError(f"{path.name} must be a mapping")
    return dict(payload)


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    return _mapping_or_empty(value)


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    result: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise StudyReplayError("journal payload expected a sequence of mappings")
        result.append(dict(item))
    return tuple(result)


def _mapping_of_mappings(value: Any) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(item, Mapping):
            raise StudyReplayError("journal payload expected a mapping of mappings")
        result[str(key)] = dict(item)
    return result


def _staged_topology_seed(
    base_seed: int,
    topology: TopologyChoice,
    topologies: Sequence[TopologyChoice],
) -> int:
    """Derive a deterministic Sobol stream seed for multi-topology staged runs."""
    if len(topologies) <= 1:
        return base_seed
    digest = hashlib.blake2s(topology.id.encode("utf-8"), digest_size=4).digest()
    topology_offset = int.from_bytes(digest, "big") + 1
    return (base_seed + topology_offset) % (2**32)


def _two_phase_certification_pool(
    *,
    leaderboard: Sequence[StudyRecord],
    pareto: Sequence[StudyRecord],
    top_k: int,
    include_pareto_extras: bool,
) -> tuple[StudyRecord, ...]:
    """Return scalar top-K, plus Pareto-front extras for multi-objective profiles."""
    selected: list[StudyRecord] = []
    seen: set[str] = set()
    pareto_extras = pareto if include_pareto_extras else ()
    for record in (*leaderboard[:top_k], *pareto_extras):
        if record.candidate_id in seen:
            continue
        selected.append(record)
        seen.add(record.candidate_id)
    if not selected:
        return ()
    limit = min(
        len(leaderboard),
        max(top_k, top_k * _TWO_PHASE_CERTIFICATION_POOL_MAX_MULTIPLIER),
    )
    if limit <= 0:
        limit = len(selected)
    if len(selected) > limit:
        truncated = selected[limit:]
        _LOGGER.warning(
            "two_phase_certification_pool_truncated top_k=%s limit=%s pareto=%s "
            "union=%s truncated_candidate_ids=%s",
            top_k,
            limit,
            len(pareto_extras),
            len(selected),
            ",".join(record.candidate_id for record in truncated),
        )
        return tuple(selected[:limit])
    return tuple(selected)


def _certification_pool_limit(
    top_k: int,
    *,
    definitions: Sequence[ObjectiveDefinition],
) -> int:
    if len(definitions) <= 1:
        return top_k
    return max(top_k, top_k * _TWO_PHASE_CERTIFICATION_POOL_MAX_MULTIPLIER)


def _disagreement_aggregate(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"max": 0.0, "p95": 0.0}
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return {"max": ordered[-1], "p95": ordered[index]}


def _run_exact_certification(
    certification_pool: Sequence[StudyRecord],
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
    requested_top_k: int,
) -> _CertificationPassResult:
    if not certification_pool:
        raise StudyNoFeasibleError("two-phase certification received no explore candidates")
    primary_metric = _primary_objective_metric(definitions)
    explore_by_id = {record.candidate_id: record for record in records}
    certification_rows: list[dict[str, Any]] = []
    certified_records: list[StudyRecord] = []
    replay_code_version: str | None = None
    replay_data_digests: Mapping[str, str] | None = None

    for explore_record in certification_pool:
        candidate = _certification_candidate_from_record(explore_record)
        results, _ = _evaluate_candidates(
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
        "top_k": requested_top_k,
        "certification_pool_size": len(certification_pool),
        "certification_pool_limit": _certification_pool_limit(
            requested_top_k,
            definitions=definitions,
        ),
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


def _certification_candidate_from_record(record: StudyRecord) -> Candidate:
    metadata = dict(record.search_provenance)
    metadata["proposal_source"] = record.proposal_source
    metadata["seed_lineage"] = record.seed_lineage
    if "topology_id" in metadata and "topology" not in metadata:
        metadata["topology"] = {"id": metadata["topology_id"]}
    return Candidate(id=record.candidate_id, patch=record.patch, metadata=metadata)


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
    conditional_digests = [
        c5_sampler_context(schema, active=active).conditional_subspace_digest
        for active in (False, True)
    ]
    return MappingProxyType(
        {
            "recipe_schema_version": schema.recipe_schema_version,
            "allowlist_version": schema.allowlist_version,
            "bounds_digest": schema.bounds_digest,
            "profile_pinned_paths": list(profile_pinned_paths),
            "cli_pinned_paths": list(cli_pinned_paths),
            "resolved_pinned_paths": [
                ".".join(path) for path in getattr(schema, "pinned_paths", ())
            ],
            "search_knob_paths": [".".join(spec.path) for spec in schema.search_allowlist],
            "conditional_allocation_version": "optimizer-conditional-subspace-v1",
            "conditional_subspace_digests": conditional_digests,
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
        validated = validate_profile(
            resolved,
            expected_feedstock=expected_feedstock,
            source=f"<profile:{profile}>",
            schema=schema,
        )
        _requested_staged_topologies(validated, None)
        return validated
    if not isinstance(profile, Mapping):
        raise TypeError("profile must be a profile name or mapping")
    validated = validate_profile(
        profile,
        expected_feedstock=expected_feedstock,
        source=getattr(profile, "source", "<profile>"),
        schema=schema,
    )
    _requested_staged_topologies(validated, None)
    return validated


def resolve_strategy(
    strategy: str | Strategy,
    *,
    profile: Mapping[str, Any],
    seed: int,
    schema: RecipeSchema,
    warm_start_seeds: Sequence[WarmStartSeed] = (),
) -> Strategy:
    if not isinstance(strategy, str):
        return strategy
    if strategy == "random":
        return RandomStrategy(schema, seed=seed)
    if strategy == "screen":
        return MorrisScreenStrategy(schema, seed=seed)
    if strategy == "staged":
        return StagedStrategy(
            schema,
            seed=seed,
            objective_profile=profile,
            stage0_seed_candidates=warm_start_seeds,
        )
    if strategy == "bayes":
        from simulator.optimize.strategy import OptunaTPEStrategy

        return OptunaTPEStrategy(
            schema,
            seed=seed,
            objective_profile=profile,
            warm_start_seeds=warm_start_seeds,
        )
    if strategy == "nsga2":
        from simulator.optimize.strategy import OptunaNSGA2Strategy

        return OptunaNSGA2Strategy(
            schema,
            seed=seed,
            objective_profile=profile,
            warm_start_seeds=warm_start_seeds,
        )
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


def _validate_multi_topology_seed_recipes(
    profile: Mapping[str, Any],
    requested_topologies: Sequence[TopologyChoice] | None,
) -> None:
    if requested_topologies is None or len(requested_topologies) <= 1:
        return
    requested_ids = {topology.id for topology in requested_topologies}
    for index, seed in enumerate(profile.get("seed_recipes", ()) or ()):
        if not isinstance(seed, Mapping):
            continue
        topology_id = _seed_topology_id(seed)
        if topology_id is None:
            raise ProfileValidationError(
                f"profile {profile_id(profile)!r}: seed_recipes[{index}] requires "
                "topology in multi-topology staged profiles"
            )
        if topology_id not in requested_ids:
            raise ProfileValidationError(
                f"profile {profile_id(profile)!r}: seed_recipes[{index}] topology "
                f"{topology_id!r} is not requested"
            )


def _resolve_warm_start_source(
    profile: Mapping[str, Any],
    override: str | Path | Mapping[str, Any] | None,
    *,
    current_store_path: Path,
) -> _WarmStartSource | None:
    raw = override if override is not None else profile.get("warm_start_from")
    if raw is None:
        return None
    base_dir = _warm_start_base_dir(profile, from_profile=override is None)
    store_path, pareto_path = _warm_start_paths(raw, base_dir=base_dir)
    current_resolved = current_store_path.expanduser().resolve()
    if store_path.expanduser().resolve() == current_resolved:
        raise StudyError(
            "warm_start_from must point at an explicit prior run, not the current output store"
        )
    if not store_path.is_file():
        raise StudyError(f"warm_start_from store missing: {store_path}")
    if not pareto_path.is_file():
        raise StudyError(f"warm_start_from pareto artifact missing: {pareto_path}")
    return _WarmStartSource(ResultStore(store_path), pareto_path)


def _warm_start_base_dir(
    profile: Mapping[str, Any],
    *,
    from_profile: bool,
) -> Path:
    if from_profile:
        source = getattr(profile, "source", None)
        if isinstance(source, str) and source and not source.startswith("<"):
            return Path(source).expanduser().resolve().parent
    return Path.cwd()


def _warm_start_paths(
    raw: str | Path | Mapping[str, Any],
    *,
    base_dir: Path,
) -> tuple[Path, Path]:
    if isinstance(raw, Path):
        return _infer_warm_start_paths(_resolve_warm_start_path(raw, base_dir))
    if isinstance(raw, str):
        return _infer_warm_start_paths(_resolve_warm_start_path(Path(raw), base_dir))
    if not isinstance(raw, Mapping):
        raise StudyError("warm_start_from must be a path string or mapping")

    store_raw = raw.get("store_path", raw.get("store"))
    pareto_raw = raw.get("pareto_path", raw.get("pareto", raw.get("artifact_path", raw.get("artifact"))))
    path_raw = raw.get("path", raw.get("run_dir"))
    if store_raw is None and pareto_raw is None and path_raw is None:
        raise StudyError(
            "warm_start_from must name a prior run dir, cache.sqlite, or pareto.json"
        )
    if path_raw is not None:
        inferred_store, inferred_pareto = _infer_warm_start_paths(
            _resolve_warm_start_path(Path(str(path_raw)), base_dir)
        )
    else:
        inferred_store = inferred_pareto = None
    store_path = (
        _resolve_warm_start_path(Path(str(store_raw)), base_dir)
        if store_raw is not None
        else inferred_store
    )
    pareto_path = (
        _resolve_warm_start_path(Path(str(pareto_raw)), base_dir)
        if pareto_raw is not None
        else inferred_pareto
    )
    if store_path is None or pareto_path is None:
        raise StudyError(
            "warm_start_from mapping must resolve both cache.sqlite and pareto.json"
        )
    return store_path, pareto_path


def _resolve_warm_start_path(path: Path, base_dir: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def _infer_warm_start_paths(path: Path) -> tuple[Path, Path]:
    if path.name == "cache.sqlite":
        return path, path.parent / "pareto.json"
    if path.name == "pareto.json":
        return path.parent / "cache.sqlite", path
    return path / "cache.sqlite", path / "pareto.json"


def _resolve_warm_start_seeds(
    profile: Mapping[str, Any],
    *,
    feedstock: str,
    fidelity: str,
    schema: RecipeSchema,
    warm_start_source: _WarmStartSource | None,
    constraints: Any,
    search_space_identity: Mapping[str, Any],
    requested_topologies: Sequence[TopologyChoice] | None,
) -> tuple[WarmStartSeed, ...]:
    seeds = [
        *_store_warm_start_seeds(
            profile,
            feedstock=feedstock,
            fidelity=fidelity,
            schema=schema,
            warm_start_source=warm_start_source,
            constraints=constraints,
            search_space_identity=search_space_identity,
            requested_topologies=requested_topologies,
        ),
        *_profile_warm_start_seeds(profile, schema=schema),
    ]
    unique: list[WarmStartSeed] = []
    seen: set[tuple[str | None, str]] = set()
    for seed in seeds:
        key = (seed.topology_id, seed.patch.canonical_json())
        if key in seen:
            continue
        seen.add(key)
        unique.append(seed)
    return tuple(unique)


def _profile_warm_start_seeds(
    profile: Mapping[str, Any],
    *,
    schema: RecipeSchema,
) -> tuple[WarmStartSeed, ...]:
    seed_rows = tuple(profile.get("seed_recipes", ()) or ())
    if seed_rows:
        _warn_profile_seed_epoch_mismatch(profile)
    seeds: list[WarmStartSeed] = []
    for index, seed in enumerate(seed_rows):
        if not isinstance(seed, Mapping):
            continue
        # Profile seeds are advisory recipe candidates. They never inject a cached
        # score; each seed is re-evaluated through the normal pipeline.
        patch = RecipePatch.from_nested(seed["patch"]).validated(schema)
        seed_id = str(seed["id"])
        source_campaigns = seed.get("source_campaigns")
        if source_campaigns is None and seed.get("source_campaign") is not None:
            source_campaigns = [seed.get("source_campaign")]
        seeds.append(
            WarmStartSeed(
                id=seed_id,
                patch=patch,
                proposal_source="seed_recipe",
                topology_id=_seed_topology_id(seed),
                origin={
                    "kind": "profile_seed",
                    "profile": profile_id(profile),
                    "seed_index": index,
                    "seed_id": seed_id,
                    "source_campaigns": list(source_campaigns or ()),
                },
            )
        )
    return tuple(seeds)


def _warn_profile_seed_epoch_mismatch(profile: Mapping[str, Any]) -> None:
    mismatches: list[str] = []
    stamped_code = profile.get("code_version")
    if isinstance(stamped_code, str) and stamped_code != current_code_version():
        mismatches.append(
            f"code_version profile={stamped_code!r} current={current_code_version()!r}"
        )
    stamped_corpus = profile.get("corpus_version")
    if isinstance(stamped_corpus, str) and stamped_corpus != current_corpus_version():
        mismatches.append(
            f"corpus_version profile={stamped_corpus!r} current={current_corpus_version()!r}"
        )
    if mismatches:
        _LOGGER.warning(
            "profile_seed_epoch_warning profile=%s stale advisory seed_recipes: %s",
            profile_id(profile),
            "; ".join(mismatches),
        )


def _store_warm_start_seeds(
    profile: Mapping[str, Any],
    *,
    feedstock: str,
    fidelity: str,
    schema: RecipeSchema,
    warm_start_source: _WarmStartSource | None,
    constraints: Any,
    search_space_identity: Mapping[str, Any],
    requested_topologies: Sequence[TopologyChoice] | None,
) -> tuple[WarmStartSeed, ...]:
    if warm_start_source is None:
        return ()
    store = warm_start_source.store
    pareto_path = warm_start_source.pareto_path
    try:
        payload = json.loads(pareto_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudyError(f"could not read warm-start artifact {pareto_path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise StudyError(f"warm-start artifact {pareto_path} must be a JSON object")
    pareto_rows = payload.get("pareto")
    if not isinstance(pareto_rows, list) or not pareto_rows:
        return ()
    persisted_identity = payload.get("search_space_identity")
    if persisted_identity is None:
        raise StudyError(
            f"warm-start artifact {pareto_path} missing search_space_identity"
        )
    if _jsonable_value(persisted_identity) != _jsonable_value(search_space_identity):
        raise StudyError(
            "warm-start search_space_identity mismatch; stale seeds rejected"
        )

    requested_ids = (
        {topology.id for topology in requested_topologies}
        if requested_topologies is not None and len(requested_topologies) > 1
        else None
    )
    seeds: list[WarmStartSeed] = []
    for index, record in enumerate(pareto_rows):
        if not isinstance(record, Mapping):
            continue
        provenance = record.get("search_provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        topology_id = _record_topology_id(provenance)
        if requested_ids is not None:
            if topology_id is None:
                continue
            if topology_id not in requested_ids:
                continue
        patch_payload = record.get("optimizer_patch", record.get("patch"))
        cache_key_value = record.get("cache_key")
        if not isinstance(patch_payload, Mapping) or not isinstance(cache_key_value, str):
            continue
        cached = store.fetch(cache_key_value)
        if cached is None:
            raise StudyError(
                f"warm-start seed {record.get('candidate_id')!r} missing store row "
                "or stale-corpus store row; stale-corpus seeds rejected"
            )
        if cached.eval_spec is None:
            raise StudyError(
                f"warm-start seed {record.get('candidate_id')!r} missing eval_spec"
            )
        stored_cache_key = cache_key(cached.eval_spec)
        if stored_cache_key != cache_key_value:
            raise StudyError(
                f"warm-start seed {record.get('candidate_id')!r} corrupt cache_key: "
                f"artifact has {cache_key_value!r}, eval_spec derives {stored_cache_key!r}"
            )
        if not _is_certified_cache_state(_cache_state_from_scored(cached)):
            continue
        patch = RecipePatch.from_nested(patch_payload).validated(schema)
        try:
            current_spec, _ = _build_eval_inputs(
                patch,
                feedstock,
                fidelity,
                profile,
                schema,
                constraints=constraints,
                conditional_context=_full_conditional_context_from_metadata(provenance),
            )
        except ProfileValidationError as exc:
            if _is_stale_profile_refusal(exc):
                raise StudyError(
                    f"warm-start seed {record.get('candidate_id')!r} is stale: {exc}"
                ) from exc
            raise
        current_identity = _evalspec_seed_identity(current_spec)
        stored_identity = _evalspec_seed_identity(cached.eval_spec)
        if current_spec.recipe_id != cached.eval_spec.recipe_id:
            raise StudyError(
                f"warm-start recipe_id mismatch for {record.get('candidate_id')!r}: "
                f"patch derives {current_spec.recipe_id!r}, "
                f"store has {cached.eval_spec.recipe_id!r}"
            )
        current_cache_key = cache_key(current_spec)
        if current_cache_key != cache_key_value:
            raise StudyError(
                f"warm-start cache_key mismatch for {record.get('candidate_id')!r}: "
                f"patch derives {current_cache_key!r}, artifact has {cache_key_value!r}"
            )
        if current_identity != stored_identity:
            diff_keys = ", ".join(_identity_diff_keys(current_identity, stored_identity))
            raise StudyError(
                f"warm-start EvalSpec identity mismatch for "
                f"{record.get('candidate_id')!r}: {diff_keys or 'unknown'}"
            )
        if result_scope_json(current_spec) != result_scope_json(cached.eval_spec):
            raise StudyError(
                f"warm-start result_scope mismatch for {record.get('candidate_id')!r}"
            )
        source_id = str(record.get("candidate_id") or cache_key_value[:12])
        seeds.append(
            WarmStartSeed(
                id=f"store-{source_id}",
                patch=patch,
                proposal_source="store_warm_start",
                topology_id=topology_id,
                origin={
                    "kind": "store_warm_start",
                    "source_candidate_id": record.get("candidate_id"),
                    "source_cache_key": cache_key_value,
                    "source_pareto_index": index,
                    "source_artifact": str(pareto_path),
                },
            )
        )
    return tuple(seeds)


def _seed_topology_id(seed: Mapping[str, Any]) -> str | None:
    raw = seed.get("topology", seed.get("topology_id"))
    if raw is None:
        return None
    try:
        return enumerate_topologies([raw])[0].id
    except (TypeError, ValueError) as exc:
        raise ProfileValidationError(f"invalid seed topology {raw!r}: {exc}") from exc


def _record_topology_id(provenance: Mapping[str, Any]) -> str | None:
    topology_id = provenance.get("topology_id")
    if isinstance(topology_id, str) and topology_id:
        return topology_id
    topology = provenance.get("topology")
    if isinstance(topology, Mapping):
        value = topology.get("id")
        if isinstance(value, str) and value:
            return value
    return None


def _evalspec_seed_identity(spec: Any) -> Mapping[str, Any]:
    payload = json.loads(canonical_evalspec_json(spec).decode("utf-8"))
    if not isinstance(payload, dict):
        raise StudyError("canonical EvalSpec payload must be a JSON object")
    return payload


def _identity_diff_keys(
    current: Mapping[str, Any],
    stored: Mapping[str, Any],
) -> tuple[str, ...]:
    keys = sorted(set(current) | set(stored))
    return tuple(key for key in keys if current.get(key) != stored.get(key))


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
) -> tuple[tuple[tuple[Candidate, ScoredResult, bool], ...], int]:
    results: list[tuple[Candidate, ScoredResult, bool] | None] = [None] * len(candidates)
    misses: list[tuple[int, Candidate]] = []
    staged_prefixes: dict[str, ScoredResult] = {}
    prefix_evals_run = 0
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
            prefix, prefix_eval_ran = _ensure_staged_prefix_replay(
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
            prefix_evals_run += int(prefix_eval_ran)
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
            conditional_context = _full_evaluation_conditional_context(candidate)
            evaluator_kwargs: dict[str, Any] = {}
            if conditional_context is not None:
                evaluator_kwargs["conditional_context"] = conditional_context
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
                        conditional_context=_full_evaluation_conditional_context(
                            candidate
                        ),
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
    return completed, prefix_evals_run


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
) -> tuple[ScoredResult | None, bool]:
    if not _is_staged_candidate(candidate):
        return None, False
    prefix_depth = candidate.metadata.get("prefix_depth", 0)
    if not isinstance(prefix_depth, int):
        raise StagedBeamStateError("staged prefix_depth metadata must be an int")
    if prefix_depth <= 0:
        return None, False

    prefix_stage_ids = _string_tuple_metadata(candidate, "prefix_stage_ids")
    stage_index = candidate.metadata.get("stage_index")
    if (
        isinstance(stage_index, bool)
        or not isinstance(stage_index, int)
        or stage_index != prefix_depth
    ):
        raise StagedBeamStateError(
            "staged prefix_depth does not match candidate stage_index"
        )
    authoritative_prefix = authoritative_prefix_stage_ids(
        schema,
        profile,
        _topology_id_metadata(candidate),
        prefix_depth,
    )
    if prefix_stage_ids != authoritative_prefix:
        raise StagedBeamStateError(
            "staged prefix stage IDs do not match the authoritative topology stage table"
        )
    prefix_patch = _prefix_patch_from_metadata(candidate, schema)
    context = conditional_context_from_metadata(candidate.metadata)
    if context is not None and context.scope == "prefix-before-guard-stage":
        context = c5_sampler_context(
            schema,
            active=True,
            scope="prefix-before-guard-stage",
            prefix_stage_ids=prefix_stage_ids,
        )
        prefix_spec, _ = _build_prefix_eval_inputs(
            prefix_patch,
            feedstock,
            fidelity,
            profile,
            schema,
            prefix_stage_ids=prefix_stage_ids,
            prefix_recipe_ids=_string_tuple_metadata(candidate, "prefix_recipe_ids"),
            topology_id=_topology_id_metadata(candidate),
            constraints=constraints,
            conditional_context=context,
        )
    else:
        base_spec, _ = _build_eval_inputs(
            prefix_patch,
            feedstock,
            fidelity,
            profile,
            schema,
            constraints=constraints,
            conditional_context=context,
        )
        prefix_spec = make_prefix_eval_spec(
            base_spec,
            prefix_stage_ids=prefix_stage_ids,
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
        return cached, False

    cached = store.lookup(prefix_spec)
    if cached is not None:
        prefix_replay_cache[prefix_key] = cached
        return cached, False

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
        return fresh, True
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
    return cached, True


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


def _full_evaluation_conditional_context(
    candidate: Candidate,
):
    return _full_conditional_context_from_metadata(candidate.metadata)


def _full_conditional_context_from_metadata(metadata: Mapping[str, Any]):
    context = conditional_context_from_metadata(metadata)
    if context is not None and context.scope == "prefix-before-guard-stage":
        return None
    return context


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
            conditional_context=_full_evaluation_conditional_context(candidate),
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
    conditional_context = _full_evaluation_conditional_context(candidate)
    evaluator_kwargs: dict[str, Any] = {}
    if conditional_context is not None:
        evaluator_kwargs["conditional_context"] = conditional_context
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
                conditional_context=conditional_context,
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
            conditional_context=_full_evaluation_conditional_context(candidate),
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
                conditional_context=_full_evaluation_conditional_context(candidate),
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
    if (
        status is None
        and getattr(getattr(scored, "eval_spec", None), "backend_name", None)
        == ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    ):
        return "unavailable"
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
        backend_name=reference.backend_name,
        backend_status=_result_backend_status(scored),
        backend_authoritative=reference.backend_authoritative,
        evidence_class=reference.evidence_class,
        cache_state=reference.cache_state,
        runtime_status=reference.runtime_status,
        label_source=reference.label_source,
        degradation_reason=reference.degradation_reason,
        degraded_from=reference.degraded_from,
        backend_real_active=reference.backend_real_active,
        certification_allowed=reference.certification_allowed,
        contributors=reference.contributors,
        backend_status_reason=reference.backend_status_reason,
    )
    return replace(scored, run_reference=light_reference)


def _light_backend_status_trace(scored: ScoredResult) -> Mapping[str, Any] | None:
    reference = scored.run_reference
    status = _result_backend_status(scored)
    trace = getattr(reference, "trace", None) if reference is not None else None
    payload: dict[str, Any] = {}
    if status is not None:
        payload["backend_status"] = status
    if reference is not None and reference.backend_name is not None:
        payload["backend_name"] = reference.backend_name
    if reference is not None and reference.backend_authoritative is not None:
        payload["backend_authoritative"] = reference.backend_authoritative
    if reference is not None and reference.evidence_class is not None:
        payload["evidence_class"] = reference.evidence_class
    if reference is not None and reference.cache_state is not None:
        payload["cache_state"] = reference.cache_state
        payload["reduced_real_cache_state"] = reference.cache_state
    if (
        reference is not None
        and reference.status == "refused"
        and reference.reason
    ):
        payload["refusal_reason"] = reference.reason
    if isinstance(trace, Mapping):
        for key in (
            "backend_name",
            "backend_diagnostics",
            "out_of_domain_crash_point",
            "refusal_reason",
            "refusal_diagnostic",
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
            "evidence_class",
            "cache_state",
            "reduced_real_cache_state",
            "cache_rung",
            "physics_rung",
            "sig_fig_rung",
            "rung",
            "evidence_rank",
            "proof_rank",
            "proof_grade",
            "neighbor_disagreement",
            "reduced_real_cache",
            "per_hour_summary",
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
    light_scored = _strip_heavy_result(scored)
    failure = light_scored.failure_category
    objectives = _objective_mapping(light_scored.objectives)
    search_provenance = _search_provenance_from_candidate(candidate)
    result_blob = _result_blob(light_scored)
    return StudyRecord(
        candidate_id=candidate.id,
        patch=candidate.patch,
        feasible=bool(light_scored.feasible),
        status=_status(light_scored),
        objectives=objectives,
        feasibility_margins=_margin_mapping(light_scored.feasibility_margins),
        cache_key=light_scored.cache_key,
        failure_category=failure.value if failure is not None else None,
        failing_gates=light_scored.failing_gates,
        notes=light_scored.notes,
        cache_hit=cache_hit,
        product_summary=_product_summary_mapping(light_scored.run_reference),
        trace_summary=_trace_summary_mapping(light_scored),
        result_blob=result_blob if isinstance(result_blob, Mapping) else {},
        proposal_source=str(search_provenance["proposal_source"]),
        seed_lineage=bool(search_provenance["seed_lineage"]),
        search_provenance=search_provenance,
        eval_spec=light_scored.eval_spec,
    )


def _search_provenance_from_candidate(candidate: Candidate) -> Mapping[str, Any]:
    metadata = candidate.metadata
    proposal_source = metadata.get("proposal_source", "unknown")
    proposal_source = str(proposal_source) if proposal_source is not None else "unknown"
    topology = metadata.get("topology")
    topology_id = None
    if isinstance(topology, Mapping):
        raw_topology_id = topology.get("id")
        if isinstance(raw_topology_id, str) and raw_topology_id:
            topology_id = raw_topology_id
    seed_origin = metadata.get("seed_origin")
    payload: dict[str, Any] = {
        "proposal_source": proposal_source,
        "seed_lineage": bool(metadata.get("seed_lineage")),
        "strategy": str(metadata.get("strategy", "")),
    }
    if topology_id is not None:
        payload["topology_id"] = topology_id
        payload["topology"] = _jsonable_value(topology)
    for key in (
        "stage_index",
        "stage_id",
        "parent_candidate_id",
        "parent_cache_key",
        "trial_number",
        "seed_id",
        "conditional_mask",
        "conditional_subspace_digest",
        "effective_pins",
        "conditional_scope",
        "conditional_prefix_stage_ids",
    ):
        value = metadata.get(key)
        if value is not None:
            payload[key] = _jsonable_value(value)
    if isinstance(seed_origin, Mapping):
        payload["seed_origin"] = _jsonable_value(seed_origin)
    return MappingProxyType(payload)


def _objective_mapping(objectives: ObjectiveVector | None) -> Mapping[str, float | None]:
    if objectives is None:
        return MappingProxyType({})
    mapping = objectives.as_mapping()
    finite_mapping = {
        str(key): None if value is None else _finite(value, str(key))
        for key, value in mapping.items()
    }
    return canonical_objective_mapping(finite_mapping)


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
    if reference.backend_name is not None:
        payload["backend_name"] = reference.backend_name
    if reference.backend_authoritative is not None:
        payload["backend_authoritative"] = reference.backend_authoritative
    if reference.evidence_class is not None:
        payload["evidence_class"] = reference.evidence_class
    if reference.status == "refused" and reference.reason:
        payload["refusal_reason"] = reference.reason
    if reference.cache_state is not None:
        payload["reduced_real_cache_state"] = str(reference.cache_state)
        payload["cache_state"] = str(reference.cache_state)
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
            "refusal_reason",
            "refusal_diagnostic",
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
    strategy_provenance: Mapping[str, Any] | None = None,
    config: StudyConfig | None = None,
    strategy: Strategy | str | None = None,
    strategy_name: str | None = None,
    sampler_name: str | None = None,
    study_status: str | None = None,
    write_store: ResultStore | None = None,
    prefix_evals_run: int = 0,
) -> dict[str, Path]:
    created_at = datetime.now(UTC).isoformat()
    resolved_study_status = study_status or (
        COMPLETED_STATUS if winner is not None else COMPLETED_NO_FEASIBLE_WINNER_STATUS
    )
    resolved_strategy = strategy_name or (
        _strategy_label(config.strategy) if config is not None else "unknown"
    )
    resolved_sampler = sampler_name or (
        _resolved_strategy_sampler(config.strategy) if config is not None else active_sampler_name()
    )
    resolved_strategy_input = strategy if strategy is not None else (
        config.strategy if config is not None else resolved_strategy
    )
    strategy_config = _strategy_config_payload(
        resolved_strategy_input,
        config=config,
        sampler_name=resolved_sampler,
    )
    strategy_config_digest = _strategy_config_digest(strategy_config)
    study_id_value = _study_id(
        profile=profile,
        feedstock=feedstock,
        fidelity=fidelity,
        created_at=created_at,
        config=config,
        strategy_name=resolved_strategy,
        strategy_config_digest=strategy_config_digest,
        search_space_identity=search_space_identity,
    )
    manifest_path = out / "study.manifest.json"
    summary_path = out / "study.summary.json"
    profile_path = out / "study.profile.yaml"
    pareto_path = out / "pareto.json"
    search_provenance_path = out / "search_provenance.json"
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
    pareto_payload["member_schema_version"] = MEMBER_SCHEMA_VERSION
    pareto_payload["status"] = resolved_study_status
    pareto_payload["failure_counts"] = dict(failure_counts)
    best_non_seeded = _best_non_seeded_lineage(leaderboard)
    pareto_payload["best_overall_candidate_id"] = (
        winner.candidate_id if winner is not None else None
    )
    pareto_payload["best_non_seeded_lineage_candidate_id"] = (
        best_non_seeded.candidate_id if best_non_seeded is not None else None
    )
    summary_payload = _study_summary_payload(
        study_id=study_id_value,
        created_at=created_at,
        study_status=resolved_study_status,
        profile=profile,
        feedstock=feedstock,
        fidelity=fidelity,
        definitions=definitions,
        leaderboard=leaderboard,
        winner=winner,
        failure_counts=failure_counts,
        config=config,
        strategy_name=resolved_strategy,
        prefix_evals_run=prefix_evals_run,
    )
    winner_written = False
    tap_sidecar_written = False
    with _store_write_context(write_store):
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
        search_provenance_path.write_text(
            json.dumps(
                _json_member_payload(
                    _search_provenance_payload(
                        leaderboard,
                        winner=winner,
                        best_non_seeded=best_non_seeded,
                        strategy_provenance=strategy_provenance,
                    )
                ),
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
        profile_path.write_text(
            yaml.safe_dump(_jsonable_value(profile), sort_keys=True),
            encoding="utf-8",
        )
        summary_path.write_text(
            json.dumps(summary_payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps(
                _study_manifest_payload(
                    study_id=study_id_value,
                    created_at=created_at,
                    study_status=resolved_study_status,
                    profile=profile,
                    feedstock=feedstock,
                    fidelity=fidelity,
                    config=config,
                    strategy_name=resolved_strategy,
                    sampler_name=resolved_sampler,
                    search_space_identity=search_space_identity,
                    strategy_config=strategy_config,
                    prefix_evals_run=prefix_evals_run,
                    # journal replay reconstructs strategy state from the ask/tell
                    # journal alone; a warm_start_from study needs bundled seed
                    # state replay cannot rebuild, so it is not journal-replayable
                    # even though the events journal exists.
                    replayable=(
                        (out / "study.events.jsonl").is_file()
                        and getattr(config, "warm_start_from", None) is None
                    ),
                ),
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        if winner is not None:
            winner_patch = _materialized_winner_patch(winner, schema, profile)
            if winner.eval_spec is None:
                raise ValueError("winner record missing EvalSpec cost parameters")
            winner_document = {
                RECIPE_COST_PARAMETERS_KEY: _jsonable_value(
                    winner.eval_spec.cost_parameters
                ),
                **dict(winner_patch),
            }
            winner_path.write_text(
                yaml.safe_dump(winner_document, sort_keys=False),
                encoding="utf-8",
            )
            winner_written = True
            tap_sidecar = _tap_truncated_sidecar(
                winner,
                winner_patch,
                schema.to_setpoints_patch(winner.patch),
            )
            if tap_sidecar is not None:
                tap_sidecar = _json_member_payload(tap_sidecar)
                tap_sidecar_path.write_text(
                    json.dumps(tap_sidecar, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8",
                )
                tap_sidecar_written = True
    artifacts = {
        "manifest": manifest_path,
        "summary": summary_path,
        "profile_snapshot": profile_path,
        "pareto": pareto_path,
        "leaderboard": leaderboard_path,
        "search_provenance": search_provenance_path,
    }
    events_path = out / STUDY_EVENTS_NAME
    if events_path.is_file():
        artifacts["events_journal"] = events_path
    strategy_state_path = out / STRATEGY_STATE_NAME
    if strategy_state_path.is_file():
        artifacts["strategy_state"] = strategy_state_path
    if winner_written:
        artifacts["winner"] = winner_path
    if tap_sidecar_written:
        artifacts["winner_tap_truncated"] = tap_sidecar_path
    return artifacts


def _study_summary_payload(
    *,
    study_id: str,
    created_at: str,
    study_status: str,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    definitions: Sequence[ObjectiveDefinition],
    leaderboard: Sequence[StudyRecord],
    winner: StudyRecord | None,
    failure_counts: Mapping[str, int],
    config: StudyConfig | None,
    strategy_name: str,
    prefix_evals_run: int = 0,
) -> Mapping[str, Any]:
    feasible_count = sum(1 for record in leaderboard if record.feasible)
    infeasible_count = sum(int(value) for value in failure_counts.values())
    if infeasible_count == 0:
        infeasible_count = sum(1 for record in leaderboard if not record.feasible)
    evaluated = feasible_count + infeasible_count
    budget = int(config.budget) if config is not None else evaluated
    source_record, products_source = _summary_products_source(winner, leaderboard)
    if study_status == ABORTED_STATUS:
        source_record, products_source = None, "none"
    best_non_seeded = _best_non_seeded_lineage(leaderboard)
    return {
        "save_schema_version": SAVE_SCHEMA_VERSION,
        "member_schema_version": MEMBER_SCHEMA_VERSION,
        "study_id": study_id,
        "created_at": created_at,
        "study_status": study_status,
        "feedstock_id": feedstock,
        "profile_id": profile_id(profile),
        "profile_display_name": _profile_display_name(profile),
        "strategy": strategy_name,
        "seed": int(config.seed) if config is not None else 0,
        "budget": budget,
        "evaluated": evaluated,
        "prefix_evals_run": int(prefix_evals_run),
        "verdict_counts": {
            "feasible": feasible_count,
            "not_attempted": max(budget - evaluated, 0),
            "infeasible": infeasible_count,
        },
        "objectives_spec": [
            {
                "metric": definition.metric,
                "sense": definition.sense,
                "units": definition.units,
            }
            for definition in definitions
        ],
        "winner": (
            _summary_record_ref(winner, _record_rank(winner, leaderboard))
            if winner is not None
            else None
        ),
        "dual_winner_non_seeded": (
            _summary_record_ref(best_non_seeded, _record_rank(best_non_seeded, leaderboard))
            if best_non_seeded is not None
            and (winner is None or best_non_seeded.candidate_id != winner.candidate_id)
            else None
        ),
        "honesty": _summary_honesty_payload(
            fidelity,
            source_record,
            records=leaderboard,
        ),
        "badges": _summary_badges_payload(),
        "coating": _summary_coating_payload(source_record),
        "products_source": products_source,
        "products": _summary_products_payload(source_record),
        "origin": "local",
        "verification": None,
    }


def _study_manifest_payload(
    *,
    study_id: str,
    created_at: str,
    study_status: str,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    config: StudyConfig | None,
    strategy_name: str,
    sampler_name: str,
    search_space_identity: Mapping[str, Any] | None,
    strategy_config: Mapping[str, Any],
    replayable: bool,
    prefix_evals_run: int = 0,
) -> Mapping[str, Any]:
    return {
        "save_schema_version": SAVE_SCHEMA_VERSION,
        "member_schema_version": MEMBER_SCHEMA_VERSION,
        "study_id": study_id,
        "created_at": created_at,
        "code_version": current_code_version(),
        "git": {"sha": None, "dirty": None},
        "strategy": {
            "name": strategy_name,
            "class": strategy_name,
            "config": _jsonable_value(strategy_config),
        },
        "seed": int(config.seed) if config is not None else 0,
        "budget": int(config.budget) if config is not None else 0,
        "prefix_evals_run": int(prefix_evals_run),
        "parallel": int(config.parallel) if config is not None else 1,
        "fidelity": fidelity,
        "profile": {
            "id": profile_id(profile),
            "display_name": _profile_display_name(profile),
            "basename": _profile_basename(config.profile if config is not None else None),
            "content_hash": _profile_content_hash(profile),
        },
        "feedstock_id": feedstock,
        "search_space_identity": _jsonable_value(search_space_identity or {}),
        "recipe_schema_version": recipe_schema_version,
        "allowlist_version": allowlist_version,
        "data_digests": {},
        "corpus_version": current_corpus_version(),
        "result_scope": None,
        "sampler_name": sampler_name,
        "scipy_version": _package_version("scipy"),
        "optuna_version": _package_version("optuna"),
        "cache_tier_policy": _jsonable_value(
            _mapping_or_empty(profile.get("cache_tier_policy"))
        ),
        "two_phase_settings": _jsonable_value(
            _mapping_or_empty(profile.get("two_phase_certification"))
        ),
        "study_status": study_status,
        "replayable": bool(replayable),
        "reoptimized_from": None,
        "goals_source": None,
    }


def _json_member_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.setdefault("member_schema_version", MEMBER_SCHEMA_VERSION)
    return result


def _strategy_config_payload(
    strategy: Strategy | str,
    *,
    config: StudyConfig | None,
    sampler_name: str,
) -> Mapping[str, Any]:
    payload: dict[str, Any] = {}
    if isinstance(strategy, str):
        payload["strategy"] = strategy
    else:
        payload["strategy"] = getattr(strategy, "name", type(strategy).__name__)
        for attr in (
            "seed",
            "sampler_name",
            "num_trajectories",
            "num_levels",
            "prune_threshold",
            "beam_width",
            "children_per_parent",
            "max_backward_passes",
            "max_joint_refines",
            "stage_ids",
        ):
            if hasattr(strategy, attr):
                value = getattr(strategy, attr)
                payload[attr] = value() if callable(value) else value
        topology = getattr(strategy, "topology", None)
        metadata = getattr(topology, "metadata", None)
        if callable(metadata):
            payload["topology"] = metadata()
    payload["sampler_name"] = sampler_name
    if config is not None:
        payload["seed"] = int(config.seed)
        payload["budget"] = int(config.budget)
        payload["parallel"] = int(config.parallel)
        if config.warm_start_from is not None:
            payload["warm_start_from"] = str(config.warm_start_from)
        if config.per_eval_timeout_seconds is not None:
            payload["per_eval_timeout_seconds"] = float(config.per_eval_timeout_seconds)
    return _jsonable_value(payload)


def _strategy_config_digest(strategy_config: Mapping[str, Any]) -> str:
    encoded = canonical_json_dumps(_jsonable_value(strategy_config)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _study_id(
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    created_at: str,
    config: StudyConfig | None,
    strategy_name: str,
    strategy_config_digest: str | None,
    search_space_identity: Mapping[str, Any] | None,
) -> str:
    identity_block = {
        "strategy": strategy_name,
        "strategy_config_digest": strategy_config_digest,
        "seed": int(config.seed) if config is not None else 0,
        "budget": int(config.budget) if config is not None else 0,
        "fidelity": fidelity,
        "profile_content_hash": _profile_content_hash(profile),
        "feedstock_id": feedstock,
        "recipe_schema_version": recipe_schema_version,
        "allowlist_version": allowlist_version,
        "bounds_digest": _mapping_or_empty(search_space_identity).get("bounds_digest"),
        "data_digests": {},
        "corpus_version": current_corpus_version(),
        "result_scope": None,
        "created_at": created_at,
    }
    encoded = canonical_json_dumps(_jsonable_value(identity_block)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _summary_record_ref(record: StudyRecord, rank: int | None) -> Mapping[str, Any]:
    return {
        "candidate_id": record.candidate_id,
        "cache_key": record.cache_key,
        "rank": rank,
        "feasible": record.feasible,
        "objectives": dict(record.objectives),
        "proposal_source": record.proposal_source,
        "seed_lineage": record.seed_lineage,
    }


def _record_rank(record: StudyRecord | None, leaderboard: Sequence[StudyRecord]) -> int | None:
    if record is None:
        return None
    for rank, candidate in enumerate(leaderboard, start=1):
        if candidate.candidate_id == record.candidate_id:
            return rank
    return None


def _summary_products_source(
    winner: StudyRecord | None,
    leaderboard: Sequence[StudyRecord],
) -> tuple[StudyRecord | None, str]:
    if winner is not None:
        return winner, "winner"
    if leaderboard and leaderboard[0].feasible:
        return leaderboard[0], "rank_1_leaderboard"
    return None, "none"


def _summary_honesty_payload(
    fidelity: str,
    record: StudyRecord | None,
    *,
    records: Sequence[StudyRecord],
) -> Mapping[str, Any]:
    carrier = _mapping_or_empty(record.trace_summary if record is not None else {})
    result_blob = _mapping_or_empty(record.result_blob if record is not None else {})
    label = optimizer_tier_label(carrier, result_blob)
    canonical = _mapping_or_empty(label.get("canonical"))
    extraction = _mapping_or_empty(
        record.product_summary.get("extraction_completeness") if record is not None else {}
    )
    payload: dict[str, Any] = {
        "fidelity": fidelity,
        "tier": label.get("tier", "unknown"),
        "backend_name": carrier.get("backend_name"),
        "backend_status": carrier.get("backend_status") or canonical.get("runtime_status"),
        "evidence_class": label.get("evidence_class") or canonical.get("evidence_class"),
        "ux_label": label.get("ux_label", "UNVERIFIED"),
        "certification_allowed": bool(label.get("certification_allowed", False)),
        "completeness": {
            "status": str(extraction.get("status") or "unknown"),
            "percent": _optional_float(
                extraction.get("percent", extraction.get("completeness_percent"))
            ),
        },
        "extraction_completeness_summary": _jsonable_value(extraction),
        "corpus_version": current_corpus_version(),
        "code_version": current_code_version(),
        "cache_states_seen": _cache_states_seen(records),
        "diagnostic_warnings": _diagnostic_warnings(records),
    }
    evidence_rank = _summary_evidence_rank(label)
    if evidence_rank is not None:
        payload["evidence_rank"] = evidence_rank
    return payload


def _summary_evidence_rank(label: Mapping[str, Any]) -> str | None:
    for key in ("evidence_rank", "proof_rank", "proof_grade"):
        value = label.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _summary_badges_payload() -> Mapping[str, Any]:
    corpus_version = current_corpus_version()
    code_version = current_code_version()
    return {
        "corpus": {
            "status": "current",
            "raw_status": "accepted",
            "label": corpus_version,
            "stored_version": corpus_version,
            "current_version": corpus_version,
        },
        "code": {
            "status": "current",
            "raw_status": "current",
            "label": "current",
            "stored_version": code_version,
            "current_version": code_version,
        },
    }


def _summary_coating_payload(record: StudyRecord | None) -> Mapping[str, Any]:
    summary = _mapping_or_empty(record.product_summary if record is not None else {})
    wall = _mapping_or_empty(summary.get("wall_deposit_kg_by_segment_species"))
    return {
        "campaigns_to_resinter": _optional_float(summary.get("campaigns_to_resinter")),
        "verdict": str(
            summary.get("coating_status")
            or summary.get("coating_output_status")
            or "unknown"
        ),
        "worst_species": _worst_wall_deposit_species(wall),
        "wall_deposit_kg_by_segment_species": _jsonable_value(wall),
    }


def _summary_products_payload(record: StudyRecord | None) -> Mapping[str, Any] | None:
    if record is None:
        return None
    summary = _mapping_or_empty(record.product_summary)
    classes = _mapping_or_empty(summary.get("product_classes"))
    product_ledger = _mapping_or_empty(summary.get("product_ledger_kg"))
    terminal_rump_species = _terminal_rump_species_kg(record)
    rump_oxides = _oxide_wt_pct_from_kg(terminal_rump_species)
    if not rump_oxides:
        rump_oxides = _oxide_wt_pct_from_kg(
            _species_kg_from_classes(classes, "refractory_ceramic_rump")
        )
    return {
        "oxygen_kg": _first_float(
            _nested_value(classes, ("metals_plus_O2", "O2_kg")),
            _nested_value(classes, ("oxygen", "class_total_kg")),
            product_ledger.get("O2"),
            product_ledger.get("oxygen"),
        ),
        "metals_kg": _species_kg_from_classes(
            classes,
            "ingots_metals",
            "metals_plus_O2",
            species_keys=("kg_by_species", "metals_kg_by_species"),
        ),
        "glass_kg": _species_kg_from_classes(classes, "glass", "pure_silica_glass"),
        "volatiles_captured_kg": _species_kg_from_classes(
            classes,
            "captured_volatiles",
            "volatiles_captured",
        ),
        "refractory_rump_kg": _first_float(
            _nested_value(classes, ("refractory_ceramic_rump", "class_total_kg")),
            _nested_value(classes, ("terminal_rump", "class_total_kg")),
        ),
        "rump_top_oxides_wt_pct": rump_oxides,
        "ceramic_rump_panel": _ceramic_rump_panel_from_oxides(rump_oxides),
        "terminal_rump_by_class_kg": _terminal_rump_by_class_kg(classes, record),
    }


def _cache_states_seen(records: Sequence[StudyRecord]) -> list[str]:
    seen: set[str] = set()
    for record in records:
        trace = _mapping_or_empty(record.trace_summary)
        for key in ("reduced_real_cache_state", "cache_state"):
            value = trace.get(key)
            if value is not None and str(value).strip():
                seen.add(str(value))
    return sorted(seen)


def _diagnostic_warnings(records: Sequence[StudyRecord]) -> list[str]:
    warnings: set[str] = set()
    for record in records:
        trace = _mapping_or_empty(record.trace_summary)
        backend_name = str(trace.get("backend_name") or "")
        backend_status = str(trace.get("backend_status") or "")
        if (
            canonical_backend_name(backend_name)
            == ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
            or canonical_backend_name(backend_status)
            == ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
        ):
            warnings.add("internal-analytical-backend")
        if record.failure_category == "diagnostic_only":
            warnings.add("diagnostic-only rows")
        if _is_tap_truncated(_composition_target_payload(record)):
            warnings.add("tap_truncated")
        if trace.get("vapor_pressure_fallback_provider_id") or trace.get(
            "kernel_fallback_used"
        ):
            warnings.add("vapor-pressure fallback")
        if any("previously_ungated" in str(note) for note in record.notes):
            warnings.add("previously_ungated")
    return sorted(warnings)


def _profile_display_name(profile: Mapping[str, Any]) -> str:
    raw = profile.get("display_name") or profile.get("name") or profile_id(profile)
    return str(raw)


def _profile_basename(raw_profile: Any) -> str | None:
    if isinstance(raw_profile, (str, Path)):
        return Path(raw_profile).name
    return None


def _profile_content_hash(profile: Mapping[str, Any]) -> str:
    encoded = canonical_json_dumps(_jsonable_value(profile)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else MappingProxyType({})


def _nested_value(source: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = source
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _species_kg_from_classes(
    classes: Mapping[str, Any],
    *class_keys: str,
    species_keys: Sequence[str] = ("kg_by_species",),
) -> Mapping[str, float]:
    result: dict[str, float] = {}
    for class_key in class_keys:
        class_payload = _mapping_or_empty(classes.get(class_key))
        for species_key in species_keys:
            species_map = _mapping_or_empty(class_payload.get(species_key))
            for species, kg in species_map.items():
                value = _optional_float(kg)
                if value is not None:
                    result[str(species)] = value
    return result


def _terminal_rump_species_kg(record: StudyRecord) -> Mapping[str, float]:
    trace = _mapping_or_empty(record.trace_summary)
    direct = _mapping_or_empty(trace.get("terminal_rump_by_species_kg"))
    if direct:
        return {
            str(species): value
            for species, kg in direct.items()
            for value in [_optional_float(kg)]
            if value is not None
        }
    rump = _mapping_or_empty(trace.get("rump_terminal"))
    return {
        str(species): value
        for species, kg in _mapping_or_empty(rump.get("kg_by_species")).items()
        for value in [_optional_float(kg)]
        if value is not None
    }


def _terminal_rump_by_class_kg(
    classes: Mapping[str, Any],
    record: StudyRecord,
) -> Mapping[str, float]:
    trace = _mapping_or_empty(record.trace_summary)
    direct = _mapping_or_empty(trace.get("terminal_rump_by_class_kg"))
    if direct:
        return {
            str(key): value
            for key, kg in direct.items()
            for value in [_optional_float(kg)]
            if value is not None
        }
    result: dict[str, float] = {}
    for key in sorted(_TERMINAL_RUMP_PRODUCT_CLASS_KEYS):
        payload = classes.get(key)
        value = _optional_float(_mapping_or_empty(payload).get("class_total_kg"))
        if value is not None:
            result[str(key)] = value
    return result


def _oxide_wt_pct_from_kg(species_kg: Mapping[str, Any]) -> Mapping[str, float]:
    oxide_kg = {
        str(species): amount
        for species, kg in species_kg.items()
        if _is_oxide_species(str(species))
        for amount in [_optional_float(kg)]
        if amount is not None and amount > 0.0
    }
    total = sum(oxide_kg.values())
    if total <= 0.0:
        return {}
    return {
        species: round(amount / total * 100.0, 3)
        for species, amount in sorted(oxide_kg.items())
    }


def _is_oxide_species(species: str) -> bool:
    if species in {"O2", "H2O", "CO2"}:
        return False
    if species == "REE_oxides":
        return True
    return bool(_OXIDE_FORMULA_RE.fullmatch(species))


def _ceramic_rump_panel_from_oxides(composition: Mapping[str, Any]) -> Mapping[str, Any]:
    return ceramic_rump_payload(composition)


def _ceramic_match_payload(match: CeramicMatch) -> Mapping[str, Any]:
    return {
        "ceramic_id": match.ceramic_id,
        "label": match.label,
        "composition_kind": match.composition_kind,
        "service_temp": _ceramic_service_temp_payload(match.service_temp),
        "liner_suitability": dict(match.liner_suitability),
    }


def _ceramic_service_temp_payload(
    service_temp: CeramicServiceTemperature,
) -> Mapping[str, Any]:
    usable = service_temp.usable_service_C
    kind = service_temp.kind
    if usable is not None:
        display = f"Usable service: {usable:g} C"
        usable_service = True
    elif service_temp.value_C is not None:
        display = f"{kind}: {service_temp.value_C:g} C; not a usable service rating"
        usable_service = False
    else:
        display = f"{kind}; not a usable service rating"
        usable_service = False
    return {
        "value_C": _round_or_none(service_temp.value_C),
        "kind": kind,
        "usable_service_C": _round_or_none(usable),
        "usable_service": usable_service,
        "display": display,
        "note": service_temp.note,
    }


def _worst_wall_deposit_species(wall: Mapping[str, Any]) -> str | None:
    totals: dict[str, float] = {}
    for species_map in wall.values():
        if not isinstance(species_map, Mapping):
            continue
        for species, kg in species_map.items():
            value = _optional_float(kg)
            if value is not None:
                totals[str(species)] = totals.get(str(species), 0.0) + value
    if not totals:
        return None
    return max(sorted(totals), key=lambda species: totals[species])


def _first_float(*values: Any) -> float | None:
    for value in values:
        number = _optional_float(value)
        if number is not None:
            return number
    return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 3)


_TAP_TRUNCATION_DURATION_PATHS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "C0B": ("campaigns", "C0b_p_cleanup", "duration_hr"),
        "C0b_p_cleanup": ("campaigns", "C0b_p_cleanup", "duration_hr"),
        "C2A": ("campaigns", "C2A_continuous", "duration_hr"),
        "C2A_continuous": ("campaigns", "C2A_continuous", "duration_hr"),
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


def _write_aborted_artifacts_from_cache(
    out: Path,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    definitions: Sequence[ObjectiveDefinition],
    fallback_records: Sequence[StudyRecord] = (),
    schema: RecipeSchema,
    failure_counts: Mapping[str, int],
    search_space_identity: Mapping[str, Any] | None = None,
    strategy_provenance: Mapping[str, Any] | None = None,
    config: StudyConfig | None = None,
    strategy: Strategy | str | None = None,
    strategy_name: str | None = None,
    sampler_name: str | None = None,
    constraints: Any = None,
    write_store: ResultStore | None = None,
    prefix_evals_run: int = 0,
) -> bool:
    records = _records_from_cache_sqlite(
        out,
        schema,
        profile=profile,
        feedstock=feedstock,
        fidelity=fidelity,
        constraints=constraints,
        write_store=write_store,
    )
    if not records:
        records = tuple(fallback_records)
    if not records:
        return False

    computed_failures = _failure_counts(records)
    resolved_failure_counts = dict(failure_counts)
    resolved_failure_counts.update(computed_failures)
    feasible = tuple(record for record in records if record.feasible)
    leaderboard = (
        tuple(sorted(feasible, key=lambda row: _rank_key(row, definitions)))
        if feasible
        else tuple(records)
    )
    pareto_ranked = tuple(
        sorted(
            pareto_front(
                feasible,
                definitions,
                objective_getter=lambda row: row.objectives,
            ),
            key=lambda row: _rank_key(row, definitions),
        )
    )
    _write_artifacts(
        out,
        profile=profile,
        feedstock=feedstock,
        fidelity=fidelity,
        definitions=definitions,
        pareto=pareto_ranked,
        leaderboard=leaderboard,
        winner=None,
        schema=schema,
        failure_counts=resolved_failure_counts,
        search_space_identity=search_space_identity,
        strategy_provenance=strategy_provenance,
        config=config,
        strategy=strategy,
        strategy_name=strategy_name,
        sampler_name=sampler_name,
        study_status=ABORTED_STATUS,
        write_store=write_store,
        prefix_evals_run=prefix_evals_run,
    )
    return True


def _records_from_cache_sqlite(
    out: Path,
    schema: RecipeSchema,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    constraints: Any = None,
    write_store: ResultStore | None = None,
) -> tuple[StudyRecord, ...]:
    store_path = out / "cache.sqlite"
    if not store_path.is_file():
        return ()
    store = ResultStore(
        store_path,
        write_lock_path=write_store.write_lock_path if write_store is not None else None,
        write_lock_timeout_ms=(
            write_store.write_lock_timeout_ms if write_store is not None else None
        ),
    )
    active_constraints = constraints
    if active_constraints is None:
        try:
            active_constraints = _constraints_for_profile(profile)
        except ProfileValidationError as exc:
            if not _is_stale_profile_refusal(exc):
                return ()
    try:
        selector_spec, _ = _build_eval_inputs(
            RecipePatch({}).validated(schema),
            feedstock,
            fidelity,
            profile,
            schema,
            constraints=active_constraints,
        )
    except (ProfileValidationError, RecipeValidationError, ValueError):
        return ()
    scoped = store.query(
        selector_spec.feedstock_id,
        profile_id=selector_spec.profile_id,
        fidelity=selector_spec.fidelity,
        code_version=selector_spec.code_version,
        data_digests=selector_spec.data_digests,
        result_scope=result_scope_payload(selector_spec),
    )
    records: list[StudyRecord] = []
    for index, scored in enumerate(scoped):
        candidate = _candidate_from_cached_scored(scored, index=index, schema=schema)
        records.append(_to_record(candidate, scored, cache_hit=True))
    return tuple(records)


def _candidate_from_cached_scored(
    scored: ScoredResult,
    *,
    index: int,
    schema: RecipeSchema,
) -> Candidate:
    cache_key_value = scored.cache_key or f"row-{index:06d}"
    candidate_id = scored.candidate_id or f"cache-{cache_key_value[:12]}"
    return Candidate(
        id=str(candidate_id),
        patch=RecipePatch({}).validated(schema),
        metadata={
            "strategy": "cache.sqlite",
            "sequence": index,
            "proposal_source": "cache_sqlite",
            "seed_lineage": False,
        },
    )


def _write_empty_artifacts(
    out: Path,
    *,
    profile: Mapping[str, Any],
    feedstock: str,
    fidelity: str,
    definitions: Sequence[ObjectiveDefinition],
    failure_counts: Mapping[str, int],
    config: StudyConfig | None = None,
    constraints: Any = None,
    write_store: ResultStore | None = None,
    prefix_evals_run: int = 0,
) -> None:
    fidelity = str(canonical_backend_name(fidelity))
    schema = RecipeSchema()
    strategy_name = _strategy_label(config.strategy) if config is not None else "unknown"
    sampler_name = (
        _resolved_strategy_sampler(config.strategy)
        if config is not None
        else active_sampler_name()
    )
    if _write_aborted_artifacts_from_cache(
        out,
        profile=profile,
        feedstock=feedstock,
        fidelity=fidelity,
        definitions=definitions,
        schema=schema,
        failure_counts=failure_counts,
        config=config,
        strategy=config.strategy if config is not None else None,
        strategy_name=strategy_name,
        sampler_name=sampler_name,
        constraints=constraints,
        write_store=write_store,
        prefix_evals_run=prefix_evals_run,
    ):
        return

    created_at = datetime.now(UTC).isoformat()
    strategy_config = _strategy_config_payload(
        config.strategy if config is not None else strategy_name,
        config=config,
        sampler_name=sampler_name,
    )
    strategy_config_digest = _strategy_config_digest(strategy_config)
    study_id_value = _study_id(
        profile=profile,
        feedstock=feedstock,
        fidelity=fidelity,
        created_at=created_at,
        config=config,
        strategy_name=strategy_name,
        strategy_config_digest=strategy_config_digest,
        search_space_identity=None,
    )
    with _store_write_context(write_store):
        (out / "study.profile.yaml").write_text(
            yaml.safe_dump(_jsonable_value(profile), sort_keys=True),
            encoding="utf-8",
        )
        (out / "study.summary.json").write_text(
            json.dumps(
                _study_summary_payload(
                    study_id=study_id_value,
                    created_at=created_at,
                    study_status=ABORTED_STATUS,
                    profile=profile,
                    feedstock=feedstock,
                    fidelity=fidelity,
                    definitions=definitions,
                    leaderboard=(),
                    winner=None,
                    failure_counts=failure_counts,
                    config=config,
                    strategy_name=strategy_name,
                    prefix_evals_run=prefix_evals_run,
                ),
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (out / "study.manifest.json").write_text(
            json.dumps(
                _study_manifest_payload(
                    study_id=study_id_value,
                    created_at=created_at,
                    study_status=ABORTED_STATUS,
                    profile=profile,
                    feedstock=feedstock,
                    fidelity=fidelity,
                    config=config,
                    strategy_name=strategy_name,
                    sampler_name=sampler_name,
                    search_space_identity=None,
                    strategy_config=strategy_config,
                    prefix_evals_run=prefix_evals_run,
                    replayable=(
                        (out / "study.events.jsonl").is_file()
                        and getattr(config, "warm_start_from", None) is None
                    ),
                ),
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (out / "pareto.json").write_text(
            json.dumps(
                {
                    "member_schema_version": MEMBER_SCHEMA_VERSION,
                    "feedstock": feedstock,
                    "fidelity": fidelity,
                    "failure_counts": dict(failure_counts),
                    "objectives": [_definition_payload(definition) for definition in definitions],
                    "pareto": [],
                    "profile": profile_id(profile),
                    "selection_rule": WINNER_SELECTION_RULE,
                    "status": ABORTED_STATUS,
                    "winner_candidate_id": None,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        _write_leaderboard(out / "leaderboard.csv", (), (), None, definitions, schema)


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


def _best_non_seeded_lineage(records: Sequence[StudyRecord]) -> StudyRecord | None:
    for record in records:
        if not record.seed_lineage:
            return record
    return None


def _search_provenance_payload(
    records: Sequence[StudyRecord],
    *,
    winner: StudyRecord | None,
    best_non_seeded: StudyRecord | None,
    strategy_provenance: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    counts: dict[str, int] = {}
    seeded_ids: list[str] = []
    for record in records:
        counts[record.proposal_source] = counts.get(record.proposal_source, 0) + 1
        if record.seed_lineage:
            seeded_ids.append(record.candidate_id)
    payload: dict[str, Any] = {
        "best_overall_candidate_id": winner.candidate_id if winner is not None else None,
        "best_non_seeded_lineage_candidate_id": (
            best_non_seeded.candidate_id if best_non_seeded is not None else None
        ),
        "proposal_source_counts": dict(sorted(counts.items())),
        "seeded_candidate_ids": seeded_ids,
    }
    if strategy_provenance:
        payload["strategy_provenance"] = _jsonable_value(strategy_provenance)
    return payload


def _strategy_provenance_payload(*strategies: Strategy) -> Mapping[str, Any]:
    dropped = 0
    dropped_ids: list[str] = []
    for strategy in strategies:
        count = getattr(strategy, "warm_start_rejected_seed_count", 0)
        try:
            dropped += int(count)
        except (TypeError, ValueError):
            pass
        raw_ids = getattr(strategy, "warm_start_rejected_seed_ids", ())
        if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, (str, bytes)):
            dropped_ids.extend(str(value) for value in raw_ids)
    if not dropped:
        return {}
    return {
        "optuna_incomplete_seed_dropped_count": dropped,
        "optuna_incomplete_seed_dropped_ids": dropped_ids,
    }


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
        "proposal_source",
        "seed_lineage",
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
                "proposal_source": record.proposal_source,
                "seed_lineage": record.seed_lineage,
                # For tap rows, patch_json is the materialized tap-hour patch, not the parent trajectory.
                "patch_json": _json_dump_value(
                    _materialized_record_patch(record, schema, profile)
                ),
            }
            for definition in definitions:
                value = objective_value_for_metric(record.objectives, definition.metric)
                row[definition.metric] = "" if value is None else value
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
        "optimizer_patch": record.patch.to_nested(),
        "patch": patch,
        "product_summary": _jsonable_value(record.product_summary),
        "proposal_source": record.proposal_source,
        "search_provenance": _jsonable_value(record.search_provenance),
        "seed_lineage": record.seed_lineage,
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
