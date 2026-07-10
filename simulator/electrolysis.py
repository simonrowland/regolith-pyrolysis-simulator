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
        bounded FeO/electronic-loss band with saturating dV response
    Grounded heuristic: high-FeO melts stay in the 0.30-0.60 CE
    loss band, low-FeO melts can reach >=0.85, and post-Fe/low
    electronic-conduction operation approaches but does not exceed 0.995.

    Species selectivity:                                     [SEL-1]
        At overlapping voltage windows, current partitions
        between species proportional to their exchange current
        densities (approximated here by concentration and
        voltage proximity).

Standard decomposition voltages at ~1873 K / ~1600 C (per mol O2):
    NiO:   0.39 V   Na2O:  0.5 V    K2O:    0.5 V
    FeO:   0.75 V   Cr2O3:  0.95 V  MnO:    1.05 V
    Fe2O3: 0.90 V reference only; not a live MRE full-reduction rung
    SiO2:  1.45 V   TiO2:  1.70 V   Al2O3:  1.95 V
    MgO:   2.2 V    CaO:   2.5 V

Source posture: raw-thermo where cited; otherwise legacy/status-marked.
The MRE ladder is North-Star-optional diagnostic output, not certification
evidence.
"""

from __future__ import annotations

import math
from typing import Dict, List, Mapping, Tuple

from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_METAL_PHASE_CONDENSED,
    ELLINGHAM_METAL_PHASE_GAS,
)
from simulator.chemistry.melt_activity import melt_oxide_activity
from simulator.core import (
    MOLAR_MASS, OXIDE_TO_METAL, FARADAY, GAS_CONSTANT, MeltState,
)
from simulator.mre_ladder import DECOMP_VOLTAGES, mre_decomposition_voltage_reference
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


FERRIC_TO_FERROUS_REFERENCE_V = 0.65
FERRIC_TO_FERROUS_REFERENCE_STATUS = (
    "uncertified_heuristic_reference_not_raw_thermo"
)
FERRIC_TO_FERROUS_ELECTRONS = 2
FERRIC_TO_FERROUS_FEO_PER_FE2O3 = 2.0
FERRIC_TO_FERROUS_O2_PER_FE2O3 = 0.5
MRE_NORTH_STAR_POSTURE = "north_star_optional_diagnostic"
MRE_OPTIONAL_BANNER = (
    "MRE is North-Star-optional diagnostic output; uncited MRE heuristics "
    "are denied certification."
)
MRE_CERTIFICATION_EVIDENCE_CLASS = "internal-analytical"
MRE_CERTIFICATION_DENYLIST_REASON = (
    "mre_current_partition_uncited_heuristic_denied_certification"
)
MRE_CURRENT_PARTITION_SOURCE = (
    "heuristic:activity_exp_overvoltage_SEL-1_plus_bounded_FeO_CE_v1_not_certified"
)
MRE_CURRENT_PARTITION_CERTIFICATION = "uncertified_current_partition"
MRE_MULTI_OXIDE_PARTITION_REFUSAL = "uncertified_multi_oxide_current_partition"
MRE_RAW_MARGIN_REFUSAL = "non_authoritative_fallback_raw_margin_nonpositive"
# C1-01 validates the fallback/raw acceptance split for FeO.  Other fallback
# oxides keep their existing policy until their margins are independently
# validated; their raw requirements are still surfaced in diagnostics below.
MRE_RAW_MARGIN_GUARDED_OXIDES = frozenset({"FeO"})
MRE_FIXED_REDUCIBLE_OXIDES = tuple(
    oxide for oxide in DECOMP_VOLTAGES
    if oxide != 'Fe2O3'
)


def min_decomposition_voltage(*, temperature_K: float | None = None) -> float:
    voltages = [
        reference.voltage
        for oxide in MRE_FIXED_REDUCIBLE_OXIDES
        if (
            reference := mre_decomposition_voltage_reference(
                oxide,
                temperature_K=temperature_K,
            )
        ) is not None
    ]
    return min(voltages) if voltages else min(DECOMP_VOLTAGES.values())


def melt_account_mol_from_kg(
    composition_kg: Mapping[str, float],
) -> dict[str, float]:
    account_mol: dict[str, float] = {}
    for oxide, kg in composition_kg.items():
        molar_mass = MOLAR_MASS.get(str(oxide))
        if molar_mass is None:
            continue
        try:
            kg_value = float(kg)
        except (TypeError, ValueError):
            continue
        if kg_value <= 0.0:
            continue
        account_mol[str(oxide)] = kg_value * 1000.0 / molar_mass
    return account_mol


def mre_oxide_activity(
    oxide: str,
    account_mol: Mapping[str, float],
) -> float:
    activity = melt_oxide_activity(oxide, account_mol)
    if activity is None:
        return 0.0
    return max(0.0, float(activity.activity))


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


CE_PHYSICAL_FLOOR = 0.10
CE_PHYSICAL_CEILING = 0.995
FEO_POST_FE_FRACTION = 0.005
FEO_LOW_FRACTION = 0.020
FEO_HIGH_FRACTION = 0.100
CE_POST_FE_FLOOR = 0.90
CE_POST_FE_CEILING = 0.995
CE_LOW_FEO_FLOOR = 0.85
CE_LOW_FEO_CEILING = 0.96
CE_HIGH_FEO_FLOOR = 0.30
CE_HIGH_FEO_CEILING = 0.60
DV_RESPONSE_PER_V = 1.20
CURRENT_EFFICIENCY_MODEL_ID = "bounded_feo_electronic_loss_v1"


def _finite_float(value, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, x))


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    t = _clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b
    return a + (b - a) * t


def _current_efficiency_terms(
    dV: float,
    feo_fraction: float,
    *,
    electronic_transference: float | None = None,
) -> dict[str, float]:
    feo = _clamp(_finite_float(feo_fraction, 0.0), 0.0, 1.0)
    if electronic_transference is None:
        loss = _smoothstep(FEO_LOW_FRACTION, FEO_HIGH_FRACTION, feo)
    else:
        loss = _clamp(_finite_float(electronic_transference, 0.0), 0.0, 1.0)

    post_fe = 1.0 - _smoothstep(FEO_POST_FE_FRACTION, FEO_LOW_FRACTION, feo)
    base_floor = _lerp(CE_LOW_FEO_FLOOR, CE_HIGH_FEO_FLOOR, loss)
    base_ceiling = _lerp(CE_LOW_FEO_CEILING, CE_HIGH_FEO_CEILING, loss)
    floor = base_floor + post_fe * (CE_POST_FE_FLOOR - CE_LOW_FEO_FLOOR)
    ceiling = base_ceiling + post_fe * (CE_POST_FE_CEILING - CE_LOW_FEO_CEILING)
    overpotential = max(0.0, _finite_float(dV, 0.0))
    dV_term = 1.0 - math.exp(-DV_RESPONSE_PER_V * overpotential)
    eta = floor + (ceiling - floor) * dV_term
    eta = _clamp(eta, CE_PHYSICAL_FLOOR, CE_PHYSICAL_CEILING)
    return {
        "eta": eta,
        "feo_fraction": feo,
        "electronic_loss_coordinate": loss,
        "post_fe_coordinate": post_fe,
        "floor": floor,
        "ceiling": ceiling,
        "dV_term": dV_term,
    }


def current_efficiency(
    dV: float,
    feo_fraction: float,
    *,
    electronic_transference: float | None = None,
) -> float:
    return _current_efficiency_terms(
        dV,
        feo_fraction,
        electronic_transference=electronic_transference,
    )["eta"]


def current_efficiency_diagnostic(dV: float, feo_fraction: float) -> dict[str, float | str]:
    terms = _current_efficiency_terms(dV, feo_fraction)
    return {
        "model": CURRENT_EFFICIENCY_MODEL_ID,
        "source": MRE_CURRENT_PARTITION_SOURCE,
        "eta": terms["eta"],
        "feo_fraction": terms["feo_fraction"],
        "feo_wt_pct": 100.0 * terms["feo_fraction"],
        "electronic_loss_coordinate": terms["electronic_loss_coordinate"],
        "post_fe_coordinate": terms["post_fe_coordinate"],
        "floor": terms["floor"],
        "ceiling": terms["ceiling"],
        "dV_term": terms["dV_term"],
        "saturation": "FeO/electronic-loss ceiling applied before dV response",
    }


def uncertified_multi_oxide_partition_targets(reducible) -> tuple[str, ...]:
    targets = sorted({
        str(row[0])
        for row in reducible
        for mode in (row[4],)
        if mode == "oxide_to_metal"
    })
    return tuple(targets) if len(targets) > 1 else ()


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
        n = ELECTRONS_PER_OXIDE.get(oxide, 2)
        T_K = T_C + CELSIUS_TO_KELVIN_OFFSET
        reference = mre_decomposition_voltage_reference(oxide, temperature_K=T_K)
        E0 = 2.5 if reference is None else reference.voltage

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

    def ferric_to_ferrous_voltage(self, T_C: float, activity: float,
                                  pO2_bar: float = 1.0) -> float:
        activity = max(float(activity), 1e-30)
        pO2_activity = max(float(pO2_bar), 1e-30)
        T_K = float(T_C) + CELSIUS_TO_KELVIN_OFFSET
        return (
            FERRIC_TO_FERROUS_REFERENCE_V
            - (GAS_CONSTANT * T_K) / (
                FERRIC_TO_FERROUS_ELECTRONS * FARADAY
            ) * math.log(activity)
            + (GAS_CONSTANT * T_K) / (
                FERRIC_TO_FERROUS_ELECTRONS * FARADAY
            ) * FERRIC_TO_FERROUS_O2_PER_FE2O3 * math.log(pO2_activity)
        )

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
        melt_account_mol = melt_account_mol_from_kg(melt_state.composition_kg)
        feo_fraction = max(0.0, comp.get('FeO', 0.0)) / 100.0
        result = {
            'oxides_reduced_kg': {},
            'oxides_reduced_mol': {},
            'metals_produced_kg': {},
            'metals_produced_mol': {},
            'gas_products_produced_kg': {},
            'gas_products_produced_mol': {},
            'oxides_produced_kg': {},
            'oxides_produced_mol': {},
            'oxide_charge_electrons': {},
            'O2_produced_kg': 0.0,
            'O2_produced_mol': 0.0,
            'energy_kWh': 0.0,
            'mre_north_star_posture': MRE_NORTH_STAR_POSTURE,
            'mre_optional_banner': MRE_OPTIONAL_BANNER,
            'certification_evidence_class': MRE_CERTIFICATION_EVIDENCE_CLASS,
            'certification_allowed': False,
            'certification_denylist_reason': MRE_CERTIFICATION_DENYLIST_REASON,
            'current_partition_source': MRE_CURRENT_PARTITION_SOURCE,
            'current_partition_certified': False,
            'yield_certification': MRE_CURRENT_PARTITION_CERTIFICATION,
            'current_efficiency_model': CURRENT_EFFICIENCY_MODEL_ID,
            'current_efficiency_feo_fraction': feo_fraction,
            'current_efficiency_by_oxide': {},
            'mre_activity_model': 'gamma_x_single_cation',
            'mre_oxide_activity_by_oxide': {},
            'mre_decomposition_voltage_authority_by_oxide': {},
            'mre_decomposition_voltage_authoritative_by_oxide': {},
            'mre_decomposition_voltage_status_by_oxide': {},
            'mre_metal_product_phase_by_oxide': {},
            'mre_ellingham_phase_basis_by_oxide': {},
            'mre_raw_graph_requirement_V_by_oxide': {},
            'mre_raw_voltage_margin_V_by_oxide': {},
            'mre_raw_margin_refused_targets': {},
            'mre_phase_refused_targets': {},
        }

        # Find all reducible species at this voltage
        reducible = []
        for oxide in MRE_FIXED_REDUCIBLE_OXIDES:
            if oxide not in melt_state.composition_kg:
                continue
            if melt_state.composition_kg.get(oxide, 0.0) < 1e-6:
                continue

            activity = mre_oxide_activity(oxide, melt_account_mol)
            reference = mre_decomposition_voltage_reference(
                oxide,
                temperature_K=T_C + CELSIUS_TO_KELVIN_OFFSET,
            )
            result['mre_oxide_activity_by_oxide'][oxide] = activity
            if reference is not None:
                result['mre_decomposition_voltage_authority_by_oxide'][oxide] = (
                    reference.authority
                )
                result['mre_decomposition_voltage_authoritative_by_oxide'][oxide] = (
                    reference.authoritative
                )
                result['mre_decomposition_voltage_status_by_oxide'][oxide] = (
                    reference.status
                )
                result['mre_metal_product_phase_by_oxide'][oxide] = (
                    reference.metal_product_phase
                )
                result['mre_ellingham_phase_basis_by_oxide'][oxide] = (
                    reference.ellingham_phase_basis
                )
            E_nernst = self.nernst_voltage(
                oxide, T_C, activity, pO2_bar=pO2_bar)

            fallback_margin_V = voltage_V - E_nernst
            raw_margin_V = None
            if (
                reference is not None
                and reference.raw_graph_voltage_V is not None
            ):
                # The Nernst activity/pO2 shift is identical for the selected
                # fallback and raw graph E0.  Replacing E0 therefore shifts the
                # full requirement by exactly (E0_raw - E0_fallback).
                raw_requirement_V = (
                    E_nernst
                    + reference.raw_graph_voltage_V
                    - reference.voltage
                )
                raw_margin_V = voltage_V - raw_requirement_V
                result['mre_raw_graph_requirement_V_by_oxide'][oxide] = (
                    raw_requirement_V
                )
                result['mre_raw_voltage_margin_V_by_oxide'][oxide] = raw_margin_V

            if (
                fallback_margin_V > 0.0
                and oxide in MRE_RAW_MARGIN_GUARDED_OXIDES
                and reference is not None
                and not reference.authoritative
                and raw_margin_V is not None
                and raw_margin_V <= 0.0
            ):
                result['mre_raw_margin_refused_targets'][oxide] = {
                    'fallback_requirement_V': E_nernst,
                    'fallback_margin_V': fallback_margin_V,
                    'raw_requirement_V': raw_requirement_V,
                    'raw_margin_V': raw_margin_V,
                    'voltage_status': reference.status,
                }
                continue

            if fallback_margin_V > 0.0:
                product_phase = None if reference is None else reference.metal_product_phase
                if product_phase not in (
                    ELLINGHAM_METAL_PHASE_CONDENSED,
                    ELLINGHAM_METAL_PHASE_GAS,
                ):
                    result['mre_phase_refused_targets'][oxide] = {
                        'reason': 'mre_product_phase_missing_or_unknown',
                        'metal_product_phase': product_phase,
                        'voltage_status': None if reference is None else reference.status,
                    }
                    continue
                overvoltage = fallback_margin_V
                reducible.append((
                    oxide, E_nernst, overvoltage, activity,
                    "oxide_to_metal", reference,
                ))

        if melt_state.composition_kg.get('Fe2O3', 0.0) >= 1e-6:
            activity = mre_oxide_activity('Fe2O3', melt_account_mol)
            E_ferric = self.ferric_to_ferrous_voltage(
                T_C, activity, pO2_bar=pO2_bar)
            if E_ferric < voltage_V:
                reducible.append((
                    'Fe2O3',
                    E_ferric,
                    voltage_V - E_ferric,
                    activity,
                    "ferric_to_ferrous",
                    None,
                ))

        if not reducible:
            if voltage_V > 0.0 and current_A > 0.0:
                result['energy_kWh'] = voltage_V * current_A / 1000.0
            if result['mre_raw_margin_refused_targets']:
                result['reason_refused'] = MRE_RAW_MARGIN_REFUSAL
            if result['mre_phase_refused_targets']:
                result['reason_refused'] = 'mre_product_phase_mismatch_refused'
            return result

        refused_targets = uncertified_multi_oxide_partition_targets(reducible)
        if refused_targets:
            result['energy_kWh'] = voltage_V * current_A / 1000.0
            result['reason_refused'] = MRE_MULTI_OXIDE_PARTITION_REFUSAL
            result['reducible_oxide_targets'] = refused_targets
            return result

        # Partition current among reducible species            [SEL-1]
        # Weight by: concentration × exp(overvoltage)
        weights = {}
        billable_current_A = 0.0
        any_capped = False

        for oxide, E, dV, a, _mode, _reference in reducible:
            weights[oxide] = a * math.exp(min(dV, 3.0))

        total_weight = sum(weights.values())
        if total_weight <= 0:
            if voltage_V > 0.0 and current_A > 0.0:
                result['energy_kWh'] = voltage_V * current_A / 1000.0
            return result

        for oxide, E, dV, a, mode, reference in reducible:
            fraction = weights[oxide] / total_weight
            I_species = current_A * fraction

            # Current efficiency                                [CE-1]
            ce_diagnostic = current_efficiency_diagnostic(dV, feo_fraction)
            eta_CE = float(ce_diagnostic['eta'])
            result['current_efficiency_by_oxide'][oxide] = ce_diagnostic

            # Faraday's law: mass reduced this hour            [FARADAY-1]
            n = (
                FERRIC_TO_FERROUS_ELECTRONS
                if mode == "ferric_to_ferrous"
                else ELECTRONS_PER_OXIDE.get(oxide, 2)
            )
            M_oxide_gmol = MOLAR_MASS.get(oxide, 100.0)  # g/mol
            t_s = 3600.0  # 1 hour in seconds

            uncapped_moles_reduced = (I_species * eta_CE * t_s) / (n * FARADAY)
            kg_oxide_reduced = uncapped_moles_reduced * M_oxide_gmol / 1000.0  # g->kg

            # Don't reduce more than available
            available = melt_state.composition_kg.get(oxide, 0.0)
            if available < kg_oxide_reduced:
                any_capped = True
            kg_oxide_reduced = min(kg_oxide_reduced, available)
            moles_reduced = kg_oxide_reduced * 1000.0 / M_oxide_gmol
            species_cap_ratio = 0.0
            if uncapped_moles_reduced > 0.0:
                species_cap_ratio = min(1.0, moles_reduced / uncapped_moles_reduced)
            billable_current_A += I_species * species_cap_ratio

            if kg_oxide_reduced > 1e-10:
                result['oxides_reduced_kg'][oxide] = kg_oxide_reduced
                result['oxides_reduced_mol'][oxide] = moles_reduced
                result['oxide_charge_electrons'][oxide] = n

                if mode == "ferric_to_ferrous":
                    feo_mol = moles_reduced * FERRIC_TO_FERROUS_FEO_PER_FE2O3
                    feo_kg = feo_mol * MOLAR_MASS['FeO'] / 1000.0
                    result['oxides_produced_kg']['FeO'] = (
                        result['oxides_produced_kg'].get('FeO', 0.0) + feo_kg
                    )
                    result['oxides_produced_mol']['FeO'] = (
                        result['oxides_produced_mol'].get('FeO', 0.0) + feo_mol
                    )
                    O2_mol = moles_reduced * FERRIC_TO_FERROUS_O2_PER_FE2O3
                    O2_kg = O2_mol * MOLAR_MASS['O2'] / 1000.0
                    result['O2_produced_kg'] += O2_kg
                    result['O2_produced_mol'] += O2_mol
                    continue

                # Metal produced
                metal_info = OXIDE_TO_METAL.get(oxide)
                if metal_info:
                    metal, n_met, n_oxy = metal_info
                    M_metal_gmol = MOLAR_MASS[metal]  # g/mol
                    metal_mol = moles_reduced * n_met
                    metal_kg = metal_mol * M_metal_gmol / 1000.0
                    product_phase = (
                        ELLINGHAM_METAL_PHASE_CONDENSED
                        if reference is None
                        else reference.metal_product_phase
                    )
                    if product_phase == ELLINGHAM_METAL_PHASE_GAS:
                        result['gas_products_produced_kg'][metal] = (
                            result['gas_products_produced_kg'].get(metal, 0.0)
                            + metal_kg)
                        result['gas_products_produced_mol'][metal] = (
                            result['gas_products_produced_mol'].get(metal, 0.0)
                            + metal_mol)
                    else:
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

        # Energy consumed. Final depletion hours bill each species by its own
        # capped Faradaic share; uncapped species keep their full current share.
        # Preserve exact commanded energy when no cap bound.
        energy_current_A = billable_current_A if any_capped else current_A
        result['energy_kWh'] = voltage_V * energy_current_A * 1.0 / 1000.0

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
        melt_account_mol = melt_account_mol_from_kg(melt_state.composition_kg)
        sequence = []

        for oxide in MRE_FIXED_REDUCIBLE_OXIDES:
            if melt_state.composition_kg.get(oxide, 0.0) < 1e-6:
                continue
            activity = mre_oxide_activity(oxide, melt_account_mol)
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
        T_K = T_C + CELSIUS_TO_KELVIN_OFFSET
        total_energy = 0.0

        for oxide in MRE_FIXED_REDUCIBLE_OXIDES:
            reference = mre_decomposition_voltage_reference(oxide, temperature_K=T_K)
            if reference is None:
                continue
            E0 = reference.voltage
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
