"""Feasible-run objective vector projection for recipe optimization."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
import fnmatch
import hashlib
import math
from types import MappingProxyType
from collections.abc import Callable, Mapping as MappingABC, Sequence
from typing import Any, Iterable, Mapping, TypeVar

from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.accounting.formulas import resolve_species_formula
from simulator.config import DEFAULT_DATA_DIR, load_config_bundle
from simulator.feedstock_composition import normalized_feedstock_component_masses_kg
from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value
from simulator.optimize.physics import (
    PhysicsConstraintSet,
    ThresholdSpec,
    extraction_completeness_report,
    target_species_yield_report,
)
from simulator.optimize.product_pools import COMPOSITION_PRODUCT_POOLS, STREAM_PRODUCT_POOLS
from simulator.three_product_report import classify_products
from simulator.diagnostics import (
    wall_deposit_remobilization_by_segment_species,
    wall_deposit_sticking_authority_status,
)
from simulator.trace import wall_deposit_kg_by_zone_species


_MISSING = object()
VALID_OBJECTIVE_SENSES = {"minimize", "maximize"}
COMPOSITION_TARGET_TYPE = "composition_target"
COMPOSITION_TARGET_METRIC_PREFIX = "composition_target:"
SSO2_OWNER_RECIPE_ID = "sso2_pn2_fe_drain_silica"
SUPPORTED_COMPOSITION_POOLS = COMPOSITION_PRODUCT_POOLS
COMPOSITION_VECTOR_SPECIES = frozenset({"Na", "K", "Fe", "Mg", "Si", "Al", "Ca", "O2"})
COMPOSITION_VECTOR_ROLES = frozenset({"extract", "retain", "free", "to_window"})
COMPOSITION_WINDOW_MODES = frozenset({"hard_window", "soft_distance"})
COMPOSITION_EXTRACTION_MECHANISMS = frozenset(
    {"thermal_volatilization", "c3_metallothermic_shuttle", "c6_mg_thermite"}
)
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
        "thermal_window",
        "hold_construction",
        "constraints",
        "score_weights",
    }
)
_TARGET_SPEC_DIGEST_DISPLAY_KEYS = frozenset({"thermal_window", "hold_construction"})
_EXTRACTION_KEYS = frozenset(
    {"basis", "captured_pool", "credit_policy", "completeness_min", "mechanisms"}
)
_CREDIT_POLICY_KEYS = frozenset({"additives", "vented"})
_COMPOSITION_WINDOW_KEYS = frozenset(
    {"pool", "basis", "mode", "oxides", "ratios", "exploratory"}
)
_OXIDE_ROW_KEYS = frozenset(
    {"oxide", "min", "max", "strict", "weight", "needs_experiment", "tier", "provenance"}
)
_RATIO_ROW_KEYS = frozenset(
    {
        "numerator",
        "denominator",
        "min",
        "max",
        "strict",
        "weight",
        "needs_experiment",
        "provenance",
    }
)
_MATURITY_KEYS = frozenset({"mode", "campaign", "hours", "best_tap"})
_BEST_TAP_KEYS = frozenset(
    {
        "enabled",
        "tap_grid",
        "tap_stability_hours",
        "dwell_policy",
        "captured_pool_nonterminal_policy",
        "nonterminal_captured_pool_note",
    }
)
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
        "cleaned_melt_at_stage0_exit": ("process.cleaned_melt",),
        "residual_rump_at_stop": (
            "process.cleaned_melt",
            SPENT_REDUCTANT_RESIDUE_ACCOUNT,
        ),
        "terminal_rump_earned": ("terminal.slag",),
    }
)
_OXIDE_KEY_ALIASES = MappingProxyType(
    {
        "FeO_total": ("FeO",),
        "Fe_total_as_Fe2O3_wt_pct": ("Fe2O3",),
        "Na2O_plus_K2O": ("K2O", "Na2O"),
        "Al2O3_CaO_MgO_balance": ("Al2O3", "CaO", "MgO"),
        "TiO2_plus_Cr2O3_plus_REO": ("Cr2O3", "TiO2", "REO"),
    }
)
_VENTED_PRODUCT_ACCOUNTS = frozenset(
    {"terminal.oxygen_melt_offgas_vented_to_vacuum", "vent"}
)
# Source: AccountingQueries.product_ledger merges sim._unspent_additive_reagents_kg(),
# which emits unspent_<element>_reagent entries for additive bookkeeping.
CAPTURED_PRODUCT_BOOKKEEPING_SPECIES_PATTERNS = ("unspent_*_reagent",)
_EPS = 1.0e-12
_SNAPSHOT_GRADE_WT_ABS_TOL = 1.0e-6
_SNAPSHOT_GRADE_WT_REL_TOL = 1.0e-7
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

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return (type(self), (self.values, _thaw_value(self.evidence)))


ObjectiveLike = ObjectiveVector | Mapping[str, float]
T = TypeVar("T")


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw_value(item) for item in value)
    if isinstance(value, list):
        return [_thaw_value(item) for item in value]
    return value


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
    result: dict[str, Any] = {}
    thermal_window = target.get("thermal_window")
    if isinstance(thermal_window, str) and thermal_window:
        result["thermal_window"] = thermal_window
    hold_construction = target.get("hold_construction")
    if isinstance(hold_construction, str) and hold_construction:
        result["hold_construction"] = hold_construction
    window = target.get("composition_window", {})
    if not isinstance(window, Mapping):
        return result
    oxides = window.get("oxides", {})
    if not isinstance(oxides, Mapping):
        oxides = {}
    resolved_oxides: dict[str, Mapping[str, Any]] = {}
    for oxide, raw in oxides.items():
        if not isinstance(raw, Mapping):
            continue
        row: dict[str, Any] = {
            "min": raw.get("min"),
            "max": raw.get("max"),
            "strict": raw.get("strict", True),
            "weight": raw.get("weight"),
        }
        for key in ("tier", "needs_experiment", "provenance"):
            if key in raw:
                row[key] = raw[key]
        if any(key in row for key in ("tier", "needs_experiment", "provenance")):
            resolved_oxides[str(oxide)] = MappingProxyType(row)
    resolved_ratios: list[Mapping[str, Any]] = []
    ratios = window.get("ratios", ())
    if isinstance(ratios, (list, tuple)):
        for raw in ratios:
            if not isinstance(raw, Mapping):
                continue
            row: dict[str, Any] = {
                "numerator": tuple(raw.get("numerator", ())),
                "denominator": tuple(raw.get("denominator", ())),
                "min": raw.get("min"),
                "max": raw.get("max"),
                "strict": raw.get("strict", True),
                "weight": raw.get("weight"),
            }
            for key in ("needs_experiment", "provenance"):
                if key in raw:
                    row[key] = raw[key]
            resolved_ratios.append(MappingProxyType(row))
    if not resolved_oxides and not resolved_ratios:
        return result
    payload: dict[str, Any] = {
        "pool": str(window.get("pool", "")),
        "mode": str(window.get("mode", "")),
        "exploratory": bool(window.get("exploratory", False)),
        "oxides": MappingProxyType(resolved_oxides),
    }
    if resolved_ratios:
        payload["ratios"] = tuple(resolved_ratios)
    result["composition_window"] = MappingProxyType(payload)
    return result


def target_spec_digest(target: Mapping[str, Any]) -> str:
    # Exclude `thermal_window`: derived display text; seed/patch windows carry runtime meaning.
    normalized = normalize_canonical_value(_target_spec_digest_payload(target))
    return hashlib.sha256(canonical_json_dumps(normalized).encode("utf-8")).hexdigest()


def _target_spec_digest_payload(value: Any) -> Any:
    if isinstance(value, MappingABC):
        return {
            str(key): _target_spec_digest_payload(item)
            for key, item in value.items()
            if str(key) not in _TARGET_SPEC_DIGEST_DISPLAY_KEYS
        }
    if isinstance(value, tuple):
        return tuple(_target_spec_digest_payload(item) for item in value)
    if isinstance(value, list):
        return [_target_spec_digest_payload(item) for item in value]
    return value


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


def composition_targets_require_stage0_exit(profile: Mapping[str, Any]) -> bool:
    for objective in composition_target_specs(profile):
        target = objective["target"]
        window = target.get("composition_window", {})
        pools = {
            str(target.get("pool", "")),
            str(window.get("pool", "")) if isinstance(window, Mapping) else "",
        }
        if "cleaned_melt_at_stage0_exit" in pools:
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
        {"pool", "species_vector", "score_weights"},
        where,
    )
    pool = _validate_pool(raw.get("pool"), f"{where}.pool")
    vector = _normalize_species_vector(raw.get("species_vector"), where=f"{where}.species_vector")
    extraction = _normalize_extraction(
        raw.get("extraction", {}),
        vector=vector,
        where=f"{where}.extraction",
    )
    raw_window = raw.get("composition_window", _MISSING)
    window: Mapping[str, Any] | None = None
    if raw_window is not _MISSING and raw_window is not None:
        window = _normalize_composition_window(
            raw_window,
            target_id=target_id,
            where=f"{where}.composition_window",
        )
    score_weights = _normalize_score_weights(
        raw.get("score_weights"),
        vector=vector,
        window=window,
        where=f"{where}.score_weights",
    )
    maturity = _normalize_maturity(
        raw.get("maturity", {}),
        target_id=target_id,
        window=window,
        where=f"{where}.maturity",
    )
    constraints = _normalize_target_constraints(
        raw.get("constraints", {}),
        where=f"{where}.constraints",
    )
    require_coating_gate = True
    if "require_coating_gate" in raw:
        require_coating_gate = raw["require_coating_gate"]
        if not isinstance(require_coating_gate, bool):
            raise ObjectiveProfileError(f"{where}.require_coating_gate must be bool")
    thermal_window = raw.get("thermal_window")
    if "thermal_window" in raw:
        if not isinstance(thermal_window, str) or not thermal_window.strip():
            raise ObjectiveProfileError(f"{where}.thermal_window must be a non-empty string")
        thermal_window = thermal_window.strip()
    hold_construction = raw.get("hold_construction")
    if "hold_construction" in raw:
        if not isinstance(hold_construction, str) or not hold_construction.strip():
            raise ObjectiveProfileError(f"{where}.hold_construction must be a non-empty string")
        hold_construction = hold_construction.strip()
    _validate_target_shape(
        vector=vector,
        extraction=extraction,
        window=window,
        score_weights=score_weights,
        where=where,
    )
    normalized = {
        "pool": pool,
        "require_coating_gate": require_coating_gate,
        "species_vector": MappingProxyType(vector),
        "extraction": MappingProxyType(extraction),
        "maturity": MappingProxyType(maturity),
        "constraints": MappingProxyType(constraints),
        "score_weights": MappingProxyType(score_weights),
    }
    if window is not None:
        normalized["composition_window"] = MappingProxyType(window)
    if thermal_window is not None:
        normalized["thermal_window"] = thermal_window
    if hold_construction is not None:
        normalized["hold_construction"] = hold_construction
    return normalized


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
    if captured_pool not in STREAM_PRODUCT_POOLS:
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
    raw_mechanisms = raw.get("mechanisms", {})
    if raw_mechanisms is None:
        raw_mechanisms = {}
    if not isinstance(raw_mechanisms, Mapping):
        raise ObjectiveProfileError(f"{where}.mechanisms must be a mapping")
    mechanisms: dict[str, str] = {}
    for species, mechanism in raw_mechanisms.items():
        key = str(species)
        if key not in normalized_min:
            raise ObjectiveProfileError(
                f"{where}.mechanisms.{key} must match an extracted species"
            )
        mechanism_name = str(mechanism)
        if mechanism_name not in COMPOSITION_EXTRACTION_MECHANISMS:
            raise ObjectiveProfileError(
                f"{where}.mechanisms.{key} has invalid mechanism {mechanism_name!r}"
            )
        mechanisms[key] = mechanism_name
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
        "mechanisms": MappingProxyType(mechanisms),
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
    _require_objective_keys(raw, {"pool", "basis", "mode"}, where)
    pool = _validate_pool(raw.get("pool"), f"{where}.pool")
    if str(raw.get("basis")) != "oxide_wt_pct":
        raise ObjectiveProfileError(f"{where}.basis must be 'oxide_wt_pct'")
    mode = str(raw.get("mode"))
    if mode not in COMPOSITION_WINDOW_MODES:
        raise ObjectiveProfileError(f"{where}.mode must be hard_window or soft_distance")
    exploratory_raw = raw.get("exploratory", False)
    if not isinstance(exploratory_raw, bool):
        raise ObjectiveProfileError(f"{where}.exploratory must be bool")
    exploratory = exploratory_raw
    if mode == "soft_distance" and (
        not exploratory or str(target_id).startswith("pc-")
    ):
        raise ObjectiveProfileError(
            f"{where}.mode soft_distance requires exploratory non-menu target"
        )
    normalized_oxides = _normalize_oxide_rows(raw.get("oxides", ()), where=f"{where}.oxides")
    normalized_ratios = _normalize_ratio_rows(raw.get("ratios", ()), where=f"{where}.ratios")
    if not normalized_oxides and not normalized_ratios:
        raise ObjectiveProfileError(f"{where} must contain at least one oxide or ratio row")
    if normalized_ratios:
        if not any(
            bool(row.get("strict", True)) and len(_oxide_key_elements(str(oxide))) == 1
            for oxide, row in normalized_oxides.items()
        ):
            raise ObjectiveProfileError(
                f"{where}.ratios require at least one strict per-species oxide band"
            )
        if any(not bool(row.get("strict", True)) for row in normalized_oxides.values()):
            raise ObjectiveProfileError(
                f"{where}.ratios companion oxide bands must be strict"
            )
    strict_rows = [
        row
        for row in (*normalized_oxides.values(), *normalized_ratios)
        if bool(row.get("strict", True))
    ]
    soft_rows = [
        row
        for row in (*normalized_oxides.values(), *normalized_ratios)
        if not bool(row.get("strict", True))
    ]
    if soft_rows and mode == "hard_window":
        for row in soft_rows:
            lower = float(row["min"])
            upper = float(row["max"])
            if upper <= lower:
                raise ObjectiveProfileError(f"{where} soft row has zero-width band")
    if not strict_rows and not exploratory:
        raise ObjectiveProfileError(
            f"{where} certifiable non-exploratory target needs at least one strict row"
        )
    return {
        "pool": pool,
        "basis": "oxide_wt_pct",
        "mode": mode,
        "oxides": MappingProxyType(normalized_oxides),
        "ratios": tuple(MappingProxyType(row) for row in normalized_ratios),
        "exploratory": exploratory,
    }


def _normalize_oxide_rows(raw: Any, *, where: str) -> dict[str, Mapping[str, Any]]:
    if raw in (None, _MISSING):
        return {}
    items: list[tuple[str, Any]] = []
    if isinstance(raw, Mapping):
        items = [(str(oxide), row) for oxide, row in raw.items()]
    elif isinstance(raw, (list, tuple)):
        for ordinal, item in enumerate(raw):
            if not isinstance(item, Mapping):
                raise ObjectiveProfileError(f"{where}[{ordinal}] must be a mapping")
            if "oxide" not in item:
                raise ObjectiveProfileError(f"{where}[{ordinal}].oxide is required")
            oxide_name = str(item["oxide"])
            row = dict(item)
            row.pop("oxide", None)
            items.append((oxide_name, row))
    else:
        raise ObjectiveProfileError(f"{where} must be a mapping or list")
    normalized: dict[str, Mapping[str, Any]] = {}
    for oxide_name, row in items:
        _validate_oxide_key(oxide_name, f"{where}.{oxide_name}")
        normalized[oxide_name] = MappingProxyType(
            _normalize_oxide_row(row, where=f"{where}.{oxide_name}")
        )
    return normalized


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
    strict = row.get("strict", True)
    if not isinstance(strict, bool):
        raise ObjectiveProfileError(f"{where}.strict must be bool")
    normalized = {
        "min": lower,
        "max": upper,
        "strict": strict,
        "weight": weight,
    }
    if "needs_experiment" in row:
        normalized["needs_experiment"] = bool(row["needs_experiment"])
    if "tier" in row:
        normalized["tier"] = str(row["tier"])
    if "provenance" in row:
        normalized["provenance"] = str(row["provenance"])
    return normalized


def _normalize_ratio_rows(raw: Any, *, where: str) -> tuple[dict[str, Any], ...]:
    if raw in (None, _MISSING):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ObjectiveProfileError(f"{where} must be a list")
    normalized: list[dict[str, Any]] = []
    for ordinal, item in enumerate(raw):
        row_where = f"{where}[{ordinal}]"
        if not isinstance(item, Mapping):
            raise ObjectiveProfileError(f"{row_where} must be a mapping")
        payload = item.get("ratio", item)
        if not isinstance(payload, Mapping):
            raise ObjectiveProfileError(f"{row_where}.ratio must be a mapping")
        _reject_unknown_objective_keys(payload, _RATIO_ROW_KEYS, f"{row_where}.ratio")
        _require_objective_keys(
            payload,
            {"numerator", "denominator", "min", "max", "weight"},
            f"{row_where}.ratio",
        )
        lower = _finite_profile_float(payload["min"], f"{row_where}.ratio.min")
        upper = _finite_profile_float(payload["max"], f"{row_where}.ratio.max")
        if upper < lower:
            raise ObjectiveProfileError(f"{row_where}.ratio has empty window")
        weight = _positive_profile_float(payload["weight"], f"{row_where}.ratio.weight")
        strict = payload.get("strict", True)
        if not isinstance(strict, bool):
            raise ObjectiveProfileError(f"{row_where}.ratio.strict must be bool")
        normalized_row: dict[str, Any] = {
            "numerator": _normalize_ratio_operand(
                payload["numerator"],
                where=f"{row_where}.ratio.numerator",
            ),
            "denominator": _normalize_ratio_operand(
                payload["denominator"],
                where=f"{row_where}.ratio.denominator",
            ),
            "min": lower,
            "max": upper,
            "strict": strict,
            "weight": weight,
        }
        if "needs_experiment" in payload:
            normalized_row["needs_experiment"] = bool(payload["needs_experiment"])
        if "provenance" in payload:
            normalized_row["provenance"] = str(payload["provenance"])
        normalized.append(normalized_row)
    return tuple(normalized)


def _normalize_score_weights(
    raw: Any,
    *,
    vector: Mapping[str, str],
    window: Mapping[str, Any] | None,
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
    row_count = 0
    if window is not None:
        row_count += len(window.get("oxides", {}))
        row_count += len(window.get("ratios", ()))
    if weights["composition"] > 0.0 and row_count <= 0:
        raise ObjectiveProfileError(f"{where}.composition positive but window is empty")
    return weights


def _validate_target_shape(
    *,
    vector: Mapping[str, str],
    extraction: Mapping[str, Any],
    window: Mapping[str, Any] | None,
    score_weights: Mapping[str, float],
    where: str,
) -> None:
    has_extract = any(role == "extract" for role in vector.values())
    has_retain = any(role == "retain" for role in vector.values())
    has_to_window = any(role == "to_window" for role in vector.values())
    if window is None:
        if has_retain:
            raise ObjectiveProfileError(f"{where}.composition_window required for retain role")
        if has_to_window:
            raise ObjectiveProfileError(f"{where}.composition_window required for to_window role")
        if not has_extract:
            raise ObjectiveProfileError(f"{where}.species_vector has no target role")
        if any(role != "extract" and role != "free" for role in vector.values()):
            raise ObjectiveProfileError(f"{where}.species_vector invalid windowless target shape")
        if tuple(score_weights.items()) != (
            ("extraction", 1.0),
            ("composition", 0.0),
        ):
            raise ObjectiveProfileError(
                f"{where}.score_weights windowless extraction-only requires extraction 1.0"
            )
        if set(extraction.get("completeness_min", {})) != {
            species for species, role in vector.items() if role == "extract"
        }:
            raise ObjectiveProfileError(
                f"{where}.extraction.completeness_min must match extract species"
            )
        return

    if has_to_window:
        _validate_to_window_correspondence(vector, window, f"{where}.species_vector")


def _validate_to_window_correspondence(
    vector: Mapping[str, str],
    window: Mapping[str, Any],
    where: str,
) -> None:
    oxides = window.get("oxides", {})
    ratios = window.get("ratios", ())
    for species, role in vector.items():
        if role != "to_window":
            continue
        if any(species in _oxide_key_elements(str(oxide)) for oxide in oxides):
            continue
        found_ratio = False
        for ratio in ratios:
            numerator = ratio.get("numerator", ())
            if any(species in _oxide_key_elements(str(oxide)) for oxide in numerator):
                found_ratio = True
                break
        if found_ratio:
            continue
        raise ObjectiveProfileError(
            f"{where}.{species} to_window requires same-pool oxide row or ratio numerator"
        )


def _normalize_ratio_operand(raw: Any, *, where: str) -> tuple[str, ...]:
    if isinstance(raw, str):
        operands = (raw,)
    elif isinstance(raw, (list, tuple)):
        if not raw:
            raise ObjectiveProfileError(f"{where} must not be empty")
        operands = tuple(str(item) for item in raw)
    else:
        raise ObjectiveProfileError(f"{where} must be an oxide key or explicit list")
    normalized: set[str] = set()
    for oxide in operands:
        _validate_oxide_key(oxide, where)
        normalized.update(_canonical_oxide_operand_parts(oxide))
    return tuple(sorted(normalized))


def _canonical_oxide_operand_parts(oxide_name: str) -> tuple[str, ...]:
    if oxide_name in _OXIDE_KEY_ALIASES:
        return tuple(_OXIDE_KEY_ALIASES[oxide_name])
    if "_plus_" in oxide_name:
        return tuple(sorted(str(part) for part in oxide_name.split("_plus_")))
    return (oxide_name,)


def _validate_oxide_key(oxide_name: str, where: str) -> None:
    if not oxide_name:
        raise ObjectiveProfileError(f"{where} oxide key is required")
    if oxide_name in _OXIDE_KEY_ALIASES:
        return
    if "_plus_" in oxide_name:
        for part in oxide_name.split("_plus_"):
            _validate_oxide_key(part, where)
        return
    try:
        formula = resolve_species_formula(oxide_name)
    except Exception as exc:  # noqa: BLE001 - profile validation reports the key.
        raise ObjectiveProfileError(f"{where} unknown oxide key {oxide_name!r}") from exc
    if "O" not in formula.elements:
        raise ObjectiveProfileError(f"{where} oxide key {oxide_name!r} must contain oxygen")


def _oxide_key_elements(oxide_name: str) -> frozenset[str]:
    if oxide_name == "TiO2_plus_Cr2O3_plus_REO":
        return frozenset({"Ti", "Cr", "REE"})
    elements: set[str] = set()
    for part in _canonical_oxide_operand_parts(oxide_name):
        if part == "REO":
            elements.add("REE")
            continue
        formula = resolve_species_formula(part)
        elements.update(element for element in formula.elements if element != "O")
    return frozenset(elements)


def _normalize_maturity(
    raw: Any,
    *,
    target_id: str,
    window: Mapping[str, Any] | None,
    where: str,
) -> dict[str, Any]:
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
    if "best_tap" in raw:
        result["best_tap"] = MappingProxyType(
            _normalize_best_tap(
                raw["best_tap"],
                target_id=target_id,
                window=window,
                where=f"{where}.best_tap",
            )
        )
    return result


def _normalize_best_tap(
    raw: Any,
    *,
    target_id: str,
    window: Mapping[str, Any] | None,
    where: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ObjectiveProfileError(f"{where} must be a mapping")
    _reject_unknown_objective_keys(raw, _BEST_TAP_KEYS, where)
    enabled = bool(raw.get("enabled", False))
    if not isinstance(raw.get("enabled", False), bool):
        raise ObjectiveProfileError(f"{where}.enabled must be bool")
    if not enabled:
        return {"enabled": False}

    exploratory = bool(window.get("exploratory", False)) if isinstance(window, Mapping) else False
    default_stability = 1 if exploratory else 3
    stability = _positive_profile_int(
        raw.get("tap_stability_hours", default_stability),
        f"{where}.tap_stability_hours",
    )
    if not exploratory and stability < 2:
        raise ObjectiveProfileError(
            f"{where}.tap_stability_hours must be >= 2 for certifying targets"
        )

    tap_grid = _normalize_tap_grid(raw.get("tap_grid", "recorded_hours"), f"{where}.tap_grid")
    dwell_policy = str(raw.get("dwell_policy", "trailing_recorded_hours"))
    if dwell_policy != "trailing_recorded_hours":
        raise ObjectiveProfileError(
            f"{where}.dwell_policy must be 'trailing_recorded_hours'"
        )
    captured_policy = str(raw.get("captured_pool_nonterminal_policy", "fail_loud"))
    if captured_policy not in {"fail_loud", "allow_with_note"}:
        raise ObjectiveProfileError(
            f"{where}.captured_pool_nonterminal_policy must be fail_loud or allow_with_note"
        )
    normalized: dict[str, Any] = {
        "enabled": True,
        "tap_grid": tap_grid,
        "tap_stability_hours": stability,
        "dwell_policy": dwell_policy,
        "captured_pool_nonterminal_policy": captured_policy,
    }
    if "nonterminal_captured_pool_note" in raw:
        normalized["nonterminal_captured_pool_note"] = _required_text(
            raw["nonterminal_captured_pool_note"],
            f"{where}.nonterminal_captured_pool_note",
        )
    return normalized


def _normalize_tap_grid(raw: Any, where: str) -> str | tuple[int, ...]:
    if raw is None:
        return "recorded_hours"
    if isinstance(raw, str):
        if raw != "recorded_hours":
            raise ObjectiveProfileError(f"{where} must be 'recorded_hours' or a list of hours")
        return raw
    if not isinstance(raw, (list, tuple)):
        raise ObjectiveProfileError(f"{where} must be 'recorded_hours' or a list of hours")
    hours = tuple(sorted({_positive_profile_int(item, f"{where}[]") for item in raw}))
    if not hours:
        raise ObjectiveProfileError(f"{where} must not be empty")
    return hours


def _positive_profile_int(value: Any, where: str) -> int:
    if isinstance(value, bool):
        raise ObjectiveProfileError(f"{where} must be an integer")
    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise ObjectiveProfileError(f"{where} must be an integer") from exc
    if float(numeric) != float(value):
        raise ObjectiveProfileError(f"{where} must be an integer")
    if numeric <= 0:
        raise ObjectiveProfileError(f"{where} must be positive")
    return numeric


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
        elif definition.metric == SSO2_OWNER_RECIPE_ID:
            from simulator.optimize.sso2_evidence import (
                sso2_owner_recipe_objective_reader,
            )

            value, reader_evidence = sso2_owner_recipe_objective_reader(
                run_execution,
                constraints=_extraction_constraints_from_profile(profile),
            )
            evidence[definition.metric] = reader_evidence
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
    best_tap = _best_tap_config(objective["target"])
    if best_tap is not None:
        return _best_tap_score(
            objective,
            run_execution,
            profile,
            best_tap=best_tap,
            evidence=evidence,
        )
    return _composition_target_score_at(
        objective,
        run_execution,
        profile,
        evidence=evidence,
    )


def _composition_target_score_at(
    objective: Mapping[str, Any],
    run_execution: Any,
    profile: Mapping[str, Any],
    *,
    evidence: dict[str, Any] | None = None,
    pool_projection: Mapping[str, Mapping[str, float]] | None = None,
    pool_snapshot_hour: int | None = None,
    tap_provenance: str | None = None,
) -> float:
    target = objective["target"]
    vector = target["species_vector"]
    extraction = target["extraction"]
    window = target.get("composition_window")
    weights = target["score_weights"]
    extraction_weight = float(weights["extraction"])
    composition_weight = float(weights["composition"])
    target_trace: dict[str, Any] = {
        "target_spec_id": str(objective["id"]),
        "target_spec_digest": target_spec_digest(target),
        "target_maturity": dict(target.get("maturity", {})),
        "target_provenance": _target_provenance(target),
        "rows": [],
        "resolved_composition": {"oxide_wt_pct": {}, "ratios": {}},
        "certified_envelope": [],
        "preference_score": None,
        "extraction_completeness": {},
        "certification_tier": "certified",
    }
    if pool_snapshot_hour is not None:
        target_trace["pool_snapshot_hour"] = int(pool_snapshot_hour)
    if tap_provenance is not None:
        target_trace["tap_provenance"] = str(tap_provenance)

    extraction_score = 0.0
    if extraction_weight > 0.0:
        bookkeeping_exclusions: set[str] = set()
        extraction_evidence: dict[str, Any] = {}
        extraction_score = _extraction_score(
            vector,
            extraction,
            run_execution,
            profile,
            bookkeeping_exclusions=bookkeeping_exclusions,
            completeness_evidence=extraction_evidence,
            pool_projection=pool_projection,
        )
        target_trace["extraction_completeness"] = extraction_evidence
        if bookkeeping_exclusions and evidence is not None:
            excluded = tuple(sorted(bookkeeping_exclusions))
            evidence["captured_product_bookkeeping_exclusions"] = excluded
            evidence["notes"] = (
                "excluded captured-products bookkeeping species from extraction credit: "
                + ", ".join(excluded),
            )

    composition_score = 0.0
    if composition_weight > 0.0:
        if not isinstance(window, Mapping):
            raise ObjectiveComputationError("composition target has no composition window")
        window_evidence: dict[str, Any] = {}
        composition_score = _composition_window_score(
            window,
            run_execution,
            evidence=window_evidence,
            pool_projection=pool_projection,
            pool_snapshot_hour=pool_snapshot_hour,
            tap_provenance=tap_provenance,
        )
        target_trace.update(window_evidence)
        if str(window["mode"]) == "soft_distance":
            target_trace["certification_tier"] = "exploratory"
        if str(window["mode"]) == "hard_window" and not bool(
            window_evidence.get("reached_window", composition_score > 0.0)
        ):
            if evidence is not None:
                evidence["composition_target"] = target_trace
            return 0.0

    score = extraction_weight * extraction_score + composition_weight * composition_score
    if evidence is not None:
        evidence["composition_target"] = target_trace
    return score


def _best_tap_config(target: Mapping[str, Any]) -> Mapping[str, Any] | None:
    maturity = target.get("maturity", {})
    if not isinstance(maturity, Mapping):
        return None
    best_tap = maturity.get("best_tap")
    if not isinstance(best_tap, Mapping) or not bool(best_tap.get("enabled", False)):
        return None
    return best_tap


def _best_tap_score(
    objective: Mapping[str, Any],
    run_execution: Any,
    profile: Mapping[str, Any],
    *,
    best_tap: Mapping[str, Any],
    evidence: dict[str, Any] | None = None,
) -> float:
    snapshots = _tap_snapshots(run_execution, best_tap)
    configured_hours = _configured_run_hours(run_execution, profile)
    per_hour_summary = _per_hour_summary_by_hour(run_execution)
    curve: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    terminal_rump_exclusions: list[dict[str, Any]] = []
    traces_by_hour: dict[int, dict[str, Any]] = {}
    target = objective["target"]

    for snapshot in snapshots:
        hour = _snapshot_hour(snapshot)
        grade_report = _tap_grade_report(run_execution, snapshot, snapshots)
        if hour < configured_hours and _target_uses_terminal_rump(target):
            operator_instruction = _operator_instruction(
                snapshot,
                tap_hour=hour,
                configured_hours=configured_hours,
                profile=profile,
            )
            exclusion = {
                "hour": hour,
                "excluded": True,
                "reason": "terminal_rump_nonterminal_best_tap",
                "grade_report": grade_report,
                "operator_instruction": operator_instruction,
            }
            curve.append(exclusion)
            terminal_rump_exclusions.append(exclusion)
            continue
        try:
            projection = _tap_pool_projection(run_execution, snapshot, snapshots)
            hour_evidence: dict[str, Any] = {}
            score = _composition_target_score_at(
                objective,
                run_execution,
                profile,
                evidence=hour_evidence,
                pool_projection=projection,
                pool_snapshot_hour=hour,
                tap_provenance=(
                    "completed_run" if hour >= configured_hours else "tap_truncated"
                ),
            )
        except ObjectiveComputationError as exc:
            curve.append(
                {
                    "hour": hour,
                    "excluded": True,
                    "reason": str(exc),
                    "grade_report": grade_report,
                }
            )
            continue
        target_trace = dict(hour_evidence.get("composition_target", {}))
        target_trace["tap_grade_report"] = grade_report
        if hour in per_hour_summary:
            cache_state = per_hour_summary[hour].get("reduced_real_cache_state")
            if cache_state is not None:
                target_trace["reduced_real_cache_state"] = str(cache_state)
        traces_by_hour[hour] = target_trace
        candidates.append(
            {
                "hour": hour,
                "score": score,
                "trace": target_trace,
                "grade_report": grade_report,
            }
        )

    if not candidates:
        if terminal_rump_exclusions:
            if evidence is not None:
                evidence["composition_target"] = _terminal_rump_nonterminal_best_tap_trace(
                    objective,
                    best_tap,
                    configured_hours=configured_hours,
                    curve=curve,
                    exclusion=terminal_rump_exclusions[0],
                )
            return 0.0
        raise ObjectiveComputationError("best_tap found no eligible tap candidates")

    for candidate in candidates:
        certified, knife_edge, dwell_hours = _tap_certification(
            candidate["hour"],
            traces_by_hour,
            best_tap=best_tap,
        )
        candidate["certified"] = certified
        candidate["knife_edge"] = knife_edge
        candidate["dwell_hours"] = dwell_hours
        curve.append(
            {
                "hour": candidate["hour"],
                "score": candidate["score"],
                "certified": certified,
                "knife_edge": knife_edge,
                "grade_report": candidate["grade_report"],
            }
        )

    winner = max(
        candidates,
        key=lambda item: (
            _finite_float(item["score"], "best_tap score"),
            bool(item["certified"]),
            -int(item["hour"]),
        ),
    )
    tap_hour = int(winner["hour"])
    nonterminal = tap_hour < configured_hours
    if nonterminal and _target_uses_captured_pool(target):
        policy = str(best_tap["captured_pool_nonterminal_policy"])
        if policy != "allow_with_note":
            raise ObjectiveComputationError(
                "best_tap selected non-terminal captured-pool candidate without "
                "captured_pool_nonterminal_policy=allow_with_note"
            )

    snapshot = _snapshot_by_hour(snapshots, tap_hour)
    operator_instruction = _operator_instruction(
        snapshot,
        tap_hour=tap_hour,
        configured_hours=configured_hours,
        profile=profile,
    )
    tap_coating_summary = (
        _tap_coating_product_summary(run_execution, snapshots, tap_hour)
        if nonterminal
        else MappingProxyType({})
    )
    winning_trace = dict(winner["trace"])
    winning_trace.update(
        {
            "best_tap_enabled": True,
            "tap_grid": _jsonable_tap_grid(best_tap["tap_grid"]),
            "tap_hour": tap_hour,
            "configured_hours": configured_hours,
            "tap_stability_hours": int(best_tap["tap_stability_hours"]),
            "operator_instruction": operator_instruction,
            "pool_snapshot_hour": tap_hour,
            "tap_provenance": "tap_truncated" if nonterminal else "completed_run",
            "knife_edge": bool(winner["knife_edge"]),
            "certified": bool(winner["certified"]),
            "tap_score_curve": sorted(curve, key=lambda item: int(item["hour"])),
            "tap_grade_report": winner["grade_report"],
            "truncated_recipe": {
                "provenance": "tap_truncated" if nonterminal else "completed_run",
                "tap_hour": tap_hour,
                "configured_hours": configured_hours,
                "operator_instruction": operator_instruction,
                "tap_grade_report": winner["grade_report"],
            },
        }
    )
    if tap_coating_summary:
        winning_trace["tap_coating_product_summary"] = tap_coating_summary
        winning_trace["truncated_recipe"]["tap_coating_product_summary"] = tap_coating_summary
    if nonterminal and _target_uses_captured_pool(target):
        note_pool = _target_captured_pool_note_id(target)
        winning_trace["nonterminal_captured_pool_note"] = str(
            best_tap.get("nonterminal_captured_pool_note")
            or (
                f"target {objective['id']} selected captured-pool tap for pool "
                f"{note_pool} at hour {tap_hour} of configured {configured_hours}"
            )
        )
    if evidence is not None:
        evidence["composition_target"] = winning_trace
    return _finite_float(winner["score"], "best_tap score")


def _tap_snapshots(
    run_execution: Any,
    best_tap: Mapping[str, Any],
) -> tuple[Any, ...]:
    snapshots = tuple(getattr(run_execution, "snapshots", ()) or ())
    if not snapshots:
        trace = getattr(run_execution, "trace", None)
        snapshots = tuple(getattr(trace, "snapshots", ()) or ())
    if not snapshots:
        raise ObjectiveComputationError("best_tap requires recorded hour snapshots")
    grid = best_tap["tap_grid"]
    if grid == "recorded_hours":
        return snapshots
    requested = {int(hour) for hour in grid}
    by_hour = {_snapshot_hour(snapshot): snapshot for snapshot in snapshots}
    missing = sorted(requested - set(by_hour))
    if missing:
        raise ObjectiveComputationError(
            f"best_tap tap_grid requested missing hours: {missing}"
        )
    return tuple(by_hour[hour] for hour in sorted(requested))


def _tap_pool_projection(
    run_execution: Any,
    snapshot: Any,
    snapshots: Sequence[Any],
) -> Mapping[str, Mapping[str, float]]:
    residual = _snapshot_melt_mol(snapshot, run_execution)
    captured_products = _cumulative_stage_species_mol(run_execution, snapshots, _snapshot_hour(snapshot))
    stage3 = {
        species: mol
        for (stage, species), mol in captured_products.items()
        if int(stage) == 3 and species in {"SiO", "SiO2"}
    }
    captured_flat: dict[str, float] = {}
    for (_stage, species), mol in captured_products.items():
        captured_flat[species] = captured_flat.get(species, 0.0) + mol
    projection: dict[str, Mapping[str, float]] = {}
    if residual:
        projection["cleaned_melt_at_stage0_exit"] = MappingProxyType(residual)
        projection["residual_rump_at_stop"] = MappingProxyType(residual)
    if captured_flat:
        projection["captured_products"] = MappingProxyType(captured_flat)
    if stage3:
        projection["captured_stage_3_silica"] = MappingProxyType(stage3)
    return MappingProxyType(projection)


def _tap_coating_product_summary(
    run_execution: Any,
    snapshots: Sequence[Any],
    hour: int,
) -> Mapping[str, Any]:
    raw_by_segment = _cumulative_wall_deposit_by_segment_species_kg(snapshots, hour)
    if not raw_by_segment:
        authority = _coating_authority_status(raw_by_segment, run_execution)
        return MappingProxyType(
            {
                "wall_deposit_kg_by_segment_species": MappingProxyType({}),
                "wall_deposit_kg_by_zone_species": MappingProxyType({}),
                "wall_deposit_remobilization_by_segment_species": MappingProxyType({}),
                "campaigns_to_resinter": "infinite",
                **_coating_authority_summary(authority),
            }
        )
    zone_by_segment = _coating_zone_by_segment(run_execution)
    if zone_by_segment is None:
        raise ObjectiveComputationError(
            "best_tap coating projection requires wall_zone_by_segment trace"
        )
    by_segment = _wall_deposit_by_segment_species_summary(raw_by_segment)
    try:
        by_zone = wall_deposit_kg_by_zone_species(raw_by_segment, zone_by_segment)
    except (TypeError, ValueError) as exc:
        raise ObjectiveComputationError(str(exc)) from exc
    sim = getattr(run_execution, "simulator", run_execution)
    remobilization = wall_deposit_remobilization_by_segment_species(
        sim,
        snapshots=snapshots,
        cumulative_deposits_kg=raw_by_segment,
        through_hour=hour,
    )
    authority = _coating_authority_status(raw_by_segment, run_execution)
    return MappingProxyType(
        {
            "wall_deposit_kg_by_segment_species": by_segment,
            "wall_deposit_kg_by_zone_species": MappingProxyType(
                {
                    zone: MappingProxyType(dict(species_kg))
                    for zone, species_kg in by_zone.items()
                }
            ),
            "wall_deposit_remobilization_by_segment_species": MappingProxyType({
                segment: MappingProxyType({
                    species: MappingProxyType(dict(fields))
                    for species, fields in species_map.items()
                })
                for segment, species_map in remobilization.items()
            }),
            "campaigns_to_resinter": _campaigns_to_resinter(raw_by_segment),
            **_coating_authority_summary(authority),
        }
    )


def _cumulative_wall_deposit_by_segment_species_kg(
    snapshots: Sequence[Any],
    hour: int,
) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for snapshot in snapshots:
        if _snapshot_hour(snapshot) > hour:
            continue
        raw = getattr(snapshot, "wall_deposit_by_segment_species_delta", None)
        if not isinstance(raw, Mapping):
            continue
        for key, kg in raw.items():
            if not isinstance(key, tuple) or len(key) != 2:
                raise ObjectiveComputationError(
                    "wall deposit key must be (segment, species)"
                )
            segment, species = str(key[0]), str(key[1])
            amount = _finite_float(kg, f"wall_deposit[{segment!r}][{species!r}]")
            if amount > _EPS:
                result[(segment, species)] = result.get((segment, species), 0.0) + amount
    return result


def _coating_zone_by_segment(run_execution: Any) -> Mapping[str, str] | None:
    trace = getattr(run_execution, "trace", None)
    raw = _carrier_value(trace, "wall_zone_by_segment")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ObjectiveComputationError("wall_zone_by_segment trace is not a mapping")
    return MappingProxyType({str(segment): str(zone) for segment, zone in raw.items()})


def _snapshot_melt_mol(snapshot: Any, run_execution: Any) -> dict[str, float]:
    source = _snapshot_melt_oxide_kg(snapshot, run_execution)
    result: dict[str, float] = {}
    for species, kg in source.items():
        mol = _kg_to_mol(str(species), _finite_float(kg, f"snapshot[{species!r}]"), run_execution)
        if mol > _EPS:
            result[str(species)] = result.get(str(species), 0.0) + mol
    return result


def _snapshot_melt_oxide_kg(snapshot: Any, run_execution: Any) -> dict[str, float]:
    inventory = getattr(snapshot, "inventory", None)
    melt_oxide_kg = getattr(inventory, "melt_oxide_kg", None)
    composition_source = _snapshot_composition_oxide_kg(snapshot)
    if isinstance(melt_oxide_kg, Mapping) and melt_oxide_kg:
        inventory_source = {
            str(species): _finite_float(kg, f"snapshot.inventory.melt_oxide_kg[{species!r}]")
            for species, kg in melt_oxide_kg.items()
            if abs(_finite_float(kg, f"snapshot.inventory.melt_oxide_kg[{species!r}]")) > _EPS
        }
        if composition_source:
            _assert_snapshot_grade_basis_equivalent(
                inventory_source,
                composition_source,
                run_execution,
            )
        return inventory_source
    return composition_source


def _snapshot_composition_oxide_kg(snapshot: Any) -> dict[str, float]:
    composition = getattr(snapshot, "composition_wt_pct", None)
    mass_kg = _finite_float(getattr(snapshot, "melt_mass_kg", 0.0), "snapshot.melt_mass_kg")
    if not isinstance(composition, Mapping) or mass_kg <= _EPS:
        return {}
    return {
        str(species): mass_kg * _finite_float(wt_pct, f"snapshot.composition_wt_pct[{species!r}]") / 100.0
        for species, wt_pct in composition.items()
        if abs(_finite_float(wt_pct, f"snapshot.composition_wt_pct[{species!r}]")) > _EPS
    }


def _assert_snapshot_grade_basis_equivalent(
    primary_kg: Mapping[str, float],
    composition_kg: Mapping[str, float],
    run_execution: Any,
) -> None:
    primary_wt = _oxide_wt_pct_from_kg(primary_kg, run_execution)
    composition_wt = _oxide_wt_pct_from_kg(composition_kg, run_execution)
    for species in sorted(set(primary_wt) | set(composition_wt)):
        primary = primary_wt.get(species, 0.0)
        composition = composition_wt.get(species, 0.0)
        # Normalized wt% bases can differ by FP normalization noise; 1e-7 relative stays <=1e-5 wt% for real components.
        if not math.isclose(
            primary,
            composition,
            rel_tol=_SNAPSHOT_GRADE_WT_REL_TOL,
            abs_tol=_SNAPSHOT_GRADE_WT_ABS_TOL,
        ):
            raise ObjectiveComputationError(
                "best_tap melt grade basis diverges from projected pool "
                f"for {species}: inventory={primary:.12g} "
                f"wt_pct composition={composition:.12g} wt_pct"
            )


def _oxide_wt_pct_from_kg(
    oxide_kg: Mapping[str, float],
    run_execution: Any,
) -> dict[str, float]:
    kg_by_species = {
        str(species): _finite_float(kg, f"snapshot oxide kg[{species!r}]")
        for species, kg in oxide_kg.items()
        if abs(_finite_float(kg, f"snapshot oxide kg[{species!r}]")) > _EPS
    }
    total_kg = sum(kg_by_species.values())
    if total_kg <= _EPS:
        return {}
    return {
        species: kg / total_kg * 100.0
        for species, kg in sorted(kg_by_species.items())
        if _kg_to_mol(species, kg, run_execution) > _EPS
    }


def _cumulative_stage_species_mol(
    run_execution: Any,
    snapshots: Sequence[Any],
    hour: int,
) -> dict[tuple[int, str], float]:
    kg_by_stage_species = _cumulative_stage_species_kg(snapshots, hour)
    result: dict[tuple[int, str], float] = {}
    for (stage, species), kg in kg_by_stage_species.items():
        mol = _kg_to_mol(species, kg, run_execution)
        if mol > _EPS:
            result[(stage, species)] = mol
    return result


def _cumulative_stage_species_kg(
    snapshots: Sequence[Any],
    hour: int,
) -> dict[tuple[int, str], float]:
    result: dict[tuple[int, str], float] = {}
    for snapshot in snapshots:
        if _snapshot_hour(snapshot) > hour:
            continue
        raw = getattr(snapshot, "condensed_by_stage_species_delta", None)
        if not isinstance(raw, Mapping):
            continue
        for key, kg in raw.items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            stage, species = int(key[0]), str(key[1])
            amount = _finite_float(kg, f"stage[{stage}][{species!r}]")
            if amount > _EPS:
                result[(stage, species)] = result.get((stage, species), 0.0) + amount
    return result


def _tap_certification(
    hour: int,
    traces_by_hour: Mapping[int, Mapping[str, Any]],
    *,
    best_tap: Mapping[str, Any],
) -> tuple[bool, bool, tuple[int, ...]]:
    stability = int(best_tap["tap_stability_hours"])
    dwell_hours = tuple(range(hour - stability + 1, hour + 1))
    instant_pass = _tap_trace_certifies(traces_by_hour.get(hour, {}))
    if not instant_pass:
        return False, False, dwell_hours
    if stability <= 1:
        return True, False, dwell_hours
    dwell_pass = all(
        _tap_trace_certifies(traces_by_hour.get(dwell_hour, {}))
        for dwell_hour in dwell_hours
    )
    return dwell_pass, not dwell_pass, dwell_hours


def _tap_trace_certifies(trace: Mapping[str, Any]) -> bool:
    if not trace or str(trace.get("certification_tier", "certified")) == "exploratory":
        return False
    rows = trace.get("rows", ())
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
        for row in rows:
            if isinstance(row, Mapping) and bool(row.get("strict", True)) and row.get("pass") is not True:
                return False
    if trace.get("window_mode") == "hard_window" and trace.get("reached_window") is not True:
        return False
    extraction = trace.get("extraction_completeness", {})
    if isinstance(extraction, Mapping):
        species = extraction.get("species", {})
        if isinstance(species, Mapping):
            for payload in species.values():
                if isinstance(payload, Mapping) and payload.get("pass") is not True:
                    return False
    return True


def _tap_grade_report(
    run_execution: Any,
    snapshot: Any,
    snapshots: Sequence[Any],
) -> Mapping[str, Any]:
    hour = _snapshot_hour(snapshot)
    report: dict[str, Any] = {"pool_snapshot_hour": hour}
    melt_grade = _melt_tap_grade(snapshot, run_execution)
    if melt_grade:
        report["melt_tap"] = melt_grade
    train_grade = _distillation_train_grade(run_execution, snapshots, hour)
    if train_grade:
        report["distillation_train_taps"] = train_grade
    return MappingProxyType(report)


def _melt_tap_grade(snapshot: Any, run_execution: Any) -> Mapping[str, Any]:
    oxide_wt_pct = _oxide_wt_pct_from_kg(
        _snapshot_melt_oxide_kg(snapshot, run_execution),
        run_execution,
    )
    if not oxide_wt_pct:
        return MappingProxyType({})
    return MappingProxyType({"oxide_wt_pct": dict(sorted(oxide_wt_pct.items()))})


def _distillation_train_grade(
    run_execution: Any,
    snapshots: Sequence[Any],
    hour: int,
) -> Mapping[str, Any]:
    stage_species_kg = _cumulative_stage_species_kg(snapshots, hour)
    by_stage: dict[int, dict[str, float]] = {}
    for (stage, species), kg in stage_species_kg.items():
        by_stage.setdefault(stage, {})[species] = by_stage.setdefault(stage, {}).get(species, 0.0) + kg
    report: dict[str, Any] = {}
    for stage, species_kg in sorted(by_stage.items()):
        total_kg = sum(species_kg.values())
        if total_kg <= _EPS:
            continue
        breakdown = {
            species: kg / total_kg * 100.0
            for species, kg in sorted(species_kg.items())
            if kg > _EPS
        }
        if not breakdown:
            continue
        dominant_species, purity = max(breakdown.items(), key=lambda item: item[1])
        species_mol = {
            species: _kg_to_mol(species, kg, run_execution)
            for species, kg in sorted(species_kg.items())
            if kg > _EPS
        }
        report[str(stage)] = {
            "dominant_species": dominant_species,
            "dominant_species_purity_pct": purity,
            "species_wt_pct": breakdown,
            "species_mol": species_mol,
            "total_kg": total_kg,
        }
    return MappingProxyType(report)


def _target_uses_captured_pool(target: Mapping[str, Any]) -> bool:
    extraction = target.get("extraction", {})
    weights = target.get("score_weights", {})
    extraction_weight = (
        float(weights.get("extraction", 0.0))
        if isinstance(weights, Mapping)
        else 0.0
    )
    if (
        extraction_weight > 0.0
        and isinstance(extraction, Mapping)
        and str(extraction.get("captured_pool", "")) in STREAM_PRODUCT_POOLS
    ):
        return True
    window = target.get("composition_window", {})
    pools = {str(target.get("pool", ""))}
    if isinstance(window, Mapping):
        pools.add(str(window.get("pool", "")))
    return bool(pools & STREAM_PRODUCT_POOLS)


def _target_uses_terminal_rump(target: Mapping[str, Any]) -> bool:
    window = target.get("composition_window", {})
    pools = {str(target.get("pool", ""))}
    if isinstance(window, Mapping):
        pools.add(str(window.get("pool", "")))
    return "terminal_rump_earned" in pools


def _target_captured_pool_note_id(target: Mapping[str, Any]) -> str:
    extraction = target.get("extraction", {})
    if isinstance(extraction, Mapping) and extraction.get("captured_pool"):
        return str(extraction["captured_pool"])
    window = target.get("composition_window", {})
    if isinstance(window, Mapping) and window.get("pool"):
        return str(window["pool"])
    return str(target.get("pool", ""))


def _terminal_rump_nonterminal_best_tap_trace(
    objective: Mapping[str, Any],
    best_tap: Mapping[str, Any],
    *,
    configured_hours: int,
    curve: Sequence[Mapping[str, Any]],
    exclusion: Mapping[str, Any],
) -> dict[str, Any]:
    target = objective["target"]
    window = target.get("composition_window", {})
    tap_hour = int(exclusion["hour"])
    operator_instruction = exclusion.get("operator_instruction")
    if not isinstance(operator_instruction, Mapping):
        operator_instruction = MappingProxyType({})
    return {
        "target_spec_id": str(objective["id"]),
        "target_spec_digest": target_spec_digest(target),
        "target_maturity": dict(target.get("maturity", {})),
        "target_provenance": _target_provenance(target),
        "rows": [],
        "resolved_composition": {"oxide_wt_pct": {}, "ratios": {}},
        "certified_envelope": [],
        "preference_score": None,
        "extraction_completeness": {},
        "certification_tier": "certified",
        "reached_window": False,
        "window_mode": str(window.get("mode", "")) if isinstance(window, Mapping) else "",
        "terminal_rump_source": "tap_truncated",
        "terminal_rump_nonterminal_reason": str(exclusion["reason"]),
        "best_tap_enabled": True,
        "tap_grid": _jsonable_tap_grid(best_tap["tap_grid"]),
        "tap_hour": tap_hour,
        "configured_hours": configured_hours,
        "tap_stability_hours": int(best_tap["tap_stability_hours"]),
        "pool_snapshot_hour": tap_hour,
        "tap_provenance": "tap_truncated",
        "operator_instruction": operator_instruction,
        "knife_edge": False,
        "certified": False,
        "tap_score_curve": sorted(
            [dict(item) for item in curve],
            key=lambda item: int(item["hour"]),
        ),
        "tap_grade_report": dict(exclusion.get("grade_report", {})),
        "truncated_recipe": {
            "provenance": "tap_truncated",
            "tap_hour": tap_hour,
            "configured_hours": configured_hours,
            "operator_instruction": operator_instruction,
            "tap_grade_report": dict(exclusion.get("grade_report", {})),
        },
    }


def _operator_instruction(
    snapshot: Any,
    *,
    tap_hour: int,
    configured_hours: int,
    profile: Mapping[str, Any],
) -> Mapping[str, Any]:
    campaign = getattr(snapshot, "campaign", "")
    phase_at_tap = getattr(campaign, "name", str(campaign))
    overhead = getattr(snapshot, "overhead", None)
    composition = getattr(overhead, "composition", {}) if overhead is not None else {}
    pO2_mbar = 0.0
    if isinstance(composition, Mapping):
        pO2_mbar = _finite_float(composition.get("O2", 0.0), "snapshot.pO2_mbar")
    run = profile.get("run", {}) if isinstance(profile.get("run"), Mapping) else {}
    payload: dict[str, Any] = {
        "tap_hour": tap_hour,
        "configured_hours": configured_hours,
        "phase_at_tap": phase_at_tap,
        "configured_campaign": str(run.get("campaign", "")),
        "T_C": _finite_float(getattr(snapshot, "temperature_C", 0.0), "snapshot.T_C"),
        "pO2_mbar": pO2_mbar,
        "provenance": "tap_truncated" if tap_hour < configured_hours else "completed_run",
    }
    if isinstance(composition, Mapping) and composition.get("N2") is not None:
        payload["pN2_mbar"] = _finite_float(composition.get("N2", 0.0), "snapshot.pN2_mbar")
    for attr, key in (
        ("pN2_mbar", "pN2_mbar"),
        ("pn2_mbar", "pN2_mbar"),
        ("sweep_setting", "sweep_setting"),
        ("sweep_mode", "sweep_mode"),
        ("carrier_gas", "carrier_gas"),
        ("recirculating_inert_sweep", "recirculating_inert_sweep"),
    ):
        if hasattr(snapshot, attr):
            payload[key] = getattr(snapshot, attr)
    if overhead is not None:
        for attr, key in (
            ("carrier_gas", "carrier_gas"),
            ("sweep_setting", "sweep_setting"),
            ("sweep_mode", "sweep_mode"),
            ("recirculating_inert_sweep", "recirculating_inert_sweep"),
        ):
            if hasattr(overhead, attr):
                payload[key] = getattr(overhead, attr)
    return MappingProxyType(payload)


def _configured_run_hours(run_execution: Any, profile: Mapping[str, Any]) -> int:
    run = profile.get("run", {}) if isinstance(profile.get("run"), Mapping) else {}
    raw = run.get("hours")
    if raw is None:
        sim = getattr(run_execution, "simulator", None)
        raw = getattr(getattr(sim, "record", None), "total_hours", None)
    if raw is None:
        snapshots = tuple(getattr(run_execution, "snapshots", ()) or ())
        raw = _snapshot_hour(snapshots[-1]) if snapshots else 0
    return _positive_runtime_int(raw, "configured run hours")


def _positive_runtime_int(value: Any, where: str) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise ObjectiveComputationError(f"{where} must be an integer") from exc
    if numeric <= 0:
        raise ObjectiveComputationError(f"{where} must be positive")
    return numeric


def _snapshot_hour(snapshot: Any) -> int:
    return _positive_runtime_int(getattr(snapshot, "hour", None), "snapshot.hour")


def _snapshot_by_hour(snapshots: Sequence[Any], hour: int) -> Any:
    for snapshot in snapshots:
        if _snapshot_hour(snapshot) == hour:
            return snapshot
    raise ObjectiveComputationError(f"best_tap missing selected hour {hour}")


def _per_hour_summary_by_hour(run_execution: Any) -> dict[int, Mapping[str, Any]]:
    result: dict[int, Mapping[str, Any]] = {}
    for entry in getattr(run_execution, "per_hour", ()) or ():
        if not isinstance(entry, Mapping):
            continue
        raw_hour = entry.get("hour")
        if raw_hour is None:
            continue
        result[_positive_runtime_int(raw_hour, "per_hour.hour")] = entry
    return result


def _jsonable_tap_grid(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


def _extraction_score(
    vector: Mapping[str, str],
    extraction: Mapping[str, Any],
    run_execution: Any,
    profile: Mapping[str, Any],
    *,
    bookkeeping_exclusions: set[str] | None = None,
    completeness_evidence: dict[str, Any] | None = None,
    pool_projection: Mapping[str, Mapping[str, float]] | None = None,
) -> float:
    scores: list[float] = []
    completeness_min = extraction["completeness_min"]
    captured_pool = str(extraction["captured_pool"])
    if completeness_evidence is not None:
        completeness_evidence["captured_pool"] = captured_pool
        completeness_evidence["basis"] = str(extraction["basis"])
        completeness_evidence["mechanisms"] = MappingProxyType(
            dict(extraction.get("mechanisms", {}))
        )
        completeness_evidence["species"] = {}
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
            pool_projection=pool_projection,
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
        species_score = _clamp(completeness / threshold, 0.0, 1.0)
        if completeness_evidence is not None:
            completeness_evidence["species"][species] = {
                "input_mol": input_mol,
                "captured_mol": captured_mol,
                "completeness": completeness,
                "threshold": threshold,
                "pass": completeness >= threshold,
                "score": species_score,
            }
        scores.append(species_score)
    if not scores:
        raise ObjectiveComputationError("composition target extraction branch has no species")
    return sum(scores) / len(scores)


def _composition_window_score(
    window: Mapping[str, Any],
    run_execution: Any,
    *,
    evidence: dict[str, Any] | None = None,
    pool_projection: Mapping[str, Mapping[str, float]] | None = None,
    pool_snapshot_hour: int | None = None,
    tap_provenance: str | None = None,
) -> float:
    pool = str(window["pool"])
    pool_provenance: dict[str, Any] = {}
    species_mol = _pool_species_mol(
        pool,
        run_execution,
        pool_provenance=pool_provenance,
        pool_projection=pool_projection,
        tap_provenance=tap_provenance,
    )
    if not species_mol:
        raise ObjectiveComputationError(f"composition target pool {pool!r} is missing or empty")
    mode = str(window["mode"])
    oxides = window["oxides"]
    ratios = window.get("ratios", ())
    row_verdicts: list[dict[str, Any]] = []
    resolved_oxides: dict[str, float] = {}
    resolved_ratios: dict[str, float] = {}
    certified_envelope: list[dict[str, Any]] = []

    def oxide_verdict(oxide: str, row: Mapping[str, Any]) -> dict[str, Any]:
        observed = _oxide_wt_pct(oxide, species_mol, run_execution)
        resolved_oxides[oxide] = observed
        return _window_row_verdict(
            row_type="oxide",
            row_id=oxide,
            pool=pool,
            basis="oxide_wt_pct",
            operand=oxide,
            row=row,
            value=observed,
        )

    def ratio_verdict(index: int, row: Mapping[str, Any]) -> dict[str, Any]:
        numerator = tuple(str(item) for item in row["numerator"])
        denominator = tuple(str(item) for item in row["denominator"])
        numerator_wt = _oxide_operand_wt_pct(
            numerator,
            species_mol,
            run_execution,
            where=f"ratio[{index}].numerator",
        )
        denominator_wt = _oxide_operand_wt_pct(
            denominator,
            species_mol,
            run_execution,
            where=f"ratio[{index}].denominator",
        )
        if denominator_wt <= _EPS:
            raise ObjectiveComputationError(
                f"composition target ratio[{index}] denominator is zero or missing"
            )
        if numerator_wt <= _EPS:
            raise ObjectiveComputationError(
                f"composition target ratio[{index}] numerator is zero or missing"
            )
        value = numerator_wt / denominator_wt
        if not math.isfinite(value):
            raise ObjectiveComputationError(f"composition target ratio[{index}] is non-finite")
        ratio_id = f"{'+'.join(numerator)}/{'+'.join(denominator)}"
        resolved_ratios[ratio_id] = value
        verdict = _window_row_verdict(
            row_type="ratio",
            row_id=ratio_id,
            pool=pool,
            basis="oxide_wt_pct_ratio",
            operand={
                "numerator": numerator,
                "denominator": denominator,
                "numerator_wt_pct": numerator_wt,
                "denominator_wt_pct": denominator_wt,
            },
            row=row,
            value=value,
        )
        return verdict

    strict_rows: list[dict[str, Any]] = []
    soft_sources: list[tuple[str, str | int, Mapping[str, Any]]] = []
    for oxide, row in oxides.items():
        if bool(row.get("strict", True)):
            strict_rows.append(oxide_verdict(str(oxide), row))
        else:
            soft_sources.append(("oxide", str(oxide), row))
    for index, row in enumerate(ratios):
        if bool(row.get("strict", True)):
            strict_rows.append(ratio_verdict(index, row))
        else:
            soft_sources.append(("ratio", index, row))

    if mode == "hard_window":
        row_verdicts.extend(strict_rows)
        certified_envelope.extend(strict_rows)
        if any(not bool(row["pass"]) for row in strict_rows):
            for row_type, row_id, row in soft_sources:
                skipped = _skipped_soft_row_verdict(row_type, row_id, pool, row)
                row_verdicts.append(skipped)
            if evidence is not None:
                evidence.update(
                    _composition_window_evidence(
                        row_verdicts=row_verdicts,
                        resolved_oxides=resolved_oxides,
                        resolved_ratios=resolved_ratios,
                        certified_envelope=certified_envelope,
                        preference_score=None,
                        reached_window=False,
                        window_mode=mode,
                        pool_source=pool_provenance.get(pool, ""),
                        pool_snapshot_hour=pool_snapshot_hour,
                    )
                )
            return 0.0
        if not soft_sources:
            preference_score = None
            score = 1.0
        else:
            weighted = 0.0
            total_weight = 0.0
            for row_type, row_id, row in soft_sources:
                verdict = (
                    oxide_verdict(str(row_id), row)
                    if row_type == "oxide"
                    else ratio_verdict(int(row_id), row)
                )
                row_verdicts.append(verdict)
                weight = _finite_float(row["weight"], f"{row_id}.weight")
                total_weight += weight
                weighted += weight * _finite_float(verdict["score"], f"{row_id}.score")
            if total_weight <= 0.0:
                raise ObjectiveComputationError("composition target window has zero soft weight")
            preference_score = weighted / total_weight
            score = preference_score
        if evidence is not None:
            evidence.update(
                _composition_window_evidence(
                    row_verdicts=row_verdicts,
                    resolved_oxides=resolved_oxides,
                    resolved_ratios=resolved_ratios,
                    certified_envelope=certified_envelope,
                    preference_score=preference_score,
                        reached_window=True,
                        window_mode=mode,
                        pool_source=pool_provenance.get(pool, ""),
                        pool_snapshot_hour=pool_snapshot_hour,
                    )
                )
        return score
    if mode == "soft_distance":
        weighted = 0.0
        total_weight = 0.0
        soft_rows: list[dict[str, Any]] = []
        for oxide, row in oxides.items():
            soft_rows.append(oxide_verdict(str(oxide), row))
        for index, row in enumerate(ratios):
            soft_rows.append(ratio_verdict(index, row))
        for verdict in soft_rows:
            weight = _finite_float(verdict["weight"], f"{verdict['id']}.weight")
            total_weight += weight
            weighted += weight * _finite_float(verdict["score"], f"{verdict['id']}.score")
        if total_weight <= 0.0:
            raise ObjectiveComputationError("composition target window has zero total weight")
        preference_score = weighted / total_weight
        if evidence is not None:
            evidence.update(
                _composition_window_evidence(
                    row_verdicts=soft_rows,
                    resolved_oxides=resolved_oxides,
                    resolved_ratios=resolved_ratios,
                    certified_envelope=[],
                    preference_score=preference_score,
                        reached_window=False,
                        window_mode=mode,
                        pool_source=pool_provenance.get(pool, ""),
                        pool_snapshot_hour=pool_snapshot_hour,
                    )
                )
        return preference_score
    raise ObjectiveComputationError(f"unsupported composition window mode {mode!r}")


def _window_row_verdict(
    *,
    row_type: str,
    row_id: str,
    pool: str,
    basis: str,
    operand: Any,
    row: Mapping[str, Any],
    value: float,
) -> dict[str, Any]:
    lower = _finite_float(row["min"], f"{row_id}.min")
    upper = _finite_float(row["max"], f"{row_id}.max")
    if upper < lower:
        raise ObjectiveComputationError(f"composition target row {row_id!r} has empty window")
    strict = bool(row.get("strict", True))
    in_band = lower <= value <= upper
    score = 1.0 if in_band else 0.0
    reason = "in_band" if in_band else "outside_band"
    if not strict:
        if upper <= lower:
            raise ObjectiveComputationError(
                f"composition target soft row {row_id!r} has zero-width band"
            )
        score = _soft_band_score(value, lower, upper)
        reason = "soft_distance_inverse_linear_to_band"
    payload: dict[str, Any] = {
        "id": row_id,
        "type": row_type,
        "pool": pool,
        "basis": basis,
        "operand": operand,
        "min": lower,
        "max": upper,
        "value": value,
        "strict": strict,
        "pass": in_band,
        "score": score,
        "reason": reason,
        "weight": _finite_float(row["weight"], f"{row_id}.weight"),
    }
    for key in ("needs_experiment", "provenance", "tier"):
        if key in row:
            payload[key] = row[key]
    return payload


def _skipped_soft_row_verdict(
    row_type: str,
    row_id: str | int,
    pool: str,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(row_id),
        "type": row_type,
        "pool": pool,
        "strict": False,
        "pass": None,
        "score": None,
        "reason": "hard_gate_failed_soft_not_computed",
        "weight": _finite_float(row["weight"], f"{row_id}.weight"),
        "min": _finite_float(row["min"], f"{row_id}.min"),
        "max": _finite_float(row["max"], f"{row_id}.max"),
    }
    for key in ("needs_experiment", "provenance", "tier"):
        if key in row:
            payload[key] = row[key]
    return payload


def _composition_window_evidence(
    *,
    row_verdicts: Sequence[Mapping[str, Any]],
    resolved_oxides: Mapping[str, float],
    resolved_ratios: Mapping[str, float],
    certified_envelope: Sequence[Mapping[str, Any]],
    preference_score: float | None,
    reached_window: bool,
    window_mode: str,
    pool_source: str,
    pool_snapshot_hour: int | None = None,
) -> dict[str, Any]:
    return {
        "rows": [dict(row) for row in row_verdicts],
        "resolved_composition": {
            "oxide_wt_pct": dict(sorted(resolved_oxides.items())),
            "ratios": dict(sorted(resolved_ratios.items())),
        },
        "certified_envelope": [dict(row) for row in certified_envelope],
        "preference_score": preference_score,
        "reached_window": reached_window,
        "window_mode": window_mode,
        **({"terminal_rump_source": pool_source} if pool_source else {}),
        **(
            {"pool_snapshot_hour": int(pool_snapshot_hour)}
            if pool_snapshot_hour is not None
            else {}
        ),
    }


def _soft_band_score(value: float, lower: float, upper: float) -> float:
    band_width = upper - lower
    if band_width <= 0.0:
        raise ObjectiveComputationError("composition target soft row has zero-width band")
    if not all(math.isfinite(raw) for raw in (value, lower, upper, band_width)):
        raise ObjectiveComputationError("composition target soft row has non-finite value")
    if lower <= value <= upper:
        normalized_distance = 0.0
    elif value < lower:
        normalized_distance = (lower - value) / band_width
    else:
        normalized_distance = (value - upper) / band_width
    return 1.0 / (1.0 + normalized_distance)


def _oxide_operand_wt_pct(
    operand: Sequence[str],
    species_mol: Mapping[str, float],
    run_execution: Any,
    *,
    where: str,
) -> float:
    total_kg = sum(
        _mol_to_kg(species, mol, run_execution)
        for species, mol in species_mol.items()
    )
    if total_kg <= _EPS:
        raise ObjectiveComputationError("composition target pool has zero mass")
    operand_kg = 0.0
    for oxide in operand:
        oxide_kg = _oxide_equivalent_kg(str(oxide), species_mol, run_execution)
        if oxide_kg <= _EPS:
            raise ObjectiveComputationError(f"composition target {where} missing {oxide!r}")
        operand_kg += oxide_kg
    value = operand_kg / total_kg * 100.0
    if not math.isfinite(value):
        raise ObjectiveComputationError(f"composition target {where} is non-finite")
    return value


def _pool_species_mol(
    pool: str,
    run_execution: Any,
    *,
    bookkeeping_exclusions: set[str] | None = None,
    pool_provenance: dict[str, Any] | None = None,
    pool_projection: Mapping[str, Mapping[str, float]] | None = None,
    tap_provenance: str | None = None,
) -> Mapping[str, float]:
    if pool_projection is not None:
        projected = pool_projection.get(pool)
        if projected is None:
            if pool != "terminal_rump_earned":
                raise ObjectiveComputationError(
                    f"composition target projected pool {pool!r} is missing"
                )
        else:
            if not projected:
                raise ObjectiveComputationError(
                    f"composition target projected pool {pool!r} is empty"
                )
            return MappingProxyType(dict(projected))
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
    if pool in ("cleaned_melt_at_stage0_exit", "residual_rump_at_stop"):
        ledger_values = _ledger_mol_by_accounts(
            sim,
            _POOL_LEDGER_ACCOUNTS[pool],
        )
        if ledger_values:
            return MappingProxyType(ledger_values)
        raise ObjectiveComputationError(f"composition target pool {pool!r} unavailable")
    if pool == "terminal_rump_earned":
        if tap_provenance == "tap_truncated":
            raise ObjectiveComputationError(
                "composition target terminal rump cannot use non-terminal best_tap"
            )
        trace = getattr(run_execution, "trace", None)
        payload = _carrier_value(trace, "rump_terminal")
        if isinstance(payload, Mapping):
            if str(payload.get("status", "")) != "earned":
                raise ObjectiveComputationError("composition target terminal rump is not earned")
            if pool_provenance is not None:
                pool_provenance[pool] = "earned_crash"
            ledger_values = _ledger_mol_by_accounts(
                sim,
                _POOL_LEDGER_ACCOUNTS[pool],
            )
            if ledger_values:
                return MappingProxyType(ledger_values)
            fallback = _terminal_rump_trace_mol(run_execution)
            if fallback:
                return MappingProxyType(fallback)
        else:
            completion_problem = _terminal_rump_completed_run_problem(run_execution)
            if completion_problem is not None:
                raise ObjectiveComputationError(completion_problem)
            ledger_values = _ledger_mol_by_accounts(
                sim,
                _POOL_LEDGER_ACCOUNTS["terminal_rump_earned"],
            )
            if not ledger_values and tap_provenance is None:
                ledger_values = _ledger_mol_by_accounts(
                    sim,
                    _POOL_LEDGER_ACCOUNTS["residual_rump_at_stop"],
                )
            if ledger_values:
                if pool_provenance is not None:
                    pool_provenance[pool] = "completed_run"
                return MappingProxyType(ledger_values)
    raise ObjectiveComputationError(f"composition target pool {pool!r} unavailable")


def _captured_target_mol(
    target_species: str,
    pool: str,
    run_execution: Any,
    *,
    bookkeeping_exclusions: set[str] | None = None,
    pool_projection: Mapping[str, Mapping[str, float]] | None = None,
) -> float:
    species_mol = _pool_species_mol(
        pool,
        run_execution,
        bookkeeping_exclusions=bookkeeping_exclusions,
        pool_projection=pool_projection,
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
    if not isinstance(feedstock, Mapping):
        raise ObjectiveComputationError("composition target feedstock composition unavailable")
    mass_kg = _run_mass_kg(run_execution, profile)
    composition = normalized_feedstock_component_masses_kg(feedstock, mass_kg)
    total = 0.0
    for species, kg_value in composition.items():
        kg = _finite_float(kg_value, f"feedstock[{species!r}]")
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


def _terminal_rump_completed_run_problem(run_execution: Any) -> str | None:
    backend_status = _latest_backend_status(run_execution)
    if backend_status == "ok":
        return None
    if backend_status is None:
        return (
            "composition target terminal rump completed_run lacks positive "
            "completion evidence"
        )
    return (
        "composition target terminal rump cannot use completed_run because "
        f"backend_status={backend_status!r}"
    )


def _latest_backend_status(run_execution: Any) -> str | None:
    sim = getattr(run_execution, "simulator", None)
    carriers = (
        run_execution,
        getattr(run_execution, "trace", None),
        getattr(sim, "_last_backend_diagnostics", None),
        getattr(sim, "_last_out_of_domain_diagnostics", None),
    )
    return _select_backend_status(
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


def _latest_backend_status_from_sequence(value: Any) -> str | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    return _select_backend_status(
        status
        for item in value
        for status in _backend_statuses_from_carrier(item)
    )


def _carrier_has_crash_point(carrier: MappingABC[Any, Any]) -> bool:
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
    if oxide_name == "FeO_total":
        return _single_oxide_equivalent_kg("FeO", species_mol, run_execution)
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
        "target_species_yield_report": target_species_yield_report(sim),
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
    if "stream_purity_min" in raw_constraints:
        threshold = base.stream_purity_min
        updates["stream_purity_min"] = ThresholdSpec(
            id=threshold.id,
            value=_finite_float(
                raw_constraints["stream_purity_min"],
                "profile.constraints.stream_purity_min",
            ),
            units=threshold.units,
            source="profile",
            source_ref="profile.constraints.stream_purity_min",
            tolerance=threshold.tolerance,
        )
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
    sim = getattr(run_execution, "simulator", run_execution)
    remobilization = wall_deposit_remobilization_by_segment_species(
        sim,
        cumulative_deposits_kg=raw_by_segment,
    )
    authority = _coating_authority_status(raw_by_segment, run_execution)
    return MappingProxyType({
        "wall_deposit_kg_by_segment_species": by_segment,
        "wall_deposit_kg_by_zone_species": MappingProxyType({
            zone: MappingProxyType(dict(species_kg))
            for zone, species_kg in by_zone.items()
        }),
        "wall_deposit_remobilization_by_segment_species": MappingProxyType({
            segment: MappingProxyType({
                species: MappingProxyType(dict(fields))
                for species, fields in species_map.items()
            })
            for segment, species_map in remobilization.items()
        }),
        "campaigns_to_resinter": _campaigns_to_resinter(raw_by_segment),
        **_coating_authority_summary(authority),
    })


def _coating_authority_status(
    wall_deposit_by_segment_species: Mapping[tuple[str, str], float],
    run_execution: Any,
) -> Mapping[str, Any]:
    trace = getattr(run_execution, "trace", None)
    trace_status: Any = {}
    if isinstance(trace, Mapping):
        trace_status = trace.get("wall_deposit_sticking_authority", {})
    elif trace is not None:
        trace_status = getattr(trace, "wall_deposit_sticking_authority", {})
    return wall_deposit_sticking_authority_status(
        wall_deposit_by_segment_species,
        trace_status if isinstance(trace_status, Mapping) else {},
    )


def _coating_authority_summary(authority: Mapping[str, Any]) -> dict[str, Any]:
    authoritative = _coating_authority_is_authoritative(authority)
    return {
        "coating_authoritative": authoritative,
        "coating_output_status": str(
            authority.get("output_status", "authoritative")
        ),
        "coating_status": "available" if authoritative else "warning",
        "coating_status_reason": (
            ""
            if authoritative
            else str(authority.get("message", "non-authoritative coating"))
        ),
        "wall_deposit_sticking_authority": _plain_payload(authority),
    }


def _coating_authority_is_authoritative(authority: Mapping[str, Any]) -> bool:
    for key in (
        "authoritative_for_coating",
        "authoritative_for_deposit_mass",
        "authoritative",
    ):
        if key in authority:
            return bool(authority[key])
    return not _authority_payload_has_deposited_species(authority)


def _authority_payload_has_deposited_species(authority: Mapping[str, Any]) -> bool:
    raw = authority.get("deposited_species")
    if isinstance(raw, str):
        return bool(raw)
    if isinstance(raw, (list, tuple)):
        return bool(raw)
    return False


def _plain_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _plain_value(value)
        for key, value in payload.items()
    }


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _plain_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_plain_value(item) for item in value)
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    return value


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
    total_wall_load_kg = sum(by_species.values())
    threshold = _wall_resinter_threshold_kg()
    if threshold is None:
        return f"resinter_threshold_kg / {total_wall_load_kg:.12g}"
    return threshold / total_wall_load_kg


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
    energy_component = _energy_component_metric(metric)
    if energy_component is not None:
        return _energy_component_value(sim, energy_component)
    energy_per_product_component = _energy_per_product_metric(metric)
    if energy_per_product_component is not None:
        product_kg = _nested_float(
            product_classes,
            ("metals_plus_O2", "class_total_kg"),
        )
        if product_kg <= 0.0:
            raise ObjectiveComputationError(
                f"{metric} denominator metals_plus_O2 class_total_kg is zero"
            )
        return _energy_component_value(sim, energy_per_product_component) / product_kg
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


def _energy_component_metric(metric: str) -> str | None:
    aliases = {
        "electrical_energy_kWh": "electrical",
        "energy_electrical_kWh": "electrical",
        "solar_thermal_energy_kWh": "solar_thermal",
        "energy_solar_thermal_kWh": "solar_thermal",
        "thermal_energy_kWh": "thermal_total",
        "energy_thermal_kWh": "thermal_total",
        "latent_energy_kWh": "latent",
        "energy_latent_kWh": "latent",
        "dissociation_energy_kWh": "dissociation",
        "energy_dissociation_kWh": "dissociation",
    }
    return aliases.get(metric)


def _energy_per_product_metric(metric: str) -> str | None:
    aliases = {
        "energy_total_per_product_kWh_per_kg": "total",
        "total_energy_per_product_kWh_per_kg": "total",
        "electrical_energy_per_product_kWh_per_kg": "electrical",
        "solar_thermal_energy_per_product_kWh_per_kg": "solar_thermal",
        "thermal_energy_per_product_kWh_per_kg": "thermal_total",
        "latent_energy_per_product_kWh_per_kg": "latent",
        "dissociation_energy_per_product_kWh_per_kg": "dissociation",
    }
    return aliases.get(metric)


def _energy_component_value(sim: Any, component: str) -> float:
    if component == "total":
        return _sim_float(sim, "energy_cumulative_kWh", "energy_total_kWh")

    breakdown = getattr(sim, "energy_cumulative_breakdown_kWh", _MISSING)
    if isinstance(breakdown, Mapping) and component in breakdown:
        return _finite_float(breakdown[component], f"energy[{component!r}]")

    tracker = getattr(sim, "_energy_tracker", None)
    if tracker is not None:
        cumulative_breakdown = getattr(tracker, "cumulative_breakdown", None)
        if callable(cumulative_breakdown):
            raw = cumulative_breakdown()
            if isinstance(raw, Mapping) and component in raw:
                return _finite_float(raw[component], f"energy[{component!r}]")

    record = getattr(sim, "record", None)
    record_attr = {
        "electrical": "energy_electrical_kWh",
        "solar_thermal": "energy_solar_thermal_kWh",
        "thermal_total": "energy_solar_thermal_kWh",
        "latent": "energy_latent_kWh",
        "dissociation": "energy_dissociation_kWh",
    }.get(component)
    if record is not None and record_attr is not None:
        return _required_attr_float(record, record_attr)
    raise ObjectiveComputationError(f"energy component {component!r} unavailable")


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
