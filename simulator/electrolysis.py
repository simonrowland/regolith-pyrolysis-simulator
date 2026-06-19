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
        E = E° + (RT / nF) × ln(a_O2^νO2 / a_oxide)
    Adjusts standard decomposition voltages for actual melt
    activities, evolved-O₂ backpressure, and temperature.

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

Standard decomposition voltages at ~1873 K / ~1600 C (per mol O2):
    NiO:   0.39 V   Na2O:  0.5 V    K2O:    0.5 V
    FeO:   0.75 V   Fe2O3: 0.90 V   Cr2O3:  0.95 V
    MnO:   1.05 V   SiO2:  1.45 V   TiO2:   1.70 V
    Al2O3: 1.95 V   MgO:   2.2 V    CaO:    2.5 V

Source: raw-thermo reanchor, E = -DeltaGf(1873 K)/(nF), rounded to 0.05 V.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from simulator.core import (
    MOLAR_MASS, OXIDE_TO_METAL, FARADAY, GAS_CONSTANT, MeltState,
)


# Standard decomposition voltages at ~1873 K / ~1600 C (V).
# Raw-thermo rungs use E = -DeltaGf(1873 K)/(nF), rounded to 0.05 V.
DECOMP_VOLTAGES = {
    # NiO source: DeltaGf(NiO, ~1873 K) ~= -76 kJ/mol
    # [Hemingway 1990 Am. Mineral. 75:781 + Robie & Hemingway + NEA
    # Chemical Thermodynamics of Nickel]; E = -DeltaGf/(2F) ~= 0.39 V
    # standard-state. Runtime Nernst applies melt activity + pO2.
    'NiO':   0.39,
    # Na2O/K2O volatility caveat: condensed-phase DeltaGf at 1873 K is
    # estimated; Na/K are volatile above their boiling points, so activity
    # and vapor partitioning can lower the effective threshold. Hold legacy
    # 0.5 V pending activity/vapor-aware grounding.
    'Na2O':  0.5,
    'K2O':   0.5,
    # O'Neill 1988 + Chase 1998 Fe-O emf/raw-thermo anchor.
    'FeO':   0.75,
    # FeO-scale-tied rescale. Simplified sequential ferric reduction:
    # FeO is favored first; Fe2O3 remains explicit until a fO2-coupled
    # ferric/ferrous melt model lands.
    'Fe2O3': 0.90,
    # NIST-JANAF/Chase 1998 + Barin; modest-confidence upper-range anchor.
    'Cr2O3': 0.95,
    # NIST-JANAF/Chase 1998 + Barin; modest-confidence anchor.
    'MnO':   1.05,
    # Chase 1998 raw-thermo anchor.
    'SiO2':  1.45,
    # Chase 1998 + Barin raw-thermo anchor.
    'TiO2':  1.70,
    # NIST-JANAF/Chase 1998 + Barin raw-thermo anchor.
    'Al2O3': 1.95,
    'MgO':   2.2,
    'CaO':   2.5,
}


def min_decomposition_voltage() -> float:
    return min(DECOMP_VOLTAGES.values())


# Electrons transferred per formula unit of oxide reduced
ELECTRONS_PER_OXIDE = {
    'NiO':   2,   # NiO → Ni + ½ O₂
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

    def nernst_voltage(
        self,
        oxide: str,
        T_C: float,
        activity: float = 1.0,
        pO2_bar: float = 1.0,
    ) -> float:
        """
        Nernst-adjusted decomposition voltage.

        E = E° + (RT / nF) × ln(a_O2^νO2 / a_oxide)        [NERNST-1]

        Lower oxide activity (depleted species) → higher voltage
        needed for reduction. Lower evolved-O2 activity lowers the
        decomposition threshold for reactions producing O2.

        Args:
            oxide:    Oxide species key (e.g., 'SiO2')
            T_C:      Temperature (°C)
            activity: Oxide activity in the melt (0-1)
            pO2_bar:  Evolved-O2 activity, referenced to 1 bar

        Returns:
            Adjusted decomposition voltage (V)
        """
        E0 = self.decomp_voltages.get(oxide, 2.5)
        n = ELECTRONS_PER_OXIDE.get(oxide, 2)
        T_K = T_C + 273.15

        if activity <= 1e-10:
            return E0 + 1.0  # Very high — species essentially depleted

        metal_info = OXIDE_TO_METAL.get(oxide)
        o2_mol_per_oxide = 0.0
        if metal_info:
            _metal, _n_met, n_oxy = metal_info
            o2_mol_per_oxide = n_oxy / 2.0
        pO2_activity = max(float(pO2_bar), 1e-30)

        # Nernst adjustment. For MOx -> M + νO2 O2, Q = aO2^νO2 / a_oxide.
        E = E0 - (GAS_CONSTANT * T_K) / (n * FARADAY) * math.log(activity)
        E += (
            (GAS_CONSTANT * T_K)
            / (n * FARADAY)
            * o2_mol_per_oxide
            * math.log(pO2_activity)
        )
        return E

    def step_hour(self, melt_state: MeltState,
                   voltage_V: float,
                   current_A: float,
                   T_C: float,
                   pO2_bar: float = 1.0) -> Dict:
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
            E_nernst = self.nernst_voltage(
                oxide, T_C, activity, pO2_bar=pO2_bar)

            if E_nernst < voltage_V:
                overvoltage = voltage_V - E_nernst
                reducible.append((oxide, E_nernst, overvoltage, activity))

        if not reducible:
            return result

        # Partition current among reducible species            [SEL-1]
        # Weight by: concentration × exp(overvoltage)
        weights = {}
        uncapped_charge_mol_e = 0.0
        capped_charge_mol_e = 0.0
        any_capped = False

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

            uncapped_moles_reduced = (I_species * eta_CE * t_s) / (n * FARADAY)
            uncapped_charge_mol_e += uncapped_moles_reduced * n
            kg_oxide_reduced = uncapped_moles_reduced * M_oxide_gmol / 1000.0  # g->kg

            # Don't reduce more than available
            available = melt_state.composition_kg.get(oxide, 0.0)
            if available < kg_oxide_reduced:
                any_capped = True
            kg_oxide_reduced = min(kg_oxide_reduced, available)
            moles_reduced = kg_oxide_reduced * 1000.0 / M_oxide_gmol
            capped_charge_mol_e += moles_reduced * n

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

        # Energy consumed. Final depletion hours are scaled by capped
        # Faradaic charge; non-depletion hours stay at commanded V*A*hr.
        # Scale ONLY when a cap actually bound, so non-depletion energy
        # multiplies by an exact 1.0 and is BIT-identical to V*A*hr/1000
        # (the capped/uncapped ratio would otherwise carry a kg->mol ULP).
        cap_ratio = 1.0
        if any_capped and uncapped_charge_mol_e > 0.0:
            cap_ratio = capped_charge_mol_e / uncapped_charge_mol_e
        result['energy_kWh'] = voltage_V * current_A * 1.0 / 1000.0 * cap_ratio

        return result

    def get_reduction_sequence(
        self,
        melt_state: MeltState,
        T_C: float,
        pO2_bar: float = 1.0,
    ) -> List[Tuple[str, float]]:
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
            E = self.nernst_voltage(oxide, T_C, activity, pO2_bar=pO2_bar)
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
