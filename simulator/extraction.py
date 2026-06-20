"""MRE, alkali-shuttle, and thermite helpers for PyrolysisSimulator."""

from __future__ import annotations

import math
from typing import Dict

import simulator.mre_ladder as mre_ladder
from simulator.accounting.queries import AccountingQueries
from simulator.condensation_routing import product_stage_number
from simulator.state import (
    FARADAY,
    MOLAR_MASS,
    CampaignPhase,
)


class ExtractionMixin:
    _LEDGER_KG_TOL = 1e-9
    _RUMP_EXPECTATION_TOL_KG = 1e-6
    _RUMP_ELEMENT_SPECIES = {
        'Si': ('SiO2',),
        'Al': ('Al2O3',),
        'Mg': ('MgO',),
        'Ca': ('CaO',),
        'Ti': ('TiO2',),
        'REE': ('REE_oxides',),
    }
    _C5_BRANCH_ONE_TARGET_CANDIDATES = frozenset({'Si', 'Al', 'Mg', 'Ca'})

    # ``_positive_ledger_kg`` and ``_positive_ledger_mol`` were removed
    # alongside ``_record_atom_transition`` when the METALLOTHERMIC_STEP
    # flip (``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) 6/7) replaced
    # their only call sites with ``ChemistryKernel.commit_batch``.

    def _ledger_account_species_kg(self, account: str, species: str) -> float:
        return max(
            0.0,
            float(self.atom_ledger.kg_by_account(account).get(species, 0.0)),
        )

    def _cleaned_melt_available_mol_by_species(
        self,
        species_names,
    ) -> dict[str, float]:
        cleaned_melt = self.atom_ledger.mol_by_account('process.cleaned_melt')
        return {
            str(species): max(0.0, float(cleaned_melt.get(species, 0.0)))
            for species in species_names
        }

    def _process_reagent_inventory_kg(self, species: str) -> float:
        return self._ledger_account_species_kg(
            'process.reagent_inventory', species)

    def _rump_element_kg(self, element: str) -> float:
        return AccountingQueries(self).rump_element_kg(element)

    def _initial_rump_element_kg(self, element: str) -> float:
        species_names = self._RUMP_ELEMENT_SPECIES.get(element, ())
        initial_inventory = getattr(self.record, 'initial_inventory', None)
        sources = []
        if initial_inventory is not None:
            sources.extend((
                getattr(initial_inventory, 'melt_oxide_kg', {}),
                getattr(initial_inventory, 'terminal_slag_components_kg', {}),
            ))
        sources.append(getattr(self, '_campaign_start_composition', {}))
        total = 0.0
        for source in sources:
            for species in species_names:
                total += max(0.0, float(source.get(species, 0.0)))
        return total

    def _actual_rump_elements_kg(self) -> Dict[str, float]:
        return AccountingQueries(self).actual_rump_elements_kg()

    def _normalise_c5_target_elements(self, targets) -> set[str]:
        if targets is None:
            return set()
        if isinstance(targets, str):
            target_items = [targets]
        else:
            target_items = list(targets)

        normalised: set[str] = set()
        for item in target_items:
            text = str(item)
            for element, species_names in self._RUMP_ELEMENT_SPECIES.items():
                if element in text or any(species in text for species in species_names):
                    normalised.add(element)
        return normalised & self._C5_BRANCH_ONE_TARGET_CANDIDATES

    def _configured_c5_target_elements(self) -> set[str]:
        campaigns = self.setpoints.get('campaigns', {}) or {}
        c5_cfg = campaigns.get('C5', {}) or {}
        if not isinstance(c5_cfg, dict):
            c5_cfg = {}

        target_elements = self._normalise_c5_target_elements(
            c5_cfg.get('c5_targets'))
        branch_key = (
            'branch_one'
            if getattr(self.record, 'branch', '') == 'one'
            else 'branch_two'
        )
        branch_cfg = c5_cfg.get(branch_key, {}) or {}
        if isinstance(branch_cfg, dict):
            target_elements.update(self._normalise_c5_target_elements(
                branch_cfg.get('c5_targets')))
            target_elements.update(self._normalise_c5_target_elements(
                branch_cfg.get('targets')))

        if getattr(self.record, 'branch', '') == 'one' and not target_elements:
            target_elements.update(self._C5_BRANCH_ONE_TARGET_CANDIDATES)
        return target_elements

    def _expected_rump_sets_for_campaign(self, campaign) -> tuple[set[str], set[str]]:
        campaign_name = getattr(campaign, 'name', str(campaign))
        expected: set[str] = set()
        targeted: set[str] = set()

        if campaign_name in {'C2A', 'C2A_STAGED'}:
            expected.update({'Ca', 'Al', 'REE', 'Ti'})
        elif campaign_name == 'C5':
            targeted.update(self._configured_c5_target_elements())
            if getattr(self.record, 'branch', '') == 'one':
                expected.update(self._C5_BRANCH_ONE_TARGET_CANDIDATES - targeted)
        elif campaign_name == 'C6':
            targeted.add('Al')
            expected.update({'Ca', 'REE'})
        elif campaign_name == 'MRE_BASELINE':
            targeted.update({'Ca', 'Al', 'Mg', 'Si'})
            expected.add('REE')

        expected = {
            element
            for element in expected
            if self._initial_rump_element_kg(element) > self._RUMP_EXPECTATION_TOL_KG
        }
        targeted = {
            element
            for element in targeted
            if self._initial_rump_element_kg(element) > self._RUMP_EXPECTATION_TOL_KG
        }
        return expected, targeted

    def _rump_expectation_diagnostic(self, campaign=None) -> dict:
        campaign = campaign or self.melt.campaign
        campaign_name = getattr(campaign, 'name', str(campaign))
        expected, targeted = self._expected_rump_sets_for_campaign(campaign)
        actual = self._actual_rump_elements_kg()
        missing = sorted(element for element in expected if element not in actual)
        diagnostic = {
            'campaign': campaign_name,
            'actual_rump_elements_kg': actual,
            'expected_unconsumed_rump_elements': sorted(expected),
            'targeted_rump_elements': sorted(targeted),
            'missing_expected_rump_elements': missing,
        }
        if missing:
            diagnostic['warning'] = (
                f"{campaign_name} expected rump elements missing: "
                f"{', '.join(missing)}"
            )
        return diagnostic

    # ``_record_atom_transition`` and ``_record_atom_transition_mol``
    # were removed when the METALLOTHERMIC_STEP flip
    # (``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) 6/7) replaced their
    # only call sites with ``ChemistryKernel.commit_batch``.  No other
    # caller used them.

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
        # WRITER-EXEMPT: shuttle-reagent-move -- atom-balanced reagent
        # shuttle between ledger accounts, outside chemistry transitions.
        self.atom_ledger.move(
            name,
            debit_account,
            credit_account,
            {species: kg},
            reason=reason,
        )
        return kg

    def _draw_reagent_to_process(
        self,
        species: str,
        requested_kg: float,
        *,
        fail_if_insufficient: bool = False,
        ) -> float:
        requested_kg = max(0.0, float(requested_kg))
        if requested_kg <= self._LEDGER_KG_TOL:
            return 0.0

        reservoir = f'reservoir.reagent.{species}'
        available_kg = self._ledger_account_species_kg(reservoir, species)
        if (
            fail_if_insufficient
            and requested_kg > available_kg + self._LEDGER_KG_TOL
        ):
            raise ValueError(
                f"requested {requested_kg:.12g} kg {species} reagent exceeds "
                f"available inventory {available_kg:.12g} kg"
            )
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

    def _activate_additive_reagent(self, species: str, requested_kg: float) -> None:
        if species in self._activated_additive_reagents:
            return
        self._activated_additive_reagents.add(species)
        self._draw_reagent_to_process(
            species,
            requested_kg,
            fail_if_insufficient=True,
        )

    def _activate_stage0_carbon_reagent(self, required_kg: float) -> None:
        if 'C' in self._activated_additive_reagents:
            return
        self._activated_additive_reagents.add('C')
        self._draw_reagent_to_process(
            'C',
            required_kg,
            fail_if_insufficient=True,
        )

    def _set_melt_species_projection(self, species: str, kg: float) -> None:
        self.melt.composition_kg.update({species: max(0.0, float(kg))})

    def _project_extraction_melt(self) -> None:
        self._project_cleaned_melt_from_atom_ledger()

    def _set_condensed_species_projection(
        self, stage_idx: int, species: str, kg: float
    ) -> None:
        self.train.stages[stage_idx].collected_kg.update(
            {species: max(0.0, float(kg))})

    def _clear_condensed_species_projection(self, species: str) -> None:
        for stage in self.train.stages:
            stage.collected_kg.pop(species, None)

    def _condensed_species_projected_kg(self, species: str) -> float:
        return sum(
            max(0.0, float(stage.collected_kg.get(species, 0.0)))
            for stage in self.train.stages
        )

    def _audit_metal_projection_drift(self) -> Dict[str, float]:
        """0.5.4 W8 (M2 historical-audit closure, 2026-05-28):
        per-species drift between ``process.metal_phase`` (the
        canonical AtomLedger metal account) and the UI projection
        sum across ``train.stages[*].collected_kg``.

        Returns a dict ``{species: drift_kg}`` for metal species
        where the absolute drift exceeds ``_LEDGER_KG_TOL``. Sign
        convention: ``ledger_kg - projection_kg`` — positive when
        the ledger account exceeds the UI projection (some metal
        has been credited but not yet projected; the normal
        steady-state drift direction), zero when in sync. Negative
        values should never appear in practice because
        ``_project_condensed_species`` line 248-250 already clears
        the projection if it overshoots the ledger; the audit
        surface still includes them honestly so an operator-visible
        bug would be audit-visible rather than silent.

        Diagnostic only — does NOT raise on drift. The ≤5e-12 %
        global mass-balance closure invariant
        (``HourSnapshot.mass_balance_error_pct``) remains the hard
        gate; this per-species view gives earlier-warning visibility
        when ledger ↔ UI drift opens up. Audit dict carried on
        ``HourSnapshot.metal_projection_drift_kg`` so external tools
        + tests can read it without touching simulator internals.
        """
        ledger_metals = self.atom_ledger.kg_by_account('process.metal_phase')
        # 0.5.4 milestone-review P2 (codex /challenge 2026-05-28):
        # iterate the UNION of species across both ledger and
        # projection, not just ledger keys. Pre-fix, an empty ledger
        # with stale phantom Fe lingering in
        # ``train.stages[*].collected_kg`` returned ``{}`` —
        # contradicting the "empty dict means in sync" semantics
        # documented on ``HourSnapshot.metal_projection_drift_kg``.
        # The projection-only stale state surfaces now with negative
        # drift (ledger_kg - projection_kg < 0), giving operators
        # visibility into the legitimately rare but operator-visible
        # failure mode where a ledger debit landed without a paired
        # projection clear (e.g., a hand-mutation of train.stages
        # bypassed ``_clear_condensed_species_projection``).
        projection_species: set[str] = set()
        for stage in self.train.stages:
            for species, value in stage.collected_kg.items():
                try:
                    if float(value) > 0.0:
                        projection_species.add(species)
                except (TypeError, ValueError):
                    continue
        union_species = set(ledger_metals.keys()) | projection_species
        if not union_species:
            return {}
        drift: Dict[str, float] = {}
        for species in union_species:
            raw = ledger_metals.get(species, 0.0)
            try:
                ledger_kg = float(raw)
            except (TypeError, ValueError):
                continue
            if not (ledger_kg == ledger_kg):  # NaN guard
                continue
            if ledger_kg < 0.0:
                ledger_kg = 0.0
            projected_kg = self._condensed_species_projected_kg(species)
            delta = ledger_kg - projected_kg
            if abs(delta) > self._LEDGER_KG_TOL:
                drift[species] = delta
        return drift

    def _project_condensed_species(
        self,
        stage_idx: int,
        species: str,
        delta_kg: float | None = None,
        *,
        source_account: str = 'process.condensation_train',
    ) -> None:
        kg = self._ledger_account_species_kg(
            source_account, species)
        if kg <= self._LEDGER_KG_TOL:
            self._clear_condensed_species_projection(species)
            return

        projected = self._condensed_species_projected_kg(species)
        if projected > kg + self._LEDGER_KG_TOL:
            self._clear_condensed_species_projection(species)
            projected = 0.0

        add_kg = kg - projected if delta_kg is None else float(delta_kg)
        add_kg = min(max(0.0, add_kg), max(0.0, kg - projected))
        if add_kg <= self._LEDGER_KG_TOL:
            return
        current = max(
            0.0, float(self.train.stages[stage_idx].collected_kg.get(species, 0.0))
        )
        self._set_condensed_species_projection(stage_idx, species, current + add_kg)

    def _project_extraction_product(
        self,
        recipe: str,
        species: str,
        delta_kg: float | None = None,
        *,
        source_account: str = 'process.metal_phase',
    ) -> None:
        stage_idx = product_stage_number(recipe, species)
        if stage_idx is None:
            return
        self._project_condensed_species(
            stage_idx,
            species,
            delta_kg=delta_kg,
            source_account=source_account,
        )

    def _parse_mre_voltage_sequence_yaml(self) -> list:
        """0.5.4.1 B5 (CW1 closure): parse ``setpoints['mre_voltage_
        sequence']['sequence']`` into the Python ladder shape. Returns
        a sorted list of ``{voltage, species, min_hold_hours}`` dicts,
        or an empty list if YAML is missing / malformed / empty after
        filtering. The caller falls back to
        ``_MRE_VOLTAGE_LADDER_FALLBACK`` on empty.

        Parsing is conservative — any individual unparseable entry is
        skipped rather than crashing the whole sequence. Defensive
        against operator typos / partial YAML blocks; designed to
        degrade to the fallback ladder gracefully.
        """
        return mre_ladder.parse_mre_voltage_sequence_yaml(self.setpoints)

    @staticmethod
    def _coerce_mre_decomposition_voltage(value) -> float | None:
        """Coerce a YAML ``decomposition_V`` value to a single float
        per the parsing rules documented on
        ``_build_mre_voltage_sequence``. Returns None on
        unparseable / non-finite input."""
        return mre_ladder.coerce_mre_decomposition_voltage(value)

    # 0.5.4.1 B5 (CW1 historical-audit closure, 2026-05-28):
    # canonical FALLBACK ladder used by ``_build_mre_voltage_sequence``
    # when ``setpoints['mre_voltage_sequence']['sequence']`` is missing
    # or unparseable. Pre-B5 this list was inline and the YAML
    # ``data/setpoints.yaml § mre_voltage_sequence.sequence`` block
    # documented the same data but was never read — operators
    # tweaking the YAML saw zero effect. B5 wires the YAML through,
    # so this list is now the fallback / golden ground truth, NOT
    # the only source-of-truth.
    _MRE_VOLTAGE_LADDER_FALLBACK = mre_ladder.MRE_VOLTAGE_LADDER_FALLBACK

    # Default ``min_hold_hours`` per species used when YAML doesn't
    # carry an explicit value. Sourced from the same fallback ladder
    # above (so YAML-without-hours behaves identically to the fallback
    # for those species). For species the YAML adds but the fallback
    # doesn't cover (e.g., Na2O / K2O in the published YAML), the
    # default below applies.
    _MRE_DEFAULT_MIN_HOLD_HOURS = mre_ladder.MRE_DEFAULT_MIN_HOLD_HOURS

    def _build_mre_voltage_sequence(self) -> list:
        """Build the stepped voltage hold sequence (Ellingham ladder).

        0.5.4.1 B5 wired the YAML setpoint
        ``setpoints['mre_voltage_sequence']['sequence']`` (was previously
        dead config — operators saw zero effect from YAML edits). Now:

        - If the YAML block is present and at least one entry parses
          cleanly, return the YAML-derived ladder.
        - Otherwise fall back to ``_MRE_VOLTAGE_LADDER_FALLBACK``.

        YAML schema (per ``data/setpoints.yaml § mre_voltage_sequence
        .sequence``): each entry has ``species`` (single string),
        ``decomposition_V`` (scalar OR ``[low, high]`` range OR string
        like ``"<0.5"``), optional ``campaign`` (informational),
        optional ``note`` (informational), and optional
        ``min_hold_hours`` (else falls back to
        ``_MRE_DEFAULT_MIN_HOLD_HOURS``).

        Parsing rules:
        - Range ``[low, high]`` → use the mean (operator can pin
          midpoint by setting equal values).
        - String ``"<X"`` → parse as ``X`` (operator-warning that the
          actual is below; midpoint not encoded). Strings ``">X"``
          similarly parse as ``X``. Other strings → skip entry.
        - Non-finite / non-coercible voltage → skip entry.
        - Empty / missing ``species`` → skip entry.

        Entries are sorted by voltage ascending so C5 max-voltage filtering
        (``voltage <= 1.6``) works on the resulting list without
        reordering. Closes CW1 historical-audit item
        (``docs-private/audits/2026-05-27-p3-historical-audit.txt``).
        """
        return mre_ladder.build_mre_voltage_sequence(self.setpoints)

    def _step_mre(self) -> float:
        """
        Perform one hour of molten regolith electrolysis (C5 or MRE baseline).

        Voltage strategy:
            C5 (limited MRE):    Stepped holds at the selected EvalSpec
                                 target/max-voltage rung.
                                 ``allowed_oxides`` is an operator target-rung
                                 selectivity filter (the EvalSpec target step);
                                 Nernst + the voltage cap already govern
                                 which species are physically reducible.
                                 Electrode life 5-10× longer than full MRE.

            MRE_BASELINE:        Stepped holds at each Ellingham threshold (0.75->2.5 V).
                                 Each species substantially extracted before advancing.
                                 Higher current (3000 A) for faster throughput.

        ELECTROLYSIS_STEP intent -- kernel-authoritative since
        ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) fifth flip and the
        THIRD authoritative ledger-mutating intent. The
        :class:`BuiltinElectrolysisStepProvider` mirrors
        :meth:`ElectrolysisModel.step_hour` Nernst / Faraday / current-
        efficiency math exactly; the provider emits a
        :class:`LedgerTransitionProposal` debiting
        ``process.cleaned_melt`` (oxide consumed) and crediting
        ``process.metal_phase`` (cathode metals) +
        ``terminal.oxygen_mre_anode_stored`` (anode O2 -- its OWN bin
        per AGENTS.md #6, distinct from melt-offgas / Stage-0 / overhead
        headspace).  :meth:`ChemistryKernel.commit_batch` is the sole
        writable path into the ledger for this intent after the flip;
        the legacy :meth:`_record_mre_ledger_transition` is gone (the
        ledger write happens INSIDE the kernel commit, not in this
        method).  Energy stays in the provider's diagnostic (not in
        the ledger) and routes to :class:`EnergyTracker` via the
        existing ``_mre_energy_this_hr`` counter, same as pre-flip.

        Returns O₂ produced this hour (kg).
        """
        from simulator.chemistry.kernel.capabilities import ChemistryIntent
        from simulator.electrolysis import ELECTRONS_PER_OXIDE

        if (
            self.melt.campaign == CampaignPhase.C5
            and not getattr(self.melt, 'c5_enabled', False)
        ):
            self._mre_metals_this_hr = {}
            self._mre_voltage_V = 0.0
            self._mre_current_A = 0.0
            self._mre_effective_current_A = 0.0
            self._mre_energy_this_hr = 0.0
            self.melt.mre_voltage_V = 0.0
            self.melt.mre_current_A = 0.0
            return 0.0

        # --- Voltage and current selection (stepped holds) ---         [Step 9]
        if self.melt.campaign == CampaignPhase.MRE_BASELINE:
            seq = self._mre_voltage_sequence
            if not seq:
                # Fallback if sequence not loaded; start at reanchored FeO
                # rung (0.75 V) consistent with DECOMP_VOLTAGES (MRE #32B).
                voltage_V = min(0.75 + self.melt.campaign_hour * 0.1, 2.5)
            else:
                idx = min(self._mre_voltage_step_idx, len(seq) - 1)
                step_info = seq[idx]
                voltage_V = step_info['voltage']

                self._mre_hold_hours += 1

                # Advance to next voltage step when target species depleted
                if self._mre_hold_hours >= step_info.get('min_hold_hours', 3):
                    target_current_low = (
                        self._mre_effective_current_A < 3000.0 * 0.05)
                    if target_current_low:
                        self._mre_voltage_step_idx += 1
                        self._mre_hold_hours = 0

            current_A = 3000.0  # Full-scale MRE: ~60 kA/m² at 0.05 m²
            c5_allowed_oxides = None
        else:
            # C5 limited MRE: EvalSpec/session fields are behavior determinants.
            target = str(getattr(self.melt, 'mre_target_species', '') or '')
            configured_max = (
                mre_ladder.coerce_mre_decomposition_voltage(
                    getattr(self.melt, 'mre_max_voltage_V', 0.0)
                )
                or 0.0
            )
            ladder_cap_V = configured_max
            if ladder_cap_V <= 0.0 and target:
                ladder_cap_V = mre_ladder.max_voltage_for_target(
                    target, self._mre_voltage_sequence
                )
            allowed_oxides = mre_ladder.allowed_oxides_for_target(
                target,
                self._mre_voltage_sequence,
                ladder_cap_V,
            )
            selected_steps = mre_ladder.filter_steps_up_to_max_v(
                self._mre_voltage_sequence, ladder_cap_V
            )
            if target:
                seq = [
                    step for step in selected_steps
                    if target in step['species']
                ]
            else:
                seq = selected_steps
            if not seq:
                self._mre_metals_this_hr = {}
                self._mre_voltage_V = 0.0
                self._mre_current_A = 0.0
                self._mre_effective_current_A = 0.0
                self._mre_energy_this_hr = 0.0
                self.melt.mre_voltage_V = 0.0
                self.melt.mre_current_A = 0.0
                return 0.0

            sequence_completion_key = (
                target,
                float(ladder_cap_V),
                tuple(
                    (float(step['voltage']), tuple(step['species']))
                    for step in seq
                ),
            )
            if getattr(self, '_mre_c5_sequence_complete_key', None) == sequence_completion_key:
                self._mre_metals_this_hr = {}
                self._mre_voltage_V = 0.0
                self._mre_current_A = 0.0
                self._mre_effective_current_A = 0.0
                self._mre_energy_this_hr = 0.0
                self.melt.mre_voltage_V = 0.0
                self.melt.mre_current_A = 0.0
                return 0.0

            else:
                idx = min(self._mre_voltage_step_idx, len(seq) - 1)
                step_info = seq[idx]
                voltage_V = step_info['voltage']

                self._mre_hold_hours += 1
                if self._mre_hold_hours >= step_info.get('min_hold_hours', 3):
                    c5_current_A = mre_ladder.C5_LIMITED_MRE_CURRENT_A
                    target_current_low = (
                        self._mre_effective_current_A < c5_current_A * 0.05)
                    safety_max_hold = (
                        self._mre_hold_hours
                        >= mre_ladder.C5_DEPLETION_SAFETY_MAX_HOLD_HR
                    )
                    if target_current_low or safety_max_hold:
                        self._mre_voltage_step_idx += 1
                        if idx >= len(seq) - 1:
                            self._mre_c5_sequence_complete_key = sequence_completion_key
                        self._mre_hold_hours = 0

            current_A = mre_ladder.C5_LIMITED_MRE_CURRENT_A
            # Operator target-rung selectivity; not a second Nernst gate.
            c5_allowed_oxides = (
                sorted(allowed_oxides) if allowed_oxides is not None else None
            )

        # F-B1: dispatch + commit split via the _dispatch_only /
        # _commit_proposal helper pair so the per-account balance
        # snapshot can sit between them.  We need the pre-commit metal
        # / O2 totals to route the per-tick cathode delta into the
        # condenser stages (matches pre-flip behaviour: routing keys
        # off the per-tick increment in process.metal_phase, not the
        # legacy result dict).  commit_batch is still the ONLY writable
        # path into the AtomLedger for ELECTROLYSIS_STEP.
        electrolysis_controls = {
            'voltage_V': float(voltage_V),
            'current_A': float(current_A),
            'dt_hr': 1.0,
            'pO2_bar': float(self._commanded_pO2_bar()),
        }
        melt_fO2_log = getattr(self.melt, 'melt_fO2_log', None)
        try:
            melt_fO2_log = float(melt_fO2_log)
        except (TypeError, ValueError):
            melt_fO2_log = self._compute_intrinsic_melt_fO2()
        if not math.isfinite(melt_fO2_log):
            melt_fO2_log = self._compute_intrinsic_melt_fO2()
        electrolysis_controls['melt_fO2_log'] = float(melt_fO2_log)
        if c5_allowed_oxides is not None:
            electrolysis_controls['allowed_oxides'] = c5_allowed_oxides
        kernel_result = self._dispatch_only(
            ChemistryIntent.ELECTROLYSIS_STEP,
            control_inputs=electrolysis_controls,
            fO2_log=float(melt_fO2_log),
            fe_redox_policy='kress91_live',
        )
        diagnostic = dict(kernel_result.diagnostic or {})
        result = diagnostic  # legacy variable name -- same shape as step_hour's dict.

        produced_metals = set(result.get('metals_produced_kg', {}) or {})
        produced_metals.update(result.get('metals_produced_mol', {}) or {})
        metal_before_kg = {
            metal: self._ledger_account_species_kg(
                'process.metal_phase', metal)
            for metal in produced_metals
        }
        o2_before_kg = self._ledger_account_species_kg(
            'terminal.oxygen_mre_anode_stored', 'O2')

        proposal = kernel_result.transition
        if proposal is None and getattr(kernel_result, 'status', '') == 'refused':
            refusal_record = {
                'intent': ChemistryIntent.ELECTROLYSIS_STEP.name,
                'hour': int(self.melt.hour),
                'campaign_hour': int(self.melt.campaign_hour),
                'campaign': self.melt.campaign.name,
                'temperature_C': float(self.melt.temperature_C),
                'voltage_V': float(voltage_V),
                'current_A': float(current_A),
                'diagnostic': diagnostic,
            }
            self._last_mre_refusal_diagnostic = refusal_record
            if not hasattr(self, '_mre_refusal_history'):
                self._mre_refusal_history = []
            self._mre_refusal_history.append(refusal_record)
            self._chem_no_op_dispatch_count += 1
            self._mre_metals_this_hr = {}
            self._mre_voltage_V = 0.0
            self._mre_current_A = 0.0
            self._mre_effective_current_A = 0.0
            self._mre_energy_this_hr = 0.0
            self.melt.mre_voltage_V = 0.0
            self.melt.mre_current_A = 0.0
            reason = diagnostic.get('reason_refused', 'electrolysis_step_refused')
            raise RuntimeError(f'MRE electrolysis refused: {reason}')
        if proposal is not None:
            self._commit_proposal(
                ChemistryIntent.ELECTROLYSIS_STEP, proposal,
            )
        else:
            # F-A4: no-op dispatch counter mirrors the
            # _dispatch_and_commit helper's behaviour at split sites.
            self._chem_no_op_dispatch_count += 1

        # Route cathode metals through the canonical registry.  Metal-phase
        # destinations stay in process.metal_phase; only true condenser
        # destinations receive a stage UI projection.
        mre_metal_deltas_kg: Dict[str, float] = {}
        for metal in produced_metals:
            delta_kg = (
                self._ledger_account_species_kg(
                    'process.metal_phase', metal)
                - metal_before_kg.get(metal, 0.0)
            )
            if delta_kg > 1e-10:
                mre_metal_deltas_kg[metal] = delta_kg
                self._project_extraction_product(
                    'MRE',
                    metal,
                    source_account='process.metal_phase',
                    delta_kg=delta_kg,
                )

        self._mre_metals_this_hr = dict(sorted(mre_metal_deltas_kg.items()))

        self._project_extraction_melt()

        # Route anodic O₂ to Stage 6 accumulator (mass balance).       [Step 5]
        O2_kg = max(
            0.0,
            self._ledger_account_species_kg(
                'terminal.oxygen_mre_anode_stored', 'O2')
            - o2_before_kg,
        )
        self._sync_oxygen_kg_counters()

        # Store energy for EnergyTracker (don't add to cumulative).    [Step 6]
        self._mre_energy_this_hr = result.get('energy_kWh', 0.0)

        # Store voltage/current for snapshot                            [Step 7]
        self._mre_voltage_V = voltage_V
        self._mre_current_A = current_A

        # Calculate effective current from actual Faradaic reduction.   [Step 8]
        total_charge_C = 0.0
        charge_electrons = result.get('oxide_charge_electrons', {}) or {}
        for oxide, kg_removed in result.get('oxides_reduced_kg', {}).items():
            n_e = charge_electrons.get(oxide, ELECTRONS_PER_OXIDE.get(oxide, 2))
            M_ox = MOLAR_MASS.get(oxide, 100.0)
            moles_ox = kg_removed * 1000.0 / M_ox
            total_charge_C += moles_ox * n_e * FARADAY
        self._mre_effective_current_A = total_charge_C / 3600.0

        # Store effective current on melt state for endpoint detection
        self.melt.mre_voltage_V = voltage_V
        self.melt.mre_current_A = self._mre_effective_current_A

        return O2_kg

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
            self._activate_additive_reagent(
                'K',
                self.record.additives_kg.get('K', 0.0),
            )
            self._transfer_condensed_species('K')
            self._transfer_condensed_species('Na')
            self.shuttle_K_inventory_kg = self._sync_reagent_counter_from_ledger('K')
            na_additive_kg = self.record.additives_kg.get('Na', 0.0)
            if na_additive_kg > self._LEDGER_KG_TOL:
                self._activate_additive_reagent(
                    'Na',
                    na_additive_kg,
                )
                self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')
            else:
                self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')
            self.shuttle_cycle_K = 0

        elif campaign == CampaignPhase.C3_NA:
            self._activate_additive_reagent(
                'Na',
                self.record.additives_kg.get('Na', 0.0),
            )
            self._transfer_condensed_species('Na')
            self._transfer_condensed_species('K')
            self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')
            self.shuttle_K_inventory_kg = self._sync_reagent_counter_from_ledger('K')
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

        S1c self-re-flux (2026-05-27, post-0.5.0): at the start of every
        C3 tick we transfer any alkali that recondensed onto the
        condensation train back into ``process.reagent_inventory``. The
        previous tick's recovered alkali becomes available to this
        tick's injection, which is the intra-batch shuttle
        amplification CLAUDE.md §4 describes ("Same Na inventory
        amplifies across multiple batches before final recovery" — read
        across the bakeout/inject within a single C3 phase as well).
        Pre-S1c the shuttle was single-charge + start-of-phase
        recovery; intra-cycle recycle simply wasn't wired. This is the
        honest implementation of Review D P1-3 (S1b documented the
        design as the cheap-doc; S1c lands the actual mechanism).

        ``_transfer_condensed_species`` is a no-op when the train holds
        zero recovered alkali, so the early-phase first-tick behavior
        is unchanged. Post-V1c the K shuttle is refused at any
        practical melt T (S1b gate), so the C3_K recycle is dead code
        in practice; we keep the call for the unlikely case where a
        future recipe opens a window in which K → FeO is positive.
        """
        # Reset per-hour tracking
        self._shuttle_injected_this_hr = 0.0
        self._shuttle_reduced_this_hr = 0.0
        self._shuttle_metal_this_hr = 0.0

        campaign = self.melt.campaign

        # S1c: intra-C3 self-re-flux. Before this tick's inject/bakeout
        # dispatch, pull any alkali that recondensed onto the train back
        # into the reagent inventory so it is available for THIS tick.
        # Autoreview pre-0.5.1 P2 (2026-05-27): the C3_K dispatch below
        # (lines 588-589) injects BOTH K AND Na (Na for the feo_cleanup
        # target_stage), so the recycle must also pull recovered Na, not
        # just K -- otherwise Na that recondensed during the previous
        # bakeout tick sits idle in the train and the intended
        # intra-cycle Na recycle silently fails under C3_K.
        if campaign == CampaignPhase.C3_K:
            self._transfer_condensed_species('K')
            self._transfer_condensed_species('Na')
        elif campaign == CampaignPhase.C3_NA:
            self._transfer_condensed_species('Na')

        cycle_period = 6  # hours per inject-bakeout cycle
        # Staged C2A enters the cool Na cleanup at the end of the cooldown
        # tick, so the first real shuttle tick starts with campaign_hour == 1.
        if (
            campaign in (CampaignPhase.C3_K, CampaignPhase.C3_NA)
            and self.record.path == 'A_staged'
        ):
            is_injection = self.melt.campaign_hour <= 3
        else:
            is_injection = (self.melt.campaign_hour % cycle_period) < 3

        if is_injection:
            self._shuttle_phase = 'inject'
            # liquid_fraction is consumed only by the inject dispatches below, so
            # compute it here (NOT before the branch) — bakeout ticks must not pay
            # the liquidus-engine cost or hit its no-source raise for a discarded value.
            liquid_fraction = None
            if self._freeze_gate_enabled():
                liquid_fraction = self._freeze_gate_liquid_fraction_factor()
            if campaign == CampaignPhase.C3_K:
                self._shuttle_inject_K(liquid_fraction=liquid_fraction)
                self._shuttle_inject_Na(
                    target_stage='feo_cleanup',
                    liquid_fraction=liquid_fraction,
                )
            elif campaign == CampaignPhase.C3_NA:
                target_stage = (
                    'feo_cleanup'
                    if self.record.path == 'A_staged'
                    else 'cr_ti'
                )
                self._shuttle_inject_Na(
                    target_stage=target_stage,
                    liquid_fraction=liquid_fraction,
                )
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

    def _shuttle_inject_K(self, *, liquid_fraction=None):
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

        METALLOTHERMIC_STEP intent -- kernel-authoritative since
        ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) sixth flip and the
        FOURTH authoritative ledger-mutating intent in the migration.
        The :class:`BuiltinMetallothermicStepProvider` mirrors the
        legacy K-shuttle stoichiometry line-for-line and emits a
        :class:`LedgerTransitionProposal` debiting
        ``process.reagent_inventory`` (K consumed) +
        ``process.cleaned_melt`` (FeO reduced) and crediting
        ``process.cleaned_melt`` (K₂O coproduct) +
        ``process.metal_phase`` (Fe produced).
        :meth:`ChemistryKernel.commit_batch` is the sole writable path
        into the ledger for this intent after the flip; the legacy
        ``self._record_atom_transition`` direct mutation is gone.
        """
        from simulator.chemistry.kernel.capabilities import ChemistryIntent
        from engines.builtin.metallothermic_step import REACTION_FAMILY_C3_K

        if self.shuttle_K_inventory_kg <= 0.01:
            return  # No K available

        # F-B1: dispatch + commit through the shared helper.  The
        # kernel's commit_batch path is still the ONLY writable entry
        # into the AtomLedger for METALLOTHERMIC_STEP.
        kernel_result = self._dispatch_only(
            ChemistryIntent.METALLOTHERMIC_STEP,
            control_inputs={
                'reaction_family': REACTION_FAMILY_C3_K,
                'reagent_available_kg': float(
                    self.shuttle_K_inventory_kg),
                'true_available_mol_by_species':
                    self._cleaned_melt_available_mol_by_species(('FeO',)),
                'liquid_fraction': liquid_fraction,
                'dt_hr': 1.0,
            },
        )
        diagnostic = dict(kernel_result.diagnostic or {})
        proposal = kernel_result.transition
        if proposal is None:
            if getattr(kernel_result, 'status', '') == 'refused':
                if diagnostic.get('reason_refused') != 'no_liquid_phase':
                    refusal_record = {
                        'reaction_family': REACTION_FAMILY_C3_K,
                        'reagent': 'K',
                        'hour': int(self.melt.hour),
                        'campaign_hour': int(self.melt.campaign_hour),
                        'campaign': self.melt.campaign.name,
                        'temperature_C': float(self.melt.temperature_C),
                        'diagnostic': diagnostic,
                    }
                    self._last_shuttle_refusal_diagnostic = refusal_record
                    self._shuttle_refusal_history.append(refusal_record)
            self._chem_no_op_dispatch_count += 1
            return

        self._commit_proposal(
            ChemistryIntent.METALLOTHERMIC_STEP, proposal,
        )

        # Fe produced goes to its canonical product destination.
        self._project_extraction_product(
            'C3', 'Fe', source_account='process.metal_phase')

        # Deduct K from shuttle inventory
        # (K comes from additives, not from a condenser stage)
        self.shuttle_K_inventory_kg = self._sync_reagent_counter_from_ledger('K')

        self._project_extraction_melt()

        # Track for snapshot -- same shape as pre-flip kg counters.
        self._shuttle_injected_this_hr += float(
            diagnostic.get('reagent_consumed_kg', 0.0))
        self._shuttle_reduced_this_hr += float(
            diagnostic.get('oxide_reduced_kg', 0.0))
        self._shuttle_metal_this_hr += float(
            diagnostic.get('metal_produced_kg', 0.0))

    def _shuttle_inject_Na(
        self,
        target_stage: str = 'cr_ti',
        *,
        liquid_fraction=None,
    ):
        """
        Na-shuttle injection: reduce stage-selected oxides.

        Reactions:                                               [THERMO-5]
            2Na + FeO → Na₂O + Fe(l)   [cool Fe-cleanup only]
            4Na + TiO₂ → 2Na₂O + Ti(l)  [accessibility uncertain]
            6Na + Cr₂O₃ → 3Na₂O + 2Cr(l)

        Stoichiometry (TiO₂ reaction):
            91.95 g Na + 79.87 g TiO₂ → 123.96 g Na₂O + 47.87 g Ti
            1 kg Na → 0.869 kg TiO₂ reduced
                    → 1.348 kg Na₂O dissolved
                    → 0.521 kg Ti produced

        Na₂O solubility limit: 8-12 wt% in the silicate melt.
        Activity coefficient γ(Na₂O) ≈ 10⁻² to 10⁻³ in CMAS.    [THERMO-10]

        METALLOTHERMIC_STEP intent -- kernel-authoritative since
        ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) sixth flip.  The
        provider can bundle the two-reaction (Cr2O3 + TiO2) atom-balanced
        path the legacy code recorded as two separate transitions into
        a single :class:`LedgerTransitionProposal` so the kernel
        commits one atom-balanced :class:`LedgerTransition` per
        dispatch.  The diagnostic exposes per-oxide / per-metal kg so
        the snapshot retains the legacy total counters.
        """
        from simulator.chemistry.kernel.capabilities import ChemistryIntent
        from engines.builtin.metallothermic_step import REACTION_FAMILY_C3_NA

        if self.shuttle_Na_inventory_kg <= 0.01:
            return

        # F-B1: dispatch + commit through the shared helper.
        kernel_result = self._dispatch_only(
            ChemistryIntent.METALLOTHERMIC_STEP,
            control_inputs={
                'reaction_family': REACTION_FAMILY_C3_NA,
                'na_target_stage': target_stage,
                'reagent_available_kg': float(
                    self.shuttle_Na_inventory_kg),
                'true_available_mol_by_species':
                    self._cleaned_melt_available_mol_by_species(
                        ('FeO', 'Cr2O3', 'TiO2'),
                    ),
                'liquid_fraction': liquid_fraction,
                'dt_hr': 1.0,
            },
        )
        diagnostic = dict(kernel_result.diagnostic or {})
        proposal = kernel_result.transition
        if proposal is None:
            if getattr(kernel_result, 'status', '') == 'refused':
                if diagnostic.get('reason_refused') != 'no_liquid_phase':
                    refusal_record = {
                        'reaction_family': REACTION_FAMILY_C3_NA,
                        'reagent': 'Na',
                        'target_stage': target_stage,
                        'hour': int(self.melt.hour),
                        'campaign_hour': int(self.melt.campaign_hour),
                        'campaign': self.melt.campaign.name,
                        'temperature_C': float(self.melt.temperature_C),
                        'diagnostic': diagnostic,
                    }
                    self._last_shuttle_refusal_diagnostic = refusal_record
                    self._shuttle_refusal_history.append(refusal_record)
            self._chem_no_op_dispatch_count += 1
            return

        self._commit_proposal(
            ChemistryIntent.METALLOTHERMIC_STEP, proposal,
        )

        # Reduced metals use the canonical recipe product registry.  Cr routes
        # to the dedicated Cr stage; Ti stays as a metal-phase product unless a
        # future accepted physical condenser is added.
        for metal in ('Fe', 'Cr', 'Ti'):
            self._project_extraction_product(
                'C3', metal, source_account='process.metal_phase')

        # Deduct Na from shuttle inventory (drawn from the ledger so
        # the counter stays in sync with the kernel-committed debit).
        self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')

        self._project_extraction_melt()

        # Track for snapshot -- same shape as pre-flip kg counters.
        self._shuttle_injected_this_hr += float(
            diagnostic.get('reagent_consumed_kg', 0.0))
        self._shuttle_reduced_this_hr += float(
            diagnostic.get('oxide_reduced_kg', 0.0))
        self._shuttle_metal_this_hr += float(
            diagnostic.get('metal_produced_kg', 0.0))

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
        self._clear_condensed_species_projection(species)
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

        METALLOTHERMIC_STEP intent -- kernel-authoritative since
        ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) sixth flip.  The
        provider dispatches the primary thermite reaction first; the
        back-reduction (a chemically distinct reaction the legacy
        recorded as its own transition) is a second dispatch on the
        same intent so each chemical step stays a single atom-balanced
        :class:`LedgerTransition`.  The two dispatches share state
        through the ``mol_Al_produced`` control input that flows from
        the primary's diagnostic into the back-reduction's request --
        the back-reduction consumes
        ``BACK_REDUCTION_FRACTION = 0.30`` of the matched primary's
        freshly-produced Al kg.
        """
        from simulator.chemistry.kernel.capabilities import ChemistryIntent
        from engines.builtin.metallothermic_step import REACTION_FAMILY_C6_MG

        self._thermite_Al2O3_reduced_this_hr = 0.0
        self._thermite_Al_produced_this_hr = 0.0
        self._thermite_Mg_consumed_this_hr = 0.0

        if self.thermite_Mg_inventory_kg <= 0.01:
            return  # No Mg available

        liquid_fraction = None
        if self._freeze_gate_enabled():
            liquid_fraction = self._freeze_gate_liquid_fraction_factor()

        # ------------------------------------------------------------------
        # Pass 1: primary thermite reaction (3 Mg + Al2O3 -> 3 MgO + 2 Al).
        #
        # F-B1: the _dispatch_only / _commit_proposal split lets the
        # caller gate the commit on a "primary produced a proposal"
        # check.  When the kernel returns ``transition is None`` we
        # short-circuit BEFORE the back-reduction pass (no primary
        # means nothing for the cascade to consume).
        # ------------------------------------------------------------------
        primary_result = self._dispatch_only(
            ChemistryIntent.METALLOTHERMIC_STEP,
            control_inputs={
                'reaction_family': REACTION_FAMILY_C6_MG,
                'reagent_available_kg': float(
                    self.thermite_Mg_inventory_kg),
                'true_available_mol_by_species':
                    self._cleaned_melt_available_mol_by_species(('Al2O3',)),
                'liquid_fraction': liquid_fraction,
                'dt_hr': 1.0,
            },
        )
        primary_diag = dict(primary_result.diagnostic or {})
        primary_proposal = primary_result.transition
        if primary_proposal is None:
            # F-A4: counter mirrors the _dispatch_and_commit helper.
            self._chem_no_op_dispatch_count += 1
            return

        self._commit_proposal(
            ChemistryIntent.METALLOTHERMIC_STEP, primary_proposal,
        )

        Mg_consumed_kg = float(primary_diag.get('reagent_consumed_kg', 0.0))
        Al2O3_removed_kg = float(primary_diag.get('oxide_reduced_kg', 0.0))
        Al_produced_kg = float(primary_diag.get('metal_produced_kg', 0.0))
        mol_Al_produced = float(primary_diag.get('mol_Al_produced', 0.0))

        # ------------------------------------------------------------------
        # Pass 2: back-reduction cascade (4 Al + 3 SiO2 -> 2 Al2O3 + 3 Si),
        # if SiO2 / Al gates open.  The provider re-runs its own gate
        # internally; this method just orchestrates the second dispatch
        # and updates the local kg counters.  Split helpers again: the
        # cascade may legitimately return ``transition is None`` (no
        # SiO2 to back-reduce), which still needs to drive the Al
        # snapshot bookkeeping below.
        # ------------------------------------------------------------------
        back_result = self._dispatch_only(
            ChemistryIntent.METALLOTHERMIC_STEP,
            control_inputs={
                'reaction_family': REACTION_FAMILY_C6_MG,
                'back_reduction': True,
                'mol_Al_produced': mol_Al_produced,
                'reagent_available_kg': 0.0,
                'true_available_mol_by_species':
                    self._cleaned_melt_available_mol_by_species(('SiO2',)),
                'dt_hr': 1.0,
            },
        )
        back_diag = dict(back_result.diagnostic or {})
        back_proposal = back_result.transition
        back_si_before_kg = self._ledger_account_species_kg(
            'process.metal_phase', 'Si')
        if back_proposal is not None:
            self._commit_proposal(
                ChemistryIntent.METALLOTHERMIC_STEP, back_proposal,
            )
            metal_phase_delta = (
                self._ledger_account_species_kg(
                    'process.metal_phase', 'Si')
                - back_si_before_kg
            )
            # Provider credited Si to process.metal_phase; the registry keeps
            # Si as a metal-phase product instead of minting a condenser stage.
            self._project_extraction_product(
                'C6', 'Si', metal_phase_delta,
                source_account='process.metal_phase')
        else:
            # F-A4: no-op dispatch counter mirrors the
            # _dispatch_and_commit helper.  A SiO2-poor melt or an
            # Al-depleted state legitimately yields no back-reduction
            # transition; the counter lets a replay tool see that the
            # second dispatch fired and returned no-op rather than was
            # skipped at the caller.
            self._chem_no_op_dispatch_count += 1

            # Net Al / Al2O3 deltas after back-reduction (legacy
            # snapshot semantics: counters track NET removed Al2O3 and
            # NET produced Al).
            Al_lost_to_back_kg = float(back_diag.get('Al_consumed_kg', 0.0))
            Al2O3_regenerated_kg = float(
                back_diag.get('Al2O3_regenerated_kg', 0.0))
            Al_produced_kg -= Al_lost_to_back_kg
            Al2O3_removed_kg -= Al2O3_regenerated_kg

        # Al product remains in the metal-phase product account.
        self._project_extraction_product(
            'C6', 'Al', source_account='process.metal_phase')

        # Deduct Mg from thermite inventory.
        self.thermite_Mg_inventory_kg = self._sync_reagent_counter_from_ledger('Mg')

        self._project_extraction_melt()

        # Track for snapshot / summary (matches pre-flip counter shape).
        self._thermite_Al2O3_reduced_this_hr = max(0.0, Al2O3_removed_kg)
        self._thermite_Al_produced_this_hr = max(0.0, Al_produced_kg)
        self._thermite_Mg_consumed_this_hr = Mg_consumed_kg

    # ------------------------------------------------------------------
    # Equipment spec helpers
    # ------------------------------------------------------------------
