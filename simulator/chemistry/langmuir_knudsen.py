"""Formal Langmuir / Knudsen analytical flux model.

KEMS is the un-enhanced baseline: ballistic gas transport (Kn >> 1, ``r_gas -> 0``)
and zero induction stirring (``stir=0``, no melt-side renewal). Equilibrium-mode
KEMS measures the thermodynamic driving force ``p_eq``; free-evaporation (Langmuir)
measurements pin the kinetic coefficient ``alpha`` at that same surface.

The furnace model layers validated transport effects on top of this baseline:

* overhead ``pN2`` adds continuum gas-side resistance ``r_gas`` through
  Chapman-Enskog / Sherwood mass transfer when ``Kn < 0.01``; outside that
  regime chamber transmission is represented by the evolved ``p_bulk``, and
* radial stirring can enhance continuum Sherwood transport. The melt term stays
  disabled until species/composition-dependent liquid-transfer inputs exist.

This module exposes the two limits and the three-resistance transition as clean
analytical functions. The series physics is delegated to
:func:`engines.builtin.evaporation_flux._series_resistance_evaporation_flux_kg_m2_s`
— no constants are re-derived here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from engines.builtin.evaporation_flux import (
    SeriesEvaporationFlux,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from engines.builtin.vapor_pressure import (
    FIT_TARGET_STANDARD_REACTION,
    vapor_pressure_antoine_coefficients,
)
from simulator.condensation import GAS_CONSTANT_J_MOL_K, alpha_s
from simulator.evaporation import _load_evaporation_alpha_by_species
_VAPOR_PRESSURE_GROUPS = ("metals", "oxide_vapors")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VAPOR_PRESSURES_PATH = _REPO_ROOT / "data" / "vapor_pressures.yaml"
_VALIDATION_SIDECAR_PATH = (
    _REPO_ROOT / "data/literature/langmuir_knudsen_flux_validation.yaml"
)


@dataclass(frozen=True)
class LangmuirKnudsenDiagnostics:
    """Series flux with explicit limit diagnostics."""

    series: SeriesEvaporationFlux
    langmuir_flux_kg_s_m2: float
    knudsen_effusion_flux_kg_s_m2: float

    @property
    def flux_kg_s_m2(self) -> float:
        return self.series.flux_kg_s_m2

    @property
    def knudsen_number(self) -> float:
        return self.series.knudsen_number

    @property
    def r_interface(self) -> float:
        return self.series.r_interface

    @property
    def r_gas(self) -> float:
        return self.series.r_gas

    @property
    def r_melt(self) -> float:
        return self.series.r_melt

    @property
    def gas_resistance_weight(self) -> float:
        return self.series.gas_resistance_weight

    def as_dict(self) -> dict[str, float]:
        return {
            "flux_kg_s_m2": self.flux_kg_s_m2,
            "langmuir_flux_kg_s_m2": self.langmuir_flux_kg_s_m2,
            "knudsen_effusion_flux_kg_s_m2": self.knudsen_effusion_flux_kg_s_m2,
            "knudsen_number": self.knudsen_number,
            "Kn": self.knudsen_number,
            "r_interface": self.r_interface,
            "r_gas": self.r_gas,
            "r_melt": self.r_melt,
            "gas_resistance_weight": self.gas_resistance_weight,
            "k_hk_kg_s_m2_pa": self.series.k_hk_kg_s_m2_pa,
            "alpha_intrinsic": self.series.alpha_intrinsic,
            "alpha_effective": self.series.alpha_effective,
        }


def hertz_knudsen_k_kg_s_m2_pa(T_surface_K: float, molar_mass_kg_mol: float) -> float:
    """Hertz-Knudsen kinetic coefficient ``k_HK`` in kg/(m^2*Pa*s).

    Premise: multiply molar Langmuir flux by molar mass. Algebra:
    ``M / sqrt(2*pi*M*R*T) = sqrt(M/(2*pi*R*T))``. Unit check: the
    coefficient has units ``s/m``, equivalent to ``kg/(m^2*Pa*s)`` because
    ``Pa = kg/(m*s^2)``. Limiting case: multiplying by ``alpha * delta_p``
    recovers the authoritative builtin mass-flux expression exactly.
    """
    return math.sqrt(
        molar_mass_kg_mol
        / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * T_surface_K)
    )


def knudsen_effusion_molar_flux(
    T_surface_K: float,
    p_eq_pa: float,
    *,
    molar_mass_kg_mol: float,
    p_bulk_pa: float = 0.0,
) -> float:
    """Ideal equilibrium-effusion flux in mol/(m^2*s).

    Premise: equilibrium Maxwellian vapor reaches a unit-area ideal Knudsen
    orifice. Algebra: ``n*v_bar/4`` with ``n=p/(R*T)`` reduces to
    ``J_K=(p_eq-p_bulk)/sqrt(2*pi*M*R*T)``. Unit check:
    ``Pa/sqrt((kg/mol)*(J/mol)) = mol/(m^2*s)``. Limiting case: an ideal
    Clausing factor of one and ``p_bulk -> 0`` gives the equilibrium KEMS
    reference flux; apparatus-specific orifice corrections belong to the
    measurement reduction, not this surface-flux model.
    """
    delta_p = max(0.0, float(p_eq_pa) - float(p_bulk_pa))
    denominator = math.sqrt(
        2.0
        * math.pi
        * molar_mass_kg_mol
        * GAS_CONSTANT_J_MOL_K
        * T_surface_K
    )
    return delta_p / denominator


def langmuir_molar_flux(
    T_surface_K: float,
    p_eq_pa: float,
    p_bulk_pa: float,
    alpha: float,
    *,
    molar_mass_kg_mol: float,
) -> float:
    """Free-evaporation flux in mol/(m^2*s).

    Premise: only fraction ``alpha`` of equilibrium surface crossings escapes.
    Algebra: ``J_L=alpha*J_K`` gives the requested
    ``alpha*(p_eq-p_bulk)/sqrt(2*pi*M*R*T)``. Units remain mol/(m^2*s).
    Limiting cases: ``alpha -> 1`` recovers equilibrium effusion and
    ``alpha -> 0`` stops free evaporation.
    """
    if alpha <= 0.0:
        return 0.0
    return float(alpha) * knudsen_effusion_molar_flux(
        T_surface_K,
        p_eq_pa,
        molar_mass_kg_mol=molar_mass_kg_mol,
        p_bulk_pa=p_bulk_pa,
    )


def langmuir_flux(
    species: str,
    T_surface_K: float,
    p_eq_pa: float,
    p_bulk_pa: float,
    alpha: float,
    *,
    molar_mass_kg_mol: float,
) -> float:
    """Free-evaporation (Langmuir) surface flux in kg/(m^2*s).

    ``J = alpha * max(0, p_eq - p_bulk) * sqrt(M / (2*pi*R*T))``

    This is the KEMS un-enhanced baseline: ballistic gas (``r_gas -> 0``) with
    ``stir=0`` melt renewal off. The evaporation coefficient ``alpha`` is the
    kinetic pin measured in open-sweep / mass-loss experiments.
    """
    _ = species  # species label retained for call-site readability / logging
    molar_flux = langmuir_molar_flux(
        T_surface_K,
        p_eq_pa,
        p_bulk_pa,
        alpha,
        molar_mass_kg_mol=molar_mass_kg_mol,
    )
    return molar_flux * molar_mass_kg_mol


def knudsen_effusion_flux(
    species: str,
    T_surface_K: float,
    p_eq_pa: float,
    *,
    molar_mass_kg_mol: float,
    p_bulk_pa: float = 0.0,
    alpha: float = 1.0,
) -> float:
    """Equilibrium-effusion limit flux in kg/(m^2*s).

    A Knudsen-effusion mass-spectrometry (KEMS) cell holds vapor near ``p_eq``
    and effuses through a small orifice in free-molecular flow. With the
    intrinsic coefficient at unity this reduces to ``J = p_eq * k_HK`` — KEMS
    measures ``p_eq`` (thermodynamics). Free-evaporation (Langmuir) measurements
    at the same surface instead report ``alpha * p_eq`` (kinetics).
    """
    _ = species
    molar_flux = knudsen_effusion_molar_flux(
        T_surface_K,
        p_eq_pa,
        molar_mass_kg_mol=molar_mass_kg_mol,
        p_bulk_pa=p_bulk_pa,
    )
    return float(alpha) * molar_flux * molar_mass_kg_mol


def series_flux(
    species: str,
    p_eq_pa: float,
    p_bulk_pa: float,
    T_surface_K: float,
    molar_mass_kg_mol: float,
    alpha: float,
    *,
    knudsen_number: float | None = None,
    pipe_diameter_m: float = 0.12,
    overhead_pressure_pa: float = 0.0,
    axial_stir_factor: float = 0.0,
    radial_stir_factor: float = 1.0,
    carrier_gas: str = "N2",
    T_gas_K: float | None = None,
    melt_resistance_enabled: bool = False,
    gas_resistance_enabled: bool = True,
) -> LangmuirKnudsenDiagnostics:
    """Full three-resistance flux with explicit limit diagnostics.

    Premise: interface, gas-film, and melt-renewal conductances can act in
    series only when each is expressed against the same pressure potential.
    Algebra stays delegated to the authoritative helper: ``J=delta_p /
    (r_interface+r_gas+r_melt)``.
    Every resistance has units ``m^2*Pa*s/kg``, so the result is kg/(m^2*s).
    Limiting cases: free-molecular vacuum makes ``r_gas`` vanish, reducing to
    :func:`langmuir_flux` while the ungrounded melt term is disabled;
    ``Kn < 0.01`` enables the continuum gas film.
    """
    series = _series_resistance_evaporation_flux_kg_m2_s(
        species=species,
        P_eq_pa=p_eq_pa,
        P_bulk_pa=p_bulk_pa,
        T_surface_K=T_surface_K,
        molar_mass_kg_mol=molar_mass_kg_mol,
        alpha_i=alpha,
        knudsen_number=knudsen_number,
        pipe_diameter_m=pipe_diameter_m,
        overhead_pressure_pa=overhead_pressure_pa,
        axial_stir_factor=axial_stir_factor,
        radial_stir_factor=radial_stir_factor,
        carrier_gas=carrier_gas,
        T_gas_K=T_gas_K,
        melt_resistance_enabled=melt_resistance_enabled,
        gas_resistance_enabled=gas_resistance_enabled,
    )
    langmuir = langmuir_flux(
        species,
        T_surface_K,
        p_eq_pa,
        p_bulk_pa,
        alpha,
        molar_mass_kg_mol=molar_mass_kg_mol,
    )
    knudsen = knudsen_effusion_flux(
        species,
        T_surface_K,
        p_eq_pa,
        molar_mass_kg_mol=molar_mass_kg_mol,
        p_bulk_pa=p_bulk_pa,
        alpha=1.0,
    )
    return LangmuirKnudsenDiagnostics(
        series=series,
        langmuir_flux_kg_s_m2=langmuir,
        knudsen_effusion_flux_kg_s_m2=knudsen,
    )


@lru_cache(maxsize=1)
def _vapor_pressure_data() -> dict[str, Any]:
    payload = yaml.safe_load(_VAPOR_PRESSURES_PATH.read_text()) or {}
    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    return vapor_pressure_legacy_view(payload)


def _species_row(species: str) -> dict[str, Any]:
    data = _vapor_pressure_data()
    for group_name in _VAPOR_PRESSURE_GROUPS:
        row = (data.get(group_name) or {}).get(species)
        if isinstance(row, dict):
            return row
    raise KeyError(f"no vapor_pressures.yaml row for species {species!r}")


def species_molar_mass_kg_mol(species: str) -> float:
    row = _species_row(species)
    molar_mass_g_mol = float(row["molar_mass_g_mol"])
    return molar_mass_g_mol / 1000.0


def pseudo_antoine_p_eq_pa(species: str, T_K: float) -> float:
    """Evaluate the builtin pseudo-Antoine ``p_eq`` row used by the provider."""
    row = _species_row(species)
    if str(row.get("fit_target", "") or "") == FIT_TARGET_STANDARD_REACTION:
        raise ValueError(
            f"{species} uses a standard_reaction_term; raw pseudo-Antoine "
            "evaluation would omit melt activity and pO2 context. Use the "
            "builtin vapor-pressure provider for an effective P_eq."
        )
    antoine, _ = vapor_pressure_antoine_coefficients(row, temperature_K=T_K)
    A = float(antoine.get("A", 0.0))
    B = float(antoine.get("B", 0.0))
    C = float(antoine.get("C", 0.0))
    if A <= 0.0 or T_K <= 0.0:
        return 0.0
    log10_p = A - B / (T_K + C)
    return 10.0 ** log10_p


def grounded_alpha(species: str, T_K: float) -> tuple[float, dict[str, Any]]:
    """Return YAML-backed alpha using the same ``alpha_s`` path as the provider."""
    alpha_by_species = _load_evaporation_alpha_by_species(_vapor_pressure_data())
    alpha_spec = alpha_by_species[species]
    context: dict[str, Any] = {"coefficient_spec": alpha_spec}
    value = alpha_s(species, T_K, context)
    return value, dict(context.get("alpha_s_evaluation", {}))


@dataclass(frozen=True)
class BaselineValidationRow:
    measurement_id: str
    species: str
    regime: str
    T_K: float
    modeled_langmuir_to_effusion_ratio: float
    measured_ratio_center: float
    measured_ratio_low: float
    measured_ratio_high: float
    relative_error_percent: float
    allowed_error_percent: float
    source: str

    def as_dict(self) -> dict[str, float | str]:
        return {
            "measurement_id": self.measurement_id,
            "species": self.species,
            "regime": self.regime,
            "T_K": self.T_K,
            "modeled_langmuir_to_effusion_ratio": (
                self.modeled_langmuir_to_effusion_ratio
            ),
            "measured_ratio_center": self.measured_ratio_center,
            "measured_ratio_low": self.measured_ratio_low,
            "measured_ratio_high": self.measured_ratio_high,
            "relative_error_percent": self.relative_error_percent,
            "allowed_error_percent": self.allowed_error_percent,
            "source": self.source,
        }


def _baseline_validation_rows() -> list[dict[str, Any]]:
    payload = yaml.safe_load(_VALIDATION_SIDECAR_PATH.read_text()) or {}
    measurements = payload.get("measurements") or {}
    return [
        dict(row, measurement_id=measurement_id)
        for measurement_id, row in measurements.items()
    ]


def validate_against_baseline(
    *,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[BaselineValidationRow]:
    """Compare the ballistic model to measured Langmuir/effusion flux ratios.

    The normalized ratio is the measured evaporation coefficient. It preserves
    direct Langmuir-versus-Knudsen evidence without inventing missing aperture
    geometry or hardcoding model-generated absolute flux. Runtime alpha comes
    independently from ``vapor_pressures.yaml``; observations and their stated
    error bars come only from the literature sidecar. No coefficient is fitted.
    """
    validation_rows = (
        list(rows) if rows is not None else _baseline_validation_rows()
    )
    results: list[BaselineValidationRow] = []
    for entry in validation_rows:
        species = str(entry["species"])
        temperature_range = [
            float(value) for value in entry["temperature_range_k"]
        ]
        T_K = sum(temperature_range) / len(temperature_range)
        alpha, _ = grounded_alpha(species, T_K)
        molar_mass = species_molar_mass_kg_mol(species)
        equilibrium_effusion = knudsen_effusion_molar_flux(
            T_K,
            1.0,
            molar_mass_kg_mol=molar_mass,
        )
        free_evaporation = langmuir_molar_flux(
            T_K,
            1.0,
            0.0,
            alpha,
            molar_mass_kg_mol=molar_mass,
        )
        modeled = free_evaporation / equilibrium_effusion
        measured = entry["measured_langmuir_to_effusion_flux_ratio"]
        if "range" in measured:
            low, high = (float(value) for value in measured["range"])
            center = (low + high) / 2.0
        else:
            center = float(measured["value"])
            uncertainty = float(measured["uncertainty_absolute"])
            low, high = center - uncertainty, center + uncertainty
        relative_error = (modeled - center) / center * 100.0
        allowed_error = max(center - low, high - center) / center * 100.0
        source = entry.get("source") or {}
        results.append(
            BaselineValidationRow(
                measurement_id=str(entry.get("measurement_id") or ""),
                species=species,
                regime=str(entry["regime"]),
                T_K=T_K,
                modeled_langmuir_to_effusion_ratio=modeled,
                measured_ratio_center=center,
                measured_ratio_low=low,
                measured_ratio_high=high,
                relative_error_percent=relative_error,
                allowed_error_percent=allowed_error,
                source=str(source.get("citation") or ""),
            )
        )
    return results


__all__ = (
    "BaselineValidationRow",
    "LangmuirKnudsenDiagnostics",
    "grounded_alpha",
    "hertz_knudsen_k_kg_s_m2_pa",
    "knudsen_effusion_flux",
    "knudsen_effusion_molar_flux",
    "langmuir_flux",
    "langmuir_molar_flux",
    "pseudo_antoine_p_eq_pa",
    "series_flux",
    "species_molar_mass_kg_mol",
    "validate_against_baseline",
)
