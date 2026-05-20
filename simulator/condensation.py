"""
Condensation Train Model
=========================

★ TIER 2: SCIENTIST-READABLE ★

Models vapor flow through the 8-stage metals condensation train.
Each stage operates at a fixed temperature range and preferentially
collects species whose condensation temperature falls within that range.

Train topology (metals train, active C2A onward):
    Stage 0  Hot duct (>1400°C)      — IR spectroscopy, no condensation
    Stage 1  Fe condenser (1100-1400°C) — liquid Fe drains to sump
    Stage 2  Cr oxide harvester (1100-1300°C) — Cr2O3 product cartridge
    Stage 3  SiO zone (900-1200°C)   — fused silica on removable baffles
    Stage 4  Alkali/Mg cyclone (350-700°C) — Na/K/Mg condensation
    Stage 5  Vortex dust filter (200-350°C) — entrained particle capture
    Stage 6  Turbine-compressor      — pressure regulation, pO₂ control
    Stage 7  Turbine outlet monitor — terminal ledger owns O2 storage

A separate volatiles train handles C0/C0b products and is sealed
by a gate valve after devolatilisation.

Key physics:
    Condensation efficiency per species per stage:             [COND-1]
        η = 1 - exp(-t_res / τ_cond)
    where:
        t_res   = residence time in the stage (s)
        τ_cond  = characteristic condensation time (s)
              = f(T_stage, T_cond_species, surface_area, α_stick)

    If T_stage << T_condense → τ_cond is very small → η → 1
    If T_stage ≈ T_condense → τ_cond is large → η → 0

The Fe → SiO separation (Stage 1 → Stage 2):
    Stage 1 at 1200-1400°C: Fe condenses as liquid, SiO passes through
    (SiO condensation T is 900-1200°C, below Stage 1 operating T).
    Chevron separator at Stage 1 exit catches entrained Fe droplets.
    Sharp T boundary (radiation gap) prevents early SiO condensation.
    Impurity: ~0.1-1% Fe passes to Stage 2; ~0.5-2% SiO condenses in Stage 1.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import yaml

from simulator.core import (
    CondensationTrain, CondensationStage, EvaporationFlux, MeltState,
)
from simulator.state import MOLAR_MASS


BOLTZMANN_CONSTANT_J_K = 1.380649e-23
AVOGADRO_MOL = 6.02214076e23
REGIME_FACTOR_KN_PLACEHOLDER = 1.0
HKL_BAND_SAMPLES = 33
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'


# Condensation temperatures at ~1 mbar partial pressure (°C)
# Used to determine where each species preferentially deposits.
CONDENSATION_TEMPS_C = {
    'Fe':  1250,
    'SiO': 1050,   # condenses as amorphous SiO₂ (disproportionation)
    'CrO2': 1250,  # condenses as Cr2O3 + O2 in the dedicated Cr stage
    'Mg':  580,
    'Na':  480,
    'K':   420,
    'Ca':  780,
    'Mn':  1000,
    'Cr':  1280,
    'Al':  1180,   # negligible at process T, but included for completeness
    'Ti':  1500,   # negligible at process T
}

# Sticking coefficients (probability of condensation on contact)
STICKING_COEFF = {
    'Fe':  0.9,
    'SiO': 0.7,    # SiO → SiO₂ disproportionation is not instantaneous
    'CrO2': 0.9,
    'Mg':  0.8,
    'Na':  0.95,
    'K':   0.95,
    'Ca':  0.85,
    'Mn':  0.85,
    'Cr':  0.9,
}


def _load_yaml_data(filename: str) -> Dict[str, Any]:
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with path.open('r', encoding='utf-8') as f:
        loaded = yaml.safe_load(f) or {}
    return loaded if isinstance(loaded, dict) else {}


VAPOR_PRESSURE_DATA = _load_yaml_data('vapor_pressures.yaml')
MATERIALS_DATA = _load_yaml_data('materials.yaml')


@dataclass(frozen=True)
class CondensationRouteResult:
    """Per-hour routing plan; quantities are projections until ledger credit."""

    remaining_by_species: Dict[str, float] = field(default_factory=dict)
    condensed_by_stage_species: Dict[int, Dict[str, float]] = field(default_factory=dict)

    def condensed_for_species(self, species: str) -> float:
        return sum(
            stage_species.get(species, 0.0)
            for stage_species in self.condensed_by_stage_species.values()
        )

    def silica_fume_fraction_of_feedstock(self, feedstock_kg: float) -> float:
        """Return SiO-derived SiO2 condensate mass divided by feedstock mass."""

        if feedstock_kg <= 0.0:
            return 0.0
        sio_condensed_kg = self.condensed_for_species('SiO')
        sio_to_sio2 = 0.5 * MOLAR_MASS['SiO2'] / MOLAR_MASS['SiO']
        return (sio_condensed_kg * sio_to_sio2) / feedstock_kg


class CondensationModel:
    """
    Routes evaporated species through the condensation train.

    For each species in the evaporation flux, calculates the
    fraction that condenses in each stage based on the stage
    temperature relative to the species' condensation temperature.
    """

    def __init__(self, train: CondensationTrain):
        self.train = train

        # Default residence time per stage (seconds)
        # In a real design, this comes from equipment sizing
        self.residence_time_s = {
            0: 0.5,    # Hot duct — fast transit
            1: 5.0,    # Fe condenser — baffles slow the flow
            2: 240.0,  # Cr oxide harvester — dedicated hot cartridge
            3: 4.0,    # SiO zone — removable baffles
            4: 3.0,    # Cyclone — vortex residence
            5: 2.0,    # Dust filter
            6: 0.2,    # Turbine — very fast
            7: 0.0,    # Accumulator — no condensation
        }

    def route(self, evap_flux: EvaporationFlux, melt: MeltState):
        """
        Route all evaporated species through the train.

        For each species, walk through stages 0→6.  At each stage,
        calculate condensation fraction η.  Whatever condenses is
        added to that stage's collected_kg; the remainder passes
        to the next stage.

        O2 terminal storage is handled by the simulator atom ledger.  Stage
        collection dictionaries are UI projections and are updated only after
        the simulator commits the matching ledger transition.
        """
        remaining_by_species = {}
        condensed_by_stage_species: Dict[int, Dict[str, float]] = {}
        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            remaining_kg = rate_kg_hr  # Mass still in vapor phase

            T_cond = _species_condensation_temperature_C(species)
            hkl_condensed_by_stage: Dict[int, float] = {}

            for stage in self.train.stages:
                if remaining_kg <= 1e-15:
                    break
                if _cr_stage_isolation_blocks(stage, species):
                    continue

                # Calculate band-aware H-K-L deposition efficiency [COND-2]
                eta = self._condensation_efficiency(
                    stage=stage,
                    species=species,
                    T_cond_C=T_cond,
                    residence_s=self.residence_time_s.get(
                        stage.stage_number, 1.0),
                    alpha_s=_stage_alpha_s(stage, species),
                )

                condensed_kg = remaining_kg * eta
                if condensed_kg > 1e-15:
                    hkl_condensed_by_stage[stage.stage_number] = (
                        hkl_condensed_by_stage.get(stage.stage_number, 0.0)
                        + condensed_kg)

                remaining_kg -= condensed_kg

            hkl_condensed_total_kg = sum(hkl_condensed_by_stage.values())
            capture_budget_kg = _pressure_isolated_capture_budget_kg(
                species,
                rate_kg_hr,
                self.train.stages,
                self.residence_time_s,
            )
            if hkl_condensed_total_kg <= 1e-15:
                capture_budget_kg = 0.0
            elif capture_budget_kg > 0.0:
                scale = capture_budget_kg / hkl_condensed_total_kg
                for stage_number, hkl_stage_kg in hkl_condensed_by_stage.items():
                    condensed_kg = hkl_stage_kg * scale
                    if condensed_kg <= 1e-15:
                        continue
                    stage_species = condensed_by_stage_species.setdefault(
                        stage_number, {})
                    stage_species[species] = (
                        stage_species.get(species, 0.0) + condensed_kg)

            remaining_by_species[species] = max(
                0.0, rate_kg_hr - capture_budget_kg)

        return CondensationRouteResult(
            remaining_by_species=remaining_by_species,
            condensed_by_stage_species=condensed_by_stage_species,
        )

    def _condensation_efficiency(
        self,
        *,
        stage: CondensationStage,
        species: str,
        T_cond_C: float,
        residence_s: float,
        alpha_s: float,
    ) -> float:
        """
        Condensation efficiency for one species in one stage.

        Hertz-Knudsen-Langmuir surface deposition:

            J = alpha_s * max(0, P_local - P_sat(T_surface))
                / sqrt(2*pi*m*k*T_surface) * regime_factor(Kn)

        Chunk A keeps the existing stage residence-time surrogate for
        geometry and integrates the H-K-L driving force across the actual
        stage T-band. Chunk C replaces the constant regime factor with
        pressure/Knudsen coupling.
        """
        if residence_s <= 0.0 or alpha_s <= 0.0:
            return 0.0

        P_local_pa = _local_species_pressure_pa(species, T_cond_C)
        if P_local_pa <= 0.0:
            return 0.0

        T_ref_K = max(T_cond_C + 273.15, 1.0)
        reference_flux = _hkl_impingement_flux_mol_m2_s(
            species, P_local_pa, T_ref_K,
        )
        if reference_flux <= 0.0:
            return 0.0

        lo_C, hi_C = _stage_temp_band_C(stage)
        if hi_C < lo_C:
            lo_C, hi_C = hi_C, lo_C

        band_flux_fraction = 0.0
        width_C = hi_C - lo_C
        for sample in range(HKL_BAND_SAMPLES):
            if width_C <= 0.0:
                T_surface_C = lo_C
            else:
                T_surface_C = (
                    lo_C + width_C * (sample + 0.5) / HKL_BAND_SAMPLES
                )
            T_surface_K = max(T_surface_C + 273.15, 1.0)
            flux = _hkl_surface_deposition_flux_mol_m2_s(
                species, P_local_pa, T_surface_K, alpha_s,
            )
            band_flux_fraction += flux / reference_flux
        band_flux_fraction /= HKL_BAND_SAMPLES

        rate_s_inv = max(0.0, band_flux_fraction)
        eta = 1.0 - math.exp(-residence_s * rate_s_inv)
        return max(0.0, min(1.0, eta))


def _species_vapor_data(species: str) -> Mapping[str, Any]:
    for family in ('metals', 'oxide_vapors'):
        data = (VAPOR_PRESSURE_DATA.get(family, {}) or {}).get(species, {})
        if data and isinstance(data, Mapping):
            return data
    return {}


def _species_condensation_temperature_C(species: str) -> float:
    if species in CONDENSATION_TEMPS_C:
        return float(CONDENSATION_TEMPS_C[species])
    data = _species_vapor_data(species)
    try:
        return float(data.get('condensation_T_C_at_1mbar', 500.0))
    except (TypeError, ValueError):
        return 500.0


def _stage_material_config(stage: CondensationStage) -> Mapping[str, Any]:
    stages = MATERIALS_DATA.get('stages', {}) or {}
    if not isinstance(stages, Mapping):
        return {}
    config = stages.get(stage.stage_number, stages.get(str(stage.stage_number), {}))
    return config if isinstance(config, Mapping) else {}


def _stage_temp_band_C(stage: CondensationStage) -> tuple[float, float]:
    lo_C, hi_C = stage.temp_range_C
    return float(lo_C), float(hi_C)


def _stage_alpha_s(stage: CondensationStage, species: str) -> float:
    config = _stage_material_config(stage)
    alpha_by_species = config.get('alpha_s_by_species', {}) or {}
    value = (
        alpha_by_species.get(species)
        if isinstance(alpha_by_species, Mapping)
        else None
    )
    if value is None:
        value = STICKING_COEFF.get(species, 0.8)
    try:
        alpha_s = float(value)
    except (TypeError, ValueError):
        alpha_s = 0.0
    if not math.isfinite(alpha_s):
        return 0.0
    return max(0.0, min(1.0, alpha_s))


def _antoine_psat_pa(species: str, T_K: float) -> float | None:
    data = _species_vapor_data(species)
    antoine = data.get('antoine', {}) if isinstance(data, Mapping) else {}
    if not isinstance(antoine, Mapping):
        return None
    try:
        A = float(antoine.get('A', 0.0))
        B = float(antoine.get('B', 0.0))
        C = float(antoine.get('C', 0.0))
        T_K = float(T_K)
    except (TypeError, ValueError):
        return None
    if not (A > 0.0 and math.isfinite(T_K) and T_K + C > 0.0):
        return None
    # Same Antoine form used by equilibrium.py and builtin vapor pressure.
    return 10.0 ** (A - B / (T_K + C))


def _local_species_pressure_pa(species: str, T_cond_C: float) -> float:
    P_local_pa = _antoine_psat_pa(species, T_cond_C + 273.15)
    if P_local_pa is not None and P_local_pa > 0.0:
        return P_local_pa
    # Existing condensation temperatures are documented at ~1 mbar.
    return 100.0


def _molecular_mass_kg_per_molecule(species: str) -> float:
    data = _species_vapor_data(species)
    value = data.get('molar_mass_g_mol') if isinstance(data, Mapping) else None
    if value is None:
        value = MOLAR_MASS.get(species)
    try:
        molar_mass_g_mol = float(value)
    except (TypeError, ValueError):
        molar_mass_g_mol = 50.0
    if not math.isfinite(molar_mass_g_mol) or molar_mass_g_mol <= 0.0:
        molar_mass_g_mol = 50.0
    return (molar_mass_g_mol / 1000.0) / AVOGADRO_MOL


def _hkl_impingement_flux_mol_m2_s(
    species: str,
    pressure_pa: float,
    T_K: float,
) -> float:
    if pressure_pa <= 0.0 or T_K <= 0.0:
        return 0.0
    molecule_kg = _molecular_mass_kg_per_molecule(species)
    denominator = math.sqrt(
        2.0 * math.pi * molecule_kg * BOLTZMANN_CONSTANT_J_K * T_K
    )
    if denominator <= 0.0:
        return 0.0
    return pressure_pa / denominator / AVOGADRO_MOL


def _hkl_surface_deposition_flux_mol_m2_s(
    species: str,
    P_local_pa: float,
    T_surface_K: float,
    alpha_s: float,
) -> float:
    P_sat_pa = _antoine_psat_pa(species, T_surface_K)
    if P_sat_pa is None:
        return 0.0
    driving_pressure_pa = max(0.0, P_local_pa - P_sat_pa)
    if driving_pressure_pa <= 0.0:
        return 0.0
    # Chunk C replaces this constant with pressure/Knudsen coupling.
    return (
        alpha_s
        * REGIME_FACTOR_KN_PLACEHOLDER
        * _hkl_impingement_flux_mol_m2_s(
            species, driving_pressure_pa, T_surface_K,
        )
    )


def _pressure_isolated_capture_budget_kg(
    species: str,
    rate_kg_hr: float,
    stages: list[CondensationStage],
    residence_time_s: Mapping[int, float],
) -> float:
    """Hold total vapor removal fixed until Chunk C pressure coupling lands."""

    remaining_kg = max(0.0, rate_kg_hr)
    T_cond_C = _species_condensation_temperature_C(species)
    alpha_s = STICKING_COEFF.get(species, 0.8)
    for stage in stages:
        if remaining_kg <= 1e-15:
            break
        if _cr_stage_isolation_blocks(stage, species):
            continue
        eta = _pressure_isolated_stage_efficiency(
            stage,
            T_cond_C,
            float(residence_time_s.get(stage.stage_number, 1.0)),
            alpha_s,
        )
        remaining_kg -= remaining_kg * eta
    return max(0.0, min(rate_kg_hr, rate_kg_hr - remaining_kg))


def _pressure_isolated_stage_efficiency(
    stage: CondensationStage,
    T_cond_C: float,
    residence_s: float,
    alpha_s: float,
) -> float:
    lo_C, hi_C = stage.temp_range_C
    T_stage_C = (float(lo_C) + float(hi_C)) / 2.0
    if T_stage_C >= T_cond_C:
        return 0.0
    delta_T = T_cond_C - T_stage_C
    tau_s = 1.0 / (
        alpha_s * max(delta_T / max(T_cond_C, 1.0), 0.01)
    )
    eta = 1.0 - math.exp(-residence_s / tau_s)
    return max(0.0, min(1.0, eta))


def _cr_stage_isolation_blocks(stage: CondensationStage, species: str) -> bool:
    chromium_stage = 'CrO2' in stage.target_species
    if species in {'Cr', 'CrO2'}:
        return not chromium_stage
    return chromium_stage
