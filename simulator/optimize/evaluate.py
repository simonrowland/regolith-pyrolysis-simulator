"""Recipe optimizer evaluation loop and failure taxonomy."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import inspect
import math
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from collections.abc import Mapping as MappingABC, Set as AbstractSet

from simulator.accounting import OverdraftError, resolve_species_formula
from simulator.backends import BackendUnavailableError
from simulator.chemistry.kernel import ProposalRejected
from simulator.config import DEFAULT_DATA_DIR, load_config_bundle
from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value
from simulator.optimize.evalspec import (
    EvalSpec,
    cache_key,
    current_code_version,
    feedstock_recipe_digest,
)
from simulator.optimize.objective import (
    ObjectiveComputationError,
    ObjectiveVector,
    composition_target_eval_metadata,
    composition_target_infeasible_reason,
    composition_targets_require_coating,
    composition_targets_require_terminal_rump,
    compute_objectives,
    product_summary,
)
from simulator.optimize.physics import (
    GATE_ORDER,
    FeasibilityResult,
    GateMargin,
    PhysicsConstraintSet,
    ThresholdSpec,
    physics_constraints_digest,
)
from simulator.optimize.profiles import physics_constraints_from_profile, validate_profile
from simulator.optimize.recipe import RecipePatch, RecipeSchema, RecipeValidationError
from simulator.optimize.worker_runtime import get_worker_runtime
from simulator.reduced_real_determinism import PT0NonFinitePayload
from simulator.mre_ladder import max_voltage_for_target, parse_ladder_from_setpoints
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun, RunnerError


MASS_BALANCE_ABORT_PCT = 5e-12
RUMP_TERMINAL_LIQUID_FRACTION_MAX = 1e-9


class FailureCategory(str, Enum):
    INVALID_PATCH = "invalid_patch"
    INFEASIBLE_RECIPE = "infeasible_recipe"
    OUT_OF_DOMAIN = "out_of_domain"
    PHYSICS_REFUSED = "physics_refused"
    NON_FINITE_PAYLOAD = "non_finite_payload"
    INVALID_RECIPE = "invalid_recipe"
    ENGINE_BUG = "engine_bug"
    BACKEND_UNAVAILABLE = "backend_unavailable"


@dataclass(frozen=True)
class RumpTerminalAssessment:
    earned: bool
    reason: str
    notes: tuple[str, ...]
    trace_payload: Mapping[str, Any]
    liquid_fraction: float | None = None
    solidus_T_C: float | None = None
    liquidus_T_C: float | None = None
    T_crash_C: float | None = None


@dataclass(frozen=True)
class RunReference:
    status: str
    error_message: str = ""
    reason: str = ""
    trace: Any = field(default=None, compare=False, repr=False)
    product_summary: Mapping[str, Any] = field(default_factory=dict)
    backend_status: str | None = None
    backend_authoritative: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "product_summary",
            MappingProxyType(dict(self.product_summary)),
        )
        if self.backend_status is None:
            backend_status = _backend_status_from_carrier(self.trace)
            if backend_status is not None:
                object.__setattr__(self, "backend_status", backend_status)
        if self.backend_authoritative is None:
            backend_authoritative = _backend_authoritative_from_carrier(self.trace)
            if backend_authoritative is not None:
                object.__setattr__(
                    self,
                    "backend_authoritative",
                    backend_authoritative,
                )

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return (
            type(self),
            (
                self.status,
                self.error_message,
                self.reason,
                _thaw_value(self.trace),
                _thaw_value(self.product_summary),
                self.backend_status,
                self.backend_authoritative,
            ),
        )


@dataclass(frozen=True)
class _TraceOverlay:
    original_trace: Any
    overrides: Mapping[str, Any]

    def __getattr__(self, name: str) -> Any:
        if name in self.overrides:
            return self.overrides[name]
        return getattr(self.original_trace, name)


@dataclass(frozen=True)
class _TraceOverrideRunExecution:
    run_execution: Any
    trace_payload: Mapping[str, Any]
    trace: Any = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trace",
            _TraceOverlay(
                getattr(self.run_execution, "trace", None),
                self.trace_payload,
            ),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.run_execution, name)


@dataclass(frozen=True)
class ScoredResult:
    candidate_id: str | None
    eval_spec: EvalSpec | None
    cache_key: str | None
    feasible: bool
    failure_category: FailureCategory | None = None
    objectives: ObjectiveVector | None = None
    feasibility_margins: Mapping[str, GateMargin] = field(default_factory=dict)
    failing_gates: tuple[str, ...] = ()
    run_reference: RunReference | None = field(default=None, compare=False)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "feasibility_margins",
            MappingProxyType(dict(self.feasibility_margins)),
        )
        object.__setattr__(self, "failing_gates", _ordered_failing_gates(self.failing_gates))
        object.__setattr__(self, "notes", _ordered_notes(self.notes))
        if self.feasible:
            if self.failure_category is not None:
                raise ValueError("feasible result cannot carry failure_category")
            if self.objectives is None:
                raise ValueError("feasible result requires objectives")
        else:
            if self.objectives is not None:
                raise ValueError("infeasible result must not carry objectives")

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return (
            type(self),
            (
                self.candidate_id,
                self.eval_spec,
                self.cache_key,
                self.feasible,
                self.failure_category,
                self.objectives,
                _thaw_value(self.feasibility_margins),
                self.failing_gates,
                self.run_reference,
                self.notes,
            ),
        )


class EvaluationAbort(RuntimeError):
    category: FailureCategory

    def __init__(
        self,
        message: str,
        *,
        category: FailureCategory,
        patch: RecipePatch,
        candidate_id: str | None = None,
        eval_spec: EvalSpec | None = None,
        cache_key_value: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.patch = patch
        self.candidate_id = candidate_id
        self.eval_spec = eval_spec
        self.cache_key = cache_key_value


class EngineBugAbort(EvaluationAbort):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, category=FailureCategory.ENGINE_BUG, **kwargs)


class BackendUnavailableAbort(EvaluationAbort):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(
            message,
            category=FailureCategory.BACKEND_UNAVAILABLE,
            **kwargs,
        )


class EvaluationInputError(ValueError):
    """Raised when optimizer evaluation inputs or config references are invalid."""


def evaluate(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: Mapping[str, Any],
    candidate_id: str | None = None,
    executor: RunExecutor | None = None,
    constraints: PhysicsConstraintSet | None = None,
    schema: RecipeSchema | None = None,
    worker_runtime: Any | None = None,
) -> ScoredResult:
    """Run one recipe candidate and return its feasible-only score."""

    active_schema = schema or RecipeSchema()
    try:
        validated_patch = patch.validated(active_schema)
    except RecipeValidationError as exc:
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=None,
            cache_key=None,
            feasible=False,
            failure_category=FailureCategory.INVALID_PATCH,
            notes=(str(exc),),
        )

    active_constraints = _composition_target_constraints(profile, constraints)
    try:
        spec, run_config = _build_eval_inputs(
            validated_patch,
            feedstock_id,
            fidelity,
            profile,
            active_schema,
            constraints=active_constraints,
        )
    except BackendUnavailableError as exc:
        raise BackendUnavailableAbort(
            str(exc),
            patch=validated_patch,
            candidate_id=candidate_id,
        ) from exc
    key = cache_key(spec)
    active_executor = executor or RunExecutor()
    runtime = worker_runtime if worker_runtime is not None else get_worker_runtime()

    try:
        run_execution = _execute_run(
            active_executor,
            run_config,
            worker_runtime=runtime,
        )
    except PT0NonFinitePayload as exc:
        return _non_finite_payload_result(
            candidate_id,
            spec,
            key,
            str(exc),
        )
    except BackendUnavailableError as exc:
        raise BackendUnavailableAbort(
            str(exc),
            patch=validated_patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key_value=key,
        ) from exc
    except RunnerError as exc:
        if _is_backend_unavailable_message(str(exc)):
            raise BackendUnavailableAbort(
                str(exc),
                patch=validated_patch,
                candidate_id=candidate_id,
                eval_spec=spec,
                cache_key_value=key,
            ) from exc
        if _is_non_finite_payload_message(str(exc)):
            return _non_finite_payload_result(
                candidate_id,
                spec,
                key,
                str(exc),
            )
        if _is_inventory_overdraw_message(str(exc)):
            return _invalid_recipe_result(
                candidate_id,
                spec,
                key,
                str(exc),
            )
        raise EngineBugAbort(
            f"{type(exc).__name__}: {exc}",
            patch=validated_patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key_value=key,
        ) from exc
    except (ProposalRejected, OverdraftError) as exc:
        return _invalid_recipe_result(
            candidate_id,
            spec,
            key,
            f"{type(exc).__name__}: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 -- crashes abort the study
        raise EngineBugAbort(
            f"{type(exc).__name__}: {exc}",
            patch=validated_patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key_value=key,
        ) from exc

    status = str(getattr(run_execution, "status", "ok"))
    error_message = str(getattr(run_execution, "error_message", ""))
    if status == "failed":
        if _is_backend_unavailable_message(error_message):
            raise BackendUnavailableAbort(
                error_message,
                patch=validated_patch,
                candidate_id=candidate_id,
                eval_spec=spec,
                cache_key_value=key,
            )
        if _is_non_finite_payload_message(error_message):
            return _non_finite_payload_result(
                candidate_id,
                spec,
                key,
                error_message,
                run_execution=run_execution,
                profile=profile,
            )
        if _is_inventory_overdraw_message(error_message):
            return _invalid_recipe_result(
                candidate_id,
                spec,
                key,
                error_message,
                run_execution=run_execution,
                profile=profile,
            )
        raise EngineBugAbort(
            error_message or "run executor failed",
            patch=validated_patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key_value=key,
        )

    backend_status = _latest_backend_status(run_execution)
    if _has_out_of_domain_backend_signal(
        run_execution,
        backend_status=backend_status,
    ):
        return _out_of_domain_result(
            candidate_id,
            spec,
            key,
            run_execution,
            profile,
            patch=validated_patch,
            constraints=active_constraints,
        )

    _abort_on_non_authoritative_backend_status(
        run_execution,
        spec=spec,
        patch=validated_patch,
        candidate_id=candidate_id,
        key=key,
    )

    _abort_on_mass_balance_breach(
        run_execution,
        patch=validated_patch,
        candidate_id=candidate_id,
        eval_spec=spec,
        key=key,
    )

    if status == "refused":
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key=key,
            feasible=False,
            failure_category=FailureCategory.PHYSICS_REFUSED,
            run_reference=_run_reference(run_execution, profile),
            notes=tuple(
                note for note in (
                    str(getattr(run_execution, "reason", "")),
                    error_message,
                )
                if note
            ),
        )

    feasibility = (active_constraints or PhysicsConstraintSet()).evaluate(run_execution.trace)
    if not feasibility.feasible:
        return _infeasible_result(candidate_id, spec, key, feasibility, run_execution, profile)
    target_reason = composition_target_infeasible_reason(profile)
    if target_reason:
        return _target_infeasible_result(
            candidate_id,
            spec,
            key,
            run_execution,
            profile,
            gate="composition_target_order",
            detail=target_reason,
            notes=(target_reason,),
        )
    if composition_targets_require_terminal_rump(profile):
        if _trace_has_unearned_rump_terminal(run_execution):
            return _target_infeasible_result(
                candidate_id,
                spec,
                key,
                run_execution,
                profile,
                gate="rump_terminal",
                detail="rump_terminal_unproven",
                notes=("rump_terminal_unproven",),
            )
        completion_problem = _terminal_rump_completed_run_problem(run_execution)
        if completion_problem is not None:
            return _target_infeasible_result(
                candidate_id,
                spec,
                key,
                run_execution,
                profile,
                gate="rump_terminal",
                detail=completion_problem,
                notes=(completion_problem,),
            )

    try:
        objectives = compute_objectives(profile, run_execution)
    except ObjectiveComputationError as exc:
        raise EngineBugAbort(
            str(exc),
            patch=validated_patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key_value=key,
        ) from exc

    trace_payload = _composition_target_trace_payload(profile, objectives, run_execution)
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=key,
        feasible=True,
        objectives=objectives,
        feasibility_margins=feasibility.margins,
        failing_gates=(),
        run_reference=_run_reference(run_execution, profile, trace_payload=trace_payload),
    )


def _execute_run(
    executor: Any,
    run_config: Any,
    *,
    worker_runtime: Any | None,
) -> Any:
    execute = executor.execute
    if worker_runtime is not None and _accepts_keyword(execute, "worker_runtime"):
        return execute(run_config, worker_runtime=worker_runtime)
    return execute(run_config)


def _accepts_keyword(callable_obj: Any, keyword: str) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD or name == keyword
        for name, parameter in signature.parameters.items()
    )


def _build_eval_inputs(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    profile: Mapping[str, Any],
    schema: RecipeSchema,
    *,
    constraints: Any | None = None,
) -> tuple[EvalSpec, Any]:
    bundle = load_config_bundle(DEFAULT_DATA_DIR)
    if feedstock_id not in bundle.feedstocks:
        raise EvaluationInputError(f"unknown feedstock_id {feedstock_id!r}")
    profile = validate_profile(
        profile,
        expected_feedstock=feedstock_id,
        source="<profile>",
        schema=schema,
    )
    feedstock = bundle.feedstocks[feedstock_id]
    profile_id = str(profile.get("profile_id") or profile.get("id") or "inline-profile")
    profile_digest = _profile_digest(profile)
    target_metadata = composition_target_eval_metadata(profile)
    run_options = _run_options(profile, fidelity)
    _validate_c5_eval_options(run_options, bundle.setpoints)
    setpoints_patch = schema.to_setpoints_patch(patch)
    for digest_key in ("setpoints", "feedstocks", "vapor_pressures"):
        if digest_key not in bundle.digests:
            raise EvaluationInputError(f"missing config digest {digest_key!r}")

    run_config = PyrolysisRun(
        feedstock_id=feedstock_id,
        campaign=str(run_options["campaign"]),
        hours=int(run_options["hours"]),
        additives_kg=dict(run_options["additives_kg"]),
        mass_kg=float(run_options["mass_kg"]),
        backend_name=str(run_options["backend_name"]),
        reduced_real_cache=run_options["reduced_real_cache"],
        setpoints_patch=setpoints_patch,
        runtime_campaign_overrides=dict(run_options["runtime_campaign_overrides"]),
        track=str(run_options["track"]),
        c5_enabled=bool(run_options["c5_enabled"]),
        mre_target_species=str(run_options["mre_target_species"]),
        mre_max_voltage_V=float(run_options["mre_max_voltage_V"]),
    )._session_config()

    spec = EvalSpec(
        recipe_id=patch.recipe_id(),
        feedstock_recipe_digest=feedstock_recipe_digest(feedstock),
        feedstock_id=feedstock_id,
        profile_id=profile_id,
        fidelity=fidelity,
        code_version=current_code_version(),
        data_digests={
            "setpoints": bundle.digests["setpoints"],
            "feedstocks": bundle.digests["feedstocks"],
            "vapor_pressures": bundle.digests["vapor_pressures"],
            "profile": profile_digest,
            "physics_constraints": physics_constraints_digest(constraints),
        },
        campaign=str(run_options["campaign"]),
        hours=int(run_options["hours"]),
        mass_kg=float(run_options["mass_kg"]),
        additives_kg=run_config.additives_kg,
        track=str(run_options["track"]),
        backend_name=str(run_options["backend_name"]),
        c5_enabled=bool(run_options["c5_enabled"]),
        mre_max_voltage_V=float(run_options["mre_max_voltage_V"]),
        mre_target_species=str(run_options["mre_target_species"]),
        runtime_campaign_overrides=run_options["runtime_campaign_overrides"],
        chemistry_kernel=run_options["chemistry_kernel"],
        target_spec_id=str(target_metadata["target_spec_id"]),
        target_spec_digest=str(target_metadata["target_spec_digest"]),
        target_maturity=target_metadata["target_maturity"],
        target_provenance=target_metadata["target_provenance"],
    )
    return spec, run_config


def _run_options(profile: Mapping[str, Any], fidelity: str) -> Mapping[str, Any]:
    fidelity_options = profile.get("fidelities", {})
    selected = {}
    if isinstance(fidelity_options, Mapping):
        raw_selected = fidelity_options.get(fidelity, {})
        if isinstance(raw_selected, Mapping):
            selected = dict(raw_selected)
    merged = dict(profile.get("run", {}) if isinstance(profile.get("run"), Mapping) else {})
    merged.update(selected)
    backend_name = str(merged.get("backend_name", "stub"))
    raw_cache_config = (
        merged.get("reduced_real_cache")
        if backend_name == "cached-real"
        else None
    )
    reduced_real_cache = (
        dict(raw_cache_config)
        if isinstance(raw_cache_config, Mapping)
        else None
    )
    return MappingProxyType({
        "campaign": merged.get("campaign", "C0"),
        "hours": int(merged.get("hours", 24)),
        "mass_kg": float(merged.get("mass_kg", 1000.0)),
        "additives_kg": dict(merged.get("additives_kg", {}) or {}),
        "track": merged.get("track", "pyrolysis"),
        "backend_name": backend_name,
        "c5_enabled": bool(merged.get("c5_enabled", False)),
        "mre_max_voltage_V": float(merged.get("mre_max_voltage_V", 0.0) or 0.0),
        "mre_target_species": str(merged.get("mre_target_species", "") or ""),
        "reduced_real_cache": reduced_real_cache,
        "runtime_campaign_overrides": dict(
            merged.get("runtime_campaign_overrides", {}) or {}
        ),
        "chemistry_kernel": dict(merged.get("chemistry_kernel", {}) or {}),
    })


def _validate_c5_eval_options(
    run_options: Mapping[str, Any],
    setpoints: Mapping[str, Any],
) -> None:
    if not bool(run_options.get("c5_enabled", False)):
        return
    max_voltage = float(run_options.get("mre_max_voltage_V", 0.0) or 0.0)
    if max_voltage > 0.0:
        return
    target = str(run_options.get("mre_target_species", "") or "").strip()
    sequence = parse_ladder_from_setpoints(dict(setpoints))
    if target and max_voltage_for_target(target, sequence) > 0.0:
        return
    raise EvaluationInputError(
        "c5_enabled requires positive mre_max_voltage_V or canonical "
        f"mre_target_species; invalid mre_target_species {target!r}"
    )


def _profile_digest(profile: Mapping[str, Any]) -> str:
    normalized = normalize_canonical_value(profile)
    return hashlib.sha256(canonical_json_dumps(normalized).encode("utf-8")).hexdigest()


def _composition_target_constraints(
    profile: Mapping[str, Any],
    constraints: PhysicsConstraintSet | None,
) -> PhysicsConstraintSet | None:
    active = constraints or physics_constraints_from_profile(profile)
    try:
        requires_coating = composition_targets_require_coating(profile)
    except ValueError:
        return active
    if not requires_coating:
        return active
    if not hasattr(active, "active_gates"):
        return active
    if "coating" in active.active_gates:
        return active
    return replace(active, active_gates=(*active.active_gates, "coating"))


def _infeasible_result(
    candidate_id: str | None,
    spec: EvalSpec,
    key: str,
    feasibility: FeasibilityResult,
    run_execution: Any,
    profile: Mapping[str, Any],
    *,
    notes: tuple[str, ...] = (),
    trace_payload: Mapping[str, Any] | None = None,
) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=key,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
        feasibility_margins=feasibility.margins,
        failing_gates=feasibility.failing_gates,
        run_reference=_run_reference(run_execution, profile, trace_payload=trace_payload),
        notes=notes,
    )


def _target_infeasible_result(
    candidate_id: str | None,
    spec: EvalSpec,
    key: str,
    run_execution: Any,
    profile: Mapping[str, Any],
    *,
    gate: str,
    detail: str,
    notes: tuple[str, ...],
    trace_payload: Mapping[str, Any] | None = None,
) -> ScoredResult:
    margin = _target_infeasible_margin(gate, detail)
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=key,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
        feasibility_margins={gate: margin},
        failing_gates=(gate,),
        run_reference=_run_reference(
            run_execution,
            profile,
            trace_payload=trace_payload,
        ),
        notes=notes,
    )


def _target_infeasible_margin(gate: str, detail: str) -> GateMargin:
    threshold = ThresholdSpec(
        id=f"{gate}_required",
        value=1.0,
        units="boolean",
        source="code_default",
        source_ref="simulator.optimize.evaluate: composition target hard gate",
    )
    return GateMargin(
        gate=gate,
        feasible=False,
        margin=-1.0,
        threshold=threshold,
        observed=0.0,
        detail=detail,
    )


def _composition_target_trace_payload(
    profile: Mapping[str, Any],
    objectives: ObjectiveVector,
    run_execution: Any,
    *,
    base_trace: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    entries = []
    for metric, raw in objectives.evidence.items():
        if not isinstance(raw, MappingABC):
            continue
        payload = raw.get("composition_target")
        if isinstance(payload, MappingABC):
            entries.append({"metric": str(metric), **dict(payload)})
    if not entries:
        return base_trace

    trace = base_trace if base_trace is not None else getattr(run_execution, "trace", None)
    merged: dict[str, Any] = {}
    if isinstance(trace, MappingABC):
        merged.update(_compact_jsonable(trace))
    else:
        backend_status = _latest_backend_status(run_execution)
        if backend_status is not None:
            merged["backend_status"] = backend_status
        backend_authoritative = _backend_authoritative(run_execution)
        if backend_authoritative is not None:
            merged["backend_authoritative"] = backend_authoritative

    metadata = dict(composition_target_eval_metadata(profile))
    target_payload: Mapping[str, Any]
    if len(entries) == 1:
        target_payload = {**metadata, **entries[0]}
    else:
        target_payload = {**metadata, "targets": entries}
    merged["composition_target"] = _compact_jsonable(target_payload)
    return MappingProxyType(merged)


def _trace_has_earned_rump_terminal(run_execution: Any) -> bool:
    trace = getattr(run_execution, "trace", None)
    payload = _carrier_value(trace, "rump_terminal")
    if not isinstance(payload, MappingABC):
        return False
    return str(payload.get("status", "")) == "earned"


def _trace_has_unearned_rump_terminal(run_execution: Any) -> bool:
    trace = getattr(run_execution, "trace", None)
    payload = _carrier_value(trace, "rump_terminal")
    if not isinstance(payload, MappingABC):
        return False
    return str(payload.get("status", "")) != "earned"


def _terminal_rump_completed_run_problem(run_execution: Any) -> str | None:
    if _trace_has_earned_rump_terminal(run_execution):
        return None
    backend_status = _latest_backend_status(run_execution)
    if backend_status == "ok":
        return None
    if backend_status is None:
        return "rump_terminal_completion_unknown"
    return f"rump_terminal_completion_not_completed: backend_status={backend_status}"


def _out_of_domain_result(
    candidate_id: str | None,
    spec: EvalSpec,
    key: str,
    run_execution: Any,
    profile: Mapping[str, Any],
    *,
    patch: RecipePatch,
    constraints: PhysicsConstraintSet | None,
) -> ScoredResult:
    assessment = _assess_rump_terminal(run_execution)
    if assessment.earned:
        _abort_on_mass_balance_breach(
            run_execution,
            patch=patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            key=key,
        )
        feasibility = (constraints or PhysicsConstraintSet()).evaluate(run_execution.trace)
        if not feasibility.feasible:
            return _infeasible_result(
                candidate_id,
                spec,
                key,
                feasibility,
                run_execution,
                profile,
                notes=assessment.notes,
                trace_payload=assessment.trace_payload,
            )
        scoring_execution = _TraceOverrideRunExecution(
            run_execution,
            assessment.trace_payload,
        )
        try:
            objectives = compute_objectives(profile, scoring_execution)
        except ObjectiveComputationError as exc:
            raise EngineBugAbort(
                str(exc),
                patch=patch,
                candidate_id=candidate_id,
                eval_spec=spec,
                cache_key_value=key,
            ) from exc
        trace_payload = _composition_target_trace_payload(
            profile,
            objectives,
            scoring_execution,
            base_trace=assessment.trace_payload,
        )
        margins = dict(feasibility.margins)
        margins["rump_terminal"] = _rump_terminal_margin(assessment)
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key=key,
            feasible=True,
            objectives=objectives,
            feasibility_margins=margins,
            failing_gates=(),
            run_reference=_run_reference(
                scoring_execution,
                profile,
                trace_payload=trace_payload,
            ),
            notes=assessment.notes,
        )

    if composition_targets_require_terminal_rump(profile):
        return _target_infeasible_result(
            candidate_id,
            spec,
            key,
            run_execution,
            profile,
            gate="rump_terminal",
            detail="rump_terminal_unproven",
            notes=(*assessment.notes, "rump_terminal_unproven"),
            trace_payload=assessment.trace_payload,
        )

    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=key,
        feasible=False,
        failure_category=FailureCategory.OUT_OF_DOMAIN,
        feasibility_margins={"backend_domain": _out_of_domain_margin()},
        failing_gates=("backend_domain",),
        run_reference=_run_reference(
            run_execution,
            profile,
            trace_payload=assessment.trace_payload,
        ),
        notes=assessment.notes,
    )


def _out_of_domain_margin() -> GateMargin:
    threshold = ThresholdSpec(
        id="alphamelts_domain",
        value=1.0,
        units="boolean",
        source="code_default",
        source_ref="simulator.optimize.evaluate: backend_status out_of_domain",
    )
    return GateMargin(
        gate="backend_domain",
        feasible=False,
        margin=-1.0,
        threshold=threshold,
        observed=0.0,
        detail="authoritative backend rejected composition as out of domain",
    )


def _assess_rump_terminal(run_execution: Any) -> RumpTerminalAssessment:
    diagnostics = _out_of_domain_diagnostics(run_execution)
    crash_point = _crash_point_from_diagnostics(diagnostics)
    if crash_point is None:
        return _rump_terminal_not_earned(
            run_execution,
            diagnostics,
            reason="missing_crash_point",
        )

    T_crash_C = _finite_optional_float(crash_point.get("temperature_C"))
    pressure_bar = _finite_optional_float(crash_point.get("pressure_bar"))
    fO2_log = _finite_optional_float(crash_point.get("fO2_log"))
    if T_crash_C is None or pressure_bar is None or fO2_log is None:
        return _rump_terminal_not_earned(
            run_execution,
            diagnostics,
            reason="incomplete_crash_point_controls",
            crash_point=crash_point,
        )
    composition_mol_by_account = _crash_point_composition_mol_by_account(
        crash_point,
        run_execution,
    )
    if composition_mol_by_account is None:
        return _rump_terminal_not_earned(
            run_execution,
            diagnostics,
            reason="missing_crash_point_composition",
            crash_point=crash_point,
        )
    proof_inputs = _rump_terminal_proof_inputs(
        composition_mol_by_account,
        T_crash_C=T_crash_C,
        pressure_bar=pressure_bar,
        fO2_log=fO2_log,
    )
    unproven_curve_proof = _rump_terminal_curve_proof(
        curve_source="liquidus_solidus:kernel",
        composition_derived=False,
        proof_inputs=proof_inputs,
    )

    sim = getattr(run_execution, "simulator", None)
    curve_from_kernel = getattr(sim, "_freeze_gate_curve_from_kernel_liquidus", None)
    interpolate = getattr(sim, "_interpolate_freeze_gate_curve", None)
    if not callable(curve_from_kernel) or not callable(interpolate):
        return _rump_terminal_not_earned(
            run_execution,
            diagnostics,
            reason="kernel_liquidus_unavailable",
            crash_point=crash_point,
            curve_proof=unproven_curve_proof,
        )

    reasons: list[str] = []
    try:
        curve = curve_from_kernel(
            reasons,
            fO2_log=fO2_log,
            temperature_C=T_crash_C,
            pressure_bar=pressure_bar,
            composition_mol_by_account=composition_mol_by_account,
            allow_parametric=False,
        )
    except Exception as exc:  # noqa: BLE001 - proof source unavailable, not earned
        return _rump_terminal_not_earned(
            run_execution,
            diagnostics,
            reason="rump_terminal_unproven",
            detail=(
                "rump_terminal_unproven: kernel curve not composition-derived; "
                f"{type(exc).__name__}: {exc}"
            ),
            crash_point=crash_point,
            curve_proof=unproven_curve_proof,
        )
    if curve is None:
        return _rump_terminal_not_earned(
            run_execution,
            diagnostics,
            reason="rump_terminal_unproven",
            detail=_rump_terminal_unproven_detail(reasons),
            crash_point=crash_point,
            curve_proof=unproven_curve_proof,
        )

    curve_source = str(curve.get("source") or "")
    composition_derived = bool(curve.get("composition_derived"))
    curve_proof = _rump_terminal_curve_proof(
        curve_source=curve_source,
        composition_derived=composition_derived,
        proof_inputs=proof_inputs,
    )
    if not composition_derived:
        return _rump_terminal_not_earned(
            run_execution,
            diagnostics,
            reason="rump_terminal_unproven",
            detail="rump_terminal_unproven: kernel curve not composition-derived",
            crash_point=crash_point,
            curve_proof=curve_proof,
        )

    try:
        liquid_fraction = float(interpolate(curve, T_crash_C))
        solidus_T_C = float(curve["solidus_T_C"])
        liquidus_T_C = float(curve["liquidus_T_C"])
    except (TypeError, ValueError, KeyError) as exc:
        return _rump_terminal_not_earned(
            run_execution,
            diagnostics,
            reason="kernel_liquidus_invalid",
            detail=f"{type(exc).__name__}: {exc}",
            crash_point=crash_point,
            curve_proof=curve_proof,
        )
    if not all(
        math.isfinite(value)
        for value in (liquid_fraction, solidus_T_C, liquidus_T_C)
    ):
        return _rump_terminal_not_earned(
            run_execution,
            diagnostics,
            reason="kernel_liquidus_invalid",
            crash_point=crash_point,
            curve_proof=curve_proof,
        )

    if liquid_fraction <= RUMP_TERMINAL_LIQUID_FRACTION_MAX:
        note = _rump_terminal_note(
            "earned_by=kernel_liquidus",
            liquid_fraction=liquid_fraction,
            solidus_T_C=solidus_T_C,
            T_crash_C=T_crash_C,
        )
        trace = _out_of_domain_trace_payload(
            run_execution,
            diagnostics,
            crash_point=crash_point,
            rump_terminal={
                "status": "earned",
                "earned_by": "kernel_liquidus",
                "liquid_fraction": liquid_fraction,
                "liquid_fraction_threshold": RUMP_TERMINAL_LIQUID_FRACTION_MAX,
                "solidus_T_C": solidus_T_C,
                "liquidus_T_C": liquidus_T_C,
                "T_crash_C": T_crash_C,
                **curve_proof,
            },
        )
        return RumpTerminalAssessment(
            earned=True,
            reason="earned_by_kernel_liquidus",
            notes=("backend_status=out_of_domain", note),
            trace_payload=trace,
            liquid_fraction=liquid_fraction,
            solidus_T_C=solidus_T_C,
            liquidus_T_C=liquidus_T_C,
            T_crash_C=T_crash_C,
        )

    note = _rump_terminal_note(
        "not_earned reason=kernel_liquidus_disagree",
        liquid_fraction=liquid_fraction,
        solidus_T_C=solidus_T_C,
        T_crash_C=T_crash_C,
    )
    trace = _out_of_domain_trace_payload(
        run_execution,
        diagnostics,
        crash_point=crash_point,
        rump_terminal={
            "status": "not_earned",
            "reason": "kernel_liquidus_disagree",
            "liquid_fraction": liquid_fraction,
            "liquid_fraction_threshold": RUMP_TERMINAL_LIQUID_FRACTION_MAX,
            "solidus_T_C": solidus_T_C,
            "liquidus_T_C": liquidus_T_C,
            "T_crash_C": T_crash_C,
            **curve_proof,
        },
    )
    return RumpTerminalAssessment(
        earned=False,
        reason="kernel_liquidus_disagree",
        notes=("backend_status=out_of_domain", note),
        trace_payload=trace,
        liquid_fraction=liquid_fraction,
        solidus_T_C=solidus_T_C,
        liquidus_T_C=liquidus_T_C,
        T_crash_C=T_crash_C,
    )


def _rump_terminal_not_earned(
    run_execution: Any,
    diagnostics: Mapping[str, Any],
    *,
    reason: str,
    detail: str = "",
    crash_point: Mapping[str, Any] | None = None,
    proof_inputs: Mapping[str, Any] | None = None,
    curve_proof: Mapping[str, Any] | None = None,
) -> RumpTerminalAssessment:
    note = f"rump_terminal: not_earned reason={reason}"
    if detail:
        note = f"{note} detail={detail}"
    proof_payload: dict[str, Any] = {}
    if proof_inputs is not None:
        proof_payload["proof_inputs"] = _compact_jsonable(proof_inputs)
    if curve_proof is not None:
        proof_payload.update(_compact_jsonable(curve_proof))
    trace = _out_of_domain_trace_payload(
        run_execution,
        diagnostics,
        crash_point=crash_point,
        rump_terminal={
            "status": "not_earned",
            "reason": reason,
            **({"detail": detail} if detail else {}),
            **proof_payload,
        },
    )
    return RumpTerminalAssessment(
        earned=False,
        reason=reason,
        notes=("backend_status=out_of_domain", note),
        trace_payload=trace,
    )


def _rump_terminal_margin(assessment: RumpTerminalAssessment) -> GateMargin:
    observed = float(assessment.liquid_fraction or 0.0)
    threshold = ThresholdSpec(
        id="rump_terminal_liquid_fraction_max",
        value=RUMP_TERMINAL_LIQUID_FRACTION_MAX,
        units="fraction",
        source="code_default",
        source_ref=(
            "simulator.optimize.evaluate:"
            "RUMP_TERMINAL_LIQUID_FRACTION_MAX"
        ),
    )
    return GateMargin(
        gate="rump_terminal",
        feasible=True,
        margin=RUMP_TERMINAL_LIQUID_FRACTION_MAX - observed,
        threshold=threshold,
        observed=observed,
        detail="kernel liquidus voted the out_of_domain crash point sub-solidus",
    )


def _rump_terminal_note(
    prefix: str,
    *,
    liquid_fraction: float,
    solidus_T_C: float,
    T_crash_C: float,
) -> str:
    return (
        f"rump_terminal: {prefix}, "
        f"liquid_fraction={liquid_fraction:.12g}, "
        f"solidus_T_C={solidus_T_C:.12g}, "
        f"T_crash_C={T_crash_C:.12g}"
    )


def _out_of_domain_diagnostics(run_execution: Any) -> Mapping[str, Any]:
    sim = getattr(run_execution, "simulator", None)
    candidates = (
        getattr(sim, "_last_out_of_domain_diagnostics", None),
        getattr(sim, "_last_backend_diagnostics", None),
        _carrier_value(getattr(run_execution, "trace", None), "backend_diagnostics"),
        _carrier_value(getattr(run_execution, "trace", None), "out_of_domain_crash_point"),
    )
    for candidate in candidates:
        if isinstance(candidate, MappingABC):
            if "out_of_domain_crash_point" in candidate:
                return _compact_jsonable(candidate)
            if candidate is candidates[-1]:
                return {"out_of_domain_crash_point": _compact_jsonable(candidate)}
    return {}


def _crash_point_from_diagnostics(
    diagnostics: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    raw = diagnostics.get("out_of_domain_crash_point")
    if raw is None:
        raw = diagnostics.get("crash_point")
    if not isinstance(raw, MappingABC):
        return None
    return _compact_jsonable(raw)


def _crash_point_has_composition(crash_point: Mapping[str, Any]) -> bool:
    for key in (
        "composition_mol",
        "composition_wt_pct",
        "composition_melts_wt_pct",
    ):
        raw = crash_point.get(key)
        if isinstance(raw, MappingABC) and any(
            _finite_optional_float(value) is not None
            for value in raw.values()
        ):
            return True
    raw_by_account = crash_point.get("composition_mol_by_account")
    if isinstance(raw_by_account, MappingABC):
        for species_mol in raw_by_account.values():
            if isinstance(species_mol, MappingABC) and any(
                _finite_optional_float(value) is not None
                for value in species_mol.values()
            ):
                return True
    return False


def _crash_point_composition_mol_by_account(
    crash_point: Mapping[str, Any],
    run_execution: Any,
) -> dict[str, dict[str, float]] | None:
    raw_by_account = crash_point.get("composition_mol_by_account")
    if isinstance(raw_by_account, MappingABC):
        by_account = _finite_nested_float_mapping(raw_by_account)
        if by_account:
            return by_account

    raw_mol = crash_point.get("composition_mol")
    if isinstance(raw_mol, MappingABC):
        mol = _finite_float_mapping(raw_mol)
        if mol:
            return {"process.cleaned_melt": mol}

    for key in ("composition_melts_wt_pct", "composition_wt_pct"):
        raw_wt_pct = crash_point.get(key)
        if isinstance(raw_wt_pct, MappingABC):
            mol = _composition_wt_pct_to_mol(raw_wt_pct, run_execution)
            if mol:
                return {"process.cleaned_melt": mol}
    return None


def _finite_nested_float_mapping(
    values: Mapping[Any, Any],
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for account, species_mol in values.items():
        if not isinstance(species_mol, MappingABC):
            continue
        cleaned = _finite_float_mapping(species_mol)
        if cleaned:
            result[str(account)] = cleaned
    return result


def _composition_wt_pct_to_mol(
    values: Mapping[Any, Any],
    run_execution: Any,
) -> dict[str, float]:
    sim = getattr(run_execution, "simulator", None)
    registry = dict(getattr(sim, "species_formula_registry", {}) or {})
    result: dict[str, float] = {}
    for species, raw_mass in values.items():
        mass_basis = _finite_optional_float(raw_mass)
        if mass_basis is None or mass_basis <= 0.0:
            continue
        try:
            formula = resolve_species_formula(str(species), registry)
        except Exception:  # noqa: BLE001 - unregistered species cannot prove
            continue
        mol = mass_basis / formula.molar_mass_kg_per_mol()
        if math.isfinite(mol) and mol > 0.0:
            result[str(species)] = mol
    return result


def _rump_terminal_proof_inputs(
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
    *,
    T_crash_C: float,
    pressure_bar: float,
    fO2_log: float,
) -> dict[str, Any]:
    normalized = normalize_canonical_value(composition_mol_by_account)
    digest = hashlib.sha256(
        canonical_json_dumps(normalized).encode("utf-8"),
    ).hexdigest()
    return {
        "composition_digest": digest,
        "T_crash_C": T_crash_C,
        "pressure_bar": pressure_bar,
        "fO2_log": fO2_log,
    }


def _rump_terminal_curve_proof(
    *,
    curve_source: str,
    composition_derived: bool,
    proof_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "curve_source": curve_source,
        "composition_derived": composition_derived,
        "proof_inputs": dict(proof_inputs),
    }


def _rump_terminal_unproven_detail(reasons: list[str]) -> str:
    detail = "rump_terminal_unproven: kernel curve not composition-derived"
    if reasons:
        return f"{detail}; {'; '.join(reasons[-4:])}"
    return detail


def _out_of_domain_trace_payload(
    run_execution: Any,
    diagnostics: Mapping[str, Any],
    *,
    crash_point: Mapping[str, Any] | None,
    rump_terminal: Mapping[str, Any],
) -> Mapping[str, Any]:
    payload: dict[str, Any] = {
        "backend_status": "out_of_domain",
        "rump_terminal": _compact_jsonable(rump_terminal),
    }
    backend_authoritative = _backend_authoritative(run_execution)
    if backend_authoritative is not None:
        payload["backend_authoritative"] = backend_authoritative
    if diagnostics:
        payload["backend_diagnostics"] = _compact_jsonable(diagnostics)
    if crash_point is not None:
        payload["out_of_domain_crash_point"] = _compact_jsonable(crash_point)
    rump_kg = _terminal_rump_by_species_kg(run_execution)
    if rump_kg:
        payload["terminal_rump_by_species_kg"] = rump_kg
    return payload


def _terminal_rump_by_species_kg(run_execution: Any) -> dict[str, float]:
    trace = getattr(run_execution, "trace", None)
    raw = _carrier_value(trace, "terminal_rump_by_species_kg")
    if isinstance(raw, MappingABC):
        return _finite_float_mapping(raw)
    sim = getattr(run_execution, "simulator", None)
    getter = getattr(sim, "_terminal_rump_by_species", None)
    if callable(getter):
        try:
            return _finite_float_mapping(getter() or {})
        except (TypeError, ValueError):
            return {}
    return {}


def _carrier_value(carrier: Any, key: str) -> Any:
    if carrier is None:
        return None
    if isinstance(carrier, MappingABC):
        return carrier.get(key)
    return getattr(carrier, key, None)


def _finite_float_mapping(values: Mapping[Any, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, raw in values.items():
        value = _finite_optional_float(raw)
        if value is not None and value > 0.0:
            result[str(key)] = value
    return result


def _finite_optional_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _compact_jsonable(value: Any) -> Any:
    if isinstance(value, MappingABC):
        return {
            str(key): _compact_jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_compact_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_compact_jsonable(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    return numeric if math.isfinite(numeric) else str(value)


def _non_finite_payload_result(
    candidate_id: str | None,
    spec: EvalSpec,
    key: str,
    error_message: str,
    *,
    run_execution: Any | None = None,
    profile: Mapping[str, Any] | None = None,
) -> ScoredResult:
    run_reference = (
        _run_reference(run_execution, profile or {})
        if run_execution is not None
        else RunReference(
            status="failed",
            error_message=error_message,
            reason="non_finite_payload",
            trace={"backend_status": "ok"},
            backend_status="ok",
            backend_authoritative=True,
        )
    )
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=key,
        feasible=False,
        failure_category=FailureCategory.NON_FINITE_PAYLOAD,
        feasibility_margins={"non_finite_payload": _non_finite_payload_margin()},
        failing_gates=("non_finite_payload",),
        run_reference=run_reference,
        notes=(
            "CALC_BUG: PT-0 payload contained a non-finite derived value",
            error_message,
        ),
    )


def _non_finite_payload_margin() -> GateMargin:
    threshold = ThresholdSpec(
        id="pt0_payload_finite",
        value=0.0,
        units="nonfinite_count",
        source="code_default",
        source_ref="simulator.optimize.evaluate: PT0NonFinitePayload",
    )
    return GateMargin(
        gate="non_finite_payload",
        feasible=False,
        margin=-1.0,
        threshold=threshold,
        observed=1.0,
        detail="PT-0 payload contains a non-finite derived calc value",
    )


def _invalid_recipe_result(
    candidate_id: str | None,
    spec: EvalSpec,
    key: str,
    error_message: str,
    *,
    run_execution: Any | None = None,
    profile: Mapping[str, Any] | None = None,
) -> ScoredResult:
    overdraw_kg = _extract_overdraw_kg(error_message)
    run_reference = (
        _run_reference(run_execution, profile or {})
        if run_execution is not None
        else RunReference(
            status="failed",
            error_message=error_message,
            reason="invalid_recipe",
            trace={"backend_status": "ok"},
            backend_status="ok",
            backend_authoritative=True,
        )
    )
    notes = [
        "ProposalRejected: recipe attempted to draw more inventory than available",
        error_message,
    ]
    if overdraw_kg is not None:
        notes.append(f"overdraw_kg={overdraw_kg:.12g}")
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=key,
        feasible=False,
        failure_category=FailureCategory.INVALID_RECIPE,
        feasibility_margins={
            "inventory_overdraw": _invalid_recipe_margin(overdraw_kg)
        },
        failing_gates=("inventory_overdraw",),
        run_reference=run_reference,
        notes=tuple(notes),
    )


def _invalid_recipe_margin(overdraw_kg: float | None) -> GateMargin:
    observed = overdraw_kg if overdraw_kg is not None else 1.0
    threshold = ThresholdSpec(
        id="inventory_overdraw_kg",
        value=0.0,
        units="kg",
        source="code_default",
        source_ref="simulator.optimize.evaluate: ProposalRejected inventory overdraw",
    )
    return GateMargin(
        gate="inventory_overdraw",
        feasible=False,
        margin=-float(observed),
        threshold=threshold,
        observed=float(observed),
        detail="ledger proposal would overdraw an inventory account",
    )


def _run_reference(
    run_execution: Any,
    profile: Mapping[str, Any],
    *,
    trace_payload: Mapping[str, Any] | None = None,
) -> RunReference:
    summary: Mapping[str, Any] = {}
    if str(getattr(run_execution, "status", "ok")) != "refused":
        try:
            summary = product_summary(run_execution, profile)
        except ObjectiveComputationError:
            summary = {}
    return RunReference(
        status=str(getattr(run_execution, "status", "ok")),
        error_message=str(getattr(run_execution, "error_message", "")),
        reason=str(getattr(run_execution, "reason", "")),
        trace=trace_payload if trace_payload is not None else getattr(run_execution, "trace", None),
        product_summary=summary,
        backend_status=_latest_backend_status(run_execution),
        backend_authoritative=_backend_authoritative(run_execution),
    )


def _abort_on_non_authoritative_backend_status(
    run_execution: Any,
    *,
    spec: EvalSpec,
    patch: RecipePatch,
    candidate_id: str | None,
    key: str,
) -> None:
    if spec.backend_name == "stub":
        return
    backend_status = _latest_backend_status(run_execution)
    if backend_status is None:
        raise BackendUnavailableAbort(
            f"backend_status missing for real backend {spec.backend_name!r}",
            patch=patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key_value=key,
        )
    if backend_status != "ok":
        raise BackendUnavailableAbort(
            "backend_status="
            f"{backend_status!r} for real backend {spec.backend_name!r}",
            patch=patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key_value=key,
        )
    if _backend_authoritative(run_execution) is not True:
        raise BackendUnavailableAbort(
            "backend_authoritative is not True for real backend "
            f"{spec.backend_name!r}",
            patch=patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key_value=key,
        )


def _latest_backend_status(run_execution: Any) -> str | None:
    return _select_backend_status(_backend_statuses_from_run_execution(run_execution))


def _backend_authoritative(run_execution: Any) -> bool | None:
    raw = getattr(run_execution, "backend_authoritative", None)
    return bool(raw) if raw is not None else None


def _backend_status_from_carrier(carrier: Any) -> str | None:
    return _select_backend_status(_backend_statuses_from_carrier(carrier))


def _has_out_of_domain_backend_signal(
    run_execution: Any,
    *,
    backend_status: str | None = None,
) -> bool:
    if backend_status == "out_of_domain":
        return True
    diagnostics = _out_of_domain_diagnostics(run_execution)
    return _crash_point_from_diagnostics(diagnostics) is not None


def _backend_authoritative_from_carrier(carrier: Any) -> bool | None:
    if carrier is None:
        return None
    if isinstance(carrier, MappingABC):
        raw = carrier.get("backend_authoritative")
        return bool(raw) if raw is not None else None
    raw = getattr(carrier, "backend_authoritative", None)
    return bool(raw) if raw is not None else None


def _latest_backend_status_from_sequence(value: Any) -> str | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    return _select_backend_status(
        status
        for item in value
        for status in _backend_statuses_from_carrier(item)
    )


def _backend_statuses_from_run_execution(run_execution: Any) -> tuple[str, ...]:
    sim = getattr(run_execution, "simulator", None)
    carriers = (
        run_execution,
        getattr(run_execution, "trace", None),
        getattr(sim, "_last_backend_diagnostics", None),
        getattr(sim, "_last_out_of_domain_diagnostics", None),
    )
    return tuple(
        status
        for carrier in carriers
        for status in _backend_statuses_from_carrier(carrier)
    )


def _backend_statuses_from_carrier(carrier: Any) -> tuple[str, ...]:
    if carrier is None:
        return ()
    statuses: list[str] = []
    if isinstance(carrier, MappingABC):
        raw = carrier.get("backend_status")
        if raw is not None:
            statuses.append(str(raw))
        if _carrier_has_crash_point(carrier):
            statuses.append("out_of_domain")
        for key in ("per_hour", "hours"):
            status = _latest_backend_status_from_sequence(carrier.get(key))
            if status is not None:
                statuses.append(status)
        for key in ("trace", "backend_diagnostics", "diagnostics"):
            statuses.extend(_backend_statuses_from_carrier(carrier.get(key)))
        return tuple(statuses)
    raw = getattr(carrier, "backend_status", None)
    if raw is not None:
        statuses.append(str(raw))
    for attr in ("per_hour", "hours"):
        status = _latest_backend_status_from_sequence(getattr(carrier, attr, None))
        if status is not None:
            statuses.append(status)
    for attr in ("trace", "backend_diagnostics", "diagnostics"):
        statuses.extend(_backend_statuses_from_carrier(getattr(carrier, attr, None)))
    return tuple(statuses)


def _carrier_has_crash_point(carrier: Mapping[Any, Any]) -> bool:
    return any(
        isinstance(carrier.get(key), MappingABC)
        for key in ("out_of_domain_crash_point", "crash_point")
    )


def _select_backend_status(statuses: Iterable[str]) -> str | None:
    values = tuple(str(status) for status in statuses if status is not None)
    for status in ("out_of_domain", "unavailable", "not_converged"):
        if status in values:
            return status
    if values:
        return values[-1]
    return None


def _ordered_failing_gates(values: Any) -> tuple[str, ...]:
    if isinstance(values, AbstractSet):
        raise TypeError("failing_gates must be an ordered sequence, not a set")
    gates = tuple(str(gate) for gate in values)
    gate_rank = {gate: index for index, gate in enumerate(GATE_ORDER)}
    return tuple(sorted(gates, key=lambda gate: (gate_rank.get(gate, len(gate_rank)), gate)))


def _ordered_notes(values: Any) -> tuple[str, ...]:
    if isinstance(values, AbstractSet):
        raise TypeError("notes must be an ordered sequence, not a set")
    return tuple(str(note) for note in values)


def _thaw_value(value: Any) -> Any:
    if isinstance(value, MappingABC):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw_value(item) for item in value)
    if isinstance(value, list):
        return [_thaw_value(item) for item in value]
    return value


def _is_backend_unavailable_message(message: str) -> bool:
    lowered = message.lower()
    if lowered.startswith("backend failure:"):
        lowered = lowered.removeprefix("backend failure:").strip()
    explicit_unavailable = (
        "unavailable" in lowered
        or "not initialized" in lowered
        or "not configured" in lowered
        or "config error" in lowered
        or "no module named" in lowered
        or "module not initialized" in lowered
        or "missing binary" in lowered
        or "binary is not configured" in lowered
        or "subprocess transport unavailable" in lowered
    )
    import_failure = lowered.startswith("importerror") or " importerror" in lowered
    return explicit_unavailable or import_failure


def _is_non_finite_payload_message(message: str) -> bool:
    lowered = message.lower()
    return (
        "pt0nonfinitepayload" in lowered
        or "non-finite value in pt-0 payload" in lowered
    )


def _is_inventory_overdraw_message(message: str) -> bool:
    lowered = message.lower()
    return (
        "proposalrejected" in lowered
        or "overdrafterror" in lowered
        or (
            "insufficient available" in lowered
            and "balance would be" in lowered
        )
    )


def _extract_overdraw_kg(message: str) -> float | None:
    match = re.search(r"balance would be\s+([-+0-9.eE]+)\s+kg", message)
    if match is None:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return abs(value)


def _abort_on_mass_balance_breach(
    run_execution: Any,
    *,
    patch: RecipePatch,
    candidate_id: str | None,
    eval_spec: EvalSpec,
    key: str,
) -> None:
    snapshots_raw = getattr(run_execution, "snapshots", None)
    if snapshots_raw is None:
        raise EngineBugAbort(
            "mass balance snapshots missing",
            patch=patch,
            candidate_id=candidate_id,
            eval_spec=eval_spec,
            cache_key_value=key,
        )
    snapshots = tuple(snapshots_raw)
    if not snapshots:
        raise EngineBugAbort(
            "mass balance snapshots empty",
            patch=patch,
            candidate_id=candidate_id,
            eval_spec=eval_spec,
            cache_key_value=key,
        )
    for index, snapshot in enumerate(snapshots):
        raw = getattr(snapshot, "mass_balance_error_pct", None)
        if raw is None:
            raise EngineBugAbort(
                f"mass balance closure at snapshot {index} missing",
                patch=patch,
                candidate_id=candidate_id,
                eval_spec=eval_spec,
                cache_key_value=key,
            )
        try:
            closure_pct = float(raw)
        except (TypeError, ValueError) as exc:
            raise EngineBugAbort(
                f"mass balance closure at snapshot {index} is not numeric: {raw!r}",
                patch=patch,
                candidate_id=candidate_id,
                eval_spec=eval_spec,
                cache_key_value=key,
            ) from exc
        if not math.isfinite(closure_pct):
            raise EngineBugAbort(
                f"mass balance closure at snapshot {index} is non-finite: {raw!r}",
                patch=patch,
                candidate_id=candidate_id,
                eval_spec=eval_spec,
                cache_key_value=key,
            )
        if abs(closure_pct) > MASS_BALANCE_ABORT_PCT:
            raise EngineBugAbort(
                (
                    f"mass balance breach at snapshot {index}: "
                    f"{closure_pct:.12g}% > {MASS_BALANCE_ABORT_PCT:.12g}%"
                ),
                patch=patch,
                candidate_id=candidate_id,
                eval_spec=eval_spec,
                cache_key_value=key,
            )
