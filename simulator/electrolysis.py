"""
Molten Regolith Electrolysis (MRE) Model
=========================================

★ TIER 2: SCIENTIST-READABLE ★

Models the electrochemical reduction of oxide species from a
silicate melt at controlled voltage and temperature.

Used by both C5 (limited MRE in pyrolysis track) and the
Standard MRE Baseline (root branch alternative).

Physics:
    Nernst equation:                                         [NERNST-1]
        E = E° - (RT / nF) × ln(a_oxide)
    Adjusts standard decomposition voltages for actual melt
    activities (from MELTS equilibrium) and temperature.

    Faraday's law:                                           [FARADAY-1]
        m = (I × t × M) / (n × F)
    Converts electrical current to mass of metal reduced.

    Current efficiency:                                      [CE-1]
        η_CE = 0.30 + 0.45 × (1 - exp(-0.5 × (V - E_nernst)))
    Empirical model loosely based on Schreiner (MIT) and
    Sirk et al. observations.  Efficiency is lower at voltages
    near the Nernst potential (back-reaction losses) and improves
    with overvoltage.

    Species selectivity:                                     [SEL-1]
        At overlapping voltage windows, current partitions
        between species proportional to their exchange current
        densities (approximated here by concentration and
        voltage proximity).

Standard decomposition voltages at ~1600°C (per mol O₂):
    Na₂O:  <0.5 V   K₂O:   <0.5 V   FeO:    0.6 V
    Fe₂O₃: 0.75 V   Cr₂O₃: 0.9 V    MnO:    1.0 V
    SiO₂:  1.4 V
    TiO₂:  1.5 V    Al₂O₃: 1.9 V    MgO:    2.2 V
    CaO:   2.5 V

Source: Ellingham diagram at 1600°C (context-setpoints.yaml §9)
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from simulator.core import (
    MOLAR_MASS, OXIDE_TO_METAL, FARADAY, GAS_CONSTANT, MeltState,
)


# Standard decomposition voltages at ~1600°C (V)
DECOMP_VOLTAGES = {
    'Na2O':  0.5,
    'K2O':   0.5,
    'FeO':   0.6,
    # Simplified sequential ferric reduction: FeO is favored first; Fe2O3
    # remains explicit until a fO2-coupled ferric/ferrous melt model lands.
    'Fe2O3': 0.75,
    'Cr2O3': 0.9,
    'MnO':   1.0,
    'SiO2':  1.4,
    'TiO2':  1.5,
    'Al2O3': 1.9,
    'MgO':   2.2,
    'CaO':   2.5,
}

# Electrons transferred per formula unit of oxide reduced
ELECTRONS_PER_OXIDE = {
    'Na2O':  2,   # Na₂O → 2 Na + ½ O₂  (2 electrons)
    'K2O':   2,
    'FeO':   2,   # FeO → Fe + ½ O₂
    'Fe2O3': 6,   # Fe₂O₃ → 2 Fe + 1½ O₂
    'Cr2O3': 6,   # Cr₂O₃ → 2 Cr + 1½ O₂
    'MnO':   2,
    'SiO2':  4,   # SiO₂ → Si + O₂
    'TiO2':  4,
    'Al2O3': 6,   # Al₂O₃ → 2 Al + 1½ O₂
    'MgO':   2,
    'CaO':   2,
}


class ElectrolysisModel:
    """
    Molten Regolith Electrolysis simulator.

    Models the electrochemical decomposition of oxide species
    in a silicate melt at controlled voltage and current.
    """

    def __init__(self):
        self.decomp_voltages = dict(DECOMP_VOLTAGES)
        self.electrode_area_m2 = 0.05  # Default electrode area

    def nernst_voltage(self, oxide: str, T_C: float,
                        activity: float = 1.0) -> float:
        """
        Nernst-adjusted decomposition voltage.

        E = E° - (RT / nF) × ln(a_oxide)                    [NERNST-1]

        Lower oxide activity (depleted species) → higher voltage
        needed for reduction.

        Args:
            oxide:    Oxide species key (e.g., 'SiO2')
            T_C:      Temperature (°C)
            activity: Oxide activity in the melt (0-1)

        Returns:
            Adjusted decomposition voltage (V)
        """
        E0 = self.decomp_voltages.get(oxide, 2.5)
        n = ELECTRONS_PER_OXIDE.get(oxide, 2)
        T_K = T_C + 273.15

        if activity <= 1e-10:
            return E0 + 1.0  # Very high — species essentially depleted

        # Nernst adjustment
        E = E0 - (GAS_CONSTANT * T_K) / (n * FARADAY) * math.log(activity)
        return E

    def step_hour(self, melt_state: MeltState,
                   voltage_V: float,
                   current_A: float,
                   T_C: float) -> Dict:
        """
        Simulate one hour of electrolysis.

        For each oxide species whose Nernst voltage is below the
        applied voltage, calculate the fraction of current going
        to that species and apply Faraday's law.

        Args:
            melt_state: Current melt composition
            voltage_V:  Applied cell voltage (V)
            current_A:  Total cell current (A)
            T_C:        Cell temperature (°C)

        Returns:
            Dict with keys:
                oxides_reduced_kg:  {oxide: kg_removed}
                metals_produced_kg: {metal: kg_produced}
                O2_produced_kg:     float
                energy_kWh:         float
        """
        comp = melt_state.composition_wt_pct()
        result = {
            'oxides_reduced_kg': {},
            'oxides_reduced_mol': {},
            'metals_produced_kg': {},
            'metals_produced_mol': {},
            'O2_produced_kg': 0.0,
            'O2_produced_mol': 0.0,
            'energy_kWh': 0.0,
        }

        # Find all reducible species at this voltage
        reducible = []
        for oxide in DECOMP_VOLTAGES:
            if oxide not in melt_state.composition_kg:
                continue
            if melt_state.composition_kg.get(oxide, 0.0) < 1e-6:
                continue

            # Crude activity ≈ wt_fraction
            activity = comp.get(oxide, 0.0) / 100.0
            E_nernst = self.nernst_voltage(oxide, T_C, activity)

            if E_nernst < voltage_V:
                overvoltage = voltage_V - E_nernst
                reducible.append((oxide, E_nernst, overvoltage, activity))

        if not reducible:
            return result

        # Partition current among reducible species            [SEL-1]
        # Weight by: concentration × exp(overvoltage)
        weights = {}
        for oxide, E, dV, a in reducible:
            weights[oxide] = a * math.exp(min(dV, 3.0))

        total_weight = sum(weights.values())
        if total_weight <= 0:
            return result

        for oxide, E, dV, a in reducible:
            fraction = weights[oxide] / total_weight
            I_species = current_A * fraction

            # Current efficiency                                [CE-1]
            eta_CE = 0.30 + 0.45 * (1.0 - math.exp(-0.5 * max(0, dV)))
            eta_CE = min(0.95, max(0.10, eta_CE))

            # Faraday's law: mass reduced this hour            [FARADAY-1]
            n = ELECTRONS_PER_OXIDE.get(oxide, 2)
            M_oxide_gmol = MOLAR_MASS.get(oxide, 100.0)  # g/mol
            t_s = 3600.0  # 1 hour in seconds

            moles_reduced = (I_species * eta_CE * t_s) / (n * FARADAY)
            kg_oxide_reduced = moles_reduced * M_oxide_gmol / 1000.0  # g→kg

            # Don't reduce more than available
            available = melt_state.composition_kg.get(oxide, 0.0)
            kg_oxide_reduced = min(kg_oxide_reduced, available)
            moles_reduced = kg_oxide_reduced * 1000.0 / M_oxide_gmol

            if kg_oxide_reduced > 1e-10:
                result['oxides_reduced_kg'][oxide] = kg_oxide_reduced
                result['oxides_reduced_mol'][oxide] = moles_reduced

                # Metal produced
                metal_info = OXIDE_TO_METAL.get(oxide)
                if metal_info:
                    metal, n_met, n_oxy = metal_info
                    M_metal_gmol = MOLAR_MASS[metal]  # g/mol
                    metal_mol = moles_reduced * n_met
                    metal_kg = metal_mol * M_metal_gmol / 1000.0
                    result['metals_produced_kg'][metal] = (
                        result['metals_produced_kg'].get(metal, 0.0)
                        + metal_kg)
                    result['metals_produced_mol'][metal] = (
                        result['metals_produced_mol'].get(metal, 0.0)
                        + metal_mol)

                    # O₂ produced
                    O2_mol = moles_reduced * n_oxy / 2.0
                    O2_kg = O2_mol * MOLAR_MASS['O2'] / 1000.0
                    result['O2_produced_kg'] += O2_kg
                    result['O2_produced_mol'] += O2_mol

        # Energy consumed
        result['energy_kWh'] = voltage_V * current_A * 1.0 / 1000.0  # V×A×hr/1000

        return result

    def get_reduction_sequence(self, melt_state: MeltState,
                                T_C: float) -> List[Tuple[str, float]]:
        """
        Return oxide species in order of increasing Nernst voltage.

        Useful for showing the operator what will reduce first
        at the current melt composition and temperature.
        """
        comp = melt_state.composition_wt_pct()
        sequence = []

        for oxide in DECOMP_VOLTAGES:
            if melt_state.composition_kg.get(oxide, 0.0) < 1e-6:
                continue
            activity = comp.get(oxide, 0.0) / 100.0
            E = self.nernst_voltage(oxide, T_C, activity)
            sequence.append((oxide, E))

        sequence.sort(key=lambda x: x[1])
        return sequence

    def estimate_total_energy_kWh(self, melt_state: MeltState,
                                    max_voltage_V: float,
                                    T_C: float = 1575.0,
                                    current_A: float = 100.0) -> float:
        """
        Estimate total electrical energy to process to a target voltage.

        Rough estimate by summing Faraday energy for all species
        below the target voltage, divided by estimated efficiency.
        """
        comp = melt_state.composition_wt_pct()
        total_energy = 0.0

        for oxide, E0 in self.decomp_voltages.items():
            if E0 > max_voltage_V:
                continue
            kg = melt_state.composition_kg.get(oxide, 0.0)
            if kg < 1e-6:
                continue

            n = ELECTRONS_PER_OXIDE.get(oxide, 2)
            M_oxide_gmol = MOLAR_MASS.get(oxide, 100.0)  # g/mol
            moles = kg * 1000.0 / M_oxide_gmol  # kg→g then ÷ g/mol

            # Faraday energy: E = V × n × F × moles / (η × 1000)
            eta = 0.5  # average efficiency
            V = (E0 + max_voltage_V) / 2.0  # average operating voltage
            energy_kWh = V * n * FARADAY * moles / (eta * 1000.0 * 3600.0)
            total_energy += energy_kWh

        return total_energy
