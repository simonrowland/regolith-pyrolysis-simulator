"""Builtin VAPOR_PRESSURE provider (Antoine + Ellingham).

Kernel-registered provider that originally owned the ``VAPOR_PRESSURE``
intent (goal #7 ``BUILTIN-ENGINE-EXTRACTION``) and was demoted to the
**fallback** slot under goal #10 ``VAPOROCK-AUTHORITY-PROMOTION``.
:class:`engines.vaporock.provider.VapoRockProvider` is now the
authoritative provider; the kernel consults this builtin only when
VapoRock is unavailable AND the simulator was constructed with
``allow_fallback_vapor=True`` (the flag is read at
:meth:`PyrolysisSimulator.__init__` time and threaded into
:class:`ChemistryKernel.allow_fallback_intents`).

The provider:

- reads ``process.cleaned_melt`` from the account view (the only
  account it declares),
- looks up Antoine coefficients from the ``vapor_pressures.yaml``
  payload passed at construction time,
- combines Ellingham oxide-decomposition equilibrium with Antoine
  reference terms to compute per-species saturation pressures at the
  request's ``temperature_C`` and the caller-supplied commanded
  ``pO2_bar`` (via ``control_inputs``). Only
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
from typing import Any

from engines.builtin._common import (
    composition_wt_pct_from_account_view,
    diagnostic_control_audit,
    reject_wrong_intent,
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
# - Oxide vapors with `fit_target: standard_reaction_term` use raw Antoine as
#   a ΔG-equivalent term, consumed with explicit oxide-activity + pO2
#   exponents -- single-counted via explicit reaction stoichiometry.
# Dispatch keeps the existing math, but uses `fit_target` for honest source
# labels and runtime warnings when pseudo VapoRock curve-fit fallback rows are
# actually used.
#
# Mirrors EquilibriumMixin._ELLINGHAM_THERMO -- the canonical table.
# Tuple: (dH_f kJ/mol_O2, dS_f kJ/(mol*K), n_M, n_ox)
ELLINGHAM_FIT_RANGE_K = (1100.0, 1700.0)
_ELLINGHAM_THERMO: dict[str, tuple[float, float, float, float]] = {
    # V1c JANAF high-T refit over 1100-1700 K for Na/K/Fe/Cr/Mg/Ca/Al/Ti/Si.
    # Mn updated 0.5.2 (2026-05-27) to a proper high-T linear refit
    # anchored on Mn(l) above the s→l transition at 1517 K (Mn-008
    # NIST-JANAF + phase transition data); see the rationale in
    # simulator/equilibrium.py::_ELLINGHAM_THERMO.
    'Na': (-1135.130, -0.537417, 4, 2),
    'K':  (-975.838, -0.520580, 4, 2),
    'Fe': (-538.946, -0.125272, 2, 2),
    'Mn': (-794.540, -0.165650, 2, 2),  # Mn-008 high-T (Mn(l) basis, 1517-1700 K)
    'Cr': (-748.076, -0.168676, 4/3, 2/3),
    'Mg': (-1342.444, -0.336009, 2, 2),
    'Ca': (-1285.155, -0.222295, 2, 2),
    'Al': (-1126.073, -0.218805, 4/3, 2/3),
    'Ti': (-939.632, -0.177149, 1, 1),
    'Si': (-910.940, -0.182400, 1, 1),
}


class VaporPressureComputationError(RuntimeError):
    """Raised when vapor-pressure math produces a non-finite value."""


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


def _fit_target(row: Mapping[str, Any] | None) -> str:
    return str((row or {}).get("fit_target", "") or "").strip()


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _range_tuple(value: Any) -> tuple[float, float] | None:
    if not value or len(value) != 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


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

    if not bool((row or {}).get("interval_required")):
        pure = (row or {}).get(COEFF_BLOCK_PURE_COMPONENT)
        if _is_mapping(pure):
            return (
                _selected_temperature_segment(pure, temperature_K),
                COEFF_BLOCK_PURE_COMPONENT,
            )
    antoine = (row or {}).get(COEFF_BLOCK_ANTOINE)
    if _is_mapping(antoine):
        return antoine, COEFF_BLOCK_ANTOINE
    return {}, COEFF_BLOCK_ANTOINE


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


def vapor_pressure_source_label(
    base_source: str,
    row: Mapping[str, Any] | None,
    *,
    coefficient_block: str | None = None,
    temperature_K: float | None = None,
) -> str:
    """Return honest provenance for an Antoine vapor-pressure row."""

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
        return f"{base_source}:pure_component_first_principles"
    if coefficient_block == COEFF_BLOCK_PURE_COMPONENT:
        return f"{base_source}:pure_component_legacy_derivation"
    if bool((row or {}).get("interval_required")):
        return f"{base_source}:interval_required_uncertified"
    if target == FIT_TARGET_PURE_COMPONENT:
        return f"{base_source}:legacy_pure_component_estimate"
    if target == FIT_TARGET_PSEUDO_VAPOROCK:
        return f"{base_source}:backsolved_vaporock_curve_fit"
    if target == FIT_TARGET_STANDARD_REACTION:
        return f"{base_source}:standard_reaction_term"
    if target:
        return f"{base_source}:fit_target={target}"
    return base_source


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
        "VapoRock is authoritative when available.",
        category,
        stacklevel=stacklevel,
    )
    return True


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


def _ellingham_fit_extrapolation(
    temperature_K: float,
    *,
    species: str,
) -> dict[str, object] | None:
    valid_low, valid_high = ELLINGHAM_FIT_RANGE_K
    if valid_low <= temperature_K <= valid_high:
        return None
    return {
        "temperature_K": float(temperature_K),
        "fit_range_K": (valid_low, valid_high),
        "species": species,
    }


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
    """Fallback ``VAPOR_PRESSURE`` provider (Antoine + Ellingham).

    See module docstring.  Originally registered as authoritative
    under goal #7 and demoted to the fallback slot under goal #10
    when VapoRock took over the authoritative role.  The provider
    still declares VAPOR_PRESSURE in
    :attr:`CapabilityProfile.is_authoritative_for` so the registry's
    fallback slot accepts it (an authority-capable provider sitting
    in the fallback slot can take over the authoritative role
    cleanly when VapoRock is unavailable and the simulator opted in
    via ``allow_fallback_vapor=True``).

    ``vapor_pressure_data`` is the parsed
    ``data/vapor_pressures.yaml`` payload (keys: ``metals``,
    ``oxide_vapors``).
    """

    name = "builtin-vapor-pressure"

    DECLARED_ACCOUNT = "process.cleaned_melt"

    def __init__(
        self,
        vapor_pressure_data: Mapping[str, Any],
    ) -> None:
        self._vapor_pressure_data = dict(vapor_pressure_data or {})
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
            # Mirrors _stub_equilibrium: below 400 K, no significant
            # evaporation. Return an empty vapor-pressure dict with an
            # 'ok' status -- this is a converged outcome, not a failure.
            return IntentResult(
                intent=ChemistryIntent.VAPOR_PRESSURE,
                status="ok",
                control_audit=control_audit,
                diagnostic={"vapor_pressures_Pa": {}, "activities": {}},
            )

        pO2_bar = self._resolve_pO2_bar(request)
        comp_wt = composition_wt_pct_from_account_view(
            request.account_view, self.DECLARED_ACCOUNT
        )

        vapor_pressures: dict[str, float] = {}
        vapor_pressure_sources: dict[str, str] = {}
        activities: dict[str, float] = {}
        metal_extrapolations: dict[str, dict[str, object]] = {}
        ellingham_extrapolations: dict[str, dict[str, object]] = {}
        warnings: list[str] = []

        metals_data = self._vapor_pressure_data.get('metals', {}) or {}
        for species, (dH_f, dS_f, n_M, n_ox) in _ELLINGHAM_THERMO.items():
            sp_data = metals_data.get(species, {}) or {}
            if not sp_data:
                continue
            if str(sp_data.get('consumer_status', '')).lower() == 'inactive':
                continue

            parent_oxide = sp_data.get('parent_oxide', '')
            if not parent_oxide:
                continue

            antoine, coefficient_block = vapor_pressure_antoine_coefficients(
                sp_data,
                temperature_K=T_K,
            )
            A = antoine.get('A', 0)
            B = antoine.get('B', 0)
            C = antoine.get('C', 0)
            if not (A > 0 and T_K > 300):
                continue
            valid_range = vapor_pressure_valid_range_K(
                sp_data,
                coefficient_block,
                temperature_K=T_K,
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
                        f"{T_K:.2f} K"
                    )
            log_P = A - B / (T_K + C)
            P_reference_Pa = _pow10_pressure_or_raise(
                log_P,
                species=species,
                field="P_reference_Pa",
            )

            a_oxide = comp_wt.get(parent_oxide, 0.0) / 100.0
            if a_oxide <= 1e-10:
                continue

            ellingham_extrapolation = _ellingham_fit_extrapolation(
                T_K,
                species=species,
            )
            if ellingham_extrapolation is not None:
                ellingham_extrapolations[species] = ellingham_extrapolation
                valid_low, valid_high = ELLINGHAM_FIT_RANGE_K
                warnings.append(
                    f"{species} Ellingham JANAF high-T fit extrapolated beyond "
                    f"fit_range_K [{valid_low:g}, {valid_high:g}] at "
                    f"{T_K:.2f} K"
                )

            activities[species] = a_oxide

            # Ellingham: dG_f(T) = dH_f - T * dS_f (kJ/mol O2)
            dG_f_kJ = dH_f - T_K * dS_f
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
            numerator = _require_finite_vapor_value(
                K_decomp * (a_oxide ** n_ox) / pO2_bar,
                species=species,
                field="metal_activity_numerator",
            )
            if numerator <= 0:
                continue

            a_M_liquid = numerator ** (1.0 / n_M)
            a_M_liquid = _require_finite_vapor_value(
                a_M_liquid,
                species=species,
                field="metal_activity",
            )
            a_M_liquid = min(a_M_liquid, 1.0)
            P_effective_Pa = _require_finite_vapor_value(
                a_M_liquid * P_reference_Pa,
                species=species,
                field="P_effective_Pa",
            )
            if P_effective_Pa > 1e-15:
                vapor_pressures[species] = P_effective_Pa
                source_label = vapor_pressure_source_label(
                    "builtin_fallback",
                    sp_data,
                    coefficient_block=coefficient_block,
                    temperature_K=T_K,
                )
                if species in metal_extrapolations:
                    source_label = (
                        f"{source_label}:"
                        "extrapolated_beyond_valid_range_K"
                    )
                vapor_pressure_sources[species] = source_label
                if coefficient_block == COEFF_BLOCK_ANTOINE:
                    warn_pseudo_vapor_pressure_fallback(
                        species,
                        sp_data,
                        self._pseudo_vapor_pressure_warning_seen,
                        stacklevel=2,
                    )

        oxide_vapors_data = self._vapor_pressure_data.get('oxide_vapors', {}) or {}
        for name, data in oxide_vapors_data.items():
            antoine = (data or {}).get('antoine', {}) or {}
            A = antoine.get('A', 0)
            B = antoine.get('B', 0)
            C = antoine.get('C', 0)
            valid = data.get('valid_range_K', [0, 9999])
            if not (A > 0 and valid[0] <= T_K <= valid[1]):
                continue
            log_P = A - B / (T_K + C)
            P_sat = _pow10_pressure_or_raise(
                log_P,
                species=name,
                field="P_sat",
            )

            parent_oxide = data.get('parent_oxide', '')
            if parent_oxide:
                a_ox = comp_wt.get(parent_oxide, 0.0) / 100.0
                activities[name] = a_ox
                activity_exponent = float(
                    data.get('oxide_activity_exponent', 1.0)
                )
                P_sat = _require_finite_vapor_value(
                    P_sat * max(a_ox, 0.0) ** activity_exponent,
                    species=name,
                    field="P_sat_activity",
                )

            pO2_exponent = float(data.get('pO2_exponent', 0.0) or 0.0)
            if pO2_exponent:
                pO2_reference_bar = max(
                    1e-30, float(data.get('pO2_reference_bar', 1.0) or 1.0)
                )
                P_sat = _require_finite_vapor_value(
                    P_sat * (pO2_bar / pO2_reference_bar) ** pO2_exponent,
                    species=name,
                    field="P_sat_pO2",
                )

            # SiO suppression by pO2: p(SiO) ~ 1/sqrt(pO2). Reference is
            # 1e-9 bar (lunar hard vacuum).
            if name == 'SiO' and not pO2_exponent and pO2_bar > 1e-9:
                suppression = math.sqrt(1e-9 / pO2_bar)
                P_sat = _require_finite_vapor_value(
                    P_sat * suppression,
                    species=name,
                    field="P_sat_suppressed",
                )

            if P_sat > 1e-15:
                vapor_pressures[name] = P_sat
                vapor_pressure_sources[name] = vapor_pressure_source_label(
                    "builtin_fallback",
                    data,
                    coefficient_block=COEFF_BLOCK_ANTOINE,
                    temperature_K=T_K,
                )
                warn_pseudo_vapor_pressure_fallback(
                    name,
                    data,
                    self._pseudo_vapor_pressure_warning_seen,
                    stacklevel=2,
                )

        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status="ok",
            transition=None,
            control_audit=control_audit,
            diagnostic={
                "vapor_pressures_Pa": vapor_pressures,
                "vapor_pressures_source": vapor_pressure_sources,
                "activities": activities,
                "pO2_bar": pO2_bar,
                "extrapolated_beyond_valid_range_K": metal_extrapolations,
                "ellingham_extrapolated_beyond_fit_range_K": (
                    ellingham_extrapolations
                ),
            },
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_pO2_bar(self, request: IntentRequest) -> float:
        """Pick up the commanded pO2 (bar) from the caller.

        The kernel's standard ``fO2_log`` channel carries the absolute
        log10(fO2/bar); the simulator computes the commanded pO2 in
        :meth:`EquilibriumMixin._commanded_pO2_bar` and passes it through
        ``control_inputs['pO2_bar']`` to keep parity with the legacy
        ``_stub_equilibrium`` (which uses that value directly, not the
        fO2_log channel). If neither is supplied, fall back to the
        numerical vacuum floor.
        """

        pO2 = request.control_inputs.get('pO2_bar') if request.control_inputs else None
        if pO2 is not None:
            return max(float(pO2), 1e-9)
        if request.fO2_log is not None:
            return max(10.0 ** float(request.fO2_log), 1e-9)
        return 1e-9
