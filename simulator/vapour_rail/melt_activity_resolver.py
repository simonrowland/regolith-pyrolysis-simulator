"""t-568 typed melt-activity resolver and behavior-neutral shadow surface.

The service is component-keyed and canonical in natural-log space.  Phase 1
adapts legacy table/Kress values into :class:`SourceReactionActivity` while the
legacy scalar remains the only behavior authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import yaml

from simulator.physical_constants import GAS_CONSTANT
from simulator.vapour_rail.activity import (
    ActivityAttempt,
    ActivityRefusalCode,
    ActivityTier,
    ActivityVerdictKind,
    SourceReactionActivity,
    StandardStateIdentity,
)


REGISTRY_SCHEMA_VERSION: Final[int] = 1
REGISTRY_RELATIVE_PATH: Final[Path] = Path("data/melt_activity_models.yaml")
CRYSTALLINE_TARGET_PHASE: Final[str] = "crystalline"
CRYSTALLINE_TARGET_CONVENTION: Final[str] = "raoultian_pure_endmember"
CRYSTALLINE_TARGET_BASIS: Final[str] = "raoultian_pure_endmember"
CRYSTALLINE_TARGET_PRESSURE_BAR: Final[float] = 1.0
PROVEN_EMPTY_COMPONENT: Final[str] = "proven_empty_component"
SHADOW_EQUALITY_ABS_TOL_LN: Final[float] = 1.0e-10
MELT_ACTIVITY_SHADOW_RECORD_LIMIT: Final[int] = 64

SHADOW_COMPARABLE: Final[str] = "comparable"
SHADOW_NOT_COMPARABLE_YET: Final[str] = "not_comparable_yet"
SHADOW_LEGACY_DEGRADED: Final[str] = "legacy_degraded"

_ENGINE_COVERED_ELEMENTS: Final[frozenset[str]] = frozenset(
    {
        "Al",
        "Ca",
        "Co",
        "Cr",
        "Fe",
        "H",
        "K",
        "Mg",
        "Mn",
        "Na",
        "Ni",
        "P",
        "Si",
        "Ti",
    }
)

_UNSUPPORTED_RESERVOIR_CODES: Final[dict[str, ActivityRefusalCode]] = {
    "CrO": ActivityRefusalCode.UNSUPPORTED_VALENCE_RESERVOIR,
    "TiO1.5": ActivityRefusalCode.UNSUPPORTED_VALENCE_RESERVOIR,
    "S2-_melt": ActivityRefusalCode.SULFUR_RESERVOIR_OWNER_MISSING,
    "SO4_melt": ActivityRefusalCode.SULFUR_RESERVOIR_OWNER_MISSING,
    "S_dissolved": ActivityRefusalCode.SULFUR_RESERVOIR_OWNER_MISSING,
    "F-_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
    "Cl-_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
    "Br-_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
    "I-_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
    "NaCl_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
    "salt_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
}


class MeltActivityRegistryError(ValueError):
    """Registry/schema content cannot support deterministic resolution."""


@dataclass(frozen=True)
class MeltReservoir:
    component_id: str
    amount_mol: float
    formula: str
    charge: str | None = None
    valence: str | None = None
    speciation: str | None = None

    def as_mapping(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "amount_mol": float(self.amount_mol),
            "formula": self.formula,
            "charge": self.charge,
            "valence": self.valence,
            "speciation": self.speciation,
        }


@dataclass(frozen=True)
class MeltActivityQuery:
    component_id: str
    formula_basis: str
    target_standard_state: StandardStateIdentity
    temperature_K: float
    pressure_bar: float
    component_mole_fractions: Mapping[str, float]
    composition_basis: str
    ordered_reservoirs: tuple[MeltReservoir, ...]
    inventory_digest: str
    inventory_complete: bool
    state_fingerprint: str
    matrix_domain_ref: str | None = None
    assemblage_ref: str | None = None
    phase_kind: str | None = None
    consumed_reservoir_ids: tuple[str, ...] = ()
    unmodeled_nonzero_reservoir_ids: tuple[str, ...] = ()
    intrinsic_fO2_log10: float | None = None
    redox_model_pressure_bar: float | None = None
    redox_basis_ref: str | None = None
    redox_model_id: str | None = None
    redox_model_digest: str | None = None
    composition_wt_pct: Mapping[str, float] = field(default_factory=dict)
    out_of_domain: bool = False
    continuation_ln_band: tuple[float, float] | None = None


@dataclass(frozen=True)
class TierAEngineInput:
    engine_component_ids: tuple[str, ...]
    basis_coefficients: tuple[float, ...]
    target_mu0_J_per_mol: float
    engine_ln_activities: tuple[float, ...] | None = None
    source_mu0_J_per_mol: tuple[float, ...] | None = None
    mixture_mu_J_per_mol: tuple[float, ...] | None = None
    source_standard_state: StandardStateIdentity | None = None
    conversion_ref: str | None = None


@dataclass(frozen=True)
class Phase0SelfCheck:
    passed: bool
    target_standard_state_family: str
    catalog_rows_checked: int
    pin_impacts_checked: int
    failures: tuple[str, ...]
    registry_digest: str

    def as_mapping(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "target_standard_state_family": self.target_standard_state_family,
            "catalog_rows_checked": self.catalog_rows_checked,
            "pin_impacts_checked": self.pin_impacts_checked,
            "failures": list(self.failures),
            "registry_digest": self.registry_digest,
        }


@dataclass(frozen=True)
class CatalogStandardStateRoles:
    producer_identity: Mapping[str, Any]
    thermodynamic_declaration: Mapping[str, Any]


def catalog_standard_state_roles(
    source_reaction: Mapping[str, Any],
) -> CatalogStandardStateRoles:
    """Return the two deliberately separate catalog standard-state roles.

    ``producer_identity`` is ``activity_input.standard_state``: an exact-match
    handshake against the activity producer's reported identity.  It does not
    declare the reference-reaction physics.  ``thermodynamic_declaration`` is
    ``source_reaction.thermodynamic_standard_state``: the physical standard
    state used by the reaction rail.  Consumers must not substitute one role
    for the other even when most catalog rows happen to make them equal.
    """

    activity_input = source_reaction.get("activity_input")
    producer_identity = (
        activity_input.get("standard_state")
        if isinstance(activity_input, Mapping)
        else None
    )
    thermodynamic_declaration = source_reaction.get(
        "thermodynamic_standard_state"
    )
    return CatalogStandardStateRoles(
        producer_identity=(
            producer_identity
            if isinstance(producer_identity, Mapping)
            else MappingProxyType({})
        ),
        thermodynamic_declaration=(
            thermodynamic_declaration
            if isinstance(thermodynamic_declaration, Mapping)
            else MappingProxyType({})
        ),
    )


@dataclass(frozen=True)
class ShadowComparison:
    component_id: str
    legacy_value: float | None
    typed_ln_value: float | None
    delta_ln: float | None
    equal: bool | None
    population: str
    comparison_status: str = SHADOW_NOT_COMPARABLE_YET
    comparison_method: str | None = None
    tolerance_ln: float | None = None
    refusal_code: ActivityRefusalCode | None = None
    fallback_reason: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.comparison_status == SHADOW_COMPARABLE:
            if self.population != "legacy_in_domain":
                raise ValueError("comparable shadow rows must be legacy_in_domain")
            if (
                self.legacy_value is None
                or not math.isfinite(self.legacy_value)
                or self.legacy_value <= 0.0
                or self.typed_ln_value is None
                or not math.isfinite(self.typed_ln_value)
                or self.delta_ln is None
                or not math.isfinite(self.delta_ln)
                or self.tolerance_ln is None
                or not math.isfinite(self.tolerance_ln)
                or self.tolerance_ln < 0.0
                or not self.comparison_method
                or not isinstance(self.equal, bool)
            ):
                raise ValueError(
                    "comparable shadow rows require finite values/delta/tolerance, "
                    "a method, and a boolean equality verdict"
                )
            expected_delta = self.typed_ln_value - math.log(self.legacy_value)
            if not math.isclose(
                self.delta_ln,
                expected_delta,
                rel_tol=1.0e-15,
                abs_tol=1.0e-15,
            ):
                raise ValueError(
                    "shadow delta_ln must be derived from typed_ln_value and "
                    "legacy_value"
                )
            expected_equal = abs(expected_delta) <= self.tolerance_ln
            if self.equal is not expected_equal:
                raise ValueError(
                    "shadow equality verdict must be derived from delta_ln/tolerance_ln"
                )
            return
        if self.comparison_status == SHADOW_NOT_COMPARABLE_YET:
            if self.population != "legacy_in_domain":
                raise ValueError(
                    "not_comparable_yet shadow rows must be legacy_in_domain"
                )
            if self.equal is not None or self.delta_ln is not None:
                raise ValueError(
                    "not_comparable_yet shadow rows cannot carry equality or delta"
                )
            return
        if self.comparison_status == SHADOW_LEGACY_DEGRADED:
            if self.population != "legacy_degraded":
                raise ValueError(
                    "legacy_degraded comparison status requires degraded population"
                )
            if (
                self.equal is not None
                or self.delta_ln is not None
                or self.typed_ln_value is not None
            ):
                raise ValueError(
                    "legacy_degraded shadow rows cannot carry typed equality values"
                )
            return
        raise ValueError(
            f"unsupported shadow comparison status {self.comparison_status!r}"
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "legacy_value": self.legacy_value,
            "typed_ln_value": self.typed_ln_value,
            "delta_ln": self.delta_ln,
            "equal": self.equal,
            "population": self.population,
            "comparison_status": self.comparison_status,
            "comparison_method": self.comparison_method,
            "tolerance_ln": self.tolerance_ln,
            "refusal_code": (
                self.refusal_code.value if self.refusal_code is not None else None
            ),
            "fallback_reason": self.fallback_reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class MeltActivityShadow:
    results_by_component: Mapping[str, SourceReactionActivity]
    comparisons: tuple[ShadowComparison, ...]
    state_fingerprint: str
    inventory_digest: str
    registry_digest: str
    status: str = "shadow_only_no_behavior_authority"
    dropped_component_count: int = field(default=0, init=False)
    dropped_comparison_count: int = field(default=0, init=False)
    comparison_summary: Mapping[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        sorted_results = sorted(self.results_by_component.items())
        all_comparisons = tuple(self.comparisons)
        legacy_in_domain = tuple(
            item
            for item in all_comparisons
            if item.population == "legacy_in_domain"
        )
        equality = tuple(
            item
            for item in legacy_in_domain
            if item.comparison_status == SHADOW_COMPARABLE
        )
        not_comparable = tuple(
            item
            for item in legacy_in_domain
            if item.comparison_status == SHADOW_NOT_COMPARABLE_YET
        )
        degraded = tuple(
            item
            for item in all_comparisons
            if item.population == "legacy_degraded"
        )
        mismatch_count = sum(item.equal is False for item in equality)
        if mismatch_count:
            gate_status = "failed_divergence"
        elif not_comparable:
            gate_status = "incomplete_not_comparable_yet"
        elif equality:
            gate_status = "passed"
        else:
            gate_status = "incomplete_no_comparable_pins"
        retained_results = sorted_results[:MELT_ACTIVITY_SHADOW_RECORD_LIMIT]
        retained_comparisons = tuple(
            all_comparisons[:MELT_ACTIVITY_SHADOW_RECORD_LIMIT]
        )
        object.__setattr__(
            self,
            "results_by_component",
            MappingProxyType(dict(retained_results)),
        )
        object.__setattr__(self, "comparisons", retained_comparisons)
        object.__setattr__(
            self,
            "dropped_component_count",
            len(sorted_results) - len(retained_results),
        )
        object.__setattr__(
            self,
            "dropped_comparison_count",
            len(all_comparisons) - len(retained_comparisons),
        )
        object.__setattr__(
            self,
            "comparison_summary",
            MappingProxyType(
                {
                    "legacy_in_domain_population_count": len(legacy_in_domain),
                    "equality_population_count": len(equality),
                    "equality_match_count": sum(
                        item.equal is True for item in equality
                    ),
                    "equality_mismatch_count": mismatch_count,
                    "not_comparable_yet_count": len(not_comparable),
                    "equality_gate_status": gate_status,
                    "degraded_population_count": len(degraded),
                    "degraded_excluded_from_equality": all(
                        item.equal is None for item in degraded
                    ),
                }
            ),
        )

    @property
    def legacy_in_domain_population(self) -> tuple[ShadowComparison, ...]:
        return tuple(
            item for item in self.comparisons if item.population == "legacy_in_domain"
        )

    @property
    def equality_population(self) -> tuple[ShadowComparison, ...]:
        return tuple(
            item
            for item in self.legacy_in_domain_population
            if item.comparison_status == SHADOW_COMPARABLE
        )

    @property
    def not_comparable_population(self) -> tuple[ShadowComparison, ...]:
        return tuple(
            item
            for item in self.legacy_in_domain_population
            if item.comparison_status == SHADOW_NOT_COMPARABLE_YET
        )

    @property
    def degraded_population(self) -> tuple[ShadowComparison, ...]:
        return tuple(
            item for item in self.comparisons if item.population == "legacy_degraded"
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "state_fingerprint": self.state_fingerprint,
            "inventory_digest": self.inventory_digest,
            "registry_digest": self.registry_digest,
            "results_by_component": {
                key: value.as_mapping()
                for key, value in sorted(self.results_by_component.items())
            },
            "comparisons": [item.as_mapping() for item in self.comparisons],
            "comparison_tolerance_ln": SHADOW_EQUALITY_ABS_TOL_LN,
            **dict(self.comparison_summary),
            "record_limit": MELT_ACTIVITY_SHADOW_RECORD_LIMIT,
            "comparisons_recorded_count": len(self.comparisons),
            "dropped_component_count": self.dropped_component_count,
            "dropped_comparison_count": self.dropped_comparison_count,
            "record_truncated": bool(
                self.dropped_component_count or self.dropped_comparison_count
            ),
        }


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("fingerprinted numeric values must be finite")
        return value
    return str(value)


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def crystalline_target_standard_state(component_id: str) -> StandardStateIdentity:
    component = str(component_id).strip()
    if not component:
        raise ValueError("component_id is required for a target standard state")
    return StandardStateIdentity(
        convention=CRYSTALLINE_TARGET_CONVENTION,
        phase=CRYSTALLINE_TARGET_PHASE,
        reference_pressure_bar=CRYSTALLINE_TARGET_PRESSURE_BAR,
        reference_temperature_K=None,
        component_basis=CRYSTALLINE_TARGET_BASIS,
        identity_id=(
            f"rail.pure_oxide.{component}.raoultian.crystalline_at_T.1bar.v1"
        ),
        component_id=component,
    )


def supercooled_liquid_target_standard_state(
    component_id: str,
) -> StandardStateIdentity:
    component = str(component_id).strip()
    if not component:
        raise ValueError("component_id is required for a target standard state")
    return StandardStateIdentity(
        convention="raoultian_pure_endmember",
        phase="liquid",
        reference_pressure_bar=1.0,
        reference_temperature_K=None,
        component_basis="raoultian_pure_endmember",
        identity_id=(
            f"rail.pure_oxide.{component}.raoultian."
            "supercooled_liquid_at_T.1bar.v1"
        ),
        component_id=component,
    )


def complete_inventory_identity(
    ledger_snapshot: Mapping[str, Any],
    *,
    temperature_K: float | None,
    pressure_bar: float | None,
    intrinsic_fO2_log10: float | None,
    redox_model_pressure_bar: float | None = None,
    redox_basis_ref: str | None = None,
    redox_model_id: str | None = None,
    redox_model_digest: str | None = None,
    composition_wt_pct: Mapping[str, float] | None = None,
) -> tuple[str, str, tuple[MeltReservoir, ...], bool]:
    """Hash the complete ordered cleaned-melt reservoir account."""

    reservoirs: list[MeltReservoir] = []
    canonical_accounts: dict[str, dict[str, Any]] = {}
    inventory_complete = True
    account_id = "process.cleaned_melt"
    raw_account = ledger_snapshot.get(account_id)
    if not isinstance(raw_account, Mapping):
        inventory_complete = False
        raw_account = {}
        canonical_accounts[account_id] = {
            "__invalid_account__": type(ledger_snapshot.get(account_id)).__name__
        }
    else:
        account: dict[str, Any] = {}
        for species_id, raw_amount in sorted(raw_account.items()):
            try:
                amount = float(raw_amount)
            except (TypeError, ValueError):
                inventory_complete = False
                account[str(species_id)] = {
                    "status": "invalid_non_numeric_amount"
                }
                continue
            if not math.isfinite(amount):
                inventory_complete = False
                account[str(species_id)] = {
                    "status": "invalid_non_finite_amount"
                }
                continue
            if amount < 0.0:
                inventory_complete = False
                account[str(species_id)] = {
                    "status": "invalid_negative_amount"
                }
                continue
            species = str(species_id)
            account[species] = amount
            reservoirs.append(
                MeltReservoir(
                    component_id=f"{account_id}:{species}",
                    amount_mol=amount,
                    formula=species,
                    speciation=str(account_id),
                )
            )
        canonical_accounts[account_id] = account
    inventory_digest = _stable_digest(canonical_accounts)
    state_fingerprint = _stable_digest(
        {
            "temperature_K": temperature_K,
            "pressure_bar": pressure_bar,
            "intrinsic_fO2_log10": intrinsic_fO2_log10,
            "redox_model_pressure_bar": redox_model_pressure_bar,
            "redox_basis_ref": redox_basis_ref,
            "redox_model_id": redox_model_id,
            "redox_model_digest": redox_model_digest,
            "composition_wt_pct": dict(composition_wt_pct or {}),
            "inventory_digest": inventory_digest,
            "inventory_complete": inventory_complete,
        }
    )
    return state_fingerprint, inventory_digest, tuple(reservoirs), inventory_complete


def ownerless_nonzero_reservoir_ids(
    reservoirs: Sequence[MeltReservoir],
) -> tuple[str, ...]:
    """Ownerless reduced-valence, S, and halide reservoirs in melt state."""

    reduced_valence_aliases = {
        "CrO",
        "CrO_melt",
        "Cr2+",
        "Cr(II)",
        "TiO1.5",
        "TiO1.5_melt",
        "Ti3+",
        "Ti(III)",
    }
    ownerless_elements = {"S", "F", "Cl", "Br", "I"}

    def has_no_owner(reservoir: MeltReservoir) -> bool:
        formula = reservoir.formula
        tokens = set(re.findall(r"[A-Z][a-z]?", formula))
        lowered = formula.lower()
        return (
            formula in reduced_valence_aliases
            or bool(tokens & ownerless_elements)
            or any(
                label in lowered
                for label in (
                    "sulfide",
                    "sulfate",
                    "dissolved_s",
                    "fluoride",
                    "chloride",
                    "bromide",
                    "iodide",
                    "salt",
                )
            )
        )

    return tuple(
        reservoir.component_id
        for reservoir in reservoirs
        if reservoir.amount_mol != 0.0 and has_no_owner(reservoir)
    )


class MeltActivityRegistry:
    """Validated file-backed activity model/inventory registry."""

    def __init__(self, payload: Mapping[str, Any], *, source_path: Path) -> None:
        self.source_path = source_path
        self.payload = MappingProxyType(dict(payload))
        self.digest = _stable_digest(payload)
        self._validate()
        self.rows_by_id = MappingProxyType(
            {
                str(row["row_id"]): MappingProxyType(dict(row))
                for row in self.payload.get("model_rows", ())
            }
        )
        self.row_digests = MappingProxyType(
            {
                str(row["row_id"]): _stable_digest(row)
                for row in self.payload.get("model_rows", ())
            }
        )
        self.tier_c_rows_by_component = MappingProxyType(
            {
                str(row["component_id"]): MappingProxyType(dict(row))
                for row in self.payload.get("tier_c_inventory", ())
                if row.get("component_id")
            }
        )
        self.tier_c_row_digests = MappingProxyType(
            {
                str(row["row_id"]): _stable_digest(row)
                for row in self.payload.get("tier_c_inventory", ())
            }
        )

    @classmethod
    @lru_cache(maxsize=8)
    def load(cls, path: str | Path | None = None) -> "MeltActivityRegistry":
        source_path = (
            Path(path)
            if path is not None
            else Path(__file__).resolve().parents[2] / REGISTRY_RELATIVE_PATH
        )
        payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise MeltActivityRegistryError("registry root must be a mapping")
        return cls(payload, source_path=source_path)

    def _validate(self) -> None:
        def finite_interval(
            value: Any, *, minimum: float | None = None
        ) -> bool:
            if (
                not isinstance(value, Sequence)
                or isinstance(value, (str, bytes))
                or len(value) != 2
            ):
                return False
            try:
                lower, upper = (float(item) for item in value)
            except (TypeError, ValueError):
                return False
            return (
                math.isfinite(lower)
                and math.isfinite(upper)
                and lower <= upper
                and (minimum is None or lower >= minimum)
            )

        if int(self.payload.get("schema_version", -1)) != REGISTRY_SCHEMA_VERSION:
            raise MeltActivityRegistryError("unsupported melt-activity schema_version")
        if self.payload.get("kind") != "melt_activity_models":
            raise MeltActivityRegistryError("registry kind must be melt_activity_models")
        target = self.payload.get("target_standard_state")
        if not isinstance(target, Mapping):
            raise MeltActivityRegistryError("target_standard_state is required")
        expected_target = {
            "selection_policy": "per_row_exact",
            "convention": "raoultian_pure_endmember",
            "phase": "liquid",
            "reference_pressure_bar": 1.0,
            "component_basis": "raoultian_pure_endmember",
        }
        for key, expected in expected_target.items():
            if target.get(key) != expected:
                raise MeltActivityRegistryError(
                    f"target_standard_state.{key} must be {expected!r}"
                )
        template = str(target.get("id_template") or "")
        if "{component_id}" not in template:
            raise MeltActivityRegistryError(
                "target standard-state ID must be component-qualified"
            )
        if tuple(target.get("b154_crystalline_overrides") or ()) != (
            "CaO",
            "MgO",
        ):
            raise MeltActivityRegistryError(
                "b-154 crystalline target overrides must be exactly CaO and MgO"
            )
        crystalline_template = str(
            target.get("b154_crystalline_id_template") or ""
        )
        if crystalline_template != (
            "rail.pure_oxide.{component_id}.raoultian."
            "crystalline_at_T.1bar.v1"
        ):
            raise MeltActivityRegistryError(
                "b-154 crystalline target template is invalid"
            )

        rows = self.payload.get("model_rows")
        if not isinstance(rows, Sequence):
            raise MeltActivityRegistryError("model_rows must be a sequence")
        row_ids: set[str] = set()
        selection_keys: set[tuple[str, str, str]] = set()
        required_blocks = {
            "tier",
            "model_family",
            "status",
            "rail_component",
            "source",
            "source_standard_state",
            "target_standard_state",
            "conversion",
            "domain",
            "band",
            "provenance",
            "validation",
        }
        for row in rows:
            if not isinstance(row, Mapping):
                raise MeltActivityRegistryError("every model row must be a mapping")
            missing = sorted(required_blocks - set(row))
            if missing:
                raise MeltActivityRegistryError(
                    f"{row.get('row_id')}: missing required blocks {missing}"
                )
            row_id = str(row.get("row_id") or "")
            if not row_id or row_id in row_ids:
                raise MeltActivityRegistryError(f"duplicate/empty row_id {row_id!r}")
            row_ids.add(row_id)
            component = str((row.get("rail_component") or {}).get("id") or "")
            target_id = str((row.get("target_standard_state") or {}).get("id") or "")
            tier = str(row.get("tier") or "")
            if tier not in {item.value for item in ActivityTier}:
                raise MeltActivityRegistryError(f"{row_id}: invalid tier {tier!r}")
            if component not in target_id:
                raise MeltActivityRegistryError(
                    f"{row_id}: target standard-state ID is not component-qualified"
                )
            rail_component = row.get("rail_component")
            if not isinstance(rail_component, Mapping) or not all(
                rail_component.get(key) is not None
                for key in ("id", "parent_oxide", "formula_multiplier")
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: incomplete rail-component identity"
                )
            try:
                formula_multiplier = float(rail_component["formula_multiplier"])
            except (TypeError, ValueError):
                formula_multiplier = math.nan
            if not math.isfinite(formula_multiplier) or formula_multiplier <= 0.0:
                raise MeltActivityRegistryError(
                    f"{row_id}: formula multiplier must be finite and positive"
                )
            if not str(row.get("model_family") or "") or not str(
                row.get("status") or ""
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: model_family and status are required"
                )
            target_state = row.get("target_standard_state") or {}
            resolution_status = str(
                target_state.get("resolution_status") or ""
            )
            if not resolution_status:
                raise MeltActivityRegistryError(
                    f"{row_id}: target resolution_status is required"
                )
            row_target = self.row_standard_state(row, "target_standard_state")
            if (
                not target_id
                or row_target.component_id != component
                or row_target.reference_pressure_bar != 1.0
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: target must be exact, component-qualified, and at 1 bar"
                )
            if component in {"CaO", "MgO"}:
                expected_crystalline = crystalline_target_standard_state(component)
                if (
                    resolution_status != "adjudicated_b154"
                    or row_target != expected_crystalline
                ):
                    raise MeltActivityRegistryError(
                        f"{row_id}: CaO/MgO target must be the b-154 crystalline state"
                    )
            elif row_target != supercooled_liquid_target_standard_state(component):
                raise MeltActivityRegistryError(
                    f"{row_id}: non-b-154 target must be the exact liquid candidate"
                )
            if component in {"AlO1.5", "TiO2", "CrO1.5", "MnO"}:
                if resolution_status != "standard_state_unresolved":
                    raise MeltActivityRegistryError(
                        f"{row_id}: t-570 target sidecar remains unresolved"
                    )
            source = row.get("source") or {}
            engine_ids = tuple(source.get("engine_component_ids") or ())
            coefficients = tuple(source.get("basis_coefficients") or ())
            if not engine_ids or len(engine_ids) != len(coefficients):
                raise MeltActivityRegistryError(
                    f"{row_id}: engine component IDs/basis coefficients must align"
                )
            try:
                numeric_coefficients = tuple(float(value) for value in coefficients)
            except (TypeError, ValueError):
                numeric_coefficients = ()
            if (
                len(numeric_coefficients) != len(coefficients)
                or not all(
                    math.isfinite(value) and value != 0.0
                    for value in numeric_coefficients
                )
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: basis coefficients must be finite nonzero numbers"
                )
            if not all(
                key in source
                for key in (
                    "provider",
                    "engine_version",
                    "database_id",
                    "evidence_refs",
                )
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: incomplete engine/provider identity"
                )
            source_state = row.get("source_standard_state") or {}
            if not all(
                source_state.get(key) is not None
                for key in ("id", "phase", "reference_pressure_bar")
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: incomplete source standard state"
                )
            try:
                source_pressure_bar = float(
                    source_state.get("reference_pressure_bar")
                )
            except (TypeError, ValueError):
                source_pressure_bar = math.nan
            if not math.isfinite(source_pressure_bar) or source_pressure_bar <= 0.0:
                raise MeltActivityRegistryError(
                    f"{row_id}: source standard-state pressure must be positive"
                )
            conversion = row.get("conversion") or {}
            if not all(
                conversion.get(key) is not None
                for key in (
                    "mu0_source_ref",
                    "mu0_target_ref",
                    "formula_balance_receipt",
                    "pressure_correction_model",
                )
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: incomplete standard-state conversion receipt"
                )
            domain = row.get("domain") or {}
            if (
                not all(
                    domain.get(key) is not None
                    for key in (
                        "T_K",
                        "P_bar",
                        "matrix_domain_ref",
                        "redox_basis",
                        "phase_requirement",
                        "full_melt_inventory_required",
                        "unmodeled_reservoir_policy",
                    )
                )
                or domain.get("full_melt_inventory_required") is not True
                or domain.get("unmodeled_reservoir_policy") != "refuse_if_nonzero"
                or domain.get("matrix_domain_ref")
                not in (self.payload.get("matrix_domains") or {})
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: incomplete or non-fail-closed domain"
                )
            if not finite_interval(domain.get("T_K"), minimum=0.0) or not finite_interval(
                domain.get("P_bar"), minimum=0.0
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: T/P domains must be finite ordered numeric intervals"
                )
            band = row.get("band") or {}
            if not all(
                key in band
                for key in (
                    "space",
                    "lower_offset",
                    "upper_offset",
                    "marginal_sigma_ln",
                    "independent_sigma_ln",
                    "correlation_loadings",
                    "correlation_basis_ref",
                    "coverage",
                    "kind",
                )
            ) or band.get("space") != "ln_activity":
                raise MeltActivityRegistryError(
                    f"{row_id}: incomplete or non-ln activity band"
                )
            lower_offset = band.get("lower_offset")
            upper_offset = band.get("upper_offset")
            if (lower_offset is None) != (upper_offset is None):
                raise MeltActivityRegistryError(
                    f"{row_id}: band offsets must both be finite or both be null"
                )
            if lower_offset is not None:
                try:
                    lower_ln = float(lower_offset)
                    upper_ln = float(upper_offset)
                except (TypeError, ValueError):
                    lower_ln = math.nan
                    upper_ln = math.nan
                if not (
                    math.isfinite(lower_ln)
                    and math.isfinite(upper_ln)
                    and lower_ln <= 0.0 <= upper_ln
                ):
                    raise MeltActivityRegistryError(
                        f"{row_id}: band offsets must satisfy lower <= 0 <= upper"
                    )
            for sigma_key in ("marginal_sigma_ln", "independent_sigma_ln"):
                sigma = band.get(sigma_key)
                if sigma is None:
                    continue
                try:
                    numeric_sigma = float(sigma)
                except (TypeError, ValueError):
                    numeric_sigma = math.nan
                if not math.isfinite(numeric_sigma) or numeric_sigma < 0.0:
                    raise MeltActivityRegistryError(
                        f"{row_id}: {sigma_key} must be finite and non-negative"
                    )
            coverage = band.get("coverage")
            if coverage is not None:
                try:
                    numeric_coverage = float(coverage)
                except (TypeError, ValueError):
                    numeric_coverage = math.nan
                if not math.isfinite(numeric_coverage) or not 0.0 < numeric_coverage <= 1.0:
                    raise MeltActivityRegistryError(
                        f"{row_id}: band coverage must be in (0, 1]"
                    )
            loadings = band.get("correlation_loadings")
            if not isinstance(loadings, Sequence) or isinstance(loadings, (str, bytes)):
                raise MeltActivityRegistryError(
                    f"{row_id}: correlation_loadings must be a sequence"
                )
            if loadings and band.get("correlation_basis_ref") is None:
                raise MeltActivityRegistryError(
                    f"{row_id}: correlated band needs correlation_basis_ref"
                )
            provenance = row.get("provenance") or {}
            if not all(
                key in provenance
                for key in ("review_status", "extract_loci", "source_digest")
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: incomplete provenance receipt"
                )
            validation = row.get("validation") or {}
            if not all(
                key in validation
                for key in (
                    "calibration_family_ids",
                    "holdout_ids",
                    "residual_metric",
                    "result",
                    "certification_ceiling",
                )
            ):
                raise MeltActivityRegistryError(
                    f"{row_id}: incomplete validation receipt"
                )
            key = (component, target_id, tier)
            if key in selection_keys:
                raise MeltActivityRegistryError(
                    f"equal-priority activity-row ambiguity for {key!r}"
                )
            selection_keys.add(key)

        tier_b_rows = [row for row in rows if row.get("tier") == "B"]
        if tier_b_rows:
            raise MeltActivityRegistryError(
                "Phase 1 admits Tier B schema/admission checks but no coefficient rows"
            )

        inventory = self.payload.get("tier_c_inventory")
        if not isinstance(inventory, Sequence):
            raise MeltActivityRegistryError("tier_c_inventory must be a sequence")
        elements = [str(row.get("element") or "") for row in inventory]
        if len(elements) != 55 or len(set(elements)) != 55:
            raise MeltActivityRegistryError(
                "tier_c_inventory must contain exactly 55 unique elements"
            )
        invalid_covered = sorted(set(elements) & _ENGINE_COVERED_ELEMENTS)
        if invalid_covered:
            raise MeltActivityRegistryError(
                f"engine-covered elements cannot be Tier C inventory: {invalid_covered}"
            )
        tier_c_row_ids: set[str] = set()
        tier_c_components: set[str] = set()
        for row in inventory:
            missing = {
                "row_id",
                "element",
                "component_id",
                "formula_basis",
                "target_standard_state_id",
                "matrix_domain_ref",
                "disposition",
            } - set(row)
            if missing:
                raise MeltActivityRegistryError(
                    f"Tier C {row.get('element')}: missing identity fields {sorted(missing)}"
                )
            tier_c_row_id = str(row.get("row_id") or "")
            if not tier_c_row_id or tier_c_row_id in tier_c_row_ids:
                raise MeltActivityRegistryError(
                    f"duplicate/empty Tier C row_id {tier_c_row_id!r}"
                )
            tier_c_row_ids.add(tier_c_row_id)
            tier_c_component = str(row.get("component_id") or "")
            if tier_c_component:
                if tier_c_component in tier_c_components:
                    raise MeltActivityRegistryError(
                        f"duplicate Tier C component {tier_c_component!r}"
                    )
                tier_c_components.add(tier_c_component)
            if row.get("disposition") not in {"ideal_when_identity_supplied", "refuse"}:
                raise MeltActivityRegistryError(
                    f"Tier C {row.get('element')}: invalid disposition"
                )
            if row.get("disposition") == "refuse" and not row.get("refusal_code"):
                raise MeltActivityRegistryError(
                    f"Tier C {row.get('element')}: refusal_code required"
                )
            if row.get("refusal_code") is not None:
                try:
                    ActivityRefusalCode(str(row["refusal_code"]))
                except ValueError as exc:
                    raise MeltActivityRegistryError(
                        f"Tier C {row.get('element')}: invalid refusal_code"
                    ) from exc
            if row.get("disposition") == "ideal_when_identity_supplied" and not all(
                row.get(key)
                for key in (
                    "component_id",
                    "formula_basis",
                    "target_standard_state_id",
                    "matrix_domain_ref",
                )
            ):
                raise MeltActivityRegistryError(
                    f"Tier C {row.get('element')}: ideal row needs exact chemical identity"
                )
            if row.get("disposition") == "ideal_when_identity_supplied":
                component_id = str(row["component_id"])
                expected_target_id = supercooled_liquid_target_standard_state(
                    component_id
                ).identity_id
                if row.get("target_standard_state_id") != expected_target_id:
                    raise MeltActivityRegistryError(
                        f"Tier C {row.get('element')}: target standard state mismatch"
                    )

        expected_unsupported = {
            key: value.value for key, value in _UNSUPPORTED_RESERVOIR_CODES.items()
        }
        configured_unsupported = {
            str(row.get("component_id")): str(row.get("refusal_code"))
            for row in self.payload.get("unsupported_reservoirs", ())
            if isinstance(row, Mapping)
        }
        if configured_unsupported != expected_unsupported:
            raise MeltActivityRegistryError(
                "unsupported reservoir inventory must be exact and owner-specific"
            )

    def target_standard_state(self, component_id: str) -> StandardStateIdentity:
        row = self.row_for_component(component_id, tier=ActivityTier.A)
        if row is not None:
            return self.row_standard_state(row, "target_standard_state")
        return supercooled_liquid_target_standard_state(component_id)

    def row_for_component(
        self, component_id: str, *, tier: ActivityTier
    ) -> Mapping[str, Any] | None:
        matches = [
            row
            for row in self.rows_by_id.values()
            if (row.get("rail_component") or {}).get("id") == component_id
            and row.get("tier") == tier.value
        ]
        if len(matches) > 1:
            raise MeltActivityRegistryError(
                f"ambiguous {tier.value} row for {component_id!r}"
            )
        return matches[0] if matches else None

    def row_digest(self, row_id: str) -> str:
        digest = self.row_digests.get(row_id) or self.tier_c_row_digests.get(row_id)
        if digest is None:
            raise MeltActivityRegistryError(f"unknown activity row {row_id!r}")
        return digest

    def tier_c_row_for_component(
        self, component_id: str
    ) -> Mapping[str, Any] | None:
        return self.tier_c_rows_by_component.get(component_id)

    def row_standard_state(
        self, row: Mapping[str, Any], block_name: str
    ) -> StandardStateIdentity:
        block = row.get(block_name)
        if not isinstance(block, Mapping):
            raise MeltActivityRegistryError(
                f"{row.get('row_id')}: {block_name} must be a mapping"
            )
        component_id = str((row.get("rail_component") or {}).get("id") or "")
        return StandardStateIdentity(
            convention=str(block.get("convention") or "registry_exact"),
            phase=str(block.get("phase") or ""),
            reference_pressure_bar=float(block.get("reference_pressure_bar")),
            reference_temperature_K=(
                float(block["reference_temperature_K"])
                if block.get("reference_temperature_K") is not None
                else None
            ),
            component_basis=str(block.get("component_basis") or "provider_native"),
            identity_id=str(block.get("id") or ""),
            component_id=component_id,
        )

    def phase0_self_check(
        self, vapor_pressure_path: str | Path | None = None
    ) -> Phase0SelfCheck:
        path = (
            Path(vapor_pressure_path)
            if vapor_pressure_path is not None
            else self.source_path.parent / "vapor_pressures.yaml"
        )
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        failures: list[str] = []
        checked: list[tuple[str, CatalogStandardStateRoles]] = []

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                activity_input = value.get("activity_input")
                if isinstance(activity_input, Mapping):
                    component = str(activity_input.get("component_id") or "")
                    if component in {"CaO", "MgO"}:
                        checked.append(
                            (component, catalog_standard_state_roles(value))
                        )
                for child in value.values():
                    visit(child)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                for child in value:
                    visit(child)

        visit(payload)
        if len(checked) != 6:
            failures.append(f"expected 6 Ca/Mg declarations, found {len(checked)}")
        for component, state_roles in checked:
            standard = state_roles.thermodynamic_declaration
            expected = self.target_standard_state(component)
            actual_tuple = (
                standard.get("convention"),
                standard.get("phase"),
                standard.get("reference_pressure_bar"),
                standard.get("component_basis"),
            )
            expected_tuple = (
                expected.convention,
                expected.phase,
                expected.reference_pressure_bar,
                expected.component_basis,
            )
            if actual_tuple != expected_tuple:
                failures.append(
                    f"{component} thermodynamic declaration {actual_tuple!r} "
                    f"!= {expected_tuple!r}"
                )
        impacts = self.payload.get("phase0_pin_impact", ())
        if len(impacts) != 6:
            failures.append(f"expected 6 b-154 pin impacts, found {len(impacts)}")
        for impact in impacts:
            if impact.get("disposition") != "crystalline_coherent":
                failures.append(
                    f"{impact.get('species_id')}: non-coherent pin disposition"
                )
        return Phase0SelfCheck(
            passed=not failures,
            target_standard_state_family=str(
                (self.payload.get("target_standard_state") or {}).get(
                    "b154_crystalline_id_template"
                )
                or ""
            ),
            catalog_rows_checked=len(checked),
            pin_impacts_checked=len(impacts),
            failures=tuple(failures),
            registry_digest=self.digest,
        )


def validate_tier_b_candidate(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Phase-1 Tier B admission skeleton; no coefficient rows are shipped."""

    errors: list[str] = []

    def nonempty_sequence(value: Any) -> bool:
        return (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes))
            and len(value) > 0
        )

    required = {
        "row_id",
        "rail_component",
        "primary_source",
        "matrix_composition",
        "domain",
        "published_convention",
        "coefficient_identification",
        "apparatus",
        "uncertainty",
        "canonical_conversion",
        "validation",
        "interaction_basis",
        "interaction_terms",
        "descriptor_model",
    }
    missing = sorted(required - set(row))
    if missing:
        errors.append(f"missing required Tier B fields: {missing}")
    source = row.get("primary_source")
    if (
        not isinstance(source, Mapping)
        or source.get("review_status") != "reviewed"
        or not all(source.get(key) for key in ("citation", "extract_locator", "digest"))
    ):
        errors.append("primary source must be reviewed")
    component = row.get("rail_component")
    if not isinstance(component, Mapping) or not all(
        component.get(key) is not None
        for key in ("id", "oxidation_state", "formula_multiplier")
    ):
        errors.append("rail component identity/oxidation/formula multiplier required")
    matrix = row.get("matrix_composition")
    matrix_components: tuple[str, ...] = ()
    matrix_values: tuple[float, ...] = ()
    if not isinstance(matrix, Mapping) or not all(
        matrix.get(key) for key in ("basis", "components", "values", "digest")
    ):
        errors.append("complete matrix composition and digest required")
    else:
        raw_components = matrix.get("components")
        raw_values = matrix.get("values")
        try:
            matrix_components = tuple(str(value) for value in raw_components)
            matrix_values = tuple(float(value) for value in raw_values)
        except (TypeError, ValueError):
            matrix_components = ()
            matrix_values = ()
        if (
            not matrix_components
            or len(matrix_components) != len(matrix_values)
            or len(set(matrix_components)) != len(matrix_components)
            or not all(math.isfinite(value) and value >= 0.0 for value in matrix_values)
            or not math.isclose(
                sum(matrix_values), 1.0, rel_tol=0.0, abs_tol=1.0e-9
            )
        ):
            errors.append("matrix components/values must be unique, aligned, and normalized")
    domain = row.get("domain")
    if not isinstance(domain, Mapping) or not all(
        domain.get(key) is not None
        for key in (
            "T_K",
            "P_bar",
            "fO2_log10",
            "redox_basis",
            "phase_requirement",
            "concentration_range",
            "descriptor_hull_ref",
        )
    ):
        errors.append("complete T/P/fO2/redox/phase/concentration domain required")
    convention = row.get("published_convention")
    if not isinstance(convention, Mapping) or not all(
        convention.get(key) is not None
        for key in (
            "standard_state_id",
            "log_base",
            "concentration_scale",
            "coefficient_convention",
        )
    ):
        errors.append("published standard state/log/concentration convention required")
    identification = row.get("coefficient_identification")
    if not isinstance(identification, Mapping) or not all(
        identification.get(key)
        for key in ("kind", "source_series_ids", "estimator_receipt")
    ):
        errors.append("coefficient identifiability receipt required")
    apparatus = row.get("apparatus")
    if not isinstance(apparatus, Mapping) or not all(
        apparatus.get(key) for key in ("cell_material", "method")
    ):
        errors.append("apparatus cell material and volatility method required")
    uncertainty = row.get("uncertainty")
    if not isinstance(uncertainty, Mapping) or not all(
        uncertainty.get(key) is not None
        for key in ("marginal_sigma_ln", "covariance_ref", "shared_error_groups")
    ):
        errors.append("marginal/shared uncertainty and covariance required")
    conversion = row.get("canonical_conversion")
    if not isinstance(conversion, Mapping) or not all(
        conversion.get(key) for key in ("target_standard_state_id", "receipt_digest")
    ):
        errors.append("canonical standard-state conversion receipt required")
    validation = row.get("validation")
    if not isinstance(validation, Mapping) or not all(
        validation.get(key) is not None
        for key in (
            "calibration_family_ids",
            "holdout_ids",
            "matrix_family_ids",
            "independent_solute_class_count",
            "fitted_scalar_dof",
            "design_matrix_digest",
            "residual_band_ln",
            "certification_ceiling",
            "publication_holdout",
            "solute_class_holdout",
        )
    ):
        errors.append("frozen design/holdouts/residual band/ceiling required")
    else:
        raw_calibration_families = validation.get("calibration_family_ids")
        raw_holdout_ids = validation.get("holdout_ids")
        raw_matrix_families = validation.get("matrix_family_ids")
        calibration_families = tuple(raw_calibration_families or ())
        matrix_families = tuple(raw_matrix_families or ())
        if (
            not nonempty_sequence(raw_calibration_families)
            or len(set(calibration_families)) < 3
            or not nonempty_sequence(raw_holdout_ids)
        ):
            errors.append(
                "at least three publication families and independent holdouts are required"
            )
        if (
            not nonempty_sequence(raw_matrix_families)
            or len(set(matrix_families)) < 2
        ):
            errors.append("at least two matrix families are required")
        publication_holdout = validation.get("publication_holdout")
        if not isinstance(publication_holdout, Mapping) or not all(
            publication_holdout.get(key) is not None
            for key in (
                "holdout_family_ids",
                "p95_abs_delta_ln_activity",
                "passed",
            )
        ):
            errors.append("publication-family holdout metrics are required")
        else:
            try:
                publication_p95 = float(
                    publication_holdout["p95_abs_delta_ln_activity"]
                )
            except (TypeError, ValueError):
                publication_p95 = math.inf
            if (
                not nonempty_sequence(publication_holdout.get("holdout_family_ids"))
                or publication_holdout.get("passed") is not True
                or not math.isfinite(publication_p95)
                or publication_p95 > math.log(10.0)
            ):
                errors.append("publication-family holdout gate is not satisfied")
        solute_holdout = validation.get("solute_class_holdout")
        solute_metric_keys = (
            "holdout_class_ids",
            "pooled_p95_abs_delta_ln_activity",
            "abs_median_signed_delta_ln_activity",
            "empirical_95_band_coverage",
            "max_class_median_abs_delta_ln_activity",
            "passed",
        )
        if not isinstance(solute_holdout, Mapping) or not all(
            solute_holdout.get(key) is not None for key in solute_metric_keys
        ):
            errors.append("solute-class holdout metrics are required")
        else:
            try:
                solute_p95 = float(
                    solute_holdout["pooled_p95_abs_delta_ln_activity"]
                )
                signed_bias = float(
                    solute_holdout["abs_median_signed_delta_ln_activity"]
                )
                coverage = float(solute_holdout["empirical_95_band_coverage"])
                worst_class = float(
                    solute_holdout["max_class_median_abs_delta_ln_activity"]
                )
            except (TypeError, ValueError):
                solute_p95 = signed_bias = worst_class = math.inf
                coverage = -math.inf
            if (
                not nonempty_sequence(solute_holdout.get("holdout_class_ids"))
                or solute_holdout.get("passed") is not True
                or not all(
                    math.isfinite(value)
                    for value in (solute_p95, signed_bias, coverage, worst_class)
                )
                or solute_p95 > math.log(10.0)
                or signed_bias > math.log(2.0)
                or coverage < 0.9
                or coverage > 1.0
                or worst_class > math.log(10.0)
            ):
                errors.append("solute-class holdout gate is not satisfied")
    descriptors = row.get("descriptor_model")
    if isinstance(descriptors, Mapping):
        if descriptors.get("species_intercept") is not None:
            errors.append("species-name intercepts are forbidden")
        family = descriptors.get("response_family")
        slots = tuple(descriptors.get("coefficient_slots") or ())
        expected_slots = (
            ("beta_0", "beta_F", "beta_chi", "beta_q", "beta_Lambda", "beta_T")
            if family == "ln_gamma_infinity"
            else ("theta_0", "theta_F", "theta_q", "theta_Lambda", "theta_T")
            if family == "wagner_interaction"
            else ()
        )
        if not expected_slots:
            errors.append("descriptor response family is not an admitted v1 equation")
        if slots != expected_slots:
            errors.append("descriptor coefficient slots/DOF do not match fixed v1 form")
        if isinstance(validation, Mapping) and expected_slots:
            try:
                fitted_dof = int(validation.get("fitted_scalar_dof"))
                independent_classes = int(
                    validation.get("independent_solute_class_count")
                )
            except (TypeError, ValueError):
                fitted_dof = independent_classes = -1
            if fitted_dof != len(expected_slots):
                errors.append("fitted Tier B DOF must match the frozen v1 slots")
            if independent_classes < 3 * len(expected_slots):
                errors.append("Tier B needs at least three independent classes per DOF")
    else:
        errors.append("descriptor_model must be a mapping")
    basis = row.get("interaction_basis")
    if not nonempty_sequence(basis):
        interaction_basis: tuple[str, ...] = ()
        errors.append("interaction_basis must be a nonempty ordered component list")
    else:
        interaction_basis = tuple(str(value) for value in basis)
        expected_basis = tuple(
            component
            for component, value in zip(matrix_components, matrix_values)
            if value > 0.0
        )
        if (
            len(set(interaction_basis)) != len(interaction_basis)
            or interaction_basis != expected_basis
        ):
            errors.append(
                "interaction_basis must exactly match ordered nonzero matrix components"
            )
    interactions = row.get("interaction_terms")
    if nonempty_sequence(interactions):
        allowed_origins = {"direct_fit", "descriptor_prediction", "structural_zero"}
        term_components: list[str] = []
        for item in interactions:
            if not isinstance(item, Mapping) or item.get("origin") not in allowed_origins:
                errors.append("every interaction term needs exactly one admitted origin")
                continue
            component_id = str(item.get("component_id") or "")
            term_components.append(component_id)
            if not component_id or not all(
                item.get(key) is not None
                for key in ("epsilon_T", "covariance_row_column_ref")
            ):
                errors.append(
                    "every interaction term needs component, epsilon(T), and covariance reference"
                )
            origin = item.get("origin")
            required_receipts = {
                "direct_fit": (
                    "identifying_source_series",
                    "estimator_receipt",
                    "covariance_ref",
                ),
                "descriptor_prediction": (
                    "model_version",
                    "design_matrix_digest",
                    "held_out_class_status",
                    "hull_receipt",
                    "predictive_covariance_ref",
                ),
                "structural_zero": ("primary_evidence_ref", "constraint_receipt"),
            }[origin]
            if not all(item.get(key) for key in required_receipts):
                errors.append(
                    f"{origin} interaction term lacks its complete origin receipt"
                )
        if tuple(term_components) != interaction_basis:
            errors.append(
                "interaction terms must cover the ordered interaction_basis exactly once"
            )
    else:
        errors.append("interaction_terms must be a nonempty sequence")
    return tuple(errors)


