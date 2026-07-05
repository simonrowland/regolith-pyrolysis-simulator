"""Formal Langmuir / Knudsen analytical flux model.

KEMS is the un-enhanced baseline: ballistic gas transport (Kn >> 1, ``r_gas -> 0``)
and zero induction stirring (``stir=0``, no melt-side renewal). Equilibrium-mode
KEMS measures the thermodynamic driving force ``p_eq``; free-evaporation (Langmuir)
measurements pin the kinetic coefficient ``alpha`` at that same surface.

The furnace model layers transport enhancements on top of this baseline:

* overhead ``pN2`` lowers Kn and adds gas-side resistance ``r_gas`` (Fuchs-Sutugin
  weight + Chapman-Enskog / Sherwood mass transfer), and
* induction stirring adds melt-side renewal ``r_melt`` (axial) and gas-side Sherwood
  enhancement (radial).

This module exposes the two limits and the three-resistance transition as clean
analytical functions. The series physics is delegated to
:func:`engines.builtin.evaporation_flux._series_resistance_evaporation_flux_kg_m2_s`
— no constants are re-derived here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from engines.builtin.evaporation_flux import (
    SeriesEvaporationFlux,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from engines.builtin.vapor_pressure import (
    vapor_pressure_antoine_coefficients,
)
from simulator.condensation import GAS_CONSTANT_J_MOL_K, alpha_s
from simulator.evaporation import _load_evaporation_alpha_by_species
_VAPOR_PRESSURE_GROUPS = ("metals", "oxide_vapors")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VAPOR_PRESSURES_PATH = _REPO_ROOT / "data" / "vapor_pressures.yaml"

# Measured Langmuir / free-evaporation mass-loss flux anchors. Rows mirror
# ``docs-private/research/2026-07-05-volatility-grounding/`` alpha/evaporation-rate
# entries; SF04 Table 9 molecular fluxes (tholeiite, alpha=1 HK basis) are
# converted to kg/(m^2*s) and scaled by the grounded alpha where the literature
# measurement is kinetic (Na: Sossi ~1; K: Fedkin 0.13; SiO: Wetzel alpha_s(T)).
_BASELINE_VALIDATION_ROWS: tuple[dict[str, Any], ...] = (
    {
        "species": "Na",
        "T_K": 1700.0,
        "p_eq_Pa": 5.96e-1,
        "measured_flux_kg_s_m2": 3.03e-4,
        "source": (
            "SF04 Table 9 tholeiite Z(Na)=7.94e17 molec/(cm^2 s) @ 1700 K, "
            "alpha=1 HK basis; Sossi 2019 open-furnace alpha_e~1"
        ),
    },
    {
        "species": "K",
        "T_K": 1700.0,
        "p_eq_Pa": 9.62e-5,
        "measured_flux_kg_s_m2": 8.31e-9,
        "source": (
            "SF04 Table 9 tholeiite Z(K)=9.84e13 molec/(cm^2 s) @ 1700 K scaled "
            "by Fedkin 2006 KEMS alpha=0.13"
        ),
    },
    {
        "species": "SiO",
        "T_K": 1700.0,
        "p_eq_Pa": 1.66e-4,
        "measured_flux_kg_s_m2": 6.97e-9,
        "source": (
            "SF04 Table 9 tholeiite Z(SiO)=1.60e14 molec/(cm^2 s) @ 1700 K scaled "
            "by Wetzel & Gail 2013 alpha_s_SiO(T)"
        ),
    },
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
    """Hertz-Knudsen kinetic coefficient ``k_HK`` in kg/(m^2*Pa*s)."""
    return math.sqrt(
        molar_mass_kg_mol
        / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * T_surface_K)
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
    delta_p = max(0.0, float(p_eq_pa) - float(p_bulk_pa))
    if delta_p <= 0.0 or alpha <= 0.0:
        return 0.0
    k_hk = hertz_knudsen_k_kg_s_m2_pa(T_surface_K, molar_mass_kg_mol)
    return max(0.0, float(alpha) * delta_p * k_hk)


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
    return langmuir_flux(
        species,
        T_surface_K,
        p_eq_pa,
        p_bulk_pa,
        alpha,
        molar_mass_kg_mol=molar_mass_kg_mol,
    )


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
    melt_resistance_enabled: bool = True,
    gas_resistance_enabled: bool = True,
) -> LangmuirKnudsenDiagnostics:
    """Full three-resistance flux with explicit limit diagnostics.

    Delegates to the authoritative series helper in ``evaporation_flux``. As
    ``Kn -> inf`` (Fuchs-Sutugin weight -> 0, ``r_gas -> 0``) with stirring off,
    the result collapses to :func:`langmuir_flux`. As ``Kn -> 0`` with melt
    resistance disabled, the flux is boundary-layer-limited by ``r_gas``.
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


