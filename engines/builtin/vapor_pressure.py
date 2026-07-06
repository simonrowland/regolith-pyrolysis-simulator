"""Builtin VAPOR_PRESSURE provider (Antoine + Ellingham).

Kernel-registered authoritative provider for the ``VAPOR_PRESSURE``
intent. VapoRock may run beside it as a diagnostic shadow, but the
pressure dict consumed by evaporation comes from this builtin provider.

The provider:

- reads ``process.cleaned_melt`` from the account view (the only
  account it declares),
- looks up Antoine coefficients from the ``vapor_pressures.yaml``
  payload passed at construction time,
- combines Ellingham oxide-decomposition equilibrium with Antoine
  reference terms to compute per-species effective equilibrium pressures at the
  request's ``temperature_C`` and the caller-supplied transport/overhead
  ``pO2_bar`` (via ``control_inputs``). The intrinsic-melt fO2/redox
  channel resolves separately from the transport pO2 channel so future
  redox controls cannot alias the SiO transport lever. Only
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
    resolve_request_vacuum_floor_bar,
    resolve_transport_pO2_bar,
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
# Canonical Ellingham table now lives in the dependency-free leaf
# ``simulator.chemistry.ellingham_thermo`` so both this provider and the
# EquilibriumMixin fallback can import it at module level without closing an
# import cycle. Re-exported here under the legacy ``_ELLINGHAM_THERMO`` name
# (consumed by metallothermic_step, vaporock/provider, and tests).
from simulator.chemistry.ellingham_thermo import (  # noqa: E402
    ELLINGHAM_FIT_RANGE_K,
    ELLINGHAM_THERMO as _ELLINGHAM_THERMO,
    ellingham_authority_diagnostic,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_extrapolation,
    ellingham_fit_range_K,
    ellingham_stoichiometry,
)


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
PSEUDO_VAPOROCK_CURVE_FIT_SOURCE = "vaporock_backsolved_curve_fit"
_BUILTIN_VAPOR_SOURCE_CLASSES = frozenset(
    {
        "builtin_authoritative",
        "builtin_extrapolation_limited",
        "builtin_fallback",
    }
)


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
            selected = _selected_temperature_segment(pure, temperature_K)
            use_row_level = False
            if temperature_K is not None:
                try:
                    denominator = float(temperature_K) + float(selected.get("C", 0.0))
                    projected_log_pressure = float(selected.get("A", 0.0)) - (
                        float(selected.get("B", 0.0)) / denominator
                    )
                    use_row_level = (
                        not math.isfinite(projected_log_pressure)
                        or projected_log_pressure > 308.0
                    )
                except (TypeError, ValueError, ZeroDivisionError):
                    use_row_level = True
            if use_row_level:
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
        or str(species) != "Fe"
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
    return ellingham_fit_extrapolation(
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
        feo_activity_diagnostic = None
        if intrinsic_fO2_log is not None:
            from simulator.fe_redox import calphad_ferrous_feo_activity_diagnostic

            feo_activity_diagnostic = calphad_ferrous_feo_activity_diagnostic(
                comp_wt=comp_wt,
                fO2_log=intrinsic_fO2_log,
                T_K=T_K,
                pressure_bar=request.pressure_bar,
                floor_bar=vacuum_floor_bar,
            )

        vapor_pressures: dict[str, float] = {}
        vapor_pressure_sources: dict[str, str] = {}
        vapor_pressure_provenance: dict[str, dict[str, float | str]] = {}
        activities: dict[str, float] = {}
        metal_extrapolations: dict[str, dict[str, object]] = {}
        oxide_vapor_extrapolations: dict[str, dict[str, object]] = {}
        ellingham_extrapolations: dict[str, dict[str, object]] = {}
        warnings: list[str] = []

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

            antoine, coefficient_block = vapor_pressure_antoine_coefficients(
                sp_data,
                temperature_K=T_K,
            )
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

            if parent_oxide == 'FeO' and intrinsic_fO2_log is not None:
                from simulator.fe_redox import kress91_ferrous_feo_activity

                a_oxide = kress91_ferrous_feo_activity(
                    comp_wt=comp_wt,
                    fO2_log=intrinsic_fO2_log,
                    T_K=T_K,
                    pressure_bar=request.pressure_bar,
                    floor_bar=vacuum_floor_bar,
                )
            else:
                a_oxide = comp_wt.get(parent_oxide, 0.0) / 100.0
            if a_oxide <= 1e-10:
                continue

            ellingham_extrapolation = _ellingham_fit_extrapolation(
                T_K,
                species=species,
            )
            if ellingham_extrapolation is not None:
                ellingham_extrapolations[species] = ellingham_extrapolation
                valid_low, valid_high = ellingham_fit_range_K(species)
                warnings.append(
                    f"{species} Ellingham JANAF high-T fit extrapolated beyond "
                    f"fit_range_K [{valid_low:g}, {valid_high:g}] at "
                    f"{T_K:.2f} K"
                )

            activities[species] = a_oxide

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
            numerator = _require_finite_vapor_value(
                K_decomp * (a_oxide ** n_ox) / transport_pO2_bar,
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
            P_eq_Pa = _require_finite_vapor_value(
                a_M_liquid * P_reference_Pa,
                species=species,
                field="P_eq_Pa",
            )
            if P_eq_Pa > 1e-15:
                vapor_pressures[species] = P_eq_Pa
                source_label = vapor_pressure_source_label(
                    "builtin_authoritative",
                    sp_data,
                    coefficient_block=coefficient_block,
                    temperature_K=T_K,
                    authority_limited_by_ellingham_fit_range=(
                        species in ellingham_extrapolations
                    ),
                )
                if species in metal_extrapolations:
                    source_label = (
                        f"{source_label}:"
                        "extrapolated_beyond_valid_range_K"
                    )
                if species in ellingham_extrapolations:
                    source_label = (
                        f"{source_label}:"
                        "extrapolated_beyond_ellingham_fit_range_K"
                    )
                vapor_pressure_sources[species] = source_label
                vapor_pressure_provenance[species] = {
                    "pressure_kind": _runtime_pressure_kind(
                        sp_data,
                        coefficient_block,
                        effective_scaled=(a_M_liquid != 1.0),
                    ),
                    "P_reference_Antoine_Pa": P_reference_Pa,
                    "P_eq_Pa": P_eq_Pa,
                    "pO2_bar": transport_pO2_bar,
                    "activity_factor": a_M_liquid,
                    "source_label": source_label,
                }
                if coefficient_block == COEFF_BLOCK_ANTOINE:
                    warn_pseudo_vapor_pressure_fallback(
                        species,
                        sp_data,
                        self._pseudo_vapor_pressure_warning_seen,
                        stacklevel=2,
                    )

        oxide_vapors_data = self._vapor_pressure_data.get('oxide_vapors', {}) or {}
        for name, data in oxide_vapors_data.items():
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
            if not A > 0:
                continue

            parent_oxide = data.get('parent_oxide', '')
            activity_factor = 1.0
            if parent_oxide:
                a_ox = comp_wt.get(parent_oxide, 0.0) / 100.0
                if a_ox <= 1e-10:
                    continue
                activities[name] = a_ox
                activity_exponent = float(
                    data.get('oxide_activity_exponent', 1.0)
                )
                activity_factor = max(a_ox, 0.0) ** activity_exponent

            valid_range = _range_tuple(valid)
            if valid_range is not None:
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
            if pO2_exponent:
                pO2_reference_bar = max(
                    1e-30, float(data.get('pO2_reference_bar', 1.0) or 1.0)
                )
                P_eq_Pa = _require_finite_vapor_value(
                    P_eq_Pa
                    * (transport_pO2_bar / pO2_reference_bar)
                    ** pO2_exponent,
                    species=name,
                    field="P_eq_pO2",
                )
                pO2_scaled = True

            # SiO suppression by pO2: p(SiO) ~ 1/sqrt(pO2). Reference is
            # the body/environment vacuum floor.
            if (
                name == 'SiO'
                and not pO2_exponent
                and transport_pO2_bar > vacuum_floor_bar
            ):
                suppression = math.sqrt(vacuum_floor_bar / transport_pO2_bar)
                P_eq_Pa = _require_finite_vapor_value(
                    P_eq_Pa * suppression,
                    species=name,
                    field="P_eq_suppressed",
                )
                pO2_scaled = True

            if P_eq_Pa > 1e-15:
                vapor_pressures[name] = P_eq_Pa
                source_label = vapor_pressure_source_label(
                    "builtin_authoritative",
                    data,
                    coefficient_block=COEFF_BLOCK_ANTOINE,
                    temperature_K=T_K,
                    authority_limited_by_ellingham_fit_range=(
                        name in ellingham_extrapolations
                    ),
                )
                if name in oxide_vapor_extrapolations:
                    source_label = (
                        f"{source_label}:"
                        "extrapolated_beyond_valid_range_K"
                    )
                vapor_pressure_sources[name] = source_label
                vapor_pressure_provenance[name] = {
                    "pressure_kind": _runtime_pressure_kind(
                        data,
                        COEFF_BLOCK_ANTOINE,
                        effective_scaled=(
                            activity_factor != 1.0 or pO2_scaled
                        ),
                    ),
                    "P_reference_Antoine_Pa": P_reference_Pa,
                    "P_eq_Pa": P_eq_Pa,
                    "pO2_bar": transport_pO2_bar,
                    "activity_factor": activity_factor,
                    "source_label": source_label,
                }
                warn_pseudo_vapor_pressure_fallback(
                    name,
                    data,
                    self._pseudo_vapor_pressure_warning_seen,
                    stacklevel=2,
                )

        diagnostic = {
            "vapor_pressures_Pa": vapor_pressures,
            "vapor_pressures_source": vapor_pressure_sources,
            "vapor_pressure_numerator_provenance": vapor_pressure_provenance,
            "activities": activities,
            "pO2_bar": transport_pO2_bar,
            "vacuum_floor_bar": vacuum_floor_bar,
            "extrapolated_beyond_valid_range_K": {
                **metal_extrapolations,
                **oxide_vapor_extrapolations,
            },
            "ellingham_extrapolated_beyond_fit_range_K": (
                ellingham_extrapolations
            ),
            "ellingham_authority": ellingham_authority_diagnostic(
                ellingham_extrapolations,
                consumer="builtin-vapor-pressure",
            ),
            "structural_activity_reference": structural_activity_reference,
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
        ``_stub_equilibrium``. If that explicit transport channel is
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
