"""MRE, alkali-shuttle, and thermite helpers for PyrolysisSimulator."""

from __future__ import annotations

import math
import warnings
from typing import Any, Dict, Mapping

import simulator.mre_ladder as mre_ladder
from simulator.account_ids import (
    C7_AL_CREDIT_ACCOUNT,
    METAL_BOTTOM_POOL_ACCOUNT,
    METAL_FLOAT_LAYER_ACCOUNT,
    METAL_PHASE_ACCOUNT,
    METAL_PHASE_ACCOUNTS,
    STAGE_COLLECTION_BACKING_ACCOUNTS,
)
from simulator.accounting.queries import AccountingQueries
from simulator.chemistry.melt_activity import melt_oxide_activity
from simulator.condensation_routing import product_stage_number
from simulator.state import (
    FARADAY,
    GAS_CONSTANT,
    MOLAR_MASS,
    OXIDE_TO_METAL,
    CampaignPhase,
    EvaporationFlux,
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
        if account == METAL_PHASE_ACCOUNT:
            return sum(
                max(
                    0.0,
                    float(
                        self.atom_ledger.kg_by_account(metal_account).get(
                            species, 0.0
                        )
                    ),
                )
                for metal_account in METAL_PHASE_ACCOUNTS
            )
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
        elif campaign_name == 'C7_CA_ALUMINOTHERMIC':
            targeted.add('Ca')
            expected.add('REE')
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
        allow_credit: bool = False,
        ) -> float:
        requested_kg = max(0.0, float(requested_kg))
        if requested_kg <= self._LEDGER_KG_TOL:
            return 0.0

        reservoir = f'reservoir.reagent.{species}'
        available_kg = self._ledger_account_species_kg(reservoir, species)
        if (
            fail_if_insufficient
            and not allow_credit
            and requested_kg > available_kg + self._LEDGER_KG_TOL
        ):
            raise ValueError(
                f"requested {requested_kg:.12g} kg {species} reagent exceeds "
                f"available inventory {available_kg:.12g} kg"
            )
        draw_kg = requested_kg if allow_credit else min(requested_kg, available_kg)
        if draw_kg <= self._LEDGER_KG_TOL:
            return 0.0
        moved_kg = self._move_ledger_species(
            f'draw_{species}_reagent_to_process',
            reservoir,
            'process.reagent_inventory',
            species,
            draw_kg,
            reason=(
                f'C3 {species} alkali credit-line draw'
                if allow_credit
                else f'{species} reagent draw from reservoir'
            ),
        )
        if not allow_credit:
            self._move_cost_inventory_lots_best_effort(
                source_account=reservoir,
                destination_account='process.reagent_inventory',
                species=species,
                quantity_kg=moved_kg,
                reason=f'{species} reagent draw from reservoir',
            )
        return moved_kg

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

    def _c3_alkali_requested_dose_kg(self, species: str) -> float:
        campaigns = getattr(self, 'setpoints', {}).get('campaigns', {})
        if not isinstance(campaigns, Mapping):
            return 0.0
        c3 = campaigns.get('C3', {})
        if not isinstance(c3, Mapping):
            return 0.0
        dosing = c3.get('alkali_dosing', {})
        if dosing in (None, {}):
            return 0.0
        if not isinstance(dosing, Mapping):
            raise ValueError('campaigns.C3.alkali_dosing must be a mapping')
        key = f'{species}_kg'
        if key not in dosing or dosing[key] is None:
            return 0.0
        try:
            requested_kg = float(dosing[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f'campaigns.C3.alkali_dosing.{key} must be numeric'
            ) from exc
        if not math.isfinite(requested_kg) or requested_kg < 0.0:
            raise ValueError(
                f'campaigns.C3.alkali_dosing.{key} must be finite and non-negative'
            )
        return requested_kg

    def _record_c3_alkali_credit_draw(self, species: str, kg: float) -> None:
        if kg <= self._LEDGER_KG_TOL:
            return
        drawn = getattr(self, '_c3_alkali_credit_drawn_kg_by_species', None)
        if not isinstance(drawn, dict):
            drawn = {}
            self._c3_alkali_credit_drawn_kg_by_species = drawn
        drawn[species] = float(drawn.get(species, 0.0)) + float(kg)

    def _record_feedstock_recovered_reagent(self, species: str, kg: float) -> None:
        if kg <= self._LEDGER_KG_TOL:
            return
        recovered = getattr(
            self,
            '_feedstock_recovered_reagent_kg_by_species',
            None,
        )
        if not isinstance(recovered, dict):
            recovered = {}
            self._feedstock_recovered_reagent_kg_by_species = recovered
        recovered[species] = float(recovered.get(species, 0.0)) + float(kg)

    def _c3_alkali_credit_outstanding_kg_by_species(self) -> dict[str, float]:
        outstanding: dict[str, float] = {}
        for species in ('Na', 'K'):
            account = f'reservoir.reagent.{species}'
            balance_kg = float(
                self.atom_ledger.kg_by_account(account).get(species, 0.0)
            )
            if balance_kg < -self._LEDGER_KG_TOL:
                outstanding[species] = -balance_kg
        return outstanding

    def _top_up_c3_alkali_credit(self, species: str) -> float:
        # The C3 alkali dose is a steady-state reagent-inventory FLOOR, not a
        # one-time per-run cap: this runs each C3 tick and tops the shortfall
        # back up to the requested dose from the recycled credit line. So
        # ``c3_alkali_credit_drawn_kg_by_species`` accumulates GROSS draws
        # across replenishment cycles, while
        # ``c3_alkali_credit_outstanding_kg_by_species`` is the NET makeup
        # required per run — use OUTSTANDING (not drawn) for makeup accounting.
        requested_kg = self._c3_alkali_requested_dose_kg(species)
        if requested_kg <= self._LEDGER_KG_TOL:
            return 0.0
        current_kg = self._process_reagent_inventory_kg(species)
        shortfall_kg = max(0.0, requested_kg - current_kg)
        if shortfall_kg <= self._LEDGER_KG_TOL:
            return 0.0
        drawn_kg = self._draw_reagent_to_process(
            species,
            shortfall_kg,
            allow_credit=True,
        )
        self._record_c3_alkali_credit_draw(species, drawn_kg)
        if species == 'K':
            self.shuttle_K_inventory_kg = self._sync_reagent_counter_from_ledger('K')
        elif species == 'Na':
            self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')
        return drawn_kg

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
        for key in tuple(self._stage_collection_kg_by_source):
            if key[2] == species:
                self._stage_collection_kg_by_source.pop(key, None)

    def _condensed_species_projected_kg(self, species: str) -> float:
        return sum(
            max(0.0, float(stage.collected_kg.get(species, 0.0)))
            for stage in self.train.stages
        )

    def _stage_collection_backing_kg(self, species: str) -> float:
        return sum(
            self._ledger_account_species_kg(account, species)
            for account in STAGE_COLLECTION_BACKING_ACCOUNTS
        )

    def _record_stage_collection_source(
        self,
        source_account: str,
        stage_idx: int,
        species: str,
        delta_kg: float,
    ) -> None:
        key = (source_account, int(stage_idx), species)
        self._stage_collection_kg_by_source[key] = max(
            0.0,
            self._stage_collection_kg_by_source.get(key, 0.0)
            + float(delta_kg),
        )

    def _remove_stage_collection_source_projection(
        self, source_account: str, species: str, remove_kg: float
    ) -> float:
        remaining_kg = max(0.0, float(remove_kg))
        removed_kg = 0.0
        keys = sorted(
            (
                key
                for key in self._stage_collection_kg_by_source
                if key[0] == source_account and key[2] == species
            ),
            key=lambda key: key[1],
            reverse=True,
        )
        for key in keys:
            if remaining_kg <= self._LEDGER_KG_TOL:
                break
            tracked_kg = self._stage_collection_kg_by_source.get(key, 0.0)
            take_kg = min(tracked_kg, remaining_kg)
            stage_idx = key[1]
            stage = self.train.stages[stage_idx]
            projected_kg = max(
                0.0, float(stage.collected_kg.get(species, 0.0))
            )
            take_kg = min(take_kg, projected_kg)
            stage_remaining_kg = projected_kg - take_kg
            source_remaining_kg = tracked_kg - take_kg
            if stage_remaining_kg <= self._LEDGER_KG_TOL:
                stage.collected_kg.pop(species, None)
            else:
                stage.collected_kg[species] = stage_remaining_kg
            if source_remaining_kg <= self._LEDGER_KG_TOL:
                self._stage_collection_kg_by_source.pop(key, None)
            else:
                self._stage_collection_kg_by_source[key] = source_remaining_kg
            remaining_kg -= take_kg
            removed_kg += take_kg
        return removed_kg

    def _trim_condensed_species_projection(
        self, species: str, target_kg: float
    ) -> None:
        excess_kg = max(
            0.0,
            self._condensed_species_projected_kg(species)
            - max(0.0, float(target_kg)),
        )
        for stage in reversed(self.train.stages):
            if excess_kg <= self._LEDGER_KG_TOL:
                break
            current_kg = max(
                0.0, float(stage.collected_kg.get(species, 0.0))
            )
            remove_kg = min(current_kg, excess_kg)
            remaining_kg = current_kg - remove_kg
            if remaining_kg <= self._LEDGER_KG_TOL:
                stage.collected_kg.pop(species, None)
            else:
                stage.collected_kg[species] = remaining_kg
            excess_kg -= remove_kg

    def _audit_metal_projection_drift(self) -> Dict[str, float]:
        """0.5.4 W8 (M2 historical-audit closure, 2026-05-28):
        per-species drift between aggregate mol-native stage-collection backing
        accounts (metal-phase staging + diagnostic pools + condensation train)
        and the UI projection
        sum across ``train.stages[*].collected_kg``.

        Returns a dict ``{species: drift_kg}`` for species
        where the absolute drift exceeds ``_LEDGER_KG_TOL``. Sign
        convention: ``ledger_kg - projection_kg`` — positive when
        the combined backing accounts exceed the UI projection
        (some stage-collected mass has been credited but not yet
        projected), zero when in sync. Negative values identify
        projection mass without matching
        backing-account mass. Both signs remain visible so one-sided
        ledger or projection mutations cannot pass silently.

        Diagnostic only — does NOT raise on drift. The runner-strict
        result consumer remaps a nonempty audit to failed status. The
        ≤5e-12 % global mass-balance closure invariant
        (``HourSnapshot.mass_balance_error_pct``) remains the hard
        gate; this per-species view gives earlier-warning visibility
        when ledger ↔ UI drift opens up. Audit dict carried on
        ``HourSnapshot.metal_projection_drift_kg`` so external tools
        + tests can read it without touching simulator internals.
        """
        ledger_metals: Dict[str, float] = {}
        for account in STAGE_COLLECTION_BACKING_ACCOUNTS:
            for species, kg in self.atom_ledger.kg_by_account(account).items():
                try:
                    ledger_kg = float(kg)
                except (TypeError, ValueError):
                    warnings.warn(
                        "metal projection drift audit skipped malformed ledger "
                        f"value for {species!r} in {account!r}: {kg!r}",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
                ledger_metals[species] = ledger_metals.get(species, 0.0) + ledger_kg
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

    def _ensure_metal_phase_stratification_provider(self) -> None:
        from simulator.chemistry.kernel.capabilities import ChemistryIntent

        if self._chem_registry.authoritative_for(
            ChemistryIntent.METAL_PHASE_STRATIFICATION
        ) is not None:
            return
        from engines.builtin.metal_phase_stratification import (
            BuiltinMetalPhaseStratificationProvider,
        )

        self._chem_registry.register_idempotent(
            BuiltinMetalPhaseStratificationProvider(),
            [ChemistryIntent.METAL_PHASE_STRATIFICATION],
        )

    def _restore_metal_phase_staging(self) -> None:
        """Restore diagnostic pools before legacy physics reads metal staging."""

        from simulator.chemistry.kernel.capabilities import ChemistryIntent

        prior_pools = {
            'bottom_pool': dict(
                self.atom_ledger.mol_by_account(METAL_BOTTOM_POOL_ACCOUNT)
            ),
            'float_layer': dict(
                self.atom_ledger.mol_by_account(METAL_FLOAT_LAYER_ACCOUNT)
            ),
        }
        if not any(prior_pools.values()):
            return
        # The copy preserves pool identity across the temporary staging view;
        # committed ledger accounts remain the only mass authority.
        self._metal_phase_stratification_prior_pools_mol = prior_pools
        self._ensure_metal_phase_stratification_provider()
        controls = {
            'mode': 'restore_staging',
            'k_mix_per_hr': 0.0,
            'dt_hr': 0.0,
        }
        result = self._dispatch_only(
            ChemistryIntent.METAL_PHASE_STRATIFICATION,
            control_inputs=controls,
        )
        if result.transition is not None:
            self._commit_proposal(
                ChemistryIntent.METAL_PHASE_STRATIFICATION,
                result.transition,
                diagnostic=result.diagnostic,
                control_inputs=controls,
                transition_source='metal_phase_stratification_restore',
                transition_meta={'behavior_gate': 'diagnostic_only'},
            )

    def _step_metal_phase_stratification(self, equilibrium) -> None:
        """Commit diagnostic alloy disposition, then report density/film state."""

        from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
        from simulator.chemistry.kernel.capabilities import ChemistryIntent
        from simulator.material_densities import (
            alloy_density_kg_m3,
            alloy_density_uncertainty_relative_fraction,
            buoyancy_verdict,
            liquid_metal_density_provenance,
            material_density_data,
            resolve_melt_density_kg_m3,
        )
        from simulator.metal_stratification import (
            BOTTOM_POOL_SPECIES,
            FLOAT_LAYER_SPECIES,
            k_mix_from_axial_stirring,
            pool_weight_percent,
        )

        balances = self.atom_ledger.mol_by_account()
        classified_species = BOTTOM_POOL_SPECIES | FLOAT_LAYER_SPECIES
        if not any(
            any(
                species in classified_species and float(amount) > 0.0
                for species, amount in balances.get(account, {}).items()
            )
            for account in METAL_PHASE_ACCOUNTS
        ):
            self._last_metal_phase_stratification_diagnostic = {}
            self._metal_phase_stratification_prior_pools_mol = {}
            return

        self._ensure_metal_phase_stratification_provider()

        k_mix_per_hr = k_mix_from_axial_stirring(self.melt.stir_state.axial)
        temperature_K = float(self.melt.temperature_C) + 273.15
        melt_density, melt_density_tier = resolve_melt_density_kg_m3(
            getattr(equilibrium, 'liquid_density_kg_m3', None)
        )
        controls = {
            'mode': 'stratify',
            'k_mix_per_hr': k_mix_per_hr,
            'dt_hr': 1.0,
            'temperature_K': temperature_K,
            'melt_density_kg_m3': melt_density,
            'prior_pool_mol': dict(
                self._metal_phase_stratification_prior_pools_mol
            ),
        }
        result = self._dispatch_only(
            ChemistryIntent.METAL_PHASE_STRATIFICATION,
            control_inputs=controls,
        )
        if result.transition is not None:
            self._commit_proposal(
                ChemistryIntent.METAL_PHASE_STRATIFICATION,
                result.transition,
                diagnostic=result.diagnostic,
                control_inputs=controls,
                transition_source='metal_phase_stratification',
                transition_meta={'behavior_gate': 'diagnostic_only'},
            )

        pools_mol = {
            'bottom_pool': dict(
                self.atom_ledger.mol_by_account(METAL_BOTTOM_POOL_ACCOUNT)
            ),
            'float_layer': dict(
                self.atom_ledger.mol_by_account(METAL_FLOAT_LAYER_ACCOUNT)
            ),
        }
        self._metal_phase_stratification_prior_pools_mol = {
            pool_name: dict(species_mol)
            for pool_name, species_mol in pools_mol.items()
        }
        pool_reports: dict[str, dict[str, Any]] = {}
        for pool_name, species_mol in pools_mol.items():
            positive = {
                species: max(0.0, float(amount))
                for species, amount in species_mol.items()
                if float(amount) > 0.0
            }
            report: dict[str, Any] = {
                'species_mol': positive,
                'composition_wt_pct': pool_weight_percent(positive),
                'density_correlation_provenance': {
                    species: liquid_metal_density_provenance(
                        species, temperature_K
                    )
                    for species in sorted(positive)
                },
            }
            if positive:
                density = alloy_density_kg_m3(positive, temperature_K)
                report['density_kg_m3'] = density
                report['buoyancy'] = buoyancy_verdict(
                    density,
                    melt_density,
                    alloy_uncertainty_relative_fraction=(
                        alloy_density_uncertainty_relative_fraction(positive)
                    ),
                )
            pool_reports[pool_name] = report

        assumptions = material_density_data()['diagnostic_assumptions']
        reference_thickness = float(
            assumptions['coherent_float_layer_reference_thickness_m']
        )
        float_mol = pools_mol['float_layer']
        float_mass_kg = sum(
            max(0.0, float(amount))
            * float(ATOMIC_WEIGHTS_G_PER_MOL[species])
            / 1000.0
            for species, amount in float_mol.items()
            if species in ATOMIC_WEIGHTS_G_PER_MOL
        )
        float_density = float(
            pool_reports['float_layer'].get('density_kg_m3', 0.0) or 0.0
        )
        interface_area_m2 = max(1e-12, float(self.melt.melt_surface_area_m2))
        # Premise: a spread layer's volume is mass/density and V=A*h.
        # Algebra: h=m/(rho*A). Unit check: kg/[(kg/m3)*m2]=m. Sanity:
        # 0.54 kg Al-Si over the configured 0.2 m2 at ~2.7 Mg/m3 is 1 mm.
        thickness_m = (
            float_mass_kg / (float_density * interface_area_m2)
            if float_density > 0.0
            else 0.0
        )
        coverage_fraction = min(
            1.0,
            thickness_m / reference_thickness if reference_thickness > 0.0 else 0.0,
        )
        load_bearing = coverage_fraction >= 1.0
        has_metal = any(
            any(float(amount) > 0.0 for amount in species_mol.values())
            for species_mol in pools_mol.values()
        )
        has_unclassified = bool(
            dict(result.diagnostic or {}).get('unclassified_staging_mol')
        )
        if not has_metal and not has_unclassified:
            self._last_metal_phase_stratification_diagnostic = {}
            return

        self._last_metal_phase_stratification_diagnostic = {
            **dict(result.diagnostic or {}),
            'temperature_K': temperature_K,
            'melt_density_kg_m3': melt_density,
            'melt_density_tier': melt_density_tier,
            'melt_density_fallback_engaged': (
                melt_density_tier != 'engine_liquid_eos'
            ),
            'pools': pool_reports,
            'interface': {
                'area_m2': interface_area_m2,
                'float_layer_mass_kg': float_mass_kg,
                'equivalent_film_thickness_m': thickness_m,
                'coverage_reference_thickness_m': reference_thickness,
                'coverage_fraction_at_reference_thickness': coverage_fraction,
                'aggressive_float_tap_assumption_load_bearing': load_bearing,
            },
            'existing_extraction_behavior': 'unchanged_diagnostic_only',
        }

    def _project_condensed_species(
        self,
        stage_idx: int,
        species: str,
        delta_kg: float | None = None,
        *,
        source_account: str = 'process.condensation_train',
    ) -> None:
        if source_account not in STAGE_COLLECTION_BACKING_ACCOUNTS:
            raise ValueError(
                f'unsupported stage-collection source account: {source_account}'
            )
        backing_kg = self._stage_collection_backing_kg(species)
        if backing_kg <= self._LEDGER_KG_TOL:
            self._clear_condensed_species_projection(species)
            return

        projected = self._condensed_species_projected_kg(species)
        if projected > backing_kg + self._LEDGER_KG_TOL:
            self._trim_condensed_species_projection(species, backing_kg)
            projected = self._condensed_species_projected_kg(species)

        add_kg = (
            backing_kg - projected if delta_kg is None else float(delta_kg)
        )
        add_kg = min(
            max(0.0, add_kg), max(0.0, backing_kg - projected)
        )
        if add_kg <= self._LEDGER_KG_TOL:
            return
        current = max(
            0.0, float(self.train.stages[stage_idx].collected_kg.get(species, 0.0))
        )
        self._set_condensed_species_projection(stage_idx, species, current + add_kg)
        self._record_stage_collection_source(
            source_account, stage_idx, species, add_kg
        )

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
        like ``"<0.5"`` OR ``"canonical"`` to resolve from
        ``simulator.mre_ladder``'s graph-first canonical helper), optional ``campaign``
        (informational), optional ``note`` (informational), and optional
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
        temperature_K = None
        try:
            temperature_C = float(getattr(self.melt, 'temperature_C', 1600.0))
        except (TypeError, ValueError):
            temperature_C = 1600.0
        if temperature_C >= 1000.0:
            temperature_K = temperature_C + 273.15
        return mre_ladder.build_mre_voltage_sequence(
            self.setpoints,
            temperature_K=temperature_K,
        )

    @staticmethod
    def _finite_or_none(value: float | None) -> float | None:
        if value is None:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    def _c5_ellingham_derived_decomposition_voltage(
        self,
        oxide: str,
        *,
        temperature_K: float,
        pO2_bar: float,
        oxide_activity: float,
    ) -> tuple[float | None, str | None, str | None, bool, str | None]:
        metal_info = OXIDE_TO_METAL.get(str(oxide))
        if metal_info is None:
            return None, None, None, False, 'oxide_not_in_reduction_stoichiometry'

        metal_species, _n_met, n_oxy = metal_info
        n_electrons = 2.0 * float(n_oxy)
        if n_electrons <= 0.0:
            return None, str(metal_species), None, False, 'invalid_electron_count'

        reference = mre_ladder.mre_decomposition_voltage_reference(
            oxide,
            temperature_K=temperature_K,
        )
        if reference is None:
            return None, str(metal_species), None, False, 'decomposition_voltage_unavailable'
        standard_Ed_V = reference.voltage
        activity = max(float(oxide_activity), 1e-30)
        pO2 = max(float(pO2_bar), 1e-30)
        o2_mol_per_oxide = float(n_oxy) / 2.0
        nernst_V = (
            -((GAS_CONSTANT * float(temperature_K)) / (n_electrons * FARADAY))
            * math.log(activity)
            + ((GAS_CONSTANT * float(temperature_K)) / (n_electrons * FARADAY))
            * o2_mol_per_oxide
            * math.log(pO2)
        )
        status = None
        if not reference.authoritative:
            status = f'{reference.authority}:{reference.status}'
        return (
            standard_Ed_V + nernst_V,
            reference.ellingham_species or str(metal_species),
            reference.authority,
            reference.authoritative,
            status,
        )

    def _build_c5_ellingham_ladder_diagnostic(
        self,
        *,
        step_info: Mapping[str, Any],
        declared_rung_V: float,
        pO2_bar: float,
    ) -> dict[str, Any]:
        temperature_C = float(getattr(self.melt, 'temperature_C', 0.0))
        temperature_K = temperature_C + 273.15
        cleaned_melt_kg = AccountingQueries(self).species_kg_by_accounts(
            ('process.cleaned_melt',)
        )
        cleaned_melt_mol: dict[str, float] = {}
        for species, kg in cleaned_melt_kg.items():
            molar_mass = MOLAR_MASS.get(str(species))
            if molar_mass is None:
                continue
            try:
                kg_value = float(kg)
            except (TypeError, ValueError):
                continue
            if kg_value <= 0.0:
                continue
            cleaned_melt_mol[str(species)] = kg_value * 1000.0 / molar_mass
        rung_species = tuple(str(oxide) for oxide in step_info.get('species', ()) or ())

        ladder_steps = []
        for raw_entry in self._mre_voltage_sequence or ():
            if not isinstance(raw_entry, Mapping):
                continue
            voltage = mre_ladder.coerce_mre_decomposition_voltage(
                raw_entry.get('voltage')
            )
            if voltage is None:
                continue
            raw_species = raw_entry.get('species') or ()
            if isinstance(raw_species, str):
                species = (raw_species,)
            else:
                species = tuple(str(item) for item in raw_species if item)
            if not species:
                continue
            ladder_steps.append((float(voltage), species))

        species_rows: dict[str, Any] = {}
        for static_declared_V, species in ladder_steps:
            for oxide in species:
                activity_reference = melt_oxide_activity(oxide, cleaned_melt_mol)
                activity = (
                    0.0
                    if activity_reference is None
                    else max(0.0, float(activity_reference.activity))
                )
                (
                    derived_Ed_V,
                    metal_species,
                    voltage_authority,
                    voltage_authoritative,
                    status,
                ) = self._c5_ellingham_derived_decomposition_voltage(
                    oxide,
                    temperature_K=temperature_K,
                    pO2_bar=pO2_bar,
                    oxide_activity=activity,
                )
                species_rows[oxide] = {
                    'ellingham_species': metal_species,
                    'static_declared_V': static_declared_V,
                    'oxide_activity': activity,
                    'oxide_activity_model': 'gamma_x_single_cation',
                    'inventory_present': bool(
                        cleaned_melt_kg.get(oxide, 0.0) >= 1e-6
                    ),
                    'derived_Ed_V': self._finite_or_none(derived_Ed_V),
                    'delta_vs_declared_rung_V': self._finite_or_none(
                        None if derived_Ed_V is None else derived_Ed_V - declared_rung_V
                    ),
                    'delta_vs_static_declared_V': self._finite_or_none(
                        None if derived_Ed_V is None else derived_Ed_V - static_declared_V
                    ),
                    'declared_after_held_rung': bool(static_declared_V > declared_rung_V),
                    'voltage_authority': voltage_authority,
                    'voltage_authoritative': bool(voltage_authoritative),
                    'status': status or 'ok',
                }

        held_derived = {
            oxide: row['derived_Ed_V']
            for oxide, row in species_rows.items()
            if oxide in rung_species
        }
        ordering_divergences = [
            oxide
            for oxide, row in species_rows.items()
            if (
                oxide not in rung_species
                and row.get('declared_after_held_rung')
                and row.get('derived_Ed_V') is not None
                and float(row['derived_Ed_V']) <= declared_rung_V
            )
        ]
        derived_order = [
            oxide
            for oxide, row in sorted(
                species_rows.items(),
                key=lambda item: (
                    float('inf')
                    if item[1].get('derived_Ed_V') is None
                    else float(item[1]['derived_Ed_V']),
                    item[0],
                ),
            )
        ]
        declared_order = [
            oxide
            for _voltage, species in sorted(ladder_steps, key=lambda item: item[0])
            for oxide in species
        ]
        non_authoritative_voltage_by_oxide = {
            oxide: {
                'authority': row.get('voltage_authority'),
                'authoritative': False,
                'status': row.get('status', 'unknown'),
                'static_declared_V': row.get('static_declared_V'),
            }
            for oxide, row in species_rows.items()
            if not bool(row.get('voltage_authoritative'))
        }

        return {
            'schema': 'c5_ellingham_ladder_diagnostic_v1',
            'certification': 'diagnostic_uncertified',
            'authority': 'authoritative_ellingham_graph_with_static_fallback',
            'activity_basis': 'gamma_x_single_cation_cleaned_melt_account',
            'temperature_C': temperature_C,
            'temperature_K': temperature_K,
            'pO2_bar': float(pO2_bar),
            'declared_rung_V': float(declared_rung_V),
            'rung_species': list(rung_species),
            'derived_Ed_V': held_derived,
            'delta_vs_declared_rung_V': {
                oxide: species_rows[oxide]['delta_vs_declared_rung_V']
                for oxide in rung_species
                if oxide in species_rows
            },
            'reordering': {
                'ordering_divergence_detected': bool(ordering_divergences),
                'other_species_below_declared_rung': ordering_divergences,
                'derived_order_by_Ed': derived_order,
                'declared_order_by_static_voltage': declared_order,
            },
            'non_authoritative_voltage_by_oxide': non_authoritative_voltage_by_oxide,
            'species': species_rows,
        }

    def _route_mre_gas_products_to_condensation(
        self,
        gas_products_kg: Mapping[str, float],
    ) -> dict[str, Any]:
        species_kg = {
            str(species): float(kg)
            for species, kg in (gas_products_kg or {}).items()
            if float(kg) > 1e-12
        }
        if not species_kg:
            return {}

        evap_flux = EvaporationFlux(species_kg_hr=species_kg)
        evap_flux.update_totals()
        self._configure_condensation_operating_conditions(evap_flux)
        route_result = self.condensation_model.route(evap_flux, self.melt)
        route_diagnostic: dict[str, Any] = {}
        species_order = tuple(
            getattr(route_result, 'wall_route_species_order', ()) or (
                species_kg.keys()
            )
        )
        for species in species_order:
            if species not in species_kg:
                continue
            rate_kg_hr = species_kg[species]
            remaining_kg_hr = float(
                route_result.remaining_by_species.get(species, 0.0)
            )
            condensed_kg = max(0.0, rate_kg_hr - remaining_kg_hr)
            sp_data = dict(
                (self.vapor_pressures.get('metals', {}) or {}).get(
                    species, {}
                )
            )
            credited_condensed_kg = self._dispatch_condensation_route(
                species,
                condensed_kg,
                sp_data,
                route_result,
            )
            product_projection = self._condensed_products_kg(
                species,
                credited_condensed_kg,
                sp_data,
            )
            self._project_condensed_stage_collection(
                route_result,
                species,
                credited_condensed_kg,
                product_projection,
            )
            route_diagnostic[species] = {
                'gas_product_kg': rate_kg_hr,
                'credited_condensed_kg': float(credited_condensed_kg),
                'remaining_kg': float(max(0.0, remaining_kg_hr)),
                'product_projection_kg': dict(product_projection),
            }
        return route_diagnostic

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
        ``process.cleaned_melt`` (oxide consumed) and crediting either
        ``process.metal_phase`` (condensed-basis cathode metals) or
        ``process.overhead_gas`` (gas-basis products routed through
        the condensation train) +
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

        mre_diagnostic_state_before_step = {
            'uncertified_yield': dict(
                getattr(self, '_mre_uncertified_yield', {}) or {}),
            'ellingham_ladder_diagnostic': dict(
                getattr(self, '_mre_ellingham_ladder_diagnostic', {}) or {}),
            'effective_voltage_margin_by_oxide': dict(
                getattr(
                    self,
                    '_mre_effective_voltage_margin_V_by_oxide',
                    {},
                ) or {}
            ),
            'effective_voltage_margin_temperature_C': getattr(
                self,
                '_mre_effective_voltage_margin_temperature_C',
                None,
            ),
        }
        self._mre_uncertified_yield = {}
        self._mre_ellingham_ladder_diagnostic = {}
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
            self.melt.mre_declared_rung_V = 0.0
            self.melt.mre_current_A = 0.0
            return 0.0

        # --- Voltage and current selection (stepped holds) ---         [Step 9]
        c5_step_info: Mapping[str, Any] | None = None
        mre_replay_state_before_dispatch = {
            'hold_hours': self._mre_hold_hours,
            'voltage_step_idx': self._mre_voltage_step_idx,
            'rung_ever_effective': getattr(
                self, '_mre_rung_ever_effective', False),
            'sequence_complete_key': getattr(
                self, '_mre_c5_sequence_complete_key', None),
            'melt_c5_on_final_rung': getattr(
                self.melt, 'mre_c5_on_final_rung', False),
            'melt_c5_ladder_complete': getattr(
                self.melt, 'mre_c5_ladder_complete', False),
            'melt_declared_rung_V': getattr(
                self.melt, 'mre_declared_rung_V', 0.0),
            'uncertified_yield': dict(
                mre_diagnostic_state_before_step['uncertified_yield']),
            'ellingham_ladder_diagnostic': dict(
                mre_diagnostic_state_before_step[
                    'ellingham_ladder_diagnostic']),
            'effective_voltage_margin_by_oxide': dict(
                mre_diagnostic_state_before_step[
                    'effective_voltage_margin_by_oxide']),
            'effective_voltage_margin_temperature_C': (
                mre_diagnostic_state_before_step[
                    'effective_voltage_margin_temperature_C']
            ),
        }
        if self.melt.campaign == CampaignPhase.MRE_BASELINE:
            seq = self._mre_voltage_sequence
            if not seq:
                feo_voltage = mre_ladder.canonical_mre_decomposition_voltage(
                    "FeO",
                    temperature_K=float(self.melt.temperature_C) + 273.15,
                )
                voltage_V = min(
                    float(feo_voltage or 0.75) + self.melt.campaign_hour * 0.1,
                    2.5,
                )
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
            c5_rung_advanced = False
        else:
            # C5 limited MRE: EvalSpec/session fields are behavior determinants.
            c5_rung_advanced = False
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
                self.melt.mre_declared_rung_V = 0.0
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
                self.melt.mre_declared_rung_V = 0.0
                self.melt.mre_current_A = 0.0
                self.melt.mre_c5_ladder_complete = True
                return 0.0

            else:
                idx = min(self._mre_voltage_step_idx, len(seq) - 1)
                step_info = seq[idx]
                c5_step_info = step_info
                # Cell sits at the stage voltage cap; the ladder rung is the
                # accounting schedule, not a second applied voltage. Reported
                # voltage MUST be the dispatched one (solved==reported); the
                # declared rung is carried separately for endpoint/diagnostics.
                voltage_V = float(ladder_cap_V)
                self.melt.mre_declared_rung_V = float(step_info['voltage'])
                self.melt.mre_c5_on_final_rung = bool(idx >= len(seq) - 1)
                rung_allowed_oxides = set(step_info['species'])
                if allowed_oxides is not None:
                    rung_allowed_oxides &= set(allowed_oxides)

                self._mre_hold_hours += 1
                if self._mre_hold_hours >= step_info.get('min_hold_hours', 3):
                    c5_current_A = mre_ladder.C5_LIMITED_MRE_CURRENT_A
                    target_current_low = (
                        self._mre_effective_current_A < c5_current_A * 0.05)
                    rung_ever_effective = bool(
                        getattr(self, '_mre_rung_ever_effective', False)
                    )
                    rung_species_absent = all(
                        self.melt.composition_kg.get(oxide, 0.0) < 1e-6
                        for oxide in step_info['species']
                    )
                    present_rung_species = {
                        oxide
                        for oxide in rung_allowed_oxides
                        if self.melt.composition_kg.get(oxide, 0.0) >= 1e-6
                    }
                    prior_voltage_margins = mre_diagnostic_state_before_step[
                        'effective_voltage_margin_by_oxide'
                    ]
                    campaign_target_C, _ramp_rate = (
                        self.campaign_mgr.get_temp_target(
                            self.melt.campaign,
                            self.melt.campaign_hour,
                            self.melt,
                        )
                    )
                    at_campaign_temperature = (
                        campaign_target_C is None
                        or self.melt.temperature_C >= float(campaign_target_C)
                    )
                    margin_temperature_C = mre_diagnostic_state_before_step[
                        'effective_voltage_margin_temperature_C'
                    ]
                    margin_measured_at_campaign_temperature = (
                        campaign_target_C is None
                        or (
                            margin_temperature_C is not None
                            and float(margin_temperature_C)
                            >= float(campaign_target_C)
                        )
                    )
                    rung_present_but_unreachable = (
                        at_campaign_temperature
                        and margin_measured_at_campaign_temperature
                        and bool(present_rung_species)
                        and all(
                            prior_voltage_margins.get(
                                oxide, float('inf')
                            ) <= 0.0
                            for oxide in present_rung_species
                        )
                    )
                    safety_max_hold = (
                        self._mre_hold_hours
                        >= mre_ladder.C5_DEPLETION_SAFETY_MAX_HOLD_HR
                    )
                    depleted_after_effective = (
                        target_current_low
                        and (rung_ever_effective or rung_species_absent)
                    )
                    if (
                        depleted_after_effective
                        or rung_present_but_unreachable
                        or safety_max_hold
                    ):
                        self._mre_voltage_step_idx += 1
                        if idx >= len(seq) - 1:
                            self._mre_c5_sequence_complete_key = sequence_completion_key
                        self._mre_hold_hours = 0
                        self._mre_rung_ever_effective = False
                        c5_rung_advanced = True

            current_A = mre_ladder.C5_LIMITED_MRE_CURRENT_A
            # Declared C5 ladder holds are rung-scoped selectivity filters;
            # Nernst remains the physical reducibility gate inside the engine.
            c5_allowed_oxides = sorted(rung_allowed_oxides)

        pO2_bar = float(self._commanded_pO2_bar())
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
            'pO2_bar': pO2_bar,
        }
        melt_fO2_log = self._current_melt_redox_fO2_log()
        electrolysis_controls['melt_fO2_log'] = float(melt_fO2_log)
        if c5_allowed_oxides is not None:
            electrolysis_controls['allowed_oxides'] = c5_allowed_oxides
            from engines.builtin.metallothermic_step import (
                SPENT_REDUCTANT_RESIDUE_ACCOUNT,
            )
            residue_kg = self.atom_ledger.kg_by_account(
                SPENT_REDUCTANT_RESIDUE_ACCOUNT
            )
            for oxide in c5_allowed_oxides:
                self._move_ledger_species(
                    'c5_mre_spent_residue_to_cleaned_melt',
                    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
                    'process.cleaned_melt',
                    oxide,
                    residue_kg.get(oxide, 0.0),
                    reason=(
                        'C5 MRE treats melt-resident spent reductant residue '
                        'as electrolyzable cleaned melt for the active rung'
                    ),
                )
        c5_ellingham_ladder_diagnostic = {}
        if self.melt.campaign == CampaignPhase.C5 and c5_step_info is not None:
            try:
                c5_ellingham_ladder_diagnostic = (
                    self._build_c5_ellingham_ladder_diagnostic(
                        step_info=c5_step_info,
                        declared_rung_V=float(c5_step_info['voltage']),
                        pO2_bar=pO2_bar,
                    )
                )
            except Exception as exc:  # diagnostic-only: never gate C5 behavior
                c5_ellingham_ladder_diagnostic = {
                    'schema': 'c5_ellingham_ladder_diagnostic_v1',
                    'certification': 'diagnostic_uncertified',
                    'authority': 'authoritative_ellingham_graph_with_static_fallback',
                    'activity_basis': 'gamma_x_single_cation_cleaned_melt_account',
                    'status': f'diagnostic_failed:{type(exc).__name__}',
                    'declared_rung_V': float(c5_step_info['voltage']),
                    'rung_species': [
                        str(oxide)
                        for oxide in c5_step_info.get('species', ()) or ()
                    ],
                }
        kernel_result = self._dispatch_only(
            ChemistryIntent.ELECTROLYSIS_STEP,
            control_inputs=electrolysis_controls,
            fO2_log=float(melt_fO2_log),
            fe_redox_policy='kress91_live',
        )
        diagnostic = dict(kernel_result.diagnostic or {})
        self._mre_effective_voltage_margin_V_by_oxide = dict(
            diagnostic.get('mre_effective_voltage_margin_V_by_oxide') or {}
        )
        self._mre_effective_voltage_margin_temperature_C = (
            float(self.melt.temperature_C)
            if self._mre_effective_voltage_margin_V_by_oxide
            else None
        )
        if c5_ellingham_ladder_diagnostic:
            diagnostic['mre_ellingham_ladder_diagnostic'] = (
                c5_ellingham_ladder_diagnostic
            )
            self._mre_ellingham_ladder_diagnostic = dict(
                c5_ellingham_ladder_diagnostic
            )
        result = diagnostic  # legacy variable name -- same shape as step_hour's dict.
        self._mre_uncertified_yield = dict(
            result.get('uncertified_yield') or {})

        produced_metals = set(result.get('metals_produced_kg', {}) or {})
        produced_metals.update(result.get('metals_produced_mol', {}) or {})
        produced_gases = set(result.get('gas_products_produced_kg', {}) or {})
        produced_gases.update(result.get('gas_products_produced_mol', {}) or {})
        metal_before_kg = {
            metal: self._ledger_account_species_kg(
                'process.metal_phase', metal)
            for metal in produced_metals
        }
        gas_before_kg = {
            metal: self._ledger_account_species_kg(
                'process.overhead_gas', metal)
            for metal in produced_gases
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
            self.melt.mre_declared_rung_V = float(
                mre_replay_state_before_dispatch['melt_declared_rung_V'])
            self.melt.mre_current_A = 0.0
            self._mre_hold_hours = int(
                mre_replay_state_before_dispatch['hold_hours'])
            self._mre_voltage_step_idx = int(
                mre_replay_state_before_dispatch['voltage_step_idx'])
            self._mre_rung_ever_effective = bool(
                mre_replay_state_before_dispatch['rung_ever_effective'])
            self._mre_c5_sequence_complete_key = (
                mre_replay_state_before_dispatch['sequence_complete_key'])
            self.melt.mre_c5_on_final_rung = bool(
                mre_replay_state_before_dispatch['melt_c5_on_final_rung'])
            self.melt.mre_c5_ladder_complete = bool(
                mre_replay_state_before_dispatch['melt_c5_ladder_complete'])
            self._mre_uncertified_yield = dict(
                mre_replay_state_before_dispatch['uncertified_yield'])
            self._mre_ellingham_ladder_diagnostic = dict(
                mre_replay_state_before_dispatch[
                    'ellingham_ladder_diagnostic'])
            self._mre_effective_voltage_margin_V_by_oxide = dict(
                mre_replay_state_before_dispatch[
                    'effective_voltage_margin_by_oxide'])
            self._mre_effective_voltage_margin_temperature_C = (
                mre_replay_state_before_dispatch[
                    'effective_voltage_margin_temperature_C']
            )
            reason = diagnostic.get('reason_refused', 'electrolysis_step_refused')
            raise RuntimeError(f'MRE electrolysis refused: {reason}')
        if proposal is not None:
            transition = self._commit_proposal(
                ChemistryIntent.ELECTROLYSIS_STEP,
                proposal,
                diagnostic=diagnostic,
                control_inputs=electrolysis_controls,
            )
            self._apply_mre_anode_o2_redox_source_terms(
                transition,
                label='redox_source:mre_electrolysis_reduction',
                exchange_direction='redox_source:mre_electrolysis_reduction',
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

        mre_gas_deltas_kg: dict[str, float] = {}
        for metal in produced_gases:
            delta_kg = (
                self._ledger_account_species_kg(
                    'process.overhead_gas', metal)
                - gas_before_kg.get(metal, 0.0)
            )
            if delta_kg > 1e-10:
                mre_gas_deltas_kg[metal] = delta_kg
        gas_route_diagnostic = self._route_mre_gas_products_to_condensation(
            mre_gas_deltas_kg
        )
        if gas_route_diagnostic:
            result['mre_gas_condensation_route'] = gas_route_diagnostic
            for route in gas_route_diagnostic.values():
                for product, kg in (route.get('product_projection_kg') or {}).items():
                    amount = float(kg)
                    if amount > 1e-10:
                        mre_metal_deltas_kg[str(product)] = (
                            mre_metal_deltas_kg.get(str(product), 0.0)
                            + amount
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
        if (
            self.melt.campaign == CampaignPhase.C5
            and not c5_rung_advanced
            and self._mre_effective_current_A
            >= mre_ladder.C5_LIMITED_MRE_CURRENT_A * 0.05
        ):
            self._mre_rung_ever_effective = True

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

        K and Na reagent is supplied by the C3 alkali credit line
        (S2b): recovered Stage-4 condensate is transferred first, then
        the ``campaigns.C3.alkali_dosing`` request is topped up for the
        remaining shortfall by drawing against the negative-authorized
        ``reservoir.reagent.{Na,K}`` credit reservoirs.  The line models
        a steady-state recycled inventory, so the phase is NOT limited by
        the alkali harvested in the previous run.  Native melt Na₂O/K₂O
        is never mined into elemental reagent (BUG-069 stays off).  An
        explicit physical ``additives_kg`` charge, if present, is real
        pre-funded inventory that reduces the credit top-up need.

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
            na_additive_kg = self.record.additives_kg.get('Na', 0.0)
            if na_additive_kg > self._LEDGER_KG_TOL:
                self._activate_additive_reagent(
                    'Na',
                    na_additive_kg,
                )
            self._top_up_c3_alkali_credit('K')
            self._top_up_c3_alkali_credit('Na')
            self.shuttle_K_inventory_kg = self._sync_reagent_counter_from_ledger('K')
            self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')
            self.shuttle_cycle_K = 0

        elif campaign == CampaignPhase.C3_NA:
            self._activate_additive_reagent(
                'Na',
                self.record.additives_kg.get('Na', 0.0),
            )
            self._transfer_condensed_species('Na')
            self._transfer_condensed_species('K')
            self._top_up_c3_alkali_credit('Na')
            self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')
            self.shuttle_K_inventory_kg = self._sync_reagent_counter_from_ledger('K')
            self.shuttle_cycle_Na = 0

    def _recompute_staged_na_fe_hold_setpoint(self) -> None:
        """Repair a stranded staged Na/Fe hold from the live chemistry models."""

        from engines.builtin.metallothermic_step import (
            BuiltinMetallothermicStepProvider,
            REACTION_FAMILY_C3_NA,
        )
        from simulator.chemistry.kernel.capabilities import ChemistryIntent

        configured_temperature_C = float(
            self.campaign_mgr.get_temp_target(
                CampaignPhase.C3_NA,
                0,
                self.melt,
            )[0]
        )
        configured_margin = (
            BuiltinMetallothermicStepProvider
            ._reduction_margin_kj_per_mol_o2(
                'Na',
                'FeO',
                configured_temperature_C,
            )
        )
        if configured_margin > 0.0:
            self._last_c3_na_hold_adjustment = {}
            self.campaign_mgr.last_c3_na_hold_adjustment = None
            return

        curve = self._melt_redox_liquidus_gate_curve()
        crossover_temperature_C = (
            BuiltinMetallothermicStepProvider._crossover_temperature_C(
                'Na',
                'Fe',
            )
        )
        if not isinstance(curve, Mapping) or crossover_temperature_C is None:
            diagnostic = {
                'status': 'unavailable',
                'reason': 'na_fe_hold_window_authority_unavailable',
                'configured_temperature_C': configured_temperature_C,
                'configured_margin_kJ_per_mol_O2': configured_margin,
            }
            self._last_c3_na_hold_adjustment = diagnostic
            self.campaign_mgr.last_c3_na_hold_adjustment = diagnostic
            return

        solidus_temperature_C = float(curve['solidus_T_C'])
        rows: list[dict[str, Any]] = []
        feasible_rows: list[dict[str, Any]] = []
        true_feo_mol = self._cleaned_melt_available_mol_by_species(('FeO',))

        def liquid_fraction_at(temperature_C: float) -> float:
            try:
                return float(
                    self._interpolate_freeze_gate_curve(
                        curve,
                        temperature_C,
                    )
                )
            except (IndexError, KeyError, TypeError, ValueError):
                return 0.0

        boundary_tolerance_C = 1.0e-9

        def margin_at(temperature_C: float) -> float:
            return (
                BuiltinMetallothermicStepProvider
                ._reduction_margin_kj_per_mol_o2(
                    'Na',
                    'FeO',
                    temperature_C,
                )
            )

        margin_low_C = min(
            solidus_temperature_C,
            float(crossover_temperature_C) - 1.0,
        )
        margin_high_C = max(
            configured_temperature_C,
            float(crossover_temperature_C) + 1.0,
        )
        for _ in range(64):
            if margin_at(margin_low_C) > 0.0:
                break
            margin_low_C -= max(1.0, margin_high_C - margin_low_C)
        for _ in range(64):
            if margin_at(margin_high_C) <= 0.0:
                break
            margin_high_C += max(1.0, margin_high_C - margin_low_C)
        for _ in range(64):
            if margin_high_C - margin_low_C <= boundary_tolerance_C:
                break
            midpoint_C = (margin_low_C + margin_high_C) / 2.0
            if margin_at(midpoint_C) > 0.0:
                margin_low_C = midpoint_C
            else:
                margin_high_C = midpoint_C
        solved_crossover_temperature_C = (
            margin_low_C + margin_high_C
        ) / 2.0

        liquid_fraction_monotonicity_tolerance = 1.0e-9
        monotonicity_violation: dict[str, float] | None = None
        if margin_low_C > solidus_temperature_C:
            bracket_temperatures_C = {
                solidus_temperature_C,
                margin_low_C,
            }
            for point in tuple(curve.get('path') or ()):
                try:
                    sample_temperature_C = float(point[0])
                except (IndexError, TypeError, ValueError):
                    continue
                if (
                        solidus_temperature_C
                        < sample_temperature_C
                        < margin_low_C
                ):
                    bracket_temperatures_C.add(sample_temperature_C)
            previous_sample: tuple[float, float] | None = None
            for sample_temperature_C in sorted(bracket_temperatures_C):
                sample_liquid_fraction = liquid_fraction_at(
                    sample_temperature_C
                )
                if (
                        previous_sample is not None
                        and sample_liquid_fraction
                        + liquid_fraction_monotonicity_tolerance
                        < previous_sample[1]
                ):
                    monotonicity_violation = {
                        'earlier_temperature_C': previous_sample[0],
                        'earlier_liquid_fraction': previous_sample[1],
                        'later_temperature_C': sample_temperature_C,
                        'later_liquid_fraction': sample_liquid_fraction,
                    }
                    break
                previous_sample = (
                    sample_temperature_C,
                    sample_liquid_fraction,
                )

        if monotonicity_violation is not None:
            diagnostic = {
                'status': 'unavailable',
                'reason': 'lf_curve_non_monotone_window_unresolved',
                'configured_temperature_C': configured_temperature_C,
                'configured_margin_kJ_per_mol_O2': configured_margin,
                'crossover_temperature_C': solved_crossover_temperature_C,
                'boundary_tolerance_C': boundary_tolerance_C,
                'liquid_fraction_monotonicity_tolerance': (
                    liquid_fraction_monotonicity_tolerance
                ),
                'monotonicity_validation_window_T_min_C': (
                    solidus_temperature_C
                ),
                'monotonicity_validation_window_T_max_C': margin_low_C,
                'monotonicity_violation': monotonicity_violation,
                'liquid_fraction_curve': {
                    'source': curve.get('source'),
                    'solidus_T_C': curve.get('solidus_T_C'),
                    'liquidus_T_C': curve.get('liquidus_T_C'),
                    'path': list(curve.get('path') or ()),
                },
            }
            self._last_c3_na_hold_adjustment = diagnostic
            self.campaign_mgr.last_c3_na_hold_adjustment = diagnostic
            return

        first_positive_liquid_temperature_C: float | None = None
        if (
                margin_low_C > solidus_temperature_C
                and liquid_fraction_at(margin_low_C) > 0.0
        ):
            liquid_low_C = solidus_temperature_C
            liquid_high_C = margin_low_C
            for _ in range(64):
                if liquid_high_C - liquid_low_C <= boundary_tolerance_C:
                    break
                midpoint_C = (liquid_low_C + liquid_high_C) / 2.0
                if liquid_fraction_at(midpoint_C) > 0.0:
                    liquid_high_C = midpoint_C
                else:
                    liquid_low_C = midpoint_C
            first_positive_liquid_temperature_C = liquid_high_C

        candidate_temperatures_C: list[float] = []
        if first_positive_liquid_temperature_C is not None:
            candidate_temperatures_C.extend((
                first_positive_liquid_temperature_C,
                margin_low_C,
            ))
            for point in tuple(curve.get('path') or ()):
                try:
                    sample_temperature_C = float(point[0])
                except (IndexError, TypeError, ValueError):
                    continue
                if (
                        first_positive_liquid_temperature_C
                        <= sample_temperature_C
                        <= margin_low_C
                ):
                    candidate_temperatures_C.append(sample_temperature_C)
        candidate_temperatures_C = sorted(set(candidate_temperatures_C))

        # Premise: feasibility is the intersection of positive liquid fraction
        # and positive Na/Fe reduction margin. Method: bisect both governing
        # curves to their boundary knots, add the liquid-fraction curve's own
        # sample knots, then let the provider accept/refuse and score each row.
        # Tolerance: 1e-9 C is an accepted residual floor; a narrower feasible
        # window may be reported empty, but is physically meaningless against
        # whole-degree thermal-control granularity. Sanity: for (1100, 0),
        # (1181.2, 0), (1181.4, 0.1), this samples the real sub-degree window
        # below the ~1181.4948 C Na/Fe crossover instead of falsely returning
        # na_fe_hold_window_empty.
        for temperature_C in candidate_temperatures_C:
            margin = margin_at(temperature_C)
            liquid_fraction = liquid_fraction_at(temperature_C)
            row: dict[str, Any] = {
                'temperature_C': temperature_C,
                'margin_kJ_per_mol_O2': margin,
                'liquid_fraction': liquid_fraction,
                'status': 'outside_joint_window',
                'Fe_produced_kg': 0.0,
            }
            result = self._dispatch_only(
                ChemistryIntent.METALLOTHERMIC_STEP,
                control_inputs={
                    'reaction_family': REACTION_FAMILY_C3_NA,
                    'na_target_stage': 'feo_cleanup',
                    'reagent_available_kg': float(
                        self.shuttle_Na_inventory_kg
                    ),
                    'true_available_mol_by_species': true_feo_mol,
                    'liquid_fraction': liquid_fraction,
                    'dt_hr': 1.0,
                },
                temperature_C_override=temperature_C,
            )
            result_diagnostic = dict(result.diagnostic or {})
            fe_produced_kg = float(
                dict(result_diagnostic.get('per_metal_produced_kg') or {})
                .get('Fe', 0.0)
            )
            row.update({
                'status': str(result.status),
                'reason_refused': str(
                    result_diagnostic.get('reason_refused') or ''
                ),
                'Fe_produced_kg': fe_produced_kg,
            })
            if (
                    result.status == 'ok'
                    and result.transition is not None
                    and fe_produced_kg > 0.0
            ):
                feasible_rows.append(row)
            rows.append(row)

        diagnostic = {
            'status': 'empty',
            'reason': 'na_fe_hold_window_empty',
            'configured_temperature_C': configured_temperature_C,
            'configured_margin_kJ_per_mol_O2': configured_margin,
            'crossover_temperature_C': solved_crossover_temperature_C,
            'crossover_temperature_hint_C': float(crossover_temperature_C),
            'joint_window_T_min_exclusive_C': (
                first_positive_liquid_temperature_C
            ),
            'first_positive_liquid_temperature_C': (
                first_positive_liquid_temperature_C
            ),
            'joint_window_T_max_exclusive_C': solved_crossover_temperature_C,
            'boundary_tolerance_C': boundary_tolerance_C,
            'accepted_residual_window_floor_C': boundary_tolerance_C,
            'feasibility_authority': (
                'BuiltinMetallothermicStepProvider.dispatch'
            ),
            'liquid_fraction_curve': {
                'source': curve.get('source'),
                'solidus_T_C': curve.get('solidus_T_C'),
                'liquidus_T_C': curve.get('liquidus_T_C'),
                'path': list(curve.get('path') or ()),
            },
            'objective': 'Fe_produced_kg',
            'candidate_rule': 'solved boundaries plus curve sample knots',
            'rows': rows,
            'selection_tie_break': (
                'closest feasible argmax to configured_temperature_C'
            ),
        }
        if feasible_rows:
            max_fe_kg = max(row['Fe_produced_kg'] for row in feasible_rows)
            tied = [
                row for row in feasible_rows
                if math.isclose(
                    row['Fe_produced_kg'],
                    max_fe_kg,
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
            ]
            selected = min(
                tied,
                key=lambda row: (
                    abs(row['temperature_C'] - configured_temperature_C),
                    row['temperature_C'],
                ),
            )
            selected_temperature_C = float(selected['temperature_C'])
            active = self.campaign_mgr._active_c3_na_scoped_overrides
            if isinstance(active, dict):
                active['inject_target_C'] = selected_temperature_C
            diagnostic.update({
                'status': 'applied',
                'reason': 'na_fe_hold_recomputed',
                'applied_field': 'inject_target_C',
                'selected_temperature_C': selected_temperature_C,
                'selected_Fe_produced_kg': float(
                    selected['Fe_produced_kg']
                ),
            })
        self._last_c3_na_hold_adjustment = diagnostic
        self.campaign_mgr.last_c3_na_hold_adjustment = diagnostic

    def _step_shuttle(self):
        """
        Perform one hour of alkali metallothermic shuttle processing.

        The C3 campaign alternates between injection and bakeout sub-phases
        on a 6-hour cycle (3 hrs inject, 3 hrs bakeout):

        **Injection** (T ~1200-1350°C):                          [THERMO-5]
            K phase:  2K(g) + FeO(melt) → K₂O(melt) + Fe(l)
                      4K(g) + SiO₂(melt) → 2K₂O(melt) + Si(l)  [conditioning]
            Na phase: 2Na(g) + TiO₂(melt) → Na₂O(melt, spent residue) + Ti(l)
                      6Na(g) + Cr₂O₃(melt) → 3Na₂O(melt, spent residue) + 2Cr(l)

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
            self._top_up_c3_alkali_credit('K')
            self._top_up_c3_alkali_credit('Na')
        elif campaign == CampaignPhase.C3_NA:
            self._transfer_condensed_species('Na')
            self._top_up_c3_alkali_credit('Na')

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
                # Just entered bakeout. Defer the diagnostic counter until the
                # hour snapshots successfully; zero-transition aborts must be
                # replayable without mutating state.
                if campaign == CampaignPhase.C3_K:
                    self._pending_shuttle_bakeout_cycle_increment = campaign.name
                elif campaign == CampaignPhase.C3_NA:
                    self._pending_shuttle_bakeout_cycle_increment = campaign.name

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

        transition = self._commit_proposal(
            ChemistryIntent.METALLOTHERMIC_STEP,
            proposal,
            diagnostic=diagnostic,
            control_inputs={
                'reaction_family': REACTION_FAMILY_C3_K,
                'dt_hr': 1.0,
            },
        )
        self._apply_transition_redox_source_terms(
            transition,
            label='redox_source:c3_k_shuttle_reduction',
            target_oxides=('FeO',),
            exchange_direction='redox_source:c3_k_shuttle_reduction',
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
                    → 1.348 kg melt-resident spent-residue Na₂O
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

        transition = self._commit_proposal(
            ChemistryIntent.METALLOTHERMIC_STEP,
            proposal,
            diagnostic=diagnostic,
            control_inputs={
                'reaction_family': REACTION_FAMILY_C3_NA,
                'dt_hr': 1.0,
            },
        )
        self._apply_transition_redox_source_terms(
            transition,
            label='redox_source:c3_na_shuttle_reduction',
            target_oxides=('FeO', 'Cr2O3', 'TiO2'),
            exchange_direction='redox_source:c3_na_shuttle_reduction',
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
        if recovered_kg <= self._LEDGER_KG_TOL:
            self._trim_condensed_species_projection(
                species, self._stage_collection_backing_kg(species)
            )
            return 0.0
        moved_kg = self._move_ledger_species(
            f'recover_{species}_to_reagent_inventory',
            source_account,
            'process.reagent_inventory',
            species,
            recovered_kg,
            reason=f'recovered {species} condensate transfer',
        )
        self._remove_stage_collection_source_projection(
            source_account, species, moved_kg
        )
        self._trim_condensed_species_projection(
            species, self._stage_collection_backing_kg(species)
        )
        self._move_cost_inventory_lots_best_effort(
            source_account=source_account,
            destination_account='process.reagent_inventory',
            species=species,
            quantity_kg=moved_kg,
            reason=f'recovered {species} condensate transfer',
        )
        non_feedstock_moved_kg = self._consume_non_feedstock_reagent_element(
            source_account,
            species,
            moved_kg,
            recovered_kg,
        )
        self._record_feedstock_recovered_reagent(
            species,
            max(0.0, moved_kg - non_feedstock_moved_kg),
        )
        if species == 'K':
            self.shuttle_K_inventory_kg = self._sync_reagent_counter_from_ledger('K')
        elif species == 'Na':
            self.shuttle_Na_inventory_kg = self._sync_reagent_counter_from_ledger('Na')
        elif species == 'Mg':
            self.thermite_Mg_inventory_kg = self._sync_reagent_counter_from_ledger('Mg')
        return recovered_kg

    def _record_c6_refusal(self, diagnostic: Mapping[str, Any]) -> None:
        self._last_c6_refusal_diagnostic = {
            'status': 'refused',
            'reaction_family': 'c6_mg_thermite',
            'hour': int(self.melt.hour),
            'campaign_hour': int(self.melt.campaign_hour),
            'campaign': self.melt.campaign.name,
            'temperature_C': float(self.melt.temperature_C),
            'diagnostic': dict(diagnostic),
        }
        self._c6_campaign_refused = True

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

        c6_cfg = ((self.setpoints or {}).get('campaigns', {}) or {}).get('C6', {}) or {}
        window_by_feedstock = c6_cfg.get('static_window_by_feedstock', {}) or {}
        window = window_by_feedstock.get(self.record.feedstock_key, {}) or {}
        if isinstance(window, Mapping) and window.get('status') == 'refused':
            self._record_c6_refusal({
                'reason_refused': str(window.get('reason_refused') or ''),
                'liquid_fraction': float(
                    window.get('liquid_fraction_at_hold', 0.0) or 0.0),
                'joint_window': dict(window.get('joint_window', {}) or {}),
                'source': str(window.get('source') or ''),
            })
            self._chem_no_op_dispatch_count += 1
            return

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
                'JANAF_4th_multiphase_margin_kJ_per_mol_O2': dict(
                    c6_cfg.get('JANAF_4th_multiphase_margin_kJ_per_mol_O2')
                    or {}
                ),
                'kinetic_driven_above_crossover': bool(
                    c6_cfg.get('kinetic_driven_above_crossover')
                ),
                'kinetic_note': str(c6_cfg.get('kinetic_note') or ''),
                'dt_hr': 1.0,
            },
        )
        primary_diag = dict(primary_result.diagnostic or {})
        primary_proposal = primary_result.transition
        if primary_proposal is None:
            if getattr(primary_result, 'status', '') == 'refused':
                self._record_c6_refusal(primary_diag)
            # F-A4: counter mirrors the _dispatch_and_commit helper.
            self._chem_no_op_dispatch_count += 1
            return

        primary_transition = self._commit_proposal(
            ChemistryIntent.METALLOTHERMIC_STEP,
            primary_proposal,
            diagnostic=primary_diag,
            control_inputs={
                'reaction_family': REACTION_FAMILY_C6_MG,
                'dt_hr': 1.0,
            },
        )
        self._apply_transition_redox_source_terms(
            primary_transition,
            label='redox_source:c6_mg_thermite_primary',
            target_oxides=('Al2O3', 'MgO'),
            exchange_direction='redox_source:c6_mg_thermite_primary',
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
                'liquid_fraction': liquid_fraction,
                'JANAF_4th_multiphase_margin_kJ_per_mol_O2': dict(
                    c6_cfg.get('JANAF_4th_multiphase_margin_kJ_per_mol_O2')
                    or {}
                ),
                'kinetic_driven_above_crossover': bool(
                    c6_cfg.get('kinetic_driven_above_crossover')
                ),
                'kinetic_note': str(c6_cfg.get('kinetic_note') or ''),
                'dt_hr': 1.0,
            },
        )
        back_diag = dict(back_result.diagnostic or {})
        back_proposal = back_result.transition
        back_si_before_kg = self._ledger_account_species_kg(
            'process.metal_phase', 'Si')
        if back_proposal is not None:
            back_transition = self._commit_proposal(
                ChemistryIntent.METALLOTHERMIC_STEP,
                back_proposal,
                diagnostic=back_diag,
                control_inputs={
                    'reaction_family': REACTION_FAMILY_C6_MG,
                    'back_reduction': True,
                    'dt_hr': 1.0,
                },
            )
            self._apply_c6_back_reduction_redox_source_terms(
                back_transition,
                label='redox_source:c6_mg_thermite_back_reduction',
                exchange_direction='redox_source:c6_mg_thermite_back_reduction',
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

        # Net Al / Al2O3 deltas after back-reduction (legacy snapshot
        # semantics: counters track NET removed Al2O3 and NET produced Al).
        # The kernel ledger is the source of truth for whether the cascade
        # committed; committed back-reduction diagnostics must reduce the
        # telemetry counters too, not only the no-op path.
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

    def _c7_campaign_config(self) -> dict:
        cfg = dict(
            self.campaign_mgr._campaign_config(
                CampaignPhase.C7_CA_ALUMINOTHERMIC
            )
        )
        ovr = self.campaign_mgr._campaign_overrides(
            CampaignPhase.C7_CA_ALUMINOTHERMIC
        )
        cfg.update(dict(ovr))
        return cfg

    @staticmethod
    def _c7_bool(value, default: bool = False) -> bool:
        if value is None:
            return bool(default)
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'on'}
        return bool(value)

    @staticmethod
    def _c7_nested(value) -> dict:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _c7_float(value, default: float = 0.0) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return float(default)
        return result if math.isfinite(result) else float(default)

    @staticmethod
    def _c7_clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, float(value)))

    def _c7_knob_diag(
        self,
        path: str,
        requested: float,
        applied: float,
        reason: str,
    ) -> dict:
        return {
            'path': f'campaigns.C7.{path}',
            'requested': requested,
            'applied': applied,
            'reason': reason,
            'saturated': not math.isclose(
                float(requested),
                float(applied),
                rel_tol=0.0,
                abs_tol=1e-15,
            ),
        }

    def _init_c7_al_credit(self) -> None:
        if getattr(self, '_c7_al_credit_funded', False):
            return
        cfg = self._c7_campaign_config()
        credit_kg = max(0.0, self._c7_float(cfg.get('al_credit_limit_kg'), 0.0))
        self._c7_al_credit_funded = True
        self._c7_al_credit_input_kg = 0.0
        if credit_kg <= self._LEDGER_KG_TOL:
            return
        # WRITER-EXEMPT: c7-al-credit-funding external input, not a chemistry transition.
        self.atom_ledger.load_external(
            C7_AL_CREDIT_ACCOUNT,
            {'Al': credit_kg},
            source='C7 imported Al credit line',
        )
        self._add_non_feedstock_reagent_element(
            C7_AL_CREDIT_ACCOUNT,
            'Al',
            credit_kg,
        )
        self.cost_ledger.seed_external_material(
            account=C7_AL_CREDIT_ACCOUNT,
            species='Al',
            quantity_kg=credit_kg,
            provenance={
                'source': 'C7 imported Al credit line',
                'source_tag': 'owner-ratify-placeholder:external-reagent-seed',
                'ticket': 'COST-PARAM-REAGENT-KG',
            },
        )
        self._c7_al_credit_input_kg = credit_kg

    def _c7_residual_ceramic_report(self) -> dict:
        aluminate_species = {'Ca3Al2O6', 'Ca12Al14O33'}
        residual_kg = 0.0
        ree_kg = 0.0
        for account in ('process.cleaned_melt', 'terminal.slag'):
            for species, kg in self.atom_ledger.kg_by_account(account).items():
                amount = max(0.0, float(kg))
                if account == 'terminal.slag' and species in aluminate_species:
                    continue
                residual_kg += amount
                if species == 'REE_oxides':
                    ree_kg += amount
        wt_pct = (ree_kg / residual_kg * 100.0) if residual_kg > 0.0 else 0.0
        return {
            'REE_oxides_kg': ree_kg,
            'REE_oxides_wt_pct': wt_pct,
            'residual_terminal_ceramic_kg': residual_kg,
        }

    def _c7_stoich(self, mode: str) -> dict:
        if str(mode).upper() == 'C12A7':
            return {
                'CaO': 33.0,
                'Al': 14.0,
                'Ca': 21.0,
                'aluminate_species': 'Ca12Al14O33',
            }
        return {
            'CaO': 6.0,
            'Al': 2.0,
            'Ca': 3.0,
            'aluminate_species': 'Ca3Al2O6',
        }

    def _c7_transport_extent_mol(
        self,
        cfg: dict,
        *,
        ca_per_extent: float,
    ) -> tuple[float, dict]:
        from engines.builtin.ca_aluminothermic_step import (
            BuiltinCaAluminothermicStepProvider,
            C7_MAX_TOTAL_PRESSURE_MBAR,
            C7_MIN_TOTAL_PRESSURE_MBAR,
        )
        from engines.builtin.evaporation_flux import (
            _series_resistance_evaporation_flux_kg_m2_s,
        )

        requested_stir = self._c7_float(
            cfg.get('stir_factor'),
            getattr(self.melt.stir_state, 'axial', self.melt.stir_factor),
        )
        clamped_stir = self._c7_clamp(requested_stir, 0.0, 10.0)
        cold_skull_safe = self._c7_float(
            cfg.get('c7_cold_skull_safe_stir_factor'), 6.0)
        applied_stir = min(clamped_stir, max(0.0, cold_skull_safe))
        hold_temp_C = self._c7_float(
            cfg.get('hold_temp_C', cfg.get('default_hold_T_C')), 1200.0)
        hold_temp_K = hold_temp_C + 273.15
        p_total_raw = cfg.get('p_total_mbar')
        if p_total_raw is None:
            p_total_raw = cfg.get('p_total_mbar_default')
        p_total_mbar = self._c7_float(p_total_raw, self.melt.p_total_mbar)
        route_controls = {
            'active_ca_condensation_route': self._c7_bool(
                cfg.get('active_ca_condensation_route'), True),
            'dedicated_ca_condenser': self._c7_bool(
                cfg.get('dedicated_ca_condenser'), True),
            'ca_condensation_species': str(
                cfg.get('ca_condensation_species') or 'Ca'),
            'ca_condenser_temperature_C': self._c7_float(
                cfg.get('ca_condenser_temperature_C'), 780.0),
        }
        active_route = (
            BuiltinCaAluminothermicStepProvider
            ._has_dedicated_ca_route(route_controls)
        )
        area_m2 = max(
            0.0,
            self._c7_float(
                cfg.get('ca_route_surface_area_m2'),
                getattr(self.melt, 'melt_surface_area_m2', 0.2),
            ),
        )
        hold_time_h = max(0.0, self._c7_float(cfg.get('hold_time_h'), 1.0))
        ca_entry = dict(
            (getattr(self, 'vapor_pressures', {}) or {})
            .get('metals', {})
            .get('Ca', {})
            or {}
        )
        antoine = dict(ca_entry.get('pure_component_antoine') or {})
        alpha_entry = dict(ca_entry.get('evaporation_alpha') or {})
        ca_alpha = self._c7_float(alpha_entry.get('value'), 0.0)
        p_sat_pa = 0.0
        if antoine:
            try:
                p_sat_pa = 10.0 ** (
                    float(antoine.get('A', 0.0))
                    - float(antoine.get('B', 0.0))
                    / (hold_temp_K + float(antoine.get('C', 0.0)))
                )
            except (OverflowError, TypeError, ValueError, ZeroDivisionError):
                p_sat_pa = 0.0
        p_bulk_pa = max(
            0.0,
            self._c7_float(
                getattr(self.overhead, 'composition', {}).get('Ca', 0.0),
                0.0,
            )
            * 100.0,
        )
        overhead_pressure_pa = max(0.0, p_total_mbar * 100.0)
        series_config = dict(
            (getattr(self, 'setpoints', {}) or {})
            .get('chemistry_kernel', {})
            .get('evaporation_series_resistance', {})
            or {}
        )
        carrier_resolver = getattr(self, '_resolve_condensation_carrier_gas', None)
        carrier_gas = carrier_resolver() if callable(carrier_resolver) else 'N2'
        gas_temperature_K = float(
            getattr(self.overhead, 'headspace_temperature_K', 0.0) or hold_temp_K
        )
        if active_route and (
            C7_MIN_TOTAL_PRESSURE_MBAR <= p_total_mbar <= C7_MAX_TOTAL_PRESSURE_MBAR
        ):
            series_flux = _series_resistance_evaporation_flux_kg_m2_s(
                species='Ca',
                P_eq_pa=p_sat_pa,
                P_bulk_pa=p_bulk_pa,
                T_surface_K=hold_temp_K,
                molar_mass_kg_mol=MOLAR_MASS['Ca'] / 1000.0,
                alpha_i=ca_alpha,
                knudsen_number=None,
                pipe_diameter_m=float(
                    getattr(self.overhead_model, 'pipe_diameter_m', 0.12)
                ),
                overhead_pressure_pa=overhead_pressure_pa,
                axial_stir_factor=requested_stir,
                radial_stir_factor=self._c7_float(
                    cfg.get('radial_stir_factor'), 1.0),
                cold_skull_envelope={
                    'frozen_skull_stir_ceiling': max(0.0, cold_skull_safe)
                },
                carrier_gas=carrier_gas,
                T_gas_K=gas_temperature_K,
                melt_resistance_enabled=bool(
                    series_config.get('melt_resistance_enabled', True)
                ),
                gas_resistance_enabled=bool(
                    series_config.get('gas_resistance_enabled', True)
                ),
                melt_surface_renewal_base_kg_s_m2_pa=self._c7_float(
                    series_config.get('melt_surface_renewal_base_kg_s_m2_pa'),
                    1.0e-4,
                ),
                melt_surface_renewal_source=str(
                    series_config.get(
                        'melt_surface_renewal_source',
                        'owner-ratify:melt-side-surface-renewal-v1',
                    )
                ),
            )
        else:
            series_flux = _series_resistance_evaporation_flux_kg_m2_s(
                species='Ca',
                P_eq_pa=0.0,
                P_bulk_pa=p_bulk_pa,
                T_surface_K=hold_temp_K,
                molar_mass_kg_mol=MOLAR_MASS['Ca'] / 1000.0,
                alpha_i=ca_alpha,
            )
        hold_time_s = hold_time_h * 3600.0
        ca_transport_mol = (
            series_flux.flux_kg_s_m2
            * area_m2
            * hold_time_s
            / max(MOLAR_MASS['Ca'] / 1000.0, 1e-30)
        )
        extent_mol = ca_transport_mol / max(ca_per_extent, 1e-30)
        resistances = {
            'melt': series_flux.r_melt,
            'interface': series_flux.r_interface,
            'gas': series_flux.r_gas,
        }
        finite_resistances = {
            key: value
            for key, value in resistances.items()
            if math.isfinite(value)
        }
        limiting = (
            max(finite_resistances, key=finite_resistances.get)
            if finite_resistances
            else 'none'
        )
        saturation = [
            self._c7_knob_diag(
                'stir_factor',
                requested_stir,
                clamped_stir,
                'clamped_to_supported_envelope',
            ),
            self._c7_knob_diag(
                'stir_factor',
                clamped_stir,
                applied_stir,
                'cold_skull_safe_stir_factor',
            ),
        ]
        if getattr(series_flux, 'frozen_skull_stir_clamped', False):
            saturation.append(
                self._c7_knob_diag(
                    'stir_factor',
                    clamped_stir,
                    applied_stir,
                    'series_resistance_frozen_skull_stir_ceiling',
                )
            )
        return extent_mol, {
            'r_transport': extent_mol,
            'transport_ca_mol': ca_transport_mol,
            'c7_ca_flux_kg_s_m2': series_flux.flux_kg_s_m2,
            'c7_ca_p_sat_pa': p_sat_pa,
            'c7_ca_p_bulk_pa': p_bulk_pa,
            'c7_ca_alpha_intrinsic': ca_alpha,
            'c7_ca_alpha_effective': series_flux.alpha_effective,
            'c7_ca_route_surface_area_m2': area_m2,
            'c7_hold_time_s': hold_time_s,
            'c7_overhead_pressure_pa': overhead_pressure_pa,
            'c7_knudsen_number': series_flux.knudsen_number,
            'R_melt': series_flux.r_melt,
            'R_interface': series_flux.r_interface,
            'R_gas': series_flux.r_gas,
            'R_total': series_flux.r_melt + series_flux.r_interface + series_flux.r_gas,
            'limiting_resistance': limiting,
            'stir_requested': requested_stir,
            'stir_applied': applied_stir,
            'c7_transport_source': 'series_resistance_hkl_ca_evaporation',
            'c7_transport_refusal': (
                ''
                if active_route
                and C7_MIN_TOTAL_PRESSURE_MBAR
                <= p_total_mbar
                <= C7_MAX_TOTAL_PRESSURE_MBAR
                else 'no_active_route_or_pressure_outside_vacuum_envelope'
            ),
            'c7_knob_saturation': saturation,
        }

    def _step_c7_ca_aluminothermic(self) -> None:
        from simulator.chemistry.kernel.capabilities import ChemistryIntent
        from engines.builtin.ca_aluminothermic_step import (
            REACTION_FAMILY_C7_CA_ALUMINOTHERMIC,
        )

        cfg = self._c7_campaign_config()
        if not self._c7_bool(cfg.get('enabled'), False):
            self._last_c7_diagnostic = {'enabled': False}
            return
        if getattr(self, '_c7_aluminothermic_applied', False):
            return
        self._c7_aluminothermic_applied = True

        self._last_c7_diagnostic = {}
        self._last_c7_refusal_diagnostic = {}
        before = self._c7_residual_ceramic_report()
        mode = str(cfg.get('aluminate_mode') or 'C3A').upper()
        stoich = self._c7_stoich(mode)
        al_fraction_raw = self._c7_float(cfg.get('al_fraction'), 1.0)
        al_fraction = self._c7_clamp(al_fraction_raw, 0.0, 1.0)
        extent_fraction_raw = self._c7_float(cfg.get('extent_fraction'), 1.0)
        extent_fraction = self._c7_clamp(extent_fraction_raw, 0.0, 1.0)

        balances = self.atom_ledger.mol_by_account()
        cleaned_cao = max(
            0.0, float(balances.get('process.cleaned_melt', {}).get('CaO', 0.0))
        )
        slag_cao = max(0.0, float(balances.get('terminal.slag', {}).get('CaO', 0.0)))
        cao_available = cleaned_cao + slag_cao
        in_situ_al_available = sum(
            max(0.0, float(balances.get(account, {}).get('Al', 0.0)))
            for account in (METAL_PHASE_ACCOUNT, METAL_FLOAT_LAYER_ACCOUNT)
        )
        in_situ_al_budget = in_situ_al_available * al_fraction
        credit_al_available = max(
            0.0, float(balances.get(C7_AL_CREDIT_ACCOUNT, {}).get('Al', 0.0))
        )
        total_al_budget = in_situ_al_budget + credit_al_available
        if total_al_budget <= 0.0 or cao_available <= 0.0:
            self._last_c7_refusal_diagnostic = {
                'reason_refused': 'c7_al_or_cao_budget_empty',
                'cao_available_mol': cao_available,
                'al_budget_mol': total_al_budget,
            }
            self._c7_product_report = self._build_c7_product_report(
                before, before, {}
            )
            return

        r_stoich_total = min(
            cao_available / stoich['CaO'],
            total_al_budget / stoich['Al'],
        )
        r_transport, transport_diag = self._c7_transport_extent_mol(
            cfg, ca_per_extent=stoich['Ca'])
        objective_extent = min(r_stoich_total, r_transport)
        objective = str(cfg.get('objective') or 'ree_enrichment')
        p_total_raw = cfg.get('p_total_mbar')
        if p_total_raw is None:
            p_total_raw = cfg.get('p_total_mbar_default')
        pO2_raw = cfg.get('pO2_mbar')
        if pO2_raw is None:
            pO2_raw = cfg.get('pO2_mbar_default')
        common_controls = {
            'reaction_family': REACTION_FAMILY_C7_CA_ALUMINOTHERMIC,
            'campaign': 'C7_CA_ALUMINOTHERMIC',
            'decision': 'yes',
            'reductant_species': 'Al',
            'objective': objective,
            'aluminate_mode': mode,
            'extent_fraction': extent_fraction,
            'allow_partial_extent': self._c7_bool(
                cfg.get('allow_partial_extent'), True),
            'hold_temp_C': self._c7_float(
                cfg.get('hold_temp_C', cfg.get('default_hold_T_C')), 1200.0),
            'p_total_mbar': self._c7_float(p_total_raw, self.melt.p_total_mbar),
            'pO2_mbar': self._c7_float(pO2_raw, self.melt.pO2_mbar),
            'active_ca_condensation_route': self._c7_bool(
                cfg.get('active_ca_condensation_route'), True),
            'dedicated_ca_condenser': self._c7_bool(
                cfg.get('dedicated_ca_condenser'), True),
            'ca_condensation_species': str(
                cfg.get('ca_condensation_species') or 'Ca'),
            'ca_condenser_temperature_C': self._c7_float(
                cfg.get('ca_condenser_temperature_C'), 780.0),
            'thermo_margin_kj_per_mol_o2': self._c7_float(
                cfg.get('thermo_margin_kJ_per_mol_O2'), 1.0),
            'transport_extent_mol': r_transport,
            'objective_extent_mol': objective_extent,
        }
        diagnostics: list[dict] = []
        for source_account, al_budget in (
            ('process.metal_phase', in_situ_al_budget),
            (C7_AL_CREDIT_ACCOUNT, credit_al_available),
        ):
            if al_budget <= 0.0 or objective_extent <= 0.0:
                continue
            source_extent = min(objective_extent, al_budget / stoich['Al'])
            result = self._dispatch_only(
                ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
                control_inputs={
                    **common_controls,
                    'al_source_account': source_account,
                    'objective_extent_mol': source_extent,
                },
            )
            diag = dict(result.diagnostic or {})
            proposal = result.transition
            if proposal is None:
                self._chem_no_op_dispatch_count += 1
                self._last_c7_refusal_diagnostic = diag
                break
            transition = self._commit_proposal(
                ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
                proposal,
                diagnostic=diag,
                control_inputs={
                    **common_controls,
                    'al_source_account': source_account,
                    'objective_extent_mol': source_extent,
                },
            )
            self._apply_c7_aluminothermic_redox_source_terms(
                transition,
                label='redox_source:c7_ca_aluminothermic_reduction',
                exchange_direction='redox_source:c7_ca_aluminothermic_reduction',
            )
            diagnostics.append(diag)
            objective_extent = max(0.0, objective_extent - float(diag.get('r_c7', 0.0)))

        ca_overhead_mol = self._ledger_account_species_kg(
            'process.overhead_gas', 'Ca') / MOLAR_MASS['Ca'] * 1000.0
        if ca_overhead_mol > 0.0 and not self._last_c7_refusal_diagnostic:
            capture_fraction = min(
                1.0,
                max(0.0, float(transport_diag.get('transport_ca_mol', 0.0)))
                / max(ca_overhead_mol, 1e-30),
            )
            capture_result = self._dispatch_and_commit(
                ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
                control_inputs={
                    **common_controls,
                    'operation': 'ca_capture',
                    'capture_mol': ca_overhead_mol,
                    'capture_fraction': capture_fraction,
                    'route_uncaptured_to_wall': True,
                },
            )
            diagnostics.append(dict(capture_result.diagnostic or {}))
            capture_diag = dict(capture_result.diagnostic or {})
            self._move_cost_product_lots_best_effort(
                source_account='process.overhead_gas',
                destination_account='process.condensation_train',
                species='Ca',
                quantity_kg=float(capture_diag.get('ca_metal_captured_kg', 0.0)),
                reason='C7 captured Ca product transfer',
            )
            self._move_cost_product_lots_best_effort(
                source_account='process.overhead_gas',
                destination_account='process.wall_deposit',
                species='Ca',
                quantity_kg=float(
                    capture_diag.get('ca_uncaptured_wall_deposit_kg', 0.0)),
                reason='C7 uncaptured Ca wall-deposit transfer',
            )
            self._step_c7_ca_shuttle_feedback(
                cfg,
                common_controls,
                float(capture_diag.get('captured_ca_mol', 0.0) or 0.0),
                diagnostics,
            )
            self._project_extraction_product(
                'C7', 'Ca', source_account='process.condensation_train')

        self._project_extraction_melt()
        after = self._c7_residual_ceramic_report()
        aggregate = self._aggregate_c7_diagnostics(
            diagnostics,
            before,
            after,
            {
                'c7_al_in_situ_available_mol': in_situ_al_available,
                'c7_al_credit_funded_mol': credit_al_available,
                'c7_al_credit_input_kg': float(
                    getattr(self, '_c7_al_credit_input_kg', 0.0) or 0.0),
                'c7_al_export_remaining_mol': max(
                    0.0,
                    float(
                        self.atom_ledger.mol_by_account()
                        .get('process.metal_phase', {})
                        .get('Al', 0.0)
                    ),
                ),
                **transport_diag,
                'c7_knob_saturation': [
                    self._c7_knob_diag(
                        'al_fraction', al_fraction_raw, al_fraction,
                        'clamped_to_supported_envelope'),
                    self._c7_knob_diag(
                        'extent_fraction', extent_fraction_raw, extent_fraction,
                        'clamped_to_supported_envelope'),
                    *transport_diag.get('c7_knob_saturation', []),
                ],
            },
        )
        self._last_c7_diagnostic = aggregate
        self._c7_product_report = self._build_c7_product_report(
            before, after, aggregate)

    def _step_c7_ca_shuttle_feedback(
        self,
        cfg: dict,
        common_controls: dict,
        captured_ca_mol: float,
        diagnostics: list[dict],
    ) -> None:
        ca_shuttle = self._c7_nested(cfg.get('ca_shuttle'))
        if not self._c7_bool(ca_shuttle.get('enabled'), False):
            return
        if getattr(self, '_c7_ca_shuttle_applied', False):
            diagnostics.append({
                'operation': 'ca_shuttle_alumina_feedback',
                'reason_refused': 'c7_ca_shuttle_already_applied',
            })
            return
        self._c7_ca_shuttle_applied = True
        rate_fraction_raw = self._c7_float(
            ca_shuttle.get('rate_fraction'), 0.0)
        reserve_fraction_raw = self._c7_float(
            ca_shuttle.get('reserve_ca_product_fraction'), 1.0)
        rate_fraction = self._c7_clamp(rate_fraction_raw, 0.0, 1.0)
        reserve_fraction = self._c7_clamp(reserve_fraction_raw, 0.0, 1.0)
        if captured_ca_mol <= 0.0 or rate_fraction <= 0.0:
            diagnostics.append({
                'operation': 'ca_shuttle_alumina_feedback',
                'reason_refused': 'c7_ca_shuttle_no_surplus_extent',
                'captured_ca_mol': captured_ca_mol,
                'ca_shuttle_rate_fraction': rate_fraction,
                'reserved_product_ca_mol': captured_ca_mol * reserve_fraction,
                'c7_knob_saturation': [
                    self._c7_knob_diag(
                        'ca_shuttle.rate_fraction',
                        rate_fraction_raw,
                        rate_fraction,
                        'clamped_to_supported_envelope'),
                    self._c7_knob_diag(
                        'ca_shuttle.reserve_ca_product_fraction',
                        reserve_fraction_raw,
                        reserve_fraction,
                        'clamped_to_supported_envelope'),
                ],
            })
            return
        result = self._dispatch_and_commit(
            ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
            control_inputs={
                **common_controls,
                'operation': 'ca_shuttle_alumina_feedback',
                'reductant_species': 'Ca',
                'captured_ca_mol': captured_ca_mol,
                'ca_shuttle_rate_fraction': rate_fraction_raw,
                'ca_shuttle_reserve_ca_product_fraction': reserve_fraction_raw,
                'ca_shuttle_targets': ca_shuttle.get('targets', ['Al2O3']),
            },
        )
        diagnostics.append(dict(result.diagnostic or {}))

    def _aggregate_c7_diagnostics(
        self,
        diagnostics: list[dict],
        before: dict,
        after: dict,
        base: dict,
    ) -> dict:
        aggregate = dict(base)
        for key in (
            'c7_al_in_situ_drawn_mol',
            'c7_al_credit_drawn_mol',
            'ca_overhead_mol',
            'captured_ca_mol',
            'wall_deposit_ca_mol',
            'ca_metal_kg',
            'ca_metal_captured_kg',
            'calcium_aluminate_slag_kg',
            'al_spend_kg',
            'cao_removed_kg',
            'reserved_product_ca_mol',
            'shuttle_drawn_ca_mol',
            'unused_surplus_ca_mol',
            'al_recovered_mol',
            'al_recovered_kg',
            'cao_returned_kg',
            'ca_shuttle_drawn_kg',
            'alumina_consumed_kg',
        ):
            aggregate[key] = sum(float(d.get(key, 0.0) or 0.0) for d in diagnostics)
        credit_funded = float(aggregate.get('c7_al_credit_funded_mol', 0.0))
        credit_drawn = float(aggregate.get('c7_al_credit_drawn_mol', 0.0))
        aggregate['c7_al_credit_unused_mol'] = max(0.0, credit_funded - credit_drawn)
        aggregate['c7_al_spend_total_mol'] = (
            float(aggregate.get('c7_al_in_situ_drawn_mol', 0.0))
            + credit_drawn
        )
        aggregate['REE_oxides_wt_pct_before_C7'] = before['REE_oxides_wt_pct']
        aggregate['REE_oxides_wt_pct_after_C7'] = after['REE_oxides_wt_pct']
        aggregate['REE_enrichment_factor'] = (
            after['REE_oxides_wt_pct'] / before['REE_oxides_wt_pct']
            if before['REE_oxides_wt_pct'] > 0.0 else 0.0
        )
        aggregate['REE_oxides_kg_preserved'] = after['REE_oxides_kg']
        aggregate['residual_terminal_ceramic_kg'] = (
            after['residual_terminal_ceramic_kg'])
        aggregate['raw_diagnostics'] = diagnostics
        return aggregate

    def _build_c7_product_report(
        self,
        before: dict,
        after: dict,
        diagnostic: dict,
    ) -> dict:
        ca_product_kg = max(
            0.0,
            float(diagnostic.get('ca_metal_captured_kg', 0.0))
            - float(diagnostic.get('ca_shuttle_drawn_kg', 0.0)),
        )
        ca_product = {
            'kg': ca_product_kg,
            'account': 'process.condensation_train:Ca',
        }
        if diagnostic.get('shuttle_drawn_ca_mol', 0.0):
            ca_product.update({
                'reserved_product_kg': ca_product_kg,
                'shuttle_drawn_kg': float(
                    diagnostic.get('ca_shuttle_drawn_kg', 0.0)),
                'unused_surplus_ca_mol': float(
                    diagnostic.get('unused_surplus_ca_mol', 0.0)),
            })
        return {
            'enabled': bool(diagnostic),
            'products': {
                'Ca_metal': ca_product,
                'calcium_aluminate_cement_slag': {
                    'kg': float(
                        diagnostic.get('calcium_aluminate_slag_kg', 0.0)),
                    'label': 'calcium-aluminate refractory (accounting grade)',
                    'account': 'terminal.slag:{Ca3Al2O6|Ca12Al14O33}',
                },
                'residual_REE_enriched_terminal_ceramic': {
                    'kg': after['residual_terminal_ceramic_kg'],
                    'REE_oxides_wt_pct_before_C7': before['REE_oxides_wt_pct'],
                    'REE_oxides_wt_pct_after_C7': after['REE_oxides_wt_pct'],
                    'REE_enrichment_factor': (
                        after['REE_oxides_wt_pct'] / before['REE_oxides_wt_pct']
                        if before['REE_oxides_wt_pct'] > 0.0 else 0.0
                    ),
                    'REE_oxides_kg_preserved': after['REE_oxides_kg'],
                },
            },
            'diagnostic': diagnostic,
            'refusal': dict(getattr(self, '_last_c7_refusal_diagnostic', {}) or {}),
        }

    # ------------------------------------------------------------------
    # Equipment spec helpers
    # ------------------------------------------------------------------