class MeltActivityResolver:
    """Component-keyed ln-space resolver with Phase-1 shadow adapters."""

    def __init__(self, registry: MeltActivityRegistry | None = None) -> None:
        self.registry = registry or MeltActivityRegistry.load()

    def _refusal(
        self,
        query: MeltActivityQuery,
        code: ActivityRefusalCode,
        detail: str,
        *,
        tier: ActivityTier | None,
        model_row_id: str | None,
        attempts: tuple[ActivityAttempt, ...] = (),
    ) -> SourceReactionActivity:
        return SourceReactionActivity(
            component_id=query.component_id,
            value=None,
            verdict=ActivityVerdictKind.REFUSAL,
            bound_direction=None,
            reason=code.value,
            standard_state=query.target_standard_state,
            phase_assemblage_ref=query.assemblage_ref,
            chemical_potential_ref=None,
            state_fingerprint=query.state_fingerprint,
            solve_group_id=None,
            provider="melt_activity_resolver",
            authority=False,
            refusal_code=code,
            detail=detail,
            tier=tier,
            model_row_id=model_row_id,
            domain_status="refused",
            target_standard_state=query.target_standard_state,
            attempts=attempts,
        )

    def resolve_engine_basis(
        self, query: MeltActivityQuery, engine: TierAEngineInput
    ) -> SourceReactionActivity:
        row = self.registry.row_for_component(query.component_id, tier=ActivityTier.A)
        row_id = str(row.get("row_id")) if row is not None else None
        if row is None:
            return self._refusal(
                query,
                ActivityRefusalCode.UNMAPPED_ENDMEMBER,
                "no Tier A engine-basis conversion row",
                tier=ActivityTier.A,
                model_row_id=None,
            )
        target_state = row.get("target_standard_state") or {}
        if target_state.get("resolution_status") == "standard_state_unresolved":
            return self._refusal(
                query,
                ActivityRefusalCode.STANDARD_STATE_UNRESOLVED,
                "target standard-state sidecar is unresolved",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if query.target_standard_state != self.registry.row_standard_state(
            row, "target_standard_state"
        ):
            return self._refusal(
                query,
                ActivityRefusalCode.STANDARD_STATE_MISMATCH,
                "query target is not the exact per-row rail standard",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        source = row.get("source") or {}
        expected_engine_ids = tuple(source.get("engine_component_ids") or ())
        expected_coefficients = tuple(
            float(value) for value in source.get("basis_coefficients") or ()
        )
        supplied_coefficients = tuple(
            float(value) for value in engine.basis_coefficients
        )
        if (
            tuple(engine.engine_component_ids) != expected_engine_ids
            or supplied_coefficients != expected_coefficients
        ):
            return self._refusal(
                query,
                ActivityRefusalCode.BASIS_TRANSFORM_FAILED,
                "engine component IDs/basis coefficients do not match the admitted row",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        expected_source_state = self.registry.row_standard_state(
            row, "source_standard_state"
        )
        if engine.source_standard_state != expected_source_state:
            return self._refusal(
                query,
                ActivityRefusalCode.STANDARD_STATE_MISMATCH,
                "engine source standard state does not match the admitted row",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        domain = row.get("domain") or {}
        try:
            minimum_T, maximum_T = (float(value) for value in domain["T_K"])
            minimum_P, maximum_P = (float(value) for value in domain["P_bar"])
        except (KeyError, TypeError, ValueError):
            return self._refusal(
                query,
                ActivityRefusalCode.MISSING_EVIDENCE,
                "Tier A row has no valid T/P domain",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if not (
            minimum_T <= query.temperature_K <= maximum_T
            and minimum_P <= query.pressure_bar <= maximum_P
        ):
            return self._refusal(
                query,
                ActivityRefusalCode.DESCRIPTOR_HULL_EXCEEDED,
                "query T/P lies outside the admitted Tier A row domain",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if query.matrix_domain_ref != domain.get("matrix_domain_ref"):
            return self._refusal(
                query,
                ActivityRefusalCode.DESCRIPTOR_HULL_EXCEEDED,
                "query matrix-domain receipt does not match the admitted row",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if query.phase_kind != domain.get("phase_requirement"):
            return self._refusal(
                query,
                ActivityRefusalCode.ASSEMBLAGE_MISMATCH,
                "query phase does not match the admitted Tier A row",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if not query.consumed_reservoir_ids:
            return self._refusal(
                query,
                ActivityRefusalCode.INCOMPLETE_MELT_INVENTORY,
                "query does not declare the melt reservoirs consumed by the reaction",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if query.unmodeled_nonzero_reservoir_ids:
            return self._refusal(
                query,
                ActivityRefusalCode.UNMODELED_RESERVOIR_PRESENT,
                "Tier A row has no spectator policy for nonzero ownerless reservoirs",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if not query.inventory_complete:
            return self._refusal(
                query,
                ActivityRefusalCode.INCOMPLETE_MELT_INVENTORY,
                "Tier A requires a complete ordered reservoir inventory",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        coefficients = supplied_coefficients
        if len(coefficients) != len(engine.engine_component_ids) or not coefficients:
            return self._refusal(
                query,
                ActivityRefusalCode.BASIS_TRANSFORM_FAILED,
                "engine component IDs and basis coefficients must be nonempty/aligned",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if not all(math.isfinite(value) for value in coefficients):
            return self._refusal(
                query,
                ActivityRefusalCode.BASIS_TRANSFORM_FAILED,
                "basis coefficients must be finite",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        try:
            target_mu0 = float(engine.target_mu0_J_per_mol)
            if not math.isfinite(target_mu0):
                raise ValueError
            candidates: list[tuple[str, float]] = []
            if (
                engine.engine_ln_activities is not None
                and engine.source_mu0_J_per_mol is not None
            ):
                if not (
                    len(engine.engine_ln_activities)
                    == len(engine.source_mu0_J_per_mol)
                    == len(coefficients)
                ):
                    raise ValueError
                ln_value = sum(
                    coefficient * float(ln_activity)
                    for coefficient, ln_activity in zip(
                        coefficients, engine.engine_ln_activities, strict=True
                    )
                ) + (
                    sum(
                        coefficient * float(mu0)
                        for coefficient, mu0 in zip(
                            coefficients, engine.source_mu0_J_per_mol, strict=True
                        )
                    )
                    - target_mu0
                ) / (GAS_CONSTANT * query.temperature_K)
                candidates.append(("engine_activity_plus_standard_state_offset", ln_value))
            if engine.mixture_mu_J_per_mol is not None:
                if len(engine.mixture_mu_J_per_mol) != len(coefficients):
                    raise ValueError
                ln_value = (
                    sum(
                        coefficient * float(mu)
                        for coefficient, mu in zip(
                            coefficients, engine.mixture_mu_J_per_mol, strict=True
                        )
                    )
                    - target_mu0
                ) / (GAS_CONSTANT * query.temperature_K)
                candidates.append(("absolute_mixture_potential", ln_value))
        except (TypeError, ValueError, ZeroDivisionError):
            return self._refusal(
                query,
                ActivityRefusalCode.NON_FINITE_POTENTIAL,
                "Tier A potentials/temperature must be finite and aligned",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if not candidates or not all(math.isfinite(value) for _, value in candidates):
            return self._refusal(
                query,
                ActivityRefusalCode.NON_FINITE_POTENTIAL,
                "Tier A requires one complete potential route",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if len(candidates) == 2 and not math.isclose(
            candidates[0][1], candidates[1][1], rel_tol=0.0, abs_tol=1.0e-10
        ):
            return self._refusal(
                query,
                ActivityRefusalCode.CONSISTENCY_GATE_FAILED,
                "direct-mu and activity-plus-offset transforms disagree",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        ln_value = candidates[0][1]
        try:
            legacy_value = math.exp(ln_value)
        except OverflowError:
            legacy_value = None
        value = legacy_value if legacy_value != 0.0 else None
        return SourceReactionActivity(
            component_id=query.component_id,
            value=value,
            ln_value=ln_value,
            verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
            bound_direction=None,
            reason="tier_a_method_pending_independent_validation",
            standard_state=query.target_standard_state,
            phase_assemblage_ref=query.assemblage_ref,
            chemical_potential_ref=engine.conversion_ref,
            state_fingerprint=query.state_fingerprint,
            solve_group_id=None,
            provider="melt_activity_resolver.tier_a",
            authority=False,
            report_label="status-bearing-not-point",
            tier=ActivityTier.A,
            model_row_id=row_id,
            domain_status="in_domain_pending_validation",
            conversion_ref=engine.conversion_ref,
            source_standard_state=engine.source_standard_state,
            target_standard_state=query.target_standard_state,
            attempts=(
                ActivityAttempt(
                    ActivityTier.A, row_id, "selected_status_bearing"
                ),
            ),
            random_variable_key=(
                row_id,
                self.registry.row_digest(row_id),
                query.state_fingerprint,
                str(query.target_standard_state.identity_id),
            ),
            derivation={
                "routes": [name for name, _ in candidates],
                "basis_coefficients": list(coefficients),
                "engine_component_ids": list(engine.engine_component_ids),
                "target_mu0_J_per_mol": target_mu0,
                "canonical_space": "ln_activity",
                "legacy_value_edge": (
                    "representable" if value is not None else "unrepresentable"
                ),
            },
        )

    def resolve_tier_c(self, query: MeltActivityQuery) -> SourceReactionActivity:
        if query.component_id in _UNSUPPORTED_RESERVOIR_CODES:
            code = _UNSUPPORTED_RESERVOIR_CODES[query.component_id]
            return self._refusal(
                query,
                code,
                "reservoir has no admitted melt-side activity owner",
                tier=ActivityTier.C,
                model_row_id=f"inventory.{query.component_id}.refusal.v1",
            )
        row = self.registry.tier_c_row_for_component(query.component_id)
        if row is None:
            return self._refusal(
                query,
                ActivityRefusalCode.STANDARD_STATE_UNRESOLVED,
                "no Tier C row identifies this melt component and standard state",
                tier=ActivityTier.C,
                model_row_id=None,
            )
        row_id = str(row["row_id"])
        if row.get("disposition") == "refuse":
            raw_code = str(row.get("refusal_code") or "standard_state_unresolved")
            return self._refusal(
                query,
                ActivityRefusalCode(raw_code),
                "Tier C inventory row is explicitly refused until identity/evidence lands",
                tier=ActivityTier.C,
                model_row_id=row_id,
            )
        if (
            query.formula_basis != row.get("formula_basis")
            or query.target_standard_state.identity_id
            != row.get("target_standard_state_id")
            or query.matrix_domain_ref != row.get("matrix_domain_ref")
        ):
            return self._refusal(
                query,
                ActivityRefusalCode.STANDARD_STATE_MISMATCH,
                "Tier C query identity/matrix does not match the admitted row",
                tier=ActivityTier.C,
                model_row_id=row_id,
            )
        if query.unmodeled_nonzero_reservoir_ids:
            return self._refusal(
                query,
                ActivityRefusalCode.UNMODELED_RESERVOIR_PRESENT,
                "Tier C row has no spectator policy for nonzero ownerless reservoirs",
                tier=ActivityTier.C,
                model_row_id=row_id,
            )
        if not query.inventory_complete:
            return self._refusal(
                query,
                ActivityRefusalCode.INCOMPLETE_MELT_INVENTORY,
                "Tier C cannot convert missing inventory into ideality or zero",
                tier=ActivityTier.C,
                model_row_id=None,
            )
        try:
            fractions = tuple(
                float(value) for value in query.component_mole_fractions.values()
            )
        except (TypeError, ValueError):
            fractions = (math.nan,)
        if (
            not fractions
            or not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in fractions)
            or not math.isclose(sum(fractions), 1.0, rel_tol=0.0, abs_tol=1.0e-9)
        ):
            return self._refusal(
                query,
                ActivityRefusalCode.INCOMPLETE_MELT_INVENTORY,
                "Tier C requires a finite normalized complete mole-fraction basis",
                tier=ActivityTier.C,
                model_row_id=row_id,
            )
        canonical_account: dict[str, float] = {}
        inventory_identity_valid = bool(query.ordered_reservoirs)
        for reservoir in query.ordered_reservoirs:
            expected_id = f"process.cleaned_melt:{reservoir.formula}"
            amount = float(reservoir.amount_mol)
            if (
                reservoir.speciation != "process.cleaned_melt"
                or reservoir.component_id != expected_id
                or reservoir.formula in canonical_account
                or not math.isfinite(amount)
                or amount < 0.0
            ):
                inventory_identity_valid = False
                break
            canonical_account[reservoir.formula] = amount
        reconstructed_digest = _stable_digest(
            {"process.cleaned_melt": canonical_account}
        )
        inventory_total = sum(canonical_account.values())
        reported_fractions = {
            str(key): float(value)
            for key, value in query.component_mole_fractions.items()
        }
        expected_fraction_keys = set(canonical_account)
        if (
            not inventory_identity_valid
            or reconstructed_digest != query.inventory_digest
            or inventory_total <= 0.0
            or set(reported_fractions) != expected_fraction_keys
            or any(
                not math.isclose(
                    reported_fractions[component],
                    amount / inventory_total,
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
                for component, amount in canonical_account.items()
            )
        ):
            return self._refusal(
                query,
                ActivityRefusalCode.INCOMPLETE_MELT_INVENTORY,
                "Tier C mole fractions do not reconcile to the ordered inventory digest",
                tier=ActivityTier.C,
                model_row_id=row_id,
            )
        if query.component_id not in query.component_mole_fractions:
            return self._refusal(
                query,
                ActivityRefusalCode.MISSING_EVIDENCE,
                "component mole fraction is absent on the named composition basis",
                tier=ActivityTier.C,
                model_row_id=None,
            )
        if query.out_of_domain and query.continuation_ln_band is None:
            return self._refusal(
                query,
                ActivityRefusalCode.DESCRIPTOR_HULL_EXCEEDED,
                "out-of-domain Tier C evaluation needs an explicit continuation band",
                tier=ActivityTier.C,
                model_row_id=None,
            )
        try:
            mole_fraction = float(query.component_mole_fractions[query.component_id])
        except (TypeError, ValueError):
            mole_fraction = math.nan
        if not math.isfinite(mole_fraction) or mole_fraction < 0.0:
            return self._refusal(
                query,
                ActivityRefusalCode.MISSING_EVIDENCE,
                "component mole fraction must be finite and non-negative",
                tier=ActivityTier.C,
                model_row_id=None,
            )
        attempts = (
            ActivityAttempt(
                ActivityTier.A,
                None,
                "refused",
                ActivityRefusalCode.UNMAPPED_ENDMEMBER,
                "no admitted engine-basis row",
            ),
            ActivityAttempt(
                ActivityTier.B,
                None,
                "refused",
                ActivityRefusalCode.MISSING_EVIDENCE,
                "Phase 1 contains no Tier B coefficient rows",
            ),
            ActivityAttempt(ActivityTier.C, row_id, "selected_status_bearing"),
        )
        if mole_fraction == 0.0:
            return SourceReactionActivity(
                component_id=query.component_id,
                value=0.0,
                ln_value=None,
                verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
                bound_direction=None,
                reason=PROVEN_EMPTY_COMPONENT,
                standard_state=query.target_standard_state,
                phase_assemblage_ref=query.assemblage_ref,
                chemical_potential_ref=None,
                state_fingerprint=query.state_fingerprint,
                solve_group_id=None,
                provider="melt_activity_resolver.tier_c",
                authority=False,
                report_label="status-bearing-proven-zero",
                tier=ActivityTier.C,
                model_row_id=row_id,
                domain_status="proven_empty_component",
                target_standard_state=query.target_standard_state,
                attempts=attempts,
                zero_because=PROVEN_EMPTY_COMPONENT,
                derivation={
                    "inventory_complete": True,
                    "zero_proof": "complete atom-balanced inventory has X_i=0",
                },
            )
        ln_value = math.log(mole_fraction)
        if query.continuation_ln_band is not None:
            lower, upper = (float(value) for value in query.continuation_ln_band)
            if not (
                math.isfinite(lower)
                and math.isfinite(upper)
                and lower <= 0.0 <= upper
            ):
                return self._refusal(
                    query,
                    ActivityRefusalCode.VALIDATION_BAND_UNAVAILABLE,
                    "continuation band must be finite natural-log offsets around zero",
                    tier=ActivityTier.C,
                    model_row_id=row_id,
                )
            ln_band: tuple[float | None, float | None] = (lower, upper)
            band_kind = "out_of_domain_model_form_envelope"
            domain_status = "out_of_domain_continuation_status_bearing"
        else:
            ln_band = (None, None)
            band_kind = "unbounded_model_form"
            domain_status = "in_domain_model_form_unbounded"
        return SourceReactionActivity(
            component_id=query.component_id,
            value=mole_fraction,
            ln_value=ln_value,
            verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
            bound_direction=None,
            reason="declared_ideal_solution",
            standard_state=query.target_standard_state,
            phase_assemblage_ref=query.assemblage_ref,
            chemical_potential_ref=None,
            state_fingerprint=query.state_fingerprint,
            solve_group_id=None,
            provider="melt_activity_resolver.tier_c",
            authority=False,
            report_label="status-bearing-not-point",
            tier=ActivityTier.C,
            model_row_id=row_id,
            domain_status=domain_status,
            target_standard_state=query.target_standard_state,
            attempts=attempts,
            ln_band=ln_band,
            band_kind=band_kind,
            random_variable_key=(
                row_id,
                self.registry.row_digest(row_id),
                query.state_fingerprint,
                str(query.target_standard_state.identity_id),
            ),
            derivation={
                "model": "declared_ideal_solution",
                "algebra": "ln(a_i)=ln(X_i)",
                "composition_basis": query.composition_basis,
                "certification_ceiling": "never",
            },
        )

    def adapt_legacy_value(
        self,
        query: MeltActivityQuery,
        legacy_value: float,
        *,
        evidence_ref: str | None,
        evidence_tier: str | None,
        provenance: Mapping[str, Any] | None,
    ) -> SourceReactionActivity:
        row_id = f"legacy.activity.{query.component_id}.v1"
        if not query.inventory_complete or not query.consumed_reservoir_ids:
            return self._refusal(
                query,
                ActivityRefusalCode.INCOMPLETE_MELT_INVENTORY,
                "legacy adapter requires complete inventory and consumed-reservoir IDs",
                tier=None,
                model_row_id=row_id,
            )
        if query.unmodeled_nonzero_reservoir_ids:
            return self._refusal(
                query,
                ActivityRefusalCode.UNMODELED_RESERVOIR_PRESENT,
                "legacy row has no spectator policy for nonzero ownerless reservoirs",
                tier=None,
                model_row_id=row_id,
            )
        value = float(legacy_value)
        if not math.isfinite(value) or value <= 0.0:
            return self._refusal(
                query,
                ActivityRefusalCode.MISSING_EVIDENCE,
                "legacy shadow adapter requires a finite positive activity",
                tier=None,
                model_row_id=row_id,
            )
        row_digest = _stable_digest(
            {
                "row_id": row_id,
                "adapter_version": 1,
                "standard_state": query.target_standard_state.as_mapping(),
            }
        )
        return SourceReactionActivity(
            component_id=query.component_id,
            value=value,
            ln_value=math.log(value),
            verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
            bound_direction=None,
            reason="legacy_scalar_typed_shadow",
            standard_state=query.target_standard_state,
            phase_assemblage_ref=query.assemblage_ref,
            chemical_potential_ref=None,
            state_fingerprint=query.state_fingerprint,
            solve_group_id=None,
            provider="melt_activity_resolver.legacy_shadow",
            authority=False,
            report_label="shadow-only-not-point",
            tier=None,
            model_row_id=row_id,
            domain_status="legacy_in_domain",
            target_standard_state=query.target_standard_state,
            attempts=(
                ActivityAttempt(
                    ActivityTier.A,
                    None,
                    "not_selected",
                    ActivityRefusalCode.MISSING_EVIDENCE,
                    "engine potentials unavailable in Phase 1",
                ),
                ActivityAttempt(
                    ActivityTier.B,
                    None,
                    "not_selected",
                    ActivityRefusalCode.MISSING_EVIDENCE,
                    "no Tier B coefficient rows in Phase 1",
                ),
                ActivityAttempt(None, row_id, "selected_shadow_adapter"),
            ),
            ln_band=(None, None),
            band_kind="legacy_unbounded_shadow",
            random_variable_key=(
                row_id,
                row_digest,
                query.state_fingerprint,
                str(query.target_standard_state.identity_id),
            ),
            evidence_ref=evidence_ref,
            evidence_tier=evidence_tier,
            derivation={
                "canonical_space": "ln_activity",
                "legacy_edge": "value retained only for equality/diagnostics",
                "legacy_provenance": dict(provenance or {}),
            },
        )

    def resolve_feo_shadow(self, query: MeltActivityQuery) -> SourceReactionActivity:
        row = self.registry.row_for_component("FeO", tier=ActivityTier.A)
        row_id = str(row.get("row_id")) if row is not None else None
        if query.intrinsic_fO2_log10 is None:
            return self._refusal(
                query,
                ActivityRefusalCode.REDOX_STATE_UNRESOLVED,
                "intrinsic melt fO2 is absent; FeO wt% fallback is legacy-degraded",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if row is None:
            return self._refusal(
                query,
                ActivityRefusalCode.UNMAPPED_ENDMEMBER,
                "Kress-Carmichael row is absent",
                tier=ActivityTier.A,
                model_row_id=None,
            )
        expected_model_id = str((row.get("source") or {}).get("engine_version") or "")
        if (
            query.redox_basis_ref != "intrinsic_melt_fO2_log10"
            or query.redox_model_id != expected_model_id
            or query.redox_model_digest != self.registry.row_digest(str(row_id))
        ):
            return self._refusal(
                query,
                ActivityRefusalCode.REDOX_STATE_UNRESOLVED,
                "Fe redox basis/model identity does not match the admitted row",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if not query.inventory_complete or not query.consumed_reservoir_ids:
            return self._refusal(
                query,
                ActivityRefusalCode.INCOMPLETE_MELT_INVENTORY,
                "Kress-Carmichael requires complete inventory and Fe reservoirs",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if query.unmodeled_nonzero_reservoir_ids:
            return self._refusal(
                query,
                ActivityRefusalCode.UNMODELED_RESERVOIR_PRESENT,
                "Fe row has no spectator policy for nonzero ownerless reservoirs",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        minimum_K = 1200.0 + 273.15
        maximum_K = 1630.0 + 273.15
        if not minimum_K <= query.temperature_K <= maximum_K:
            return self._refusal(
                query,
                ActivityRefusalCode.REDOX_MODEL_OUT_OF_DOMAIN,
                "Kress-Carmichael authoritative calibration is 1200-1630 C",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if not query.composition_wt_pct:
            return self._refusal(
                query,
                ActivityRefusalCode.REDOX_STATE_UNRESOLVED,
                "Kress-Carmichael requires complete composition and Fe inventory",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        from simulator.fe_redox import (
            calphad_ferrous_feo_activity_diagnostic,
            kress91_ferrous_feo_activity,
        )

        if query.redox_model_pressure_bar is None:
            return self._refusal(
                query,
                ActivityRefusalCode.REDOX_STATE_UNRESOLVED,
                "exact pressure control used by the Kress-Carmichael solve is absent",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        pressure_control = float(query.redox_model_pressure_bar)
        if not math.isfinite(pressure_control) or pressure_control <= 0.0:
            return self._refusal(
                query,
                ActivityRefusalCode.REDOX_STATE_UNRESOLVED,
                "Kress-Carmichael pressure control must be finite and positive",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        try:
            typed_value = kress91_ferrous_feo_activity(
                comp_wt=query.composition_wt_pct,
                fO2_log=float(query.intrinsic_fO2_log10),
                T_K=query.temperature_K,
                pressure_bar=pressure_control,
            )
            diagnostic = calphad_ferrous_feo_activity_diagnostic(
                comp_wt=query.composition_wt_pct,
                fO2_log=float(query.intrinsic_fO2_log10),
                T_K=query.temperature_K,
                pressure_bar=pressure_control,
            )
        except (TypeError, ValueError) as exc:
            return self._refusal(
                query,
                ActivityRefusalCode.REDOX_STATE_UNRESOLVED,
                f"Kress-Carmichael input refused: {exc}",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        if not math.isfinite(typed_value) or typed_value <= 0.0:
            return self._refusal(
                query,
                ActivityRefusalCode.REDOX_STATE_UNRESOLVED,
                "Kress-Carmichael produced no positive FeO activity",
                tier=ActivityTier.A,
                model_row_id=row_id,
            )
        ln_value = math.log(typed_value)
        source_standard_state = self.registry.row_standard_state(
            row, "source_standard_state"
        )
        return SourceReactionActivity(
            component_id="FeO",
            value=typed_value,
            ln_value=ln_value,
            verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
            bound_direction=None,
            reason="kress_carmichael_feo_source_basis_conversion_unresolved",
            standard_state=source_standard_state,
            phase_assemblage_ref=query.assemblage_ref,
            chemical_potential_ref="REF-001-kress-carmichael-1991",
            state_fingerprint=query.state_fingerprint,
            solve_group_id=None,
            provider="melt_activity_resolver.kress91_shadow",
            authority=False,
            report_label="shadow-only-not-point",
            tier=ActivityTier.A,
            model_row_id=row_id,
            domain_status="source_basis_in_domain_target_conversion_unresolved",
            source_standard_state=source_standard_state,
            target_standard_state=query.target_standard_state,
            attempts=(
                ActivityAttempt(ActivityTier.A, row_id, "selected_status_bearing"),
            ),
            ln_band=(None, None),
            band_kind="validation_band_unavailable",
            random_variable_key=(
                str(row_id),
                self.registry.row_digest(str(row_id)),
                query.state_fingerprint,
                str(query.target_standard_state.identity_id),
            ),
            derivation={
                "formulation": "Kress-Carmichael-1991 with current FeO authority blend",
                "model_version": "REF-001-kress-carmichael-1991",
                "composition_basis": "oxide_weight_percent_to_Kress91_mole_fraction",
                "intrinsic_fO2_log10": query.intrinsic_fO2_log10,
                "temperature_K": query.temperature_K,
                "pressure_bar": pressure_control,
                "target_conversion_status": "mu0_target_pending",
                "redox_receipt": diagnostic,
            },
        )

    def unsupported_reservoir_results(
        self,
        *,
        state_fingerprint: str,
        inventory_digest: str,
        temperature_K: float,
        pressure_bar: float,
    ) -> dict[str, SourceReactionActivity]:
        results: dict[str, SourceReactionActivity] = {}
        for component_id in _UNSUPPORTED_RESERVOIR_CODES:
            query = MeltActivityQuery(
                component_id=component_id,
                formula_basis=component_id,
                target_standard_state=self.registry.target_standard_state(component_id),
                temperature_K=temperature_K,
                pressure_bar=pressure_bar,
                component_mole_fractions={},
                composition_basis="complete_inventory",
                ordered_reservoirs=(),
                inventory_digest=inventory_digest,
                inventory_complete=True,
                state_fingerprint=state_fingerprint,
            )
            results[component_id] = self.resolve_tier_c(query)
        return results


def _standard_states_are_comparable(
    component_id: str,
    legacy: StandardStateIdentity,
    independent: StandardStateIdentity | None,
) -> bool:
    # Catalog declarations predate component-qualified identity IDs. Require
    # their complete thermodynamic tuple to match the independent row target,
    # while also requiring the independent side to carry the exact component.
    if independent is None or independent.component_id != component_id:
        return False
    if legacy.component_id not in (None, component_id):
        return False
    if (
        legacy.identity_id is not None
        and legacy.identity_id != independent.identity_id
    ):
        return False
    return (
        legacy.convention,
        legacy.phase,
        legacy.reference_pressure_bar,
        legacy.reference_temperature_K,
        legacy.component_basis,
    ) == (
        independent.convention,
        independent.phase,
        independent.reference_pressure_bar,
        independent.reference_temperature_K,
        independent.component_basis,
    )


def _independent_shadow_comparison(
    *,
    component_id: str,
    legacy_value: float | None,
    legacy_standard_state: StandardStateIdentity,
    independent_result: SourceReactionActivity | None,
    unavailable_detail: str,
) -> ShadowComparison:
    method = "engine_basis_plus_standard_state_conversion"
    if independent_result is None:
        return ShadowComparison(
            component_id=component_id,
            legacy_value=legacy_value,
            typed_ln_value=None,
            delta_ln=None,
            equal=None,
            population="legacy_in_domain",
            comparison_status=SHADOW_NOT_COMPARABLE_YET,
            comparison_method=method,
            tolerance_ln=SHADOW_EQUALITY_ABS_TOL_LN,
            detail=unavailable_detail,
        )
    if independent_result.ln_value is None:
        return ShadowComparison(
            component_id=component_id,
            legacy_value=legacy_value,
            typed_ln_value=None,
            delta_ln=None,
            equal=None,
            population="legacy_in_domain",
            comparison_status=SHADOW_NOT_COMPARABLE_YET,
            comparison_method=method,
            tolerance_ln=SHADOW_EQUALITY_ABS_TOL_LN,
            refusal_code=independent_result.refusal_code,
            detail=(
                independent_result.detail
                or "independent engine-basis resolution did not produce ln(activity)"
            ),
        )
    if not _standard_states_are_comparable(
        component_id, legacy_standard_state, independent_result.standard_state
    ):
        return ShadowComparison(
            component_id=component_id,
            legacy_value=legacy_value,
            typed_ln_value=independent_result.ln_value,
            delta_ln=None,
            equal=None,
            population="legacy_in_domain",
            comparison_status=SHADOW_NOT_COMPARABLE_YET,
            comparison_method=method,
            tolerance_ln=SHADOW_EQUALITY_ABS_TOL_LN,
            detail=(
                "independent result and executed legacy scalar have different "
                "standard-state identities"
            ),
        )
    if legacy_value is None or not math.isfinite(legacy_value) or legacy_value <= 0.0:
        return ShadowComparison(
            component_id=component_id,
            legacy_value=legacy_value,
            typed_ln_value=independent_result.ln_value,
            delta_ln=None,
            equal=None,
            population="legacy_in_domain",
            comparison_status=SHADOW_NOT_COMPARABLE_YET,
            comparison_method=method,
            tolerance_ln=SHADOW_EQUALITY_ABS_TOL_LN,
            detail="executed legacy activity is not finite and positive",
        )
    delta_ln = independent_result.ln_value - math.log(legacy_value)
    return ShadowComparison(
        component_id=component_id,
        legacy_value=legacy_value,
        typed_ln_value=independent_result.ln_value,
        delta_ln=delta_ln,
        equal=math.isclose(
            delta_ln,
            0.0,
            rel_tol=0.0,
            abs_tol=SHADOW_EQUALITY_ABS_TOL_LN,
        ),
        population="legacy_in_domain",
        comparison_status=SHADOW_COMPARABLE,
        comparison_method=method,
        tolerance_ln=SHADOW_EQUALITY_ABS_TOL_LN,
    )


def build_shadow_for_vapour_batch(
    *,
    rules: Sequence[Any],
    ledger_snapshot: Mapping[str, Any],
    state: Any | None,
    registry: MeltActivityRegistry | None = None,
    engine_inputs_by_component: Mapping[str, TierAEngineInput] | None = None,
) -> MeltActivityShadow:
    """Build one component result per batch without influencing live answers."""

    resolver = MeltActivityResolver(registry)
    independent_inputs = dict(engine_inputs_by_component or {})
    temperature_K = float(getattr(state, "temperature_K", 0.0) or 0.0)
    pressure_bar = float(getattr(state, "total_pressure_Pa", 0.0) or 0.0) / 1.0e5
    intrinsic_log = getattr(state, "source_reaction_fO2_log10", None)
    redox_pressure_bar = getattr(
        state, "source_reaction_activity_pressure_bar", None
    )
    redox_basis_ref = "intrinsic_melt_fO2_log10"
    redox_model_id = getattr(state, "source_reaction_redox_model_id", None)
    fe_row = resolver.registry.row_for_component("FeO", tier=ActivityTier.A)
    redox_model_digest = (
        resolver.registry.row_digest(str(fe_row["row_id"]))
        if fe_row is not None
        else None
    )
    composition_wt_pct = (
        getattr(state, "source_reaction_composition_wt_pct", {}) if state else {}
    )
    state_fp, inventory_digest, reservoirs, inventory_complete = (
        complete_inventory_identity(
            ledger_snapshot,
            temperature_K=temperature_K,
            pressure_bar=pressure_bar,
            intrinsic_fO2_log10=intrinsic_log,
            redox_model_pressure_bar=redox_pressure_bar,
            redox_basis_ref=redox_basis_ref,
            redox_model_id=redox_model_id,
            redox_model_digest=redox_model_digest,
            composition_wt_pct=composition_wt_pct,
        )
    )
    ownerless_reservoirs = ownerless_nonzero_reservoir_ids(reservoirs)

    def consumed_reservoir_ids(component_id: str) -> tuple[str, ...]:
        match = re.match(r"[A-Z][a-z]?", component_id)
        element = match.group(0) if match is not None else ""
        accepted_formulas = {component_id, element}
        row = resolver.registry.row_for_component(component_id, tier=ActivityTier.A)
        if row is not None:
            parent_oxide = str(
                ((row.get("rail_component") or {}).get("parent_oxide") or "")
            )
            if parent_oxide:
                accepted_formulas.add(parent_oxide)
            accepted_formulas.update(
                str(label)
                for label in ((row.get("source") or {}).get("redox_inputs") or ())
                if str(label)
            )
        return tuple(
            reservoir.component_id
            for reservoir in reservoirs
            if reservoir.formula in accepted_formulas
        )
    results: dict[str, SourceReactionActivity] = {}
    comparisons: list[ShadowComparison] = []
    legacy_by_component: dict[str, list[tuple[str, float, Any]]] = {}
    if state is not None:
        for rule in rules:
            declaration = getattr(rule, "source_reaction_activity", None)
            if declaration is None:
                continue
            raw = state.source_reaction_activities.get(rule.species_id)
            if raw is None:
                continue
            try:
                legacy_value = float(raw)
            except (TypeError, ValueError):
                continue
            legacy_by_component.setdefault(declaration.component_id, []).append(
                (rule.species_id, legacy_value, declaration)
            )

    for component_id, entries in sorted(legacy_by_component.items()):
        values = [entry[1] for entry in entries]
        declaration = entries[0][2]
        legacy_value = values[0]
        if any(
            not math.isclose(value, legacy_value, rel_tol=0.0, abs_tol=0.0)
            for value in values[1:]
        ):
            query = MeltActivityQuery(
                component_id=component_id,
                formula_basis=component_id,
                target_standard_state=declaration.standard_state,
                temperature_K=temperature_K,
                pressure_bar=pressure_bar,
                component_mole_fractions={},
                composition_basis="legacy_provider_reported",
                ordered_reservoirs=reservoirs,
                inventory_digest=inventory_digest,
                inventory_complete=inventory_complete,
                state_fingerprint=state_fp,
                matrix_domain_ref="matrix_domain.silicate_melt.phase1.v1",
                assemblage_ref="legacy-provider:liquid_melt",
                phase_kind="liquid_melt",
                consumed_reservoir_ids=consumed_reservoir_ids(component_id),
                unmodeled_nonzero_reservoir_ids=ownerless_reservoirs,
            )
            results[component_id] = resolver._refusal(
                query,
                ActivityRefusalCode.CONSISTENCY_GATE_FAILED,
                "carriers sharing one component supplied different legacy activities",
                tier=None,
                model_row_id=f"legacy.activity.{component_id}.v1",
            )
            comparisons.append(
                ShadowComparison(
                    component_id=component_id,
                    legacy_value=legacy_value,
                    typed_ln_value=None,
                    delta_ln=None,
                    equal=None,
                    population="legacy_in_domain",
                    comparison_status=SHADOW_NOT_COMPARABLE_YET,
                    comparison_method="engine_basis_plus_standard_state_conversion",
                    tolerance_ln=SHADOW_EQUALITY_ABS_TOL_LN,
                    refusal_code=ActivityRefusalCode.CONSISTENCY_GATE_FAILED,
                    detail=(
                        "independent comparison blocked because carriers sharing "
                        "the legacy component disagree"
                    ),
                )
            )
            continue
        first_species = entries[0][0]
        evidence_ref = state.source_reaction_activity_evidence_refs.get(first_species)
        provenance = state.source_reaction_activity_provenance.get(first_species, {})
        query = MeltActivityQuery(
            component_id=component_id,
            formula_basis=component_id,
            # Legacy adapter preserves the executed declaration. Tier A uses
            # the registry's exact per-row target for independent conversion.
            target_standard_state=declaration.standard_state,
            temperature_K=temperature_K,
            pressure_bar=pressure_bar,
            component_mole_fractions={},
            composition_basis="legacy_provider_reported",
            ordered_reservoirs=reservoirs,
            inventory_digest=inventory_digest,
            inventory_complete=inventory_complete,
            state_fingerprint=state_fp,
            matrix_domain_ref="matrix_domain.silicate_melt.phase1.v1",
            assemblage_ref="legacy-provider:liquid_melt",
            phase_kind="liquid_melt",
            consumed_reservoir_ids=consumed_reservoir_ids(component_id),
            unmodeled_nonzero_reservoir_ids=ownerless_reservoirs,
        )
        typed = resolver.adapt_legacy_value(
            query,
            legacy_value,
            evidence_ref=evidence_ref,
            evidence_tier=(
                str(provenance.get("melt_oxide_activity_evidence_tier") or "")
                or None
            ),
            provenance=provenance,
        )
        results[component_id] = typed
        independent_result = None
        engine_input = independent_inputs.get(component_id)
        tier_a_row = resolver.registry.row_for_component(
            component_id, tier=ActivityTier.A
        )
        target_resolution = str(
            ((tier_a_row or {}).get("target_standard_state") or {}).get(
                "resolution_status"
            )
            or ""
        )
        if engine_input is not None:
            engine_query = replace(
                query,
                target_standard_state=resolver.registry.target_standard_state(
                    component_id
                ),
                composition_basis="independent_engine_basis",
            )
            independent_result = resolver.resolve_engine_basis(
                engine_query, engine_input
            )
            if independent_result.ln_value is not None:
                results[component_id] = independent_result
        comparisons.append(
            _independent_shadow_comparison(
                component_id=component_id,
                legacy_value=legacy_value,
                legacy_standard_state=declaration.standard_state,
                independent_result=independent_result,
                unavailable_detail=(
                    "target standard-state sidecar is unresolved pending t-570"
                    if target_resolution == "standard_state_unresolved"
                    and component_id in {"AlO1.5", "TiO2", "CrO1.5", "MnO"}
                    else "independent engine potentials and target standard-state "
                    "conversion are unavailable"
                ),
            )
        )

    legacy_fe = None
    if state is not None and "Fe" in state.source_reaction_activities:
        try:
            legacy_fe = float(state.source_reaction_activities["Fe"])
        except (TypeError, ValueError):
            legacy_fe = None
    fe_query = MeltActivityQuery(
        component_id="FeO",
        formula_basis="FeO",
        target_standard_state=resolver.registry.target_standard_state("FeO"),
        temperature_K=temperature_K,
        pressure_bar=pressure_bar,
        component_mole_fractions={},
        composition_basis="oxide_weight_percent_to_Kress91_mole_fraction",
        ordered_reservoirs=reservoirs,
        inventory_digest=inventory_digest,
        inventory_complete=inventory_complete,
        state_fingerprint=state_fp,
        matrix_domain_ref="matrix_domain.silicate_melt.phase1.v1",
        assemblage_ref="legacy-provider:kress-carmichael-liquid-melt",
        phase_kind="liquid_melt",
        consumed_reservoir_ids=consumed_reservoir_ids("FeO"),
        unmodeled_nonzero_reservoir_ids=ownerless_reservoirs,
        intrinsic_fO2_log10=intrinsic_log,
        redox_model_pressure_bar=redox_pressure_bar,
        redox_basis_ref=redox_basis_ref,
        redox_model_id=redox_model_id,
        redox_model_digest=redox_model_digest,
        composition_wt_pct=composition_wt_pct,
    )
    fe_result = resolver.resolve_feo_shadow(fe_query)
    results["FeO"] = fe_result
    if intrinsic_log is None:
        comparisons.append(
            ShadowComparison(
                component_id="FeO",
                legacy_value=legacy_fe,
                typed_ln_value=None,
                delta_ln=None,
                equal=None,
                population="legacy_degraded",
                comparison_status=SHADOW_LEGACY_DEGRADED,
                comparison_method=None,
                refusal_code=ActivityRefusalCode.REDOX_STATE_UNRESOLVED,
                fallback_reason="feo_weight_fraction",
                detail="legacy fallback is outside the typed FeO row domain",
            )
        )
    else:
        independent_fe_result = None
        fe_engine_input = independent_inputs.get("FeO")
        if fe_engine_input is not None:
            independent_fe_result = resolver.resolve_engine_basis(
                fe_query, fe_engine_input
            )
        comparisons.append(
            _independent_shadow_comparison(
                component_id="FeO",
                legacy_value=legacy_fe,
                legacy_standard_state=(
                    fe_result.standard_state
                    or resolver.registry.row_standard_state(
                        fe_row or {}, "source_standard_state"
                    )
                ),
                independent_result=independent_fe_result,
                unavailable_detail=(
                    "independent FeO engine potential and target "
                    "standard-state conversion are unavailable; the Kress result "
                    "shares the executed legacy implementation"
                ),
            )
        )

    results.update(
        resolver.unsupported_reservoir_results(
            state_fingerprint=state_fp,
            inventory_digest=inventory_digest,
            temperature_K=temperature_K,
            pressure_bar=pressure_bar,
        )
    )
    return MeltActivityShadow(
        results_by_component=results,
        comparisons=tuple(comparisons),
        state_fingerprint=state_fp,
        inventory_digest=inventory_digest,
        registry_digest=resolver.registry.digest,
    )
