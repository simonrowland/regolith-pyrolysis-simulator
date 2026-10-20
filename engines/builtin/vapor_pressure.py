"""Builtin VAPOR_PRESSURE provider (Antoine + Ellingham).

Kernel-registered authoritative provider for the ``VAPOR_PRESSURE``
intent. VapoRock may run beside it as a diagnostic shadow, but the
pressure dict consumed by evaporation comes from this builtin provider.

The provider:

- reads ``process.cleaned_melt`` from the account view (the only
  account it declares),
- looks up Antoine coefficients from the ``vapor_pressures.yaml``
  payload passed at construction time,
- combines Ellingham oxide-decomposition equilibrium with phase-correct
  reference terms to compute per-species effective equilibrium pressures at the
  request's ``temperature_C``. Non-FeO metal release reads the independently
  supplied intrinsic-melt fO2; overhead ``pO2_bar`` remains the gas-side
  transport/backpressure channel (and the explicit SiO lever). Only
  ``pure_component_antoine`` sidecars are used for pure-component reference
  pressures when present; legacy ``antoine`` rows are used only when no
  sidecar exists. ``pseudo_psat_backsolved_from_vaporock`` rows are backsolved
  VapoRock curve-fit fallbacks only when their legacy ``antoine`` block is the
  selected coefficient source,
- returns an :class:`IntentResult` with ``transition=None``
  (diagnostic; VAPOR_PRESSURE owns no ledger mutation -- that belongs
  to ``EVAPORATION_TRANSITION``) and a ``vapor_pressures_Pa``
  diagnostic.

The :class:`CapabilityProfile` still declares the intent as
authority-capable so the registry will accept this provider in the
fallback slot (a fallback that is not authority-capable would only
produce diagnostic shadow output -- legal but useless as a real
backup).  Registry slot vs. capability is intentionally separate: the
profile says "I CAN be authoritative"; the kernel wiring decides
whether this build session actually uses this provider as the
authority or as fallback.

Account declaration: ``process.cleaned_melt`` only.  The provider must
not see gas / metal / sulfide / salt accounts -- the kernel filter
enforces this.  Mirrors the same constraint AlphaMELTS has (binding
spec §7).
"""

from __future__ import annotations

import math
import warnings as runtime_warnings
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Annotations only; the runtime import stays lazy because simulator
    # package init re-enters this module (see __init__ below).
    from simulator.vapour_rail.channels import (
        CompiledReactionTerm,
        GasChannelPotential,
    )

from engines.antoine import _antoine_log10_pressure
from engines.builtin._common import (
    composition_wt_pct_from_account_view,
    diagnostic_control_audit,
    reject_wrong_intent,
    resolve_request_vacuum_floor_bar,
    resolve_transport_pO2_bar,
)
from simulator.vapour_rail.domain_policy import (
    DomainPolicyError,
    declared_domain_transition,
)
from simulator.chemistry.kernel.capabilities import CapabilityProfile, ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.chemistry.gas_rail_authority import (
    _gas_rail_block_is_authoritative,
)


# Vapor-pressure convention contract (`data/vapor_pressures.yaml`):
# - Metals with `pure_component_antoine` sidecars evaluate that block as
#   `P_sat_pure`, then multiply by Ellingham `a_M` -- single-counted.
# - Metals with `fit_target: pseudo_psat_backsolved_from_vaporock` have raw
#   legacy Antoine evaluated as a pseudo-standard term only when no
#   pure-component sidecar is available.
# - Metal or oxide vapor rows with `fit_target: standard_reaction_term` use raw
#   Antoine as a ΔG-equivalent term, consumed with explicit oxide-activity +
#   pO2 exponents -- single-counted via explicit reaction stoichiometry.
# Dispatch keeps the existing math, but uses `fit_target` for honest source
# labels and runtime warnings when pseudo VapoRock curve-fit fallback rows are
# actually used.
#
# Canonical Ellingham table now lives in the dependency-free leaf
# ``simulator.chemistry.ellingham_thermo`` so both this provider and the
# EquilibriumMixin fallback can import it at module level without closing an
# import cycle. Re-exported here under the legacy ``_ELLINGHAM_THERMO`` name
# (consumed by metallothermic_step, vaporock/provider, and tests).
from simulator.chemistry.ellingham_thermo import (  # noqa: E402
    ELLINGHAM_METAL_PHASE_GAS,
    ELLINGHAM_FIT_RANGE_K,
    ELLINGHAM_THERMO as _ELLINGHAM_THERMO,
    ellingham_authority_diagnostic,
    ellingham_authority_limit,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_range_K,
    ellingham_metal_phase_kind,
    ellingham_stoichiometry,
)
from simulator.chemistry.melt_activity import (  # noqa: E402
    ALPHAMELTS_CROSS_CHECK_STATUS,
    MELT_OXIDE_ACTIVITY_LIMITATION,
    MELT_OXIDE_ACTIVITY_TIER,
    melt_oxide_activity,
    normalize_high_t_melt_activity,
    single_cation_mole_fractions,
)
from simulator.melt_backend.vaporock import VAPOROCK_T_MAX_K  # noqa: E402
from simulator.physical_constants import (  # noqa: E402
    GAS_CONSTANT,
    MELT_DISSOCIATION_PO2_MAX_BAR,
    MELT_DISSOCIATION_PO2_MIN_BAR,
)


def physical_melt_dissociation_pO2_bar(fO2_log: float) -> tuple[float, bool]:
    """Map log10 melt fO2 to a physically bounded pO2 in bar (b-148).

    Premise: vapor-pressure mass action for oxide-coupled carriers uses the
    melt's oxygen chemical potential as pO2_bar = 10**(fO2_log). That pO2
    must remain a *melt* state, not a float-range sentinel.
    Algebra: p = clamp(10**fO2_log, p_min, p_max) with
    p_min = MELT_DISSOCIATION_PO2_MIN_BAR and
    p_max = MELT_DISSOCIATION_PO2_MAX_BAR.
    Unit check: fO2_log is log10(bar); returned pO2 is bar absolute.
    Sanity: fO2_log = 0 → 1 bar (pure O2); fO2_log = 300 no longer yields
    1e300 bar (which made AlO2 ∝ pO2^0.25 explode by ~75 dex — b-148).

    Returns ``(pO2_bar, was_clamped)``. Callers should surface a warning
    when ``was_clamped`` is true so the redox pathology stays visible.
    """
    fO2 = float(fO2_log)
    # log10 clamp first so 10**fO2 never under/overflows to 0/inf before the
    # physical envelope is applied (fO2_log=-400 → 0.0 in float64 otherwise).
    log_min = math.log10(MELT_DISSOCIATION_PO2_MIN_BAR)
    log_max = math.log10(MELT_DISSOCIATION_PO2_MAX_BAR)
    # nan is missing input, not an envelope outlier. Mapping it onto either
    # edge invents a melt pO2; Fe-free melts never reach the Kress91
    # finite-controls guard, so the provider must refuse here.
    if math.isnan(fO2):
        raise VaporPressureComputationError(
            f"melt dissociation fO2_log must be finite; got {fO2_log!r}"
        )
    if not math.isfinite(fO2):
        # Lifted from the "unreachable after log clamp" branch below. The
        # previous sign-blind `return MAX_BAR, True` preempted that
        # sign-aware form and mapped -inf (infinitely reducing) onto the
        # most oxidizing edge. ±inf are envelope outliers; the sign of
        # the log chooses the edge.
        return (
            MELT_DISSOCIATION_PO2_MIN_BAR if fO2 < 0.0 else MELT_DISSOCIATION_PO2_MAX_BAR,
            True,
        )
    if fO2 < log_min:
        return MELT_DISSOCIATION_PO2_MIN_BAR, True
    if fO2 > log_max:
        return MELT_DISSOCIATION_PO2_MAX_BAR, True
    raw = 10.0 ** fO2
    if not math.isfinite(raw) or raw <= 0.0:
        # Should be unreachable after the log clamp; fail closed at the edge.
        return (
            MELT_DISSOCIATION_PO2_MIN_BAR if fO2 < 0.0 else MELT_DISSOCIATION_PO2_MAX_BAR,
            True,
        )
    return raw, False


MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON = (
    "melt_dissociation_pO2_floor_inverted_through_mass_action"
)
# Catalog operating-envelope lower bound from
# test_catalog_operating_envelope_no_nonphysical_pressure (1e-12 bar).
# That sweep stays under CATALOG_PHYSICAL_PRESSURE_CEILING_PA; the 1e-30
# floor is 18 dex below the band and is not a certified melt state.
MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR = 1.0e-12


def _pO2_is_melt_dissociation_floor(pO2_bar: float) -> bool:
    """True when mass-action pO2 is at or below the b-148 envelope floor.

    ``10**log10(MIN)`` is 9.999e-31 in float64, slightly below the 1e-30
    literal the clamp returns for fO2 < log10(MIN). Equality to MIN would
    miss the exact-edge case that does not set was_clamped.
    """

    try:
        oxygen = float(pO2_bar)
    except (TypeError, ValueError):
        return False
    return math.isfinite(oxygen) and oxygen > 0.0 and (
        oxygen <= MELT_DISSOCIATION_PO2_MIN_BAR
    )


def melt_dissociation_pO2_floor_inversion_notice(
    *,
    species: str,
    pressure_Pa: float,
    pO2_bar_used: float,
    pO2_exponent: float | None,
    pressure_rail: str | None,
    fO2_log: float | None,
    was_clamped: bool,
) -> dict[str, Any] | None:
    """Typed predict-and-flag notice when the pO2 floor inverts p_M.

    Premise: oxide-coupled metal pressure follows mass action
    ``p_M ∝ a * (pO2 / pO2_ref)^n`` with n < 0 (Si n=-1, pO2_ref=1e-9 bar;
    Na n=-0.25, pO2_ref=1 bar; Ellingham gas_fugacity
    ``p_M ∝ (K a^{n_ox} / pO2)^{1/n_M}``, so n_eff = -1/n_M).
    Algebra: at pO2 = MIN = 1e-30 bar, Si's pO2 term is
    (1e-30/1e-9)^{-1} = 1e21; Na's is (1e-30)^{-0.25} = 10^{7.5} ≈ 3.16e7;
    Mg (n_M=2) scales as (pO2)^{-0.5} so MIN vs 1e-9 is 10^{10.5} ≈ 3.16e10.
    Unit check: pO2/pO2_ref is bar/bar dimensionless; P remains Pa.
    Sanity: catalog envelope at 1e-12 bar keeps Si ~1e-8 Pa; the floor
    lands Si at ~7e9 Pa (above CATALOG_PHYSICAL_PRESSURE_CEILING_PA).
    Positive-n carriers (AlO2 n=+0.25) shrink at the floor — not inversion.
    Transport-channel species (Fe, SiO) keep overhead pO2 and are skipped
    when that pO2 is not the floor.

    Predict-and-flag: the number stays; the notice carries the original
    reason, authority_level, and certified pO2 band. Do not retune MIN.
    """

    if not _pO2_is_melt_dissociation_floor(pO2_bar_used):
        return None
    if pO2_exponent is not None:
        try:
            exponent = float(pO2_exponent)
        except (TypeError, ValueError):
            exponent = None
        else:
            if not math.isfinite(exponent) or exponent >= 0.0:
                return None
    elif str(pressure_rail or "") not in {
        "gas_fugacity",
        "liquid_oxide_standard_reaction",
        "gas_rail_liquid_oxide_standard_reaction",
    }:
        return None
    try:
        pressure = float(pressure_Pa)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pressure) or pressure <= 0.0:
        return None
    fO2_payload: float | None
    try:
        fO2_payload = float(fO2_log) if fO2_log is not None else None
    except (TypeError, ValueError):
        fO2_payload = None
    if fO2_payload is not None and not math.isfinite(fO2_payload):
        fO2_payload = None
    return {
        "reason": MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON,
        "authority_level": "extrapolated",
        "certified_band": {
            "pO2_bar": (
                MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
                MELT_DISSOCIATION_PO2_MAX_BAR,
            )
        },
        "pO2_bar_used": float(pO2_bar_used),
        "envelope_bar": (
            MELT_DISSOCIATION_PO2_MIN_BAR,
            MELT_DISSOCIATION_PO2_MAX_BAR,
        ),
        "fO2_log": fO2_payload,
        "was_clamped": bool(was_clamped),
        "species": str(species),
        "pressure_Pa": pressure,
        "pO2_exponent": (
            None if pO2_exponent is None else float(pO2_exponent)
        ),
        "pressure_rail": str(pressure_rail or "") or None,
    }


def _attach_pO2_floor_inversion_notices(
    *,
    vapor_pressures: dict[str, float],
    vapor_pressure_sources: dict[str, str],
    vapor_pressure_provenance: dict[str, dict[str, Any]],
    fO2_log: float | None,
    was_clamped: bool,
) -> dict[str, dict[str, Any]]:
    """Single choke: flag published metal P that used the pO2 floor."""

    notices: dict[str, dict[str, Any]] = {}
    token = MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    for species, pressure_Pa in vapor_pressures.items():
        provenance = vapor_pressure_provenance.get(species) or {}
        notice = melt_dissociation_pO2_floor_inversion_notice(
            species=str(species),
            pressure_Pa=float(pressure_Pa),
            pO2_bar_used=float(provenance.get("pO2_bar") or 0.0),
            pO2_exponent=provenance.get("pO2_exponent"),
            pressure_rail=provenance.get("pressure_rail"),
            fO2_log=fO2_log,
            was_clamped=was_clamped,
        )
        if notice is None:
            continue
        notices[str(species)] = notice
        provenance["floor_inversion_notice"] = notice
        # Public carrier extra copies provenance["extrapolation_notice"]
        # (request.py). Dual-write so wall/Pareto see the existing
        # reason/authority/band without a new result field. Do not replace
        # a temperature-range notice already on that key.
        if provenance.get("extrapolation_notice") is None:
            provenance["extrapolation_notice"] = notice
        vapor_pressure_provenance[str(species)] = provenance
        source = str(vapor_pressure_sources.get(species) or "")
        if source and token not in source.split(":"):
            vapor_pressure_sources[species] = f"{source}:{token}"
    return notices


class VaporPressureComputationError(RuntimeError):
    """Raised when vapor-pressure math cannot produce an authoritative value."""


class VaporPressureRangeError(VaporPressureComputationError):
    """A requested Antoine pressure lies outside its certified source range."""

    terminal_refusal = True


class VaporPressureNumericalOverflowError(OverflowError):
    """Typed recipe-physics overflow from vapor-pressure exponentiation."""


class VaporPressureFallbackWarning(RuntimeWarning):
    """Pseudo VapoRock curve-fit fallback is being used for vapor pressure."""


class HighUncertaintyVaporPressureFallbackWarning(VaporPressureFallbackWarning):
    """High-residual or low-confidence pseudo VapoRock fallback was used."""


FIT_TARGET_PURE_COMPONENT = "pure_component_psat"
FIT_TARGET_PSEUDO_VAPOROCK = "pseudo_psat_backsolved_from_vaporock"
FIT_TARGET_STANDARD_REACTION = "standard_reaction_term"
COEFF_BLOCK_ANTOINE = "antoine"
COEFF_BLOCK_PURE_COMPONENT = "pure_component_antoine"
GAS_RAIL_STANDARD_REACTION_KEY = "gas_rail_standard_reaction"
LIQUID_OXIDE_STANDARD_REACTION_KEY = "liquid_oxide_standard_reaction"
RECONSTRUCTED_VAPOR_PRESSURE_SEGMENT_KEY = (
    "reconstructed_vapor_pressure_segment"
)
VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG = (
    "authority_limited_by_reconstructed_vapor_pressure_segment"
)
VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_STATUS = "reconstructed_limited"
PSEUDO_VAPOROCK_CURVE_FIT_SOURCE = "vaporock_backsolved_curve_fit"
ELLINGHAM_STANDARD_PRESSURE_PA = 100000.0
_BUILTIN_VAPOR_SOURCE_CLASSES = frozenset(
    {
        "builtin_authoritative",
        "builtin_extrapolation_limited",
        "builtin_fallback",
    }
)


def _fit_target(row: Mapping[str, Any] | None) -> str:
    return str((row or {}).get("fit_target", "") or "").strip()


