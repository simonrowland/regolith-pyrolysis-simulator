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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Annotations only; the runtime import stays lazy because simulator
    # package init re-enters this module (see __init__ below).
    from simulator.vapour_rail.channels import (
        CompiledReactionTerm,
        GasChannelPotential,
    )

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
)
from simulator.physical_constants import (  # noqa: E402
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
    if not math.isfinite(fO2):
        return MELT_DISSOCIATION_PO2_MAX_BAR, True
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


def _gas_rail_block_is_authoritative(block: Mapping[str, Any]) -> bool:
    """Return False for demoted / dormant gas_rail blocks (provenance only).

    Premise: a present ``gas_rail_standard_reaction`` mapping historically
    always selected the liquid-oxide Pref over Ellingham gas_fugacity.
    Algebra: demotion is an explicit status/authority gate, not key deletion,
    so the TE+JANAF coefficients remain inspectable while Builtin falls through
    to the gas-metal Ellingham root. Unitless status string / bool. Sanity:
    ``status: dormant_non_authoritative`` (MC-5 Ca, 2026-08-07) must not
    produce ``pressure_rail=gas_rail_liquid_oxide_standard_reaction``.
    """

    if block.get("authoritative") is False:
        return False
    status = str(
        block.get("status")
        or block.get("runtime_disposition")
        or block.get("authority_status")
        or ""
    ).strip().lower()
    if not status:
        return True
    dormant_markers = (
        "dormant",
        "dormant_non_authoritative",
        "inactive",
        "inactive_dormant",
        "inactive_provenance_only",
        "status_bearing_non_authoritative",
        "non_authoritative",
        "provenance_only",
    )
    return status not in dormant_markers and not status.startswith("dormant")


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
            log10_pressure_Pa = float(antoine["A"]) - float(antoine["B"]) / (
                temperature_K + float(antoine.get("C", 0.0) or 0.0)
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
        projected_log_pressure = float(selected.get("A", 0.0)) - (
            float(selected.get("B", 0.0)) / denominator
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
        melt_account_mol = dict(
            request.account_view.accounts.get(self.DECLARED_ACCOUNT, {}) or {}
        )
        feo_activity_diagnostic = None
        if intrinsic_fO2_log is not None:
            from simulator.fe_redox import (
                calphad_ferrous_feo_activity_diagnostic,
                kress91_furnace_activity_pressure_bar,
            )

            feo_activity_pressure_bar = kress91_furnace_activity_pressure_bar(
                floor_bar=vacuum_floor_bar,
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
        warnings: list[str] = []
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
                        log_P = A - B / (T_K + C)
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
                oxide_activity = melt_oxide_activity(
                    parent_oxide, melt_account_mol, temperature_K=T_K
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
                    retain_analytical_channel or P_eq_Pa > 1e-15
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
                log_P_liq = A_l - B_l / (T_K + C_l)
                P_reference_Pa = _pow10_pressure_or_raise(
                    log_P_liq,
                    species=species,
                    field="P_reference_liquid_oxide_standard_reaction_Pa",
                )
                oxide_activity = melt_oxide_activity(
                    parent_oxide, melt_account_mol, temperature_K=T_K
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
                if P_eq_Pa > 1e-15:
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
                    log_P_gas = A_g - B_g / (T_K + C_g)
                    P_reference_Pa = _pow10_pressure_or_raise(
                        log_P_gas,
                        species=species,
                        field="P_reference_gas_rail_standard_reaction_Pa",
                    )
                else:
                    P_reference_Pa = float(
                        reconstructed_vapor_limit["pressure_Pa"]
                    )
                oxide_activity = melt_oxide_activity(
                    parent_oxide, melt_account_mol, temperature_K=T_K
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
                if P_eq_Pa > 1e-15:
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
            if parent_oxide == 'FeO':
                oxide_activity = None
                if intrinsic_fO2_log is not None:
                    from simulator.fe_redox import kress91_ferrous_feo_activity

                    a_oxide = kress91_ferrous_feo_activity(
                        comp_wt=comp_wt,
                        fO2_log=intrinsic_fO2_log,
                        T_K=T_K,
                        pressure_bar=feo_activity_pressure_bar,
                        floor_bar=vacuum_floor_bar,
                    )
                else:
                    # Documented degraded pre-existing public-caller path:
                    # without an explicit intrinsic melt fO2 channel, Fe uses
                    # FeO wt%/100 as a stand-in activity. Typed — never silent
                    # as if it were Kress91 or a VapoRock state activity.
                    # Matrix lock: do NOT splice VapoRock IW FeO activities.
                    a_oxide = comp_wt.get(parent_oxide, 0.0) / 100.0
                    fe_degraded_activity_basis = "feo_weight_fraction"
                    warnings.append(
                        "Fe degraded_activity_basis=feo_weight_fraction: "
                        "intrinsic_fO2_log absent; using FeO wt%/100 "
                        "(documented degraded public-caller path, uncertified)"
                    )
            else:
                oxide_activity = melt_oxide_activity(
                    parent_oxide, melt_account_mol, temperature_K=T_K
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
            if P_eq_Pa > 1e-15:
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
                        provenance["activity_basis"] = "kress91_ferrous"
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
                    # b-189-exempt: applicability gated at loop top
                    compiled_species = self._vapour_rail_catalog.species[name]
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
                oxide_activity = melt_oxide_activity(
                    parent_oxide, melt_account_mol, temperature_K=T_K
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
                log_P = A - B / (T_K + C)
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
                retain_analytical_channel or P_eq_Pa > 1e-15
            ):
                vapor_pressures[name] = P_eq_Pa
                # Oxide rows outside valid_range_K but inside an optional
                # extrapolation_allowed_range_K remain diagnostic-limited
                # (head demotion + suffix). SiO's source-validated domain now
                # equals the process envelope [1400, 2273.15] K, so that band
                # is no longer extrapolation; T above valid_range with no
                # allowed band already raised above.
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