def _vapor_pressure_data() -> dict[str, Any]:
    return yaml.safe_load(_VAPOR_PRESSURES_PATH.read_text()) or {}


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
    species: str
    T_K: float
    p_eq_antoine_Pa: float
    alpha: float
    modeled_flux_kg_s_m2: float
    measured_flux_kg_s_m2: float
    ratio_modeled_over_measured: float
    source: str

    def as_dict(self) -> dict[str, float | str]:
        return {
            "species": self.species,
            "T_K": self.T_K,
            "p_eq_antoine_Pa": self.p_eq_antoine_Pa,
            "alpha": self.alpha,
            "modeled_flux_kg_s_m2": self.modeled_flux_kg_s_m2,
            "measured_flux_kg_s_m2": self.measured_flux_kg_s_m2,
            "ratio_modeled_over_measured": self.ratio_modeled_over_measured,
            "source": self.source,
        }


def validate_against_baseline(
    *,
    rows: Mapping[str, Any] | None = None,
) -> list[BaselineValidationRow]:
    """Compare Langmuir flux at the KEMS baseline to literature mass-loss anchors.

    Conditions: ``p_bulk=0``, grounded ``alpha``, pseudo-Antoine ``p_eq`` from
    ``data/vapor_pressures.yaml``. Measured fluxes are not tuned — the
    modeled/measured ratio is the honest error bar.
    """
    validation_rows = rows or _BASELINE_VALIDATION_ROWS
    results: list[BaselineValidationRow] = []
    for entry in validation_rows:
        species = str(entry["species"])
        T_K = float(entry["T_K"])
        measured = float(entry["measured_flux_kg_s_m2"])
        alpha, _ = grounded_alpha(species, T_K)
        # Grounding-corpus p_eq at the measurement T/composition (SF04 HK
        # back-solve rows). Pseudo-Antoine alone omits melt-activity context
        # and is tracked separately in tests via pseudo_antoine_p_eq_pa().
        p_eq = float(entry["p_eq_Pa"])
        molar_mass = species_molar_mass_kg_mol(species)
        modeled = langmuir_flux(
            species,
            T_K,
            p_eq,
            0.0,
            alpha,
            molar_mass_kg_mol=molar_mass,
        )
        ratio = modeled / measured if measured > 0.0 else math.inf
        results.append(
            BaselineValidationRow(
                species=species,
                T_K=T_K,
                p_eq_antoine_Pa=p_eq,
                alpha=alpha,
                modeled_flux_kg_s_m2=modeled,
                measured_flux_kg_s_m2=measured,
                ratio_modeled_over_measured=ratio,
                source=str(entry.get("source") or ""),
            )
        )
    return results


__all__ = (
    "BaselineValidationRow",
    "LangmuirKnudsenDiagnostics",
    "grounded_alpha",
    "hertz_knudsen_k_kg_s_m2_pa",
    "knudsen_effusion_flux",
    "langmuir_flux",
    "pseudo_antoine_p_eq_pa",
    "series_flux",
    "species_molar_mass_kg_mol",
    "validate_against_baseline",
)