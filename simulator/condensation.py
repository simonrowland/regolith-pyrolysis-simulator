"""
Condensation Train Model
=========================

★ TIER 2: SCIENTIST-READABLE ★

Models vapor flow through the 7-stage metals condensation train.
Each stage operates at a fixed temperature range and preferentially
collects species whose condensation temperature falls within that range.

Train topology (metals train, active C2A onward):
    Stage 0  Hot duct (>1400°C)      — IR spectroscopy, no condensation
    Stage 1  Fe condenser (1100-1400°C) — liquid Fe drains to sump
    Stage 2  SiO zone (900-1200°C)   — fused silica on removable baffles
    Stage 3  Alkali/Mg cyclone (350-700°C) — Na/K/Mg condensation
    Stage 4  Vortex dust filter (200-350°C) — entrained particle capture
    Stage 5  Turbine-compressor      — pressure regulation, pO₂ control
    Stage 6  O₂ accumulator (~3 bar) — compressed O₂ storage

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
from typing import Dict

from simulator.core import (
    CondensationTrain, CondensationStage, EvaporationFlux, MeltState,
)


# Condensation temperatures at ~1 mbar partial pressure (°C)
# Used to determine where each species preferentially deposits.
CONDENSATION_TEMPS_C = {
    'Fe':  1250,
    'SiO': 1050,   # condenses as amorphous SiO₂ (disproportionation)
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
    'Mg':  0.8,
    'Na':  0.95,
    'K':   0.95,
    'Ca':  0.85,
    'Mn':  0.85,
    'Cr':  0.9,
}


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
            2: 4.0,    # SiO zone — removable baffles
            3: 3.0,    # Cyclone — vortex residence
            4: 2.0,    # Dust filter
            5: 0.2,    # Turbine — very fast
            6: 0.0,    # Accumulator — no condensation
        }

    def route(self, evap_flux: EvaporationFlux, melt: MeltState):
        """
        Route all evaporated species through the train.

        For each species, walk through stages 0→6.  At each stage,
        calculate condensation fraction η.  Whatever condenses is
        added to that stage's collected_kg; the remainder passes
        to the next stage.

        O₂ passes through all stages to the accumulator (Stage 6).
        """
        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            remaining_kg = rate_kg_hr  # Mass still in vapor phase

            T_cond = CONDENSATION_TEMPS_C.get(species, 500.0)
            alpha = STICKING_COEFF.get(species, 0.8)

            for stage in self.train.stages:
                if remaining_kg <= 1e-15:
                    break

                # Calculate condensation efficiency               [COND-1]
                eta = self._condensation_efficiency(
                    T_stage_C=_stage_midpoint(stage),
                    T_cond_C=T_cond,
                    residence_s=self.residence_time_s.get(
                        stage.stage_number, 1.0),
                    alpha=alpha,
                )

                condensed_kg = remaining_kg * eta
                if condensed_kg > 1e-15:
                    # Map SiO vapor → SiO₂ solid (disproportionation)
                    product = 'SiO2' if species == 'SiO' else species
                    stage.collected_kg[product] = (
                        stage.collected_kg.get(product, 0.0) + condensed_kg)

                remaining_kg -= condensed_kg

    def _condensation_efficiency(self, T_stage_C: float,
                                  T_cond_C: float,
                                  residence_s: float,
                                  alpha: float) -> float:
        """
        Condensation efficiency for one species in one stage.

        η = 1 - exp(-t_res / τ_cond)                          [COND-1]

        τ_cond depends on how far below the condensation temperature
        the stage operates.  When T_stage << T_cond, τ_cond is small
        and nearly everything condenses.  When T_stage ≈ T_cond,
        condensation is marginal.  When T_stage > T_cond, nothing
        condenses (η = 0).

        Args:
            T_stage_C:    Stage operating temperature (°C)
            T_cond_C:     Species condensation temperature (°C)
            residence_s:  Residence time in the stage (s)
            alpha:        Sticking coefficient (0-1)

        Returns:
            Fraction condensed (0 to 1)
        """
        # If stage is hotter than condensation T, nothing condenses
        if T_stage_C >= T_cond_C:
            return 0.0

        # Degree of subcooling (how far below condensation T)
        delta_T = T_cond_C - T_stage_C

        # Characteristic condensation time
        # Decreases with subcooling: τ ∝ 1 / (α × ΔT / T_cond)
        # At 50% subcooling (ΔT = 0.5 × T_cond), τ ≈ 1s
        tau_s = 1.0 / (alpha * max(delta_T / max(T_cond_C, 1.0), 0.01))

        eta = 1.0 - math.exp(-residence_s / tau_s)
        return max(0.0, min(1.0, eta))


def _stage_midpoint(stage: CondensationStage) -> float:
    """Midpoint temperature of a stage."""
    lo, hi = stage.temp_range_C
    return (lo + hi) / 2.0
