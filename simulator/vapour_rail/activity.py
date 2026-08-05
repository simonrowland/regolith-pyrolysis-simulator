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
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_TIER,
    MELT_OXIDE_IDEAL_ASSERTION_TIER,
    MELT_OXIDE_IDEAL_SOLUTION_MODEL,
    melt_oxide_activity_coefficient,
)
from simulator.physical_constants import GAS_CONSTANT

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


@dataclass(frozen=True)
class StandardStateIdentity:
    """Exact standard-state identity; substring matching is forbidden."""

    convention: str
    phase: str
    reference_pressure_bar: float
    reference_temperature_K: float | None = None
    component_basis: str = "raoultian_pure_endmember"

    def fingerprint(self) -> str:
        payload = {
            "convention": self.convention,
            "phase": self.phase,
            "P_bar": float(self.reference_pressure_bar),
            "T_K": self.reference_temperature_K,
            "basis": self.component_basis,
        }
        return _stable_hash(payload)


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


def composition_fingerprint(composition: Mapping[str, float]) -> str:
    """Stable fingerprint for oxide/component mole maps."""

    cleaned = {
        str(key): float(value)
        for key, value in sorted(composition.items())
        if float(value) != 0.0
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

    if not math.isfinite(float(activity_exponent)):
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
        x_value = None if mole_fraction is None else float(mole_fraction)
    except (TypeError, ValueError):
        x_value = None
    if x_value is not None and (
        not math.isfinite(x_value) or not 0.0 <= x_value <= 1.0
    ):
        x_value = None

    coeff = melt_oxide_activity_coefficient(component_id)
    if coeff is None or x_value is None:
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

    gamma_is_at_most_unity = float(coeff.gamma) <= 1.0
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
            "gamma_anchor": float(coeff.gamma),
            "gamma_property": "gamma<=1" if gamma_is_at_most_unity else "gamma>1",
            "coefficient_domain_K": coeff.valid_range_K,
        },
        evidence_ref=coeff.citation,
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
    "composition_fingerprint",
    "henrian_unknown_gamma_upper_bound",
    "prove_pressure_monotone_nondecreasing_in_activity",
    "validation_row_may_certify",
]