def _gas_rail_standard_reaction_block(
    row: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Optional liquid-oxide standard reaction for the gas rail only.

    Used by Ca/Mg two-rail metals: condensed pure-component Antoine is
    preserved below boiling; above boiling the solid-oxide Ellingham gas
    term is replaced by a liquid-oxide standard reaction that matches the
    pure-liquid Raoultian activity basis.

    Demoted blocks (``status: dormant_*`` / ``authoritative: false``) stay in
    YAML as provenance but do not select the gas-rail path — Builtin then uses
    gas_fugacity above the metal boil.
    """

    block = (row or {}).get(GAS_RAIL_STANDARD_REACTION_KEY)
    if not _is_mapping(block):
        return None
    if not _gas_rail_block_is_authoritative(block):
        return None
    return block


def _liquid_oxide_standard_reaction_block(
    row: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Optional full-T liquid-oxide standard reaction for oxide-coupled metal P.

    Used by Al/Ti/Cr/Mn: pure-component Antoine remains the pure-metal Psat
    sidecar (NBP/NIST ground truth); oxide-coupled runtime pressure uses this
    liquid-standard term instead of solid-oxide Ellingham × activity.
    """

    block = (row or {}).get(LIQUID_OXIDE_STANDARD_REACTION_KEY)
    return block if _is_mapping(block) else None

def _o2_channel_term_and_potential(
    *,
    pO2_exponent: float,
    pO2_bar: float,
    pO2_reference_bar: float,
    temperature_K: float,
    reaction_plane: str,
) -> tuple[CompiledReactionTerm | None, GasChannelPotential | None]:
    """Route a legacy linear-space rail's O2 dependence through channel #1.

    t-571 Phase 1: the O2 scalar no longer enters pressure math as a free
    fugacity — it is wrapped by the owner-gated channel factory
    (:func:`o2_potential_from_pO2_bar`, the registered Phase-1 runtime
    owner), which applies the b-148 physical envelope clamp and records the
    clamp/reference receipts.  The returned pair feeds
    :func:`channel_linear_mass_action_factor`, which reproduces the exact
    pre-migration expression ``(p_clamped / p_ref) ** e`` bit-for-bit:

    - ``legacy_pO2_bar`` is ``clamp_physical_pO2_bar(pO2_bar)``, identical to
      the legacy inline ``min(max(pO2, MIN), MAX)``;
    - ``term.derived_exponent`` is ``-(-e)/1 == e`` exactly (IEEE negation
      and division by 1.0 are exact), matching the legacy scalar exponent;
    - ``p_ref`` keeps the legacy ``max(1e-30, ref or 1.0)`` normalization.

    Returns ``(None, None)`` for a zero exponent (no O2 dependence).
    """

    from simulator.vapour_rail.channels import (
        compile_o2_channel_term,
        o2_potential_from_pO2_bar,
    )

    exponent = float(pO2_exponent)
    if not exponent:
        return None, None
    p_ref = max(1e-30, float(pO2_reference_bar) or 1.0)
    term = compile_o2_channel_term(
        signed_nu_o2=-exponent,
        target_nu=1.0,
        reaction_plane=reaction_plane,
    )
    potential = o2_potential_from_pO2_bar(
        pO2_bar=float(pO2_bar),
        temperature_K=float(temperature_K),
        reaction_plane=reaction_plane,
        pO2_reference_bar=p_ref,
    )
    return term, potential


def _standard_reaction_pressure_Pa(
    *,
    P_reference_Pa: float,
    oxide_activity_value: float,
    activity_exponent: float,
    o2_term: CompiledReactionTerm | None,
    o2_potential: GasChannelPotential | None,
) -> tuple[float, float, bool]:
    """Return (P_eq_Pa, activity_factor, pO2_scaled) for a standard reaction.

    t-571: the O2 factor arrives as a typed channel term + owner-gated
    potential (see :func:`_o2_channel_term_and_potential`) and is applied
    through :func:`channel_linear_mass_action_factor` — the channel
    interface's linear-composer form, bit-identical to the pre-migration
    ``(p_clamped / p_ref) ** e``.
    """

    from simulator.vapour_rail.channels import channel_linear_mass_action_factor

    activity_factor = max(float(oxide_activity_value), 0.0) ** float(
        activity_exponent
    )
    P_eq_Pa = float(P_reference_Pa) * activity_factor
    pO2_scaled = False
    if o2_term is not None:
        # b-148: the physical melt pO2 envelope is applied inside the
        # channel factory — never mass-action a float sentinel (1e300)^n
        # through positive-n carriers (AlO2, CrO2, …).
        P_eq_Pa *= channel_linear_mass_action_factor(o2_term, o2_potential)
        pO2_scaled = True
    return P_eq_Pa, activity_factor, pO2_scaled


_HIGH_T_FLUX_OXIDES: tuple[str, ...] = (
    "Na2O",
    "K2O",
    "SiO2",
    "FeO",
    "MgO",
    "CaO",
    "Al2O3",
    "TiO2",
)
_HIGH_T_ACTIVITY_SEAM_LIMIT_DEX = 0.1
_HIGH_T_ACTIVITY_AUTHORITY_EXCLUSIONS: dict[str, dict[str, str]] = {
    "K2O": {
        "authority": "constant_gamma",
        "ruling": "owner 2026-09-26",
        "citation": (
            "c3-owner-ruling-B-spec.md; "
            "../2026-09-26-k-activity-triangulation/"
            "zhang-controller-findings.md#correction"
        ),
        "tracking_id": "t-999",
    },
}

# Per-species cap on a first-order ln-gamma continuation, as a single-cation
# L1 distance from the last certified composition. Provenance: the solved
# c2b7b4e call log (117 calls, 2023–2473 K). On that trajectory the ln γ
# slope is composition-driven: a secant in T and in 1/T leaves the same
# error, so the cap is a composition distance, not a temperature window.
# The owner's step counts are the horizon at which parent-basis γ stays
# inside median absolute error ≤ 5% and worst ≤ 20% on the 7.5 K ramp:
# FeO 8, MgO 4, CaO 4, SiO2 3, Al2O3 3, TiO2 3, K2O 1, Na2O 0.
# FeO at 8 steps measured a worst error of 20.40% (0.40 percentage points
# over the 20% target) on that ramp; the count stays 8 because that is the
# horizon the owner counted in.
# Premise: one ramp step is a consecutive solved pair whose temperatures
# differ by exactly 7.5 K. There are 69 such pairs. Their single-cation L1
# distances, on the same eight flux parents as the r2 window, sum to
# 0.8844714170310508 (dimensionless). The mean step is that sum divided by
# 69. cap_L1 = steps * mean. Both factors are dimensionless, so the cap is
# a mole-fraction distance and compares directly with D(X_now, X_cert).
# A cap of 0 (Na2O) freezes at the certificate: one step of that parent can
# be ~94% off, so it is never first-order extrapolated. Distance == 0 is
# inside every cap, including 0, because the gate is distance > cap.
# K2O is listed so the prior is complete, but the 2026-09-26 exclusion
# still keeps it off the OpenIMCC certificate; this table does not lift
# that ruling. These distances are a prior to be tightened by bench/KEMS
# scoring, not a fit to a campaign rump.
_EXTRAPOLATION_CAP_STEPS: dict[str, int] = {
    "FeO": 8,
    "MgO": 4,
    "CaO": 4,
    "SiO2": 3,
    "Al2O3": 3,
    "TiO2": 3,
    "K2O": 1,
    "Na2O": 0,
}
_EXTRAPOLATION_RAMP_STEP_COUNT = 69
_EXTRAPOLATION_RAMP_L1_SUM = 0.8844714170310508
_EXTRAPOLATION_MEAN_STEP_L1 = (
    _EXTRAPOLATION_RAMP_L1_SUM / _EXTRAPOLATION_RAMP_STEP_COUNT
)
_EXTRAPOLATION_CAP_L1: dict[str, float] = {
    oxide: steps * _EXTRAPOLATION_MEAN_STEP_L1
    for oxide, steps in _EXTRAPOLATION_CAP_STEPS.items()
}

# Same-composition temperature bridge. A nonconvergence inside OpenIMCC's
# declared domain is usually isolated in T: the same composition solves one
# step away. Probe at most these offsets, nearest first, and stop once the
# solved points bracket the refused temperature. Six probes is the whole
# schedule (two signs, three widths). Points outside [1700, 3000] K are not
# called; that is the datapack domain, and a probe past it is a typed
# domain refusal rather than a gamma observation.
_TEMPERATURE_BRIDGE_OFFSETS_K = (25.0, 50.0, 75.0)
_TEMPERATURE_BRIDGE_MAX_CALLS = 6
_TEMPERATURE_BRIDGE_CAP_K = 75.0
_TEMPERATURE_BRIDGE_DOMAIN_K = (1700.0, 3000.0)

# One diagnostic-tier data table. This is the warning threshold suggested
# by d-175, not an accuracy calibration or a ceiling on projected solves.
_PROJECTED_COMPOSITION_WARNING_TIERS = (
    {
        "flag": "projected_composition_high_omission",
        "above_omitted_wt_pct": 5.0,
        "provenance": "owner d-175 / b-921 round 4 brief, 2026-10-18: flag above 5 wt%; no solve cap",
    },
)

# Gamma(KO0.5) is read from the production bridge's activity_coefficients row.
# Its activity is the square root of the K2O parent activity, and its x is the
# single-cation mole fraction: K cation moles divided by all cation moles.
# The Zhang anchor passes composition_kg in oxide wt% (the eight supported
# oxides from the bridge test); the bridge converts that mass basis to moles.
# Activity and mole fraction are both dimensionless, so their ratio Gamma is
# dimensionless too. This reproduces the wording-file sanity values.
# published-core is 3.5e-9 / 2.4e-8 at 1473 / 1673 K. The d-071 default
# (openimcc ccabd0b, digest ac88aa22…) prints 2.14e-6 / 1.12e-5. On the
# Zhang N-MORB wt% basis the 1473 K single-cation gamma was
# 1.6697561241999281e-6 under d066-v1 and is 2.137531039892675e-6 now
# (+28.0%). The move splits at the Gale-type N-MORB proxy, 1673 K, as
# parent a(K2O): openimcc 249fde5 (MgAl2O4 fusion from Shornikov 2017)
# multiplies it by 1.106, and 100df4f (FeO(l) Darken-Gurry/Masson
# re-anchor) multiplies that by 1.171. 1942696 and ccabd0b are 1.000
# against their parents, so the total factor is 1.295. Both corrections
# stabilise competing associates and draw Al2O3 and SiO2 away from the
# K associates, so free K2O rises. The content digest keys each
# evaluation so packs sharing a model id cannot reuse one another's
# numbers.
_K2O_PACK_EVALUATIONS_BY_DIGEST: dict[str, dict[str, Any]] = {
    "ae979b68295f6e653fb1c37d6b381f06cb0be8d4c5b421e673420a6e4596357c": {
        "pack_alias": "published-core",
        "model_id": "IMCC-SF04",
        "pack_version": "1.0.2",
        "gamma_ko0_5_by_temperature_K": {
            1473.15: "3.5e-9",
            1673.15: "2.4e-8",
        },
        "kcaal_si2o7_active_by_temperature_K": {
            1473.15: True,
            1673.15: True,
        },
    },
    "ac88aa2272c50d1cbf300634737c5d0071bddee0cd0f0483a6530c4a4992c393": {
        "pack_alias": "default",
        "model_id": "IMCC-SF04-D066",
        "pack_version": "1.0.4-d066-v2",
        "gamma_ko0_5_by_temperature_K": {
            1473.15: "2.14e-6",
            1673.15: "1.12e-5",
        },
        "kcaal_si2o7_active_by_temperature_K": {
            1473.15: False,
            1673.15: False,
        },
    },
}


def _k2o_authority_exclusion_for_pack_digest(
    pack_digest: str | None,
    model_id: str | None,
    pack_version: str | None,
    *,
    activity_step_dex: float | None = None,
) -> dict[str, str]:
    """Build the retained K2O ruling notice from the loaded pack evaluation."""

    exclusion = dict(_HIGH_T_ACTIVITY_AUTHORITY_EXCLUSIONS["K2O"])
    evaluation = _K2O_PACK_EVALUATIONS_BY_DIGEST.get(str(pack_digest or ""))
    if evaluation is None:
        reason = (
            "K2O remains excluded from the openimcc activity authority above "
            "the cap by owner ruling of 2026-09-26, retained pending "
            "re-evaluation under the D066 pack (t-1194); no evaluation exists "
            f"for this pack (model id {model_id!r}, version {pack_version!r}, "
            f"digest {pack_digest!r}). Constant-gamma K is also uncertified "
            "(t-999)."
        )
    else:
        loaded_gamma = evaluation["gamma_ko0_5_by_temperature_K"]
        loaded_active = evaluation["kcaal_si2o7_active_by_temperature_K"]

        def complex_state(active: bool) -> str:
            return "active" if active else "inactive"

        reason = (
            "K2O remains excluded from the openimcc activity authority above "
            "the cap by owner ruling of 2026-09-26, retained pending "
            "re-evaluation under the D066 pack (t-1194). The original ruling "
            "was established using published-core. For loaded pack "
            f"{evaluation['pack_alias']} ({model_id}, version {pack_version}, "
            f"digest {pack_digest}), "
            "KCaAlSi2O7 is "
            f"{complex_state(loaded_active[1473.15])} at 1473 K and "
            f"{complex_state(loaded_active[1673.15])} at 1673 K, with "
            "Gamma(KO0.5) values "
            f"{loaded_gamma[1473.15]} and {loaded_gamma[1673.15]}. "
            "Zhang et al. (2021, ACS Earth Space Chem.) measured "
            "alpha*Gamma(KO0.5) = 6.9e-8 at 1473 K and 1.11e-6 at "
            "1673 K for the cited N-MORB-like basalt."
        )
        if activity_step_dex is not None:
            reason += (
                " At this evaluated above-cap input, the OpenIMCC over "
                "constant-gamma K2O activity step is "
                f"{activity_step_dex:+.2f} dex."
            )
        reason += " Constant-gamma K is also uncertified (t-999)."
    exclusion["reason"] = reason
    return exclusion


def openimcc_binding_selected_by_inputs(
    *,
    temperature_K: float,
    controls: Mapping[str, Any],
) -> bool:
    mode = normalize_high_t_melt_activity(
        controls.get("high_t_melt_activity", "openimcc")
    )
    return not (temperature_K <= VAPOROCK_T_MAX_K or mode != "openimcc")


def _high_t_composition_vector(composition_mol: Mapping[str, float]) -> dict[str, float]:
    return {
        parent: fraction
        for parent, fraction in single_cation_mole_fractions(composition_mol).items()
        if parent in _HIGH_T_FLUX_OXIDES
    }


def _high_t_composition_distance(
    current: Mapping[str, float], previous: Mapping[str, float],
) -> float:
    return sum(
        abs(current.get(p, 0.0) - previous.get(p, 0.0))
        for p in _HIGH_T_FLUX_OXIDES
    )


def _ln_gamma_at_progress(
    certificate: Mapping[str, Any],
    progress_s: float,
    slope: float | None,
) -> float:
    """Evaluate the first-order ln-gamma step, with s = 0 exact.

    s = 0 returns gamma_last itself. exp(ln gamma_last) is not bit-identical
    to the certified coefficient, and the unit check of the Taylor step is
    that a zero step reproduces the last certified value.
    """

    gamma_last = float(certificate["gamma_last"])
    if progress_s == 0.0 or slope is None:
        return gamma_last
    return math.exp(float(certificate["ln_gamma_last"]) + slope * progress_s)


def _continued_certified_effective_gamma(
    certificate: Mapping[str, Any],
    *,
    temperature_K: float,
    composition: Mapping[str, float],
) -> dict[str, Any]:
    """First-order continuation of one certified effective gamma.

    The chemical potential of a melt component is
        μ_i = μ_i°(T) + RT ln a_i,
        ln a_i = ln γ_i + ln X_i.
    The flux multiplies the current bulk single-cation fraction X_i, which
    is known without a solve, by an effective γ_i. A refused solve withholds
    γ_i. Continue ln γ rather than γ: ln γ is the excess part of RT ln a, so
    a step is linear in the quantity the equilibrium condition uses, and the
    exponential stays positive for any finite step.

    One certified step supplies the secant. With progress s,
        ln γ_i(s) = ln γ_i,last + (ln γ_i,last − ln γ_i,prev) * s.
    s is dimensionless. On a thermal ramp it is the temperature step
        s = (T − T_last) / (T_last − T_prev)
    with both differences in kelvin, so the ratio has no unit. That is the
    progress of the lunar ramp this continuation was measured on. The ln γ
    slope there is composition-driven (a secant in T and in 1/T gives the
    same error); the caller caps accuracy with a composition distance and
    does not treat s as a thermal validity window. When T_last == T_prev the
    ramp is isothermal and s is the ratio of single-cation L1 distances
        s = D(X_now, X_last) / D(X_last, X_prev).
    L1 is dimensionless, so this ratio is too. A zero denominator (no
    observed composition step) gives s = 0.

    A quadratic term would need a third certified point. That three-point
    second difference amplifies solver noise, and the linear step is already
    within about 0.7% at one step (about 0.1% on some parents). The extra
    point is not worth the noise.

    One certified call has no slope: return gamma_last, the zero-order hold.
    s = 0 returns that same coefficient bit-exactly.
    """

    gamma_last = float(certificate["gamma_last"])
    ln_last = certificate.get("ln_gamma_last")
    ln_prev = certificate.get("ln_gamma_prev")
    has_slope = (
        ln_last is not None
        and ln_prev is not None
        and gamma_last > 0.0
    )
    slope = (float(ln_last) - float(ln_prev)) if has_slope else None
    temperature_prev = certificate.get("temperature_prev_K")
    if not has_slope or temperature_prev is None:
        return {
            "gamma": gamma_last,
            "method": "hold_last_certified_effective_gamma",
            "progress_s": 0.0,
            "progress_variable": "temperature",
            "ln_gamma_slope": None,
        }
    temperature_last = float(certificate["temperature_K"])
    temperature_prev = float(temperature_prev)
    if temperature_last != temperature_prev:
        progress_s = (float(temperature_K) - temperature_last) / (
            temperature_last - temperature_prev
        )
        progress_variable = "temperature"
    else:
        previous_composition = certificate.get("composition_prev_single_cation")
        step = (
            _high_t_composition_distance(
                certificate["composition_single_cation"],
                previous_composition,
            )
            if previous_composition is not None else 0.0
        )
        progress_s = (
            0.0 if step == 0.0 else _high_t_composition_distance(
                composition, certificate["composition_single_cation"],
            ) / step
        )
        progress_variable = "composition"
    return {
        "gamma": _ln_gamma_at_progress(certificate, progress_s, slope),
        "method": "extrapolated_first_order_ln_gamma",
        "progress_s": progress_s,
        "progress_variable": progress_variable,
        "ln_gamma_slope": slope,
    }


def _regular_solution_gamma_fit(
    observations: list[tuple[float, float]],
    temperature_K: float,
) -> dict[str, Any] | None:
    """Fit RT ln γ = W + S T and evaluate it at the refused temperature.

    μ_i = μ_i° + RT ln a_i and ln a_i = ln γ_i + ln X_i. At fixed bulk
    composition X_i is known and does not change between probes, so the
    temperature dependence left in the activity is γ_i(T). A regular-solution
    excess folds the composition factors into two constants,
        RT ln γ_i = W_i + S_i T,
    with W in J/mol and S in J/(mol·K). Those constants are constants only
    while the composition is the one they were fit on, which is why every
    probe uses the refused melt and not a neighbour on the campaign
    trajectory. R is GAS_CONSTANT, J/(mol·K), the same CODATA value the
    reach report rounded to 8.314462618. y = RT ln γ is therefore J/mol,
    and (W + S T) / (RT) is dimensionless, as ln γ must be.

    Two points determine W and S exactly. Three or more use least squares
    on y = W + S T. The algebra recovers each two-point observation: at
    T = T_1, W + S T_1 = y_1, so ln γ returns ln γ_1. A zero temperature
    span has no slope and is not a fit. This is the primary continuation.
    The trajectory secant remains the fallback when fewer than two probes
    converge; that secant changes composition, and this fit does not.
    """

    usable = [
        (float(probe_T), float(gamma))
        for probe_T, gamma in observations
        if gamma > 0.0 and math.isfinite(gamma) and math.isfinite(probe_T)
    ]
    if len(usable) < 2 or not math.isfinite(temperature_K) or temperature_K == 0.0:
        return None
    gas_R = float(GAS_CONSTANT)
    if len(usable) == 2:
        (first_T, first_gamma), (second_T, second_gamma) = usable
        if first_T == second_T:
            return None
        first_y = gas_R * first_T * math.log(first_gamma)
        second_y = gas_R * second_T * math.log(second_gamma)
        entropy = (second_y - first_y) / (second_T - first_T)
        enthalpy = first_y - entropy * first_T
        fit = "exact_line"
    else:
        count = float(len(usable))
        sum_T = sum(probe_T for probe_T, _gamma in usable)
        sum_T2 = sum(probe_T * probe_T for probe_T, _gamma in usable)
        ys = [
            gas_R * probe_T * math.log(gamma) for probe_T, gamma in usable
        ]
        sum_y = sum(ys)
        sum_Ty = sum(
            probe_T * y for (probe_T, _gamma), y in zip(usable, ys)
        )
        # denom = n ΣT² − (ΣT)² = Σ_{i<j} (T_j − T_i)², in K².
        # It is zero only when every probe temperature is the same.
        denom = count * sum_T2 - sum_T * sum_T
        if denom == 0.0:
            return None
        enthalpy = (sum_y * sum_T2 - sum_T * sum_Ty) / denom
        entropy = (count * sum_Ty - sum_T * sum_y) / denom
        fit = "least_squares"
    gamma = math.exp((enthalpy + entropy * float(temperature_K)) / (gas_R * float(temperature_K)))
    temperatures = [probe_T for probe_T, _gamma in usable]
    low = min(temperatures)
    high = max(temperatures)
    return {
        "gamma": gamma,
        "W_J_per_mol": enthalpy,
        "S_J_per_mol_K": entropy,
        "bridge_mode": (
            "interpolation" if low <= float(temperature_K) <= high else "extrapolation"
        ),
        "bridge_span_K": high - low,
        "bridge_temperatures_K": temperatures,
        "bridge_fit": fit,
    }


def _same_composition_temperature_bridge(
    composition_mol: Mapping[str, float],
    temperature_K: float,
    refusal_types: tuple[type[BaseException], ...],
    *,
    activity_basis_mol: Mapping[str, float],
) -> dict[str, Any]:
    """Re-solve one composition at T±25, ±50 and ±75 K until it brackets."""

    from simulator.melt_backend.openimcc_bridge import evaluate_cleaned_melt

    points: list[tuple[float, dict[str, float]]] = []
    attempted: list[float] = []
    domain_low, domain_high = _TEMPERATURE_BRIDGE_DOMAIN_K

    def bracketed() -> bool:
        return any(probe_T < temperature_K for probe_T, _gammas in points) and any(
            probe_T > temperature_K for probe_T, _gammas in points
        )

    for offset in _TEMPERATURE_BRIDGE_OFFSETS_K:
        if bracketed() or len(attempted) >= _TEMPERATURE_BRIDGE_MAX_CALLS:
            break
        for sign in (1.0, -1.0):
            if bracketed() or len(attempted) >= _TEMPERATURE_BRIDGE_MAX_CALLS:
                break
            probe_T = float(temperature_K) + sign * float(offset)
            if probe_T < domain_low or probe_T > domain_high:
                continue
            if abs(probe_T - float(temperature_K)) > _TEMPERATURE_BRIDGE_CAP_K:
                continue
            attempted.append(probe_T)
            try:
                cleaned = evaluate_cleaned_melt(
                    composition_mol=composition_mol,
                    temperature_K=probe_T,
                    allow_extrapolation=False,
                )
            except refusal_types:
                continue
            probe_authority = {
                "provider": "openimcc",
                "activities_by_oxide": {
                    oxide: float(activity)
                    for oxide, activity in cleaned.single_cation_activities.items()
                },
                "openimcc_pack_model_id": cleaned.bridge.pack_model_id,
                "openimcc_pack_version": cleaned.bridge.pack_version,
                "openimcc_pack_digest": cleaned.bridge.pack_digest,
            }
            gammas: dict[str, float] = {}
            for oxide in probe_authority["activities_by_oxide"]:
                if oxide in _HIGH_T_ACTIVITY_AUTHORITY_EXCLUSIONS:
                    continue
                activity = _high_t_activity_for_parent(
                    oxide,
                    activity_basis_mol,
                    temperature_K=probe_T,
                    authority=probe_authority,
                )
                if (
                    activity is None
                    or activity.activity_model != "openimcc_single_cation_activity"
                    or not (activity.effective_gamma > 0.0)
                ):
                    continue
                gammas[oxide] = float(activity.effective_gamma)
            if gammas:
                points.append((probe_T, gammas))
    fits: dict[str, dict[str, Any]] = {}
    parents = {oxide for _probe_T, gammas in points for oxide in gammas}
    for oxide in parents:
        observations = [
            (probe_T, gammas[oxide])
            for probe_T, gammas in points
            if oxide in gammas
        ]
        fit = _regular_solution_gamma_fit(observations, float(temperature_K))
        if fit is not None:
            fits[oxide] = fit
    return {
        "call_count": len(attempted),
        "attempted_temperatures_K": attempted,
        "cap_K": _TEMPERATURE_BRIDGE_CAP_K,
        "fits": fits,
    }


def _build_high_t_melt_activity_authority(
    *,
    composition_mol: Mapping[str, float],
    temperature_K: float,
    controls: Mapping[str, Any],
    below_cap_fe_activity: float,
    below_cap_fe_activity_basis: str,
) -> dict[str, Any] | None:
    """Resolve C3's high-temperature activity source once per pressure call.

    The returned payload is diagnostic state only.  Pressure and pO2 algebra
    remain in the existing dispatch branches; callers replace only the melt
    activity object for the eight flux oxides when this payload is successful.
    """

    if not openimcc_binding_selected_by_inputs(
        temperature_K=temperature_K,
        controls=controls,
    ):
        return None
    mode = "openimcc"

    base: dict[str, Any] = {
        "schema": "high_t_melt_activity.v1",
        "requested": mode,
        "temperature_K": float(temperature_K),
        "vaporock_cap_K": float(VAPOROCK_T_MAX_K),
        "above_vaporock_cap": True,
        "fallback": False,
        "provider": "openimcc",
        "composition_policy": {},
        "notices": [],
        "seam": {},
        "authority_exclusions": {},
    }
    previous = controls.get("high_t_melt_activity_previous") or {}
    certificates = dict(previous.get("certified_effective_gamma_by_parent") or {})
    base["certified_effective_gamma_by_parent"] = certificates
    hour = controls.get("simulation_hour")
    from simulator.melt_backend.openimcc_bridge import (
        _IMCC_REFUSAL_ERROR_TYPE,
        _cleaned_melt_projection,
        OpenImccCompositionPolicyRefusal,
        OpenImccUnavailableError,
        evaluate_cleaned_melt,
    )

    expected_refusal_types = (
        OpenImccCompositionPolicyRefusal,
        OpenImccUnavailableError,
    )
    if _IMCC_REFUSAL_ERROR_TYPE is not None:
        expected_refusal_types += (_IMCC_REFUSAL_ERROR_TYPE,)
    solve_composition = composition_mol
    projection_notice = None
    projection_classification = None
    try:
        try:
            cleaned = evaluate_cleaned_melt(
                composition_mol=solve_composition,
                temperature_K=temperature_K,
                allow_extrapolation=temperature_K > 3000.0,
            )
        except OpenImccCompositionPolicyRefusal as policy_refusal:
            if policy_refusal.code != "openimcc_projection_over_threshold":
                raise
            # d-175 (2026-10-18): composition coverage is not a numerical
            # gap. Reuse OpenIMCC's projection (including Fe2O3 -> FeO) and
            # solve the remaining oxide basis at the actual T. Its existing
            # mole -> wt% conversion normalizes the retained mass to 100%:
            # w'_i = 100 m_i / sum(retained m), all masses in kg, so w' is
            # wt%. On the folded-iron basis, omitting fraction f of that
            # represented mass rescales retained weights by 1/(1-f); f -> 0
            # recovers the unprojected folded-iron input. Reported omitted
            # wt% uses the original source mass (before the iron fold).
            # This removes the omitted components' interactions, not their
            # ledger mass.
            # A fresh equilibrium tracks current T and retained chemistry;
            # a frozen gamma can drift for hundreds of kelvin and cannot.
            # Activities keep the projected pure-liquid reference. Reporting
            # gamma_eff = a_projected / X_bulk_single absorbs the changed
            # cation denominator; do NOT dilute a by (1-f), a mass fraction
            # rather than a cation fraction. Then gamma_eff*X_bulk returns
            # a_projected exactly (all dimensionless). The omitted chemistry
            # is unmodelled, so this is extrapolated, never a certificate.
            _, solve_composition = _cleaned_melt_projection(composition_mol)
            projection_classification = dict(
                policy_refusal.diagnostics["composition_projection_classification"]
            )
            projection_notice = {
                "code": "projected_composition_solve",
                "method": "projected_composition_solve",
                "authority": "extrapolated",
                "temperature_K": float(temperature_K),
                "omitted_component_wt_pct": dict(
                    projection_classification["dropped_component_wt_pct"]
                ),
                "omitted_total_wt_pct": projection_classification["dropped_total_wt_pct"],
                "projection_threshold_wt_pct": projection_classification["threshold_wt_pct"],
                "original_refusal": {
                    "code": policy_refusal.code,
                    "type": type(policy_refusal).__name__,
                    "detail": str(policy_refusal),
                },
            }
            projection_notice["warning_tiers"] = [
                dict(tier) for tier in _PROJECTED_COMPOSITION_WARNING_TIERS
                if projection_notice["omitted_total_wt_pct"] > tier["above_omitted_wt_pct"]
            ]
            base["projected_composition_solve"] = projection_notice
            base["notices"].append(projection_notice)
            cleaned = evaluate_cleaned_melt(
                composition_mol=solve_composition,
                temperature_K=temperature_K,
                allow_extrapolation=temperature_K > 3000.0,
            )
    except expected_refusal_types as exc:
        code = str(getattr(exc, "code", "openimcc_evaluation_refused"))
        diagnostics = getattr(exc, "diagnostics", {})
        classification = (
            diagnostics.get("composition_projection_classification")
            if isinstance(diagnostics, Mapping)
            else None
        )
        fallback_reason = {
            "code": code,
            "type": type(exc).__name__,
            "detail": str(exc),
        }
        fallback_notice = {
            "code": code,
            "reason": code,
            "authority": "extrapolated",
            "message": str(exc),
        }
        if isinstance(diagnostics, Mapping):
            for residual_key in ("residual_inf", "residual_l2"):
                if residual_key not in diagnostics:
                    continue
                fallback_reason[residual_key] = diagnostics[residual_key]
                fallback_notice[residual_key] = diagnostics[residual_key]
        base.update(
            {
                "provider": "constant_gamma",
                "fallback": True,
                "authority": "extrapolated",
                "fallback_reason": fallback_reason,
                "notices": base["notices"] + [fallback_notice],
                "activities_by_oxide": {},
                "openimcc_extrapolated": False,
                "openimcc_flags": [],
                "openimcc_notices": [],
                "composition_projection_classification": (
                    dict(projection_classification or classification)
                    if projection_classification or isinstance(classification, Mapping)
                    else {}
                ),
            }
        )
        relaxed_projection = (
            classification.get("openimcc_projection_crmn_relaxed")
            if isinstance(classification, Mapping)
            else None
        )
        if isinstance(relaxed_projection, Mapping):
            base["openimcc_projection_crmn_relaxed"] = dict(relaxed_projection)
        held = {}
        last_extrapolated: dict[str, float] = {}
        # Primary path: same composition, nearby T. The trajectory secant
        # below is the fallback when that bridge cannot gather two points.
        # The constant-gamma table is not used for a parent that has a
        # certificate, and it is not used when the bridge itself supplied
        # a gamma.
        bridge_result = (
            _same_composition_temperature_bridge(
                solve_composition, float(temperature_K), expected_refusal_types,
                activity_basis_mol=composition_mol,
            )
            if code == "imcc_nonconvergence"
            else None
        )
        bridge_fits = bridge_result["fits"] if bridge_result else {}
        if bridge_result is not None:
            base["temperature_bridge"] = {
                "call_count": bridge_result["call_count"],
                "attempted_temperatures_K": list(
                    bridge_result["attempted_temperatures_K"]
                ),
                "cap_K": bridge_result["cap_K"],
            }
        can_continue = (
            hour is not None and bool(certificates)
            and not isinstance(exc, OpenImccCompositionPolicyRefusal)
        )
        # Composition-policy refusals never enter secant/freeze. A projected
        # numerical refusal bridges the projected composition above.
        # ImccTOutsideDatapackDomainError keeps its existing secant/freeze
        # route; the MELTS handoff below 1700 K is t-1314, not d-175.
        if bridge_fits or can_continue:
            composition = _high_t_composition_vector(composition_mol)
            prior_extrapolated = dict(
                previous.get("last_extrapolated_gamma_by_parent") or {}
            )
            refusal_count = int(previous.get("consecutive_refusal_count") or 0) + 1
            parents = list(certificates)
            for parent in bridge_fits:
                if parent not in parents:
                    parents.append(parent)
            for parent in parents:
                if parent not in composition_mol:
                    continue
                certificate = certificates.get(parent)
                fit = bridge_fits.get(parent)
                if fit is not None:
                    notice = {
                        "code": "hold_last_certified_effective_gamma",
                        "type": "typed_notice",
                        "authority": "extrapolated",
                        "parent": parent,
                        **(certificate or {}),
                        "method": "same_composition_T_bridge",
                        "gamma_applied": fit["gamma"],
                        "W_J_per_mol": fit["W_J_per_mol"],
                        "S_J_per_mol_K": fit["S_J_per_mol_K"],
                        "bridge_mode": fit["bridge_mode"],
                        "bridge_span_K": fit["bridge_span_K"],
                        "bridge_temperatures_K": list(fit["bridge_temperatures_K"]),
                        "bridge_fit": fit["bridge_fit"],
                        "bridge_call_count": bridge_result["call_count"],
                        "bridge_cap_K": bridge_result["cap_K"],
                        "bridge_attempted_temperatures_K": list(
                            bridge_result["attempted_temperatures_K"]
                        ),
                        "original_refusal": dict(fallback_reason),
                        "consecutive_refusal_count": refusal_count,
                        "message": (
                            "Same-composition temperature bridge of the "
                            "effective single-cation gamma"
                        ),
                    }
                    if certificate is not None:
                        notice["temperature_gap_K"] = abs(
                            temperature_K - certificate["temperature_K"]
                        )
                        if hour is not None:
                            notice["hour_gap"] = float(hour) - certificate["hour"]
                        notice["composition_distance_L1"] = (
                            _high_t_composition_distance(
                                composition,
                                certificate["composition_single_cation"],
                            )
                        )
                    held[parent] = notice
                    base["notices"].append(notice)
                    continue
                if not can_continue or certificate is None:
                    continue
                temperature_gap = abs(temperature_K - certificate["temperature_K"])
                hour_gap = float(hour) - certificate["hour"]
                composition_distance = _high_t_composition_distance(
                    composition, certificate["composition_single_cation"],
                )
                continued = _continued_certified_effective_gamma(
                    certificate,
                    temperature_K=temperature_K,
                    composition=composition,
                )
                cap = _EXTRAPOLATION_CAP_L1.get(parent)
                past_cap = cap is not None and composition_distance > cap
                if past_cap and parent in prior_extrapolated:
                    gamma = float(prior_extrapolated[parent])
                    method = "extrapolation_capped_frozen"
                    flag = "extrapolation_capped_frozen"
                elif past_cap:
                    # First refusal is already past the cap. Freeze at the
                    # Taylor value on the cap boundary so the stored gamma
                    # is the last in-cap step, not a fresh table value.
                    # s_cap = s * (cap / D). cap and D are both L1, so the
                    # ratio is dimensionless and s_cap is a progress.
                    # cap == 0 (Na2O) or a missing slope keeps gamma_last.
                    slope = continued["ln_gamma_slope"]
                    if (
                        cap is None or cap <= 0.0 or slope is None
                        or composition_distance <= 0.0
                    ):
                        gamma = float(certificate["gamma_last"])
                    else:
                        gamma = _ln_gamma_at_progress(
                            certificate,
                            continued["progress_s"] * (cap / composition_distance),
                            slope,
                        )
                    method = "extrapolation_capped_frozen"
                    flag = "extrapolation_capped_frozen"
                else:
                    gamma = float(continued["gamma"])
                    method = str(continued["method"])
                    flag = None
                notice = {
                    "code": "hold_last_certified_effective_gamma",
                    "type": "typed_notice",
                    "authority": "extrapolated",
                    "parent": parent,
                    **certificate,
                    "temperature_gap_K": temperature_gap,
                    "composition_distance_L1": composition_distance,
                    "hour_gap": hour_gap,
                    "original_refusal": dict(fallback_reason),
                    "method": method,
                    "gamma_applied": gamma,
                    "progress_s": continued["progress_s"],
                    "progress_variable": continued["progress_variable"],
                    "consecutive_refusal_count": refusal_count,
                    "message": (
                        "Extrapolation cap reached; freezing the last extrapolated effective gamma"
                        if flag else (
                            "First-order continuation of the certified effective single-cation ln gamma"
                            if method == "extrapolated_first_order_ln_gamma"
                            else "Holding the last certified effective single-cation gamma"
                        )
                    ),
                }
                if continued["ln_gamma_slope"] is not None:
                    notice["ln_gamma_slope"] = continued["ln_gamma_slope"]
                if cap is not None:
                    notice["composition_cap_L1"] = cap
                if flag is not None:
                    notice["flag"] = flag
                if bridge_result is not None:
                    notice["bridge_call_count"] = bridge_result["call_count"]
                    notice["bridge_cap_K"] = bridge_result["cap_K"]
                    notice["bridge_attempted_temperatures_K"] = list(
                        bridge_result["attempted_temperatures_K"]
                    )
                    notice["bridge_fallback"] = "fewer_than_two_temperature_points"
                held[parent] = notice
                last_extrapolated[parent] = gamma
                base["notices"].append(notice)
            base["consecutive_refusal_count"] = refusal_count
        base["held_effective_gamma_by_parent"] = held
        base["last_extrapolated_gamma_by_parent"] = last_extrapolated
        return base

    bridge = cleaned.bridge
    policy_notices: list[dict[str, Any]] = []
    if cleaned.policy.get("notice"):
        policy_notices.append(dict(cleaned.policy["notice"]))
    if cleaned.policy.get("fe2o3_fold"):
        policy_notices.append(dict(cleaned.policy["fe2o3_fold"]))
    relaxed_projection = cleaned.policy.get("openimcc_projection_crmn_relaxed")
    if isinstance(relaxed_projection, Mapping):
        policy_notices.append(dict(relaxed_projection))
    from simulator.melt_backend.openimcc_bridge import (
        imcc_complex_saturation_notices,
    )

    typed_openimcc_notices = list(
        imcc_complex_saturation_notices(
            bridge.flags,
            bridge.acid_sink_ratio,
            bridge.parent_oxide_x_star_ratios,
        )
    )
    base["notices"].extend(typed_openimcc_notices)
    base.update(
        {
            "activities_by_oxide": dict(cleaned.single_cation_activities),
            "openimcc_activities_by_oxide": dict(
                cleaned.single_cation_activities
            ),
            "composition_policy": dict(cleaned.policy),
            "composition_projection_classification": dict(
                cleaned.policy.get("composition_projection_classification", {})
            ),
            "openimcc_version": bridge.openimcc_version,
            "openimcc_pack_model_id": bridge.pack_model_id,
            "openimcc_pack_version": bridge.pack_version,
            "openimcc_pack_digest": bridge.pack_digest,
            "openimcc_flags": list(bridge.flags),
            "openimcc_notices": (
                list(bridge.notices) + policy_notices + typed_openimcc_notices
            ),
            "openimcc_typed_notices": typed_openimcc_notices,
            "openimcc_policy_notices": policy_notices,
            "openimcc_envelope_status": bridge.envelope_status,
            "openimcc_extrapolated": bool(bridge.extrapolated),
            "openimcc_coverage": dict(bridge.coverage),
            "openimcc_acid_sink_ratio": bridge.acid_sink_ratio,
            "temperature_domain_status": (
                "predict_and_flag_extrapolated"
                if temperature_K > 3000.0
                else "in_domain"
            ),
        }
    )
    if isinstance(relaxed_projection, Mapping):
        base["openimcc_projection_crmn_relaxed"] = dict(relaxed_projection)

    if projection_notice is not None:
        base.update({
            "fallback": True,
            "authority": "extrapolated",
            "fallback_reason": dict(projection_notice["original_refusal"]),
            "composition_projection_classification": projection_classification,
        })

    max_abs_dex = 0.0
    for oxide in _HIGH_T_FLUX_OXIDES:
        if oxide == "FeO":
            legacy_activity = float(below_cap_fe_activity)
            legacy_basis = below_cap_fe_activity_basis
        else:
            legacy = melt_oxide_activity(
                oxide,
                composition_mol,
                temperature_K=temperature_K,
            )
            legacy_activity = None if legacy is None else float(legacy.activity)
            legacy_basis = "constant_gamma" if legacy_activity is not None else None
        has_openimcc_activity = oxide in cleaned.single_cation_activities
        openimcc_activity = (
            float(cleaned.single_cation_activities[oxide])
            if has_openimcc_activity
            else None
        )
        ratio_dex = None
        if (
            legacy_activity is not None
            and legacy_activity > 0.0
            and openimcc_activity is not None
            and openimcc_activity > 0.0
        ):
            ratio_dex = math.log10(openimcc_activity / legacy_activity)
        exclusion = (
            _k2o_authority_exclusion_for_pack_digest(
                bridge.pack_digest,
                bridge.pack_model_id,
                bridge.pack_version,
                activity_step_dex=ratio_dex,
            )
            if oxide == "K2O"
            else _HIGH_T_ACTIVITY_AUTHORITY_EXCLUSIONS.get(oxide)
        )
        selected_activity = openimcc_activity
        selected_activity_basis = "openimcc_single_cation"
        selected_ratio_dex = ratio_dex
        if exclusion is not None and legacy_activity is not None:
            selected_activity = legacy_activity
            selected_activity_basis = legacy_basis or "constant_gamma"
            selected_ratio_dex = 0.0
            base["activities_by_oxide"][oxide] = selected_activity
            base["authority_exclusions"][oxide] = dict(exclusion)
        elif not has_openimcc_activity:
            selected_activity = legacy_activity
            selected_activity_basis = legacy_basis or "constant_gamma"
            exclusion = {
                "authority": selected_activity_basis,
                "code": "openimcc_parent_absent",
                "reason": (
                    f"{oxide} is absent from the cleaned-melt input; "
                    "openimcc has no activity for this parent"
                ),
            }
            base["authority_exclusions"][oxide] = dict(exclusion)
        if selected_ratio_dex is not None:
            max_abs_dex = max(max_abs_dex, abs(selected_ratio_dex))
        base["seam"][oxide] = {
            "below_cap_activity": legacy_activity,
            "below_cap_activity_basis": legacy_basis,
            "openimcc_activity": openimcc_activity,
            "ratio_dex_openimcc_over_below_cap": ratio_dex,
            "selected_activity": selected_activity,
            "selected_activity_basis": selected_activity_basis,
            "ratio_dex_selected_over_below_cap": selected_ratio_dex,
            "would_be_openimcc_step_dex": ratio_dex,
            "authority_exclusion": (
                dict(exclusion) if exclusion is not None else None
            ),
        }

    previous_temperature = controls.get(
        "high_t_melt_activity_previous_temperature_K"
    )
    crossing = bool(controls.get("high_t_melt_activity_crossing", False))
    if not crossing and previous_temperature is not None:
        try:
            crossing = (
                float(previous_temperature) <= VAPOROCK_T_MAX_K
                and temperature_K > VAPOROCK_T_MAX_K
            )
        except (TypeError, ValueError):
            crossing = False
    if crossing and max_abs_dex > _HIGH_T_ACTIVITY_SEAM_LIMIT_DEX:
        base["notices"].append(
            {
                "code": "openimcc_activity_cap_step_exceeds_0_1_dex",
                "type": "typed_notice",
                "temperature_K": float(temperature_K),
                "cap_K": float(VAPOROCK_T_MAX_K),
                "max_abs_ratio_dex": max_abs_dex,
                "threshold_dex": _HIGH_T_ACTIVITY_SEAM_LIMIT_DEX,
                "message": (
                    "openimcc/below-cap activity source switch exceeds "
                    "the provisional cap seam screen"
                ),
            }
        )
    base["seam_max_abs_ratio_dex"] = max_abs_dex
    if hour is not None and not bridge.extrapolated and projection_notice is None:
        composition = _high_t_composition_vector(composition_mol)
        previous_temperature = controls.get("high_t_melt_activity_previous_temperature_K")
        # The recorded window is still two observed local steps: thermal
        # window_K = 2*|T_cert-T_previous| (K), and composition_window_L1 =
        # 2*D(X_cert, X_previous_cert) on the flux parents' single-cation
        # fractions. D is an L1, so parents do not cancel. Those fields
        # describe the step the certificate was taken over. A later refusal
        # does not consult them: continuation is the first-order ln-gamma
        # step, capped per species by _EXTRAPOLATION_CAP_L1. Refused and
        # extrapolated calls never refresh the certificate.
        window_K = (
            2.0 * abs(temperature_K - float(previous_temperature))
            if previous_temperature is not None else 0.0
        )
        for parent in cleaned.single_cation_activities:
            if parent in base["authority_exclusions"]:
                continue
            selected = _high_t_activity_for_parent(
                parent, composition_mol, temperature_K=temperature_K, authority=base,
            )
            if selected is None or selected.x_single_cation <= 0.0:
                continue
            previous_composition = certificates.get(parent, {}).get(
                "composition_single_cation"
            )
            composition_window = (
                2.0 * _high_t_composition_distance(composition, previous_composition)
                if previous_composition is not None else 0.0
            )
            # Premise: the bridge has already converted parent activities
            # to single-cation a; a = gamma_eff * X_bulk_single. Thus
            # gamma_eff,last = a_last / X_last. On refusal the continuation
            # steps ln gamma_eff, and a single certified point holds
            # gamma_eff,last. Either way a_current = gamma_applied * X_current.
            # This is NOT OpenIMCC's internal gamma against unbound-parent
            # X on a total-species basis: that current X needs the failed
            # solve. Both this effective coefficient and the table use the
            # same available bulk single-cation basis. All factors are
            # dimensionless; X->0 gives a->0 for finite gamma. With smooth
            # T/composition, gamma_eff,last tends to the true current value
            # as step size->0. Over one step it retains the solved local
            # non-ideality; the table is a different model (SiO2 gamma=1
            # was 43x too high in the diagnosed refusal). Numerical
            # certification of the last solve is not empirical validation.
            # Keep the previous certificate's (T, X, ln γ) so the next
            # refusal has the secant. Two points only: the new record
            # replaces the parent, and the old record becomes prev.
            gamma_last = float(selected.effective_gamma)
            record: dict[str, Any] = {
                "gamma_last": gamma_last,
                "temperature_K": float(temperature_K),
                "hour": float(hour),
                "window_K": window_K,
                "composition_single_cation": composition,
                "composition_window_L1": composition_window,
                "coefficient_basis": "bulk_single_cation",
            }
            if gamma_last > 0.0 and math.isfinite(gamma_last):
                record["ln_gamma_last"] = math.log(gamma_last)
            previous_certificate = certificates.get(parent)
            if (
                isinstance(previous_certificate, Mapping)
                and float(previous_certificate.get("gamma_last") or 0.0) > 0.0
                and "ln_gamma_last" in record
            ):
                record["gamma_prev"] = float(previous_certificate["gamma_last"])
                record["ln_gamma_prev"] = float(
                    previous_certificate.get("ln_gamma_last")
                    if previous_certificate.get("ln_gamma_last") is not None
                    else math.log(float(previous_certificate["gamma_last"]))
                )
                record["temperature_prev_K"] = float(
                    previous_certificate["temperature_K"]
                )
                record["composition_prev_single_cation"] = dict(
                    previous_certificate.get("composition_single_cation") or {}
                )
            certificates[parent] = record
    # A successful solve ends the refusal streak. The next refusal
    # extrapolates from the new certificate instead of a frozen gamma.
    base["consecutive_refusal_count"] = 0
    base["last_extrapolated_gamma_by_parent"] = {}
    return base


def _high_t_activity_for_parent(
    parent_oxide: str,
    composition_mol: Mapping[str, float],
    *,
    temperature_K: float,
    authority: Mapping[str, Any] | None,
) -> Any:
    """Select the table, solved OpenIMCC, or local effective-gamma hold."""

    legacy = melt_oxide_activity(
        parent_oxide,
        composition_mol,
        temperature_K=temperature_K,
    )
    if not authority:
        return legacy
    if parent_oxide not in _HIGH_T_FLUX_OXIDES or legacy is None:
        return legacy
    if parent_oxide in _HIGH_T_ACTIVITY_AUTHORITY_EXCLUSIONS:
        return legacy
    if authority.get("fallback") and authority.get("provider") != "openimcc":
        fallback_reason = dict(authority.get("fallback_reason") or {})
        held = authority.get("held_effective_gamma_by_parent", {}).get(parent_oxide)
        if held is not None:
            if "gamma_applied" in held:
                gamma = float(held["gamma_applied"])
            else:
                gamma = float(held["gamma_last"])
            method = str(held.get("method") or "hold_last_certified_effective_gamma")
            return replace(
                legacy,
                gamma=gamma,
                effective_gamma=gamma,
                activity=gamma * legacy.x_single_cation,
                activity_model=method,
                citation="Last numerically certified OpenIMCC activity on the bulk single-cation basis",
                warning=(
                    f"{method}:"
                    f"{fallback_reason.get('code', 'openimcc_evaluation_refused')}"
                ),
                evidence_tier="PREDICT_AND_FLAG",
            )
        fallback_warning = (
            "openimcc_refused_constant_gamma_fallback:"
            f"{fallback_reason.get('code', 'openimcc_evaluation_refused')}"
        )
        return replace(
            legacy,
            warning=(
                f"{legacy.warning}; {fallback_warning}"
                if legacy.warning
                else fallback_warning
            ),
            evidence_tier="PREDICT_AND_FLAG",
        )
    if authority.get("provider") != "openimcc":
        return legacy
    activity = float(authority.get("activities_by_oxide", {}).get(parent_oxide, 0.0) or 0.0)
    if activity <= 0.0:
        return legacy
    x_single_cation = max(float(legacy.x_single_cation), 0.0)
    effective_gamma = activity / x_single_cation if x_single_cation > 0.0 else 1.0
    return replace(
        legacy,
        gamma=effective_gamma,
        activity=activity,
        citation=(
            f"openimcc {authority.get('openimcc_pack_model_id', '')} pack "
            f"{authority.get('openimcc_pack_version', '')} "
            f"digest={authority.get('openimcc_pack_digest', '')}"
        ),
        warning=("projected_composition_solve" if authority.get("projected_composition_solve") else None),
        effective_gamma=effective_gamma,
        activity_model=(
            "projected_composition_solve" if authority.get("projected_composition_solve")
            else "openimcc_single_cation_activity"
        ),
        evidence_tier="PREDICT_AND_FLAG",
        authority_status="openimcc",
    )


def _attach_high_t_activity_provenance(
    *,
    authority: Mapping[str, Any] | None,
    metals_data: Mapping[str, Any],
    oxide_vapors_data: Mapping[str, Any],
    vapor_pressure_sources: dict[str, str],
    vapor_pressure_provenance: dict[str, dict[str, Any]],
) -> None:
    """Label each published flux carrier with the C3 activity authority."""

    if not authority:
        return
    provider = str(authority.get("provider") or "constant_gamma")
    fallback_reason = dict(authority.get("fallback_reason") or {})
    for species, provenance in vapor_pressure_provenance.items():
        row = metals_data.get(species) or oxide_vapors_data.get(species) or {}
        parent_oxide = str(row.get("parent_oxide") or "")
        if parent_oxide not in _HIGH_T_FLUX_OXIDES:
            continue
        fallback_applied = bool(authority.get("fallback")) and (
            parent_oxide not in _HIGH_T_ACTIVITY_AUTHORITY_EXCLUSIONS
        )
        if fallback_applied:
            provenance["melt_activity_parent_oxide"] = parent_oxide
        provenance["high_t_melt_activity_mode"] = str(
            authority.get("requested") or "openimcc"
        )
        provenance["melt_activity_temperature_K"] = authority.get(
            "temperature_K"
        )
        seam = authority.get("seam", {}).get(parent_oxide, {})
        exclusion = (
            seam.get("authority_exclusion")
            if provider == "openimcc" and isinstance(seam, Mapping)
            else None
        )
        if exclusion is None and provider == "openimcc":
            if parent_oxide == "K2O":
                exclusion = _k2o_authority_exclusion_for_pack_digest(
                    authority.get("openimcc_pack_digest"),
                    authority.get("openimcc_pack_model_id"),
                    authority.get("openimcc_pack_version"),
                    activity_step_dex=seam.get(
                        "would_be_openimcc_step_dex"
                    ),
                )
            else:
                exclusion = _HIGH_T_ACTIVITY_AUTHORITY_EXCLUSIONS.get(
                    parent_oxide
                )
        elif authority.get("fallback"):
            exclusion = _HIGH_T_ACTIVITY_AUTHORITY_EXCLUSIONS.get(parent_oxide)
        if exclusion is not None:
            provenance["melt_activity_authority"] = exclusion["authority"]
            exclusion_payload = dict(exclusion)
            provenance["openimcc_authority_exclusion"] = exclusion_payload
            if exclusion_payload.get("code"):
                provenance["melt_activity_fallback"] = True
                provenance["openimcc_refusal"] = {
                    "code": exclusion_payload["code"],
                    "reason": exclusion_payload.get("reason"),
                }
            if parent_oxide == "K2O":
                provenance["openimcc_authority_excluded_K2O"] = {
                    "authority": exclusion["authority"],
                    "ruling": exclusion["ruling"],
                    "certification": exclusion["tracking_id"],
                    **(
                        {"reason": exclusion["reason"]}
                        if exclusion.get("reason")
                        else {}
                    ),
                    "citation": exclusion["citation"],
                }
            token = (
                "openimcc_authority_excluded_K2O"
                if parent_oxide == "K2O"
                else "openimcc_authority_excluded_parent"
            )
        elif provider == "openimcc":
            provenance["melt_activity_authority"] = provider
            provenance.update(
                {
                    "openimcc_version": authority.get("openimcc_version"),
                    "openimcc_pack_model_id": authority.get(
                        "openimcc_pack_model_id"
                    ),
                    "openimcc_pack_version": authority.get(
                        "openimcc_pack_version"
                    ),
                    "openimcc_pack_digest": authority.get(
                        "openimcc_pack_digest"
                    ),
                    "openimcc_flags": list(
                        authority.get("openimcc_flags", []) or []
                    ),
                    "openimcc_notices": list(
                        authority.get("openimcc_notices", []) or []
                    ),
                    "openimcc_policy_notices": list(
                        authority.get("openimcc_policy_notices", []) or []
                    ),
                    "openimcc_envelope_status": authority.get(
                        "openimcc_envelope_status"
                    ),
                    "openimcc_extrapolated": bool(
                        authority.get("openimcc_extrapolated", False)
                    ),
                    "openimcc_temperature_domain_status": authority.get(
                        "temperature_domain_status"
                    ),
                    "openimcc_seam": dict(
                        authority.get("seam", {}).get(parent_oxide, {})
                    ),
                }
            )
            token = "openimcc"
        else:
            held = authority.get("held_effective_gamma_by_parent", {}).get(parent_oxide)
            method = (
                str(held.get("method") or "hold_last_certified_effective_gamma")
                if held else None
            )
            provenance["melt_activity_authority"] = method or provider
            provenance["melt_activity_fallback"] = True
            provenance["openimcc_refusal"] = fallback_reason
            token = method or "openimcc_refused_constant_gamma_fallback"
            if held:
                provenance["hold_last_certified_effective_gamma"] = dict(held)
                if held.get("flag"):
                    provenance["melt_activity_flag"] = held["flag"]
            if fallback_reason.get("code"):
                token += f"_{fallback_reason['code']}"
        projected = authority.get("projected_composition_solve")
        if projected and exclusion is None:
            provenance["projected_composition_solve"] = dict(projected)
            provenance["melt_activity_fallback"] = True
            if provider == "openimcc":
                provenance["melt_activity_authority"] = "projected_composition_solve"
                token = "projected_composition_solve"
        classification = authority.get("composition_projection_classification")
        if classification:
            provenance["composition_projection_classification"] = dict(
                classification
            )
            relaxed_projection = (
                classification.get("openimcc_projection_crmn_relaxed")
                if isinstance(classification, Mapping)
                else None
            )
            if isinstance(relaxed_projection, Mapping):
                provenance["openimcc_projection_crmn_relaxed"] = dict(
                    relaxed_projection
                )
        source = str(vapor_pressure_sources.get(species) or "")
        if source and token not in source.split(":"):
            source = f"{source}:{token}"
            vapor_pressure_sources[species] = source
        provenance["source_label"] = source or provenance.get("source_label", "")


def _gamma_domain_authority(
    parent_oxide: str,
    temperature_K: float,
    oxide_activity: Any,
) -> dict[str, Any] | None:
    """Typed authority status for temperature-anchored gamma table rows."""

    from simulator.chemistry.melt_activity import (
        melt_oxide_gamma_domain_authority,
    )

    return melt_oxide_gamma_domain_authority(
        parent_oxide,
        temperature_K,
        gamma=float(oxide_activity.gamma),
    )


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _range_tuple(value: Any) -> tuple[float, float] | None:
    if not value or len(value) != 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _reconstructed_anchor_pressure_Pa(
    species: str,
    row: Mapping[str, Any] | None,
    anchor: Mapping[str, Any],
) -> float:
    """Resolve one declared reconstructed-segment pressure anchor."""

    if anchor.get("pressure_Pa") is not None:
        pressure_Pa = float(anchor["pressure_Pa"])
    elif anchor.get("pressure_rail") == GAS_RAIL_STANDARD_REACTION_KEY:
        gas_rail = _gas_rail_standard_reaction_block(row)
        antoine = (gas_rail or {}).get("antoine", {}) or {}
        temperature_K = float(anchor["temperature_K"])
        try:
            log10_pressure_Pa = _antoine_log10_pressure(
                float(antoine["A"]),
                float(antoine["B"]),
                float(antoine.get("C", 0.0) or 0.0),
                temperature_K,
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise VaporPressureComputationError(
                "invalid_reconstructed_vapor_pressure_anchor: "
                f"species={species} pressure_rail="
                f"{GAS_RAIL_STANDARD_REACTION_KEY}"
            ) from exc
        pressure_Pa = _pow10_pressure_or_raise(
            log10_pressure_Pa,
            species=species,
            field="reconstructed_segment_anchor_pressure_Pa",
        )
    else:
        raise VaporPressureComputationError(
            "invalid_reconstructed_vapor_pressure_anchor: "
            f"species={species} anchor={dict(anchor)!r}"
        )
    if not math.isfinite(pressure_Pa) or pressure_Pa <= 0.0:
        raise VaporPressureComputationError(
            "invalid_reconstructed_vapor_pressure_anchor: "
            f"species={species} pressure_Pa={pressure_Pa!r}"
        )
    return pressure_Pa


def reconstructed_vapor_pressure_authority_limit(
    species: str,
    row: Mapping[str, Any] | None,
    temperature_K: float,
    *,
    consumer: str,
) -> dict[str, Any] | None:
    """Evaluate a declared, authority-limited reciprocal-T pressure segment."""

    segment = (row or {}).get(RECONSTRUCTED_VAPOR_PRESSURE_SEGMENT_KEY)
    if not _is_mapping(segment):
        return None
    bounds = _range_tuple(segment.get("range_K"))
    if bounds is None:
        raise VaporPressureComputationError(
            "invalid_reconstructed_vapor_pressure_segment: "
            f"species={species} missing range_K"
        )
    T_K = float(temperature_K)
    low, high = bounds
    if not low <= T_K <= high:
        return None
    anchors = segment.get("anchors")
    if not isinstance(anchors, list) or len(anchors) != 2 or not all(
        _is_mapping(anchor) for anchor in anchors
    ):
        raise VaporPressureComputationError(
            "invalid_reconstructed_vapor_pressure_segment: "
            f"species={species} requires two anchors"
        )
    anchor_1, anchor_2 = anchors
    T_1 = float(anchor_1["temperature_K"])
    T_2 = float(anchor_2["temperature_K"])
    P_1 = _reconstructed_anchor_pressure_Pa(species, row, anchor_1)
    P_2 = _reconstructed_anchor_pressure_Pa(species, row, anchor_2)
    inverse_span = (1.0 / T_2) - (1.0 / T_1)
    if not math.isfinite(inverse_span) or inverse_span == 0.0:
        raise VaporPressureComputationError(
            "invalid_reconstructed_vapor_pressure_segment: "
            f"species={species} duplicate anchor temperatures"
        )
    fraction = ((1.0 / T_K) - (1.0 / T_1)) / inverse_span
    log10_pressure_Pa = math.log10(P_1) + fraction * (
        math.log10(P_2) - math.log10(P_1)
    )
    pressure_Pa = _pow10_pressure_or_raise(
        log10_pressure_Pa,
        species=species,
        field="reconstructed_vapor_pressure_segment_Pa",
    )
    return {
        "temperature_K": T_K,
        "segment_range_K": (low, high),
        "species": species,
        "consumer": consumer,
        "authority_status": VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_STATUS,
        "authority_flag": VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG,
        VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG: True,
        "authority_reason": "reconstructed_segment",
        "interpolation": segment.get("interpolation"),
        "interpolation_fraction": fraction,
        "pressure_Pa": pressure_Pa,
        "log10_pressure_Pa": log10_pressure_Pa,
        "anchor_points_Pa_K": ((P_1, T_1), (P_2, T_2)),
        "source_basis": segment.get("provenance"),
    }


def vapor_pressure_authority_diagnostic(
    authority_limits: Mapping[str, Mapping[str, Any]],
    *,
    consumer: str,
) -> dict[str, Any]:
    """Expose reconstructed vapor-pressure use with the Ellingham idiom."""

    reconstructed_limited = bool(authority_limits)
    return {
        "consumer": consumer,
        "status": (
            "authority_limited" if reconstructed_limited else "authoritative"
        ),
        VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG: reconstructed_limited,
        "authority_limits": {
            str(species): dict(data)
            for species, data in authority_limits.items()
        },
    }


def _selected_temperature_segment(
    block: Mapping[str, Any],
    temperature_K: float | None,
) -> Mapping[str, Any]:
    segments = block.get("segments")
    if not isinstance(segments, list):
        return block
    candidates = [segment for segment in segments if _is_mapping(segment)]
    if not candidates:
        return block
    if temperature_K is None:
        default_name = str(block.get("default_segment", "") or "")
        for segment in candidates:
            if default_name and str(segment.get("name", "")) == default_name:
                return segment
        return block if block.get("A") is not None else candidates[0]

    ranged: list[tuple[float, float, Mapping[str, Any]]] = []
    for segment in candidates:
        valid_range = _range_tuple(
            segment.get("valid_range_K")
            or segment.get("source_certified_range_K")
            or segment.get("source_equation_range_K")
            or segment.get("fit_range_K")
        )
        if valid_range is None:
            continue
        low, high = valid_range
        if low <= temperature_K <= high:
            return segment
        ranged.append((low, high, segment))
    if not ranged:
        return candidates[-1]
    ranged.sort(key=lambda item: item[0])
    if temperature_K < ranged[0][0]:
        return ranged[0][2]
    return ranged[-1][2]


def _coefficient_mapping(
    row: Mapping[str, Any] | None,
    coefficient_block: str | None,
    *,
    temperature_K: float | None = None,
) -> Mapping[str, Any]:
    block = (row or {}).get(coefficient_block or "")
    if not _is_mapping(block):
        return {}
    if coefficient_block == COEFF_BLOCK_PURE_COMPONENT:
        return _selected_temperature_segment(block, temperature_K)
    return block


def _source_text(
    row: Mapping[str, Any] | None,
    coefficient_block: str | None,
    *,
    temperature_K: float | None = None,
) -> str:
    if coefficient_block:
        block = _coefficient_mapping(
            row,
            coefficient_block,
            temperature_K=temperature_K,
        )
        if _is_mapping(block) and block.get("source"):
            return str(block.get("source"))
    return str((row or {}).get("source", "") or "")


def _is_legacy_or_uncertified_source(source: str) -> bool:
    text = source.lower()
    return any(
        token in text
        for token in (
            "legacy_derivation_value",
            "source_class=legacy_derivation",
            "ungrounded",
            "interval",
            "todo replace",
        )
    )


def _has_grounded_pure_component_source(
    row: Mapping[str, Any] | None,
    coefficient_block: str | None,
    *,
    temperature_K: float | None = None,
) -> bool:
    if coefficient_block != COEFF_BLOCK_PURE_COMPONENT:
        return False
    if bool((row or {}).get("interval_required")):
        return False
    coeff = (row or {}).get(COEFF_BLOCK_PURE_COMPONENT)
    if not _is_mapping(coeff):
        return False
    source = _source_text(row, coefficient_block, temperature_K=temperature_K)
    return bool(source) and not _is_legacy_or_uncertified_source(source)


def vapor_pressure_antoine_coefficients(
    row: Mapping[str, Any] | None,
    temperature_K: float | None = None,
) -> tuple[Mapping[str, Any], str]:
    """Return the runtime Antoine block and its provenance key."""

    if _fit_target(row) == FIT_TARGET_STANDARD_REACTION:
        antoine = (row or {}).get(COEFF_BLOCK_ANTOINE)
        if _is_mapping(antoine):
            return antoine, COEFF_BLOCK_ANTOINE

    if not bool((row or {}).get("interval_required")):
        pure = (row or {}).get(COEFF_BLOCK_PURE_COMPONENT)
        if _is_mapping(pure):
            selected = _selected_temperature_segment(pure, temperature_K)
            if not _pure_segment_usable(selected, temperature_K):
                antoine = (row or {}).get(COEFF_BLOCK_ANTOINE)
                if _is_mapping(antoine):
                    return antoine, COEFF_BLOCK_ANTOINE
            return (
                selected,
                COEFF_BLOCK_PURE_COMPONENT,
            )
    antoine = (row or {}).get(COEFF_BLOCK_ANTOINE)
    if _is_mapping(antoine):
        return antoine, COEFF_BLOCK_ANTOINE
    return {}, COEFF_BLOCK_ANTOINE


def _pure_segment_usable(
    selected: Mapping[str, Any],
    temperature_K: float | None,
) -> bool:
    """Shared pole/overflow guard for pure-component segment selection.

    A segment is unusable when its Antoine form diverges at ``temperature_K``
    (pole in the denominator) or projects a nonphysical pressure; callers
    then fall back rather than evaluate a divergent fit (e.g. the Ca
    Hartmann-Schneider fit has a pole at 594.6 K).
    """

    if temperature_K is None:
        return True
    try:
        denominator = float(temperature_K) + float(selected.get("C", 0.0))
        # Antoine's shifted inverse-temperature fit is calibrated on the
        # T + C > 0 branch; crossing its pole is not a physical continuation.
        if denominator <= 0.0:
            return False
        projected_log_pressure = _antoine_log10_pressure(
            float(selected.get("A", 0.0)),
            float(selected.get("B", 0.0)),
            float(selected.get("C", 0.0)),
            float(temperature_K),
        )
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return math.isfinite(projected_log_pressure) and projected_log_pressure <= 308.0


def wall_condensation_antoine_coefficients(
    row: Mapping[str, Any] | None,
    temperature_K: float | None = None,
) -> tuple[Mapping[str, Any], str]:
    """Return coefficients for local wall ``P_sat`` consumers.

    Melt-source standard-reaction terms are not pure-species saturation
    pressures. When a standard-reaction row carries a grounded pure-component
    sidecar usable at this temperature, wall re-evaporation uses that sidecar;
    otherwise it fails closed (empty block -> caller types a refusal; never
    invents a fiat pressure) rather than serve the melt activity/pO2 term as
    a local P_sat. Non-standard-reaction rows keep the runtime selector's
    behavior byte-for-byte.
    """

    if _fit_target(row) != FIT_TARGET_STANDARD_REACTION:
        return vapor_pressure_antoine_coefficients(row, temperature_K)
    if not bool((row or {}).get("interval_required")):
        pure = (row or {}).get(COEFF_BLOCK_PURE_COMPONENT)
        if _is_mapping(pure):
            selected = _selected_temperature_segment(pure, temperature_K)
            if _pure_segment_usable(selected, temperature_K):
                return selected, COEFF_BLOCK_PURE_COMPONENT
            # A grounded pure-species wall answer exists but not at this
            # temperature: fail closed so the caller types a refusal rather
            # than extrapolate through a pole or substitute the melt term.
            return {}, COEFF_BLOCK_PURE_COMPONENT
    return {}, COEFF_BLOCK_PURE_COMPONENT


def vapor_pressure_valid_range_K(
    row: Mapping[str, Any] | None,
    coefficient_block: str | None,
    temperature_K: float | None = None,
) -> Any:
    block = _coefficient_mapping(
        row,
        coefficient_block,
        temperature_K=temperature_K,
    )
    if _is_mapping(block) and block.get("valid_range_K") is not None:
        return block.get("valid_range_K")
    if (
        coefficient_block == COEFF_BLOCK_PURE_COMPONENT
        and _fit_target(row) == FIT_TARGET_PSEUDO_VAPOROCK
    ):
        # The row-level range certifies the fallback pseudo-Antoine fit, not
        # the independently sourced pure-component sidecar selected here.
        return None
    return (row or {}).get("valid_range_K")


def vapor_pressure_source_equation_range_K(
    row: Mapping[str, Any] | None,
    coefficient_block: str | None,
    temperature_K: float | None = None,
) -> Any:
    block = _coefficient_mapping(
        row,
        coefficient_block,
        temperature_K=temperature_K,
    )
    if _is_mapping(block) and block.get("source_equation_range_K") is not None:
        return block.get("source_equation_range_K")
    if _is_mapping(block) and block.get("source_certified_range_K") is not None:
        return block.get("source_certified_range_K")
    return (row or {}).get("source_equation_range_K") or (row or {}).get(
        "source_certified_range_K"
    )


def _source_range_extrapolation_suffix(
    row: Mapping[str, Any] | None,
    coefficient_block: str | None,
    *,
    temperature_K: float | None = None,
) -> str:
    block = _coefficient_mapping(
        row,
        coefficient_block,
        temperature_K=temperature_K,
    )
    if _is_mapping(block) and block.get("source_certified_range_K") is not None:
        return "extrapolated_beyond_source_certified_range_K"
    if (row or {}).get("source_certified_range_K") is not None:
        return "extrapolated_beyond_source_certified_range_K"
    return "extrapolated_beyond_source_equation_range_K"


def _is_temperature_in_range(
    temperature_K: float | None,
    valid_range: Any,
) -> bool:
    if temperature_K is None:
        return True
    if not valid_range or len(valid_range) != 2:
        return True
    low = float(valid_range[0])
    high = float(valid_range[1])
    return low <= float(temperature_K) <= high


def _source_base_for_fit_target(base_source: str, fit_target: str) -> str:
    if (
        fit_target == FIT_TARGET_PSEUDO_VAPOROCK
        and base_source in _BUILTIN_VAPOR_SOURCE_CLASSES
    ):
        return PSEUDO_VAPOROCK_CURVE_FIT_SOURCE
    return base_source


def vapor_pressure_source_label(
    base_source: str,
    row: Mapping[str, Any] | None,
    *,
    coefficient_block: str | None = None,
    temperature_K: float | None = None,
    authority_limited_by_ellingham_fit_range: bool = False,
) -> str:
    """Return honest provenance for an Antoine vapor-pressure row."""

    if (
        authority_limited_by_ellingham_fit_range
        and base_source == "builtin_authoritative"
    ):
        base_source = "builtin_extrapolation_limited"

    # Provenance tiers:
    # - source_equation_fit: source-published empirical Antoine/vapor equation,
    #   used as published (unit conversion only), not a local re-fit.
    # - source_tabulated_fit: local fit to source-published tabulated p(T) data.
    # - derived_from_evaluation: coefficients derived from an evaluated thermo
    #   dataset (JANAF/NIST Shomate, or dH_vap + Tb Clausius-Clapeyron anchor).
    # - pure_component_unspecified: grounded sidecar lacking a tier annotation;
    #   deliberately non-overclaiming.
    # - pure_component_first_principles: reserved for genuine derivation from
    #   physical constants/definitions.
    target = _fit_target(row)
    if _has_grounded_pure_component_source(
        row,
        coefficient_block,
        temperature_K=temperature_K,
    ):
        source_range = vapor_pressure_source_equation_range_K(
            row,
            coefficient_block,
            temperature_K=temperature_K,
        )
        if not _is_temperature_in_range(temperature_K, source_range):
            suffix = _source_range_extrapolation_suffix(
                row,
                coefficient_block,
                temperature_K=temperature_K,
            )
            return f"{base_source}:pure_component_extrapolated:{suffix}"
        coeff = _coefficient_mapping(
            row,
            coefficient_block,
            temperature_K=temperature_K,
        )
        provenance_class = str(
            coeff.get("provenance_class")
            or coeff.get("source_certification")
            or ""
        ).lower()
        if provenance_class in {"source_equation_fit", "source-equation-fit"}:
            return f"{base_source}:pure_component_source_equation_fit"
        if provenance_class in {"source_tabulated_fit", "source-table-fit"}:
            return f"{base_source}:pure_component_source_tabulated_fit"
        if provenance_class in {"derived_from_evaluation", "evaluation_fit"}:
            return f"{base_source}:pure_component_derived_from_evaluation"
        if provenance_class in {"pure_component_first_principles", "first_principles"}:
            return f"{base_source}:pure_component_first_principles"
        return f"{base_source}:pure_component_unspecified"
    if coefficient_block == COEFF_BLOCK_PURE_COMPONENT:
        return f"{base_source}:pure_component_legacy_derivation"
    if bool((row or {}).get("interval_required")):
        return f"{base_source}:interval_required_uncertified"
    if target == FIT_TARGET_PURE_COMPONENT:
        return f"{base_source}:legacy_pure_component_estimate"
    if target == FIT_TARGET_PSEUDO_VAPOROCK:
        base_source = _source_base_for_fit_target(base_source, target)
        return f"{base_source}:backsolved_vaporock_curve_fit"
    if target == FIT_TARGET_STANDARD_REACTION:
        return f"{base_source}:standard_reaction_term"
    if target:
        return f"{base_source}:fit_target={target}"
    return base_source


def _runtime_pressure_kind(
    row: Mapping[str, Any] | None,
    coefficient_block: str | None,
    *,
    effective_scaled: bool,
) -> str:
    if effective_scaled:
        return "effective_equilibrium"
    if (
        _fit_target(row) == FIT_TARGET_PSEUDO_VAPOROCK
        and coefficient_block == COEFF_BLOCK_ANTOINE
    ):
        return "pseudo_vaporock_fit"
    return "pure_reference"


def _metadata_value(row: Mapping[str, Any] | None, field: str) -> str:
    value = (row or {}).get(field)
    if value is None or value == "":
        return "unknown"
    return str(value)


def _is_high_uncertainty(row: Mapping[str, Any] | None) -> bool:
    residual = (row or {}).get("residual_dex")
    try:
        if residual is not None and float(residual) >= 1.0:
            return True
    except (TypeError, ValueError):
        pass
    tier = str((row or {}).get("confidence_tier", "") or "").lower()
    tier = tier.replace("-", "_").replace(" ", "_")
    return tier in {"low", "very_low", "weak", "poor", "experimental"}


def _species_authority_fields(
    row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Machine-readable authority/bracket labels from YAML (pressure-neutral).

    Mirrors Bug B ``authority_class`` emission: labels ride on diagnostics /
    provenance without changing computed P_eq. Used for interim demotions
    (e.g. Na Alternative B compensating-errors bracket).
    """

    if not _is_mapping(row):
        return {}
    out: dict[str, Any] = {}
    authority = row.get("authority_class")
    if authority is not None and str(authority).strip():
        out["authority_class"] = str(authority).strip()
    if "declared_compensation" in row:
        out["declared_compensation"] = bool(row.get("declared_compensation"))
    note = row.get("declared_compensation_note")
    if note is not None and str(note).strip():
        out["declared_compensation_note"] = str(note).strip()
    bracket = row.get("pressure_bracket")
    if _is_mapping(bracket):
        # Shallow copy; nested scalars only in the declared schema.
        out["pressure_bracket"] = dict(bracket)
        candidates = bracket.get("candidates")
        if _is_mapping(candidates):
            out["pressure_bracket"]["candidates"] = dict(candidates)
    # t-383: coherent_pair + retired shadow_bracket replace declared_compensation
    # for Na; pass through without changing P_eq (pressure-neutral labels).
    coherent = row.get("coherent_pair")
    if _is_mapping(coherent):
        out["coherent_pair"] = dict(coherent)
    shadow = row.get("shadow_bracket")
    if _is_mapping(shadow):
        out["shadow_bracket"] = dict(shadow)
    status = row.get("pseudo_antoine_status")
    if status is not None and str(status).strip():
        out["pseudo_antoine_status"] = str(status).strip()
    return out


def warn_pseudo_vapor_pressure_fallback(
    species: str,
    row: Mapping[str, Any] | None,
    seen_species: set[str],
    *,
    stacklevel: int = 2,
) -> bool:
    """Warn once when a pseudo VapoRock curve-fit row produces pressure."""

    if _fit_target(row) != FIT_TARGET_PSEUDO_VAPOROCK:
        return False

    key = str(species)
    if key in seen_species:
        return False
    seen_species.add(key)

    high_uncertainty = _is_high_uncertainty(row)
    residual = _metadata_value(row, "residual_dex")
    tier = _metadata_value(row, "confidence_tier")
    prefix = "HIGH-UNCERTAINTY WARNING" if high_uncertainty else "WARNING"
    category = (
        HighUncertaintyVaporPressureFallbackWarning
        if high_uncertainty
        else VaporPressureFallbackWarning
    )
    runtime_warnings.warn(
        f"{prefix}: {key} vapor pressure uses a backsolved VapoRock "
        "fallback (curve-fit), NOT first-principles; "
        f"residual_dex={residual}; confidence_tier={tier}; "
        "builtin provider emits a VapoRock-derived curve-fit; "
        "VapoRock runtime is diagnostic-only.",
        category,
        stacklevel=stacklevel,
    )
    return True


def _is_noncertifying_pseudo_vapor_pressure_runtime(
    species: str,
    row: Mapping[str, Any] | None,
    coefficient_block: str | None,
    *,
    temperature_K: float | None,
) -> bool:
    if (
        coefficient_block != COEFF_BLOCK_ANTOINE
        or _fit_target(row) != FIT_TARGET_PSEUDO_VAPOROCK
        or not _is_high_uncertainty(row)
    ):
        return False
    valid_range = (row or {}).get("valid_range_K")
    if temperature_K is None or not valid_range or len(valid_range) != 2:
        return False
    return float(temperature_K) > float(valid_range[1])


def reject_noncertifying_vapor_pressure_row(
    species: str,
    row: Mapping[str, Any] | None,
    coefficient_block: str | None,
) -> None:
    """Fail before uncertified vapor rows can become authoritative pressure."""

    if bool((row or {}).get("interval_required")) and not (row or {}).get(
        "certified_point"
    ):
        raise VaporPressureComputationError(
            "non_certifying_interval_vapor_pressure: "
            f"species={species} interval_required row lacks certified_point"
        )


def require_antoine_source_certified_temperature(
    species: str,
    row: Mapping[str, Any] | None,
    coefficient_block: str | None,
    temperature_K: float,
    *,
    consumer: str,
) -> dict[str, Any] | None:
    """Refuse Antoine evaluation outside an explicit source-certified range."""

    total_bounds = _range_tuple(
        (row or {}).get("total_source_certified_range_K")
    )
    if total_bounds is not None:
        total_low, total_high = total_bounds
        if not total_low <= float(temperature_K) <= total_high:
            raise VaporPressureRangeError(
                "metal_vapor_pressure_out_of_source_certified_range: "
                f"species={species} consumer={consumer} "
                f"temperature_K={float(temperature_K):.3f} "
                "source_certified_range_K="
                f"[{total_low:g}, {total_high:g}]"
            )

    reconstructed_limit = reconstructed_vapor_pressure_authority_limit(
        species,
        row,
        temperature_K,
        consumer=consumer,
    )
    if reconstructed_limit is not None:
        return reconstructed_limit

    block = _coefficient_mapping(
        row,
        coefficient_block,
        temperature_K=temperature_K,
    )
    certified_range = None
    if _is_mapping(block):
        certified_range = block.get("source_certified_range_K")
        extrapolation_policy = block.get("extrapolation_policy")
    else:
        extrapolation_policy = None
    if certified_range is None:
        certified_range = (row or {}).get("source_certified_range_K")
    if extrapolation_policy is None:
        extrapolation_policy = (row or {}).get("extrapolation_policy")
    policy = str(extrapolation_policy or "").strip().lower()
    if policy != "refuse":
        return None
    bounds = _range_tuple(certified_range)
    if bounds is None:
        return None
    low, high = bounds
    if low <= float(temperature_K) <= high:
        return None
    raise VaporPressureRangeError(
        "metal_vapor_pressure_out_of_source_certified_range: "
        f"species={species} consumer={consumer} "
        f"temperature_K={float(temperature_K):.3f} "
        f"source_certified_range_K=[{low:g}, {high:g}]"
    )


def _require_finite_vapor_value(
    value: float,
    *,
    species: str,
    field: str,
) -> float:
    try:
        checked = float(value)
    except (TypeError, ValueError) as exc:
        raise VaporPressureComputationError(
            f"vapor_pressure_nonfinite: species={species} field={field} "
            f"value={value!r}"
        ) from exc
    if not math.isfinite(checked):
        raise VaporPressureComputationError(
            f"vapor_pressure_nonfinite: species={species} field={field} "
            f"value={value!r}"
        )
    return checked


def _ellingham_authority_limit(
    temperature_K: float,
    *,
    species: str,
) -> dict[str, object] | None:
    return ellingham_authority_limit(
        temperature_K,
        species=species,
        consumer="builtin-vapor-pressure",
    )


def _pow10_pressure_or_raise(
    log_pressure: float,
    *,
    species: str,
    field: str,
) -> float:
    try:
        pressure = 10.0 ** float(log_pressure)
    except OverflowError as exc:
        raise VaporPressureNumericalOverflowError(
            f"vapor_pressure_numerical_overflow: species={species} field={field} "
            f"log_pressure={log_pressure!r}"
        ) from exc
    return _require_finite_vapor_value(
        pressure,
        species=species,
        field=field,
    )


class BuiltinVaporPressureProvider(ChemistryProvider):
    """Authoritative ``VAPOR_PRESSURE`` provider (Antoine + Ellingham).

    See module docstring. The provider declares VAPOR_PRESSURE in
    :attr:`CapabilityProfile.is_authoritative_for` and owns the pressure
    surface consumed by evaporation. ``vapor_pressure_data`` is the parsed
    ``data/vapor_pressures.yaml`` payload (keys: ``metals``,
    ``oxide_vapors``).
    """

    name = "builtin-vapor-pressure"

    DECLARED_ACCOUNT = "process.cleaned_melt"

    def __init__(
        self,
        vapor_pressure_data: Mapping[str, Any],
    ) -> None:
        from simulator.vapour_rail.catalog import (
            compile_vapour_rail_catalog,
            vapor_pressure_legacy_view,
        )

        compatibility_payload = getattr(vapor_pressure_data, "catalog_payload", None)
        catalog_payload = (
            vapor_pressure_data
            if vapor_pressure_data.get("schema_version") == 2
            else compatibility_payload
        )
        self._vapour_rail_catalog = (
            compile_vapour_rail_catalog(catalog_payload)
            if isinstance(catalog_payload, Mapping)
            and catalog_payload.get("schema_version") == 2
            else None
        )
        self._vapor_pressure_data = vapor_pressure_legacy_view(vapor_pressure_data)
        self._pseudo_vapor_pressure_warning_seen: set[str] = set()

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-vapor-pressure",
            intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            is_authoritative_for=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            declared_accounts=frozenset({self.DECLARED_ACCOUNT}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        # Lazy import: simulator.state pulls in simulator/__init__ which
        # re-enters this module during package init -- see
        # engines/builtin/__init__.py for the cycle description.
        from simulator.state import GAS_CONSTANT
        from simulator.vapour_rail.channels import (
            REACTION_PLANE_MELT_INTERFACE,
            REACTION_PLANE_TRANSPORT_HEADSPACE,
            channel_linear_mass_action_factor,
            o2_potential_from_pO2_bar,
        )

        wrong_intent = reject_wrong_intent(request, ChemistryIntent.VAPOR_PRESSURE)
        if wrong_intent is not None:
            return wrong_intent

        # The Antoine + Ellingham math runs verbatim against the request's
        # T/P/fO2 with no independent feedback. Audit reports applied ==
        # requested with the diagnostic-only note documented in
        # diagnostic_control_audit.
        control_audit = diagnostic_control_audit(request)

        T_C = request.temperature_C
        T_K = T_C + 273.15
        if T_K < 400:
            # Mirrors _internal_analytical_equilibrium: below 400 K, no significant
            # evaporation. Return an empty vapor-pressure dict with an
            # 'ok' status -- this is a converged outcome, not a failure.
            return IntentResult(
                intent=ChemistryIntent.VAPOR_PRESSURE,
                status="ok",
                control_audit=control_audit,
                diagnostic={"vapor_pressures_Pa": {}, "activities": {}},
            )

        vacuum_floor_bar = resolve_request_vacuum_floor_bar(request)
        transport_pO2_bar = self._resolve_transport_pO2_bar(request)
        controls = request.control_inputs or {}
        intrinsic_fO2_log_supplied = (
            'intrinsic_fO2_log' in controls
            and controls.get('intrinsic_fO2_log') is not None
        )
        # External callers that omit the explicit intrinsic-melt channel
        # keep the legacy FeO wt-fraction activity path.
        intrinsic_fO2_log = (
            self._resolve_intrinsic_melt_fO2_log(request)
            if intrinsic_fO2_log_supplied
            else None
        )
        melt_dissociation_pO2_bar = transport_pO2_bar
        melt_dissociation_pO2_clamped = False
        if intrinsic_fO2_log is not None:
            melt_dissociation_pO2_bar, melt_dissociation_pO2_clamped = (
                physical_melt_dissociation_pO2_bar(float(intrinsic_fO2_log))
            )
        comp_wt = composition_wt_pct_from_account_view(
            request.account_view, self.DECLARED_ACCOUNT
        )
        from simulator.chemistry.structural_activity import (
            structural_activity_diagnostic,
        )

        structural_activity_reference = structural_activity_diagnostic(
            request.account_view.accounts.get(self.DECLARED_ACCOUNT, {}),
            temperature_K=T_K,
        )
        warnings: list[str] = []
        melt_account_mol = dict(
            request.account_view.accounts.get(self.DECLARED_ACCOUNT, {}) or {}
        )
        feo_activity_pressure_bar = vacuum_floor_bar
        if intrinsic_fO2_log is not None:
            from simulator.fe_redox import (
                kress91_ferrous_feo_activity,
                kress91_furnace_activity_pressure_bar,
            )

            feo_activity_pressure_bar = kress91_furnace_activity_pressure_bar(
                floor_bar=vacuum_floor_bar,
            )
            below_cap_fe_activity = kress91_ferrous_feo_activity(
                comp_wt=comp_wt,
                fO2_log=intrinsic_fO2_log,
                T_K=T_K,
                pressure_bar=feo_activity_pressure_bar,
                floor_bar=vacuum_floor_bar,
            )
            below_cap_fe_activity_basis = "kress91_ferrous"
        else:
            below_cap_fe_activity = comp_wt.get("FeO", 0.0) / 100.0
            below_cap_fe_activity_basis = "feo_weight_fraction"
        high_t_activity_authority = _build_high_t_melt_activity_authority(
            composition_mol=melt_account_mol,
            temperature_K=T_K,
            controls=controls,
            below_cap_fe_activity=below_cap_fe_activity,
            below_cap_fe_activity_basis=below_cap_fe_activity_basis,
        )
        # An openimcc activity can move a valid carrier below the numerical
        # output floor; preserve the carrier so predict-and-flag never drops it.
        retain_high_t_openimcc_carriers = bool(
            high_t_activity_authority
            and (
                high_t_activity_authority.get("provider") == "openimcc"
                or high_t_activity_authority.get("held_effective_gamma_by_parent")
            )
        )
        if high_t_activity_authority:
            for notice in high_t_activity_authority.get("notices", ()):
                warnings.append(
                    f"{notice.get('code', 'high_t_melt_activity_notice')}: "
                    f"{notice.get('message', '')}"
                )
        feo_activity_diagnostic = None
        if intrinsic_fO2_log is not None:
            from simulator.fe_redox import (
                calphad_ferrous_feo_activity_diagnostic,
            )

            feo_activity_diagnostic = calphad_ferrous_feo_activity_diagnostic(
                comp_wt=comp_wt,
                fO2_log=intrinsic_fO2_log,
                T_K=T_K,
                pressure_bar=feo_activity_pressure_bar,
                floor_bar=vacuum_floor_bar,
            )

        vapor_pressures: dict[str, float] = {}
        vapor_pressure_sources: dict[str, str] = {}
        vapor_pressure_provenance: dict[str, dict[str, Any]] = {}
        activities: dict[str, float] = {}
        metal_extrapolations: dict[str, dict[str, object]] = {}
        oxide_vapor_extrapolations: dict[str, dict[str, object]] = {}
        ellingham_extrapolations: dict[str, dict[str, object]] = {}
        vapor_pressure_authority_limits: dict[str, dict[str, Any]] = {}
        if melt_dissociation_pO2_clamped and intrinsic_fO2_log is not None:
            # Visible, non-authoritative: redox returned a non-physical fO2;
            # mass action uses the physical envelope edge so oxygen-dependent
            # carriers (AlO2, CrO2, CrO3, …) cannot invent multi-GPa vapor
            # from a float clamp (b-148).
            warnings.append(
                "melt_dissociation_pO2_clamped_to_physical_envelope: "
                f"fO2_log={float(intrinsic_fO2_log):.6g} "
                f"pO2_bar={melt_dissociation_pO2_bar:g} "
                f"envelope_bar=[{MELT_DISSOCIATION_PO2_MIN_BAR:g}, "
                f"{MELT_DISSOCIATION_PO2_MAX_BAR:g}]"
            )

        metals_data = self._vapor_pressure_data.get('metals', {}) or {}
        for species in _ELLINGHAM_THERMO:
            n_M, n_ox = ellingham_stoichiometry(species)
            sp_data = metals_data.get(species, {}) or {}
            if not sp_data:
                continue
            if str(sp_data.get('consumer_status', '')).lower() == 'inactive':
                continue

            parent_oxide = sp_data.get('parent_oxide', '')
            if not parent_oxide:
                continue
            # A fit range has no diagnostic meaning when its parent oxide is
            # absent; gate presence before selecting or warning about the fit.
            if float(comp_wt.get(parent_oxide, 0.0) or 0.0) <= 0.0:
                continue

            fit_target = _fit_target(sp_data)
            metal_phase_kind = ellingham_metal_phase_kind(species, T_K)
            gas_standard_rail = (
                fit_target != FIT_TARGET_STANDARD_REACTION
                and metal_phase_kind == ELLINGHAM_METAL_PHASE_GAS
            )
            gas_rail_rxn_early = _gas_rail_standard_reaction_block(sp_data)
            liquid_rxn_early = _liquid_oxide_standard_reaction_block(sp_data)
            # Oxide-coupled liquid-standard rows (Al/Ti/Cr/Mn) do not use the
            # pure-component Antoine block for runtime P; that sidecar is NBP-
            # only. Skip condensed-rail loading so pure-range refuse does not
            # block the liquid-oxide standard path.
            coefficient_block: str | None = None
            P_reference_Pa: float | None = None
            reconstructed_vapor_limit: dict[str, Any] | None = None
            compiled_reference_evaluator = None
            if not gas_standard_rail and liquid_rxn_early is None:
                compiled_reference_Pa: float | None = None
                antoine, coefficient_block = vapor_pressure_antoine_coefficients(
                    sp_data,
                    temperature_K=T_K,
                )
                if (
                    fit_target == FIT_TARGET_STANDARD_REACTION
                    and self._vapour_rail_catalog is not None
                    and (
                        not antoine
                        or bool(
                            sp_data.get("prefer_compiled_reference_pressure_model")
                        )
                    )
                ):
                    evaluator = self._vapour_rail_catalog.evaluator_for_hot_train(
                        species,
                        process_phase=controls.get("process_phase"),
                    )
                    compiled_reference_evaluator = evaluator
                    # This is the declared standard-reaction reference point,
                    # not a live melt evaluation. Make both neutral inputs
                    # explicit so activity/fO2 can never default silently.
                    compiled_reference_Pa = evaluator.evaluate(
                        T_K,
                        source_activity=1.0,
                        pO2_bar=evaluator.pO2_reference_bar,
                    ).pressure_pa
                    coefficient_block = "compiled_reference_pressure_model"
                if _is_noncertifying_pseudo_vapor_pressure_runtime(
                    species,
                    sp_data,
                    coefficient_block,
                    temperature_K=T_K,
                ):
                    warnings.append(
                        "non_certifying_vapor_pressure_fallback_omitted: "
                        f"species={species} "
                        f"fit_target={FIT_TARGET_PSEUDO_VAPOROCK} "
                        f"residual_dex={_metadata_value(sp_data, 'residual_dex')} "
                        f"confidence_tier={_metadata_value(sp_data, 'confidence_tier')}"
                    )
                    continue
                if bool(sp_data.get("interval_required")):
                    reject_noncertifying_vapor_pressure_row(
                        species,
                        sp_data,
                        coefficient_block,
                    )
                A = antoine.get('A', 0)
                B = antoine.get('B', 0)
                C = antoine.get('C', 0)
                if compiled_reference_Pa is None and not (A > 0 and T_K > 300):
                    continue
                reconstructed_segment = sp_data.get(
                    RECONSTRUCTED_VAPOR_PRESSURE_SEGMENT_KEY
                )
                reconstructed_bounds = (
                    _range_tuple(reconstructed_segment.get("range_K"))
                    if _is_mapping(reconstructed_segment)
                    else None
                )
                if (
                    reconstructed_bounds is not None
                    and T_K >= reconstructed_bounds[0]
                ):
                    reconstructed_vapor_limit = (
                        require_antoine_source_certified_temperature(
                            species,
                            sp_data,
                            coefficient_block,
                            T_K,
                            consumer="builtin_condensed_rail",
                        )
                    )
                    if reconstructed_vapor_limit is not None:
                        vapor_pressure_authority_limits[species] = (
                            reconstructed_vapor_limit
                        )
                certified_range = _range_tuple(
                    antoine.get("source_certified_range_K")
                    or sp_data.get("source_certified_range_K")
                )
                if (
                    str(sp_data.get("extrapolation_policy", "")).lower()
                    == "refuse"
                    and certified_range is not None
                    and T_K < certified_range[0]
                ):
                    warnings.append(
                        "metal_vapor_pressure_out_of_source_certified_range: "
                        f"species={species} consumer=builtin_condensed_rail "
                        f"temperature_K={T_K:.3f} "
                        "source_certified_range_K="
                        f"[{certified_range[0]:g}, {certified_range[1]:g}]"
                    )
                    continue
                if reconstructed_vapor_limit is None:
                    require_antoine_source_certified_temperature(
                        species,
                        sp_data,
                        coefficient_block,
                        T_K,
                        consumer="builtin_condensed_rail",
                    )
                    valid_range = (
                        compiled_reference_evaluator.valid_temperature_K
                        if compiled_reference_evaluator is not None
                        else vapor_pressure_valid_range_K(
                            sp_data,
                            coefficient_block,
                            temperature_K=T_K,
                        )
                    )
                    if valid_range and len(valid_range) == 2:
                        valid_low = float(valid_range[0])
                        valid_high = float(valid_range[1])
                        if T_K < valid_low or T_K > valid_high:
                            metal_extrapolations[species] = {
                                "temperature_K": T_K,
                                "valid_range_K": (valid_low, valid_high),
                            }
                            warnings.append(
                                f"{species} metal Antoine fit extrapolated beyond "
                                f"valid_range_K [{valid_low:g}, {valid_high:g}] at "
                                f"{T_K:.3f} K"
                            )
                    if compiled_reference_Pa is not None:
                        P_reference_Pa = compiled_reference_Pa
                    else:
                        log_P = _antoine_log10_pressure(A, B, C, T_K)
                        P_reference_Pa = _pow10_pressure_or_raise(
                            log_P,
                            species=species,
                            field="P_reference_Pa",
                        )
                else:
                    P_reference_Pa = float(
                        reconstructed_vapor_limit["pressure_Pa"]
                    )

            if _fit_target(sp_data) == FIT_TARGET_STANDARD_REACTION:
                assert P_reference_Pa is not None
                retain_analytical_channel = bool(
                    sp_data.get("retain_analytical_pressure_channel", False)
                )
                oxide_activity = _high_t_activity_for_parent(
                    parent_oxide,
                    melt_account_mol,
                    temperature_K=T_K,
                    authority=high_t_activity_authority,
                )
                if oxide_activity is None or oxide_activity.activity <= 0.0 or (
                    not retain_analytical_channel
                    and oxide_activity.activity <= 1e-10
                ):
                    continue
                if oxide_activity.warning:
                    warnings.append(oxide_activity.warning)
                activities[species] = oxide_activity.activity

                # provenance: liquid_oxide_standard_reaction
                # K: Lamoreaux & Hildenbrand 1984 Tables 2/4 liquid KO0.5.
                # Al/Ti/Cr/Mn: TE liquid oxide mu0 + JANAF metal(g) (2026-07-20
                # pairing fix). Solid-oxide Ellingham is not used here.
                activity_exponent = float(
                    sp_data.get("oxide_activity_exponent", 1.0) or 1.0
                )
                pO2_exponent = float(sp_data.get("pO2_exponent", 0.0) or 0.0)
                pO2_reference_bar = max(
                    1e-30,
                    float(sp_data.get("pO2_reference_bar", 1.0) or 1.0),
                )
                # t-571: O2 enters through channel #1 (owner-gated,
                # envelope-clamped, receipted) — bit-identical linear form.
                o2_term, o2_potential = _o2_channel_term_and_potential(
                    pO2_exponent=pO2_exponent,
                    pO2_bar=melt_dissociation_pO2_bar,
                    pO2_reference_bar=pO2_reference_bar,
                    temperature_K=T_K,
                    reaction_plane=REACTION_PLANE_MELT_INTERFACE,
                )
                P_eq_raw, activity_factor, pO2_scaled = (
                    _standard_reaction_pressure_Pa(
                        P_reference_Pa=P_reference_Pa,
                        oxide_activity_value=oxide_activity.activity,
                        activity_exponent=activity_exponent,
                        o2_term=o2_term,
                        o2_potential=o2_potential,
                    )
                )
                P_eq_Pa = _require_finite_vapor_value(
                    P_eq_raw,
                    species=species,
                    field="P_eq_standard_reaction",
                )
                gamma_auth = _gamma_domain_authority(
                    parent_oxide, T_K, oxide_activity
                )
                if (
                    gamma_auth is not None
                    and gamma_auth["authority_status"] == "out_of_gamma_domain"
                ):
                    warnings.append(
                        f"{species} melt-oxide gamma out of declared domain "
                        f"gamma_domain_K=[{gamma_auth['gamma_domain_K'][0]:g}, "
                        f"{gamma_auth['gamma_domain_K'][1]:g}] at "
                        f"{T_K:.2f} K (regular-solution continuation "
                        "UNCERTIFIED)"
                    )
                if P_eq_Pa > 0.0 and (
                    retain_analytical_channel
                    or P_eq_Pa > 1e-15
                    or retain_high_t_openimcc_carriers
                ):
                    vapor_pressures[species] = P_eq_Pa
                    source_label = vapor_pressure_source_label(
                        "builtin_authoritative",
                        sp_data,
                        coefficient_block=coefficient_block,
                        temperature_K=T_K,
                    )
                    if species in metal_extrapolations:
                        source_label = (
                            f"{source_label}:"
                            "extrapolated_beyond_valid_range_K"
                        )
                    if (
                        gamma_auth is not None
                        and gamma_auth["authority_status"]
                        == "out_of_gamma_domain"
                    ):
                        source_label = (
                            f"{source_label}:out_of_gamma_domain"
                        )
                    vapor_pressure_sources[species] = source_label
                    vapor_pressure_provenance[species] = {
                        "pressure_kind": _runtime_pressure_kind(
                            sp_data,
                            coefficient_block,
                            effective_scaled=(
                                activity_factor != 1.0 or pO2_scaled
                            ),
                        ),
                        "pressure_rail": "liquid_oxide_standard_reaction",
                        "P_reference_Antoine_Pa": P_reference_Pa,
                        "P_eq_Pa": P_eq_Pa,
                        "pO2_bar": melt_dissociation_pO2_bar,
                        "activity_factor": activity_factor,
                        "oxide_activity_exponent": activity_exponent,
                        "pO2_exponent": pO2_exponent,
                        "source_label": source_label,
                    }
                    vapor_pressure_provenance[species].update(
                        oxide_activity.provenance()
                    )
                    if gamma_auth is not None:
                        vapor_pressure_provenance[species][
                            "gamma_domain_authority"
                        ] = gamma_auth
                continue

            # Al/Ti/Cr/Mn: liquid-oxide standard reaction for oxide-coupled P
            # at all T; pure-component Antoine is NBP-only (not used here).
            liquid_rxn = _liquid_oxide_standard_reaction_block(sp_data)
            if liquid_rxn is not None and fit_target != FIT_TARGET_STANDARD_REACTION:
                antoine_liq = liquid_rxn.get("antoine", {}) or {}
                A_l = float(antoine_liq.get("A", 0.0) or 0.0)
                B_l = float(antoine_liq.get("B", 0.0) or 0.0)
                C_l = float(antoine_liq.get("C", 0.0) or 0.0)
                if not (A_l > 0.0 and T_K > 300.0):
                    continue
                valid_liq = _range_tuple(liquid_rxn.get("valid_range_K"))
                if valid_liq is not None:
                    vlo, vhi = valid_liq
                    if T_K < vlo or T_K > vhi:
                        metal_extrapolations[species] = {
                            "temperature_K": T_K,
                            "valid_range_K": (vlo, vhi),
                            "rail": "liquid_oxide_standard_reaction",
                        }
                        warnings.append(
                            f"{species} liquid-oxide standard reaction "
                            f"extrapolated beyond valid_range_K "
                            f"[{vlo:g}, {vhi:g}] at {T_K:.3f} K"
                        )
                log_P_liq = _antoine_log10_pressure(A_l, B_l, C_l, T_K)
                P_reference_Pa = _pow10_pressure_or_raise(
                    log_P_liq,
                    species=species,
                    field="P_reference_liquid_oxide_standard_reaction_Pa",
                )
                oxide_activity = _high_t_activity_for_parent(
                    parent_oxide,
                    melt_account_mol,
                    temperature_K=T_K,
                    authority=high_t_activity_authority,
                )
                if oxide_activity is None or oxide_activity.activity <= 1e-10:
                    continue
                if oxide_activity.warning:
                    warnings.append(oxide_activity.warning)
                activities[species] = oxide_activity.activity
                activity_exponent = float(
                    liquid_rxn.get("oxide_activity_exponent", 1.0) or 1.0
                )
                pO2_exponent = float(
                    liquid_rxn.get("pO2_exponent", 0.0) or 0.0
                )
                pO2_reference_bar = max(
                    1e-30,
                    float(liquid_rxn.get("pO2_reference_bar", 1.0) or 1.0),
                )
                # t-571: O2 enters through channel #1 (owner-gated,
                # envelope-clamped, receipted) — bit-identical linear form.
                o2_term, o2_potential = _o2_channel_term_and_potential(
                    pO2_exponent=pO2_exponent,
                    pO2_bar=melt_dissociation_pO2_bar,
                    pO2_reference_bar=pO2_reference_bar,
                    temperature_K=T_K,
                    reaction_plane=REACTION_PLANE_MELT_INTERFACE,
                )
                P_eq_raw, activity_factor, pO2_scaled = (
                    _standard_reaction_pressure_Pa(
                        P_reference_Pa=P_reference_Pa,
                        oxide_activity_value=oxide_activity.activity,
                        activity_exponent=activity_exponent,
                        o2_term=o2_term,
                        o2_potential=o2_potential,
                    )
                )
                P_eq_Pa = _require_finite_vapor_value(
                    P_eq_raw,
                    species=species,
                    field="P_eq_liquid_oxide_standard_reaction",
                )
                if P_eq_Pa > 0.0 and (
                    P_eq_Pa > 1e-15 or retain_high_t_openimcc_carriers
                ):
                    vapor_pressures[species] = P_eq_Pa
                    source_label = (
                        "builtin_authoritative:"
                        "liquid_oxide_standard_reaction"
                    )
                    if species in metal_extrapolations:
                        source_label = (
                            f"{source_label}:"
                            "extrapolated_beyond_valid_range_K"
                        )
                    vapor_pressure_sources[species] = source_label
                    vapor_pressure_provenance[species] = {
                        "pressure_kind": _runtime_pressure_kind(
                            sp_data,
                            COEFF_BLOCK_ANTOINE,
                            effective_scaled=(
                                activity_factor != 1.0 or pO2_scaled
                            ),
                        ),
                        "pressure_rail": "liquid_oxide_standard_reaction",
                        "oxide_standard_state": "liquid",
                        "P_reference_Antoine_Pa": P_reference_Pa,
                        "P_eq_Pa": P_eq_Pa,
                        "pO2_bar": melt_dissociation_pO2_bar,
                        "activity_factor": activity_factor,
                        "oxide_activity_exponent": activity_exponent,
                        "pO2_exponent": pO2_exponent,
                        "source_label": source_label,
                    }
                    vapor_pressure_provenance[species].update(
                        oxide_activity.provenance()
                    )
                continue

            # Ca/Mg gas rail: liquid-oxide standard reaction replaces solid
            # Ellingham gas fugacity while condensed pure-component rail stays.
            gas_rail_rxn = gas_rail_rxn_early
            if gas_standard_rail and gas_rail_rxn is not None:
                antoine_gas = gas_rail_rxn.get("antoine", {}) or {}
                A_g = float(antoine_gas.get("A", 0.0) or 0.0)
                B_g = float(antoine_gas.get("B", 0.0) or 0.0)
                C_g = float(antoine_gas.get("C", 0.0) or 0.0)
                if not (A_g > 0.0 and T_K > 300.0):
                    continue
                if _is_mapping(
                    sp_data.get(RECONSTRUCTED_VAPOR_PRESSURE_SEGMENT_KEY)
                ):
                    reconstructed_vapor_limit = (
                        require_antoine_source_certified_temperature(
                            species,
                            sp_data,
                            GAS_RAIL_STANDARD_REACTION_KEY,
                            T_K,
                            consumer="builtin_gas_rail",
                        )
                    )
                    if reconstructed_vapor_limit is not None:
                        vapor_pressure_authority_limits[species] = (
                            reconstructed_vapor_limit
                        )
                if reconstructed_vapor_limit is None:
                    valid_gas = _range_tuple(gas_rail_rxn.get("valid_range_K"))
                    if valid_gas is not None:
                        vlo, vhi = valid_gas
                        if T_K < vlo or T_K > vhi:
                            metal_extrapolations[species] = {
                                "temperature_K": T_K,
                                "valid_range_K": (vlo, vhi),
                                "rail": "gas_rail_standard_reaction",
                            }
                            warnings.append(
                                f"{species} gas-rail liquid-oxide standard reaction "
                                f"extrapolated beyond valid_range_K "
                                f"[{vlo:g}, {vhi:g}] at {T_K:.3f} K"
                            )
                    log_P_gas = _antoine_log10_pressure(A_g, B_g, C_g, T_K)
                    P_reference_Pa = _pow10_pressure_or_raise(
                        log_P_gas,
                        species=species,
                        field="P_reference_gas_rail_standard_reaction_Pa",
                    )
                else:
                    P_reference_Pa = float(
                        reconstructed_vapor_limit["pressure_Pa"]
                    )
                oxide_activity = _high_t_activity_for_parent(
                    parent_oxide,
                    melt_account_mol,
                    temperature_K=T_K,
                    authority=high_t_activity_authority,
                )
                if oxide_activity is None or oxide_activity.activity <= 1e-10:
                    continue
                if oxide_activity.warning:
                    warnings.append(oxide_activity.warning)
                activities[species] = oxide_activity.activity
                activity_exponent = float(
                    gas_rail_rxn.get("oxide_activity_exponent", 1.0) or 1.0
                )
                pO2_exponent = float(
                    gas_rail_rxn.get("pO2_exponent", 0.0) or 0.0
                )
                pO2_reference_bar = max(
                    1e-30,
                    float(gas_rail_rxn.get("pO2_reference_bar", 1.0) or 1.0),
                )
                # t-571: O2 enters through channel #1 (owner-gated,
                # envelope-clamped, receipted) — bit-identical linear form.
                o2_term, o2_potential = _o2_channel_term_and_potential(
                    pO2_exponent=pO2_exponent,
                    pO2_bar=melt_dissociation_pO2_bar,
                    pO2_reference_bar=pO2_reference_bar,
                    temperature_K=T_K,
                    reaction_plane=REACTION_PLANE_MELT_INTERFACE,
                )
                # Premise: gas-rail P_ref is liquid-oxide standard reaction at
                # a=1, fO2=1 bar. Algebra: P = P_ref * a * fO2^n with the
                # melt dissociation pO2 channel (not transport-only). Unit Pa.
                # Sanity: residual collapses to regenerated TE+JANAF grid.
                P_eq_raw, activity_factor, pO2_scaled = (
                    _standard_reaction_pressure_Pa(
                        P_reference_Pa=P_reference_Pa,
                        oxide_activity_value=oxide_activity.activity,
                        activity_exponent=activity_exponent,
                        o2_term=o2_term,
                        o2_potential=o2_potential,
                    )
                )
                P_eq_Pa = _require_finite_vapor_value(
                    P_eq_raw,
                    species=species,
                    field="P_eq_gas_rail_standard_reaction",
                )
                if P_eq_Pa > 0.0 and (
                    P_eq_Pa > 1e-15 or retain_high_t_openimcc_carriers
                ):
                    vapor_pressures[species] = P_eq_Pa
                    source_label = (
                        "builtin_authority_limited:"
                        "gas_rail_liquid_oxide_standard_reaction:"
                        "reconstructed_vapor_pressure_segment"
                        if reconstructed_vapor_limit is not None
                        else (
                            "builtin_authoritative:"
                            "gas_rail_liquid_oxide_standard_reaction"
                        )
                    )
                    if species in metal_extrapolations:
                        source_label = (
                            f"{source_label}:"
                            "extrapolated_beyond_valid_range_K"
                        )
                    vapor_pressure_sources[species] = source_label
                    vapor_pressure_provenance[species] = {
                        "pressure_kind": _runtime_pressure_kind(
                            sp_data,
                            COEFF_BLOCK_ANTOINE,
                            effective_scaled=(
                                activity_factor != 1.0 or pO2_scaled
                            ),
                        ),
                        "pressure_rail": "gas_rail_liquid_oxide_standard_reaction",
                        "metal_standard_state": ELLINGHAM_METAL_PHASE_GAS,
                        "oxide_standard_state": "liquid",
                        "P_standard_Pa": ELLINGHAM_STANDARD_PRESSURE_PA,
                        "P_reference_Antoine_Pa": P_reference_Pa,
                        "P_eq_Pa": P_eq_Pa,
                        "pO2_bar": melt_dissociation_pO2_bar,
                        "activity_factor": activity_factor,
                        "oxide_activity_exponent": activity_exponent,
                        "pO2_exponent": pO2_exponent,
                        "source_label": source_label,
                    }
                    if reconstructed_vapor_limit is not None:
                        vapor_pressure_provenance[species].update(
                            {
                                "vapor_pressure_authority_status": (
                                    VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_STATUS
                                ),
                                VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG: True,
                            }
                        )
                    vapor_pressure_provenance[species].update(
                        oxide_activity.provenance()
                    )
                continue

            fe_degraded_activity_basis = None
            fe_activity_basis = None
            if parent_oxide == 'FeO':
                candidate_activity = _high_t_activity_for_parent(
                    parent_oxide,
                    melt_account_mol,
                    temperature_K=T_K,
                    authority=high_t_activity_authority,
                )
                if (
                    high_t_activity_authority
                    and (
                        high_t_activity_authority.get("provider") == "openimcc"
                        or "FeO" in high_t_activity_authority.get("held_effective_gamma_by_parent", {})
                    )
                    and candidate_activity is not None
                ):
                    oxide_activity = candidate_activity
                    a_oxide = oxide_activity.activity
                    fe_activity_basis = (
                        candidate_activity.activity_model
                        if candidate_activity.activity_model in {
                            "hold_last_certified_effective_gamma",
                            "extrapolated_first_order_ln_gamma",
                            "extrapolation_capped_frozen",
                            "same_composition_T_bridge",
                        }
                        else "openimcc_single_cation"
                    )
                elif intrinsic_fO2_log is not None:
                    oxide_activity = None
                    a_oxide = below_cap_fe_activity
                    fe_activity_basis = "kress91_ferrous"
                else:
                    oxide_activity = None
                    # Documented degraded pre-existing public-caller path:
                    # without an explicit intrinsic melt fO2 channel, Fe uses
                    # FeO wt%/100 as a stand-in activity. Typed — never silent
                    # as if it were Kress91 or a VapoRock state activity.
                    # Matrix lock: do NOT splice VapoRock IW FeO activities.
                    a_oxide = comp_wt.get(parent_oxide, 0.0) / 100.0
                    fe_degraded_activity_basis = "feo_weight_fraction"
                    fe_activity_basis = fe_degraded_activity_basis
                    warnings.append(
                        "Fe degraded_activity_basis=feo_weight_fraction: "
                        "intrinsic_fO2_log absent; using FeO wt%/100 "
                        "(documented degraded public-caller path, uncertified)"
                    )
            else:
                oxide_activity = _high_t_activity_for_parent(
                    parent_oxide,
                    melt_account_mol,
                    temperature_K=T_K,
                    authority=high_t_activity_authority,
                )
                if oxide_activity is None:
                    continue
                # provenance: gamma_alkali_melt_activity
                # Sossi & Fegley 2018 Eq.25 is linear in single-cation
                # a_MOx = gamma_MOx * X_MOx. The JANAF Ellingham row here is
                # still written on the parent oxide (Na2O, Al2O3, Cr2O3), so
                # feed an equivalent parent activity whose legacy exponent
                # produces the single-cation activity.
                a_oxide = oxide_activity.equivalent_parent_activity(n_ox / n_M)
                if oxide_activity.warning:
                    warnings.append(oxide_activity.warning)
            if (
                oxide_activity is None
                and a_oxide <= 1e-10
            ) or (
                oxide_activity is not None
                and oxide_activity.activity <= 1e-10
            ):
                continue

            ellingham_extrapolation = _ellingham_authority_limit(
                T_K,
                species=species,
            )
            if ellingham_extrapolation is not None:
                ellingham_extrapolations[species] = ellingham_extrapolation
                if ellingham_extrapolation["authority_status"] == "extrapolation_limited":
                    valid_low, valid_high = ellingham_fit_range_K(species)
                    warnings.append(
                        f"{species} Ellingham JANAF high-T fit extrapolated beyond "
                        f"fit_range_K [{valid_low:g}, {valid_high:g}] at "
                        f"{T_K:.2f} K"
                    )

            activities[species] = (
                a_oxide if oxide_activity is None else oxide_activity.activity
            )

            # Ellingham: dG_f(T) = dH_f - T * dS_f (kJ/mol O2)
            dG_f_kJ = ellingham_delta_g_kj_per_mol_o2(species, T_K)
            # K_decomp = exp(dG_f * 1000 / (R * T))
            try:
                K_decomp = math.exp(dG_f_kJ * 1000.0 / (GAS_CONSTANT * T_K))
            except OverflowError as exc:
                raise VaporPressureComputationError(
                    "vapor_pressure_nonfinite: "
                    f"species={species} field=K_decomp value=overflow"
                ) from exc
            K_decomp = _require_finite_vapor_value(
                K_decomp,
                species=species,
                field="K_decomp",
            )
            dissociation_pO2_bar = (
                transport_pO2_bar
                if parent_oxide == 'FeO'
                else melt_dissociation_pO2_bar
            )
            if parent_oxide != 'FeO':
                # t-571: the Ellingham O2 denominator is sourced from the
                # owner-gated O2 channel potential (melt_interface plane).
                # In-envelope the factory's envelope clamp is the identity,
                # so legacy_pO2_bar equals dissociation_pO2_bar bit-for-bit;
                # out-of-envelope degraded transport fallbacks (>100 bar
                # without intrinsic fO2) now receive the declared b-148
                # envelope instead of mass-actioning a float sentinel.
                # FeO intentionally retains the legacy transport denominator
                # (Kress91 activity already carries melt redox).
                dissociation_pO2_bar = o2_potential_from_pO2_bar(
                    pO2_bar=dissociation_pO2_bar,
                    temperature_K=T_K,
                    reaction_plane=REACTION_PLANE_MELT_INTERFACE,
                    pO2_reference_bar=1.0,
                ).legacy_pO2_bar
            # Premise: this value is the melt-supported metal source pressure,
            # not the later surface-flux boundary condition. For MgO(l) ->
            # Mg(g) + 1/2 O2, K1=(f_Mg/p0)*(fO2/p0)^1/2/a_MgO. The JANAF row
            # is normalized per mol O2, so K2=K1**2 and
            # K2=(f_Mg/p0)**2*(fO2/p0)/a_MgO**2. Therefore the generic solve is
            # root=(K2*a_oxide**n_ox/fO2_bar)**(1/n_M), n_ox=n_M=2.
            # Unit check: K, activities, and fugacity/standard-pressure ratios
            # are dimensionless; the selected rail supplies Pa only afterward.
            # Sanity/limit: lowering melt fO2 raises Mg as fO2**-1/2, while
            # fO2 -> infinity suppresses release. The separately dispatched
            # evaporation flux still subtracts overhead metal backpressure,
            # preserving the surface metal-transport path. Pure-MgO congruent
            # co-evolution is a separate reaction-basis validation, not a
            # runtime boundary condition solved by that subtraction.
            # FeO already carries melt redox through its Kress91 activity and
            # intentionally retains the legacy transport denominator here.
            numerator = _require_finite_vapor_value(
                K_decomp * (a_oxide ** n_ox) / dissociation_pO2_bar,
                species=species,
                field="metal_activity_numerator",
            )
            if numerator <= 0:
                continue

            metal_activity_root = numerator ** (1.0 / n_M)
            metal_activity_root = _require_finite_vapor_value(
                metal_activity_root,
                species=species,
                field="metal_activity",
            )
            if metal_phase_kind == ELLINGHAM_METAL_PHASE_GAS:
                # Gas-standard rows solve for f_M/p0, so P_M = root * p0.
                # Pairing this root with condensed Antoine P_sat would count
                # vaporization twice: P_wrong/P_right = P_sat/p0.
                activity_factor = metal_activity_root
                pressure_reference_Pa = ELLINGHAM_STANDARD_PRESSURE_PA
                pressure_rail = "gas_fugacity"
            else:
                # Condensed-standard rows solve a Raoultian metal activity.
                # Only this rail is capped at pure condensed metal and paired
                # with Antoine P_sat: P_M = min(root, 1) * P_sat.
                activity_factor = min(metal_activity_root, 1.0)
                assert P_reference_Pa is not None
                pressure_reference_Pa = P_reference_Pa
                pressure_rail = "condensed_raoult_psat"
            P_eq_Pa = _require_finite_vapor_value(
                activity_factor * pressure_reference_Pa,
                species=species,
                field="P_eq_Pa",
            )
            if P_eq_Pa > 0.0 and (
                P_eq_Pa > 1e-15 or retain_high_t_openimcc_carriers
            ):
                vapor_pressures[species] = P_eq_Pa
                ellingham_limit = ellingham_extrapolations.get(species)
                fit_extrapolated = (
                    ellingham_limit is not None
                    and ellingham_limit["authority_status"] == "extrapolation_limited"
                )
                reconstructed_limited = (
                    ellingham_limit is not None
                    and ellingham_limit["authority_status"] == "reconstructed_limited"
                )
                if gas_standard_rail:
                    base_source = (
                        "builtin_extrapolation_limited"
                        if fit_extrapolated
                        else (
                            "builtin_authority_limited"
                            if reconstructed_limited
                            else "builtin_authoritative"
                        )
                    )
                    source_label = f"{base_source}:gas_standard_fugacity"
                else:
                    source_label = vapor_pressure_source_label(
                        "builtin_authoritative",
                        sp_data,
                        coefficient_block=coefficient_block,
                        temperature_K=T_K,
                        authority_limited_by_ellingham_fit_range=(
                            fit_extrapolated
                        ),
                    )
                    if reconstructed_limited:
                        source_label = source_label.replace(
                            "builtin_authoritative",
                            "builtin_authority_limited",
                            1,
                        )
                if species in metal_extrapolations:
                    source_label = (
                        f"{source_label}:"
                        "extrapolated_beyond_valid_range_K"
                    )
                if fit_extrapolated:
                    source_label = (
                        f"{source_label}:"
                        "extrapolated_beyond_ellingham_fit_range_K"
                    )
                elif reconstructed_limited:
                    source_label = f"{source_label}:reconstructed_ellingham_segment"
                if reconstructed_vapor_limit is not None:
                    source_label = source_label.replace(
                        "builtin_authoritative",
                        "builtin_authority_limited",
                        1,
                    )
                    source_label = (
                        f"{source_label}:reconstructed_vapor_pressure_segment"
                    )
                vapor_pressure_sources[species] = source_label
                provenance: dict[str, Any] = {
                    "pressure_kind": _runtime_pressure_kind(
                        sp_data,
                        coefficient_block,
                        effective_scaled=(activity_factor != 1.0),
                    ),
                    "pressure_rail": pressure_rail,
                    "metal_standard_state": metal_phase_kind,
                    "P_standard_Pa": ELLINGHAM_STANDARD_PRESSURE_PA,
                    "P_eq_Pa": P_eq_Pa,
                    "pO2_bar": dissociation_pO2_bar,
                    "activity_factor": activity_factor,
                    "raw_metal_activity_root": metal_activity_root,
                    "source_label": source_label,
                }
                if P_reference_Pa is not None:
                    provenance["P_reference_Antoine_Pa"] = P_reference_Pa
                if reconstructed_vapor_limit is not None:
                    provenance.update(
                        {
                            "vapor_pressure_authority_status": (
                                VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_STATUS
                            ),
                            VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG: True,
                        }
                    )
                if parent_oxide == "FeO":
                    if fe_degraded_activity_basis is not None:
                        provenance["activity_basis"] = fe_degraded_activity_basis
                        provenance["degraded_activity_basis"] = (
                            fe_degraded_activity_basis
                        )
                    else:
                        provenance["activity_basis"] = fe_activity_basis
                        provenance["degraded_activity_basis"] = None
                vapor_pressure_provenance[species] = provenance
                if oxide_activity is not None:
                    vapor_pressure_provenance[species].update(
                        oxide_activity.provenance()
                    )
                    vapor_pressure_provenance[species][
                        "equivalent_parent_oxide_activity"
                    ] = a_oxide
                if coefficient_block == COEFF_BLOCK_ANTOINE:
                    warn_pseudo_vapor_pressure_fallback(
                        species,
                        sp_data,
                        self._pseudo_vapor_pressure_warning_seen,
                        stacklevel=2,
                    )

        oxide_vapors_data = self._vapor_pressure_data.get('oxide_vapors', {}) or {}
        from simulator.vapour_rail.catalog import CatalogCompileError

        for name, data in oxide_vapors_data.items():
            if self._vapour_rail_catalog is not None:
                try:
                    self._vapour_rail_catalog.assert_hot_train_applicable(
                        name,
                        process_phase=controls.get("process_phase"),
                    )
                except CatalogCompileError:
                    continue
            if bool((data or {}).get("interval_required")):
                reject_noncertifying_vapor_pressure_row(
                    name,
                    data,
                    COEFF_BLOCK_ANTOINE,
                )
            antoine = (data or {}).get('antoine', {}) or {}
            A = antoine.get('A', 0)
            B = antoine.get('B', 0)
            C = antoine.get('C', 0)
            valid = data.get('valid_range_K', [0, 9999])
            compiled_evaluator = None
            compiled_species = None
            if not A > 0 and self._vapour_rail_catalog is not None:
                try:
                    compiled_species = self._vapour_rail_catalog.species[name]
                    # b-189-exempt: applicability gated at loop top
                    compiled_evaluator = self._vapour_rail_catalog.evaluator_for(
                        name
                    )
                except (KeyError, ValueError):
                    pass
            if not A > 0 and compiled_evaluator is None:
                continue
            coefficient_block = (
                "compiled_reference_pressure_model"
                if compiled_evaluator is not None
                else COEFF_BLOCK_ANTOINE
            )

            parent_oxide = data.get('parent_oxide', '')
            activity_factor = 1.0
            oxide_activity = None
            if parent_oxide:
                retain_analytical_channel = compiled_evaluator is not None or bool(
                    data.get("retain_analytical_pressure_channel", False)
                )
                activity_exponent = float(
                    data.get('oxide_activity_exponent', 1.0)
                )
                oxide_activity = _high_t_activity_for_parent(
                    parent_oxide,
                    melt_account_mol,
                    temperature_K=T_K,
                    authority=high_t_activity_authority,
                )
                if oxide_activity is None:
                    continue
                if data.get("source_activity_basis") == "parent_oxide":
                    a_ox = oxide_activity.thermodynamic_parent_activity()
                    reported_activity = a_ox
                else:
                    # Legacy single-cation reaction fits declare their own
                    # exponent-adjusted compatibility basis.
                    a_ox = oxide_activity.equivalent_parent_activity(
                        activity_exponent
                    )
                    reported_activity = oxide_activity.activity
                if oxide_activity.activity <= 0.0 or (
                    not retain_analytical_channel
                    and oxide_activity.activity <= 1e-10
                ):
                    continue
                if oxide_activity.warning:
                    warnings.append(oxide_activity.warning)
                activities[name] = reported_activity
                if compiled_evaluator is not None:
                    # The compiled source reaction owns its declared activity
                    # power.  Premise: for q A_cond -> nu V + ..., mass action
                    # gives p_V proportional to a_A^(q/nu).  The melt provider
                    # reports the declared source basis, so pass that activity
                    # itself and let the evaluator apply q/nu once.
                    # Unit check: activity and its power are dimensionless.
                    # Sanity: q/nu=1 matches legacy rows; Al2O's q/nu=2 must
                    # quarter pressure when activity halves.  Calling
                    # equivalent_parent_activity(2) here would take sqrt(a)
                    # and silently flatten that physical square to a.
                    activity_factor = max(reported_activity, 0.0) ** float(
                        compiled_evaluator.activity_exponent
                    )
                else:
                    activity_factor = max(a_ox, 0.0) ** activity_exponent

            valid_range = _range_tuple(valid)
            if compiled_evaluator is None and valid_range is not None:
                valid_low, valid_high = valid_range
                if T_K < valid_low:
                    continue
                if T_K > valid_high:
                    if name == "SiO":
                        # t-838 pilot: the domain miss demotes evidence, not
                        # the numeric source reaction. Other rails retain their
                        # existing admission policy until separately reviewed.
                        oxide_vapor_extrapolations[name] = {
                            "temperature_K": T_K,
                            "valid_range_K": (valid_low, valid_high),
                            "authority_level": "extrapolated",
                            "reason": "oxide_vapor_pressure_out_of_validated_range",
                        }
                    else:
                        extrapolation_allowed_range = _range_tuple(
                            data.get("extrapolation_allowed_range_K")
                        )
                        if extrapolation_allowed_range is None:
                            raise VaporPressureComputationError(
                                "oxide_vapor_pressure_out_of_validated_range: "
                                f"species={name} temperature_K={T_K:.2f} "
                                f"valid_range_K=[{valid_low:g}, {valid_high:g}] "
                                "extrapolation_allowed_range_K=absent"
                            )
                        allowed_low, allowed_high = extrapolation_allowed_range
                        if T_K < allowed_low or T_K > allowed_high:
                            raise VaporPressureComputationError(
                                "oxide_vapor_pressure_out_of_validated_range: "
                                f"species={name} temperature_K={T_K:.2f} "
                                f"valid_range_K=[{valid_low:g}, {valid_high:g}] "
                                "extrapolation_allowed_range_K="
                                f"[{allowed_low:g}, {allowed_high:g}]"
                            )
                        oxide_vapor_extrapolations[name] = {
                            "temperature_K": T_K,
                            "valid_range_K": (valid_low, valid_high),
                            "extrapolation_allowed_range_K": (
                                allowed_low,
                                allowed_high,
                            ),
                        }
                    warnings.append(
                        f"{name} oxide-vapor Antoine fit extrapolated beyond "
                        f"valid_range_K [{valid_low:g}, {valid_high:g}] at "
                        f"{T_K:.2f} K"
                    )
            if compiled_evaluator is not None:
                assert compiled_species is not None
                try:
                    domain_transition = declared_domain_transition(
                        compiled_species, T_K
                    )
                except DomainPolicyError as exc:
                    raise VaporPressureComputationError(
                        f"{name}: {exc}"
                    ) from exc
                if domain_transition.refuses:
                    oxide_vapor_extrapolations[name] = {
                        "temperature_K": T_K,
                        "valid_range_K": compiled_evaluator.valid_temperature_K,
                        "status": domain_transition.disposition,
                        "refusal_code": domain_transition.refusal_code,
                        "detail": domain_transition.detail,
                    }
                    warnings.append(
                        f"{name}: {domain_transition.refusal_code}: "
                        f"{domain_transition.detail}"
                    )
                    continue
                # Explicit unit/reference inputs here are the declared standard
                # reaction reference, not evaluator defaults. The live point
                # below uses the physical activity and the evaluator's named
                # oxygen channel.
                reference_evaluation = compiled_evaluator.evaluate(
                    T_K,
                    source_activity=1.0,
                    pO2_bar=compiled_evaluator.pO2_reference_bar,
                )
                evaluator_pO2_bar = transport_pO2_bar
                if compiled_evaluator.oxygen_fugacity_channel == "intrinsic_melt":
                    evaluator_pO2_bar = melt_dissociation_pO2_bar
                evaluation = compiled_evaluator.evaluate(
                    T_K,
                    source_activity=(
                        max(reported_activity, 1.0e-300)
                        if parent_oxide
                        else 1.0
                    ),
                    pO2_bar=evaluator_pO2_bar,
                )
                P_reference_Pa = reference_evaluation.pressure_pa
                P_eq_Pa = evaluation.pressure_pa
                pO2_exponent = compiled_evaluator.pO2_exponent
                pO2_scaled = bool(pO2_exponent)
                if evaluation.out_of_range:
                    oxide_vapor_extrapolations[name] = {
                        "temperature_K": T_K,
                        "valid_range_K": compiled_evaluator.valid_temperature_K,
                        "status": evaluation.status,
                        "acquisition_flag": evaluation.acquisition_flag,
                    }
                    warnings.append(str(evaluation.status))
                    # Premise: the compiled evaluator's consequence-aware
                    # continuation is its best available point estimate; refusing
                    # to publish it would assert zero carrier outside the source
                    # window and create a discontinuity at every grid floor.
                    # Algebra: P_eq = 10**log10(P_cont),
                    # with activity and fO2 powers already applied exactly once
                    # by evaluate(). Units: P_eq remains Pa. Sanity: only the two
                    # fields above change at the boundary (typed warning +
                    # acquisition flag); the pressure remains continuous for every
                    # status-bearing compiled carrier.
            else:
                # Premise: above-band SiO retains its source reaction fit.
                # Algebra: log10(P_reference/Pa) = A - B/(T_K+C), unchanged;
                # B and T_K+C carry K, so the exponent is dimensionless.
                # At the certified edge the same expression is continuous.
                # This preserves the trend, not a certified error bound.
                log_P = _antoine_log10_pressure(A, B, C, T_K)
                P_reference_Pa = _pow10_pressure_or_raise(
                    log_P,
                    species=name,
                    field="P_reference_Antoine_Pa",
                )
                P_eq_Pa = P_reference_Pa
                pO2_scaled = False
                if parent_oxide:
                    P_eq_Pa = _require_finite_vapor_value(
                        P_eq_Pa * activity_factor,
                        species=name,
                        field="P_eq_activity",
                    )

                pO2_exponent = float(data.get('pO2_exponent', 0.0) or 0.0)
            if compiled_evaluator is None and pO2_exponent:
                pO2_reference_bar = max(
                    1e-30, float(data.get('pO2_reference_bar', 1.0) or 1.0)
                )
                # t-571: transport-plane O2 through channel #1.  The legacy
                # form here applied no envelope clamp; the channel factory's
                # clamp is the identity in-envelope (bit-identical) and now
                # bounds out-of-envelope transport fallbacks (b-148 physics).
                o2_term, o2_potential = _o2_channel_term_and_potential(
                    pO2_exponent=pO2_exponent,
                    pO2_bar=transport_pO2_bar,
                    pO2_reference_bar=pO2_reference_bar,
                    temperature_K=T_K,
                    reaction_plane=REACTION_PLANE_TRANSPORT_HEADSPACE,
                )
                P_eq_Pa = _require_finite_vapor_value(
                    P_eq_Pa
                    * channel_linear_mass_action_factor(o2_term, o2_potential),
                    species=name,
                    field="P_eq_pO2",
                )
                pO2_scaled = True

            # SiO mass-action by pO2: p(SiO) scales as 1/sqrt(pO2).
            # Premise: the fitted SiO Antoine row is the standard-reaction
            # term at declared pO2_reference_bar (1e-9 bar), not at the body
            # vacuum floor. Algebra for SiO2(l)->SiO(g)+0.5 O2(g):
            #   P = P_ref * a_SiO2 * sqrt(p_ref / pO2)
            # applied once on BOTH sides of p_ref (boost below ref, suppress
            # above). Unit check: bar/bar is dimensionless. Sanity: at
            # pO2=p_ref the factor is 1; at the legal lunar floor 1.3e-12 bar
            # the factor is sqrt(1e-9/1.3e-12)≈27.735 — a silent clip to
            # unity below p_ref was an under-extraction bug. Transport pO2
            # is already fail-loud gated at the body/request vacuum floor
            # (resolve_transport_pO2_bar); no additional silent pO2 floor
            # belongs here. Body floors must not retune the SiO fit itself.
            if name == 'SiO' and not pO2_exponent:
                sio_reference_bar = max(
                    1e-30,
                    float(
                        data.get('pO2_reference_bar', vacuum_floor_bar)
                        or vacuum_floor_bar
                    ),
                )
                # t-571: the sqrt mass action consumes the owner-gated O2
                # channel potential (transport_headspace plane).  The exact
                # legacy expression sqrt(p_ref / p) is preserved — only the
                # scalar source changes (typed, clamped, receipted).  The
                # envelope clamp is the identity for fail-loud floored
                # transport pO2 (>= 1e-9 bar); an out-of-envelope explicit
                # control (>100 bar) now receives the b-148 envelope.
                sio_o2_potential = o2_potential_from_pO2_bar(
                    pO2_bar=transport_pO2_bar,
                    temperature_K=T_K,
                    reaction_plane=REACTION_PLANE_TRANSPORT_HEADSPACE,
                    pO2_reference_bar=sio_reference_bar,
                )
                mass_action = math.sqrt(
                    sio_o2_potential.legacy_pO2_reference_bar
                    / sio_o2_potential.legacy_pO2_bar
                )
                P_eq_Pa = _require_finite_vapor_value(
                    P_eq_Pa * mass_action,
                    species=name,
                    field="P_eq_mass_action",
                )
                pO2_scaled = True

            retain_analytical_channel = compiled_evaluator is not None or bool(
                data.get("retain_analytical_pressure_channel", False)
            )
            if P_eq_Pa > 0.0 and (
                retain_analytical_channel
                or P_eq_Pa > 1e-15
                or retain_high_t_openimcc_carriers
            ):
                vapor_pressures[name] = P_eq_Pa
                # Above-band predictions remain diagnostic-limited, including
                # the SiO continuation without a second certified interval.
                oxide_extrapolated = name in oxide_vapor_extrapolations
                source_label = vapor_pressure_source_label(
                    (
                        "builtin_extrapolation_limited"
                        if oxide_extrapolated
                        else "builtin_authoritative"
                    ),
                    data,
                    coefficient_block=coefficient_block,
                    temperature_K=T_K,
                    authority_limited_by_ellingham_fit_range=(
                        name in ellingham_extrapolations
                    ),
                )
                if oxide_extrapolated:
                    source_label = (
                        f"{source_label}:"
                        "extrapolated_beyond_valid_range_K"
                    )
                vapor_pressure_sources[name] = source_label
                vapor_pressure_provenance[name] = {
                    "pressure_kind": _runtime_pressure_kind(
                        data,
                        coefficient_block,
                        effective_scaled=(
                            activity_factor != 1.0 or pO2_scaled
                        ),
                    ),
                    "P_reference_Antoine_Pa": P_reference_Pa,
                    "P_eq_Pa": P_eq_Pa,
                    "pO2_bar": (
                        evaluator_pO2_bar
                        if (
                            compiled_evaluator is not None
                            and parent_oxide == "P2O5"
                        )
                        else transport_pO2_bar
                    ),
                    "activity_factor": activity_factor,
                    "source_label": source_label,
                }
                if name == "SiO" and oxide_extrapolated:
                    vapor_pressure_provenance[name]["extrapolation_notice"] = dict(
                        oxide_vapor_extrapolations[name]
                    )
                if compiled_evaluator is not None:
                    vapor_pressure_provenance[name][
                        "P_reference_model_Pa"
                    ] = P_reference_Pa
                if compiled_evaluator is not None and parent_oxide == "P2O5":
                    vapor_pressure_provenance[name][
                        "oxygen_fugacity_channel"
                    ] = compiled_evaluator.oxygen_fugacity_channel
                if oxide_activity is not None:
                    vapor_pressure_provenance[name].update(
                        oxide_activity.provenance()
                    )
                    vapor_pressure_provenance[name][
                        "equivalent_parent_oxide_activity"
                    ] = a_ox
                warn_pseudo_vapor_pressure_fallback(
                    name,
                    data,
                    self._pseudo_vapor_pressure_warning_seen,
                    stacklevel=2,
                )

        # YAML authority/bracket labels (pressure-neutral; Bug B pattern).
        species_authority: dict[str, dict[str, Any]] = {}
        for species, provenance in vapor_pressure_provenance.items():
            row = metals_data.get(species) or oxide_vapors_data.get(species)
            authority_fields = _species_authority_fields(row)
            if not authority_fields:
                continue
            provenance.update(authority_fields)
            species_authority[species] = {
                key: authority_fields[key]
                for key in (
                    "authority_class",
                    "declared_compensation",
                    "pressure_bracket",
                    "coherent_pair",
                    "shadow_bracket",
                    "pseudo_antoine_status",
                )
                if key in authority_fields
            }

        _attach_high_t_activity_provenance(
            authority=high_t_activity_authority,
            metals_data=metals_data,
            oxide_vapors_data=oxide_vapors_data,
            vapor_pressure_sources=vapor_pressure_sources,
            vapor_pressure_provenance=vapor_pressure_provenance,
        )

        floor_inversion_notices = _attach_pO2_floor_inversion_notices(
            vapor_pressures=vapor_pressures,
            vapor_pressure_sources=vapor_pressure_sources,
            vapor_pressure_provenance=vapor_pressure_provenance,
            fO2_log=(
                float(intrinsic_fO2_log)
                if intrinsic_fO2_log is not None
                else None
            ),
            was_clamped=melt_dissociation_pO2_clamped,
        )
        if floor_inversion_notices:
            flagged = ",".join(sorted(floor_inversion_notices))
            warnings.append(
                f"{MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON}: "
                f"species={flagged} "
                f"pO2_bar={MELT_DISSOCIATION_PO2_MIN_BAR:g} "
                "authority_level=extrapolated "
                "certified_band_pO2_bar=["
                f"{MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR:g}, "
                f"{MELT_DISSOCIATION_PO2_MAX_BAR:g}]"
            )

        diagnostic = {
            "vapor_pressures_Pa": vapor_pressures,
            "vapor_pressures_source": vapor_pressure_sources,
            "vapor_pressure_numerator_provenance": vapor_pressure_provenance,
            "activities": activities,
            "activities_provider": "BuiltinVaporPressureProvider",
            "activities_standard_state": {
                "convention": "raoultian_pure_endmember",
                "phase": "liquid",
                "reference_pressure_bar": 1.0,
                "reference_temperature_K": None,
                "component_basis": "raoultian_pure_endmember",
            },
            # Exact intrinsic-melt oxygen channel used above for metal-source
            # dissociation. Catalog activity evaluation must consume this solve
            # input, never reconstruct it from a later EquilibriumResult or
            # substitute transport/headspace pO2.
            "source_reaction_fO2_bar": (
                melt_dissociation_pO2_bar
                if intrinsic_fO2_log_supplied
                else None
            ),
            # Exact pre-clamp redox input for the t-568 typed Fe shadow. The
            # existing bar-valued field above stays the live evaluator channel.
            "source_reaction_fO2_log10": (
                float(intrinsic_fO2_log)
                if intrinsic_fO2_log_supplied
                else None
            ),
            "source_reaction_activity_pressure_bar": float(vacuum_floor_bar),
            "source_reaction_redox_model_id": "REF-001-kress-carmichael-1991",
            "source_reaction_composition_wt_pct": dict(comp_wt),
            "melt_dissociation_pO2_clamped_to_physical_envelope": (
                melt_dissociation_pO2_clamped
            ),
            "pO2_floor_inversion_notices_by_species": floor_inversion_notices,
            "pO2_bar": transport_pO2_bar,
            "vacuum_floor_bar": vacuum_floor_bar,
            "extrapolated_beyond_valid_range_K": {
                **metal_extrapolations,
                **oxide_vapor_extrapolations,
            },
            "ellingham_extrapolated_beyond_fit_range_K": (
                {
                    species: data
                    for species, data in ellingham_extrapolations.items()
                    if data.get("authority_status") == "extrapolation_limited"
                }
            ),
            "ellingham_authority": ellingham_authority_diagnostic(
                ellingham_extrapolations,
                consumer="builtin-vapor-pressure",
            ),
            "vapor_pressure_authority": vapor_pressure_authority_diagnostic(
                vapor_pressure_authority_limits,
                consumer="builtin-vapor-pressure",
            ),
            "structural_activity_reference": structural_activity_reference,
            "melt_oxide_activity_model": {
                "basis": "single-cation mole fraction",
                "tier": MELT_OXIDE_ACTIVITY_TIER,
                "limitation": MELT_OXIDE_ACTIVITY_LIMITATION,
                "alphamelts_cross_check_status": ALPHAMELTS_CROSS_CHECK_STATUS,
            },
            "species_authority": species_authority,
        }
        if high_t_activity_authority is not None:
            diagnostic["high_t_melt_activity"] = dict(
                high_t_activity_authority
            )
        if feo_activity_diagnostic is not None:
            diagnostic["a_FeO_calphad"] = feo_activity_diagnostic

        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status="ok",
            transition=None,
            control_audit=control_audit,
            diagnostic=diagnostic,
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_transport_pO2_bar(self, request: IntentRequest) -> float:
        """Pick up the transport/overhead pO2 (bar) from the caller.

        The simulator computes the commanded overhead pO2 in
        :meth:`EquilibriumMixin._commanded_pO2_bar` and passes it through
        ``control_inputs['pO2_bar']`` to keep parity with the legacy
        ``_internal_analytical_equilibrium``. If that explicit transport channel is
        absent, preserve the old fallback to the standard ``fO2_log``
        channel; if neither is supplied, fall back to the
        numerical vacuum floor.
        """

        return resolve_transport_pO2_bar(request)

    def _resolve_intrinsic_melt_fO2_log(
        self,
        request: IntentRequest,
        *,
        default_transport_pO2_bar: float | None = None,
    ) -> float:
        """Resolve the intrinsic-melt redox fO2 channel independently.

        ``control_inputs['intrinsic_fO2_log']`` is the explicit redox
        channel used by melt diagnostics. ``control_inputs['pO2_bar']``
        remains the transport/overhead channel and must not override an
        explicit or request-level melt fO2 value.
        """

        controls = request.control_inputs or {}
        intrinsic_fO2_log = controls.get('intrinsic_fO2_log')
        if intrinsic_fO2_log is not None:
            return float(intrinsic_fO2_log)
        if request.fO2_log is not None:
            return float(request.fO2_log)
        transport_pO2_bar = (
            float(default_transport_pO2_bar)
            if default_transport_pO2_bar is not None
            else self._resolve_transport_pO2_bar(request)
        )
        return math.log10(max(transport_pO2_bar, 1e-30))
