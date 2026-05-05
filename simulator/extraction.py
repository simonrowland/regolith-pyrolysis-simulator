"""MRE, alkali-shuttle, and thermite helpers for PyrolysisSimulator."""

from __future__ import annotations

import math
from typing import Dict, Mapping

from simulator.state import (
    FARADAY,
    MOLAR_MASS,
    STOICH_RATIOS,
    CampaignPhase,
)


class ExtractionMixin:
    _LEDGER_KG_TOL = 1e-9

    @staticmethod
    def _positive_ledger_kg(
        species_kg: Mapping[str, float],
        tolerance_kg: float = _LEDGER_KG_TOL,
    ) -> Dict[str, float]:
        return {
            str(species): float(kg)
            for species, kg in species_kg.items()
            if float(kg) > tolerance_kg
        }

    @staticmethod
    def _positive_ledger_mol(
        species_mol: Mapping[str, float],
        tolerance_mol: float = 1e-12,
    ) -> Dict[str, float]:
        return {
            str(species): float(mol)
            for species, mol in species_mol.items()
            if float(mol) > tolerance_mol
        }

    def _ledger_account_species_kg(self, account: str, species: str) -> float:
        return max(
            0.0,
            float(self.atom_ledger.kg_by_account(account).get(species, 0.0)),
        )

    def _process_reagent_inventory_kg(self, species: str) -> float:
        return self._ledger_account_species_kg(
            'process.reagent_inventory', species)

    def _record_atom_transition(
        self,
        name: str,
        *,
        debits: tuple[tuple[str, Mapping[str, float]], ...],
        credits: tuple[tuple[str, Mapping[str, float]], ...],
        reason: str,
    ) -> bool:
        debit_lots = []
        for account, species_kg in debits:
            payload = self._positive_ledger_kg(species_kg)
            if payload:
                debit_lots.append(self.atom_ledger.debit(account, payload))

        credit_lots = []
        for account, species_kg in credits:
            payload = self._positive_ledger_kg(species_kg)
            if payload:
                credit_lots.append(self.atom_ledger.credit(account, payload))

        if not debit_lots and not credit_lots:
            return False
        self.atom_ledger.record(
            name,
            debits=tuple(debit_lots),
            credits=tuple(credit_lots),
            reason=reason,
        )
        return True

    def _record_atom_transition_mol(
        self,
        name: str,
        *,
        debits: tuple[tuple[str, Mapping[str, float]], ...],
        credits: tuple[tuple[str, Mapping[str, float]], ...],
        reason: str,
    ) -> bool:
        debit_lots = []
        for account, species_mol in debits:
            payload = self._positive_ledger_mol(species_mol)
            if payload:
                debit_lots.append(self.atom_ledger.debit_mol(account, payload))

        credit_lots = []
        for account, species_mol in credits:
            payload = self._positive_ledger_mol(species_mol)
            if payload:
                credit_lots.append(self.atom_ledger.credit_mol(account, payload))

        if not debit_lots and not credit_lots:
            return False
        self.atom_ledger.record(
            name,
            debits=tuple(debit_lots),
            credits=tuple(credit_lots),
            reason=reason,
        )
        return True

    def _move_ledger_species(
        self,
        name: str,
        debit_account: str,
        credit_account: str,
        species: str,
        kg: float,
        *,
        reason: str,
    ) -> float:
        kg = max(0.0, float(kg))
        if kg <= self._LEDGER_KG_TOL:
            return 0.0
        self.atom_ledger.move(
            name,
            debit_account,
            credit_account,
            {species: kg},
            reason=reason,
        )
        return kg

    def _draw_reagent_to_process(self, species: str, requested_kg: float) -> float:
        requested_kg = max(0.0, float(requested_kg))
        if requested_kg <= self._LEDGER_KG_TOL:
            return 0.0

        reservoir = f'reservoir.reagent.{species}'
        available_kg = self._ledger_account_species_kg(reservoir, species)
        draw_kg = min(requested_kg, available_kg)
        if draw_kg <= self._LEDGER_KG_TOL:
            return 0.0
        return self._move_ledger_species(
            f'draw_{species}_reagent_to_process',
            reservoir,
            'process.reagent_inventory',
            species,
            draw_kg,
            reason=f'{species} reagent draw from reservoir',
        )

    def _sync_reagent_counter_from_ledger(self, species: str) -> float:
        return self._process_reagent_inventory_kg(species)

    def _set_melt_species_projection(self, species: str, kg: float) -> None:
        self.melt.composition_kg.update({species: max(0.0, float(kg))})

    def _project_extraction_melt(self) -> None:
        self._project_cleaned_melt_from_atom_ledger()

    def _set_condensed_species_projection(
        self, stage_idx: int, species: str, kg: float
    ) -> None:
        self.train.stages[stage_idx].collected_kg.update(
            {species: max(0.0, float(kg))})

    def _project_condensed_species(
        self, stage_idx: int, species: str
    ) -> None:
        kg = self._ledger_account_species_kg(
            'process.condensation_train', species)
        for stage in self.train.stages:
            stage.collected_kg.pop(species, None)
        self._set_condensed_species_projection(stage_idx, species, kg)

    def _build_mre_voltage_sequence(self) -> list:
        """Build the stepped voltage hold sequence from setpoints.yaml."""
        # Try to load from setpoints
        mre_seq = self.setpoints.get('mre_voltage_sequence', {})
        if isinstance(mre_seq, dict):
            # The setpoints has it under a nested structure
            pass
        # Hard-coded default matching the Ellingham decomposition ladder
        return [
            {'voltage': 0.6, 'species': ['FeO'], 'min_hold_hours': 3},
            {'voltage': 0.9, 'species': ['Cr2O3'], 'min_hold_hours': 2},
            {'voltage': 1.0, 'species': ['MnO'], 'min_hold_hours': 2},
            {'voltage': 1.4, 'species': ['SiO2'], 'min_hold_hours': 5},
            {'voltage': 1.5, 'species': ['TiO2'], 'min_hold_hours': 3},
            {'voltage': 1.9, 'species': ['Al2O3'], 'min_hold_hours': 8},
            {'voltage': 2.2, 'species': ['MgO'], 'min_hold_hours': 5},
            {'voltage': 2.5, 'species': ['CaO'], 'min_hold_hours': 10},
        ]

    def _step_mre(self) -> float:
        """
        Perform one hour of molten regolith electrolysis (C5 or MRE baseline).

        Voltage strategy:
            C5 (limited MRE):    Stepped holds at Ellingham thresholds up to 1.6 V.
                                 Extracts FeO, SiO₂, TiO₂ but NOT Al₂O₃/MgO/CaO.
                                 Electrode life 5-10× longer than full MRE.

            MRE_BASELINE:        Stepped holds at each Ellingham threshold (0.6→2.5 V).
                                 Each species substantially extracted before advancing.
                                 Higher current (3000 A) for faster throughput.

        Returns O₂ produced this hour (kg).
        """
        from simulator.electrolysis import ELECTRONS_PER_OXIDE

        # --- Voltage and current selection (stepped holds) ---         [Step 9]
        if self.melt.campaign == CampaignPhase.MRE_BASELINE:
            seq = self._mre_voltage_sequence
            if not seq:
                # Fallback if sequence not loaded
                voltage_V = min(0.6 + self.melt.campaign_hour * 0.1, 2.5)
            else:
                idx = min(self._mre_voltage_step_idx, len(seq) - 1)
                step_info = seq[idx]
                voltage_V = step_info['voltage']

                self._mre_hold_hours += 1

                # Advance to next voltage step when target species depleted
                if (self._mre_hold_hours >= step_info.get('min_hold_hours', 3)
                        and idx < len(seq) - 1):
                    target_current_low = (
                        self._mre_effective_current_A < 3000.0 * 0.05)
                    if target_current_low:
                        self._mre_voltage_step_idx += 1
                        self._mre_hold_hours = 0

            current_A = 3000.0  # Full-scale MRE: ~60 kA/m² at 0.05 m²
        else:
            # C5 limited MRE: stepped holds up to 1.6 V
            seq = [s for s in self._mre_voltage_sequence
                   if s['voltage'] <= 1.6]
            if not seq:
                voltage_V = 1.6
            else:
                idx = min(self._mre_voltage_step_idx, len(seq) - 1)
                step_info = seq[idx]
                voltage_V = step_info['voltage']

                self._mre_hold_hours += 1
                if (self._mre_hold_hours >= step_info.get('min_hold_hours', 3)
                        and idx < len(seq) - 1):
                    target_current_low = (
                        self._mre_effective_current_A < 100.0 * 0.05)
                    if target_current_low:
                        self._mre_voltage_step_idx += 1
                        self._mre_hold_hours = 0

            current_A = 100.0

        result = self.electrolysis_model.step_hour(
            melt_state=self.melt,
            voltage_V=voltage_V,
            current_A=current_A,
            T_C=self.melt.temperature_C,
        )
        self._record_mre_ledger_transition(result)

        # Route cathode metals to condenser stages (product ledger).    [Step 4]
        # Same pattern as C6 thermite — condenser train serves as the
        # product accumulator for all extraction methods.
        MRE_METAL_STAGE = {
            'Fe': 1, 'Cr': 1, 'Mn': 1, 'Al': 1,  # Dense metals → Stage 1
            'Si': 2, 'Ti': 2,                       # Si/Ti → Stage 2
            'Mg': 3, 'Ca': 3, 'Na': 3, 'K': 3,     # Light metals → Stage 3
        }
        for metal, kg_produced in result.get('metals_produced_kg', {}).items():
            if kg_produced > 1e-10:
                stage_idx = MRE_METAL_STAGE.get(metal, 1)
                self._project_condensed_species(stage_idx, metal)

        self._mre_metals_this_hr = dict(result.get('metals_produced_kg', {}))

        self._project_extraction_melt()

        # Route anodic O₂ to Stage 6 accumulator (mass balance).       [Step 5]
        O2_kg = max(0.0, float(result.get('O2_produced_kg', 0.0)))
        self._sync_oxygen_kg_counters()

        # Store energy for EnergyTracker (don't add to cumulative).    [Step 6]
        self._mre_energy_this_hr = result.get('energy_kWh', 0.0)

        # Store voltage/current for snapshot                            [Step 7]
        self._mre_voltage_V = voltage_V
        self._mre_current_A = current_A

        # Calculate effective current from actual Faradaic reduction.   [Step 8]
        total_charge_C = 0.0
        for oxide, kg_removed in result.get('oxides_reduced_kg', {}).items():
            n_e = ELECTRONS_PER_OXIDE.get(oxide, 2)
            M_ox = MOLAR_MASS.get(oxide, 100.0)
            moles_ox = kg_removed * 1000.0 / M_ox
            total_charge_C += moles_ox * n_e * FARADAY
        self._mre_effective_current_A = total_charge_C / 3600.0

        # Store effective current on melt state for endpoint detection
        self.melt.mre_voltage_V = voltage_V
        self.melt.mre_current_A = self._mre_effective_current_A

        return O2_kg

    def _record_mre_ledger_transition(self, result: Mapping[str, object]) -> None:
        oxide_mol = self._positive_ledger_mol(
            result.get('oxides_reduced_mol', {}))
        if oxide_mol:
            metal_mol = self._positive_ledger_mol(
                result.get('metals_produced_mol', {}))
            O2_mol = max(0.0, float(result.get('O2_produced_mol', 0.0)))
            credits_mol: list[tuple[str, Mapping[str, float]]] = []
            if metal_mol:
                credits_mol.append(('process.condensation_train', metal_mol))
            if O2_mol > 1e-12:
                credits_mol.append(
                    ('terminal.oxygen_mre_anode_stored', {'O2': O2_mol}))
            self._record_atom_transition_mol(
                'mre_electrolysis_reduction',
                debits=(('process.cleaned_melt', oxide_mol),),
                credits=tuple(credits_mol),
                reason='MRE oxide reduction to cathode products and oxygen',
            )
            return

        oxides = self._positive_ledger_kg(
            result.get('oxides_reduced_kg', {}))
        metals = self._positive_ledger_kg(
            result.get('metals_produced_kg', {}))
        O2_kg = max(0.0, float(result.get('O2_produced_kg', 0.0)))
        if not oxides:
            return

        credits: list[tuple[str, Mapping[str, float]]] = []
        if metals:
            credits.append(('process.condensation_train', metals))
        if O2_kg > self._LEDGER_KG_TOL:
            credits.append(('terminal.oxygen_mre_anode_stored', {'O2': O2_kg}))

        self._record_atom_transition(
            'mre_electrolysis_reduction',
            debits=(('process.cleaned_melt', oxides),),
            credits=tuple(credits),
            reason='MRE oxide reduction to cathode products and oxygen',
        )

    # ------------------------------------------------------------------
    # Alkali Shuttle (C3) — Metallothermic Reduction            [THERMO-5]
    # ------------------------------------------------------------------

    def _init_shuttle_inventory(self, campaign: CampaignPhase):
        """
        Initialize shuttle inventory when entering a C3 phase.

        K and Na are sourced primarily from user-supplied inventory
        (additives), not self-bootstrapped from the batch.  In a
        running refinery, the shuttle reagents circulate: K/Na injected
        into the melt are recovered during bakeout and recycled.  The
        initial charge comes from inventory.

        Any K/Na that happened to condense in earlier campaigns
        (evaporated from the melt's own Na₂O/K₂O during C0/C2) is
        also collected as a bonus — checked across ALL condenser stages
        since Na/K may condense in Stage 4 (200-350°C) rather than
        Stage 3 (350-700°C) depending on the condensation model.

        Called once at the start of C3_K and C3_NA phases.
        """
        if campaign == CampaignPhase.C3_K:
            self._activated_additive_reagents.add('K')
            self._draw_reagent_to_process(
                'K', self.record.additives_kg.get('K', 0.0))
            self._transfer_condensed_species('K')
            self.shuttle_K_inventory_kg = self._sync_reagent_counter_from_ledger('K')
            self.shuttle_cycle_K = 0

        elif campaign == CampaignPhase.C3_NA:
            self._activated_additive_reagents.add('Na')
            self._draw_reagent_to_process(
                'Na', self.record.additives_kg.get('Na', 0.0))
            self._transfer_condensed_species('Na')
            self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')
            self.shuttle_cycle_Na = 0

    def _step_shuttle(self):
        """
        Perform one hour of alkali metallothermic shuttle processing.

        The C3 campaign alternates between injection and bakeout sub-phases
        on a 6-hour cycle (3 hrs inject, 3 hrs bakeout):

        **Injection** (T ~1200-1350°C):                          [THERMO-5]
            K phase:  2K(g) + FeO(melt) → K₂O(melt) + Fe(l)
                      4K(g) + SiO₂(melt) → 2K₂O(melt) + Si(l)  [conditioning]
            Na phase: 2Na(g) + TiO₂(melt) → Na₂O(melt) + Ti(l)
                      6Na(g) + Cr₂O₃(melt) → 3Na₂O(melt) + 2Cr(l)

        **Bakeout** (T ~1520-1680°C, pO₂ 0.5-1.5 mbar):        [THERMO-6]
            K₂O(melt) → 2K(g) + ½O₂(g)
            Na₂O(melt) → 2Na(g) + ½O₂(g)
            Recovery: 75-92% per cycle.
            K/Na vapor recondenses in Stage 3 → recycled.

        The normal evaporation model handles bakeout (K/Na have vapor
        pressure >> pO₂ at 1600°C).  This method handles the injection
        chemistry — adding alkali oxide to the melt and reducing target
        oxides to liquid metal.

        Key constraint: Na₂O/K₂O slag solubility is 8-12 wt% per cycle.
        """
        # Reset per-hour tracking
        self._shuttle_injected_this_hr = 0.0
        self._shuttle_reduced_this_hr = 0.0
        self._shuttle_metal_this_hr = 0.0

        campaign = self.melt.campaign
        cycle_period = 6  # hours per inject-bakeout cycle
        is_injection = (self.melt.campaign_hour % cycle_period) < 3

        if is_injection:
            self._shuttle_phase = 'inject'
            if campaign == CampaignPhase.C3_K:
                self._shuttle_inject_K()
            elif campaign == CampaignPhase.C3_NA:
                self._shuttle_inject_Na()
        else:
            self._shuttle_phase = 'bakeout'
            # Bakeout is handled by normal evaporation (K/Na have high
            # vapor pressure at 1520-1680°C).  Track cycle transitions.
            if self.melt.campaign_hour % cycle_period == 3:
                # Just entered bakeout — increment cycle counter
                if campaign == CampaignPhase.C3_K:
                    self.shuttle_cycle_K += 1
                elif campaign == CampaignPhase.C3_NA:
                    self.shuttle_cycle_Na += 1

    def _shuttle_inject_K(self):
        """
        K-shuttle injection: reduce FeO (primary) + condition SiO₂.

        Reaction:  2K + FeO → K₂O + Fe(l)                      [THERMO-5]
        Stoichiometry:
            78.20 g K + 71.84 g FeO → 94.20 g K₂O + 55.85 g Fe
            1 kg K → 0.919 kg FeO reduced
                   → 1.205 kg K₂O dissolved
                   → 0.714 kg Fe produced

        K₂O solubility limit: 8-12 wt% in the silicate melt.
        K injection spread over 3 injection hours per cycle.
        """
        if self.shuttle_K_inventory_kg <= 0.01:
            return  # No K available

        # --- Solubility check ---
        # K₂O already in melt + what we'd add must stay < 10 wt% (midpoint)
        K2O_SOLUBILITY_WT_PCT = 10.0
        comp_wt = self.melt.composition_wt_pct()
        K2O_current_pct = comp_wt.get('K2O', 0.0)
        if K2O_current_pct >= K2O_SOLUBILITY_WT_PCT:
            return  # Melt saturated in K₂O — wait for bakeout

        # How much K₂O can we add before hitting the limit?
        # K₂O_max = total_melt × solubility_fraction - K₂O_current
        total_melt = self.melt.total_mass_kg
        K2O_max_kg = (total_melt * K2O_SOLUBILITY_WT_PCT / 100.0
                      - self.melt.composition_kg.get('K2O', 0.0))
        K2O_max_kg = max(0.0, K2O_max_kg)

        # Convert K₂O capacity to K capacity: 1 kg K₂O ← 0.831 kg K
        # (2 × 39.10 / 94.20 = 0.830)
        K_for_K2O_limit_kg = K2O_max_kg * (2 * MOLAR_MASS['K'] / MOLAR_MASS['K2O'])

        # --- FeO available ---
        FeO_available = self.melt.composition_kg.get('FeO', 0.0)
        # 1 kg K reduces 0.919 kg FeO
        K_for_FeO_kg = FeO_available / (MOLAR_MASS['FeO'] / (2 * MOLAR_MASS['K']))

        # --- K injection this hour ---
        # Spread injection over 3 hours per cycle
        # Use up to 1/3 of available K per injection hour
        K_available_this_hr = self.shuttle_K_inventory_kg / 3.0

        # Take the minimum of all constraints
        K_inject = min(K_available_this_hr, K_for_K2O_limit_kg, K_for_FeO_kg)
        K_inject = max(0.0, K_inject)

        if K_inject < 0.001:
            return

        # --- Stoichiometric conversion ---
        # Primarily reduce FeO (thermodynamically preferred)       [THERMO-3]
        # K₂O is less stable (ΔG°f –320) than FeO (–370), but the
        # very low activity coefficient of K₂O in the silicate melt
        # (γ ~10⁻², shift ~50-80 kJ/mol) makes K → FeO reduction
        # thermodynamically accessible.

        # Molar quantities
        mol_K = K_inject / MOLAR_MASS['K'] * 1000.0  # g→mol
        mol_FeO_available = (FeO_available / MOLAR_MASS['FeO']) * 1000.0

        # Reaction: 2K + FeO → K₂O + Fe
        mol_FeO_reduced = min(mol_K / 2.0, mol_FeO_available)
        mol_K_used = mol_FeO_reduced * 2.0

        # Mass changes
        K_used_kg = (mol_K_used * MOLAR_MASS['K']) / 1000.0
        FeO_removed_kg = (mol_FeO_reduced * MOLAR_MASS['FeO']) / 1000.0
        K2O_added_kg = (mol_FeO_reduced * MOLAR_MASS['K2O']) / 1000.0
        Fe_produced_kg = (mol_FeO_reduced * MOLAR_MASS['Fe']) / 1000.0

        self._record_atom_transition(
            'c3_k_shuttle_fe_reduction',
            debits=(
                ('process.reagent_inventory', {'K': K_used_kg}),
                ('process.cleaned_melt', {'FeO': FeO_removed_kg}),
            ),
            credits=(
                ('process.cleaned_melt', {'K2O': K2O_added_kg}),
                ('process.condensation_train', {'Fe': Fe_produced_kg}),
            ),
            reason='K shuttle reduction of FeO',
        )

        # Fe produced goes to condenser Stage 1 (liquid Fe drains to sump)
        self._project_condensed_species(1, 'Fe')

        # Deduct K from shuttle inventory
        # (K comes from additives, not from a condenser stage)
        self.shuttle_K_inventory_kg = self._sync_reagent_counter_from_ledger('K')

        self._project_extraction_melt()

        # Track for snapshot
        self._shuttle_injected_this_hr = K_used_kg
        self._shuttle_reduced_this_hr = FeO_removed_kg
        self._shuttle_metal_this_hr = Fe_produced_kg

    def _shuttle_inject_Na(self):
        """
        Na-shuttle injection: reduce TiO₂ (primary) + Cr₂O₃.

        Reactions:                                               [THERMO-5]
            2Na + TiO₂ → Na₂O + Ti(l)   [accessibility uncertain]
            6Na + Cr₂O₃ → 3Na₂O + 2Cr(l)

        Stoichiometry (TiO₂ reaction):
            45.98 g Na + 79.87 g TiO₂ → 61.98 g Na₂O + 47.87 g Ti
            1 kg Na → 1.737 kg TiO₂ reduced
                    → 1.348 kg Na₂O dissolved
                    → 1.041 kg Ti produced

        Na₂O solubility limit: 8-12 wt% in the silicate melt.
        Activity coefficient γ(Na₂O) ≈ 10⁻² to 10⁻³ in CMAS.    [THERMO-10]
        """
        if self.shuttle_Na_inventory_kg <= 0.01:
            return

        # --- Solubility check ---
        Na2O_SOLUBILITY_WT_PCT = 10.0
        comp_wt = self.melt.composition_wt_pct()
        Na2O_current_pct = comp_wt.get('Na2O', 0.0)
        if Na2O_current_pct >= Na2O_SOLUBILITY_WT_PCT:
            return

        total_melt = self.melt.total_mass_kg
        Na2O_max_kg = (total_melt * Na2O_SOLUBILITY_WT_PCT / 100.0
                       - self.melt.composition_kg.get('Na2O', 0.0))
        Na2O_max_kg = max(0.0, Na2O_max_kg)
        Na_for_Na2O_limit_kg = Na2O_max_kg * (2 * MOLAR_MASS['Na'] / MOLAR_MASS['Na2O'])

        # --- Available targets ---
        TiO2_available = self.melt.composition_kg.get('TiO2', 0.0)
        Cr2O3_available = self.melt.composition_kg.get('Cr2O3', 0.0)

        # Na injection this hour (spread over 3 hrs)
        Na_available_this_hr = self.shuttle_Na_inventory_kg / 3.0
        Na_inject = min(Na_available_this_hr, Na_for_Na2O_limit_kg)
        Na_inject = max(0.0, Na_inject)

        if Na_inject < 0.001:
            return

        mol_Na = Na_inject / MOLAR_MASS['Na'] * 1000.0

        total_Na2O_added = 0.0
        total_metal_produced = 0.0
        total_oxide_reduced = 0.0
        Na_used = 0.0

        # --- First reduce Cr₂O₃ (easier: ΔG°f –500 vs Na₂O –320) ---
        if Cr2O3_available > 0.01 and mol_Na > 0.1:
            mol_Cr2O3 = (Cr2O3_available / MOLAR_MASS['Cr2O3']) * 1000.0
            # 6Na + Cr₂O₃ → 3Na₂O + 2Cr
            mol_Cr2O3_reduced = min(mol_Na / 6.0, mol_Cr2O3)
            mol_Na_for_Cr = mol_Cr2O3_reduced * 6.0

            Cr2O3_removed = (mol_Cr2O3_reduced * MOLAR_MASS['Cr2O3']) / 1000.0
            Na2O_from_Cr = (mol_Cr2O3_reduced * 3 * MOLAR_MASS['Na2O']) / 1000.0
            Cr_produced = (mol_Cr2O3_reduced * 2 * MOLAR_MASS['Cr']) / 1000.0

            self._record_atom_transition(
                'c3_na_shuttle_cr_reduction',
                debits=(
                    ('process.reagent_inventory', {'Na': (
                        mol_Na_for_Cr * MOLAR_MASS['Na']) / 1000.0}),
                    ('process.cleaned_melt', {'Cr2O3': Cr2O3_removed}),
                ),
                credits=(
                    ('process.cleaned_melt', {'Na2O': Na2O_from_Cr}),
                    ('process.condensation_train', {'Cr': Cr_produced}),
                ),
                reason='Na shuttle reduction of Cr2O3',
            )

            self._project_condensed_species(1, 'Cr')

            mol_Na -= mol_Na_for_Cr
            Na_used += (mol_Na_for_Cr * MOLAR_MASS['Na']) / 1000.0
            total_Na2O_added += Na2O_from_Cr
            total_metal_produced += Cr_produced
            total_oxide_reduced += Cr2O3_removed

        # --- Then reduce TiO₂ (harder: ΔG°f –580, uncertain access) ---
        # Apply 75% reduction efficiency to account for accessibility
        # uncertainty (the highest-priority experimental question)  [THERMO-10]
        TI_ACCESSIBILITY = 0.75
        if TiO2_available > 0.01 and mol_Na > 0.1:
            mol_TiO2 = (TiO2_available / MOLAR_MASS['TiO2']) * 1000.0
            # 2Na + TiO₂ → Na₂O + Ti (simplified; actually needs 4Na for full reduction)
            # Actually: TiO₂ has 2 oxygens, needs 4Na to fully reduce:
            # 4Na + TiO₂ → 2Na₂O + Ti
            mol_TiO2_accessible = mol_TiO2 * TI_ACCESSIBILITY
            mol_TiO2_reduced = min(mol_Na / 4.0, mol_TiO2_accessible)
            mol_Na_for_Ti = mol_TiO2_reduced * 4.0

            TiO2_removed = (mol_TiO2_reduced * MOLAR_MASS['TiO2']) / 1000.0
            Na2O_from_Ti = (mol_TiO2_reduced * 2 * MOLAR_MASS['Na2O']) / 1000.0
            Ti_produced = (mol_TiO2_reduced * MOLAR_MASS['Ti']) / 1000.0

            self._record_atom_transition(
                'c3_na_shuttle_ti_reduction',
                debits=(
                    ('process.reagent_inventory', {'Na': (
                        mol_Na_for_Ti * MOLAR_MASS['Na']) / 1000.0}),
                    ('process.cleaned_melt', {'TiO2': TiO2_removed}),
                ),
                credits=(
                    ('process.cleaned_melt', {'Na2O': Na2O_from_Ti}),
                    ('process.condensation_train', {'Ti': Ti_produced}),
                ),
                reason='Na shuttle reduction of TiO2',
            )

            self._project_condensed_species(1, 'Ti')

            mol_Na -= mol_Na_for_Ti
            Na_used += (mol_Na_for_Ti * MOLAR_MASS['Na']) / 1000.0
            total_Na2O_added += Na2O_from_Ti
            total_metal_produced += Ti_produced
            total_oxide_reduced += TiO2_removed

        # Deduct Na from shuttle inventory
        # (Na comes from additives, not from a condenser stage)
        self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')

        self._project_extraction_melt()

        # Track for snapshot
        self._shuttle_injected_this_hr = Na_used
        self._shuttle_reduced_this_hr = total_oxide_reduced
        self._shuttle_metal_this_hr = total_metal_produced

    # ------------------------------------------------------------------
    # Mg Thermite Reduction (C6)                                [THERMO-7]
    # ------------------------------------------------------------------

    def _init_thermite_inventory(self):
        """
        Initialize Mg inventory for C6 thermite reduction.

        Mg is sourced from:
        1. User-supplied additives (primary source)
        2. Any Mg condensed during C4 (bonus — recovered from condenser)

        Typical requirement: ~50-60 kg Mg for 1000 kg batch
        (stoichiometric: 3 mol Mg per mol Al₂O₃, with losses).
        """
        self._activated_additive_reagents.add('Mg')
        self._draw_reagent_to_process(
            'Mg', self.record.additives_kg.get('Mg', 0.0))
        self._transfer_condensed_species('Mg')
        self.thermite_Mg_inventory_kg = self._sync_reagent_counter_from_ledger('Mg')

    def _transfer_condensed_species(self, species: str) -> float:
        """Move recovered condensate into reagent inventory exactly once."""
        source_account = 'process.condensation_train'
        recovered_kg = self._ledger_account_species_kg(
            source_account, species)
        if recovered_kg <= self._LEDGER_KG_TOL:
            return 0.0
        self._move_ledger_species(
            f'recover_{species}_to_reagent_inventory',
            source_account,
            'process.reagent_inventory',
            species,
            recovered_kg,
            reason=f'recovered {species} condensate transfer',
        )
        for stage in self.train.stages:
            kg = stage.collected_kg.get(species, 0.0)
            if kg > 0.0:
                stage.collected_kg.update({species: 0.0})
        if species == 'K':
            self.shuttle_K_inventory_kg = self._sync_reagent_counter_from_ledger('K')
        elif species == 'Na':
            self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')
        elif species == 'Mg':
            self.thermite_Mg_inventory_kg = self._sync_reagent_counter_from_ledger('Mg')
        return recovered_kg

    def _step_thermite(self):
        """
        Perform one hour of Mg thermite reduction (C6).

        Primary reaction:                                       [THERMO-7]
            3Mg(l) + Al₂O₃(melt) → 3MgO(slag) + 2Al(l)

        Stoichiometry:
            72.93 g Mg + 101.96 g Al₂O₃ → 120.90 g MgO + 53.96 g Al
            1 kg Mg → 1.398 kg Al₂O₃ reduced
                    → 1.657 kg MgO produced
                    → 0.740 kg Al produced

        Back-reduction cascade (when Al contacts residual SiO₂): [THERMO-8]
            4Al(l) + 3SiO₂(melt) → 2Al₂O₃(melt) + 3Si(l)
            This consumes some Al but produces Si and regenerates Al₂O₃.
            Net effect: limited total Al yield from high-SiO₂ melts.
            We model ~30% of freshly produced Al back-reacting with SiO₂.

        Kinetics:
            The thermite reaction is fast (exothermic, ΔH ≈ -1350 kJ/mol Al₂O₃).
            Rate limited by Mg delivery (liquid Mg injected into hot melt)
            and mass transport in the increasingly MgO-rich slag.
            Modelled as consuming a fraction of available Mg per hour,
            decreasing as MgO accumulates (slag viscosity rises).

        Products:
            - Al metal → collected in condenser Stage 1 (liquid metal sump)
            - Si metal → collected in condenser Stage 2 (if back-reduction occurs)
            - MgO remains in the melt/slag
        """
        self._thermite_Al2O3_reduced_this_hr = 0.0
        self._thermite_Al_produced_this_hr = 0.0
        self._thermite_Mg_consumed_this_hr = 0.0

        if self.thermite_Mg_inventory_kg <= 0.01:
            return  # No Mg available

        Al2O3_available = self.melt.composition_kg.get('Al2O3', 0.0)
        if Al2O3_available < 0.01:
            return  # Nothing to reduce

        # --- Kinetic rate model ---
        # Mg injection rate decreases as MgO accumulates in the slag.
        # At start: up to 20% of remaining Mg per hour
        # As MgO builds up: rate decreases (higher slag viscosity)
        comp_wt = self.melt.composition_wt_pct()
        MgO_pct = comp_wt.get('MgO', 0.0)

        # Rate factor: drops as MgO increases (starts high, decays)
        # At 0% MgO → rate_factor = 0.20 (20% of inventory/hr)
        # At 30% MgO → rate_factor ≈ 0.05 (slag getting stiff)
        # At 50% MgO → rate_factor ≈ 0.01 (nearly frozen)
        rate_factor = 0.20 * math.exp(-0.05 * MgO_pct)
        rate_factor = max(0.01, min(0.25, rate_factor))

        Mg_available_this_hr = self.thermite_Mg_inventory_kg * rate_factor

        # --- Stoichiometric constraints ---
        # 3Mg + Al₂O₃ → 3MgO + 2Al
        # Moles: 3 mol Mg per 1 mol Al₂O₃
        mol_Mg = Mg_available_this_hr / MOLAR_MASS['Mg'] * 1000.0  # g → mol
        mol_Al2O3_available = (Al2O3_available / MOLAR_MASS['Al2O3']) * 1000.0

        # Mg is the limiting reagent (3 mol Mg per mol Al₂O₃)
        mol_Al2O3_reduced = min(mol_Mg / 3.0, mol_Al2O3_available)
        mol_Mg_used = mol_Al2O3_reduced * 3.0

        if mol_Al2O3_reduced < 0.001:
            return

        # --- Mass changes ---
        Mg_consumed_kg = (mol_Mg_used * MOLAR_MASS['Mg']) / 1000.0
        Al2O3_removed_kg = (mol_Al2O3_reduced * MOLAR_MASS['Al2O3']) / 1000.0
        MgO_produced_kg = (mol_Al2O3_reduced * 3 * MOLAR_MASS['MgO']) / 1000.0
        Al_produced_kg = (mol_Al2O3_reduced * 2 * MOLAR_MASS['Al']) / 1000.0

        self._record_atom_transition(
            'c6_mg_thermite_primary',
            debits=(
                ('process.reagent_inventory', {'Mg': Mg_consumed_kg}),
                ('process.cleaned_melt', {'Al2O3': Al2O3_removed_kg}),
            ),
            credits=(
                ('process.cleaned_melt', {'MgO': MgO_produced_kg}),
                ('process.condensation_train', {'Al': Al_produced_kg}),
            ),
            reason='Mg thermite reduction of Al2O3',
        )

        # --- Back-reduction cascade (Al + SiO₂) ---            [THERMO-8]
        # ~30% of freshly produced Al reacts with residual SiO₂:
        #   4Al + 3SiO₂ → 2Al₂O₃ + 3Si
        BACK_REDUCTION_FRACTION = 0.30
        SiO2_available = self.melt.composition_kg.get('SiO2', 0.0)
        if SiO2_available > 0.1 and Al_produced_kg > 0.01:
            mol_Al_for_back = (Al_produced_kg * BACK_REDUCTION_FRACTION
                               / MOLAR_MASS['Al'] * 1000.0)
            mol_SiO2_available = (SiO2_available / MOLAR_MASS['SiO2']) * 1000.0
            # 4Al + 3SiO₂ → 2Al₂O₃ + 3Si
            mol_SiO2_consumed = min(mol_Al_for_back * 3.0 / 4.0, mol_SiO2_available)
            mol_Al_consumed = mol_SiO2_consumed * 4.0 / 3.0

            SiO2_consumed_kg = (mol_SiO2_consumed * MOLAR_MASS['SiO2']) / 1000.0
            Al2O3_regenerated_kg = (mol_SiO2_consumed * 2.0 / 3.0
                                    * MOLAR_MASS['Al2O3']) / 1000.0
            Si_produced_kg = (mol_SiO2_consumed * MOLAR_MASS['Si']) / 1000.0
            Al_lost_to_back_kg = (mol_Al_consumed * MOLAR_MASS['Al']) / 1000.0

            self._record_atom_transition(
                'c6_al_si_back_reduction',
                debits=(
                    ('process.condensation_train', {'Al': Al_lost_to_back_kg}),
                    ('process.cleaned_melt', {'SiO2': SiO2_consumed_kg}),
                ),
                credits=(
                    ('process.cleaned_melt', {'Al2O3': Al2O3_regenerated_kg}),
                    ('process.condensation_train', {'Si': Si_produced_kg}),
                ),
                reason='Al back-reduction of SiO2 during thermite',
            )

            # Si product → condenser Stage 2
            self._project_condensed_species(2, 'Si')

            # Net Al after back-reduction
            Al_produced_kg -= Al_lost_to_back_kg

            # Net Al₂O₃ removal (primary minus regenerated)
            Al2O3_removed_kg -= Al2O3_regenerated_kg

        # Al product → condenser Stage 1 (liquid metal sump)
        self._project_condensed_species(1, 'Al')

        # Deduct Mg from thermite inventory
        self.thermite_Mg_inventory_kg = self._sync_reagent_counter_from_ledger('Mg')

        self._project_extraction_melt()

        # Track for snapshot / summary
        self._thermite_Al2O3_reduced_this_hr = max(0.0, Al2O3_removed_kg)
        self._thermite_Al_produced_this_hr = max(0.0, Al_produced_kg)
        self._thermite_Mg_consumed_this_hr = Mg_consumed_kg

    # ------------------------------------------------------------------
    # Equipment spec helpers
    # ------------------------------------------------------------------
