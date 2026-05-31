"""Recipe optimizer evaluation loop and failure taxonomy."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import math
from types import MappingProxyType
from typing import Any, Mapping

from simulator.backends import BackendUnavailableError
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
    compute_objectives,
    product_summary,
)
from simulator.optimize.physics import FeasibilityResult, GateMargin, PhysicsConstraintSet
from simulator.optimize.recipe import RecipePatch, RecipeSchema, RecipeValidationError
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun, RunnerError


MASS_BALANCE_ABORT_PCT = 5e-12


class FailureCategory(str, Enum):
    INVALID_PATCH = "invalid_patch"
    INFEASIBLE_RECIPE = "infeasible_recipe"
    PHYSICS_REFUSED = "physics_refused"
    ENGINE_BUG = "engine_bug"
    BACKEND_UNAVAILABLE = "backend_unavailable"


@dataclass(frozen=True)
class RunReference:
    status: str
    error_message: str = ""
    reason: str = ""
    trace: Any = field(default=None, compare=False, repr=False)
    product_summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "product_summary",
            MappingProxyType(dict(self.product_summary)),
        )


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
        object.__setattr__(self, "failing_gates", tuple(self.failing_gates))
        object.__setattr__(self, "notes", tuple(str(note) for note in self.notes))
        if self.feasible:
            if self.failure_category is not None:
                raise ValueError("feasible result cannot carry failure_category")
            if self.objectives is None:
                raise ValueError("feasible result requires objectives")
        else:
            if self.objectives is not None:
                raise ValueError("infeasible result must not carry objectives")


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

    try:
        spec, run_config = _build_eval_inputs(
            validated_patch,
            feedstock_id,
            fidelity,
            profile,
            active_schema,
        )
    except BackendUnavailableError as exc:
        raise BackendUnavailableAbort(
            str(exc),
            patch=validated_patch,
            candidate_id=candidate_id,
        ) from exc
    key = cache_key(spec)
    active_executor = executor or RunExecutor()

    try:
        run_execution = active_executor.execute(run_config)
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
        raise EngineBugAbort(
            f"{type(exc).__name__}: {exc}",
            patch=validated_patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key_value=key,
        ) from exc
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
        raise EngineBugAbort(
            error_message or "run executor failed",
            patch=validated_patch,
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key_value=key,
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

    feasibility = (constraints or PhysicsConstraintSet()).evaluate(run_execution.trace)
    if not feasibility.feasible:
        return _infeasible_result(candidate_id, spec, key, feasibility, run_execution, profile)

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

    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=key,
        feasible=True,
        objectives=objectives,
        feasibility_margins=feasibility.margins,
        failing_gates=(),
        run_reference=_run_reference(run_execution, profile),
    )


def _build_eval_inputs(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    profile: Mapping[str, Any],
    schema: RecipeSchema,
) -> tuple[EvalSpec, Any]:
    bundle = load_config_bundle(DEFAULT_DATA_DIR)
    if feedstock_id not in bundle.feedstocks:
        raise EvaluationInputError(f"unknown feedstock_id {feedstock_id!r}")
    feedstock = bundle.feedstocks[feedstock_id]
    profile_id = str(profile.get("profile_id") or profile.get("id") or "inline-profile")
    profile_digest = _profile_digest(profile)
    run_options = _run_options(profile, fidelity)
    setpoints_patch = schema.to_setpoints_patch(patch)
    for digest_key in ("setpoints", "feedstocks", "vapor_pressures"):
        if digest_key not in bundle.digests:
            raise EvaluationInputError(f"missing config digest {digest_key!r}")

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
        },
        campaign=str(run_options["campaign"]),
        hours=int(run_options["hours"]),
        mass_kg=float(run_options["mass_kg"]),
        additives_kg=run_options["additives_kg"],
        track=str(run_options["track"]),
        backend_name=str(run_options["backend_name"]),
        runtime_campaign_overrides=run_options["runtime_campaign_overrides"],
        chemistry_kernel=run_options["chemistry_kernel"],
    )

    run_config = PyrolysisRun(
        feedstock_id=feedstock_id,
        campaign=spec.campaign,
        hours=spec.hours,
        additives_kg=dict(spec.additives_kg),
        mass_kg=spec.mass_kg,
        backend_name=spec.backend_name,
        setpoints_patch=setpoints_patch,
        runtime_campaign_overrides=dict(spec.runtime_campaign_overrides),
        track=spec.track,
    )._session_config()
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
    return MappingProxyType({
        "campaign": merged.get("campaign", "C0"),
        "hours": int(merged.get("hours", 24)),
        "mass_kg": float(merged.get("mass_kg", 1000.0)),
        "additives_kg": dict(merged.get("additives_kg", {}) or {}),
        "track": merged.get("track", "pyrolysis"),
        "backend_name": merged.get("backend_name", "stub"),
        "runtime_campaign_overrides": dict(
            merged.get("runtime_campaign_overrides", {}) or {}
        ),
        "chemistry_kernel": dict(merged.get("chemistry_kernel", {}) or {}),
    })


def _profile_digest(profile: Mapping[str, Any]) -> str:
    normalized = normalize_canonical_value(profile)
    return hashlib.sha256(canonical_json_dumps(normalized).encode("utf-8")).hexdigest()


def _infeasible_result(
    candidate_id: str | None,
    spec: EvalSpec,
    key: str,
    feasibility: FeasibilityResult,
    run_execution: Any,
    profile: Mapping[str, Any],
) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=key,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
        feasibility_margins=feasibility.margins,
        failing_gates=feasibility.failing_gates,
        run_reference=_run_reference(run_execution, profile),
    )


def _run_reference(run_execution: Any, profile: Mapping[str, Any]) -> RunReference:
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
        trace=getattr(run_execution, "trace", None),
        product_summary=summary,
    )


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
