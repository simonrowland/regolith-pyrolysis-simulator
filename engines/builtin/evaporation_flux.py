"""Builtin EVAPORATION_FLUX provider (Hertz-Knudsen-Langmuir).

Kernel-registered provider that owns the ``EVAPORATION_FLUX`` intent.
Mirrors the kinetic flux math in
:meth:`simulator.evaporation.EvaporationMixin._calculate_evaporation`
exactly -- this is a refactor of where the per-species flux dict is
computed, not a re-derivation of how the Hertz-Knudsen-Langmuir equation
works. The provider:

- reads ``process.cleaned_melt`` from the account view (only declared
  account; satisfies binding-spec §4 even though analytic depletion and
  availability capping happen in the simulator integration layer),
- reads T from ``request.temperature_C``,
- reads per-species vapor pressures via
  ``request.control_inputs['vapor_pressures_Pa']`` -- caller passes them
  (the kernel has already produced them via the VAPOR_PRESSURE intent in
  the same tick; the provider does NOT call the kernel recursively, that
  would couple intents inside a provider),
- reads per-species overhead partials via
  ``request.control_inputs['overhead_partials_Pa']``,
- treats the supplied vapor pressures as already-equilibrated ``P_eq``;
  pO2 dependence belongs to the VAPOR_PRESSURE intent, not this flux
  intent,
- reads melt surface area, stir factor, evaporation coefficient via
  ``control_inputs['melt_surface_area_m2']``,
  ``control_inputs['stir_factor']``, ``control_inputs['alpha']``
  (a per-species mapping),
- reads per-species stoichiometry and available parent-oxide mass via
  ``control_inputs['stoich_by_species']`` and
  ``control_inputs['available_oxide_kg']`` (precomputed by the caller --
  see :meth:`EvaporationMixin._evaporation_stoich` for the source; the
  provider cannot call instance methods, so the caller serialises the
  stoich map into the request),
- reads per-species molar masses via
  ``control_inputs['molar_mass_kg_mol']`` (caller pulls these from the
  same ``vapor_pressures.yaml`` payload the legacy path uses).

Returns an :class:`IntentResult` with ``transition=None`` (kinetic flux
is a *diagnostic* per binding spec §3; the atom-conserving ledger
transition belongs to the separate ``EVAPORATION_TRANSITION`` intent --
not yet migrated) and an ``evaporation_flux_kg_hr`` diagnostic dict.

Authority: authoritative for ``EVAPORATION_FLUX`` per binding spec §3
until a future Hertz-Knudsen replacement promotes a new provider.

Account declaration: ``process.cleaned_melt`` only -- the same account
the VAPOR_PRESSURE provider declares, and the same one the legacy
:meth:`_calculate_evaporation` mutates downstream via
``_credit_evaporation_transition``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from engines.builtin._common import (
    diagnostic_control_audit,
    reject_wrong_intent,
    unpack_controls,
)
from simulator.alpha_kinetics import (
    ALPHA_AUTHORITY_STATUS_FIELD,
    ANALYTICAL_UPPER_BOUND_ALPHA_STATUS,
)
from simulator.chemistry.kernel.capabilities import CapabilityProfile, ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.scalar_boundary import is_declared_real_scalar


_DEFAULT_EVAPORATION_ALPHA = 1.0
_NONTRIVIAL_FLUX_KG_HR = 1.0e-12
DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA = 0.0
DEFAULT_MELT_SURFACE_RENEWAL_SOURCE = (
    "disabled:missing-species-state-dependent-melt-transfer-inputs"
)
# EVAPORATION-only class proxies: systematically composed refractory carriers borrow
# SiO sigma and epsilon/k because direct high-temperature transport data are
# absent, but each retains its formula molar mass. Premise: Chapman-Enskog
# D_AB scales as sqrt(1/M_A+1/M_B)/(sigma_AB^2*Omega_D); copying SiO's
# (sigma, epsilon/k) therefore changes only the unknown collision class while
# preserving the exactly derivable mass term. Units are Angstrom, K, g/mol.
# Sanity: SiO itself is the closest measured rail carrier; this proxy affects
# only continuum gas resistance, never K(T), P_eq, stoichiometry, or the
# condensation/deposition transport table. Outputs remain status-bearing and
# direct collision data remain acquisition targets.
_EVAPORATION_LJ_PROXY_PARAMS = {
    # t-609: keep the diagnostic FeO/NiO upper-screen flux computable without
    # pretending direct transport authority. The SiO collision class is a
    # status-bearing continuum-resistance proxy; exact formula masses remain
    # species-specific, and the request gate never admits inventory debit.
    "FeO_association_gas": (3.374, 71.4, 71.8440),
    "NiO_gas": (3.374, 71.4, 74.6924),
    "CrO2": (3.374, 71.4, 83.9941),
    "CrO": (3.374, 71.4, 67.9951),
    "Al2": (3.374, 71.4, 53.9630770),
    "AlO": (3.374, 71.4, 42.9805385),
    "Al2O": (3.374, 71.4, 69.9620770),
    "Al2O2": (3.374, 71.4, 85.9610770),
    "Al2O3_gas": (3.374, 71.4, 101.9600770),
    "AlO2": (3.374, 71.4, 58.9795385),
    "Ca2": (3.374, 71.4, 80.1560),
    "CaO_gas": (3.374, 71.4, 56.0770),
    "TiO": (3.374, 71.4, 63.8660),
    "TiO2_gas": (3.374, 71.4, 79.8650),
    "CrO3": (3.374, 71.4, 99.9931),
    "K2": (3.374, 71.4, 78.1966),
    "K2O_gas": (3.374, 71.4, 94.1956),
    "Mg2": (3.374, 71.4, 48.6100),
    "MgO_gas": (3.374, 71.4, 40.3040),
    "Na2": (3.374, 71.4, 45.97953856),
    "Na2O_gas": (3.374, 71.4, 61.97853856),
    "P2O5_gas": (3.374, 71.4, 141.942523996),
    "Si2": (3.374, 71.4, 56.1700),
    "Si3": (3.374, 71.4, 84.2550),
    "SiO2_gas": (3.374, 71.4, 60.0830),
}


class EvaporationFluxConfigurationError(ValueError):
    """Invalid or unsupported transport inputs for authoritative flux."""


@dataclass(frozen=True)
class SeriesEvaporationFlux:
    flux_kg_s_m2: float
    alpha_intrinsic: float
    alpha_effective: float
    k_hk_kg_s_m2_pa: float
    r_interface: float
    r_gas: float
    r_melt: float
    knudsen_number: float
    gas_resistance_weight: float
    axial_stir_applied: float
    radial_stir_applied: float
    axial_stir_clamped: bool
    radial_stir_clamped: bool
    frozen_skull_stir_clamped: bool
    frozen_skull_stir_ceiling: float | str
    melt_resistance_enabled: bool
    melt_surface_renewal_base_kg_s_m2_pa: float
    melt_surface_renewal_source: str
    k_mt_kg_s_m2_pa: float
    d_ab_m2_s: float
    transport_length_m: float

    def as_diagnostic(self) -> dict[str, float | str | bool]:
        denominator = self.r_interface + self.r_gas + self.r_melt
        if math.isfinite(denominator) and denominator > 0.0:
            r_interface_fraction = self.r_interface / denominator
            r_gas_fraction = self.r_gas / denominator
            r_melt_fraction = self.r_melt / denominator
            limiting_resistance_label = max(
                (
                    ("interface", r_interface_fraction),
                    ("gas", r_gas_fraction),
                    ("melt", r_melt_fraction),
                ),
                key=lambda item: item[1],
            )[0]
        elif math.isinf(self.r_melt):
            r_interface_fraction = 0.0
            r_gas_fraction = 0.0
            r_melt_fraction = 1.0
            limiting_resistance_label = "melt"
        else:
            r_interface_fraction = 0.0
            r_gas_fraction = 0.0
            r_melt_fraction = 0.0
            limiting_resistance_label = "none"
        return {
            "flux_kg_s_m2": self.flux_kg_s_m2,
            "alpha_intrinsic": self.alpha_intrinsic,
            "alpha_effective": self.alpha_effective,
            "alpha_eff": self.alpha_effective,
            "k_hk_kg_s_m2_pa": self.k_hk_kg_s_m2_pa,
            "r_interface": self.r_interface,
            "r_gas": self.r_gas,
            "r_melt": self.r_melt,
            "R_interface_fraction": r_interface_fraction,
            "R_gas_fraction": r_gas_fraction,
            "R_melt_fraction": r_melt_fraction,
            "limiting_resistance_label": limiting_resistance_label,
            "knudsen_number": self.knudsen_number,
            "Kn": self.knudsen_number,
            "gas_resistance_weight": self.gas_resistance_weight,
            "axial_stir_applied": self.axial_stir_applied,
            "radial_stir_applied": self.radial_stir_applied,
            "axial_stir_clamped": self.axial_stir_clamped,
            "radial_stir_clamped": self.radial_stir_clamped,
            "frozen_skull_stir_clamped": self.frozen_skull_stir_clamped,
            "frozen_skull_stir_ceiling": self.frozen_skull_stir_ceiling,
            "melt_resistance_enabled": self.melt_resistance_enabled,
            "melt_surface_renewal_base_kg_s_m2_pa": (
                self.melt_surface_renewal_base_kg_s_m2_pa
            ),
            "melt_surface_renewal_source": self.melt_surface_renewal_source,
            "k_mt_kg_s_m2_pa": self.k_mt_kg_s_m2_pa,
            "d_ab_m2_s": self.d_ab_m2_s,
            "transport_length_m": self.transport_length_m,
            # HKL upper-bound authority (matrix policy i): R_m=0 until
            # species/state melt-transfer inputs exist. Not certified flux.
            "authority_class": "upper-bound",
            "authority_reason": (
                "missing-species-state-dependent-melt-transfer-inputs"
            ),
        }


def _coerce_alpha_by_species(alpha_control) -> dict[str, Any]:
    if isinstance(alpha_control, Mapping):
        coerced: dict[str, Any] = {}
        for species, value in alpha_control.items():
            if isinstance(value, Mapping):
                coerced[str(species)] = dict(value)
                continue
            if not is_declared_real_scalar(value, allow_numeric_str=True):
                raise TypeError(f"alpha for {species} must be numeric")
            coerced[str(species)] = float(value)
        return coerced
    if alpha_control is None:
        return {}
    if not is_declared_real_scalar(alpha_control, allow_numeric_str=True):
        raise TypeError("alpha_control must be numeric or a mapping")
    return {"*": float(alpha_control)}


def _evaluate_alpha_control(
    species: str,
    T_K: float,
    alpha_spec: Any,
) -> tuple[float, dict[str, Any]]:
    from simulator.condensation import alpha_s

    context: dict[str, Any] = {'coefficient_spec': alpha_spec}
    value = alpha_s(species, T_K, context)
    evaluation = dict(context.get('alpha_s_evaluation', {}))
    if (
        isinstance(alpha_spec, Mapping)
        and alpha_spec.get(ALPHA_AUTHORITY_STATUS_FIELD)
        == ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
    ):
        evaluation[ALPHA_AUTHORITY_STATUS_FIELD] = (
            ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
        )
    return value, evaluation


def _alpha_is_unmeasured(
    species: str, alpha_by_species: Mapping[str, Any]
) -> bool:
    return species not in alpha_by_species and "*" not in alpha_by_species


def _unmeasured_alpha_fallback_permitted(
    species: str,
    *,
    allow_unmeasured_alpha_fallback: bool,
    unmeasured_alpha_fallback_species_allowlist: set[str] | None,
) -> bool:
    return bool(allow_unmeasured_alpha_fallback) and (
        unmeasured_alpha_fallback_species_allowlist is None
        or species in unmeasured_alpha_fallback_species_allowlist
    )


def _missing_alpha_record(
    *,
    P_eq_Pa: float,
    P_bulk_Pa: float,
    baseline_alpha_1_rate_kg_hr: float,
) -> dict[str, float | str]:
    return {
        "policy": "fail_loud_missing_alpha",
        "fallback_control": "chemistry_kernel.allow_unmeasured_alpha_fallback",
        "P_eq_Pa": P_eq_Pa,
        "P_bulk_Pa": P_bulk_Pa,
        "baseline_alpha_1_rate_kg_hr": baseline_alpha_1_rate_kg_hr,
    }


def _attach_missing_alpha_records(
    diagnostic: dict[str, Any],
    missing_alpha: Mapping[str, Mapping[str, float | str]],
    stoich_by_species: Mapping[str, Any],
) -> None:
    if not missing_alpha:
        return
    species_refusals: dict[str, dict[str, float | str]] = {
        str(species): dict(refusal)
        for species, refusal in (
            diagnostic.get("species_refusals") or {}
        ).items()
        if isinstance(refusal, Mapping)
    }
    for species, refusal in missing_alpha.items():
        species_refusals[str(species)] = {
            **dict(refusal),
            "status": "refused",
            "reason": "missing_evaporation_alpha",
            "disposition": "retained_in_condensed_parent_oxide",
            "parent_oxide": str(
                (stoich_by_species.get(species) or {}).get(
                    "parent_oxide", "unknown"
                )
            ),
        }
    diagnostic["missing_alpha"] = dict(missing_alpha)
    diagnostic["species_refusals"] = species_refusals


def _coerce_alpha_envelope_by_species(alpha_envelope_control) -> dict[str, tuple[float, float]]:
    if not isinstance(alpha_envelope_control, Mapping):
        return {}

    envelopes: dict[str, tuple[float, float]] = {}
    for species, envelope in alpha_envelope_control.items():
        if not isinstance(envelope, (list, tuple)) or len(envelope) != 2:
            continue
        low_raw, high_raw = envelope
        if not is_declared_real_scalar(
            low_raw,
            allow_numeric_str=True,
        ) or not is_declared_real_scalar(high_raw, allow_numeric_str=True):
            raise TypeError(f"alpha envelope for {species} must be numeric")
        low, high = float(low_raw), float(high_raw)
        envelopes[str(species)] = (low, high)
    return envelopes


def _flux_uncertainty_pct(
    alpha: float,
    envelope: tuple[float, float] | None,
) -> float | None:
    if envelope is None or alpha <= 0.0:
        return None
    low, high = envelope
    relative_span = max(abs(alpha - low), abs(high - alpha)) / alpha
    return relative_span * 100.0


def _finite_float(value, default: float) -> float:
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        raw = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(raw):
        return default
    return raw


def _stir_was_clamped(value, applied: float) -> bool:
    if isinstance(value, bool):
        return True
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(raw):
        return True
    return raw != applied


def _validated_stir_factor(value: Any, *, axis: str) -> float:
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        raw = float(value)
    except (TypeError, ValueError) as exc:
        raise EvaporationFluxConfigurationError(
            f"{axis}_stir_factor must be finite and non-negative"
        ) from exc
    if not math.isfinite(raw) or raw < 0.0:
        raise EvaporationFluxConfigurationError(
            f"{axis}_stir_factor must be finite and non-negative"
        )
    return raw


def _series_pressure_provenance_diagnostic(
    *,
    species: str,
    P_eq_Pa: float,
    P_bulk_Pa: float,
    pressure_provenance_by_species: Mapping[str, object],
    vapor_pressure_sources: Mapping[str, object],
    vapor_pressure_activities: Mapping[str, object],
    pO2_bar: object,
) -> dict[str, float | str | None]:
    provenance = pressure_provenance_by_species.get(species)
    provenance_map = provenance if isinstance(provenance, Mapping) else {}
    source_label = str(
        provenance_map.get("source_label")
        or vapor_pressure_sources.get(species)
        or "control_inputs:vapor_pressures_Pa"
    )
    activity_factor = provenance_map.get(
        "activity_factor", vapor_pressure_activities.get(species, 1.0)
    )
    try:
        activity_factor_value = float(activity_factor)
    except (TypeError, ValueError):
        activity_factor_value = 1.0
    provenance_pO2 = provenance_map.get("pO2_bar", pO2_bar)
    try:
        pO2_bar_value = float(provenance_pO2)
    except (TypeError, ValueError):
        pO2_bar_value = None
    diagnostic: dict[str, float | str | None] = {
        "pressure_kind": str(
            provenance_map.get("pressure_kind") or "effective_equilibrium"
        ),
        "P_eq_Pa": float(provenance_map.get("P_eq_Pa", P_eq_Pa)),
        "P_bulk_Pa": float(P_bulk_Pa),
        "pO2_bar": pO2_bar_value,
        "activity_factor": activity_factor_value,
        "source_label": source_label,
    }
    P_reference_Antoine_Pa = provenance_map.get("P_reference_Antoine_Pa")
    if P_reference_Antoine_Pa is not None:
        diagnostic["P_reference_Antoine_Pa"] = float(P_reference_Antoine_Pa)
    return diagnostic


def _coerce_frozen_skull_stir_ceiling(
    cold_skull_envelope: Mapping[str, float] | None,
) -> tuple[float, float | str]:
    from simulator.state import MAX_STIR_FACTOR, clamp_stir_factor

    if not isinstance(cold_skull_envelope, Mapping):
        return float(MAX_STIR_FACTOR), "not_certified"
    for key in (
        "frozen_skull_stir_ceiling",
        "max_axial_stir_factor",
        "axial_stir_ceiling",
    ):
        if key in cold_skull_envelope:
            ceiling = clamp_stir_factor(cold_skull_envelope.get(key))
            return ceiling, ceiling
    return float(MAX_STIR_FACTOR), "not_certified"


def _zero_series_evaporation_flux(
    *,
    alpha_intrinsic: float,
    k_hk_kg_s_m2_pa: float = 0.0,
    r_interface: float = math.inf,
    r_gas: float = 0.0,
    r_melt: float = 0.0,
    knudsen_number: float = math.inf,
    gas_resistance_weight: float = 0.0,
    axial_stir_applied: float = 0.0,
    radial_stir_applied: float = 1.0,
    axial_stir_clamped: bool = False,
    radial_stir_clamped: bool = False,
    frozen_skull_stir_clamped: bool = False,
    frozen_skull_stir_ceiling: float | str = "not_certified",
    melt_resistance_enabled: bool = False,
    melt_surface_renewal_base_kg_s_m2_pa: float = (
        DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA
    ),
    melt_surface_renewal_source: str = DEFAULT_MELT_SURFACE_RENEWAL_SOURCE,
    k_mt_kg_s_m2_pa: float = 0.0,
    d_ab_m2_s: float = 0.0,
    transport_length_m: float = 0.0,
) -> SeriesEvaporationFlux:
    return SeriesEvaporationFlux(
        flux_kg_s_m2=0.0,
        alpha_intrinsic=max(0.0, alpha_intrinsic),
        alpha_effective=0.0,
        k_hk_kg_s_m2_pa=max(0.0, k_hk_kg_s_m2_pa),
        r_interface=r_interface,
        r_gas=max(0.0, r_gas),
        r_melt=max(0.0, r_melt),
        knudsen_number=knudsen_number,
        gas_resistance_weight=max(0.0, min(1.0, gas_resistance_weight)),
        axial_stir_applied=max(0.0, axial_stir_applied),
        radial_stir_applied=max(0.0, radial_stir_applied),
        axial_stir_clamped=axial_stir_clamped,
        radial_stir_clamped=radial_stir_clamped,
        frozen_skull_stir_clamped=frozen_skull_stir_clamped,
        frozen_skull_stir_ceiling=frozen_skull_stir_ceiling,
        melt_resistance_enabled=bool(melt_resistance_enabled),
        melt_surface_renewal_base_kg_s_m2_pa=(
            max(0.0, melt_surface_renewal_base_kg_s_m2_pa)
        ),
        melt_surface_renewal_source=melt_surface_renewal_source,
        k_mt_kg_s_m2_pa=max(0.0, k_mt_kg_s_m2_pa),
        d_ab_m2_s=max(0.0, d_ab_m2_s),
        transport_length_m=max(0.0, transport_length_m),
    )


def _series_resistance_evaporation_flux_kg_m2_s(
    species: str,
    P_eq_pa: float,
    P_bulk_pa: float,
    T_surface_K: float,
    molar_mass_kg_mol: float,
    alpha_i: float,
    knudsen_number: float | None = None,
    pipe_diameter_m: float = 0.12,
    overhead_pressure_pa: float | None = None,
    axial_stir_factor: float | None = 0.0,
    radial_stir_factor: float | None = 1.0,
    cold_skull_envelope: Mapping[str, float] | None = None,
    carrier_gas: str = "N2",
    *,
    T_gas_K: float | None = None,
    melt_resistance_enabled: bool = False,
    melt_surface_renewal_base_kg_s_m2_pa: float = (
        DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA
    ),
    melt_surface_renewal_source: str = DEFAULT_MELT_SURFACE_RENEWAL_SOURCE,
    gas_resistance_enabled: bool = True,
) -> SeriesEvaporationFlux:
    """Series-resistance evaporation source in kg/(m^2*s).

    The sole driving pressure is ``max(0, P_eq_pa - P_bulk_pa)``. External
    pressure regime and stirring terms only add or remove resistances; they
    never inflate the intrinsic Hertz-Knudsen alpha.
    """

    from simulator.condensation import (
        DEFAULT_CARRIER_GAS,
        GAS_CONSTANT_J_MOL_K,
        _chapman_enskog_d_ab_m2_s,
        _knudsen_number,
        _stirring_enhanced_sherwood,
    )
    from simulator.state import MAX_STIR_FACTOR, clamp_stir_factor

    alpha_intrinsic = max(0.0, _finite_float(alpha_i, 0.0))
    P_eq = _finite_float(P_eq_pa, 0.0)
    P_bulk = _finite_float(P_bulk_pa, 0.0)
    T_surface = _finite_float(T_surface_K, 0.0)
    M_kg_mol = _finite_float(molar_mass_kg_mol, 0.0)
    diameter_m = _finite_float(pipe_diameter_m, 0.12)
    overhead_pa = _finite_float(overhead_pressure_pa, 0.0)
    effective_T_gas_K = _finite_float(T_gas_K, T_surface)

    axial_stir_validated = _validated_stir_factor(
        axial_stir_factor,
        axis="axial",
    )
    radial_stir_validated = _validated_stir_factor(
        radial_stir_factor,
        axis="radial",
    )
    axial_clamped = clamp_stir_factor(axial_stir_validated)
    radial_clamped = clamp_stir_factor(radial_stir_validated)
    axial_stir_ceiling, ceiling_diag = _coerce_frozen_skull_stir_ceiling(
        cold_skull_envelope
    )
    axial_stir_applied = min(axial_clamped, axial_stir_ceiling)
    radial_stir_applied = radial_clamped
    axial_was_clamped = _stir_was_clamped(axial_stir_validated, axial_clamped)
    radial_was_clamped = _stir_was_clamped(radial_stir_validated, radial_clamped)
    frozen_skull_clamped = axial_stir_applied < axial_clamped

    if (
        P_eq <= 0.0
        or T_surface <= 0.0
        or M_kg_mol <= 0.0
        or alpha_intrinsic <= 0.0
        or diameter_m <= 0.0
    ):
        return _zero_series_evaporation_flux(
            alpha_intrinsic=alpha_intrinsic,
            axial_stir_applied=axial_stir_applied,
            radial_stir_applied=radial_stir_applied,
            axial_stir_clamped=axial_was_clamped,
            radial_stir_clamped=radial_was_clamped,
            frozen_skull_stir_clamped=frozen_skull_clamped,
            frozen_skull_stir_ceiling=ceiling_diag,
            melt_resistance_enabled=melt_resistance_enabled,
            melt_surface_renewal_base_kg_s_m2_pa=(
                melt_surface_renewal_base_kg_s_m2_pa
            ),
            melt_surface_renewal_source=melt_surface_renewal_source,
            transport_length_m=max(0.0, diameter_m),
        )

    delta_p_pa = max(0.0, P_eq - P_bulk)
    k_hk_kg_s_m2_pa = math.sqrt(
        M_kg_mol / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * T_surface)
    )
    r_interface = 1.0 / (alpha_intrinsic * k_hk_kg_s_m2_pa)
    if delta_p_pa <= 0.0:
        return _zero_series_evaporation_flux(
            alpha_intrinsic=alpha_intrinsic,
            k_hk_kg_s_m2_pa=k_hk_kg_s_m2_pa,
            r_interface=r_interface,
            axial_stir_applied=axial_stir_applied,
            radial_stir_applied=radial_stir_applied,
            axial_stir_clamped=axial_was_clamped,
            radial_stir_clamped=radial_was_clamped,
            frozen_skull_stir_clamped=frozen_skull_clamped,
            frozen_skull_stir_ceiling=ceiling_diag,
            melt_resistance_enabled=melt_resistance_enabled,
            melt_surface_renewal_base_kg_s_m2_pa=(
                melt_surface_renewal_base_kg_s_m2_pa
            ),
            melt_surface_renewal_source=melt_surface_renewal_source,
            transport_length_m=max(0.0, diameter_m),
        )

    base_k_melt = _finite_float(melt_surface_renewal_base_kg_s_m2_pa, 0.0)
    if melt_resistance_enabled:
        # Premise: liquid transport is J=M_i*k_L,i*(C_b-C_s). Linearising
        # p_eq(C) with H_i=dp_eq/dC_i gives the pressure-basis conductance
        # k_m,i=M_i*k_L,i/H_i [kg Pa^-1 m^-2 s^-1 = s/m], hence
        # R_m,i=H_i/(M_i*k_L,i) [m/s]. k_L,i itself requires a species- and
        # composition-dependent D_i plus measured melt renewal/geometry.
        # None of D_i, H_i, C_i, or a physical renewal rate exists in this
        # request, so any positive universal coefficient (formerly 1e-4 s/m)
        # is dimensionally shaped but ungrounded. Refuse it; R_m=0 below is
        # explicitly the reconstructible HKL upper bound, consistent with
        # Hashimoto/Fedkin vacuum free-evaporation being interface-controlled.
        raise EvaporationFluxConfigurationError(
            "authoritative melt resistance requires species- and state-specific "
            "D_i, k_L,i, and dp_eq/dC_i; universal pressure conductance disabled"
        )

    if knudsen_number is None:
        knudsen = _knudsen_number(
            overhead_pa,
            max(effective_T_gas_K, 1.0),
            diameter_m,
            carrier_gas=str(carrier_gas or DEFAULT_CARRIER_GAS),
        )
    else:
        try:
            knudsen = float(knudsen_number)
        except (TypeError, ValueError):
            knudsen = math.inf
    from simulator.transport_constants import VISCOUS_KNUDSEN_MAX

    if math.isnan(knudsen) or knudsen < 0.0:
        raise EvaporationFluxConfigurationError(
            "knudsen_number must be finite non-negative or positive infinity"
        )

    gas_weight = 0.0
    if gas_resistance_enabled:
        if knudsen < VISCOUS_KNUDSEN_MAX:
            gas_weight = 1.0
        # Premise: an open free-molecular vacuum has no stagnant carrier-gas
        # film. Therefore R_g=0 locally. Continuum branch: k_g=M*Sh*D/(L*R*T)
        # [s/m], R_g=1/k_g [m/s], only for Kn < VISCOUS_KNUDSEN_MAX (0.01).
        # TRANSPORT-MODEL VALIDITY (not a Kn safety/coating gate): ledger-
        # authoritative yields refuse when Kn >= VISCOUS_KNUDSEN_MAX and
        # overhead_pressure > 0, because evolved P_bulk still comes from the
        # viscous-only Poiseuille model (simulator/overhead.py). The 0.6.3
        # optimizer floor (~1 mbar, Kn≈0.004) never enters that domain; t-379
        # (0.7) supplies transitional/molecular conductance and lifts the
        # validity refusal. Do not invent a molecular-flow model here. True
        # vacuum (overhead_pressure=0) remains the reconstructible HKL bound.

    r_gas = 0.0
    k_mt_kg_s_m2_pa = 0.0
    d_ab_m2_s = 0.0
    if gas_weight > 0.0:
        sherwood_eff = _stirring_enhanced_sherwood(
            radial_stir_factor=radial_stir_applied
        )
        if overhead_pa <= 0.0:
            raise EvaporationFluxConfigurationError(
                "gas resistance requires positive overhead_pressure_pa"
            )
        d_ab_m2_s = _chapman_enskog_d_ab_m2_s(
            species,
            max(effective_T_gas_K, 1.0),
            overhead_pa,
            carrier=str(carrier_gas or DEFAULT_CARRIER_GAS),
            species_params=_EVAPORATION_LJ_PROXY_PARAMS.get(species),
        )
        if not math.isfinite(d_ab_m2_s) or d_ab_m2_s <= 0.0:
            raise EvaporationFluxConfigurationError(
                "missing Chapman-Enskog transport parameters for "
                f"species={species!r} carrier_gas={carrier_gas!r}"
            )
        k_mt_mol_s_m2_pa = (
            sherwood_eff
            * d_ab_m2_s
            / (diameter_m * GAS_CONSTANT_J_MOL_K * max(effective_T_gas_K, 1.0))
        )
        k_mt_kg_s_m2_pa = k_mt_mol_s_m2_pa * M_kg_mol
        if not math.isfinite(k_mt_kg_s_m2_pa) or k_mt_kg_s_m2_pa <= 0.0:
            return _zero_series_evaporation_flux(
                alpha_intrinsic=alpha_intrinsic,
                k_hk_kg_s_m2_pa=k_hk_kg_s_m2_pa,
                r_interface=r_interface,
                knudsen_number=knudsen,
                gas_resistance_weight=gas_weight,
                axial_stir_applied=axial_stir_applied,
                radial_stir_applied=radial_stir_applied,
                axial_stir_clamped=axial_was_clamped,
                radial_stir_clamped=radial_was_clamped,
                frozen_skull_stir_clamped=frozen_skull_clamped,
                frozen_skull_stir_ceiling=ceiling_diag,
                melt_resistance_enabled=melt_resistance_enabled,
                melt_surface_renewal_base_kg_s_m2_pa=(
                    melt_surface_renewal_base_kg_s_m2_pa
                ),
                melt_surface_renewal_source=melt_surface_renewal_source,
                d_ab_m2_s=d_ab_m2_s,
                transport_length_m=diameter_m,
            )
        r_gas = gas_weight / k_mt_kg_s_m2_pa

    r_melt = 0.0

    denominator = r_interface + r_gas + r_melt
    if not math.isfinite(denominator) or denominator <= 0.0:
        return _zero_series_evaporation_flux(
            alpha_intrinsic=alpha_intrinsic,
            k_hk_kg_s_m2_pa=k_hk_kg_s_m2_pa,
            r_interface=r_interface,
            r_gas=r_gas,
            r_melt=r_melt,
            knudsen_number=knudsen,
            gas_resistance_weight=gas_weight,
            axial_stir_applied=axial_stir_applied,
            radial_stir_applied=radial_stir_applied,
            axial_stir_clamped=axial_was_clamped,
            radial_stir_clamped=radial_was_clamped,
            frozen_skull_stir_clamped=frozen_skull_clamped,
            frozen_skull_stir_ceiling=ceiling_diag,
            melt_resistance_enabled=melt_resistance_enabled,
            melt_surface_renewal_base_kg_s_m2_pa=base_k_melt,
            melt_surface_renewal_source=melt_surface_renewal_source,
            k_mt_kg_s_m2_pa=k_mt_kg_s_m2_pa,
            d_ab_m2_s=d_ab_m2_s,
            transport_length_m=diameter_m,
        )

    k_total_kg_s_m2_pa = 1.0 / denominator
    flux_kg_s_m2 = delta_p_pa * k_total_kg_s_m2_pa
    alpha_effective = k_total_kg_s_m2_pa / k_hk_kg_s_m2_pa
    if not math.isfinite(flux_kg_s_m2) or not math.isfinite(alpha_effective):
        flux_kg_s_m2 = 0.0
        alpha_effective = 0.0

    return SeriesEvaporationFlux(
        flux_kg_s_m2=max(0.0, flux_kg_s_m2),
        alpha_intrinsic=alpha_intrinsic,
        alpha_effective=max(0.0, min(alpha_intrinsic, alpha_effective)),
        k_hk_kg_s_m2_pa=k_hk_kg_s_m2_pa,
        r_interface=r_interface,
        r_gas=r_gas,
        r_melt=r_melt,
        knudsen_number=knudsen,
        gas_resistance_weight=gas_weight,
        axial_stir_applied=axial_stir_applied,
        radial_stir_applied=radial_stir_applied,
        axial_stir_clamped=axial_was_clamped,
        radial_stir_clamped=radial_was_clamped,
        frozen_skull_stir_clamped=frozen_skull_clamped,
        frozen_skull_stir_ceiling=ceiling_diag,
        melt_resistance_enabled=bool(melt_resistance_enabled),
        melt_surface_renewal_base_kg_s_m2_pa=base_k_melt,
        melt_surface_renewal_source=melt_surface_renewal_source,
        k_mt_kg_s_m2_pa=k_mt_kg_s_m2_pa,
        d_ab_m2_s=d_ab_m2_s,
        transport_length_m=diameter_m,
    )


class BuiltinEvaporationFluxProvider(ChemistryProvider):
    """Authoritative ``EVAPORATION_FLUX`` provider (Hertz-Knudsen-Langmuir).

    See module docstring. The provider is stateless -- every per-call
    input arrives through :class:`IntentRequest.control_inputs` so the
    same instance can serve every campaign / tick without holding
    simulator state.
    """

    name = "builtin-evaporation-flux"

    DECLARED_ACCOUNT = "process.cleaned_melt"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-evaporation-flux",
            intents=frozenset({ChemistryIntent.EVAPORATION_FLUX}),
            is_authoritative_for=frozenset({ChemistryIntent.EVAPORATION_FLUX}),
            declared_accounts=frozenset({self.DECLARED_ACCOUNT}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        # Lazy import: simulator.state pulls in simulator/__init__ which
        # re-enters this module during package init -- see
        # engines/builtin/__init__.py for the cycle description.
        from simulator.state import MOLAR_MASS

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.EVAPORATION_FLUX
        )
        if wrong_intent is not None:
            return wrong_intent

        # Kinetic flux math runs against the request's T/P/fO2 directly;
        # no independent feedback. Diagnostic-only audit.
        control_audit = diagnostic_control_audit(request)

        T_C = request.temperature_C
        T_K = T_C + 273.15
        controls = unpack_controls(request)
        # Uncertified melt-resistance model is refused at any T so the
        # hostile config cannot ride a cold early-return (T<400) as silent ok.
        series_config_early = controls.get("evaporation_series_resistance") or {}
        if not isinstance(series_config_early, Mapping):
            series_config_early = {}
        melt_resistance_enabled_early = bool(
            series_config_early.get(
                "melt_resistance_enabled",
                controls.get("melt_resistance_enabled", False),
            )
        )
        if melt_resistance_enabled_early:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "reason": "uncertified_melt_resistance_model",
                    "detail": (
                        "requires species- and state-specific D_i, k_L,i, "
                        "and dp_eq/dC_i"
                    ),
                },
            )
        if T_K < 400:
            # Mirrors _calculate_evaporation: below 400 K, no significant
            # evaporation -- return an empty flux dict with ok status.
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="ok",
                control_audit=control_audit,
                diagnostic={"evaporation_flux_kg_hr": {}},
            )

        # VR-11: sole flux pressure input is the batch-consumer map.
        # Compatibility vapor_pressures_Pa is reporting-only and must never
        # drive HKL (DESIGN-REV5 §1.2 / §7.4). Key presence, not truthiness:
        # an explicit empty batch map is empty flux, not a legacy fallthrough.
        if "vapour_batch_flux_pressures_Pa" not in controls:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "reason": "missing_vapour_batch_flux_pressures_Pa",
                    "detail": (
                        "EVAPORATION_FLUX requires vapour_batch_flux_pressures_Pa; "
                        "legacy vapor_pressures_Pa is not a flux input"
                    ),
                },
            )
        _batch_flux_raw = controls.get("vapour_batch_flux_pressures_Pa")
        vapor_pressures = dict(_batch_flux_raw or {})
        overhead_partials = dict(controls.get("overhead_partials_Pa") or {})
        vapor_pressure_sources = dict(controls.get("vapor_pressures_source") or {})
        pressure_provenance_by_species = dict(
            controls.get("vapor_pressure_numerator_provenance") or {}
        )
        vapor_pressure_activities = dict(
            controls.get("vapor_pressure_activities")
            or controls.get("activities")
            or {}
        )
        pO2_bar = controls.get("pO2_bar")
        molar_masses_kg_mol = dict(controls.get("molar_mass_kg_mol") or {})
        stoich_by_species = dict(controls.get("stoich_by_species") or {})
        available_oxide_kg = dict(controls.get("available_oxide_kg") or {})

        for mapping_name, values in (
            ("vapour_batch_flux_pressures_Pa", vapor_pressures),
            ("overhead_partials_Pa", overhead_partials),
            ("molar_mass_kg_mol", molar_masses_kg_mol),
            ("available_oxide_kg", available_oxide_kg),
        ):
            if any(
                not is_declared_real_scalar(value, allow_numeric_str=True)
                for value in values.values()
            ):
                return IntentResult(
                    intent=ChemistryIntent.EVAPORATION_FLUX,
                    status="refused",
                    transition=None,
                    control_audit=control_audit,
                    diagnostic={"reason": f"invalid_{mapping_name}"},
                )
        for species, stoich in stoich_by_species.items():
            oxide_per_product_kg = (
                stoich.get("oxide_per_product_kg")
                if isinstance(stoich, Mapping)
                else None
            )
            if oxide_per_product_kg is not None and not is_declared_real_scalar(
                oxide_per_product_kg,
                allow_numeric_str=True,
            ):
                return IntentResult(
                    intent=ChemistryIntent.EVAPORATION_FLUX,
                    status="refused",
                    transition=None,
                    control_audit=control_audit,
                    diagnostic={
                        "reason": "invalid_stoich_by_species",
                        "species": str(species),
                    },
                )

        try:
            raw_melt_surface_area_m2 = controls.get("melt_surface_area_m2", 0.0)
            if not is_declared_real_scalar(
                raw_melt_surface_area_m2,
                allow_numeric_str=True,
            ):
                raise TypeError
            melt_surface_area_m2 = float(raw_melt_surface_area_m2)
        except (TypeError, ValueError):
            melt_surface_area_m2 = math.nan
        if not math.isfinite(melt_surface_area_m2) or melt_surface_area_m2 < 0.0:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "reason": "invalid_melt_surface_area_m2",
                    "value": repr(controls.get("melt_surface_area_m2")),
                },
            )
        _stir_control = controls.get("stir_factor", 0.0)
        if isinstance(_stir_control, Mapping):
            axial_stir_factor = _stir_control.get("axial", 0.0)
            radial_stir_factor = _stir_control.get("radial", 1.0)
        else:
            axial_stir_factor = _stir_control
            radial_stir_factor = 1.0
        try:
            axial_stir_factor = _validated_stir_factor(
                axial_stir_factor,
                axis="axial",
            )
            radial_stir_factor = _validated_stir_factor(
                radial_stir_factor,
                axis="radial",
            )
        except EvaporationFluxConfigurationError as exc:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "reason": "invalid_stir_factor",
                    "detail": str(exc),
                },
            )
        series_config = controls.get("evaporation_series_resistance") or {}
        if not isinstance(series_config, Mapping):
            series_config = {}
        pipe_diameter_m = _finite_float(controls.get("pipe_diameter_m"), 0.12)
        overhead_pressure_pa = _finite_float(
            controls.get("overhead_pressure_pa"),
            max(0.0, float(request.pressure_bar) * 100000.0),
        )
        gas_temperature_K = _finite_float(controls.get("gas_temperature_K"), T_K)
        carrier_gas = str(controls.get("carrier_gas") or "N2")
        cold_skull_envelope = controls.get("cold_skull_envelope")
        melt_resistance_enabled = bool(
            series_config.get(
                "melt_resistance_enabled",
                controls.get("melt_resistance_enabled", False),
            )
        )
        gas_resistance_enabled = bool(
            series_config.get(
                "gas_resistance_enabled",
                controls.get("gas_resistance_enabled", True),
            )
        )
        hkl_upper_bound_transport_species = frozenset(
            str(species)
            for species in (
                controls.get("hkl_upper_bound_transport_species", ()) or ()
            )
        )
        from simulator.condensation import (
            GAS_CONSTANT_J_MOL_K,
            _knudsen_number as _kn_eval,
        )

        alpha_by_species = _coerce_alpha_by_species(controls.get("alpha"))
        alpha_envelope_by_species = _coerce_alpha_envelope_by_species(
            controls.get("alpha_envelope")
        )
        allow_unmeasured_alpha_fallback = bool(
            controls.get("allow_unmeasured_alpha_fallback", False)
        )
        fallback_species_raw = controls.get(
            "unmeasured_alpha_fallback_species"
        )
        if fallback_species_raw is None:
            unmeasured_alpha_fallback_species_allowlist = None
        elif isinstance(fallback_species_raw, (str, bytes)):
            unmeasured_alpha_fallback_species_allowlist = {
                str(fallback_species_raw)
            }
        else:
            unmeasured_alpha_fallback_species_allowlist = {
                str(species) for species in fallback_species_raw
            }
        missing_alpha: dict[str, dict[str, float | str]] = {}
        active_gas_transport_species: list[str] = []
        if (
            gas_resistance_enabled
            and melt_surface_area_m2 > 0.0
            and pipe_diameter_m > 0.0
        ):
            for species, pressure in vapor_pressures.items():
                species = str(species)
                P_eq_Pa = float(pressure)
                if P_eq_Pa <= 0.0:
                    continue
                if species in hkl_upper_bound_transport_species:
                    continue
                # α=1 is the Hertz-Knudsen ceiling used only as a marked
                # screening bound; record the same missing_alpha payload.
                alpha_is_unmeasured = _alpha_is_unmeasured(
                    species, alpha_by_species
                )
                alpha_spec = alpha_by_species.get(
                    species,
                    alpha_by_species.get("*", _DEFAULT_EVAPORATION_ALPHA),
                )
                alpha, _alpha_evaluation = _evaluate_alpha_control(
                    species,
                    T_K,
                    alpha_spec,
                )
                if alpha <= 0.0:
                    continue
                P_bulk_Pa = float(overhead_partials.get(species, 0.0))
                if P_eq_Pa <= P_bulk_Pa:
                    continue
                delta_p_Pa = P_eq_Pa - P_bulk_Pa
                stoich = stoich_by_species.get(species) or {}
                if float(stoich.get("oxide_per_product_kg") or 0.0) <= 0.0:
                    continue
                if species in available_oxide_kg:
                    available_parent_kg = float(
                        available_oxide_kg.get(species, 0.0) or 0.0
                    )
                    if available_parent_kg <= 1.0e-12:
                        continue
                else:
                    available_parent_kg = 0.0
                M_kg_mol = molar_masses_kg_mol.get(species)
                if M_kg_mol is None or M_kg_mol <= 0.0:
                    molar_mass_g_mol = MOLAR_MASS.get(species)
                    if molar_mass_g_mol is None or molar_mass_g_mol <= 0.0:
                        continue
                    M_kg_mol = molar_mass_g_mol / 1000.0
                hkl_upper_bound_rate_kg_hr = (
                    alpha
                    * math.sqrt(
                        M_kg_mol
                        / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * T_K)
                    )
                    * delta_p_Pa
                    * melt_surface_area_m2
                    * 3600.0
                )
                if hkl_upper_bound_rate_kg_hr <= _NONTRIVIAL_FLUX_KG_HR:
                    continue
                if (
                    alpha_is_unmeasured
                    and not _unmeasured_alpha_fallback_permitted(
                        species,
                        allow_unmeasured_alpha_fallback=(
                            allow_unmeasured_alpha_fallback
                        ),
                        unmeasured_alpha_fallback_species_allowlist=(
                            unmeasured_alpha_fallback_species_allowlist
                        ),
                    )
                    and available_parent_kg > 1.0e-12
                ):
                    missing_alpha[species] = _missing_alpha_record(
                        P_eq_Pa=P_eq_Pa,
                        P_bulk_Pa=P_bulk_Pa,
                        baseline_alpha_1_rate_kg_hr=(
                            hkl_upper_bound_rate_kg_hr
                        ),
                    )
                active_gas_transport_species.append(species)
        melt_surface_renewal_raw = series_config.get(
            "melt_surface_renewal_base_kg_s_m2_pa",
            controls.get("melt_surface_renewal_base_kg_s_m2_pa"),
        )
        if melt_surface_renewal_raw is None:
            melt_surface_renewal_base = (
                DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA
            )
        else:
            melt_surface_renewal_base = _finite_float(
                melt_surface_renewal_raw,
                math.nan,
            )
        melt_surface_renewal_source = str(
            series_config.get(
                "melt_surface_renewal_source",
                controls.get(
                    "melt_surface_renewal_source",
                    DEFAULT_MELT_SURFACE_RENEWAL_SOURCE,
                ),
            )
        )
        from simulator.evaporation import (
            viscous_p_bulk_out_of_domain_diagnostic,
        )

        # When gas resistance is disabled, the executed model is the HKL-only
        # path and does not use viscous Poiseuille P_bulk. Batch carriers can
        # raise the diagnostic overhead pressure into transitional Kn without
        # making that deliberately disabled transport model authoritative.
        commanded_pressure_pa = _finite_float(
            controls.get("commanded_pressure_pa"),
            overhead_pressure_pa,
        )
        domain_refusal = None
        if gas_resistance_enabled and overhead_pressure_pa > 0.0:
            kn_domain = _kn_eval(
                overhead_pressure_pa,
                max(gas_temperature_K, 1.0),
                max(pipe_diameter_m, 0.0) or 0.12,
                carrier_gas=carrier_gas,
            )
            domain_refusal = viscous_p_bulk_out_of_domain_diagnostic(
                knudsen_number=kn_domain,
                overhead_pressure_pa=overhead_pressure_pa,
                commanded_pressure_pa=commanded_pressure_pa,
                pipe_diameter_m=pipe_diameter_m,
                gas_temperature_K=gas_temperature_K,
                carrier_gas=carrier_gas,
                affected_species=tuple(active_gas_transport_species),
            )
            if active_gas_transport_species and domain_refusal is not None:
                domain_diagnostic = dict(domain_refusal)
                _attach_missing_alpha_records(
                    domain_diagnostic,
                    missing_alpha,
                    stoich_by_species,
                )
                domain_warnings = [
                    "viscous_p_bulk_transport_out_of_domain: "
                    "evaporation flux not evaluated in transitional Kn",
                ]
                if missing_alpha:
                    domain_warnings.append(
                        "per-species evaporation refusal; retained condensed "
                        "because evaporation_alpha is missing: "
                        + ", ".join(sorted(missing_alpha))
                    )
                return IntentResult(
                    intent=ChemistryIntent.EVAPORATION_FLUX,
                    status="refused",
                    transition=None,
                    control_audit=control_audit,
                    diagnostic=domain_diagnostic,
                    warnings=tuple(domain_warnings),
                )

        flux_kg_hr: dict[str, float] = {}
        alpha_used_by_species: dict[str, float] = {}
        alpha_evaluations_by_species: dict[str, dict[str, Any]] = {}
        alpha_authority_status_by_species: dict[str, str] = {}
        flux_uncertainty_pct: dict[str, float] = {}
        series_flux_diagnostics: dict[
            str, dict[str, float | str | bool | None]
        ] = {}
        unmeasured_alpha_fallback_species: list[str] = []
        missing_molar_mass: dict[str, dict[str, float | str]] = {}
        missing_transport_parameters: dict[str, dict[str, float | str]] = {}
        computable_transport_species: set[str] = set()

        for species, P_eq_Pa in vapor_pressures.items():
            P_eq_Pa = float(P_eq_Pa)
            if P_eq_Pa <= 0:
                continue

            # Molar mass: prefer the per-species value the caller looked
            # up from vapor_pressures.yaml; fall back to the grounded global
            # MOLAR_MASS table. Never guess a molar mass for HK flux.
            M_kg_mol = molar_masses_kg_mol.get(species)
            if M_kg_mol is None or M_kg_mol <= 0.0:
                molar_mass_g_mol = MOLAR_MASS.get(species)
                if molar_mass_g_mol is None or molar_mass_g_mol <= 0.0:
                    missing_molar_mass[species] = {
                        "policy": "fail_loud_missing_molar_mass",
                        "data_file": "data/vapor_pressures.yaml",
                        "control": "molar_mass_kg_mol",
                        "P_eq_Pa": P_eq_Pa,
                    }
                    continue
                M_kg_mol = molar_mass_g_mol / 1000.0

            stoich = stoich_by_species.get(species) or {}
            oxide_per_product_kg = float(stoich.get("oxide_per_product_kg") or 0.0)
            if oxide_per_product_kg <= 0.0:
                # Caller didn't supply stoich for this species -- skip
                # rather than emit a flux we can't deplete. Matches the
                # legacy AccountingError surface (the caller raises
                # there); here we skip silently since the kernel-level
                # error surface is owned by the caller.
                continue

            uses_gas_resistance = (
                gas_resistance_enabled
                and species not in hkl_upper_bound_transport_species
            )
            # The reconstructed HKL upper bound is a free-evaporation boundary:
            # when gas resistance is disabled, viscous Poiseuille P_bulk is not
            # part of the executed model and must not suppress its pressure drive.
            P_bulk_Pa = (
                float(overhead_partials.get(species, 0.0))
                if uses_gas_resistance
                else 0.0
            )
            delta_p_Pa = max(0.0, P_eq_Pa - P_bulk_Pa)
            if delta_p_Pa <= 0:
                continue
            alpha_is_unmeasured = _alpha_is_unmeasured(
                species, alpha_by_species
            )
            alpha_fallback_permitted = _unmeasured_alpha_fallback_permitted(
                species,
                allow_unmeasured_alpha_fallback=(
                    allow_unmeasured_alpha_fallback
                ),
                unmeasured_alpha_fallback_species_allowlist=(
                    unmeasured_alpha_fallback_species_allowlist
                ),
            )
            alpha_spec = alpha_by_species.get(
                species,
                alpha_by_species.get("*", _DEFAULT_EVAPORATION_ALPHA),
            )
            alpha, alpha_evaluation = _evaluate_alpha_control(
                species,
                T_K,
                alpha_spec,
            )
            if alpha_evaluation:
                alpha_evaluations_by_species[species] = alpha_evaluation
            alpha_authority_status = alpha_evaluation.get(
                ALPHA_AUTHORITY_STATUS_FIELD
            )

            baseline_alpha_1 = _series_resistance_evaporation_flux_kg_m2_s(
                species=species,
                P_eq_pa=P_eq_Pa,
                P_bulk_pa=P_bulk_Pa,
                T_surface_K=T_K,
                molar_mass_kg_mol=M_kg_mol,
                alpha_i=1.0,
                knudsen_number=math.inf,
                pipe_diameter_m=pipe_diameter_m,
                overhead_pressure_pa=overhead_pressure_pa,
                axial_stir_factor=axial_stir_factor,
                radial_stir_factor=radial_stir_factor,
                cold_skull_envelope=cold_skull_envelope,
                carrier_gas=carrier_gas,
                T_gas_K=gas_temperature_K,
                gas_resistance_enabled=False,
                melt_resistance_enabled=False,
            )
            baseline_rate_kg_hr = (
                baseline_alpha_1.flux_kg_s_m2 * melt_surface_area_m2 * 3600.0
            )
            available_parent_kg = float(available_oxide_kg.get(species, 0.0) or 0.0)
            if (
                domain_refusal is not None
                and species in available_oxide_kg
                and available_parent_kg <= 1.0e-12
            ):
                continue
            if (
                alpha_is_unmeasured
                and not alpha_fallback_permitted
                and available_parent_kg > 1.0e-12
                and baseline_rate_kg_hr > _NONTRIVIAL_FLUX_KG_HR
            ):
                missing_alpha[species] = _missing_alpha_record(
                    P_eq_Pa=P_eq_Pa,
                    P_bulk_Pa=P_bulk_Pa,
                    baseline_alpha_1_rate_kg_hr=baseline_rate_kg_hr,
                )
                continue

            if alpha_is_unmeasured and alpha_fallback_permitted:
                unmeasured_alpha_fallback_species.append(species)

            alpha_used_by_species[species] = alpha
            uncertainty_pct = _flux_uncertainty_pct(
                alpha,
                alpha_envelope_by_species.get(species),
            )
            if uncertainty_pct is not None:
                flux_uncertainty_pct[species] = uncertainty_pct

            try:
                series_flux = _series_resistance_evaporation_flux_kg_m2_s(
                    species=species,
                    P_eq_pa=P_eq_Pa,
                    P_bulk_pa=P_bulk_Pa,
                    T_surface_K=T_K,
                    molar_mass_kg_mol=M_kg_mol,
                    alpha_i=alpha,
                    pipe_diameter_m=pipe_diameter_m,
                    overhead_pressure_pa=overhead_pressure_pa,
                    axial_stir_factor=axial_stir_factor,
                    radial_stir_factor=radial_stir_factor,
                    cold_skull_envelope=cold_skull_envelope,
                    carrier_gas=carrier_gas,
                    T_gas_K=gas_temperature_K,
                    melt_resistance_enabled=melt_resistance_enabled,
                    gas_resistance_enabled=uses_gas_resistance,
                    melt_surface_renewal_base_kg_s_m2_pa=melt_surface_renewal_base,
                    melt_surface_renewal_source=melt_surface_renewal_source,
                )
            except EvaporationFluxConfigurationError as exc:
                missing_transport_parameters[species] = {
                    "policy": "fail_loud_missing_transport_parameters",
                    "carrier_gas": carrier_gas,
                    "reason": str(exc),
                }
                continue
            computable_transport_species.add(species)
            J_kg_s_m2 = series_flux.flux_kg_s_m2

            series_diagnostic = series_flux.as_diagnostic()
            if alpha_authority_status == ANALYTICAL_UPPER_BOUND_ALPHA_STATUS:
                series_diagnostic[ALPHA_AUTHORITY_STATUS_FIELD] = (
                    alpha_authority_status
                )
            if species in hkl_upper_bound_transport_species:
                series_diagnostic["gas_transport_model"] = (
                    "hkl_kinetic_upper_bound_no_chapman_enskog"
                )
                series_diagnostic["authority_status"] = (
                    "analytical_upper_bound_status_bearing_cannot_certify"
                )
            series_diagnostic.update(
                _series_pressure_provenance_diagnostic(
                    species=species,
                    P_eq_Pa=P_eq_Pa,
                    P_bulk_Pa=P_bulk_Pa,
                    pressure_provenance_by_species=(
                        pressure_provenance_by_species
                    ),
                    vapor_pressure_sources=vapor_pressure_sources,
                    vapor_pressure_activities=vapor_pressure_activities,
                    pO2_bar=pO2_bar,
                )
            )
            series_flux_diagnostics[species] = series_diagnostic
            if J_kg_s_m2 <= 0:
                continue

            rate_kg_hr = J_kg_s_m2 * melt_surface_area_m2 * 3600.0

            if rate_kg_hr > _NONTRIVIAL_FLUX_KG_HR:
                if (
                    alpha_authority_status
                    == ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
                ):
                    alpha_authority_status_by_species[species] = (
                        alpha_authority_status
                    )
                flux_kg_hr[species] = rate_kg_hr

        if missing_transport_parameters and not computable_transport_species:
            return IntentResult(
                intent=ChemistryIntent.EVAPORATION_FLUX,
                status="unavailable",
                transition=None,
                control_audit=control_audit,
                diagnostic={
                    "evaporation_flux_kg_hr": {},
                    "missing_transport_parameters": missing_transport_parameters,
                    "temperature_C": T_C,
                    "evaporation_series_resistance": series_flux_diagnostics,
                },
                warnings=(
                    "missing Chapman-Enskog transport parameters for sampled species: "
                    + ", ".join(sorted(missing_transport_parameters)),
                ),
            )

        diagnostic = {
            "evaporation_flux_kg_hr": flux_kg_hr,
            "alpha_used_by_species": alpha_used_by_species,
            "alpha_s_evaluation_by_species": alpha_evaluations_by_species,
            "flux_uncertainty_pct": flux_uncertainty_pct,
            "evaporation_series_resistance": series_flux_diagnostics,
            "temperature_C": T_C,
            # Matrix policy (i): source flux is HKL upper bound while R_m=0.
            "authority_class": "upper-bound",
            "authority_reason": (
                "missing-species-state-dependent-melt-transfer-inputs"
            ),
        }
        if alpha_authority_status_by_species:
            diagnostic["alpha_authority_status_by_species"] = (
                alpha_authority_status_by_species
            )
        _attach_missing_alpha_records(
            diagnostic, missing_alpha, stoich_by_species
        )
        if unmeasured_alpha_fallback_species:
            diagnostic["unmeasured_alpha_fallback_species"] = sorted(
                unmeasured_alpha_fallback_species
            )
        warning_messages: list[str] = []
        if missing_alpha:
            warning_messages.append(
                "per-species evaporation refusal; retained condensed because "
                "evaporation_alpha is missing: "
                + ", ".join(sorted(missing_alpha))
            )
        if unmeasured_alpha_fallback_species:
            warning_messages.append(
                "WARNING: alpha=1.0 prototype fallback used for unmeasured "
                "evaporation species: "
                + ", ".join(sorted(unmeasured_alpha_fallback_species))
            )
        if missing_transport_parameters:
            diagnostic["missing_transport_parameters"] = missing_transport_parameters
            warning_messages.append(
                "excluded evaporation species with missing Chapman-Enskog "
                "transport parameters: "
                + ", ".join(sorted(missing_transport_parameters))
            )
        if missing_molar_mass:
            diagnostic["missing_molar_mass"] = missing_molar_mass
            warning_messages.append(
                "missing molar_mass_g_mol for evaporation species in "
                "data/vapor_pressures.yaml: "
                + ", ".join(sorted(missing_molar_mass))
            )

        return IntentResult(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            status="ok",
            transition=None,
            control_audit=control_audit,
            diagnostic=diagnostic,
            warnings=tuple(warning_messages),
        )
