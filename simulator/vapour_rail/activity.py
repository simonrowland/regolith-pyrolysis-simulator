"""Assemblage-to-activity seam and Henrian upper-bound semantics (VR-9 / U2-A).

Runtime seam: :class:`CondensedPhaseActivityProvider` sits between a
read-only melt-equilibrium result and the builtin source-reaction pressure
evaluator. MAGEMin and ThermoEngine adapters supply typed evidence only;
neither adapter writes the catalog or the AtomLedger.

Diagnostic-only for this chunk: answers never certify, never drive an
authority-bearing flux flip, and never coerce an upper bound into a point.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Final

import numpy as np

from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_TIER,
    MELT_OXIDE_IDEAL_ASSERTION_TIER,
    MELT_OXIDE_IDEAL_SOLUTION_MODEL,
    activities_from_molecular_henrian_row,
    melt_oxide_activity_coefficient,
    pure_liquid_reference_coefficient,
)
from simulator.trace_oxide_parents import (
    ACTIVITY_BASIS,
    LIQUID_PARENT_OXIDE,
    ledger_component_key,
)
from simulator.physical_constants import GAS_CONSTANT
from simulator.scalar_boundary import is_declared_real_scalar

# CODATA R, J/(mol·K). Activities use mu in J/mol so RT ln(a) is dimensionally
# consistent: [J/mol] / ([J/(mol·K)] · [K]) is dimensionless.
R_J_PER_MOL_K: Final[float] = GAS_CONSTANT

REASON_HENRIAN_GAMMA_UNMEASURED: Final[str] = "henrian_gamma_unmeasured"
BOUND_NOT_POINT: Final[str] = "bound-not-point"
LOWER_BOUND_NOT_POINT: Final[str] = "lower-bound-not-point"
STATUS_BEARING_NOT_POINT: Final[str] = "status-bearing-not-point"
DIAGNOSTIC_AUTHORITY: Final[bool] = False


class ActivityVerdictKind(str, Enum):
    """How an activity number may be consumed by a pressure/flux path."""

    POINT = "Point"
    STATUS_BEARING_VALUE = "StatusBearingValue"
    UPPER_BOUND = "UpperBound"
    LOWER_BOUND = "LowerBound"
    REFUSAL = "Refusal"


class BoundDirection(str, Enum):
    UPPER = "upper"
    LOWER = "lower"


class ActivityRefusalCode(str, Enum):
    STATE_FINGERPRINT_MISMATCH = "state_fingerprint_mismatch"
    ASSEMBLAGE_MISMATCH = "assemblage_mismatch"
    STANDARD_STATE_MISMATCH = "standard_state_mismatch"
    UNMAPPED_PHASE = "unmapped_phase"
    UNMAPPED_ENDMEMBER = "unmapped_endmember"
    TIMEOUT = "timeout"
    CRASH = "crash"
    EXPIRED = "expired"
    NON_FINITE_POTENTIAL = "non_finite_potential"
    CONSISTENCY_GATE_FAILED = "consistency_gate_failed"
    MONOTONICITY_UNPROVED = "monotonicity_unproved"
    UNITY_NOT_UPPER_BOUND = "unity_not_upper_bound_for_standard_state"
    MISSING_EVIDENCE = "missing_evidence"
    COMPOUND_PROXY_FORBIDDEN = "compound_proxy_forbidden"
    STANDARD_STATE_UNRESOLVED = "standard_state_unresolved"
    BASIS_TRANSFORM_FAILED = "basis_transform_failed"
    DESCRIPTOR_HULL_EXCEEDED = "descriptor_hull_exceeded"
    VALIDATION_BAND_UNAVAILABLE = "validation_band_unavailable"
    REDOX_STATE_UNRESOLVED = "redox_state_unresolved"
    REDOX_MODEL_OUT_OF_DOMAIN = "redox_model_out_of_domain"
    UNSUPPORTED_VALENCE_RESERVOIR = "unsupported_valence_reservoir"
    SULFUR_RESERVOIR_OWNER_MISSING = "sulfur_reservoir_owner_missing"
    HALIDE_RESERVOIR_OWNER_MISSING = "halide_reservoir_owner_missing"
    INCOMPLETE_MELT_INVENTORY = "incomplete_melt_inventory"
    UNMODELED_RESERVOIR_PRESENT = "unmodeled_reservoir_present"


class ActivityTier(str, Enum):
    """Evidence architecture tier for a typed activity result."""

    A = "A"
    B = "B"
    C = "C"


@dataclass(frozen=True)
class ActivityAttempt:
    """One deterministic resolver attempt, retained even when another wins."""

    tier: ActivityTier | None
    model_row_id: str | None
    disposition: str
    refusal_code: ActivityRefusalCode | None = None
    detail: str | None = None

    def as_mapping(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value if self.tier is not None else None,
            "model_row_id": self.model_row_id,
            "disposition": self.disposition,
            "refusal_code": (
                self.refusal_code.value if self.refusal_code is not None else None
            ),
            "detail": self.detail,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ActivityAttempt":
        raw_tier = payload.get("tier")
        raw_refusal = payload.get("refusal_code")
        return cls(
            tier=ActivityTier(str(raw_tier)) if raw_tier is not None else None,
            model_row_id=(
                str(payload["model_row_id"])
                if payload.get("model_row_id") is not None
                else None
            ),
            disposition=str(payload.get("disposition") or ""),
            refusal_code=(
                ActivityRefusalCode(str(raw_refusal))
                if raw_refusal is not None
                else None
            ),
            detail=(str(payload["detail"]) if payload.get("detail") else None),
        )


def _fingerprint_float(value: Any) -> float:
    if isinstance(value, Decimal):
        return Decimal.__float__(value)
    if isinstance(value, float):
        return float.__float__(value)
    return float(value)


def _is_nonfinite_number(value: Any) -> bool:
    if isinstance(value, Decimal):
        if Decimal.is_nan(value) or Decimal.is_snan(value) or Decimal.is_infinite(value):
            return True
    try:
        if isinstance(value, np.generic) and not np.isfinite(value):
            return True
    except TypeError:
        pass
    try:
        return not math.isfinite(_fingerprint_float(value))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class StandardStateIdentity:
    """Exact standard-state identity; substring matching is forbidden."""

    convention: str
    phase: str
    reference_pressure_bar: float
    reference_temperature_K: float | None = None
    component_basis: str = "raoultian_pure_endmember"
    identity_id: str | None = None
    component_id: str | None = None

    def fingerprint(self) -> str:
        payload = {
            "convention": self.convention,
            "phase": self.phase,
            "P_bar": self.reference_pressure_bar,
            "T_K": self.reference_temperature_K,
            "basis": self.component_basis,
        }
        for key in ("P_bar", "T_K"):
            value = payload[key]
            if value is not None or key == "P_bar":
                if _is_nonfinite_number(value):
                    raise ValueError(f"non-finite standard-state {key}")
                value = _fingerprint_float(value)
                payload[key] = value if value != 0.0 else 0.0
        # Preserve legacy fingerprints when the ABI-safe identity tail is not
        # supplied; component-qualified t-568 states cannot collide.
        if self.identity_id is not None:
            payload["id"] = self.identity_id
        if self.component_id is not None:
            payload["component_id"] = self.component_id
        return _stable_hash(payload)

    def as_mapping(self) -> dict[str, Any]:
        return {
            "id": self.identity_id,
            "component_id": self.component_id,
            "convention": self.convention,
            "phase": self.phase,
            "reference_pressure_bar": float(self.reference_pressure_bar),
            "reference_temperature_K": self.reference_temperature_K,
            "component_basis": self.component_basis,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "StandardStateIdentity":
        return cls(
            convention=str(payload.get("convention") or ""),
            phase=str(payload.get("phase") or ""),
            reference_pressure_bar=float(payload.get("reference_pressure_bar")),
            reference_temperature_K=(
                float(payload["reference_temperature_K"])
                if payload.get("reference_temperature_K") is not None
                else None
            ),
            component_basis=str(
                payload.get("component_basis") or "raoultian_pure_endmember"
            ),
            identity_id=(str(payload["id"]) if payload.get("id") else None),
            component_id=(
                str(payload["component_id"])
                if payload.get("component_id")
                else None
            ),
        )


@dataclass(frozen=True)
class AssemblageIdentity:
    """MAGEMin (or equivalent) assemblage identity for cross-engine matching.

    Phase and endmember IDs are exact tokens from a reviewed map — never
    substring proxies such as free MgO when spinel is present.
    """

    engine: str
    phase_ids: tuple[str, ...]
    endmember_ids: tuple[str, ...]
    bulk_composition_fingerprint: str
    database: str | None = None

    def fingerprint(self) -> str:
        payload = {
            "engine": self.engine,
            "phases": list(self.phase_ids),
            "endmembers": list(self.endmember_ids),
            "bulk": self.bulk_composition_fingerprint,
            "database": self.database,
        }
        return _stable_hash(payload)


@dataclass(frozen=True)
class StateFingerprint:
    """Thermodynamic state identity shared by assemblage and potential calls."""

    temperature_K: float
    pressure_bar: float
    fO2_bar: float | None
    composition_fingerprint: str
    liquid_fraction: float | None = None

    def fingerprint(self) -> str:
        payload = {
            "T_K": float(self.temperature_K),
            "P_bar": float(self.pressure_bar),
            "fO2_bar": None if self.fO2_bar is None else float(self.fO2_bar),
            "composition": self.composition_fingerprint,
            "liquid_fraction": self.liquid_fraction,
        }
        return _stable_hash(payload)


@dataclass(frozen=True)
class PhaseEndmemberMap:
    """Reviewed exact phase/endmember map (no substring inference)."""

    component_id: str
    phase_id: str
    endmember_id: str
    source: str


@dataclass(frozen=True)
class MageminAssemblageEvidence:
    """Typed MAGEMin assemblage evidence for the activity seam.

    Diagnostic proposal only — never a ledger transition and never a
    catalog write.
    """

    assemblage: AssemblageIdentity
    state: StateFingerprint
    phase_compositions: Mapping[str, Mapping[str, float]]
    converged: bool
    timed_out: bool = False
    crashed: bool = False
    expired: bool = False
    provider: str = "magemin"


@dataclass(frozen=True)
class ThermoEnginePotentialEvidence:
    """Typed ThermoEngine chemical-potential evidence at a matched state.

    ``mu_J_per_mol`` is the component chemical potential; ``mu0_J_per_mol`` is
    the pure-endmember reference potential at the same T, P, and standard
    state. Both must be finite.
    """

    component_id: str
    state: StateFingerprint
    standard_state: StandardStateIdentity
    assemblage_ref: str
    mu_J_per_mol: float
    mu0_J_per_mol: float
    timed_out: bool = False
    crashed: bool = False
    expired: bool = False
    provider: str = "thermoengine"
    independent_consistency_ok: bool | None = None
    independent_consistency_note: str | None = None


@dataclass(frozen=True)
class ActivityInputDeclaration:
    """Static catalog declaration answered by :class:`SourceReactionActivity`."""

    component_id: str
    standard_state: StandardStateIdentity
    activity_model: str
    allow_henrian_upper_bound: bool = True
    compound_bearing: bool = False
    require_assemblage_match: bool = True


@dataclass(frozen=True)
class SourceReactionActivity:
    """Runtime answer to ``source_reactions[].activity_input``.

    An upper bound is preserved as an upper bound through pressure, HKL flux,
    recession, and reporting; it is never coerced to a point. Bounds and
    pending-validation consumers remain diagnostic (``authority=False``).
    """

    component_id: str
    value: float | None
    verdict: ActivityVerdictKind
    bound_direction: BoundDirection | None
    reason: str | None
    standard_state: StandardStateIdentity | None
    phase_assemblage_ref: str | None
    chemical_potential_ref: str | None
    state_fingerprint: str | None
    solve_group_id: str | None
    provider: str | None
    authority: bool = False
    report_label: str | None = None
    refusal_code: ActivityRefusalCode | None = None
    detail: str | None = None
    derivation: Mapping[str, Any] = field(default_factory=dict)
    evidence_ref: str | None = None
    evidence_tier: str | None = None
    # t-568 Phase 1 ABI-safe tail. ``value`` remains the bounded legacy edge;
    # resolver arithmetic and comparisons use ``ln_value``.
    ln_value: float | None = None
    ln_band: tuple[float | None, float | None] | None = None
    band_kind: str | None = None
    band_coverage: float | None = None
    tier: ActivityTier | None = None
    model_row_id: str | None = None
    domain_status: str | None = None
    conversion_ref: str | None = None
    source_standard_state: StandardStateIdentity | None = None
    target_standard_state: StandardStateIdentity | None = None
    attempts: tuple[ActivityAttempt, ...] = ()
    random_variable_key: tuple[str, str, str, str] | None = None
    independent_sigma_ln: float | None = None
    correlation_loadings: tuple[tuple[str, float], ...] = ()
    correlation_basis_ref: str | None = None
    zero_because: str | None = None

    def __post_init__(self) -> None:
        if self.target_standard_state is None and self.standard_state is not None:
            object.__setattr__(self, "target_standard_state", self.standard_state)
        if self.verdict is ActivityVerdictKind.REFUSAL:
            if self.value is not None or self.ln_value is not None:
                raise ValueError("activity refusal cannot carry a numeric value")
            if self.zero_because is not None:
                raise ValueError("activity refusal cannot carry a zero proof")
        if self.tier is not None and self.target_standard_state is None:
            raise ValueError("tiered activity requires a target standard state")
        if self.tier is not None and self.value == 0.0 and self.zero_because is None:
            raise ValueError("tiered zero requires an explicit zero_because proof")
        if self.ln_band is not None:
            lower, upper = self.ln_band
            if (lower is None) != (upper is None):
                raise ValueError("ln_band bounds must both be finite or both be null")
            if lower is not None and upper is not None:
                if not (
                    math.isfinite(float(lower))
                    and math.isfinite(float(upper))
                    and float(lower) <= 0.0 <= float(upper)
                ):
                    raise ValueError("ln_band must be finite offsets enclosing zero")
        if self.band_coverage is not None and not (
            math.isfinite(float(self.band_coverage))
            and 0.0 < float(self.band_coverage) <= 1.0
        ):
            raise ValueError("band_coverage must lie in (0, 1]")
        if self.independent_sigma_ln is not None and not (
            math.isfinite(float(self.independent_sigma_ln))
            and float(self.independent_sigma_ln) >= 0.0
        ):
            raise ValueError("independent_sigma_ln must be finite and non-negative")
        loading_groups: set[str] = set()
        for group_id, loading in self.correlation_loadings:
            if not group_id or group_id in loading_groups or not math.isfinite(float(loading)):
                raise ValueError("correlation loadings need unique groups and finite values")
            loading_groups.add(group_id)
        if self.ln_value is None and self.value is not None and self.value > 0.0:
            object.__setattr__(self, "ln_value", math.log(float(self.value)))
        if self.value == 0.0 and self.zero_because is None:
            # Existing constructors occasionally use zero as a numeric result.
            # They remain valid legacy objects, but only the resolver may emit
            # the typed proven-empty sentinel.
            return
        if self.zero_because is not None:
            if self.value != 0.0 or self.ln_value is not None:
                raise ValueError(
                    "typed proven-zero activity requires value=0 and ln_value=None"
                )
        elif self.ln_value is not None:
            if not math.isfinite(float(self.ln_value)):
                raise ValueError("ln_value must be finite")
            if self.value is not None and self.value > 0.0:
                expected = math.log(float(self.value))
                if not math.isclose(
                    float(self.ln_value), expected, rel_tol=0.0, abs_tol=1.0e-12
                ):
                    raise ValueError("value and ln_value are inconsistent")

    def may_certify(self) -> bool:
        """Bounds and refusals never certify; points stay non-authoritative here."""

        if self.verdict != ActivityVerdictKind.POINT:
            return False
        if not self.authority:
            return False
        return True

    def as_pressure_activity(self) -> float | None:
        """Numeric activity for a pressure evaluator, or None on refusal.

        Callers must still inspect :attr:`verdict`: a returned number under
        ``UpperBound`` must propagate as a bound, never as a certified value.
        """

        if self.verdict is ActivityVerdictKind.REFUSAL:
            return None
        if self.value is None:
            return None
        return float(self.value)

    def as_mapping(self) -> dict[str, Any]:
        """JSON-safe diagnostic form; numeric authority remains unchanged."""

        return {
            "component_id": self.component_id,
            "value": self.value,
            "ln_value": self.ln_value,
            "verdict": self.verdict.value,
            "bound_direction": (
                self.bound_direction.value if self.bound_direction is not None else None
            ),
            "reason": self.reason,
            "standard_state": (
                self.standard_state.as_mapping()
                if self.standard_state is not None
                else None
            ),
            "phase_assemblage_ref": self.phase_assemblage_ref,
            "chemical_potential_ref": self.chemical_potential_ref,
            "state_fingerprint": self.state_fingerprint,
            "solve_group_id": self.solve_group_id,
            "provider": self.provider,
            "authority": self.authority,
            "report_label": self.report_label,
            "refusal_code": (
                self.refusal_code.value if self.refusal_code is not None else None
            ),
            "detail": self.detail,
            "derivation": dict(self.derivation),
            "evidence_ref": self.evidence_ref,
            "evidence_tier": self.evidence_tier,
            "ln_band": list(self.ln_band) if self.ln_band is not None else None,
            "band_kind": self.band_kind,
            "band_coverage": self.band_coverage,
            "tier": self.tier.value if self.tier is not None else None,
            "model_row_id": self.model_row_id,
            "domain_status": self.domain_status,
            "conversion_ref": self.conversion_ref,
            "source_standard_state": (
                self.source_standard_state.as_mapping()
                if self.source_standard_state is not None
                else None
            ),
            "target_standard_state": (
                self.target_standard_state.as_mapping()
                if self.target_standard_state is not None
                else None
            ),
            "attempts": [attempt.as_mapping() for attempt in self.attempts],
            "random_variable_key": (
                list(self.random_variable_key)
                if self.random_variable_key is not None
                else None
            ),
            "independent_sigma_ln": self.independent_sigma_ln,
            "correlation_loadings": [
                {"group_id": group_id, "loading_sigma_ln": loading}
                for group_id, loading in self.correlation_loadings
            ],
            "correlation_basis_ref": self.correlation_basis_ref,
            "zero_because": self.zero_because,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SourceReactionActivity":
        raw_band = payload.get("ln_band")
        raw_key = payload.get("random_variable_key")
        raw_loadings = payload.get("correlation_loadings") or ()
        return cls(
            component_id=str(payload.get("component_id") or ""),
            value=(
                float(payload["value"]) if payload.get("value") is not None else None
            ),
            verdict=ActivityVerdictKind(str(payload.get("verdict"))),
            bound_direction=(
                BoundDirection(str(payload["bound_direction"]))
                if payload.get("bound_direction") is not None
                else None
            ),
            reason=(str(payload["reason"]) if payload.get("reason") else None),
            standard_state=(
                StandardStateIdentity.from_mapping(payload["standard_state"])
                if isinstance(payload.get("standard_state"), Mapping)
                else None
            ),
            phase_assemblage_ref=(
                str(payload["phase_assemblage_ref"])
                if payload.get("phase_assemblage_ref")
                else None
            ),
            chemical_potential_ref=(
                str(payload["chemical_potential_ref"])
                if payload.get("chemical_potential_ref")
                else None
            ),
            state_fingerprint=(
                str(payload["state_fingerprint"])
                if payload.get("state_fingerprint")
                else None
            ),
            solve_group_id=(
                str(payload["solve_group_id"])
                if payload.get("solve_group_id")
                else None
            ),
            provider=(str(payload["provider"]) if payload.get("provider") else None),
            authority=bool(payload.get("authority", False)),
            report_label=(
                str(payload["report_label"]) if payload.get("report_label") else None
            ),
            refusal_code=(
                ActivityRefusalCode(str(payload["refusal_code"]))
                if payload.get("refusal_code") is not None
                else None
            ),
            detail=(str(payload["detail"]) if payload.get("detail") else None),
            derivation=(
                dict(payload["derivation"])
                if isinstance(payload.get("derivation"), Mapping)
                else {}
            ),
            evidence_ref=(
                str(payload["evidence_ref"]) if payload.get("evidence_ref") else None
            ),
            evidence_tier=(
                str(payload["evidence_tier"])
                if payload.get("evidence_tier")
                else None
            ),
            ln_value=(
                float(payload["ln_value"])
                if payload.get("ln_value") is not None
                else None
            ),
            ln_band=(
                (raw_band[0], raw_band[1])
                if isinstance(raw_band, Sequence) and len(raw_band) == 2
                else None
            ),
            band_kind=(
                str(payload["band_kind"]) if payload.get("band_kind") else None
            ),
            band_coverage=(
                float(payload["band_coverage"])
                if payload.get("band_coverage") is not None
                else None
            ),
            tier=(
                ActivityTier(str(payload["tier"]))
                if payload.get("tier") is not None
                else None
            ),
            model_row_id=(
                str(payload["model_row_id"])
                if payload.get("model_row_id")
                else None
            ),
            domain_status=(
                str(payload["domain_status"])
                if payload.get("domain_status")
                else None
            ),
            conversion_ref=(
                str(payload["conversion_ref"])
                if payload.get("conversion_ref")
                else None
            ),
            source_standard_state=(
                StandardStateIdentity.from_mapping(payload["source_standard_state"])
                if isinstance(payload.get("source_standard_state"), Mapping)
                else None
            ),
            target_standard_state=(
                StandardStateIdentity.from_mapping(payload["target_standard_state"])
                if isinstance(payload.get("target_standard_state"), Mapping)
                else None
            ),
            attempts=tuple(
                ActivityAttempt.from_mapping(attempt)
                for attempt in payload.get("attempts") or ()
                if isinstance(attempt, Mapping)
            ),
            random_variable_key=(
                tuple(str(part) for part in raw_key)  # type: ignore[arg-type]
                if isinstance(raw_key, Sequence) and len(raw_key) == 4
                else None
            ),
            independent_sigma_ln=(
                float(payload["independent_sigma_ln"])
                if payload.get("independent_sigma_ln") is not None
                else None
            ),
            correlation_loadings=tuple(
                (
                    str(item.get("group_id") or ""),
                    float(item.get("loading_sigma_ln")),
                )
                for item in raw_loadings
                if isinstance(item, Mapping)
            ),
            correlation_basis_ref=(
                str(payload["correlation_basis_ref"])
                if payload.get("correlation_basis_ref")
                else None
            ),
            zero_because=(
                str(payload["zero_because"]) if payload.get("zero_because") else None
            ),
        )


def composition_fingerprint(composition: Mapping[str, float]) -> str:
    """Stable fingerprint for oxide/component mole maps."""

    if any(_is_nonfinite_number(value) for value in composition.values()):
        raise ValueError("non-finite composition")
    cleaned = {
        str(key): _fingerprint_float(value)
        for key, value in sorted(composition.items())
        if _fingerprint_float(value) != 0.0
    }
    return _stable_hash(cleaned)


def activity_from_chemical_potentials(
    mu_J_per_mol: float,
    mu0_J_per_mol: float,
    temperature_K: float,
) -> float:
    """Compute ``a = exp((mu - mu0) / (R T))`` with explicit derivation.

    Premise
        At fixed T, P the chemical potential of component *i* relative to a
        pure-endmember standard state is
        ``mu_i = mu_i0 + R T ln(a_i)`` (Raoultian pure-endmember convention).

    Algebra
        ``ln(a_i) = (mu_i - mu_i0) / (R T)``
        ``a_i = exp((mu_i - mu_i0) / (R T))``

    Units
        ``mu``, ``mu0`` in J/mol; ``R`` in J/(mol·K); ``T`` in K → argument of
        ``exp`` is dimensionless.

    Limiting case
        ``mu_i = mu_i0`` ⇒ ``a_i = 1`` (pure endmember at the standard state).
    """

    if not is_declared_real_scalar(mu_J_per_mol) or not is_declared_real_scalar(
        mu0_J_per_mol
    ) or not is_declared_real_scalar(
        temperature_K,
        allow_numeric_str=True,
    ):
        raise TypeError("chemical potential inputs must be numeric")
    mu_J_per_mol = float(mu_J_per_mol)
    mu0_J_per_mol = float(mu0_J_per_mol)
    if not math.isfinite(mu_J_per_mol) or not math.isfinite(mu0_J_per_mol):
        raise ValueError("chemical potentials must be finite")
    temperature_K = float(temperature_K)
    if not math.isfinite(temperature_K) or temperature_K <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    argument = (mu_J_per_mol - mu0_J_per_mol) / (R_J_PER_MOL_K * temperature_K)
    if not math.isfinite(argument):
        raise ValueError("activity exponent is non-finite")
    # Guard extreme underflow/overflow into a typed failure rather than 0/inf.
    if argument < -700.0 or argument > 700.0:
        raise ValueError("activity exponent outside representable range")
    activity = math.exp(argument)
    if not math.isfinite(activity) or activity <= 0.0:
        raise ValueError("activity must be finite and positive")
    return activity


def prove_pressure_monotone_nondecreasing_in_activity(
    activity_exponent: float,
) -> bool:
    """Prove ``P ∝ a^n`` is monotone nondecreasing in ``a > 0``.

    Premise
        Source-reaction pressure evaluators use
        ``log10 P = log10 P_ref + n log10(a) + m log10(fO2/fO2_ref)``
        (see :class:`simulator.vapour_rail.catalog.CompiledPressureEvaluator`).

    Algebra
        ``P(a) = P_ref * a^n * (fO2 factor)`` with ``P_ref > 0``, ``a > 0``.
        ``dP/da = n * P_ref * a^{n-1} * (fO2 factor)``.
        Sign of the derivative is the sign of ``n`` for all ``a > 0``.

    Units
        Exponent ``n`` is dimensionless (activity power).

    Sanity
        ``n = 0`` ⇒ activity-independent (weakly nondecreasing).
        ``n > 0`` ⇒ strictly increasing in activity.
        ``n < 0`` ⇒ decreasing; ``a = 1`` is then a *lower* pressure bound,
        not an upper bound, so the Henrian ``a=1`` path must refuse.
    """

    if not is_declared_real_scalar(
        activity_exponent,
        allow_numeric_str=True,
    ) or not math.isfinite(float(activity_exponent)):
        return False
    return float(activity_exponent) >= 0.0


def henrian_unknown_gamma_upper_bound(
    *,
    component_id: str,
    activity_exponent: float,
    standard_state: StandardStateIdentity,
    mole_fraction: float | None = None,
    state_fingerprint: str | None = None,
    solve_group_id: str | None = None,
    gamma_anchor: float | None = None,
) -> SourceReactionActivity:
    """Classify a unity-gamma activity by the coefficient *property*.

    The name is retained for API compatibility, but the result is not always
    an upper bound.  At a fixed declared mole fraction ``X``, the regular-
    solution closure preserves the side of unity carried by the coefficient
    table: ``gamma_anchor <= 1`` gives ``a <= X`` and ``gamma_anchor > 1``
    gives ``a >= X``.  For the normal positive source-reaction exponent this
    means the unity-gamma value ``X`` is respectively an upper or lower
    pressure bound.  A negative exponent reverses the pressure direction.

    Missing coefficient or composition evidence produces an explicit
    status-bearing ideal-solution assertion.  It still supplies a numeric
    prediction, but it is never mislabeled as a proved bound or clean point.
    """

    if standard_state.component_basis != "raoultian_pure_endmember":
        return SourceReactionActivity(
            component_id=component_id,
            value=None,
            verdict=ActivityVerdictKind.REFUSAL,
            bound_direction=None,
            reason=REASON_HENRIAN_GAMMA_UNMEASURED,
            standard_state=standard_state,
            phase_assemblage_ref=None,
            chemical_potential_ref=None,
            state_fingerprint=state_fingerprint,
            solve_group_id=solve_group_id,
            provider="henrian_bound_policy",
            authority=False,
            report_label=BOUND_NOT_POINT,
            refusal_code=ActivityRefusalCode.UNITY_NOT_UPPER_BOUND,
            detail=(
                "unity-gamma activity-bound semantics require "
                "raoultian_pure_endmember "
                f"basis; got {standard_state.component_basis!r}"
            ),
        )

    try:
        if not is_declared_real_scalar(
            activity_exponent,
            allow_numeric_str=True,
        ):
            raise TypeError
        exponent = float(activity_exponent)
    except (TypeError, ValueError):
        exponent = math.nan
    if not math.isfinite(exponent):
        return SourceReactionActivity(
            component_id=component_id,
            value=None,
            verdict=ActivityVerdictKind.REFUSAL,
            bound_direction=None,
            reason=REASON_HENRIAN_GAMMA_UNMEASURED,
            standard_state=standard_state,
            phase_assemblage_ref=None,
            chemical_potential_ref=None,
            state_fingerprint=state_fingerprint,
            solve_group_id=solve_group_id,
            provider="henrian_bound_policy",
            authority=False,
            report_label=BOUND_NOT_POINT,
            refusal_code=ActivityRefusalCode.MONOTONICITY_UNPROVED,
            detail=(
                "cannot classify pressure-bound direction for non-finite "
                f"activity_exponent={activity_exponent!r}"
            ),
        )

    x_value: float | None
    try:
        if mole_fraction is not None and not is_declared_real_scalar(
            mole_fraction,
            allow_numeric_str=True,
        ):
            raise TypeError
        x_value = None if mole_fraction is None else float(mole_fraction)
    except (TypeError, ValueError):
        x_value = None
    if x_value is not None and (
        not math.isfinite(x_value) or not 0.0 <= x_value <= 1.0
    ):
        x_value = None

    supplied_gamma: float | None
    if gamma_anchor is None:
        supplied_gamma = None
    else:
        try:
            if not is_declared_real_scalar(
                gamma_anchor,
                allow_numeric_str=True,
            ):
                raise TypeError
            supplied_gamma = float(gamma_anchor)
        except (TypeError, ValueError):
            supplied_gamma = math.nan
        if not math.isfinite(supplied_gamma) or supplied_gamma <= 0.0:
            return SourceReactionActivity(
                component_id=component_id,
                value=None,
                verdict=ActivityVerdictKind.REFUSAL,
                bound_direction=None,
                reason=REASON_HENRIAN_GAMMA_UNMEASURED,
                standard_state=standard_state,
                phase_assemblage_ref=None,
                chemical_potential_ref=None,
                state_fingerprint=state_fingerprint,
                solve_group_id=solve_group_id,
                provider="henrian_bound_policy",
                authority=False,
                report_label=BOUND_NOT_POINT,
                refusal_code=ActivityRefusalCode.MISSING_EVIDENCE,
                detail=(
                    "unity-gamma bound requires a finite positive gamma_anchor; "
                    f"got {gamma_anchor!r}"
                ),
            )

    coeff = (
        None
        if supplied_gamma is not None
        else melt_oxide_activity_coefficient(component_id)
    )
    if supplied_gamma is None and (coeff is None or x_value is None):
        assumed_activity = x_value if x_value is not None else 1.0
        missing = []
        if coeff is None:
            missing.append("coefficient_table_row")
        if x_value is None:
            missing.append("mole_fraction")
        return SourceReactionActivity(
            component_id=component_id,
            value=assumed_activity,
            verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
            bound_direction=None,
            reason="declared_ideal_solution_activity",
            standard_state=standard_state,
            phase_assemblage_ref=None,
            chemical_potential_ref=None,
            state_fingerprint=state_fingerprint,
            solve_group_id=solve_group_id,
            provider="declared_ideal_solution_policy",
            authority=False,
            report_label=STATUS_BEARING_NOT_POINT,
            detail=(
                "ideal-solution activity asserted because required analytical "
                f"inputs are absent: {', '.join(missing)}"
            ),
            derivation={
                "premise": "declared ideal solution has gamma=1",
                "algebra": "a = gamma*X = X",
                "units": "gamma, X, and activity are dimensionless",
                "limiting_case": "X=1 gives a=1",
                "assumed_mole_fraction": assumed_activity,
                "missing_inputs": tuple(missing),
                "activity_model": MELT_OXIDE_IDEAL_SOLUTION_MODEL,
            },
            evidence_tier=MELT_OXIDE_IDEAL_ASSERTION_TIER,
        )

    gamma_value = (
        supplied_gamma if supplied_gamma is not None else float(coeff.gamma)
    )
    gamma_is_at_most_unity = gamma_value <= 1.0
    pressure_increases_with_activity = exponent >= 0.0
    is_upper = gamma_is_at_most_unity == pressure_increases_with_activity
    direction = BoundDirection.UPPER if is_upper else BoundDirection.LOWER
    verdict = (
        ActivityVerdictKind.UPPER_BOUND
        if is_upper
        else ActivityVerdictKind.LOWER_BOUND
    )
    return SourceReactionActivity(
        component_id=component_id,
        value=x_value,
        verdict=verdict,
        bound_direction=direction,
        reason=REASON_HENRIAN_GAMMA_UNMEASURED,
        standard_state=standard_state,
        phase_assemblage_ref=None,
        chemical_potential_ref=None,
        state_fingerprint=state_fingerprint,
        solve_group_id=solve_group_id,
        provider="henrian_bound_policy",
        authority=False,
        report_label=BOUND_NOT_POINT if is_upper else LOWER_BOUND_NOT_POINT,
        derivation={
            "premise": (
                "at fixed X, the coefficient table determines whether "
                "a=gamma*X lies above or below the unity-gamma value X"
            ),
            "algebra": (
                "gamma<=1 ⇒ a<=X; gamma>1 ⇒ a>=X; the sign of the "
                "pressure activity exponent preserves or reverses that bound"
            ),
            "units": "a dimensionless; n dimensionless activity exponent",
            "limiting_case": "gamma=1 makes the bound exact at a=X",
            "activity_exponent": exponent,
            "mole_fraction": x_value,
            "gamma_anchor": gamma_value,
            "gamma_property": "gamma<=1" if gamma_is_at_most_unity else "gamma>1",
            "coefficient_domain_K": (
                None if supplied_gamma is not None else coeff.valid_range_K
            ),
        },
        evidence_ref=(
            "henrian_unknown_gamma_upper_bound"
            if supplied_gamma is not None
            else coeff.citation
        ),
        evidence_tier=MELT_OXIDE_ACTIVITY_TIER,
    )


def _is_external_activity_evidence_ref(provider_id: str, evidence_ref: str) -> bool:
    """Return whether a reported activity cites evidence outside its producer."""

    reference = evidence_ref.strip()
    if not reference:
        return False
    folded = reference.casefold()
    provider = provider_id.strip().casefold()
    producer_markers = (
        "_last_vapor_pressure_diagnostic",
        "equilibriumresult.activity_coefficients",
        "activity_coefficients[",
        ".activities[",
    )
    if any(marker in folded for marker in producer_markers):
        return False
    if provider and (folded.startswith(f"{provider}:") or folded == provider):
        return False
    external_patterns = (
        r"\bdoi\s*:?[\s]*10\.\d{4,9}/\S+",
        r"https?://\S+",
        r"\bisbn(?:-1[03])?\s*:?[\s]*[0-9Xx-]{10,}",
        r"\bissn\s*:?[\s]*\d{4}-\d{3}[\dXx]\b",
        r"\bnasa\s+ads\s+bibcode\b",
        r"\bbibcode\s*:?[\s]*[12]\d{3}[A-Za-z0-9.&]{10,}",
    )
    return any(
        re.search(pattern, reference, re.IGNORECASE)
        for pattern in external_patterns
    )


class CondensedPhaseActivityProvider:
    """Owned assemblage→activity seam (DESIGN-REV5 §9.1).

    Matches state / assemblage / standard-state identities exactly and refuses
    mismatch, timeout, crash, expiry, or unmapped phase/endmember. Diagnostic
    only: successful points still carry ``authority=False`` until a later R
    epoch promotes the activity pipeline.
    """

    def __init__(
        self,
        phase_endmember_map: Sequence[PhaseEndmemberMap] | None = None,
        *,
        per_call_deadline_s: float = 30.0,
    ) -> None:
        self._map: dict[str, PhaseEndmemberMap] = {
            item.component_id: item for item in (phase_endmember_map or ())
        }
        if not is_declared_real_scalar(
            per_call_deadline_s,
            allow_numeric_str=True,
        ):
            raise TypeError("per_call_deadline_s must be numeric")
        self.per_call_deadline_s = float(per_call_deadline_s)

    def resolve_source_reaction_activity(
        self,
        declaration: ActivityInputDeclaration,
        *,
        magemin: MageminAssemblageEvidence | None,
        thermoengine: ThermoEnginePotentialEvidence | None,
        activity_exponent: float,
        solve_group_id: str | None = None,
        state_fingerprint: str | None = None,
        measured_gamma: float | None = None,
        mole_fraction: float | None = None,
        reported_activity: float | None = None,
        reported_activity_provider: str | None = None,
        reported_activity_evidence_ref: str | None = None,
        reported_activity_standard_state: StandardStateIdentity | None = None,
        reported_activity_provenance: Mapping[str, Any] | None = None,
        compound_bearing_state: bool = False,
        temperature_K: float | None = None,
    ) -> SourceReactionActivity:
        """Answer one ``activity_input`` declaration with a typed activity."""

        if compound_bearing_state and declaration.compound_bearing:
            # Free-oxide proxy is forbidden when a compound phase is present.
            if magemin is None or thermoengine is None:
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.COMPOUND_PROXY_FORBIDDEN,
                    "compound-bearing state requires matched MAGEMin+ThermoEngine "
                    "evidence; free-oxide ACTIVITY_KEYS proxy is diagnostic-only "
                    "and cannot answer this contract",
                    standard_state=declaration.standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                )

        if reported_activity is not None:
            # A backend-reported thermodynamic activity is already the
            # dimensionless value in the declaration's standard state.  It is
            # not a gamma and must not be routed through ``a = gamma * X``.
            # Compound-bearing declarations still require the matched
            # MAGEMin/ThermoEngine path above; this point path is only admitted
            # when the catalog explicitly says assemblage matching is not
            # required for the source component.
            if declaration.activity_model != "provider_reported_thermodynamic_activity":
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.MISSING_EVIDENCE,
                    "reported activity cannot answer activity_model "
                    f"{declaration.activity_model!r}",
                    standard_state=declaration.standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                )
            if declaration.require_assemblage_match:
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.MISSING_EVIDENCE,
                    "reported activity cannot satisfy an assemblage-matched "
                    "activity_input declaration",
                    standard_state=declaration.standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                )
            provider_id = (
                reported_activity_provider.strip()
                if isinstance(reported_activity_provider, str)
                else ""
            )
            evidence_ref = (
                reported_activity_evidence_ref.strip()
                if isinstance(reported_activity_evidence_ref, str)
                else ""
            )
            if not provider_id or reported_activity_standard_state is None:
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.MISSING_EVIDENCE,
                    "reported activity requires provider and standard-state identity",
                    standard_state=declaration.standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                )
            if reported_activity_standard_state != declaration.standard_state:
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.STANDARD_STATE_MISMATCH,
                    "reported activity standard state does not match the catalog "
                    "activity_input declaration",
                    standard_state=declaration.standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                )
            try:
                if not is_declared_real_scalar(
                    reported_activity,
                    allow_numeric_str=True,
                ):
                    raise TypeError
                value = float(reported_activity)
            except (TypeError, ValueError):
                value = math.nan
            if not math.isfinite(value) or value <= 0.0:
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.MISSING_EVIDENCE,
                    "reported activity must be finite and positive",
                    standard_state=declaration.standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                )
            provenance = dict(reported_activity_provenance or {})
            domain_authority = provenance.get("gamma_domain_authority")
            domain_status = (
                str(domain_authority.get("authority_status") or "")
                if isinstance(domain_authority, Mapping)
                else ""
            )
            activity_model = str(
                provenance.get("melt_oxide_activity_model") or ""
            )
            evidence_tier = str(
                provenance.get("melt_oxide_activity_evidence_tier")
                or provenance.get("melt_oxide_gamma_tier")
                or "UNSPECIFIED"
            )
            warning = str(
                provenance.get("melt_oxide_activity_warning") or ""
            ).strip()
            evidence_is_external = _is_external_activity_evidence_ref(
                provider_id, evidence_ref
            )
            status_reasons: list[str] = []
            if domain_status == "out_of_gamma_domain":
                status_reasons.append("out_of_gamma_domain")
            if activity_model == MELT_OXIDE_IDEAL_SOLUTION_MODEL:
                status_reasons.append("declared_ideal_solution_activity")
            if not evidence_is_external:
                status_reasons.append(
                    "producer_self_reference_rejected"
                    if evidence_ref
                    else "external_activity_evidence_missing"
                )
            if warning and warning not in status_reasons:
                status_reasons.append(warning)

            is_status_bearing = bool(status_reasons)
            return SourceReactionActivity(
                component_id=declaration.component_id,
                value=value,
                verdict=(
                    ActivityVerdictKind.STATUS_BEARING_VALUE
                    if is_status_bearing
                    else ActivityVerdictKind.POINT
                ),
                bound_direction=None,
                reason=(
                    status_reasons[0]
                    if is_status_bearing
                    else "provider_reported_thermodynamic_activity"
                ),
                standard_state=declaration.standard_state,
                phase_assemblage_ref=None,
                chemical_potential_ref=None,
                state_fingerprint=state_fingerprint,
                solve_group_id=solve_group_id,
                provider=provider_id,
                authority=False,
                report_label=(
                    STATUS_BEARING_NOT_POINT if is_status_bearing else None
                ),
                detail="; ".join(status_reasons) if status_reasons else None,
                derivation={
                    "premise": (
                        "provider reports a in the exact declared standard state"
                    ),
                    "algebra": "a_source = a_reported",
                    "units": "activity is dimensionless",
                    "limiting_case": "pure endmember in its standard state has a=1",
                    "reported_activity_provenance": provenance,
                },
                evidence_ref=evidence_ref if evidence_is_external else None,
                evidence_tier=evidence_tier,
            )

        if measured_gamma is not None and mole_fraction is not None:
            # Point path for an independently supplied gamma (still diagnostic).
            if not is_declared_real_scalar(
                measured_gamma,
                allow_numeric_str=True,
            ) or not is_declared_real_scalar(
                mole_fraction,
                allow_numeric_str=True,
            ):
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.MISSING_EVIDENCE,
                    "measured_gamma and mole_fraction must be numeric",
                    standard_state=declaration.standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                )
            if mole_fraction < 0.0 or not math.isfinite(mole_fraction):
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.MISSING_EVIDENCE,
                    "mole_fraction must be finite and non-negative",
                    standard_state=declaration.standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                )
            if measured_gamma < 0.0 or not math.isfinite(measured_gamma):
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.MISSING_EVIDENCE,
                    "measured_gamma must be finite and non-negative",
                    standard_state=declaration.standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                )
            value = float(measured_gamma) * float(mole_fraction)
            return SourceReactionActivity(
                component_id=declaration.component_id,
                value=value,
                verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
                bound_direction=None,
                reason="measured_gamma_external_evidence_missing",
                standard_state=declaration.standard_state,
                phase_assemblage_ref=None,
                chemical_potential_ref=None,
                state_fingerprint=state_fingerprint,
                solve_group_id=solve_group_id,
                provider="measured_gamma",
                authority=False,
                report_label=STATUS_BEARING_NOT_POINT,
                detail="measured gamma lacks an external evidence reference",
                derivation={
                    "premise": "a = gamma * X under the declared standard state",
                    "algebra": f"a = {measured_gamma} * {mole_fraction}",
                    "units": "gamma and X dimensionless; a dimensionless",
                    "limiting_case": "gamma=1, X=1 ⇒ a=1",
                },
            )

        if magemin is None and thermoengine is None:
            if (
                declaration.allow_henrian_upper_bound
                and is_trace_parent_component(declaration.component_id)
            ):
                if temperature_K is None:
                    return _refusal(
                        declaration.component_id,
                        ActivityRefusalCode.MISSING_EVIDENCE,
                        "trace parent activity requires temperature_K",
                        standard_state=declaration.standard_state,
                        state_fingerprint=state_fingerprint,
                        solve_group_id=solve_group_id,
                    )
                return resolve_trace_parent_activity(
                    declaration.component_id,
                    temperature_K=temperature_K,
                    activity_exponent=activity_exponent,
                    standard_state=declaration.standard_state,
                    mole_fraction=mole_fraction,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                )
            if declaration.allow_henrian_upper_bound:
                return henrian_unknown_gamma_upper_bound(
                    component_id=declaration.component_id,
                    activity_exponent=activity_exponent,
                    standard_state=declaration.standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                    mole_fraction=mole_fraction,
                )
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.MISSING_EVIDENCE,
                "no assemblage/potential evidence and Henrian upper bound disabled",
                standard_state=declaration.standard_state,
                state_fingerprint=state_fingerprint,
                solve_group_id=solve_group_id,
            )

        if magemin is None or thermoengine is None:
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.MISSING_EVIDENCE,
                "both MAGEMin assemblage and ThermoEngine potentials are required "
                "for a matched chemical-potential activity point",
                standard_state=declaration.standard_state,
                state_fingerprint=state_fingerprint,
                solve_group_id=solve_group_id,
            )

        return self._resolve_matched_point(
            declaration,
            magemin=magemin,
            thermoengine=thermoengine,
            solve_group_id=solve_group_id,
        )

    def _resolve_matched_point(
        self,
        declaration: ActivityInputDeclaration,
        *,
        magemin: MageminAssemblageEvidence,
        thermoengine: ThermoEnginePotentialEvidence,
        solve_group_id: str | None,
    ) -> SourceReactionActivity:
        for evidence, label in ((magemin, "magemin"), (thermoengine, "thermoengine")):
            if evidence.timed_out:
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.TIMEOUT,
                    f"{label} call exceeded declared deadline "
                    f"({self.per_call_deadline_s}s)",
                    standard_state=declaration.standard_state,
                    solve_group_id=solve_group_id,
                )
            if evidence.crashed:
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.CRASH,
                    f"{label} call crashed",
                    standard_state=declaration.standard_state,
                    solve_group_id=solve_group_id,
                )
            if evidence.expired:
                return _refusal(
                    declaration.component_id,
                    ActivityRefusalCode.EXPIRED,
                    f"{label} evidence expired",
                    standard_state=declaration.standard_state,
                    solve_group_id=solve_group_id,
                )

        if not magemin.converged:
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.MISSING_EVIDENCE,
                "MAGEMin assemblage did not converge",
                standard_state=declaration.standard_state,
                solve_group_id=solve_group_id,
            )

        mapping = self._map.get(declaration.component_id)
        if mapping is None:
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.UNMAPPED_ENDMEMBER,
                f"no reviewed phase/endmember map for {declaration.component_id!r}",
                standard_state=declaration.standard_state,
                solve_group_id=solve_group_id,
            )

        if mapping.phase_id not in magemin.assemblage.phase_ids:
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.UNMAPPED_PHASE,
                f"phase {mapping.phase_id!r} absent from MAGEMin assemblage "
                f"{tuple(magemin.assemblage.phase_ids)!r}",
                standard_state=declaration.standard_state,
                solve_group_id=solve_group_id,
                phase_assemblage_ref=magemin.assemblage.fingerprint(),
            )

        if mapping.endmember_id not in magemin.assemblage.endmember_ids:
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.UNMAPPED_ENDMEMBER,
                f"endmember {mapping.endmember_id!r} absent from assemblage",
                standard_state=declaration.standard_state,
                solve_group_id=solve_group_id,
                phase_assemblage_ref=magemin.assemblage.fingerprint(),
            )

        state_fp = magemin.state.fingerprint()
        if thermoengine.state.fingerprint() != state_fp:
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.STATE_FINGERPRINT_MISMATCH,
                "ThermoEngine state fingerprint does not match MAGEMin state",
                standard_state=declaration.standard_state,
                solve_group_id=solve_group_id,
                phase_assemblage_ref=magemin.assemblage.fingerprint(),
                state_fingerprint=state_fp,
            )

        if thermoengine.assemblage_ref != magemin.assemblage.fingerprint():
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.ASSEMBLAGE_MISMATCH,
                "ThermoEngine assemblage_ref does not match MAGEMin assemblage",
                standard_state=declaration.standard_state,
                solve_group_id=solve_group_id,
                phase_assemblage_ref=magemin.assemblage.fingerprint(),
                state_fingerprint=state_fp,
            )

        if (
            thermoengine.standard_state.fingerprint()
            != declaration.standard_state.fingerprint()
        ):
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.STANDARD_STATE_MISMATCH,
                "ThermoEngine standard state is not commensurate with the "
                "activity_input declaration",
                standard_state=declaration.standard_state,
                solve_group_id=solve_group_id,
                phase_assemblage_ref=magemin.assemblage.fingerprint(),
                state_fingerprint=state_fp,
            )

        if thermoengine.component_id != declaration.component_id:
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.UNMAPPED_ENDMEMBER,
                "ThermoEngine component_id does not match declaration",
                standard_state=declaration.standard_state,
                solve_group_id=solve_group_id,
                phase_assemblage_ref=magemin.assemblage.fingerprint(),
                state_fingerprint=state_fp,
            )

        if thermoengine.independent_consistency_ok is False:
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.CONSISTENCY_GATE_FAILED,
                thermoengine.independent_consistency_note
                or "independent activity consistency gate failed",
                standard_state=declaration.standard_state,
                solve_group_id=solve_group_id,
                phase_assemblage_ref=magemin.assemblage.fingerprint(),
                state_fingerprint=state_fp,
            )

        try:
            activity = activity_from_chemical_potentials(
                thermoengine.mu_J_per_mol,
                thermoengine.mu0_J_per_mol,
                magemin.state.temperature_K,
            )
        except ValueError as exc:
            return _refusal(
                declaration.component_id,
                ActivityRefusalCode.NON_FINITE_POTENTIAL,
                str(exc),
                standard_state=declaration.standard_state,
                solve_group_id=solve_group_id,
                phase_assemblage_ref=magemin.assemblage.fingerprint(),
                state_fingerprint=state_fp,
            )

        mu_ref = (
            f"mu={thermoengine.mu_J_per_mol:.9g},"
            f"mu0={thermoengine.mu0_J_per_mol:.9g},"
            f"T={magemin.state.temperature_K:.9g}"
        )
        return SourceReactionActivity(
            component_id=declaration.component_id,
            value=activity,
            verdict=ActivityVerdictKind.POINT,
            bound_direction=None,
            reason="matched_magemin_thermoengine_chemical_potential",
            standard_state=declaration.standard_state,
            phase_assemblage_ref=magemin.assemblage.fingerprint(),
            chemical_potential_ref=mu_ref,
            state_fingerprint=state_fp,
            solve_group_id=solve_group_id,
            provider="condensed_phase_activity_provider",
            authority=False,
            derivation={
                "premise": "mu_i = mu_i0 + R T ln(a_i)",
                "algebra": "a_i = exp((mu_i - mu_i0)/(R T))",
                "units": "mu in J/mol; R in J/(mol·K); T in K; a dimensionless",
                "limiting_case": "mu_i = mu_i0 ⇒ a_i = 1",
                "R_J_per_mol_K": R_J_PER_MOL_K,
                "mu_J_per_mol": thermoengine.mu_J_per_mol,
                "mu0_J_per_mol": thermoengine.mu0_J_per_mol,
                "temperature_K": magemin.state.temperature_K,
                "mapped_phase_id": mapping.phase_id,
                "mapped_endmember_id": mapping.endmember_id,
            },
        )


def validation_row_may_certify(
    *,
    validation_status: str,
    activity: SourceReactionActivity | None = None,
) -> bool:
    """Never-certify ceiling for bounds and pending-validation rows.

    Owner O1 / progressive-validation ladder: ``pending_validation`` rows may
    evolve flagged and non-authoritative; they never certify. Upper bounds
    never certify. Even a validated point from this diagnostic seam stays
    non-authoritative until a later promotion epoch sets authority.
    """

    status = str(validation_status).strip().lower()
    if status in {"pending_validation", "pending"}:
        return False
    if activity is not None:
        if activity.verdict is not ActivityVerdictKind.POINT:
            return False
        if not activity.authority:
            return False
    return status == "validated" and activity is not None and activity.may_certify()


def _refusal(
    component_id: str,
    code: ActivityRefusalCode,
    detail: str,
    *,
    standard_state: StandardStateIdentity | None,
    solve_group_id: str | None,
    phase_assemblage_ref: str | None = None,
    state_fingerprint: str | None = None,
) -> SourceReactionActivity:
    return SourceReactionActivity(
        component_id=component_id,
        value=None,
        verdict=ActivityVerdictKind.REFUSAL,
        bound_direction=None,
        reason=code.value,
        standard_state=standard_state,
        phase_assemblage_ref=phase_assemblage_ref,
        chemical_potential_ref=None,
        state_fingerprint=state_fingerprint,
        solve_group_id=solve_group_id,
        provider="condensed_phase_activity_provider",
        authority=False,
        refusal_code=code,
        detail=detail,
    )


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()[:24]


_FEGLEY_GAMMA_TABLE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "vapour_rail"
    / "fegley2023_table2_gamma.json"
)
_PROXY_SET_EQUAL = re.compile(r"\bset\s*=", re.IGNORECASE)
_PROXY_INTERPOLATED = re.compile(r"interpolat", re.IGNORECASE)
# Oxide formula as printed in a gamma note: GeO2, SiO2, AsO1.5, CuO0.5.
_OXIDE_IN_NOTE = r"[A-Z][a-z]?\d*O\d*(?:\.\d+)?"
# g(this) = g(other), including a "log" between the equals and the second g.
_GAMMA_EQUALS_OTHER = re.compile(
    rf"g\s*\(\s*({_OXIDE_IN_NOTE})\s*\)\s*=\s*(?:log\s*)?g\s*\(\s*({_OXIDE_IN_NOTE})\s*\)",
    re.IGNORECASE,
)
# g(oxide), including the spaced ``g (AsO1.5)`` printed in Table 2.
_GAMMA_OF_OXIDE = re.compile(
    rf"g\s*\(\s*({_OXIDE_IN_NOTE})\s*\)",
    re.IGNORECASE,
)
_MOLECULAR_FRACTION_BASIS = "conventional_oxide_molecular"
_TRACE_HOMOLOGUE = {
    "Rb2O": "K2O",
    "Cs2O": "K2O",
    "Li2O": "Na2O",
    "Ga2O3": "Al2O3",
    "GeO2": "SiO2",
    "In2O3": "Ga2O3",
}
# Author's stated nominal: formula, a phrase that appears in that row's
# note, and the Fegley 2023 text that names it. Exactly one matching row
# is the nominal. This is not a residual ranking.
_SOURCE_STATED_NOMINAL: tuple[tuple[str, str, str], ...] = (
    ("Cu2O", "Altman (1978)", "fegley2023:1636-1641"),
    ("Cs2O", "set = g(Na2O) FactSage", "fegley2023:1398-1400"),
)
# Whole phase tokens only. ``liquid`` matches ``l``; a longer string that
# merely contains one of these tokens does not.
_PHASE_TOKEN_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"liquid", "l"}),
    frozenset({"solid", "s", "cr"}),
)
_FEGLEY_GAMMA_CACHE: dict[str, Any] | None = None


def is_trace_parent_component(component_id: str) -> bool:
    """True for a generator parent oxide or its activity-basis spelling."""

    bare = ledger_component_key(str(component_id))
    return bare in set(LIQUID_PARENT_OXIDE.values()) or bare in set(
        ACTIVITY_BASIS.values()
    )


def coefficient_formula(component_id: str) -> str:
    """Conventional oxide formula whose Table 2 rows answer this component."""

    bare = ledger_component_key(str(component_id))
    if bare in set(LIQUID_PARENT_OXIDE.values()) or bare in _TRACE_HOMOLOGUE:
        return bare
    for element, basis in ACTIVITY_BASIS.items():
        if bare == basis:
            return LIQUID_PARENT_OXIDE[element]
    return bare


def _proxy_notes(notes: str) -> bool:
    """True when the row's origin text borrows another component's gamma.

    ``set =`` and interpolation wording are proxies. So is an equality of
    one component's gamma to another's, read from the whole note:
    ``g(GeO2) = g(SiO2) from FactSage``. A numeric assignment of this
    component's own gamma (``g(GeO2) = 7.4``, ``Set log g(B2O3) = -3.2``)
    is not that equality.
    """

    if _PROXY_SET_EQUAL.search(notes) is not None:
        return True
    if _PROXY_INTERPOLATED.search(notes) is not None:
        return True
    for match in _GAMMA_EQUALS_OTHER.finditer(notes):
        if match.group(1).casefold() != match.group(2).casefold():
            return True
    return False


def _component_basis_derived(row: Mapping[str, Any]) -> bool:
    """True when the note assigns the gamma to a different oxide.

    ``g(AsO1.5)`` on an As2O3 row is that case: Table 2 stores some
    M2O3 rows on the one-cation component and some not. A melt system
    in the note (``CMAS+FeO``, a ternary) does not assign the gamma.
    """

    formula = str(row.get("formula") or "")
    notes = str(row.get("notes_as_printed") or "")
    for match in _GAMMA_OF_OXIDE.finditer(notes):
        if match.group(1).casefold() != formula.casefold():
            return True
    return False


def _optional_row_text(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_gamma_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    notes = str(raw.get("notes_as_printed") or "")
    origin = raw.get("origin")
    if origin not in {"published", "proxy_estimate"}:
        origin = "proxy_estimate" if _proxy_notes(notes) else "published"
    band = raw.get("validity_range_K")
    if band is not None:
        band = [float(band[0]), float(band[1])]
    printed = raw.get("standard_state_as_printed")
    stated_convention = raw.get("stated_convention")
    stated_phase = raw.get("stated_phase")
    return {
        "source_row_id": str(raw.get("source_row_id") or ""),
        "formula": str(raw.get("formula") or ""),
        "A": str(raw.get("A")),
        "B": str(raw.get("B")),
        "validity_range_K": band,
        "notes_as_printed": notes,
        "origin": origin,
        "standard_state_as_printed": None if printed is None else str(printed),
        "stated_convention": (
            None if not stated_convention else str(stated_convention)
        ),
        "stated_phase": None if not stated_phase else str(stated_phase),
        "mole_fraction_basis": _optional_row_text(raw, "mole_fraction_basis"),
        "stated_basis_cite": _optional_row_text(raw, "stated_basis_cite"),
    }


def load_fegley2023_gamma_table() -> dict[str, Any]:
    """Runtime Table 2 rows. Provenance names the extract and its sha256."""

    global _FEGLEY_GAMMA_CACHE
    if _FEGLEY_GAMMA_CACHE is None:
        _FEGLEY_GAMMA_CACHE = json.loads(
            _FEGLEY_GAMMA_TABLE.read_text(encoding="utf-8")
        )
    return _FEGLEY_GAMMA_CACHE


def _gamma_rows(rows: Sequence[Mapping[str, Any]] | None) -> tuple[dict[str, Any], ...]:
    source = (
        load_fegley2023_gamma_table()["rows"] if rows is None else rows
    )
    return tuple(_normalize_gamma_row(row) for row in source)


def _gamma_at(row: Mapping[str, Any], temperature_K: float) -> float:
    # log10 γ = A + B/T, T in K. Owner of the Table 2 numeric evaluation.
    exponent = float(row["A"]) + float(row["B"]) / float(temperature_K)
    gamma = 10.0 ** exponent
    if not math.isfinite(gamma) or gamma <= 0.0:
        raise ValueError(
            f"non-finite gamma for {row.get('source_row_id')!r} at {temperature_K}"
        )
    return gamma


def _row_extrapolated(row: Mapping[str, Any], temperature_K: float) -> bool:
    band = row.get("validity_range_K")
    if band is None:
        return False
    return float(temperature_K) < float(band[0]) or float(temperature_K) > float(
        band[1]
    )


def _extrapolation_notice(
    row: Mapping[str, Any], temperature_K: float
) -> dict[str, Any] | None:
    if not _row_extrapolated(row, temperature_K):
        return None
    band = row.get("validity_range_K")
    return {
        "reason": "temperature outside the fit validity range",
        "authority_level": "extrapolated",
        "certified_band": {
            "temperature_K": [float(band[0]), float(band[1])],
        },
    }


def _candidate_record(
    row: Mapping[str, Any], temperature_K: float
) -> dict[str, Any]:
    return {
        "source_row_id": row["source_row_id"],
        "gamma": _gamma_at(row, temperature_K),
        "origin": row["origin"],
        "extrapolated": _row_extrapolated(row, temperature_K),
        "validity_range_K": row.get("validity_range_K"),
        "notes_as_printed": row["notes_as_printed"],
    }


def _select_gamma_rows(
    formula: str, rows: Sequence[Mapping[str, Any]]
) -> tuple[str, tuple[Mapping[str, Any], ...]]:
    matched = tuple(row for row in rows if row["formula"] == formula)
    published = tuple(row for row in matched if row["origin"] == "published")
    proxies = tuple(row for row in matched if row["origin"] == "proxy_estimate")
    if len(published) == 1:
        return "one", published
    if len(published) > 1:
        return "many", published
    if len(proxies) == 1:
        return "one", proxies
    if len(proxies) > 1:
        return "many", proxies
    return "none", ()


def _stated_band(row: Mapping[str, Any]) -> tuple[float, float] | None:
    band = row.get("validity_range_K")
    if not isinstance(band, (list, tuple)) or len(band) != 2:
        return None
    low = float(band[0])
    high = float(band[1])
    if not math.isfinite(low) or not math.isfinite(high) or high < low:
        return None
    return low, high


def _band_distance(band: tuple[float, float], temperature_K: float) -> float:
    low, high = band
    if temperature_K < low:
        return low - temperature_K
    if temperature_K > high:
        return temperature_K - high
    return 0.0


def _select_banded_row(
    rows: Sequence[Mapping[str, Any]], temperature_K: float
) -> Mapping[str, Any] | None:
    """The stated band that covers T, otherwise the unique nearest band.

    A missing validity range is not a band and is never chosen. A point
    temperature named only in the notes (``1673 K point``) is not a band
    either: the note is not parsed into ``validity_range_K``. The lowest
    residual is not a selector. Several covering bands, or two bands at the
    same distance, means this rule does not apply.
    """

    covering: list[Mapping[str, Any]] = []
    banded: list[tuple[float, str, Mapping[str, Any]]] = []
    for row in rows:
        band = _stated_band(row)
        if band is None:
            continue
        distance = _band_distance(band, temperature_K)
        banded.append((distance, str(row.get("source_row_id") or ""), row))
        if distance == 0.0:
            covering.append(row)
    if len(covering) == 1:
        return covering[0]
    if len(covering) > 1:
        return None
    if not banded:
        return None
    banded.sort(key=lambda item: (item[0], item[1]))
    if len(banded) > 1 and banded[0][0] == banded[1][0]:
        return None
    return banded[0][2]


def _stated_nominal_row(
    formula: str, rows: Sequence[Mapping[str, Any]]
) -> tuple[Mapping[str, Any], str] | None:
    """The unique row whose note contains the source's stated nominal.

    The scan includes proxies. A published-only filter would hide a
    nominal the author placed on a proxy row. Several matches, or a note
    that contains two recorded phrases, is not a nominal.
    """

    phrases = tuple(item for item in _SOURCE_STATED_NOMINAL if item[0] == formula)
    if not phrases:
        return None
    matched: list[tuple[Mapping[str, Any], str]] = []
    for row in rows:
        notes = str(row.get("notes_as_printed") or "")
        hits = tuple(cite for _formula, phrase, cite in phrases if phrase in notes)
        if len(hits) > 1:
            return None
        if len(hits) == 1:
            matched.append((row, hits[0]))
    if len(matched) != 1:
        return None
    return matched[0]


def _geometric_mean(values: Sequence[float]) -> float:
    ordered = tuple(sorted(float(value) for value in values))
    return math.exp(sum(math.log(value) for value in ordered) / len(ordered))


def _extreme_gamma_row(
    rows: Sequence[Mapping[str, Any]],
    temperature_K: float,
    *,
    high: bool,
) -> Mapping[str, Any]:
    """Row at one edge of the envelope. A tie keeps the smaller source id."""

    def key(row: Mapping[str, Any]) -> tuple[float, str]:
        gamma = _gamma_at(row, temperature_K)
        identity = str(row.get("source_row_id") or "")
        return (-gamma if high else gamma, identity)

    return min(rows, key=key)


def _envelope_ln_band(
    *,
    parent_formula: str,
    parent_gamma: float,
    gamma_min: float,
    gamma_max: float,
    mole_fraction: float | None,
    single_cation: bool,
) -> tuple[float, float] | None:
    """Activity-ln offsets of the candidate envelope around the chosen value.

    The offsets enclose zero. They are absent when the activity is not a
    positive finite number. An alias uses ``a_single = a_parent ** (1/c)``.
    The envelope gammas stay on the parent-row basis. This is an activity
    band, not a pressure band: the activity exponent does not flip it.
    """

    if (
        mole_fraction is None
        or parent_gamma <= 0.0
        or gamma_min <= 0.0
        or gamma_max <= 0.0
    ):
        return None
    try:
        fraction = float(mole_fraction)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(fraction) or fraction <= 0.0:
        return None

    def activity(gamma: float) -> float | None:
        if single_cation:
            pair = activities_from_molecular_henrian_row(
                parent_formula, gamma, fraction
            )
            if pair is None:
                return None
            chosen = pair[1]
        else:
            chosen = gamma * fraction
        if not math.isfinite(chosen) or chosen <= 0.0:
            return None
        return chosen

    selected = activity(parent_gamma)
    low = activity(gamma_min)
    high = activity(gamma_max)
    if selected is None or low is None or high is None:
        return None
    lower = math.log(low / selected)
    upper = math.log(high / selected)
    if not (math.isfinite(lower) and math.isfinite(upper) and lower <= 0.0 <= upper):
        return None
    return (lower, upper)


def _annotate(
    answer: SourceReactionActivity, **extra: Any
) -> SourceReactionActivity:
    derivation = dict(answer.derivation)
    derivation.update(extra)
    return replace(answer, derivation=derivation)


def _activity_value(
    gamma: float | None, mole_fraction: float | None
) -> float | None:
    if gamma is None or mole_fraction is None:
        return None
    value = float(gamma) * float(mole_fraction)
    if not math.isfinite(value) or value < 0.0:
        return None
    return value


def _ladder_result(
    *,
    component_id: str,
    standard_state: StandardStateIdentity,
    state_fingerprint: str | None,
    solve_group_id: str | None,
    rung: int,
    gamma: float | None,
    mole_fraction: float | None,
    verdict: ActivityVerdictKind,
    flag: str,
    reason: str,
    source_row_id: str | None,
    source_row_ids: tuple[str, ...],
    origin: str | None,
    homologue: str | None,
    coefficient_formula: str | None,
    extrapolation_notice: Mapping[str, Any] | None,
    candidate_rows: Sequence[Mapping[str, Any]],
    tier: ActivityTier,
    target_flag: str | None = None,
    target_rung: int | None = None,
) -> SourceReactionActivity:
    status_bearing = verdict is not ActivityVerdictKind.POINT
    return SourceReactionActivity(
        component_id=component_id,
        value=_activity_value(gamma, mole_fraction),
        verdict=verdict,
        bound_direction=None,
        reason=reason,
        standard_state=standard_state,
        phase_assemblage_ref=None,
        chemical_potential_ref=None,
        state_fingerprint=state_fingerprint,
        solve_group_id=solve_group_id,
        provider="trace_parent_activity_ladder",
        authority=False,
        report_label=STATUS_BEARING_NOT_POINT if status_bearing else None,
        tier=tier,
        model_row_id=source_row_id,
        evidence_ref=source_row_id,
        evidence_tier=origin or flag,
        derivation={
            "rung": rung,
            "gamma": gamma,
            "source_row_id": source_row_id,
            "source_row_ids": source_row_ids,
            "flag": flag,
            "origin": origin,
            "homologue": homologue,
            "coefficient_formula": coefficient_formula,
            "extrapolation_notice": (
                None
                if extrapolation_notice is None
                else dict(extrapolation_notice)
            ),
            "candidate_rows": tuple(dict(row) for row in candidate_rows),
            "target_flag": target_flag,
            "target_rung": target_rung,
            "algebra": "log10 gamma = A + B/T",
        },
    )


def _same_phase_token(left: str, right: str) -> bool:
    """True when both strings are the same phase token.

    Membership is exact. ``"l" in "liquid"`` is a substring test and is
    not used.
    """

    first = str(left).strip()
    second = str(right).strip()
    if not first or not second:
        return False
    if first == second:
        return True
    return any(
        first in group and second in group for group in _PHASE_TOKEN_GROUPS
    )


def _closed_phase_token(phase: object) -> str | None:
    """Phase token the coefficient scorer compares.

    ``liquid`` and ``l`` close to ``l``. ``solid``, ``s``, and ``cr``
    close to ``cr``. The match is group membership. A string that only
    contains one of those tokens is not a phase.
    """

    text = str(phase).strip()
    if not text:
        return None
    for group in _PHASE_TOKEN_GROUPS:
        if text not in group:
            continue
        closed = group & {"l", "cr"}
        if len(closed) == 1:
            return next(iter(closed))
    return None


def _basis_annotation(
    *,
    row: Mapping[str, Any] | None,
    standard_state: StandardStateIdentity,
    established: bool,
) -> dict[str, Any]:
    """Source and target basis carried with one ladder answer.

    A printed Table 2 phrase is the source text. It is not a convention
    and it is not a phase. Stated convention and phase are the source
    when the row carries them, including when the table also prints that
    the row itself did not state a standard state. With neither, an
    established row stands on the caller's identity.
    """

    printed = None if row is None else row.get("standard_state_as_printed")
    stated_convention = None if row is None else row.get("stated_convention")
    stated_phase = None if row is None else row.get("stated_phase")
    derived = False if row is None else _component_basis_derived(row)
    if stated_convention and stated_phase and not derived:
        source_convention = stated_convention
        source_phase = stated_phase
    elif printed is not None:
        source_convention = None
        source_phase = None
    elif established:
        source_convention = standard_state.convention
        source_phase = standard_state.phase
    else:
        source_convention = None
        source_phase = None
    return {
        "basis_established": established,
        "component_basis_derived": derived,
        "row_formula": None if row is None else str(row["formula"]),
        "standard_state_as_printed": None if printed is None else printed,
        "source_convention": source_convention,
        "source_phase": source_phase,
        "mole_fraction_basis": None if row is None else row.get("mole_fraction_basis"),
        "stated_basis_cite": None if row is None else row.get("stated_basis_cite"),
        "target_convention": standard_state.convention,
        "target_phase": standard_state.phase,
    }


def _standard_state_established(
    row: Mapping[str, Any], standard_state: StandardStateIdentity
) -> bool:
    """True when the row's basis is the caller's standard state.

    A printed Table 2 phrase is not a typed convention. ``not stated in
    Table 2 row`` and ``liquid standard state`` do not by themselves
    establish ``raoultian_pure_endmember``. Stated convention and phase
    do, including beside that printed phrase, when they match the caller
    and the mole-fraction basis is the conventional-oxide molecular one
    or is absent. A note that assigns the gamma to a different oxide
    does not establish the row formula's component.
    A row with neither a printed phrase nor stated fields stands on the
    caller's identity.
    """

    if _component_basis_derived(row):
        return False
    stated_convention = row.get("stated_convention")
    stated_phase = row.get("stated_phase")
    if stated_convention or stated_phase:
        if not stated_convention or not stated_phase:
            return False
        fraction_basis = row.get("mole_fraction_basis")
        if fraction_basis not in (None, _MOLECULAR_FRACTION_BASIS):
            return False
        return (
            str(stated_convention) == standard_state.convention
            and _same_phase_token(str(stated_phase), standard_state.phase)
        )
    if row.get("standard_state_as_printed") is not None:
        return False
    return True


def _mark_derived_spelling(
    answer: SourceReactionActivity,
) -> SourceReactionActivity:
    """An activity-basis conversion is not the stored row's component.

    The parent row may be a point on the paper's basis. This spelling
    keeps the converted activity and does not claim that standard state.
    A value that was a point becomes ``component_basis_derived``. A
    proxy, an extrapolation, or a nominal keeps its own flag.
    """

    answer = _annotate(
        answer,
        component_basis_derived=True,
        basis_established=False,
        source_convention=None,
        source_phase=None,
    )
    if answer.verdict is not ActivityVerdictKind.POINT:
        return answer
    answer = replace(
        answer,
        verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
        bound_direction=None,
        reason="component_basis_derived",
        report_label=STATUS_BEARING_NOT_POINT,
    )
    return _annotate(answer, flag="component_basis_derived")


def _from_selected_row(
    *,
    component_id: str,
    row: Mapping[str, Any],
    temperature_K: float,
    mole_fraction: float | None,
    standard_state: StandardStateIdentity,
    state_fingerprint: str | None,
    solve_group_id: str | None,
    coefficient_formula: str | None,
) -> SourceReactionActivity:
    gamma = _gamma_at(row, temperature_K)
    notice = _extrapolation_notice(row, temperature_K)
    published = row["origin"] == "published"
    established = _standard_state_established(row, standard_state)
    derived = _component_basis_derived(row)
    if notice is not None:
        flag = "extrapolated"
        verdict = ActivityVerdictKind.STATUS_BEARING_VALUE
        reason = (
            "published_gamma_extrapolated"
            if published
            else "proxy_gamma_extrapolated"
        )
    elif published and established:
        flag = "published"
        verdict = ActivityVerdictKind.POINT
        reason = "published_gamma"
    elif published and derived:
        flag = "component_basis_derived"
        verdict = ActivityVerdictKind.STATUS_BEARING_VALUE
        reason = "component_basis_derived"
    elif published:
        flag = "standard_state_basis_unestablished"
        verdict = ActivityVerdictKind.STATUS_BEARING_VALUE
        reason = "standard_state_basis_unestablished"
    else:
        flag = "proxy_estimate"
        verdict = ActivityVerdictKind.STATUS_BEARING_VALUE
        reason = "proxy_gamma_estimate"
    answer = _ladder_result(
        component_id=component_id,
        standard_state=standard_state,
        state_fingerprint=state_fingerprint,
        solve_group_id=solve_group_id,
        rung=2,
        gamma=gamma,
        mole_fraction=mole_fraction,
        verdict=verdict,
        flag=flag,
        reason=reason,
        source_row_id=str(row["source_row_id"]),
        source_row_ids=(str(row["source_row_id"]),),
        origin=str(row["origin"]),
        homologue=None,
        coefficient_formula=coefficient_formula,
        extrapolation_notice=notice,
        candidate_rows=(_candidate_record(row, temperature_K),),
        tier=ActivityTier.B,
    )
    return _annotate(
        answer,
        **_basis_annotation(
            row=row,
            standard_state=standard_state,
            established=established,
        ),
    )


def _rung4(
    *,
    component_id: str,
    activity_exponent: float,
    standard_state: StandardStateIdentity,
    mole_fraction: float | None,
    state_fingerprint: str | None,
    solve_group_id: str | None,
    coefficient_formula: str | None,
) -> SourceReactionActivity:
    bound = henrian_unknown_gamma_upper_bound(
        component_id=component_id,
        activity_exponent=activity_exponent,
        standard_state=standard_state,
        mole_fraction=mole_fraction,
        state_fingerprint=state_fingerprint,
        solve_group_id=solve_group_id,
        gamma_anchor=1.0,
    )
    if bound.verdict is ActivityVerdictKind.REFUSAL:
        return bound
    annotated = _annotate(
        bound,
        rung=4,
        gamma=1.0,
        source_row_id=None,
        source_row_ids=(),
        flag="henrian_gamma_unmeasured",
        origin=None,
        homologue=None,
        coefficient_formula=coefficient_formula,
        extrapolation_notice=None,
        candidate_rows=(),
        target_flag=None,
        target_rung=None,
        algebra="gamma = 1; a <= X when the activity exponent is non-negative",
        **_basis_annotation(
            row=None,
            standard_state=standard_state,
            established=False,
        ),
    )
    return replace(annotated, tier=ActivityTier.C)


def _retag_homologue(
    *,
    component_id: str,
    homologue: str,
    followed: SourceReactionActivity,
    mole_fraction: float | None,
    standard_state: StandardStateIdentity,
    state_fingerprint: str | None,
    solve_group_id: str | None,
    coefficient_formula: str | None,
) -> SourceReactionActivity:
    derivation = followed.derivation
    # The followed row's basis is that other component's. It does not
    # establish this parent's standard state.
    answer = _ladder_result(
        component_id=component_id,
        standard_state=standard_state,
        state_fingerprint=state_fingerprint,
        solve_group_id=solve_group_id,
        rung=3,
        gamma=derivation.get("gamma"),
        mole_fraction=mole_fraction,
        verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
        flag="homologue",
        reason="homologue_gamma",
        source_row_id=derivation.get("source_row_id"),
        source_row_ids=tuple(derivation.get("source_row_ids") or ()),
        origin=derivation.get("origin"),
        homologue=homologue,
        coefficient_formula=coefficient_formula,
        extrapolation_notice=derivation.get("extrapolation_notice"),
        candidate_rows=tuple(derivation.get("candidate_rows") or ()),
        tier=ActivityTier.C,
        target_flag=derivation.get("flag"),
        target_rung=derivation.get("rung"),
    )
    return _annotate(
        answer,
        basis_established=False,
        row_formula=derivation.get("row_formula"),
        standard_state_as_printed=derivation.get("standard_state_as_printed"),
        source_convention=derivation.get("source_convention"),
        source_phase=derivation.get("source_phase"),
        target_convention=standard_state.convention,
        target_phase=standard_state.phase,
    )


def resolve_trace_parent_activity(
    component_id: str,
    *,
    temperature_K: float,
    activity_exponent: float,
    standard_state: StandardStateIdentity,
    mole_fraction: float | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    state_fingerprint: str | None = None,
    solve_group_id: str | None = None,
) -> SourceReactionActivity:
    """Resolve one trace-parent gamma on the existing verdict types.

    Rung 1 (openimcc N-parent pack) is absent. Rung 2 is a Table 2 fit.
    One published row is that gamma on the row's own component. An
    activity-basis spelling uses the row only after the pure-liquid
    reference conversion; the lookup name is not the conversion, and that
    spelling is not a published point. A printed standard-state phrase is
    not a typed basis. Stated convention, phase, and the
    conventional-oxide molecular fraction establish the caller when they
    match, including beside that phrase. A note that assigns the gamma to
    a different oxide is not a published point. One proxy row is a flagged
    estimate. Several rows of
    one origin are the row whose stated band covers T, or the unique
    nearest band outside that range (flagged extrapolated). A point
    temperature in the notes is not a band. The lowest residual is not a
    selector. When that rule does not select and the source names a
    nominal row, the nominal is a flagged rung-2 value and the candidate
    envelope is its uncertainty. Published rows with no nominal are a
    bound at the extreme gamma when every candidate lies on one side of
    1, otherwise the geometric mean (``envelope_midpoint``). A unity
    Henrian bound is not emitted against those measured rows. Otherwise
    rung 3 follows the homologue when the target itself resolved at rung
    2 or 3. Rung 4 is the unmeasured-gamma unity bound. Every non-refusal
    result carries a numeric gamma.
    """

    try:
        if not is_declared_real_scalar(temperature_K, allow_numeric_str=True):
            raise TypeError
        temperature = float(temperature_K)
    except (TypeError, ValueError):
        temperature = math.nan
    if not math.isfinite(temperature) or temperature <= 0.0:
        return _refusal(
            component_id,
            ActivityRefusalCode.MISSING_EVIDENCE,
            "trace parent activity requires a finite positive temperature_K",
            standard_state=standard_state,
            state_fingerprint=state_fingerprint,
            solve_group_id=solve_group_id,
        )
    try:
        table = _gamma_rows(rows)
    except (TypeError, ValueError, KeyError) as exc:
        return _refusal(
            component_id,
            ActivityRefusalCode.MISSING_EVIDENCE,
            f"trace parent gamma table is invalid: {exc}",
            standard_state=standard_state,
            state_fingerprint=state_fingerprint,
            solve_group_id=solve_group_id,
        )

    bare = ledger_component_key(str(component_id))
    formula = coefficient_formula(bare)
    alias = formula if formula != bare else None

    def _finish(answer: SourceReactionActivity) -> SourceReactionActivity:
        if answer.verdict is ActivityVerdictKind.REFUSAL:
            return answer
        if answer.component_id != component_id or (
            alias is not None and answer.derivation.get("coefficient_formula") != alias
        ):
            answer = replace(answer, component_id=component_id)
            if alias is not None:
                answer = _annotate(answer, coefficient_formula=alias)
        return answer

    def _apply_henrian_gamma(
        current: str, parent_gamma: float
    ) -> tuple[float, float | None] | None:
        """Stored coefficient and activity for one parent-basis gamma.

        An alias of the original request stores the pure-liquid reference
        coefficient and ``a_single = a_parent ** (1/c)``. Any other
        component stores the parent gamma and ``gamma * X``. None means
        the alias relationship is not established.
        """

        if alias is None or current != formula:
            return float(parent_gamma), _activity_value(parent_gamma, mole_fraction)
        converted = pure_liquid_reference_coefficient(
            row_formula=current,
            requested_formula=bare,
            row_gamma=float(parent_gamma),
        )
        if converted is None:
            return None
        if mole_fraction is None:
            return converted, None
        pair = activities_from_molecular_henrian_row(
            current, float(parent_gamma), float(mole_fraction)
        )
        if pair is None:
            return None
        return converted, pair[1]

    def _accept_row_basis(
        current: str, row: Mapping[str, Any]
    ) -> SourceReactionActivity | None:
        """Keep a selected row, converting an activity-basis spelling.

        Conversion runs only on the lookup formula of the original
        request (InO1.5 reads In2O3). A homologue hop is a different
        element and is not cation-converted. None means the component
        relationship is not established: the caller must not reuse the
        row gamma.
        """

        answer = _from_selected_row(
            component_id=component_id,
            row=row,
            temperature_K=temperature,
            mole_fraction=mole_fraction,
            standard_state=standard_state,
            state_fingerprint=state_fingerprint,
            solve_group_id=solve_group_id,
            coefficient_formula=alias,
        )
        if alias is None or current != formula:
            return answer
        gamma = answer.derivation.get("gamma")
        if not isinstance(gamma, (int, float)):
            return None
        applied = _apply_henrian_gamma(current, float(gamma))
        if applied is None:
            return None
        converted, value = applied
        # ln_value is derived from value. Clearing it makes __post_init__
        # recompute the logarithm of the converted activity.
        answer = replace(
            _annotate(answer, gamma=converted, coefficient_formula=current),
            component_id=component_id,
            value=value,
            ln_value=None,
        )
        return _mark_derived_spelling(answer)

    def _with_candidate_envelope(
        answer: SourceReactionActivity,
        *,
        rows: Sequence[Mapping[str, Any]],
        parent_gamma: float,
        flag: str,
        reason: str,
        verdict: ActivityVerdictKind,
        bound_direction: BoundDirection | None,
        report_label: str,
        source_row_id: str | None,
        single_cation: bool,
        nominal_cite: str | None = None,
    ) -> SourceReactionActivity:
        """Attach the candidate envelope. The chosen gamma stays put.

        Envelope min and max are parent-row coefficients. The activity
        bound is not the unmeasured unity bound, so a row-level
        extrapolation notice is not the reason for this answer.
        """

        ordered = tuple(
            sorted(rows, key=lambda row: str(row.get("source_row_id") or ""))
        )
        records = tuple(_candidate_record(row, temperature) for row in ordered)
        gammas = tuple(float(record["gamma"]) for record in records)
        extra: dict[str, Any] = {
            "flag": flag,
            "gamma_envelope_min": min(gammas),
            "gamma_envelope_max": max(gammas),
            "source_row_id": source_row_id,
            "source_row_ids": tuple(str(row["source_row_id"]) for row in ordered),
            "candidate_rows": records,
            "extrapolation_notice": None,
        }
        if nominal_cite is not None:
            extra["nominal_cite"] = nominal_cite
        ln_band = _envelope_ln_band(
            parent_formula=str(ordered[0]["formula"]),
            parent_gamma=parent_gamma,
            gamma_min=min(gammas),
            gamma_max=max(gammas),
            mole_fraction=mole_fraction,
            single_cation=single_cation,
        )
        annotated = _annotate(answer, **extra)
        return replace(
            annotated,
            verdict=verdict,
            bound_direction=bound_direction,
            reason=reason,
            report_label=report_label,
            model_row_id=source_row_id,
            evidence_ref=source_row_id,
            ln_band=ln_band,
        )

    def _published_envelope(
        current: str, group: Sequence[Mapping[str, Any]]
    ) -> SourceReactionActivity | None:
        """Bound or midpoint for published rows the band rule did not select.

        Every candidate above 1 is a lower bound at the minimum gamma.
        Every candidate below 1 is an upper bound at the maximum gamma.
        A candidate on the other side of 1, or equal to 1, is the
        geometric mean of the endpoints, flagged ``envelope_midpoint``. Proxy-only groups
        are not this rule.
        """

        if not group or any(row["origin"] != "published" for row in group):
            return None
        gammas = tuple(_gamma_at(row, temperature) for row in group)
        single_cation = alias is not None and current == formula
        if all(gamma > 1.0 for gamma in gammas):
            row = _extreme_gamma_row(group, temperature, high=False)
            accepted = _accept_row_basis(current, row)
            if accepted is None:
                return None
            return _with_candidate_envelope(
                accepted,
                rows=group,
                parent_gamma=_gamma_at(row, temperature),
                flag="envelope_lower_bound",
                reason="envelope_lower_bound",
                verdict=ActivityVerdictKind.LOWER_BOUND,
                bound_direction=BoundDirection.LOWER,
                report_label=LOWER_BOUND_NOT_POINT,
                source_row_id=str(row["source_row_id"]),
                single_cation=single_cation,
            )
        if all(gamma < 1.0 for gamma in gammas):
            row = _extreme_gamma_row(group, temperature, high=True)
            accepted = _accept_row_basis(current, row)
            if accepted is None:
                return None
            return _with_candidate_envelope(
                accepted,
                rows=group,
                parent_gamma=_gamma_at(row, temperature),
                flag="envelope_upper_bound",
                reason="envelope_upper_bound",
                verdict=ActivityVerdictKind.UPPER_BOUND,
                bound_direction=BoundDirection.UPPER,
                report_label=BOUND_NOT_POINT,
                source_row_id=str(row["source_row_id"]),
                single_cation=single_cation,
            )
        geomean = _geometric_mean((min(gammas), max(gammas)))
        applied = _apply_henrian_gamma(current, geomean)
        if applied is None:
            return None
        stored_gamma, value = applied
        answer = _ladder_result(
            component_id=component_id,
            standard_state=standard_state,
            state_fingerprint=state_fingerprint,
            solve_group_id=solve_group_id,
            rung=2,
            gamma=stored_gamma,
            mole_fraction=None,
            verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
            flag="envelope_midpoint",
            reason="envelope_midpoint",
            source_row_id=None,
            source_row_ids=(),
            origin="published",
            homologue=None,
            coefficient_formula=formula if single_cation else None,
            extrapolation_notice=None,
            candidate_rows=(),
            tier=ActivityTier.B,
        )
        answer = replace(answer, value=value, ln_value=None)
        if single_cation:
            # The mean was converted onto the one-cation spelling.
            answer = _mark_derived_spelling(answer)
            basis = {}
        elif all(
            _standard_state_established(row, standard_state) for row in group
        ):
            representative = min(
                group, key=lambda row: str(row.get("source_row_id") or "")
            )
            basis = _basis_annotation(
                row=representative,
                standard_state=standard_state,
                established=True,
            )
        else:
            basis = _basis_annotation(
                row=None,
                standard_state=standard_state,
                established=False,
            )
        answer = _annotate(
            answer,
            **basis,
            algebra="geometric mean of the published gamma envelope endpoints",
        )
        return _with_candidate_envelope(
            answer,
            rows=group,
            parent_gamma=geomean,
            flag="envelope_midpoint",
            reason="envelope_midpoint",
            verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
            bound_direction=None,
            report_label=STATUS_BEARING_NOT_POINT,
            source_row_id=None,
            single_cation=single_cation,
        )

    def _walk(current: str, seen: frozenset[str]) -> SourceReactionActivity:
        if current in seen:
            return _rung4(
                component_id=component_id,
                activity_exponent=activity_exponent,
                standard_state=standard_state,
                mole_fraction=mole_fraction,
                state_fingerprint=state_fingerprint,
                solve_group_id=solve_group_id,
                coefficient_formula=alias,
            )
        matched = tuple(row for row in table if row["formula"] == current)
        kind, group = _select_gamma_rows(current, table)
        try:
            if kind == "many":
                chosen = _select_banded_row(group, temperature)
                if chosen is not None:
                    accepted = _accept_row_basis(current, chosen)
                    if accepted is not None:
                        return accepted
            nominal = _stated_nominal_row(current, matched)
            if nominal is not None:
                row, cite = nominal
                accepted = _accept_row_basis(current, row)
                if accepted is not None:
                    return _with_candidate_envelope(
                        accepted,
                        rows=matched,
                        parent_gamma=_gamma_at(row, temperature),
                        flag="source_stated_nominal",
                        reason="source_stated_nominal",
                        verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
                        bound_direction=None,
                        report_label=STATUS_BEARING_NOT_POINT,
                        source_row_id=str(row["source_row_id"]),
                        single_cation=alias is not None and current == formula,
                        nominal_cite=cite,
                    )
            if kind == "one":
                accepted = _accept_row_basis(current, group[0])
                if accepted is not None:
                    return accepted
            if kind == "many":
                enveloped = _published_envelope(current, group)
                if enveloped is not None:
                    return enveloped
        except (TypeError, ValueError) as exc:
            return _refusal(
                component_id,
                ActivityRefusalCode.MISSING_EVIDENCE,
                f"trace parent gamma row is invalid: {exc}",
                standard_state=standard_state,
                state_fingerprint=state_fingerprint,
                solve_group_id=solve_group_id,
            )
        target = _TRACE_HOMOLOGUE.get(current)
        if target is not None:
            followed = _walk(target, seen | {current})
            if followed.verdict is ActivityVerdictKind.REFUSAL:
                return followed
            rung = followed.derivation.get("rung")
            if rung in {2, 3}:
                answer = _retag_homologue(
                    component_id=component_id,
                    homologue=target,
                    followed=followed,
                    mole_fraction=mole_fraction,
                    standard_state=standard_state,
                    state_fingerprint=state_fingerprint,
                    solve_group_id=solve_group_id,
                    coefficient_formula=alias,
                )
                gamma = answer.derivation.get("gamma")
                applied = _apply_henrian_gamma(current, float(gamma))
                if applied is not None:
                    converted, value = applied
                    answer = replace(
                        _annotate(answer, gamma=converted),
                        value=value,
                        ln_value=None,
                    )
                    if alias is not None and current == formula:
                        answer = _mark_derived_spelling(answer)
                    return answer
        return _rung4(
            component_id=component_id,
            activity_exponent=activity_exponent,
            standard_state=standard_state,
            mole_fraction=mole_fraction,
            state_fingerprint=state_fingerprint,
            solve_group_id=solve_group_id,
            coefficient_formula=alias,
        )

    return _finish(_walk(formula, frozenset()))


def trace_parent_formulas() -> tuple[str, ...]:
    return tuple(
        sorted(set(LIQUID_PARENT_OXIDE.values()) | set(ACTIVITY_BASIS.values()))
    )


def _report_basis(
    derivation: Mapping[str, Any],
    formula: str,
    standard_state: StandardStateIdentity,
) -> dict[str, Any]:
    """Source basis, target basis, and the claim the scorer compares.

    ``standard_state`` is present only when the row's basis is the
    caller's. The phase on that claim is the closed token (``l`` or
    ``cr``). A printed phrase, a homologue, or a unity bound leaves the
    claim off: the target is reported beside the source and is not
    reused as compatibility.
    """

    source_phase = derivation.get("source_phase")
    source_basis = {
        "component": derivation.get("row_formula"),
        "standard_state_as_printed": derivation.get("standard_state_as_printed"),
        "convention": derivation.get("source_convention"),
        "phase": source_phase,
    }
    target_basis = {
        "convention": derivation.get("target_convention", standard_state.convention),
        "phase": derivation.get("target_phase", standard_state.phase),
        "component_basis": formula,
    }
    payload: dict[str, Any] = {
        "source_basis": source_basis,
        "target_basis": target_basis,
    }
    if derivation.get("basis_established") is not True:
        return payload
    convention = source_basis["convention"]
    phase = _closed_phase_token(source_phase) if source_phase else None
    if not isinstance(convention, str) or not convention or phase is None:
        return payload
    payload["standard_state"] = {
        "convention": convention,
        "phase": phase,
        "component_basis": formula,
    }
    return payload


def trace_parent_gamma_report(
    temperature_K: float,
    *,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Gamma or bound for every trace parent, with its source and target basis."""

    standard_state = StandardStateIdentity(
        convention="raoultian_pure_endmember",
        phase="liquid",
        reference_pressure_bar=1.0,
    )
    report: dict[str, dict[str, Any]] = {}
    for formula in trace_parent_formulas():
        answer = resolve_trace_parent_activity(
            formula,
            temperature_K=temperature_K,
            activity_exponent=1.0,
            standard_state=standard_state,
            rows=rows,
        )
        derivation = answer.derivation
        report[formula] = {
            "verdict": answer.verdict.value,
            "gamma": derivation.get("gamma"),
            "rung": derivation.get("rung"),
            "source_row_id": derivation.get("source_row_id"),
            "source_row_ids": list(derivation.get("source_row_ids") or ()),
            "flag": derivation.get("flag"),
            "homologue": derivation.get("homologue"),
            "origin": derivation.get("origin"),
            "extrapolation_notice": derivation.get("extrapolation_notice"),
            "candidate_rows": [
                dict(row) for row in (derivation.get("candidate_rows") or ())
            ],
            "coefficient_formula": derivation.get("coefficient_formula"),
            **_report_basis(derivation, formula, standard_state),
        }
    return report


__all__ = [
    "BOUND_NOT_POINT",
    "DIAGNOSTIC_AUTHORITY",
    "LOWER_BOUND_NOT_POINT",
    "R_J_PER_MOL_K",
    "REASON_HENRIAN_GAMMA_UNMEASURED",
    "STATUS_BEARING_NOT_POINT",
    "ActivityInputDeclaration",
    "ActivityRefusalCode",
    "ActivityVerdictKind",
    "AssemblageIdentity",
    "BoundDirection",
    "CondensedPhaseActivityProvider",
    "MageminAssemblageEvidence",
    "PhaseEndmemberMap",
    "SourceReactionActivity",
    "StandardStateIdentity",
    "StateFingerprint",
    "ThermoEnginePotentialEvidence",
    "activity_from_chemical_potentials",
    "coefficient_formula",
    "composition_fingerprint",
    "henrian_unknown_gamma_upper_bound",
    "is_trace_parent_component",
    "load_fegley2023_gamma_table",
    "prove_pressure_monotone_nondecreasing_in_activity",
    "resolve_trace_parent_activity",
    "trace_parent_formulas",
    "trace_parent_gamma_report",
    "validation_row_may_certify",
]
