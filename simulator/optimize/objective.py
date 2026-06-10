"""Feasible-run objective vector projection for recipe optimization."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
import fnmatch
import hashlib
import math
from types import MappingProxyType
from collections.abc import Callable, Sequence
from typing import Any, Mapping, TypeVar

from simulator.accounting.formulas import resolve_species_formula
from simulator.config import DEFAULT_DATA_DIR, load_config_bundle
from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value
from simulator.optimize.physics import (
    PhysicsConstraintSet,
    ThresholdSpec,
    extraction_completeness_report,
)
from simulator.three_product_report import classify_products
from simulator.trace import wall_deposit_kg_by_zone_species


_MISSING = object()
VALID_OBJECTIVE_SENSES = {"minimize", "maximize"}
COMPOSITION_TARGET_TYPE = "composition_target"
COMPOSITION_TARGET_METRIC_PREFIX = "composition_target:"
SUPPORTED_COMPOSITION_POOLS = frozenset(
    {
        "captured_stage_3_silica",
        "captured_products",
        "residual_rump_at_stop",
        "terminal_rump_earned",
    }
)
COMPOSITION_VECTOR_SPECIES = frozenset({"Na", "K", "Fe", "Mg", "Si", "Al", "Ca", "O2"})
COMPOSITION_VECTOR_ROLES = frozenset({"extract", "retain", "free"})
COMPOSITION_WINDOW_MODES = frozenset({"hard_window", "soft_distance"})
_COMPOSITION_OBJECTIVE_KEYS = frozenset(
    {"type", "id", "metric", "sense", "units", "weight", "rationale", "target"}
)
_COMPOSITION_TARGET_KEYS = frozenset(
    {
        "pool",
        "require_coating_gate",
        "species_vector",
        "extraction",
        "composition_window",
        "maturity",
        "constraints",
        "score_weights",
    }
)
_EXTRACTION_KEYS = frozenset(
    {"basis", "captured_pool", "credit_policy", "completeness_min"}
)
_CREDIT_POLICY_KEYS = frozenset({"additives", "vented"})
_COMPOSITION_WINDOW_KEYS = frozenset(
    {"pool", "basis", "mode", "oxides", "exploratory"}
)
_OXIDE_ROW_KEYS = frozenset(
    {"min", "max", "weight", "needs_experiment", "tier", "provenance"}
)
_MATURITY_KEYS = frozenset({"mode", "campaign", "hours"})
_TARGET_CONSTRAINT_KEYS = frozenset(
    {"coating_min_campaigns_to_resinter", "furnace_T_max_C"}
)
_SCORE_WEIGHT_KEYS = frozenset({"extraction", "composition"})
_FE_TIER_BOUNDS = MappingProxyType(
    {
        "clear_container": MappingProxyType(
            {
                "min": 0.0,
                "max": 1.0,
                "weight": 1.0,
                "needs_experiment": True,
                "provenance": "design-composition-target-objective-2026-06-10 seed",
            }
        ),
        "green_amber_container": MappingProxyType(
            {
                "min": 1.0,
                "max": 10.0,
                "weight": 1.0,
                "needs_experiment": True,
                "provenance": "design-composition-target-objective-2026-06-10 seed",
            }
        ),
        "workable_glass": MappingProxyType(
            {
                "min": 0.0,
                "max": 10.0,
                "weight": 1.0,
                "needs_experiment": True,
                "provenance": "design-composition-target-objective-2026-06-10 seed",
            }
        ),
    }
)
_POOL_LEDGER_ACCOUNTS = MappingProxyType(
    {
        "residual_rump_at_stop": ("process.cleaned_melt",),
        "terminal_rump_earned": ("terminal.slag",),
    }
)
_VENTED_PRODUCT_ACCOUNTS = frozenset(
    {"terminal.oxygen_melt_offgas_vented_to_vacuum", "vent"}
)
# Source: AccountingQueries.product_ledger merges sim._unspent_additive_reagents_kg(),
# which emits unspent_<element>_reagent entries for additive bookkeeping.
CAPTURED_PRODUCT_BOOKKEEPING_SPECIES_PATTERNS = ("unspent_*_reagent",)
_EPS = 1.0e-12
_OBJECTIVE_SENSE_ALIASES = {
    "min": "minimize",
    "minimum": "minimize",
    "minimize": "minimize",
    "max": "maximize",
    "maximum": "maximize",
    "maximize": "maximize",
}


class ObjectiveProfileError(ValueError):
    """Raised when an optimizer profile cannot define an objective vector."""


class ObjectiveComputationError(RuntimeError):
    """Raised when declared objectives cannot be computed from run outputs."""


@dataclass(frozen=True)
class ObjectiveDefinition:
    metric: str
    sense: str
    units: str = ""
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not self.metric:
            raise ObjectiveProfileError("objective metric is required")
        object.__setattr__(self, "sense", normalize_objective_sense(self.sense))
        ordinal = int(self.ordinal)
        if ordinal < 0:
            raise ObjectiveProfileError("objective ordinal must be non-negative")
        object.__setattr__(self, "ordinal", ordinal)


@dataclass(frozen=True)
class ObjectiveImportanceEvidence:
    metric: str
    weight: float
    rationale: str
    ordinal: int = 0


@dataclass(frozen=True)
class ObjectiveValue:
    metric: str
    sense: str
    value: float
    units: str = ""
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not self.metric:
            raise ObjectiveProfileError("objective metric is required")
        object.__setattr__(self, "sense", normalize_objective_sense(self.sense))
        if not math.isfinite(float(self.value)):
            raise ObjectiveComputationError(
                f"objective {self.metric!r} produced non-finite value"
            )
        object.__setattr__(self, "value", float(self.value))
        ordinal = int(self.ordinal)
        if ordinal < 0:
            raise ObjectiveProfileError("objective ordinal must be non-negative")
        object.__setattr__(self, "ordinal", ordinal)


@dataclass(frozen=True)
class ObjectiveVector:
    values: tuple[ObjectiveValue, ...]
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        metrics = tuple(value.metric for value in self.values)
        if len(set(metrics)) != len(metrics):
            raise ObjectiveProfileError("objective metrics must be unique")
        object.__setattr__(self, "evidence", dict(self.evidence))

    def as_mapping(self) -> Mapping[str, float]:
        return MappingProxyType({value.metric: value.value for value in self.values})


ObjectiveLike = ObjectiveVector | Mapping[str, float]
T = TypeVar("T")


def objective_type(raw: Mapping[str, Any]) -> str:
    value = str(raw.get("type", "legacy_metric") or "legacy_metric")
    return "legacy_metric" if value == "legacy_metric" else value


def normalize_composition_target_objective(
    raw: Mapping[str, Any],
    *,
    where: str = "objective",
) -> dict[str, Any]:
    _reject_unknown_objective_keys(raw, _COMPOSITION_OBJECTIVE_KEYS, where)
    _require_objective_keys(raw, {"id", "metric", "sense", "units", "weight", "target"}, where)
    target_id = _required_text(raw.get("id"), f"{where}.id")
    metric = _required_text(raw.get("metric"), f"{where}.metric")
    if metric != f"{COMPOSITION_TARGET_METRIC_PREFIX}{target_id}":
        raise ObjectiveProfileError(
            f"{where}.metric must be {COMPOSITION_TARGET_METRIC_PREFIX}{target_id}"
        )
    if str(raw.get("units")) != "score_0_1":
        raise ObjectiveProfileError(f"{where}.units must be 'score_0_1'")
    if normalize_objective_sense(str(raw.get("sense", ""))) != "maximize":
        raise ObjectiveProfileError(f"{where}.sense must be maximize")
    target = _normalize_target_spec(
        raw.get("target"),
        target_id=target_id,
        where=f"{where}.target",
    )
    normalized = dict(raw)
    normalized["type"] = COMPOSITION_TARGET_TYPE
    normalized["id"] = target_id
    normalized["metric"] = metric
    normalized["sense"] = "maximize"
    normalized["target"] = target
    return normalized


def composition_target_specs(profile: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    specs: list[Mapping[str, Any]] = []
    raw_objectives = profile.get("objectives", ())
    if not isinstance(raw_objectives, (list, tuple)):
        return ()
    for ordinal, raw in enumerate(raw_objectives):
        if not isinstance(raw, Mapping):
            continue
        if objective_type(raw) == COMPOSITION_TARGET_TYPE:
            specs.append(
                normalize_composition_target_objective(
                    raw,
                    where=f"objectives[{ordinal}]",
                )
            )
    return tuple(specs)


def composition_target_eval_metadata(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    specs = composition_target_specs(profile)
    if not specs:
        return MappingProxyType(
            {
                "target_spec_id": "",
                "target_spec_digest": "",
                "target_maturity": MappingProxyType({}),
                "target_provenance": MappingProxyType({}),
            }
        )
    if len(specs) == 1:
        objective = specs[0]
        target = objective["target"]
        return MappingProxyType(
            {
                "target_spec_id": str(objective["id"]),
                "target_spec_digest": target_spec_digest(target),
                "target_maturity": MappingProxyType(dict(target.get("maturity", {}))),
                "target_provenance": MappingProxyType(_target_provenance(target)),
            }
        )
    payload = tuple(
        {
            "id": str(objective["id"]),
            "target": objective["target"],
        }
        for objective in specs
    )
    digest = target_spec_digest({"targets": payload})
    return MappingProxyType(
        {
            "target_spec_id": f"multi:{digest[:16]}",
            "target_spec_digest": digest,
            "target_maturity": MappingProxyType(
                {
                    "targets": tuple(
                        {
                            "id": str(objective["id"]),
                            "maturity": MappingProxyType(
                                dict(objective["target"].get("maturity", {}))
                            ),
                        }
                        for objective in specs
                    )
                }
            ),
            "target_provenance": MappingProxyType(
                {
                    "targets": tuple(
                        {
                            "id": str(objective["id"]),
                            "provenance": MappingProxyType(
                                _target_provenance(objective["target"])
                            ),
                        }
                        for objective in specs
                    )
                }
            ),
        }
    )


def _target_provenance(target: Mapping[str, Any]) -> dict[str, Any]:
    window = target.get("composition_window", {})
    if not isinstance(window, Mapping):
        return {}
    oxides = window.get("oxides", {})
    if not isinstance(oxides, Mapping):
        return {}
    resolved_oxides: dict[str, Mapping[str, Any]] = {}
    for oxide, raw in oxides.items():
        if not isinstance(raw, Mapping):
            continue
        row: dict[str, Any] = {
            "min": raw.get("min"),
            "max": raw.get("max"),
            "weight": raw.get("weight"),
        }
        for key in ("tier", "needs_experiment", "provenance"):
            if key in raw:
                row[key] = raw[key]
        if any(key in row for key in ("tier", "needs_experiment", "provenance")):
            resolved_oxides[str(oxide)] = MappingProxyType(row)
    if not resolved_oxides:
        return {}
    return {
        "composition_window": MappingProxyType(
            {
                "pool": str(window.get("pool", "")),
                "mode": str(window.get("mode", "")),
                "oxides": MappingProxyType(resolved_oxides),
            }
        )
    }


def target_spec_digest(target: Mapping[str, Any]) -> str:
    normalized = normalize_canonical_value(target)
    return hashlib.sha256(canonical_json_dumps(normalized).encode("utf-8")).hexdigest()


def composition_targets_require_coating(profile: Mapping[str, Any]) -> bool:
    return any(
        bool(objective["target"].get("require_coating_gate", True))
        for objective in composition_target_specs(profile)
    )


def composition_targets_require_terminal_rump(profile: Mapping[str, Any]) -> bool:
    for objective in composition_target_specs(profile):
        target = objective["target"]
        window = target.get("composition_window", {})
        pools = {
            str(target.get("pool", "")),
            str(window.get("pool", "")) if isinstance(window, Mapping) else "",
        }
        if "terminal_rump_earned" in pools:
            return True
    return False


def composition_target_infeasible_reason(profile: Mapping[str, Any]) -> str:
    for objective in composition_target_specs(profile):
        target = objective["target"]
        vector = target["species_vector"]
        if vector.get("Mg") != "extract":
            continue
        if not any(vector.get(species) == "retain" for species in ("Na", "K")):
            continue
        campaign = str(target.get("maturity", {}).get("campaign", ""))
        objective_id = str(objective.get("id", ""))
        if "C3" in campaign or "c3" in objective_id.lower():
            continue
        return "order: Mg extract with Na/K retain requires explicit C3 re-dose route"
    return ""


def _normalize_target_spec(raw: Any, *, target_id: str, where: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ObjectiveProfileError(f"{where} must be a mapping")
    _reject_unknown_objective_keys(raw, _COMPOSITION_TARGET_KEYS, where)
    _require_objective_keys(
        raw,
        {"pool", "species_vector", "composition_window", "score_weights"},
        where,
    )
    pool = _validate_pool(raw.get("pool"), f"{where}.pool")
    vector = _normalize_species_vector(raw.get("species_vector"), where=f"{where}.species_vector")
    extraction = _normalize_extraction(
        raw.get("extraction", {}),
        vector=vector,
        where=f"{where}.extraction",
    )
    window = _normalize_composition_window(
        raw.get("composition_window"),
        target_id=target_id,
        where=f"{where}.composition_window",
    )
    score_weights = _normalize_score_weights(
        raw.get("score_weights"),
        vector=vector,
        window=window,
        where=f"{where}.score_weights",
    )
    maturity = _normalize_maturity(raw.get("maturity", {}), where=f"{where}.maturity")
    constraints = _normalize_target_constraints(
        raw.get("constraints", {}),
        where=f"{where}.constraints",
    )
    require_coating_gate = True
    if "require_coating_gate" in raw:
        require_coating_gate = raw["require_coating_gate"]
        if not isinstance(require_coating_gate, bool):
            raise ObjectiveProfileError(f"{where}.require_coating_gate must be bool")
    return {
        "pool": pool,
        "require_coating_gate": require_coating_gate,
        "species_vector": MappingProxyType(vector),
        "extraction": MappingProxyType(extraction),
        "composition_window": MappingProxyType(window),
        "maturity": MappingProxyType(maturity),
        "constraints": MappingProxyType(constraints),
        "score_weights": MappingProxyType(score_weights),
    }


def _normalize_species_vector(raw: Any, *, where: str) -> dict[str, str]:
    if not isinstance(raw, Mapping) or not raw:
        raise ObjectiveProfileError(f"{where} must be a non-empty mapping")
    vector: dict[str, str] = {}
    for species, role in raw.items():
        key = str(species)
        if key not in COMPOSITION_VECTOR_SPECIES:
            raise ObjectiveProfileError(f"{where} unknown species {key!r}")
        role_name = str(role)
        if role_name not in COMPOSITION_VECTOR_ROLES:
            raise ObjectiveProfileError(f"{where}.{key} has invalid role {role_name!r}")
        vector[key] = role_name
    if all(role == "free" for role in vector.values()):
        raise ObjectiveProfileError(f"{where} must not be all-free")
    return vector


def _normalize_extraction(
    raw: Any,
    *,
    vector: Mapping[str, str],
    where: str,
) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ObjectiveProfileError(f"{where} must be a mapping")
    _reject_unknown_objective_keys(raw, _EXTRACTION_KEYS, where)
    basis = str(raw.get("basis", "input_element_mol"))
    if basis != "input_element_mol":
        raise ObjectiveProfileError(f"{where}.basis must be 'input_element_mol'")
    captured_pool = _validate_pool(raw.get("captured_pool", "captured_products"), f"{where}.captured_pool")
    if captured_pool not in {"captured_products", "captured_stage_3_silica"}:
        raise ObjectiveProfileError(f"{where}.captured_pool cannot be {captured_pool!r}")
    credit_policy = raw.get("credit_policy", {})
    if credit_policy is None:
        credit_policy = {}
    if not isinstance(credit_policy, Mapping):
        raise ObjectiveProfileError(f"{where}.credit_policy must be a mapping")
    _reject_unknown_objective_keys(
        credit_policy,
        _CREDIT_POLICY_KEYS,
        f"{where}.credit_policy",
    )
    for key, expected in (
        ("additives", "no_product_credit"),
        ("vented", "no_product_credit"),
    ):
        value = str(credit_policy.get(key, expected))
        if value != expected:
            raise ObjectiveProfileError(
                f"{where}.credit_policy.{key} must be {expected!r}"
            )
    completeness_min = raw.get("completeness_min", {})
    if completeness_min is None:
        completeness_min = {}
    if not isinstance(completeness_min, Mapping):
        raise ObjectiveProfileError(f"{where}.completeness_min must be a mapping")
    normalized_min: dict[str, float] = {}
    extract_species = [species for species, role in vector.items() if role == "extract"]
    for species in extract_species:
        if species not in completeness_min:
            raise ObjectiveProfileError(
                f"{where}.completeness_min missing extract species {species!r}"
            )
    for species, value in completeness_min.items():
        key = str(species)
        if key not in vector:
            raise ObjectiveProfileError(f"{where}.completeness_min unknown species {key!r}")
        numeric = _positive_profile_float(value, f"{where}.completeness_min.{key}")
        normalized_min[key] = numeric
    return {
        "basis": basis,
        "captured_pool": captured_pool,
        "credit_policy": MappingProxyType(
            {
                "additives": "no_product_credit",
                "vented": "no_product_credit",
            }
        ),
        "completeness_min": MappingProxyType(normalized_min),
    }


def _normalize_composition_window(
    raw: Any,
    *,
    target_id: str,
    where: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ObjectiveProfileError(f"{where} must be a mapping")
    _reject_unknown_objective_keys(raw, _COMPOSITION_WINDOW_KEYS, where)
    _require_objective_keys(raw, {"pool", "basis", "mode", "oxides"}, where)
    pool = _validate_pool(raw.get("pool"), f"{where}.pool")
    if str(raw.get("basis")) != "oxide_wt_pct":
        raise ObjectiveProfileError(f"{where}.basis must be 'oxide_wt_pct'")
    mode = str(raw.get("mode"))
    if mode not in COMPOSITION_WINDOW_MODES:
        raise ObjectiveProfileError(f"{where}.mode must be hard_window or soft_distance")
    exploratory = bool(raw.get("exploratory", False))
    if mode == "soft_distance" and (
        not exploratory or str(target_id).startswith("pc-")
    ):
        raise ObjectiveProfileError(
            f"{where}.mode soft_distance requires exploratory non-menu target"
        )
    oxides = raw.get("oxides")
    if not isinstance(oxides, Mapping) or not oxides:
        raise ObjectiveProfileError(f"{where}.oxides must be a non-empty mapping")
    normalized_oxides: dict[str, Mapping[str, Any]] = {}
    for oxide, row in oxides.items():
        oxide_name = str(oxide)
        normalized_oxides[oxide_name] = MappingProxyType(
            _normalize_oxide_row(row, where=f"{where}.oxides.{oxide_name}")
        )
    return {
        "pool": pool,
        "basis": "oxide_wt_pct",
        "mode": mode,
        "oxides": MappingProxyType(normalized_oxides),
        **({"exploratory": True} if exploratory else {}),
    }


def _normalize_oxide_row(raw: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ObjectiveProfileError(f"{where} must be a mapping")
    _reject_unknown_objective_keys(raw, _OXIDE_ROW_KEYS, where)
    row = dict(raw)
    if "tier" in row:
        tier = str(row["tier"])
        try:
            bounds = dict(_FE_TIER_BOUNDS[tier])
        except KeyError as exc:
            raise ObjectiveProfileError(f"{where}.tier unknown tier {tier!r}") from exc
        row = {**bounds, **row}
        row["tier"] = tier
        row["needs_experiment"] = True
        row.setdefault("provenance", bounds["provenance"])
    _require_objective_keys(row, {"min", "max", "weight"}, where)
    lower = _finite_profile_float(row["min"], f"{where}.min")
    upper = _finite_profile_float(row["max"], f"{where}.max")
    if upper < lower:
        raise ObjectiveProfileError(f"{where} has empty window")
    weight = _positive_profile_float(row["weight"], f"{where}.weight")
    normalized = {
        "min": lower,
        "max": upper,
        "weight": weight,
    }
    if "needs_experiment" in row:
        normalized["needs_experiment"] = bool(row["needs_experiment"])
    if "tier" in row:
        normalized["tier"] = str(row["tier"])
    if "provenance" in row:
        normalized["provenance"] = str(row["provenance"])
    return normalized


def _normalize_score_weights(
    raw: Any,
    *,
    vector: Mapping[str, str],
    window: Mapping[str, Any],
    where: str,
) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise ObjectiveProfileError(f"{where} must be a mapping")
    _reject_unknown_objective_keys(raw, _SCORE_WEIGHT_KEYS, where)
    weights = {
        "extraction": _non_negative_profile_float(raw.get("extraction", 0.0), f"{where}.extraction"),
        "composition": _non_negative_profile_float(raw.get("composition", 0.0), f"{where}.composition"),
    }
    if weights["extraction"] <= 0.0 and weights["composition"] <= 0.0:
        raise ObjectiveProfileError(f"{where} must contain at least one positive branch")
    weight_sum = weights["extraction"] + weights["composition"]
    if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ObjectiveProfileError(
            f"{where} score_weight_sum_not_one: extraction + composition must equal "
            f"1.0 for score_0_1 objective (got {weight_sum:.17g})"
        )
    if weights["extraction"] > 0.0 and not any(role == "extract" for role in vector.values()):
        raise ObjectiveProfileError(f"{where}.extraction positive but vector has no extract species")
    oxides = window.get("oxides", {})
    if weights["composition"] > 0.0 and not any(
        float(row.get("weight", 0.0)) > 0.0
        for row in oxides.values()
        if isinstance(row, Mapping)
    ):
        raise ObjectiveProfileError(f"{where}.composition positive but window is empty")
    return weights


def _normalize_maturity(raw: Any, *, where: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ObjectiveProfileError(f"{where} must be a mapping")
    _reject_unknown_objective_keys(raw, _MATURITY_KEYS, where)
    if not raw:
        return {}
    mode = str(raw.get("mode", "campaign_hours"))
    if mode != "campaign_hours":
        raise ObjectiveProfileError(f"{where}.mode must be 'campaign_hours'")
    result: dict[str, Any] = {"mode": mode}
    if "campaign" in raw:
        result["campaign"] = _required_text(raw["campaign"], f"{where}.campaign")
    if "hours" in raw:
        result["hours"] = _positive_profile_float(raw["hours"], f"{where}.hours")
    return result


def _normalize_target_constraints(raw: Any, *, where: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ObjectiveProfileError(f"{where} must be a mapping")
    _reject_unknown_objective_keys(raw, _TARGET_CONSTRAINT_KEYS, where)
    return {str(key): value for key, value in raw.items()}


def _validate_pool(raw: Any, where: str) -> str:
    pool = _required_text(raw, where)
    if pool not in SUPPORTED_COMPOSITION_POOLS:
        raise ObjectiveProfileError(f"{where} unknown pool id {pool!r}")
    return pool


def _required_text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ObjectiveProfileError(f"{where} must be a non-empty string")
    return value.strip()


def _finite_profile_float(value: Any, where: str) -> float:
    if isinstance(value, bool):
        raise ObjectiveProfileError(f"{where} must be numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ObjectiveProfileError(f"{where} must be numeric") from exc
    if not math.isfinite(numeric):
        raise ObjectiveProfileError(f"{where} must be finite")
    return numeric


def _positive_profile_float(value: Any, where: str) -> float:
    numeric = _finite_profile_float(value, where)
    if numeric <= 0.0:
        raise ObjectiveProfileError(f"{where} must be positive")
    return numeric


def _non_negative_profile_float(value: Any, where: str) -> float:
    numeric = _finite_profile_float(value, where)
    if numeric < 0.0:
        raise ObjectiveProfileError(f"{where} must be non-negative")
    return numeric


def _reject_unknown_objective_keys(
    mapping: Mapping[str, Any],
    allowed: frozenset[str],
    where: str,
) -> None:
    for key in mapping:
        if key not in allowed:
            raise ObjectiveProfileError(f"unknown {where} key {key!r}")


def _require_objective_keys(
    mapping: Mapping[str, Any],
    required: set[str],
    where: str,
) -> None:
    missing = sorted(required - set(mapping))
    if missing:
        raise ObjectiveProfileError(
            f"{where} missing required keys: {', '.join(missing)}"
        )


def objective_definitions(profile: Mapping[str, Any]) -> tuple[ObjectiveDefinition, ...]:
    raw_objectives = profile.get("objectives")
    if not isinstance(raw_objectives, (list, tuple)) or not raw_objectives:
        raise ObjectiveProfileError("profile.objectives must be a non-empty list")

    definitions: list[ObjectiveDefinition] = []
    for ordinal, raw in enumerate(raw_objectives):
        if not isinstance(raw, Mapping):
            raise ObjectiveProfileError("each objective must be a mapping")
        definitions.append(
            ObjectiveDefinition(
                metric=str(raw.get("metric", "")),
                sense=str(raw.get("sense", "")),
                units=str(raw.get("units", "")),
                ordinal=ordinal,
            )
        )
    return tuple(definitions)


def objective_importance_evidence(
    profile: Mapping[str, Any],
) -> tuple[ObjectiveImportanceEvidence, ...]:
    raw_objectives = profile.get("objectives")
    if not isinstance(raw_objectives, (list, tuple)) or not raw_objectives:
        raise ObjectiveProfileError("profile.objectives must be a non-empty list")

    rows: list[ObjectiveImportanceEvidence] = []
    for ordinal, raw in enumerate(raw_objectives):
        where = f"objectives[{ordinal}]"
        if not isinstance(raw, Mapping):
            raise ObjectiveProfileError("each objective must be a mapping")
        metric = str(raw.get("metric", "") or f"#{ordinal}")
        if "weight" not in raw:
            raise ObjectiveProfileError(
                f"insufficient-evidence: {where} {metric!r} missing weight"
            )
        try:
            weight = float(raw["weight"])
        except (TypeError, ValueError) as exc:
            raise ObjectiveProfileError(
                f"insufficient-evidence: {where} {metric!r} weight is not numeric"
            ) from exc
        if not math.isfinite(weight) or weight <= 0.0:
            raise ObjectiveProfileError(
                f"insufficient-evidence: {where} {metric!r} weight must be positive finite"
            )
        rationale = raw.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ObjectiveProfileError(
                f"insufficient-evidence: {where} {metric!r} missing rationale"
            )
        rows.append(
            ObjectiveImportanceEvidence(
                metric=metric,
                weight=weight,
                rationale=rationale,
                ordinal=ordinal,
            )
        )
    return tuple(rows)


def compute_objectives(profile: Mapping[str, Any], run_execution: Any) -> ObjectiveVector:
    """Compute the declared objective vector from real simulator outputs."""

    definitions = objective_definitions(profile)
    raw_objectives = profile.get("objectives")
    if not isinstance(raw_objectives, (list, tuple)):
        raise ObjectiveProfileError("profile.objectives must be a non-empty list")
    sim = getattr(run_execution, "simulator", run_execution)
    product_classes = classify_products(
        sim,
        early_tap_mode=bool(profile.get("early_tap_mode", False)),
    )
    product_ledger = _product_ledger(sim)

    values: list[ObjectiveValue] = []
    evidence: dict[str, Mapping[str, Any]] = {}
    for definition, raw in zip(definitions, raw_objectives, strict=True):
        if not isinstance(raw, Mapping):
            raise ObjectiveProfileError("each objective must be a mapping")
        if objective_type(raw) == COMPOSITION_TARGET_TYPE:
            normalized = normalize_composition_target_objective(
                raw,
                where=f"objectives[{definition.ordinal}]",
            )
            objective_evidence: dict[str, Any] = {}
            value = _composition_target_score(
                normalized,
                run_execution,
                profile,
                evidence=objective_evidence,
            )
            if objective_evidence:
                evidence[definition.metric] = dict(objective_evidence)
        else:
            value = _metric_value(
                definition.metric,
                sim,
                product_ledger,
                product_classes,
            )
        values.append(
            ObjectiveValue(
                metric=definition.metric,
                sense=definition.sense,
                value=value,
                units=definition.units,
                ordinal=definition.ordinal,
            )
        )
    return ObjectiveVector(tuple(values), evidence=evidence)


def _composition_target_score(
    objective: Mapping[str, Any],
    run_execution: Any,
    profile: Mapping[str, Any],
    *,
    evidence: dict[str, Any] | None = None,
) -> float:
    target = objective["target"]
    vector = target["species_vector"]
    extraction = target["extraction"]
    window = target["composition_window"]
    weights = target["score_weights"]
    extraction_weight = float(weights["extraction"])
    composition_weight = float(weights["composition"])

    extraction_score = 0.0
    if extraction_weight > 0.0:
        bookkeeping_exclusions: set[str] = set()
        extraction_score = _extraction_score(
            vector,
            extraction,
            run_execution,
            profile,
            bookkeeping_exclusions=bookkeeping_exclusions,
        )
        if bookkeeping_exclusions and evidence is not None:
            excluded = tuple(sorted(bookkeeping_exclusions))
            evidence["captured_product_bookkeeping_exclusions"] = excluded
            evidence["notes"] = (
                "excluded captured-products bookkeeping species from extraction credit: "
                + ", ".join(excluded),
            )

    composition_score = 0.0
    if composition_weight > 0.0:
        composition_score = _composition_window_score(
            window,
            run_execution,
        )
        if str(window["mode"]) == "hard_window" and composition_score <= 0.0:
            return 0.0

    return extraction_weight * extraction_score + composition_weight * composition_score


def _extraction_score(
    vector: Mapping[str, str],
    extraction: Mapping[str, Any],
    run_execution: Any,
    profile: Mapping[str, Any],
    *,
    bookkeeping_exclusions: set[str] | None = None,
) -> float:
    scores: list[float] = []
    completeness_min = extraction["completeness_min"]
    captured_pool = str(extraction["captured_pool"])
    for species, role in vector.items():
        if role != "extract":
            continue
        input_mol = _feedstock_input_target_mol(species, run_execution, profile)
        if input_mol <= _EPS:
            raise ObjectiveComputationError(
                f"composition target extract species {species!r} has no input mol"
            )
        captured_mol = _captured_target_mol(
            species,
            captured_pool,
            run_execution,
            bookkeeping_exclusions=bookkeeping_exclusions,
        )
        additive_mol = _additive_target_mol(species, run_execution, profile)
        captured_mol = max(0.0, captured_mol - additive_mol)
        completeness = captured_mol / input_mol
        threshold = _finite_float(
            completeness_min[species],
            f"composition_target.completeness_min[{species!r}]",
        )
        if threshold <= 0.0:
            raise ObjectiveComputationError(
                f"composition_target.completeness_min[{species!r}] must be positive"
            )
        scores.append(_clamp(completeness / threshold, 0.0, 1.0))
    if not scores:
        raise ObjectiveComputationError("composition target extraction branch has no species")
    return sum(scores) / len(scores)


def _composition_window_score(
    window: Mapping[str, Any],
    run_execution: Any,
) -> float:
    pool = str(window["pool"])
    species_mol = _pool_species_mol(pool, run_execution)
    if not species_mol:
        raise ObjectiveComputationError(f"composition target pool {pool!r} is missing or empty")
    mode = str(window["mode"])
    oxides = window["oxides"]
    if mode == "hard_window":
        for oxide, row in oxides.items():
            observed = _oxide_wt_pct(str(oxide), species_mol, run_execution)
            lower = _finite_float(row["min"], f"{oxide}.min")
            upper = _finite_float(row["max"], f"{oxide}.max")
            if observed < lower or observed > upper:
                return 0.0
        return 1.0
    if mode == "soft_distance":
        weighted = 0.0
        total_weight = 0.0
        for oxide, row in oxides.items():
            observed = _oxide_wt_pct(str(oxide), species_mol, run_execution)
            lower = _finite_float(row["min"], f"{oxide}.min")
            upper = _finite_float(row["max"], f"{oxide}.max")
            weight = _finite_float(row["weight"], f"{oxide}.weight")
            total_weight += weight
            if lower <= observed <= upper:
                weighted += weight
            elif observed < lower and lower > 0.0:
                weighted += weight * _clamp(observed / lower, 0.0, 1.0)
            elif observed > upper and observed > 0.0:
                weighted += weight * _clamp(upper / observed, 0.0, 1.0)
        if total_weight <= 0.0:
            raise ObjectiveComputationError("composition target window has zero total weight")
        return weighted / total_weight
    raise ObjectiveComputationError(f"unsupported composition window mode {mode!r}")


def _pool_species_mol(
    pool: str,
    run_execution: Any,
    *,
    bookkeeping_exclusions: set[str] | None = None,
) -> Mapping[str, float]:
    sim = getattr(run_execution, "simulator", run_execution)
    if pool == "captured_stage_3_silica":
        return MappingProxyType(_captured_stage_3_silica_mol(run_execution))
    if pool == "captured_products":
        return MappingProxyType(
            _captured_products_mol(
                run_execution,
                bookkeeping_exclusions=bookkeeping_exclusions,
            )
        )
    if pool == "residual_rump_at_stop":
        ledger_values = _ledger_mol_by_accounts(
            sim,
            _POOL_LEDGER_ACCOUNTS[pool],
        )
        if ledger_values:
            return MappingProxyType(ledger_values)
        raise ObjectiveComputationError(f"composition target pool {pool!r} unavailable")
    if pool == "terminal_rump_earned":
        _require_earned_terminal_rump(run_execution)
        ledger_values = _ledger_mol_by_accounts(
            sim,
            _POOL_LEDGER_ACCOUNTS[pool],
        )
        if ledger_values:
            return MappingProxyType(ledger_values)
        fallback = _terminal_rump_trace_mol(run_execution)
        if fallback:
            return MappingProxyType(fallback)
    raise ObjectiveComputationError(f"composition target pool {pool!r} unavailable")


def _captured_target_mol(
    target_species: str,
    pool: str,
    run_execution: Any,
    *,
    bookkeeping_exclusions: set[str] | None = None,
) -> float:
    species_mol = _pool_species_mol(
        pool,
        run_execution,
        bookkeeping_exclusions=bookkeeping_exclusions,
    )
    return sum(
        _target_equivalent_mol(
            target_species,
            species,
            mol,
            run_execution,
            allow_bookkeeping_skip=(pool == "captured_products"),
            bookkeeping_exclusions=bookkeeping_exclusions,
        )
        for species, mol in species_mol.items()
    )


def _feedstock_input_target_mol(
    target_species: str,
    run_execution: Any,
    profile: Mapping[str, Any],
) -> float:
    sim = getattr(run_execution, "simulator", run_execution)
    record = getattr(sim, "record", None)
    feedstock_id = str(
        profile.get("feedstock")
        or getattr(record, "feedstock_key", "")
        or getattr(record, "feedstock_id", "")
    )
    if not feedstock_id:
        raise ObjectiveComputationError("composition target feedstock id unavailable")
    bundle = load_config_bundle(DEFAULT_DATA_DIR)
    try:
        feedstock = bundle.feedstocks[feedstock_id]
    except KeyError as exc:
        raise ObjectiveComputationError(
            f"composition target unknown feedstock {feedstock_id!r}"
        ) from exc
    composition = feedstock.get("composition_wt_pct", feedstock)
    if not isinstance(composition, Mapping):
        raise ObjectiveComputationError("composition target feedstock composition unavailable")
    mass_kg = _run_mass_kg(run_execution, profile)
    total = 0.0
    for species, wt_pct in composition.items():
        kg = mass_kg * _finite_float(wt_pct, f"feedstock[{species!r}]") / 100.0
        mol = _kg_to_mol(str(species), kg, run_execution)
        total += _target_equivalent_mol(target_species, str(species), mol, run_execution)
    return total


def _additive_target_mol(
    target_species: str,
    run_execution: Any,
    profile: Mapping[str, Any],
) -> float:
    sim = getattr(run_execution, "simulator", run_execution)
    record = getattr(sim, "record", None)
    raw = getattr(record, "additives_kg", None)
    if raw is None:
        raw = profile.get("run", {}).get("additives_kg", {}) if isinstance(profile.get("run"), Mapping) else {}
    if not isinstance(raw, Mapping):
        return 0.0
    total = 0.0
    for species, kg in raw.items():
        mol = _kg_to_mol(str(species), _finite_float(kg, f"additive[{species!r}]"), run_execution)
        total += _target_equivalent_mol(target_species, str(species), mol, run_execution)
    return total


def _run_mass_kg(run_execution: Any, profile: Mapping[str, Any]) -> float:
    sim = getattr(run_execution, "simulator", run_execution)
    record = getattr(sim, "record", None)
    raw = getattr(record, "batch_mass_kg", None)
    if raw is None and isinstance(profile.get("run"), Mapping):
        raw = profile["run"].get("mass_kg", 1000.0)
    return _finite_float(raw, "composition target run mass_kg")


def _captured_stage_3_silica_mol(run_execution: Any) -> dict[str, float]:
    sim = getattr(run_execution, "simulator", run_execution)
    result: dict[str, float] = {}
    stages = tuple(getattr(getattr(sim, "train", None), "stages", ()) or ())
    if len(stages) > 3:
        collected = getattr(stages[3], "collected_kg", {})
        if isinstance(collected, Mapping):
            for species in ("SiO", "SiO2"):
                kg = collected.get(species, 0.0)
                mol = _kg_to_mol(species, _finite_float(kg, f"stage3[{species}]"), run_execution)
                if mol > _EPS:
                    result[species] = result.get(species, 0.0) + mol
    if result:
        return result
    trace = getattr(run_execution, "trace", None)
    deltas = getattr(trace, "condensed_by_stage_species_delta", ())
    for tick in (deltas if isinstance(deltas, (list, tuple)) else ()):
        if not isinstance(tick, Mapping):
            continue
        for key, kg in tick.items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            stage, species = key
            species_name = str(species)
            if int(stage) != 3 or species_name not in {"SiO", "SiO2"}:
                continue
            mol = _kg_to_mol(species_name, _finite_float(kg, f"stage3[{species_name}]"), run_execution)
            if mol > _EPS:
                result[species_name] = result.get(species_name, 0.0) + mol
    return result


def _captured_products_mol(
    run_execution: Any,
    *,
    bookkeeping_exclusions: set[str] | None = None,
) -> dict[str, float]:
    sim = getattr(run_execution, "simulator", run_execution)
    result: dict[str, float] = {}
    ledger = getattr(sim, "atom_ledger", None)
    mol_by_account = getattr(ledger, "mol_by_account", None)
    if callable(mol_by_account):
        raw = mol_by_account()
        if isinstance(raw, Mapping):
            for account, species_mol in raw.items():
                account_name = str(account)
                if account_name in _VENTED_PRODUCT_ACCOUNTS:
                    continue
                if not (
                    account_name.startswith("terminal.")
                    or account_name in {"process.metal_phase", "process.condensation_train"}
                ):
                    continue
                if not isinstance(species_mol, Mapping):
                    continue
                for species, mol in species_mol.items():
                    amount = _finite_float(mol, f"{account_name}[{species!r}]")
                    if amount > _EPS:
                        result[str(species)] = result.get(str(species), 0.0) + amount
    if result:
        return result
    for species, kg in _product_ledger(sim).items():
        species_name = str(species)
        try:
            mol = _kg_to_mol(species_name, kg, run_execution)
        except ObjectiveComputationError:
            if _is_captured_product_bookkeeping_species(species_name):
                if bookkeeping_exclusions is not None:
                    bookkeeping_exclusions.add(species_name)
                continue
            raise
        if mol > _EPS:
            result[species_name] = result.get(species_name, 0.0) + mol
    return result


def _terminal_rump_trace_mol(run_execution: Any) -> dict[str, float]:
    _require_earned_terminal_rump(run_execution)
    trace = getattr(run_execution, "trace", None)
    raw = getattr(trace, "terminal_rump_by_species_kg", None)
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, float] = {}
    for species, kg in raw.items():
        mol = _kg_to_mol(str(species), _finite_float(kg, f"terminal_rump[{species!r}]"), run_execution)
        if mol > _EPS:
            result[str(species)] = result.get(str(species), 0.0) + mol
    return result


def _require_earned_terminal_rump(run_execution: Any) -> None:
    trace = getattr(run_execution, "trace", None)
    payload = _carrier_value(trace, "rump_terminal")
    if not isinstance(payload, Mapping) or str(payload.get("status", "")) != "earned":
        raise ObjectiveComputationError("composition target terminal rump is not earned")


def _carrier_value(carrier: Any, name: str) -> Any:
    if isinstance(carrier, Mapping):
        return carrier.get(name)
    return getattr(carrier, name, None)


def _ledger_mol_by_accounts(sim: Any, accounts: Sequence[str]) -> dict[str, float]:
    ledger = getattr(sim, "atom_ledger", None)
    mol_by_account = getattr(ledger, "mol_by_account", None)
    if not callable(mol_by_account):
        return {}
    result: dict[str, float] = {}
    for account in accounts:
        raw = mol_by_account(account)
        if not isinstance(raw, Mapping):
            continue
        for species, mol in raw.items():
            amount = _finite_float(mol, f"{account}[{species!r}]")
            if amount > _EPS:
                result[str(species)] = result.get(str(species), 0.0) + amount
    return result


def _oxide_wt_pct(
    oxide_name: str,
    species_mol: Mapping[str, float],
    run_execution: Any,
) -> float:
    total_kg = sum(
        _mol_to_kg(species, mol, run_execution)
        for species, mol in species_mol.items()
    )
    if total_kg <= _EPS:
        raise ObjectiveComputationError("composition target pool has zero mass")
    oxide_kg = _oxide_equivalent_kg(oxide_name, species_mol, run_execution)
    return oxide_kg / total_kg * 100.0


def _oxide_equivalent_kg(
    oxide_name: str,
    species_mol: Mapping[str, float],
    run_execution: Any,
) -> float:
    if oxide_name == "Fe_total_as_Fe2O3_wt_pct":
        return _single_oxide_equivalent_kg("Fe2O3", species_mol, run_execution)
    if oxide_name == "Al2O3_CaO_MgO_balance":
        return sum(
            _single_oxide_equivalent_kg(oxide, species_mol, run_execution)
            for oxide in ("Al2O3", "CaO", "MgO")
        )
    if oxide_name == "TiO2_plus_Cr2O3_plus_REO":
        total = sum(
            _single_oxide_equivalent_kg(oxide, species_mol, run_execution)
            for oxide in ("TiO2", "Cr2O3")
        )
        total += sum(
            _mol_to_kg(species, mol, run_execution)
            for species, mol in species_mol.items()
            if "REO" in species or "REE" in species
        )
        return total
    if "_plus_" in oxide_name:
        return sum(
            _single_oxide_equivalent_kg(part, species_mol, run_execution)
            for part in oxide_name.split("_plus_")
        )
    return _single_oxide_equivalent_kg(oxide_name, species_mol, run_execution)


def _single_oxide_equivalent_kg(
    oxide_name: str,
    species_mol: Mapping[str, float],
    run_execution: Any,
) -> float:
    oxide_formula = _species_formula(oxide_name, run_execution)
    non_oxygen = [element for element in oxide_formula.elements if element != "O"]
    if len(non_oxygen) != 1:
        raise ObjectiveComputationError(
            f"oxide row {oxide_name!r} does not identify one cation"
        )
    element = non_oxygen[0]
    oxide_element_count = oxide_formula.elements[element]
    oxide_mol = 0.0
    for species, mol in species_mol.items():
        formula = _species_formula(species, run_execution)
        element_count = formula.elements.get(element, 0.0)
        if element_count <= 0.0:
            continue
        oxide_mol += _finite_float(mol, f"{species} mol") * element_count / oxide_element_count
    return oxide_mol * oxide_formula.molar_mass_kg_per_mol()


def _target_equivalent_mol(
    target_species: str,
    species: str,
    species_mol: float,
    run_execution: Any,
    *,
    allow_bookkeeping_skip: bool = False,
    bookkeeping_exclusions: set[str] | None = None,
) -> float:
    amount = _finite_float(species_mol, f"{species} mol")
    if amount <= _EPS:
        return 0.0
    target_formula = _species_formula(target_species, run_execution)
    target_elements = list(target_formula.elements)
    if len(target_elements) != 1:
        non_oxygen = [element for element in target_elements if element != "O"]
        if len(non_oxygen) != 1:
            raise ObjectiveComputationError(
                f"target species {target_species!r} does not identify one target element"
            )
        element = non_oxygen[0]
    else:
        element = target_elements[0]
    try:
        species_formula = _species_formula(species, run_execution)
    except ObjectiveComputationError:
        if allow_bookkeeping_skip and _is_captured_product_bookkeeping_species(species):
            if bookkeeping_exclusions is not None:
                bookkeeping_exclusions.add(str(species))
            return 0.0
        raise
    count = species_formula.elements.get(element, 0.0)
    if count <= 0.0:
        return 0.0
    return amount * count / target_formula.elements[element]


def _is_captured_product_bookkeeping_species(species: str) -> bool:
    return any(
        fnmatch.fnmatchcase(str(species), pattern)
        for pattern in CAPTURED_PRODUCT_BOOKKEEPING_SPECIES_PATTERNS
    )


def _kg_to_mol(species: str, kg: float, run_execution: Any) -> float:
    amount = _finite_float(kg, f"{species} kg")
    if amount < -_EPS:
        raise ObjectiveComputationError(f"{species} kg must be non-negative")
    if amount <= _EPS:
        return 0.0
    return amount / _species_formula(species, run_execution).molar_mass_kg_per_mol()


def _mol_to_kg(species: str, mol: float, run_execution: Any) -> float:
    amount = _finite_float(mol, f"{species} mol")
    if amount < -_EPS:
        raise ObjectiveComputationError(f"{species} mol must be non-negative")
    if amount <= _EPS:
        return 0.0
    return amount * _species_formula(species, run_execution).molar_mass_kg_per_mol()


def _species_formula(species: str, run_execution: Any):
    sim = getattr(run_execution, "simulator", run_execution)
    registry = getattr(getattr(sim, "atom_ledger", None), "registry", None)
    try:
        return resolve_species_formula(str(species), registry)
    except Exception as exc:  # noqa: BLE001 - surface as objective failure
        raise ObjectiveComputationError(f"unknown composition species {species!r}") from exc


def _clamp(value: float, lower: float, upper: float) -> float:
    numeric = _finite_float(value, "composition target score")
    return max(lower, min(upper, numeric))


def normalize_objective_sense(sense: str) -> str:
    normalized = _OBJECTIVE_SENSE_ALIASES.get(str(sense).strip().lower())
    if normalized is None:
        raise ObjectiveProfileError(
            "objective sense must be 'minimize' or 'maximize'"
        )
    return normalized


def objective_scores(
    objectives: ObjectiveLike,
    definitions: Sequence[ObjectiveDefinition],
) -> tuple[float, ...]:
    """Render objective values as maximize-native scores in profile order."""

    mapping = _objective_mapping(objectives)
    scores: list[float] = []
    for definition in definitions:
        try:
            value = mapping[definition.metric]
        except KeyError as exc:
            raise ObjectiveComputationError(
                f"objective {definition.metric!r} is missing"
            ) from exc
        numeric = _finite_float(value, definition.metric)
        scores.append(numeric if definition.sense == "maximize" else -numeric)
    return tuple(scores)


def dominates(
    left: ObjectiveLike,
    right: ObjectiveLike,
    definitions: Sequence[ObjectiveDefinition],
) -> bool:
    """Return true when left Pareto-dominates right for the profile directions."""

    left_scores = objective_scores(left, definitions)
    right_scores = objective_scores(right, definitions)
    return all(a >= b for a, b in zip(left_scores, right_scores)) and any(
        a > b for a, b in zip(left_scores, right_scores)
    )


def pareto_front(
    items: Sequence[T],
    definitions: Sequence[ObjectiveDefinition],
    *,
    objective_getter: Callable[[T], ObjectiveLike],
) -> tuple[T, ...]:
    """Stable non-dominated subset using profile objective order and directions."""

    front: list[T] = []
    for index, item in enumerate(items):
        objectives = objective_getter(item)
        if any(
            other_index != index
            and dominates(objective_getter(other), objectives, definitions)
            for other_index, other in enumerate(items)
        ):
            continue
        front.append(item)
    return tuple(front)


def product_summary(run_execution: Any, profile: Mapping[str, Any]) -> Mapping[str, Any]:
    sim = getattr(run_execution, "simulator", run_execution)
    product_classes = product_classes_summary(sim, profile)
    product_bins = _product_bins(product_classes)
    summary: dict[str, Any] = {
        "product_ledger_kg": MappingProxyType(dict(_product_ledger(sim))),
        "product_classes": product_classes,
        "product_bins": product_bins,
        "product_yield_table": _product_yield_table(
            sim,
            profile,
            product_bins,
            product_classes,
        ),
        "extraction_completeness": extraction_completeness_report(
            getattr(run_execution, "trace", None),
            _extraction_constraints_from_profile(profile),
        ),
    }
    summary.update(_coating_product_summary(run_execution))
    return MappingProxyType(summary)


def _extraction_constraints_from_profile(profile: Mapping[str, Any]) -> PhysicsConstraintSet:
    raw_constraints = profile.get("constraints", {})
    if raw_constraints is None:
        raw_constraints = {}
    if not isinstance(raw_constraints, Mapping):
        raise ObjectiveComputationError("profile.constraints must be a mapping")

    base = PhysicsConstraintSet()
    updates: dict[str, Any] = {}
    if "target_species" in raw_constraints:
        raw_targets = raw_constraints["target_species"]
        if not isinstance(raw_targets, (list, tuple)) or not raw_targets:
            raise ObjectiveComputationError(
                "profile.constraints.target_species must be a non-empty sequence"
            )
        updates["target_species"] = tuple(str(target) for target in raw_targets)
    if "extraction_min_fraction" in raw_constraints:
        threshold = base.extraction_min_fraction
        updates["extraction_min_fraction"] = ThresholdSpec(
            id=threshold.id,
            value=_finite_float(
                raw_constraints["extraction_min_fraction"],
                "profile.constraints.extraction_min_fraction",
            ),
            units=threshold.units,
            source="profile",
            source_ref="profile.constraints.extraction_min_fraction",
            tolerance=threshold.tolerance,
        )
    return replace(base, **updates) if updates else base


def _coating_product_summary(run_execution: Any) -> Mapping[str, Any]:
    trace = getattr(run_execution, "trace", None)
    if trace is None:
        return MappingProxyType({})
    raw_by_segment = getattr(trace, "wall_deposit_by_segment_species_kg", None)
    if raw_by_segment is None:
        raise ObjectiveComputationError(
            "wall_deposit_by_segment_species_kg trace is missing"
        )
    if not isinstance(raw_by_segment, Mapping):
        raise ObjectiveComputationError(
            "wall_deposit_by_segment_species_kg trace is not a mapping"
        )
    by_segment = _wall_deposit_by_segment_species_summary(raw_by_segment)
    zone_by_segment = getattr(trace, "wall_zone_by_segment", None)
    if zone_by_segment is None:
        raise ObjectiveComputationError("wall_zone_by_segment trace is missing")
    if not isinstance(zone_by_segment, Mapping):
        raise ObjectiveComputationError("wall_zone_by_segment trace is not a mapping")
    try:
        by_zone = wall_deposit_kg_by_zone_species(raw_by_segment, zone_by_segment)
    except (TypeError, ValueError) as exc:
        raise ObjectiveComputationError(str(exc)) from exc
    return MappingProxyType({
        "wall_deposit_kg_by_segment_species": by_segment,
        "wall_deposit_kg_by_zone_species": MappingProxyType({
            zone: MappingProxyType(dict(species_kg))
            for zone, species_kg in by_zone.items()
        }),
        "campaigns_to_resinter": _campaigns_to_resinter(raw_by_segment),
    })


def _product_bins(product_classes: Mapping[str, Any]) -> Mapping[str, Any]:
    bins: dict[str, Mapping[str, Any]] = {}
    for key, label in (
        ("ingots_metals", "Ingots/metals"),
        ("glass", "Glass"),
        ("oxygen", "O2"),
        ("captured_volatiles", "Captured volatiles"),
        ("refractory_ceramic_rump", "Refractory ceramic/rump"),
    ):
        value = product_classes.get(key)
        if not isinstance(value, Mapping):
            continue
        total = _optional_finite_float(value.get("class_total_kg"), key)
        if total is None or total <= 0.0:
            continue
        payload = dict(value)
        payload["id"] = key
        payload["label"] = label
        payload["kg"] = total
        bins[key] = MappingProxyType(payload)
    return MappingProxyType(bins)


def _product_yield_table(
    sim: Any,
    profile: Mapping[str, Any],
    product_bins: Mapping[str, Any],
    product_classes: Mapping[str, Any],
) -> Mapping[str, Any]:
    inputs = _input_line_items(sim, profile)
    total_input_kg = sum(row["kg"] for row in inputs)
    outputs = [
        _output_line_item(key, value, total_input_kg)
        for key, value in product_bins.items()
    ]
    products_out_kg = sum(row["kg"] for row in outputs)
    closure = _mass_closure_row(sim, total_input_kg, products_out_kg)
    status = closure["status"]
    unclassified = _unclassified_product_mass(product_classes)
    diagnostics: list[Mapping[str, Any]] = []
    if unclassified["total_kg"] > 0.0:
        diagnostics.append(_unclassified_product_mass_row(unclassified))
        status = "inconclusive"
    if not inputs:
        status = "inconclusive"
    table: dict[str, Any] = {
        "status": status,
        "inputs": tuple(inputs),
        "outputs": tuple(outputs),
        "mass_closure": MappingProxyType(closure),
        "total_input_kg": total_input_kg,
        "products_out_kg": products_out_kg,
    }
    if diagnostics:
        table["diagnostics"] = tuple(diagnostics)
        table["unclassified_product_mass"] = unclassified
    return MappingProxyType(table)


def _unclassified_product_mass(product_classes: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = product_classes.get("unclassified")
    if not isinstance(raw, Mapping):
        return MappingProxyType({
            "kg_by_species": MappingProxyType({}),
            "total_kg": 0.0,
        })
    kg_by_species: dict[str, float] = {}
    raw_species = raw.get("kg_by_species")
    if isinstance(raw_species, Mapping):
        for species, kg in raw_species.items():
            value = _optional_finite_float(kg, f"unclassified.{species}")
            if value is not None and value > 0.0:
                kg_by_species[str(species)] = value
    total = _optional_finite_float(raw.get("total_kg"), "unclassified.total_kg")
    if total is None:
        total = sum(kg_by_species.values())
    return MappingProxyType({
        "kg_by_species": MappingProxyType(kg_by_species),
        "total_kg": total,
    })


def _unclassified_product_mass_row(
    unclassified: Mapping[str, Any],
) -> Mapping[str, Any]:
    return MappingProxyType({
        "kind": "diagnostic",
        "id": "unclassified_product_mass",
        "label": "Unclassified product mass",
        "kg": unclassified["total_kg"],
        "kg_by_species": unclassified["kg_by_species"],
        "status": "inconclusive",
        "reason": "product ledger species are outside named product bins",
    })


def _input_line_items(sim: Any, profile: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    record = getattr(sim, "record", None)
    profile_run = profile.get("run", {}) if isinstance(profile.get("run"), Mapping) else {}
    feedstock_id = (
        str(getattr(record, "feedstock_key", "") or "")
        or str(profile.get("feedstock") or profile.get("feedstock_id") or "")
    )
    batch_mass = _optional_finite_float(
        getattr(record, "batch_mass_kg", None),
        "record.batch_mass_kg",
    )
    if batch_mass is None:
        batch_mass = _optional_finite_float(profile_run.get("mass_kg"), "profile.run.mass_kg")

    rows: list[Mapping[str, Any]] = []
    if batch_mass is not None and batch_mass > 0.0:
        rows.append(MappingProxyType({
            "kind": "input",
            "id": "feedstock",
            "label": "Feedstock" if not feedstock_id else f"Feedstock: {feedstock_id}",
            "kg": batch_mass,
            "source": "feedstock",
        }))

    additives = getattr(record, "additives_kg", None)
    if not isinstance(additives, Mapping):
        additives = profile_run.get("additives_kg", {})
    if isinstance(additives, Mapping):
        for species, kg in sorted(additives.items()):
            amount = _optional_finite_float(kg, f"additives_kg[{species!r}]")
            if amount is None or amount <= 0.0:
                continue
            name = str(species)
            rows.append(MappingProxyType({
                "kind": "input",
                "id": f"additive:{name}",
                "label": name,
                "kg": amount,
                "source": "additive",
            }))
    return rows


def _output_line_item(
    key: str,
    value: Mapping[str, Any],
    total_input_kg: float,
) -> Mapping[str, Any]:
    kg = _finite_float(value["kg"], f"product_bins[{key!r}].kg")
    row: dict[str, Any] = {
        "kind": "output",
        "id": key,
        "label": str(value.get("label") or key),
        "kg": kg,
    }
    if total_input_kg > 0.0:
        row["yield_pct"] = kg / total_input_kg * 100.0
    for detail_key in (
        "species_kg",
        "kg_by_species",
        "partition_kg",
        "rump_kg_by_species",
    ):
        if detail_key in value:
            row[detail_key] = value[detail_key]
    return MappingProxyType(row)


def _mass_closure_row(
    sim: Any,
    total_input_kg: float,
    products_out_kg: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": "mass_closure",
        "label": "Mass closure",
        "tolerance_pct": 5e-12,
        "products_out_kg": products_out_kg,
    }
    if total_input_kg > 0.0:
        row["mass_in_kg"] = total_input_kg
        row["product_yield_pct"] = products_out_kg / total_input_kg * 100.0
    latest = _latest_snapshot(sim)
    if latest is None:
        row["status"] = "inconclusive"
        row["reason"] = "mass_balance_error_pct missing"
        return row
    balance = _optional_finite_float(
        getattr(latest, "mass_balance_error_pct", None),
        "mass_balance_error_pct",
    )
    if balance is None:
        row["status"] = "inconclusive"
        row["reason"] = "mass_balance_error_pct missing"
        return row
    mass_out = _optional_finite_float(getattr(latest, "mass_out_kg", None), "mass_out_kg")
    if mass_out is not None:
        row["accountable_mass_out_kg"] = mass_out
    row["balance_error_pct"] = balance
    row["status"] = "closed" if abs(balance) <= row["tolerance_pct"] else "open"
    return row


def _latest_snapshot(sim: Any) -> Any | None:
    snapshots = getattr(getattr(sim, "record", None), "snapshots", None)
    if snapshots:
        return snapshots[-1]
    direct = getattr(sim, "_make_snapshot", None)
    if callable(direct):
        return direct()
    return None


def _optional_finite_float(value: Any, label: str) -> float | None:
    if value is None:
        return None
    return _finite_float(value, label)


def _wall_deposit_by_segment_species_summary(
    raw: Mapping[Any, Any],
) -> Mapping[str, Mapping[str, float]]:
    by_segment: dict[str, dict[str, float]] = {}
    for key, kg in raw.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise ObjectiveComputationError(
                "wall deposit key must be (segment, species)"
            )
        segment, species = str(key[0]), str(key[1])
        amount = _finite_float(kg, f"wall_deposit[{segment!r}][{species!r}]")
        if amount <= 1e-12:
            continue
        species_kg = by_segment.setdefault(segment, {})
        species_kg[species] = species_kg.get(species, 0.0) + amount
    return MappingProxyType({
        segment: MappingProxyType(dict(sorted(species_kg.items())))
        for segment, species_kg in sorted(by_segment.items())
    })


def _campaigns_to_resinter(
    wall_deposit_by_segment_species: Mapping[tuple[str, str], float],
) -> float | str:
    by_species: dict[str, float] = {}
    for key, kg in wall_deposit_by_segment_species.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise ObjectiveComputationError(
                "wall deposit key must be (segment, species)"
            )
        species = str(key[1])
        amount = _finite_float(kg, f"wall_deposit[{key!r}]")
        if amount > 1e-12:
            by_species[species] = by_species.get(species, 0.0) + amount
    if not by_species:
        return "infinite"
    dominant_species = max(by_species, key=by_species.get)
    dominant_kg = by_species[dominant_species]
    threshold = _wall_resinter_threshold_kg()
    if threshold is None:
        return f"resinter_threshold_kg / {dominant_kg:.12g}"
    return threshold / dominant_kg


def _wall_resinter_threshold_kg() -> float | None:
    materials = load_config_bundle(DEFAULT_DATA_DIR).materials
    surfaces = materials.get("wall_surfaces", {}) or {}
    surface = (
        surfaces.get("interstage_duct", {}) or {}
        if isinstance(surfaces, Mapping)
        else {}
    )
    liner_material = str(surface.get("liner_material") or "")
    liners = materials.get("liner_materials", {}) or {}
    liner = (
        liners.get(liner_material, {}) or {}
        if isinstance(liners, Mapping)
        else {}
    )
    threshold = liner.get("resinter_threshold_kg")
    if threshold is None:
        return None
    return _finite_float(threshold, "resinter_threshold_kg")


def product_classes_summary(sim: Any, profile: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(
        classify_products(
            sim,
            early_tap_mode=bool(profile.get("early_tap_mode", False)),
        )
    )


def _metric_value(
    metric: str,
    sim: Any,
    product_ledger: Mapping[str, float],
    product_classes: Mapping[str, Any],
) -> float:
    if metric == "pure_silica_glass_kg":
        return _nested_float(product_classes, ("pure_silica_glass", "class_total_kg"))
    if metric == "metals_plus_o2_kg":
        return _nested_float(product_classes, ("metals_plus_O2", "class_total_kg"))
    if metric == "metals_total_kg":
        return _nested_float(product_classes, ("metals_plus_O2", "metals_total_kg"))
    if metric in {"O2_kg", "o2_kg", "oxygen_kg"}:
        return _nested_float(product_classes, ("metals_plus_O2", "O2_kg"))
    if metric == "oxygen_stored_kg":
        return _oxygen_partition_value(sim, "stored")
    if metric == "oxygen_vented_kg":
        return _oxygen_partition_value(sim, "vented")
    if metric in {"energy_kWh", "energy_total_kWh"}:
        return _sim_float(sim, "energy_cumulative_kWh", "energy_total_kWh")
    if metric in {"duration_h", "total_hours"}:
        return _duration_hours(sim)
    if metric.endswith("_kg"):
        species = metric[:-3]
        if species in product_ledger:
            return _finite_float(product_ledger[species], metric)
    raise ObjectiveComputationError(
        f"objective metric {metric!r} is not available from run outputs"
    )


def _objective_mapping(objectives: ObjectiveLike) -> Mapping[str, float]:
    if isinstance(objectives, ObjectiveVector):
        return objectives.as_mapping()
    if isinstance(objectives, Mapping):
        return objectives
    accessor = getattr(objectives, "as_mapping", None)
    if callable(accessor):
        raw = accessor()
        if isinstance(raw, Mapping):
            return raw
    raise ObjectiveComputationError("objective values must be a mapping")


def _product_ledger(sim: Any) -> Mapping[str, float]:
    ledger_method = getattr(sim, "product_ledger", None)
    if callable(ledger_method):
        raw = ledger_method()
    else:
        raw = getattr(getattr(sim, "record", None), "products_kg", {})
    if not isinstance(raw, Mapping):
        raise ObjectiveComputationError("product ledger is not a mapping")
    return MappingProxyType({
        str(species): _finite_float(kg, f"product_ledger[{species!r}]")
        for species, kg in raw.items()
    })


def _nested_float(root: Mapping[str, Any], path: tuple[str, ...]) -> float:
    node: Any = root
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            raise ObjectiveComputationError(
                f"objective source missing {'.'.join(path)}"
            )
        node = node[key]
    return _finite_float(node, ".".join(path))


def _oxygen_partition_value(sim: Any, key: str) -> float:
    partition_method = getattr(sim, "_oxygen_terminal_partition_kg", None)
    if callable(partition_method):
        partition = partition_method()
        if not isinstance(partition, Mapping):
            raise ObjectiveComputationError("oxygen terminal partition is not a mapping")
        if key not in partition or partition[key] is None:
            raise ObjectiveComputationError(
                f"oxygen terminal partition missing {key!r}"
            )
        return _finite_float(partition[key], f"oxygen_partition[{key!r}]")
    record = getattr(sim, "record", None)
    if record is not None:
        attr = "oxygen_stored_kg" if key == "stored" else "oxygen_vented_kg"
        return _required_attr_float(record, attr)
    raise ObjectiveComputationError("oxygen terminal partition unavailable")


def _sim_float(sim: Any, sim_attr: str, record_attr: str) -> float:
    value = getattr(sim, sim_attr, _MISSING)
    if value is not _MISSING:
        if value is None:
            raise ObjectiveComputationError(f"{sim_attr} is missing")
        return _finite_float(value, sim_attr)
    record = getattr(sim, "record", None)
    if record is not None:
        return _required_attr_float(record, record_attr)
    raise ObjectiveComputationError(f"{sim_attr} unavailable")


def _duration_hours(sim: Any) -> float:
    melt = getattr(sim, "melt", None)
    if melt is not None:
        value = getattr(melt, "hour", _MISSING)
        if value is not _MISSING:
            if value is None:
                raise ObjectiveComputationError("melt.hour is missing")
            return _finite_float(value, "melt.hour")
    record = getattr(sim, "record", None)
    if record is not None:
        return _required_attr_float(record, "total_hours")
    raise ObjectiveComputationError("run duration unavailable")


def _required_attr_float(obj: Any, attr: str) -> float:
    value = getattr(obj, attr, _MISSING)
    if value is _MISSING or value is None:
        raise ObjectiveComputationError(f"{attr} is missing")
    return _finite_float(value, attr)


def _finite_float(value: Any, label: str) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ObjectiveComputationError(f"{label} is not numeric") from exc
    if not math.isfinite(converted):
        raise ObjectiveComputationError(f"{label} is non-finite")
    return converted
