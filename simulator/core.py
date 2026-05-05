"""
Regolith Pyrolysis Simulator — Simulation Engine
================================================

★ TIER 1: CHEMIST-READABLE ★

This file runs the hour-by-hour step() loop that models the Oxygen
Shuttle pyrometallurgical process. Shared constants, enums, and dataclasses live in simulator.state.
Thermodynamic equilibrium, evaporation, and extraction campaign helpers live
in focused mixin modules and are composed here. State symbols are re-exported
from this module for compatibility with older imports.

The Oxygen Shuttle extracts metals + O₂ from regolith in six campaigns:

    C0   Vacuum bakeoff       — CHNOPS volatiles released, native Fe₀ separated
    C0b  P-cleanup (optional) — mild oxidative hold volatilises phosphorus
    C2A  pN₂ adaptive ramp    — Na/K/Fe/SiO co-extraction under inert sweep
    C2B  pO₂-managed Fe       — Fe-only pyrolysis preserving CMAS glass
    C3   Alkali shuttle       — Na/K metallothermic polish for Ti, residual Fe
    C4   Mg selective pyrolysis — Mg vapor extraction under managed pO₂
    C5   Limited MRE          — Molten regolith electrolysis (Si, Ti at ≤1.6 V)
    C6   Mg thermite          — Al extraction via Mg → Al₂O₃ thermite

Two condensation trains branch from the crucible:
    Volatiles train (C0/C0b) — sealed by gate valve after devolatilisation
    Metals train (C2A onward) — linear 7-stage: hot duct → Fe → SiO →
        alkali/Mg cyclone → vortex filter → turbine → O₂ accumulator

Units:
    Temperature     °C
    Amount          mol in AtomLedger; kg only at external projections
    Pressure        mbar (overhead), bar (O₂ accumulator)
    Time            hours (simulation timestep = 1 h)
    Energy          kWh (electrical only; solar-thermal assumed)
    Evaporation     kg/hr (Hertz-Knudsen flux × surface area)
    Vapor pressure  Pa (Antoine → converted as needed)
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from simulator.accounting import (
    AccountingError,
    AtomLedger,
    LedgerTransition,
    coerce_species_formula,
    load_species_formulas,
    resolve_species_formula,
)
from simulator.state import (
    BOLTZMANN,
    FARADAY,
    GAS_CONSTANT,
    STEFAN_BOLTZMANN,
    Atmosphere,
    BatchRecord,
    CampaignPhase,
    CondensationStage,
    CondensationTrain,
    DecisionPoint,
    DecisionType,
    EnergyRecord,
    EvaporationFlux,
    GAS_SPECIES,
    HourSnapshot,
    METAL_SPECIES,
    MOLAR_MASS,
    MeltState,
    OXIDE_SPECIES,
    OXIDE_TO_METAL,
    OverheadGas,
    ProcessInventory,
    STOICH_RATIOS,
)
from simulator.equilibrium import EquilibriumMixin
from simulator.evaporation import EvaporationMixin
from simulator.extraction import ExtractionMixin

# ============================================================================
# SECTIONS 1-3: CONSTANTS, ENUMS, AND STATE MODELS
# ============================================================================
# Moved to simulator.state. Imported above and re-exported from this module for
# compatibility with existing callers that import state symbols from core.py.

# ============================================================================
# SECTION 4: SIMULATION ENGINE
# ============================================================================

STAGE0_GAS_COMPONENTS = {
    'h2o', 'co2', 'co_co2', 'organics', 'ch4_nh3_hcn',
    'nh3', 'hcn', 'c', 'carbon_content', 'carbonaceous_organic', 'hydrocarbons',
    'organics_hydrocarbons', 'co_ch4_propellant', 'nh3_hcn',
    'o2_extra',
}
STAGE0_SALT_COMPONENTS = {
    'cl', 'f', 'clo4', 'so3', 'nacl', 'kcl', 'salt', 'salts',
    'perchlorate', 'perchlorates', 'sulfate', 'sulfates', 'halide',
    'halides', 'carbonate', 'carbonates',
}
STAGE0_SULFIDE_COMPONENTS = {
    's', 'fes', 'fes_troilite', 'troilite', 'oldhamite', 'sulfide',
    'sulfides',
}
STAGE0_METAL_ALLOY_COMPONENTS = {
    'fe', 'ni', 'co', 'metallic_feni', 'feni', 'fe_ni', 'fe_ni_co',
    'fe_ni_alloy', 'metal', 'metals', 'alloy', 'alloys',
    'p', 'phosphorus', 'phosphide', 'phosphides',
}
STAGE0_TERMINAL_SLAG_COMPONENTS = {
    'zro2', 'ree', 'ree_oxide', 'ree_oxides', 'rare_earths',
    'rare_earth_oxide', 'rare_earth_oxides', 'th', 'tho2', 'u', 'uo2',
}
OXYGEN_SPECIES = 'O2'
OXYGEN_MELT_OFFGAS_ACCOUNT = 'terminal.oxygen_melt_offgas_stored'
OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT = 'terminal.oxygen_melt_offgas_vented_to_vacuum'
OXYGEN_MRE_ANODE_ACCOUNT = 'terminal.oxygen_mre_anode_stored'
OXYGEN_STORED_ACCOUNTS = (
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
)
OXYGEN_VENTED_ACCOUNTS = (
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
)
FLOW_MASS_ACCOUNTS = (
    'process.cleaned_melt',
    'process.raw_feedstock',
    'process.condensation_train',
    'process.overhead_gas',
    'process.reagent_inventory',
    'terminal.offgas',
    'terminal.stage0_salt_phase',
    'terminal.stage0_sulfide_matte',
    'terminal.drain_tap_material',
    'terminal.slag',
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
)
OXYGEN_MOLAR_MASS_KG_PER_MOL = MOLAR_MASS[OXYGEN_SPECIES] / 1000.0
OXYGEN_ACCOUNTING_TOLERANCE_KG = 1e-9
BACKEND_FALLBACK_EXCEPTIONS = (RuntimeError, ImportError)
STAGE0_DEFAULT_TEMP_RANGE_C = (20.0, 950.0)
STAGE0_CARBON_CLEANUP_TEMP_RANGE_C = (20.0, 1050.0)
DEFAULT_CARBONACEOUS_MELT_KG_PER_TONNE = 725.0

class PyrolysisSimulator(EquilibriumMixin, EvaporationMixin, ExtractionMixin):
    """
    Hour-by-hour simulator for the Oxygen Shuttle process.

    This is the main simulation engine.  It manages:
    - Melt state evolution (composition, temperature)
    - Evaporation kinetics (Hertz-Knudsen with stirring)
    - Condensation routing (7-stage train)
    - Overhead gas dynamics and turbine control
    - Energy tracking
    - Campaign progression and endpoint detection
    - Decision points (Path A/B, Branch One/Two)

    Usage:
        sim = PyrolysisSimulator(backend, setpoints, feedstocks)
        sim.load_batch('lunar_mare_low_ti', mass_kg=1000)
        sim.start_campaign(CampaignPhase.C0)
        while not sim.is_complete():
            snapshot = sim.step()
            # ... update UI with snapshot ...
            if sim.pending_decision:
                decision = sim.pending_decision
                sim.apply_decision(decision.decision_type, chosen_option)
    """

    def __init__(self, melt_backend, setpoints: dict, feedstocks: dict,
                 vapor_pressures: dict):
        """
        Args:
            melt_backend: A MeltBackend instance (AlphaMELTS, stub, etc.)
                          for thermodynamic equilibrium calculations.
            setpoints:    Campaign parameters loaded from setpoints.yaml.
            feedstocks:   Feedstock compositions from feedstocks.yaml.
            vapor_pressures: Antoine parameters from vapor_pressures.yaml.
        """
        self.backend = melt_backend
        self._last_backend_error = ''
        self._backend_failed = False
        self.setpoints = setpoints
        self.feedstocks = feedstocks
        self.vapor_pressures = vapor_pressures
        self._base_species_formula_registry = self._load_species_formula_registry()
        self.species_formula_registry = dict(self._base_species_formula_registry)
        self.atom_ledger = AtomLedger(registry=self.species_formula_registry)

        # --- Current state ---
        self.melt = MeltState()
        self.inventory = ProcessInventory()
        self.train = CondensationTrain.create_default()
        self.overhead = OverheadGas()

        # --- Batch record ---
        self.record = BatchRecord()
        self.energy_cumulative_kWh = 0.0
        self.oxygen_cumulative_kg = 0.0

        # --- Gas train feedback state ---
        self.O2_vented_cumulative_kg = 0.0      # Total O₂ vented to vacuum
        self.O2_stored_cumulative_kg = 0.0      # Total O₂ in accumulator
        self._melt_offgas_O2_kg_this_hr = 0.0
        self._mre_anode_O2_kg_this_hr = 0.0
        self._last_nominal_ramp = 0.0           # Campaign ramp before throttle
        self._last_actual_ramp = 0.0            # Ramp after throttle applied
        self._last_throttle_reason = ''         # Why ramp was throttled

        # --- Alkali shuttle state (C3) ---                      [THERMO-5]
        # The shuttle uses recovered Na/K (from C2A evaporation + additives)
        # as a thermochemical reductant.  Each inject-bakeout cycle:
        #   Inject:  2K(g) + FeO(melt) → K₂O(melt) + Fe(l)
        #   Bakeout: K₂O(melt) → 2K(g) + ½O₂(g)
        #   Net:     FeO → Fe + ½O₂   (K recycled)
        self.shuttle_K_inventory_kg = 0.0       # K available for injection
        self.shuttle_Na_inventory_kg = 0.0      # Na available for injection
        self.shuttle_cycle_K = 0                # Current K inject-bakeout cycle
        self.shuttle_cycle_Na = 0               # Current Na inject-bakeout cycle
        self._shuttle_injected_this_hr = 0.0    # For snapshot
        self._shuttle_reduced_this_hr = 0.0     # Oxide reduced this hour
        self._shuttle_metal_this_hr = 0.0       # Metal produced this hour
        self._shuttle_phase = ''                # 'inject' or 'bakeout'

        # --- Mg thermite state (C6) ---                         [THERMO-7]
        # Mg thermite reduction of Al₂O₃:
        #   3Mg(l) + Al₂O₃(melt) → 3MgO(slag) + 2Al(l)
        # Back-reduction cascade (if Si present):
        #   4Al(l) + 3SiO₂(melt) → 2Al₂O₃ + 3Si(l)
        self.thermite_Mg_inventory_kg = 0.0     # Mg available from additives
        self._activated_additive_reagents = set()
        self._thermite_Al2O3_reduced_this_hr = 0.0
        self._thermite_Al_produced_this_hr = 0.0
        self._thermite_Mg_consumed_this_hr = 0.0

        # --- MRE electrolysis state (C5 / MRE_BASELINE) ---
        self._mre_metals_this_hr: Dict[str, float] = {}
        self._mre_voltage_V = 0.0
        self._mre_current_A = 0.0
        self._mre_effective_current_A = 0.0
        self._mre_energy_this_hr = 0.0
        self._mre_voltage_step_idx = 0
        self._mre_hold_hours = 0
        self._mre_voltage_sequence: list = []

        # --- User-configurable parameters ---
        self.c4_max_temp_C = 1670.0             # Max T for C4 Mg pyrolysis

        # --- Campaign manager ---
        from simulator.campaigns import CampaignManager
        self.campaign_mgr = CampaignManager(setpoints)

        # --- Subsystem models (lazy imports to avoid circular deps) ---
        self._condensation_model = None
        self._overhead_model = None
        self._energy_tracker = None
        self._electrolysis_model = None
        self._equipment = None

        # --- Decision state ---
        self.pending_decision: Optional[DecisionPoint] = None
        self.paused_for_decision = False

        # --- Campaign summary tracking (for stage completion snapshots) ---
        self._campaign_start_hour = 0
        self._campaign_start_mass = 0.0
        self._campaign_start_composition: Dict[str, float] = {}
        self._campaign_start_condensation: Dict[str, float] = {}
        self._campaign_start_energy = 0.0
        self._campaign_start_O2 = 0.0
        self._last_campaign_summary: Optional[dict] = None

    # ------------------------------------------------------------------
    # Lazy-loaded subsystem models
    # ------------------------------------------------------------------

    @property
    def condensation_model(self):
        if self._condensation_model is None:
            from simulator.condensation import CondensationModel
            self._condensation_model = CondensationModel(self.train)
        return self._condensation_model

    @property
    def overhead_model(self):
        if self._overhead_model is None:
            from simulator.overhead import OverheadGasModel
            self._overhead_model = OverheadGasModel()
        return self._overhead_model

    @property
    def energy_tracker(self):
        if self._energy_tracker is None:
            from simulator.energy import EnergyTracker
            self._energy_tracker = EnergyTracker()
        return self._energy_tracker

    @property
    def electrolysis_model(self):
        if self._electrolysis_model is None:
            from simulator.electrolysis import ElectrolysisModel
            self._electrolysis_model = ElectrolysisModel()
        return self._electrolysis_model

    # ------------------------------------------------------------------
    # Batch lifecycle
    # ------------------------------------------------------------------

    def load_batch(self, feedstock_key: str, mass_kg: float = 1000.0,
                   additives_kg: Optional[Dict[str, float]] = None):
        """
        Load a batch of regolith into the crucible.

        Converts the feedstock's wt% composition into absolute kg
        and initialises the melt state at room temperature.

        Args:
            feedstock_key:  Key in feedstocks dict (e.g., 'lunar_mare_low_ti')
            mass_kg:        Total mass of regolith loaded (kg)
            additives_kg:   Optional dict of additive masses (e.g., {'Na': 5.0})
        """
        fs = self.feedstocks.get(feedstock_key)
        if fs is None:
            raise ValueError(f"Unknown feedstock: {feedstock_key}")

        additives = additives_kg or {}
        self.species_formula_registry = self._registry_for_feedstock(fs)
        self.atom_ledger = AtomLedger(registry=self.species_formula_registry)
        self.inventory = self._build_process_inventory(fs, mass_kg)
        required_carbon_kg = self.inventory.carbon_reductant_required_kg
        if (
            required_carbon_kg > 1e-12
            and float(additives.get('C', 0.0)) + 1e-12 < required_carbon_kg
        ):
            raise AccountingError(
                f"{fs.get('label', feedstock_key)} requires "
                f"{required_carbon_kg:.6g} kg C reductant for Stage 0 "
                "carbon cleanup; supply additives_kg={'C': ...}"
            )
        self._seed_atom_ledger(feedstock_key, fs, additives)
        self._consume_stage0_carbon_reductant()
        self._project_cleaned_melt_from_atom_ledger()
        self._last_backend_error = ''
        self._backend_failed = False

        # Convert wt% to absolute kg
        self.inventory.melt_oxide_kg = dict(self.melt.composition_kg)

        self.melt.temperature_C = 25.0
        self.melt.atmosphere = Atmosphere.HARD_VACUUM
        environment = fs.get('environment', {}) or {}
        self.melt.ambient_pressure_mbar = max(
            0.0,
            float(fs.get('surface_pressure_mbar')
                  or environment.get('surface_pressure_mbar')
                  or 0.0))
        self.melt.ambient_atmosphere = str(
            fs.get('atmosphere')
            or environment.get('atmosphere')
            or '')
        self.melt.p_total_mbar = self.melt.ambient_pressure_mbar
        self.melt.pO2_mbar = 0.0
        self.melt.campaign = CampaignPhase.IDLE
        self.melt.hour = 0
        self.melt.campaign_hour = 0
        self.melt.update_total_mass()

        # Reset condensation train
        self.train = CondensationTrain.create_default()
        self.overhead = OverheadGas()

        # Record
        self.record = BatchRecord(
            feedstock_key=feedstock_key,
            feedstock_label=fs.get('label', feedstock_key),
            batch_mass_kg=mass_kg,
            additives_kg=additives,
            initial_inventory=self.inventory.copy(),
        )
        self.energy_cumulative_kWh = 0.0
        self.oxygen_cumulative_kg = 0.0
        self.O2_vented_cumulative_kg = 0.0
        self.O2_stored_cumulative_kg = 0.0
        self._melt_offgas_O2_kg_this_hr = 0.0
        self._mre_anode_O2_kg_this_hr = 0.0
        self._last_nominal_ramp = 0.0
        self._last_actual_ramp = 0.0
        self._last_throttle_reason = ''

        # Reset shuttle state
        self.shuttle_K_inventory_kg = 0.0
        self.shuttle_Na_inventory_kg = 0.0
        self.shuttle_cycle_K = 0
        # Reset thermite state
        self.thermite_Mg_inventory_kg = 0.0
        self._activated_additive_reagents = set()
        self.shuttle_cycle_Na = 0
        self._shuttle_injected_this_hr = 0.0
        self._shuttle_reduced_this_hr = 0.0
        self._shuttle_metal_this_hr = 0.0
        self._shuttle_phase = ''

    @staticmethod
    def _load_species_formula_registry() -> dict:
        catalog = Path(__file__).resolve().parents[1] / 'data' / 'species_catalog.yaml'
        return load_species_formulas(catalog)

    def _registry_for_feedstock(self, feedstock: Mapping[str, Any]) -> dict:
        registry = dict(self._base_species_formula_registry)
        for species, entry in self._feedstock_formula_entries(feedstock).items():
            registry[species] = coerce_species_formula(
                species,
                self._expand_feedstock_formula_entry(species, entry, registry),
            )
        return registry

    @staticmethod
    def _feedstock_formula_entries(
        feedstock: Mapping[str, Any]
    ) -> Dict[str, Mapping[str, Any]]:
        entries: Dict[str, Mapping[str, Any]] = {}
        for section_name in (
            'species_formulas',
            'formula_inventory',
            'stage0_formula_inventory',
        ):
            section = feedstock.get(section_name) or {}
            if not isinstance(section, Mapping):
                raise ValueError(f'{section_name} must be a mapping')
            for species, entry in section.items():
                if not isinstance(entry, Mapping):
                    raise ValueError(
                        f'{section_name}.{species} must be a mapping')
                entries[str(species)] = entry
        return entries

    @staticmethod
    def _expand_feedstock_formula_entry(
        species: str,
        entry: Mapping[str, Any],
        registry: Mapping[str, Any],
    ) -> Dict[str, Any]:
        expanded = dict(entry)
        template = (
            expanded.pop('template', None)
            or expanded.pop('generic_formula', None)
        )
        has_formula = any(
            key in expanded
            for key in (
                'atoms',
                'elements',
                'formula',
                'atom_mass_fractions',
                'element_mass_fractions',
            )
        )
        if template:
            template_key = str(template)
            template_formula = registry.get(template_key)
            if template_formula is None:
                raise ValueError(
                    f'{species} formula template {template_key!r} is not '
                    'declared in data/species_catalog.yaml'
                )
            if not has_formula:
                expanded['atoms'] = dict(template_formula.elements)
            expanded.setdefault(
                'estimated',
                bool(getattr(template_formula, 'estimated', False)),
            )
            expanded.setdefault(
                'source',
                getattr(template_formula, 'source', ''),
            )
            expanded.setdefault(
                'requires_feedstock_metadata',
                bool(getattr(template_formula, 'requires_feedstock_metadata', False)),
            )
        return expanded

    @staticmethod
    def _positive_species_kg(values: Mapping[str, float]) -> Dict[str, float]:
        return {
            str(species): float(kg)
            for species, kg in values.items()
            if float(kg) > 1e-12
        }

    def _seed_atom_ledger(
        self,
        feedstock_key: str,
        feedstock: Mapping[str, Any],
        additives_kg: Mapping[str, float],
    ) -> None:
        """Seed atom ledger from current kg inventory projections."""
        self.atom_ledger = AtomLedger(registry=self.species_formula_registry)
        label = str(feedstock.get('label', feedstock_key))
        oxidation_specs, oxidized_offgas_kg = (
            self._stage0_oxidation_transition_specs(feedstock))
        terminal_offgas_external = self._subtract_species_kg(
            self.inventory.gas_volatiles_kg,
            oxidized_offgas_kg,
            context=f'{label} Stage 0 oxidation products',
        )

        self._load_ledger_account(
            'process.cleaned_melt',
            self.inventory.melt_oxide_kg,
            source=f'{label} cleaned melt',
        )
        self._load_ledger_account(
            'process.raw_feedstock',
            self.inventory.residual_components_kg,
            source=f'{label} Stage 0 residual',
        )
        self._load_ledger_account(
            'terminal.offgas',
            terminal_offgas_external,
            source=f'{label} Stage 0 volatiles',
        )
        self._load_ledger_account(
            'terminal.stage0_salt_phase',
            self.inventory.salt_phase_kg,
            source=f'{label} Stage 0 salt phase',
        )
        self._load_ledger_account(
            'terminal.stage0_sulfide_matte',
            self.inventory.sulfide_matte_kg,
            source=f'{label} Stage 0 sulfide matte',
        )
        self._load_ledger_account(
            'terminal.drain_tap_material',
            self.inventory.metal_alloy_kg,
            source=f'{label} Stage 0 metal alloy',
        )
        self._load_ledger_account(
            'terminal.slag',
            self.inventory.terminal_slag_components_kg,
            source=f'{label} Stage 0 terminal slag',
        )

        for species, kg in self._positive_species_kg(additives_kg).items():
            account = f'reservoir.reagent.{species}'
            self.atom_ledger.load_external(
                account,
                {species: kg},
                source=f'batch additive {species}',
            )

        self._record_stage0_oxidation_transitions(label, oxidation_specs)

    def _load_ledger_account(
        self, account: str, species_kg: Mapping[str, float], *, source: str
    ) -> None:
        payload = self._ledger_species_kg(species_kg)
        if payload:
            self.atom_ledger.load_external(account, payload, source=source)

    def _ledger_species_kg(self, values: Mapping[str, float]) -> Dict[str, float]:
        payload: Dict[str, float] = {}
        for species, kg in self._positive_species_kg(values).items():
            if species not in self.species_formula_registry:
                raise AccountingError(
                    f"cannot account species {species!r}; declare its "
                    "formula in data/species_catalog.yaml or the feedstock "
                    "formula_inventory"
                )
            try:
                resolve_species_formula(species, self.species_formula_registry)
            except AccountingError as exc:
                raise AccountingError(
                    f"cannot account species {species!r}; add an exact or "
                    "estimated formula to data/species_catalog.yaml"
                ) from exc
            payload[species] = kg
        return payload

    @staticmethod
    def _subtract_species_kg(
        values: Mapping[str, float],
        subtract: Mapping[str, float],
        *,
        context: str,
    ) -> Dict[str, float]:
        result: Dict[str, float] = {}
        missing = {
            species: kg
            for species, kg in subtract.items()
            if float(kg) > 1e-12 and species not in values
        }
        if missing:
            details = ', '.join(
                f'{species}={float(kg):.6g} kg'
                for species, kg in sorted(missing.items())
            )
            raise AccountingError(
                f'{context} expected products absent from destination: {details}'
            )
        for species, kg in values.items():
            remaining = float(kg) - float(subtract.get(species, 0.0))
            if remaining < -1e-8:
                raise AccountingError(
                    f'{context} subtracts more {species} than exists '
                    f'({float(subtract.get(species, 0.0)):.6g} kg > '
                    f'{float(kg):.6g} kg)'
                )
            if remaining > 1e-12:
                result[species] = remaining
        return result

    def _stage0_oxidation_transition_specs(
        self,
        feedstock: Mapping[str, Any],
    ) -> Tuple[list[dict], Dict[str, float]]:
        entries = self._feedstock_formula_entries(feedstock)
        specs: list[dict] = []
        product_totals: Dict[str, float] = {}
        for species, entry in entries.items():
            mode = str(entry.get('offgas_mode', '')).lower()
            if mode not in {'complete_oxidation', 'oxidized'}:
                continue
            kg = float(self.inventory.raw_components_kg.get(species, 0.0))
            if kg <= 1e-12:
                continue
            products_kg, oxidant_kg = self._oxidized_stage0_products(
                species, kg)
            if not products_kg:
                continue
            specs.append({
                'species': species,
                'feed_kg': kg,
                'products_kg': products_kg,
                'oxidant_kg': oxidant_kg,
            })
            self._merge_masses(product_totals, products_kg)
        return specs, product_totals

    def _record_stage0_oxidation_transitions(
        self,
        label: str,
        specs: list[dict],
    ) -> None:
        for spec in specs:
            species = str(spec['species'])
            feed_kg = float(spec['feed_kg'])
            products_kg = dict(spec['products_kg'])
            oxidant_kg = float(spec['oxidant_kg'])

            feed_account = 'process.stage0_volatile_feed'
            oxidant_account = 'reservoir.stage0_oxidant'
            self.atom_ledger.load_external(
                feed_account,
                {species: feed_kg},
                source=f'{label} Stage 0 {species} feed',
            )
            debits = [
                self.atom_ledger.debit(
                    feed_account,
                    {species: feed_kg},
                    source=f'{label} Stage 0 {species} feed',
                )
            ]
            if oxidant_kg > 1e-12:
                self.atom_ledger.load_external(
                    oxidant_account,
                    {'O2': oxidant_kg},
                    source=f'{label} Stage 0 controlled O2 oxidant',
                )
                debits.append(
                    self.atom_ledger.debit(
                        oxidant_account,
                        {'O2': oxidant_kg},
                        source=f'{label} Stage 0 controlled O2 oxidant',
                    )
                )
            self.atom_ledger.record(
                f'stage0_complete_oxidation_{species}',
                debits=debits,
                credits=[
                    self.atom_ledger.credit(
                        'terminal.offgas',
                        products_kg,
                        source=f'{label} Stage 0 oxidized {species} offgas',
                    )
                ],
                reason=(
                    'Stage 0 complete oxidation records organic volatile '
                    'atoms and controlled O2 input explicitly.'
                ),
            )

    def _project_cleaned_melt_from_atom_ledger(self) -> None:
        ledger_melt = self.atom_ledger.kg_by_account('process.cleaned_melt')
        self.melt.composition_kg = {
            oxide: float(ledger_melt.get(oxide, 0.0))
            for oxide in OXIDE_SPECIES
        }
        self.inventory.melt_oxide_kg = dict(self.melt.composition_kg)
        self.melt.update_total_mass()

    def _backend_composition_kg(self) -> Dict[str, float]:
        ledger_melt = self.atom_ledger.kg_by_account('process.cleaned_melt')
        return {
            species: kg
            for species, raw_kg in ledger_melt.items()
            if (kg := float(raw_kg)) > 1e-12
        }

    def _backend_composition_mol(self) -> Dict[str, float]:
        ledger_melt = self.atom_ledger.mol_by_account('process.cleaned_melt')
        return {
            species: mol
            for species, raw_mol in ledger_melt.items()
            if (mol := float(raw_mol)) > 0.0
        }

    def _get_equilibrium(self):
        """
        Query backend with the mol inventory from the atom ledger.

        Kg composition remains available as an adapter projection for legacy
        helper surfaces, but MeltState kg is not authoritative.
        """
        if (
            self._backend_failed
            or self.backend is None
            or not self.backend.is_available()
        ):
            return self._stub_equilibrium()

        try:
            result = self.backend.equilibrate(
                temperature_C=self.melt.temperature_C,
                composition_mol=self._backend_composition_mol(),
                fO2_log=self.melt.fO2_log,
                pressure_bar=self.melt.p_total_mbar / 1000.0,
            )
        except AccountingError:
            raise
        except BACKEND_FALLBACK_EXCEPTIONS as exc:
            self._last_backend_error = str(exc)
            self._disable_backend_after_failure()
            return self._stub_equilibrium()
        except ValueError as exc:
            self._last_backend_error = str(exc)
            self._disable_backend_after_failure()
            return self._stub_equilibrium()

        transition = getattr(result, 'ledger_transition', None)
        if transition is not None:
            self.atom_ledger.apply(transition)
            self._project_cleaned_melt_from_atom_ledger()
        return result

    def _disable_backend_after_failure(self) -> None:
        self._backend_failed = True
        if self.backend is not None and hasattr(self.backend, '_available'):
            setattr(self.backend, '_available', False)

    def _consume_stage0_carbon_reductant(self) -> None:
        required_kg = max(0.0, float(
            self.inventory.carbon_reductant_required_kg))
        if required_kg <= 1e-12:
            return
        available_kg = self.atom_ledger.kg_by_account(
            'reservoir.reagent.C').get('C', 0.0)
        if available_kg + 1e-12 < required_kg:
            raise AccountingError(
                f"Stage 0 carbon cleanup requires {required_kg:.6g} kg C; "
                f"only {available_kg:.6g} kg is available"
            )
        self.atom_ledger.apply(
            LedgerTransition.move(
                'stage0_carbon_cleanup_reductant',
                'reservoir.reagent.C',
                'terminal.offgas',
                {'C': required_kg},
                reason=(
                    'Stage 0 carbon cleanup reductant consumed as '
                    'carbon-bearing offgas equivalent'
                ),
            )
        )

    def _build_process_inventory(
        self, feedstock: Mapping[str, Any], mass_kg: float
    ) -> ProcessInventory:
        """Build raw, Stage 0, and cleaned melt inventories for a batch."""
        comp = feedstock.get('composition_wt_pct', {}) or {}
        raw = self._component_masses_from_wt_pct(comp, mass_kg)
        declared_stage0_buckets = self._declared_stage0_product_buckets(
            feedstock, mass_kg)

        for section_name in ('non_oxide_components', 'bulk_additions'):
            extra = self._component_masses_from_named_section(
                feedstock.get(section_name, {}) or {}, mass_kg)
            self._merge_masses(raw, extra)
        structural = self._component_masses_from_named_section(
            feedstock.get('structural_water', {}) or {}, mass_kg)
        self._merge_masses(raw, structural)
        self._normalize_component_masses(raw, mass_kg)
        unbacked_declared_kg = self._unbacked_declared_stage0_products_kg(
            declared_stage0_buckets, raw)
        if unbacked_declared_kg > 1e-9:
            raise ValueError(
                'declared Stage 0 products require raw source mass or '
                f'explicit accounting credit: {unbacked_declared_kg:.6g} kg'
            )

        profile = 'bulk_preservation'
        cleaned_melt_source = 'composition_wt_pct'
        stage0_temp_range = STAGE0_DEFAULT_TEMP_RANGE_C
        carbon_reductant_kg = 0.0

        if feedstock.get('anhydrous_silicate_after_degassing'):
            if not self._uses_carbonaceous_degas_cleanup(feedstock):
                raise ValueError(
                    "anhydrous_silicate_after_degassing requires explicit "
                    "stage0_profile: carbonaceous_degas_cleanup"
                )
            profile = 'carbonaceous_degas_cleanup'
            cleaned_melt_source = 'anhydrous_silicate_after_degassing'
            stage0_temp_range = self._stage0_temp_range_from_feedstock(
                feedstock,
                default=STAGE0_CARBON_CLEANUP_TEMP_RANGE_C,
                require_explicit=True,
            )
            processed_components = self._processable_stage0_components(raw)
            buckets = self._classify_stage0_components(
                self._subset_masses(raw, processed_components))
            cleaned_mass_kg = max(
                0.0,
                mass_kg
                - self._bucket_mass(buckets)
                - sum(
                    self._residual_components_after_stage0(
                        raw, processed_components).values()),
            )
            melt = self._melt_from_anhydrous_silicate(
                feedstock, mass_kg, cleaned_mass_kg=cleaned_mass_kg)
        else:
            melt = self._melt_from_raw_inventory(raw)
            processed_components = set()
            buckets = self._empty_stage0_buckets()
            if self._uses_mars_carbon_cleanup(feedstock):
                profile = 'mars_carbon_cleanup'
                stage0_temp_range = self._stage0_temp_range_from_feedstock(
                    feedstock,
                    default=STAGE0_CARBON_CLEANUP_TEMP_RANGE_C,
                    require_explicit=False,
                )
                processed_components = self._processable_stage0_components(raw)
                buckets = self._classify_stage0_components(
                    self._subset_masses(raw, processed_components))
                carbon_reductant_kg = self._carbon_reductant_required_kg(
                    feedstock, mass_kg)
            else:
                non_oxide = {
                    component: kg
                    for component, kg in raw.items()
                    if component not in OXIDE_SPECIES and kg > 0.0
                }
                buckets = self._classify_stage0_components(non_oxide)
                processed_components = self._processable_stage0_components(raw)

        formula_species = set(raw)
        formula_species.update(self._bucket_species(buckets))
        formula_species.update(self._bucket_species(declared_stage0_buckets))
        self._validate_required_feedstock_formulas(feedstock, formula_species)
        stage0_external_inputs = self._apply_stage0_offgas_chemistry(
            feedstock, buckets)

        residual = self._residual_components_after_stage0(
            raw, processed_components)
        self._merge_masses(
            residual,
            self._unsupported_cleaned_melt_components(
                feedstock, cleaned_melt_source, mass_kg))
        stage0_mass_balance_delta_kg = self._add_stage0_balance_residue(
            residual,
            mass_kg,
            melt,
            buckets,
            unbacked_declared_stage0_products_kg=unbacked_declared_kg,
            stage0_external_inputs_kg=sum(stage0_external_inputs.values()),
        )

        stage0_products = self._stage0_products_from_buckets(buckets)

        return ProcessInventory(
            raw_components_kg=raw,
            melt_oxide_kg=melt,
            residual_components_kg=residual,
            stage0_products_kg=stage0_products,
            gas_volatiles_kg=buckets['gas_volatiles'],
            salt_phase_kg=buckets['salt_phase'],
            sulfide_matte_kg=buckets['sulfide_matte'],
            metal_alloy_kg=buckets['metal_alloy'],
            terminal_slag_components_kg=buckets['terminal_slag'],
            stage0_external_inputs_kg=stage0_external_inputs,
            stage0_profile=profile,
            cleaned_melt_source=cleaned_melt_source,
            stage0_temp_range_C=stage0_temp_range,
            carbon_reductant_required_kg=carbon_reductant_kg,
            stage0_mass_balance_delta_kg=stage0_mass_balance_delta_kg,
        )

    @staticmethod
    def _bucket_species(
        buckets: Mapping[str, Mapping[str, float]]
    ) -> set[str]:
        return {
            species
            for bucket in buckets.values()
            for species, kg in bucket.items()
            if kg > 0.0
        }

    def _validate_required_feedstock_formulas(
        self, feedstock: Mapping[str, Any], species_names: set[str]
    ) -> None:
        entries = self._feedstock_formula_entries(feedstock)
        for species, entry in entries.items():
            if 'offgas_mode' in entry:
                self._validate_stage0_formula_metadata(
                    species, entry, feedstock=feedstock)
        for species in sorted(species_names):
            base_formula = self._base_species_formula_registry.get(species)
            if not getattr(base_formula, 'requires_feedstock_metadata', False):
                continue
            if species not in entries:
                raise ValueError(
                    f"{feedstock.get('label', 'feedstock')} uses mixed "
                    f"species {species!r}; declare "
                    f"stage0_formula_inventory.{species} with explicit "
                    "formula and furnace offgas metadata"
                )
            self._validate_stage0_formula_metadata(
                species, entries[species], feedstock=feedstock)

    @classmethod
    def _validate_stage0_formula_metadata(
        cls,
        species: str,
        entry: Mapping[str, Any],
        *,
        feedstock: Mapping[str, Any] | None = None,
    ) -> None:
        has_formula = any(
            key in entry
            for key in (
                'template',
                'generic_formula',
                'atoms',
                'elements',
                'formula',
                'atom_mass_fractions',
                'element_mass_fractions',
            )
        )
        if not has_formula:
            raise ValueError(
                f'stage0_formula_inventory.{species} must declare a formula '
                'or template'
            )
        for key in ('decomposition_temp_range_C', 'final_temp_C',
                    'cap_kg_per_tonne', 'source'):
            if key not in entry:
                raise ValueError(
                    f'stage0_formula_inventory.{species}.{key} is required')
        _temp_start, temp_final = cls._validate_temp_range(
            f'stage0_formula_inventory.{species}.decomposition_temp_range_C',
            entry['decomposition_temp_range_C'],
        )
        final_temp = float(entry['final_temp_C'])
        if not math.isfinite(final_temp) or final_temp < temp_final:
            raise ValueError(
                f'stage0_formula_inventory.{species}.final_temp_C must be '
                'finite and not below the decomposition range'
            )
        if feedstock and 'stage0_temp_range_C' in feedstock:
            stage_start, stage_final = cls._validate_temp_range(
                'stage0_temp_range_C',
                feedstock['stage0_temp_range_C'],
            )
            if _temp_start < stage_start - 1e-9 or temp_final > stage_final + 1e-9:
                raise ValueError(
                    f'stage0_formula_inventory.{species}.'
                    'decomposition_temp_range_C must be within stage0_temp_range_C'
                )
            if final_temp > stage_final + 1e-9:
                raise ValueError(
                    f'stage0_formula_inventory.{species}.final_temp_C must not '
                    'exceed stage0_temp_range_C final temperature'
                )
        cap = entry['cap_kg_per_tonne']
        cap_values = cap if isinstance(cap, (list, tuple)) else [cap]
        if not cap_values:
            raise ValueError(
                f'stage0_formula_inventory.{species}.cap_kg_per_tonne is empty'
            )
        for value in cap_values:
            number = float(value)
            if not math.isfinite(number) or number <= 0.0:
                raise ValueError(
                    f'stage0_formula_inventory.{species}.cap_kg_per_tonne '
                    'must be positive'
                )

    @staticmethod
    def _validate_temp_range(name: str, value: Any) -> Tuple[float, float]:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f'{name} must be [start_C, final_C]')
        start, final = (float(value[0]), float(value[1]))
        if not math.isfinite(start) or not math.isfinite(final) or final <= start:
            raise ValueError(f'{name} must increase to a finite final temperature')
        return start, final

    def _apply_stage0_offgas_chemistry(
        self,
        feedstock: Mapping[str, Any],
        buckets: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        entries = self._feedstock_formula_entries(feedstock)
        external_inputs: Dict[str, float] = {}
        gas_bucket = buckets.get('gas_volatiles', {})
        for species, kg in list(gas_bucket.items()):
            entry = entries.get(species)
            if not entry:
                continue
            mode = str(entry.get('offgas_mode', '')).lower()
            if mode not in {'complete_oxidation', 'oxidized'}:
                continue
            products_kg, oxidant_kg = self._oxidized_stage0_products(
                species, kg)
            gas_bucket.pop(species, None)
            self._merge_masses(gas_bucket, products_kg)
            if oxidant_kg > 0.0:
                external_inputs['O2'] = (
                    external_inputs.get('O2', 0.0) + oxidant_kg)
        return external_inputs

    def _oxidized_stage0_products(
        self, species: str, kg: float
    ) -> Tuple[Dict[str, float], float]:
        formula = resolve_species_formula(species, self.species_formula_registry)
        species_mol = float(kg) / formula.molar_mass_kg_per_mol()
        atom_mol = formula.atom_moles(species_mol)
        unsupported = set(atom_mol) - {'C', 'H', 'O', 'N'}
        if unsupported:
            raise ValueError(
                f'{species} complete_oxidation does not support atoms: '
                + ', '.join(sorted(unsupported))
            )

        products_mol: Dict[str, float] = {}
        carbon_mol = atom_mol.get('C', 0.0)
        hydrogen_mol = atom_mol.get('H', 0.0)
        nitrogen_mol = atom_mol.get('N', 0.0)
        feed_oxygen_mol = atom_mol.get('O', 0.0)

        if carbon_mol > 0.0:
            products_mol['CO2'] = carbon_mol
        if hydrogen_mol > 0.0:
            products_mol['H2O'] = hydrogen_mol / 2.0
        if nitrogen_mol > 0.0:
            products_mol['N2'] = nitrogen_mol / 2.0

        product_oxygen_mol = 2.0 * carbon_mol + hydrogen_mol / 2.0
        oxygen_deficit_mol = product_oxygen_mol - feed_oxygen_mol
        oxidant_o2_mol = max(0.0, oxygen_deficit_mol / 2.0)
        if oxygen_deficit_mol < -1e-12:
            products_mol['O2'] = products_mol.get('O2', 0.0) + (
                -oxygen_deficit_mol / 2.0)

        products_kg = {
            product: mol * resolve_species_formula(
                product, self.species_formula_registry
            ).molar_mass_kg_per_mol()
            for product, mol in products_mol.items()
            if mol > 0.0
        }
        oxidant_kg = oxidant_o2_mol * resolve_species_formula(
            'O2', self.species_formula_registry).molar_mass_kg_per_mol()
        return products_kg, oxidant_kg

    @classmethod
    def _melt_from_composition(
        cls, comp: Mapping[str, Any], mass_kg: float
    ) -> Dict[str, float]:
        return {
            oxide: cls._mass_from_wt_pct(comp.get(oxide, 0.0), mass_kg) or 0.0
            for oxide in OXIDE_SPECIES
        }

    @classmethod
    def _melt_from_raw_inventory(
        cls, raw: Mapping[str, float]
    ) -> Dict[str, float]:
        return {
            oxide: float(raw.get(oxide, 0.0))
            for oxide in OXIDE_SPECIES
        }

    @classmethod
    def _melt_from_anhydrous_silicate(
        cls,
        feedstock: Mapping[str, Any],
        batch_mass_kg: float,
        *,
        cleaned_mass_kg: Optional[float] = None,
    ) -> Dict[str, float]:
        stage0 = feedstock.get('anhydrous_silicate_after_degassing') or {}
        comp = stage0.get('composition_wt_pct', {}) or {}
        cleaned_mass = (
            cls._cleaned_melt_mass_kg(stage0, batch_mass_kg)
            if cleaned_mass_kg is None
            else cleaned_mass_kg
        )
        melt = cls._component_masses_from_wt_pct(comp, cleaned_mass)
        cls._normalize_component_masses(melt, cleaned_mass)
        return {
            oxide: float(melt.get(oxide, 0.0))
            for oxide in OXIDE_SPECIES
        }

    @classmethod
    def _cleaned_melt_mass_kg(
        cls, stage0: Mapping[str, Any], batch_mass_kg: float
    ) -> float:
        mass_per_tonne = cls._representative_number(
            stage0.get('mass_per_tonne_kg'))
        if mass_per_tonne is None:
            mass_per_tonne = DEFAULT_CARBONACEOUS_MELT_KG_PER_TONNE
        return batch_mass_kg * mass_per_tonne / 1000.0

    @classmethod
    def _unsupported_cleaned_melt_components(
        cls, feedstock: Mapping[str, Any], source: str, batch_mass_kg: float
    ) -> Dict[str, float]:
        if source != 'anhydrous_silicate_after_degassing':
            return {}
        stage0 = feedstock.get('anhydrous_silicate_after_degassing') or {}
        comp = stage0.get('composition_wt_pct', {}) or {}
        cleaned_mass = cls._cleaned_melt_mass_kg(stage0, batch_mass_kg)
        residual: Dict[str, float] = {}
        for component, raw_value in comp.items():
            if component in OXIDE_SPECIES:
                continue
            kg = cls._mass_from_wt_pct(raw_value, cleaned_mass)
            if kg is not None and kg > 0.0:
                residual[f'cleaned_melt_{component}'] = kg
        return residual

    @classmethod
    def _uses_mars_carbon_cleanup(
        cls, feedstock: Mapping[str, Any]
    ) -> bool:
        profile_hint = str(
            feedstock.get('stage0_profile')
            or feedstock.get('stage0_process')
            or ''
        ).lower()
        return profile_hint == 'mars_carbon_cleanup'

    @classmethod
    def _uses_carbonaceous_degas_cleanup(
        cls, feedstock: Mapping[str, Any]
    ) -> bool:
        profile_hint = str(
            feedstock.get('stage0_profile')
            or feedstock.get('stage0_process')
            or ''
        ).lower()
        return profile_hint == 'carbonaceous_degas_cleanup'

    @classmethod
    def _stage0_temp_range_from_feedstock(
        cls,
        feedstock: Mapping[str, Any],
        *,
        default: Tuple[float, float],
        require_explicit: bool = False,
    ) -> Tuple[float, float]:
        raw = feedstock.get('stage0_temp_range_C')
        if raw is None:
            if require_explicit:
                raise ValueError(
                    'stage0_temp_range_C must be explicit for this Stage 0 profile'
                )
            return default
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise ValueError('stage0_temp_range_C must be [start_C, final_C]')
        start, final = (float(raw[0]), float(raw[1]))
        if not math.isfinite(start) or not math.isfinite(final) or final <= start:
            raise ValueError('stage0_temp_range_C must increase to a finite final temperature')
        return (start, final)

    @classmethod
    def _processable_stage0_components(
        cls, raw: Mapping[str, float]
    ) -> set:
        return {
            component
            for component in raw
            if component not in OXIDE_SPECIES
            and cls._stage0_bucket_for_name(component) is not None
        }

    @staticmethod
    def _subset_masses(
        values: Mapping[str, float], keys: set
    ) -> Dict[str, float]:
        return {key: values[key] for key in keys if key in values}

    @classmethod
    def _declared_stage0_product_buckets(
        cls, feedstock: Mapping[str, Any], batch_mass_kg: float
    ) -> Dict[str, Dict[str, float]]:
        buckets = cls._empty_stage0_buckets()
        seen = set()
        for section_name in ('key_products', 'bonus_products',
                             'structural_water'):
            for raw_name, raw_value in (
                (feedstock.get(section_name, {}) or {}).items()
            ):
                if not str(raw_name).endswith('_kg_per_tonne'):
                    continue
                name = cls._component_name_from_field(str(raw_name))
                key = cls._normalized_component_key(name)
                if key in seen:
                    continue
                kg = cls._mass_from_kg_per_tonne(raw_value, batch_mass_kg)
                bucket_name = cls._stage0_bucket_for_name(name)
                if kg is None or kg <= 0.0 or bucket_name is None:
                    continue
                buckets[bucket_name][name] = (
                    buckets[bucket_name].get(name, 0.0) + kg)
                seen.add(key)
        return buckets

    @classmethod
    def _carbon_reductant_required_kg(
        cls, feedstock: Mapping[str, Any], batch_mass_kg: float
    ) -> float:
        notes = str(feedstock.get('process_notes', '') or '')
        marker = re.compile(
            r'(?P<low>\d+(?:\.\d+)?)'
            r'(?:\s*(?:-|–|—|to|/)\s*(?P<high>\d+(?:\.\d+)?))?'
            r'\s*kg\s*C\s*/\s*t',
            re.IGNORECASE,
        )
        match = marker.search(notes)
        if match is None:
            return 0.0
        try:
            low = float(match.group('low'))
            high = float(match.group('high') or low)
        except ValueError:
            return 0.0
        kg_per_tonne = (low + high) / 2.0
        return batch_mass_kg * kg_per_tonne / 1000.0

    @classmethod
    def _residual_components_after_stage0(
        cls, raw: Mapping[str, float], processed_components: set
    ) -> Dict[str, float]:
        return {
            component: kg
            for component, kg in raw.items()
            if component not in OXIDE_SPECIES
            and component not in processed_components
            and kg > 0.0
        }

    @classmethod
    def _add_stage0_balance_residue(
        cls,
        residual: Dict[str, float],
        batch_mass_kg: float,
        melt: Mapping[str, float],
        buckets: Mapping[str, Mapping[str, float]],
        *,
        unbacked_declared_stage0_products_kg: float = 0.0,
        stage0_external_inputs_kg: float = 0.0,
    ) -> float:
        accounted = (
            sum(melt.values())
            + cls._bucket_mass(buckets)
            + sum(residual.values())
        )
        unassigned = (
            batch_mass_kg + float(stage0_external_inputs_kg) - accounted
        )
        if abs(unassigned) > 1e-6:
            if unassigned < 0.0 and unbacked_declared_stage0_products_kg > 1e-9:
                detail = (
                    'declared Stage 0 products exceed available feedstock mass'
                )
            else:
                detail = 'Stage 0 products do not close against feedstock mass'
            raise ValueError(
                f'{detail}: delta={unassigned:.6g} kg; provide explicit '
                'source/product accounts instead of a balance plug'
            )
        return 0.0

    @classmethod
    def _unbacked_declared_stage0_products_kg(
        cls,
        declared_buckets: Mapping[str, Mapping[str, float]],
        raw: Mapping[str, float],
    ) -> float:
        raw_keys = {cls._normalized_component_key(component)
                    for component, kg in raw.items() if kg > 0.0}
        unbacked = 0.0
        for bucket_name, bucket in declared_buckets.items():
            for component, kg in bucket.items():
                key = cls._normalized_component_key(component)
                backed = key in raw_keys
                if bucket_name == 'metal_alloy':
                    if key in {'fe', 'iron'}:
                        backed = backed or bool(
                            raw_keys & {'fe', 'feo', 'fe2o3',
                                        'metallic_feni', 'fe_ni_alloy'}
                        )
                    elif key in {'ni', 'nickel'}:
                        backed = backed or bool(
                            raw_keys & {'ni', 'nio', 'metallic_feni',
                                        'fe_ni_alloy'}
                        )
                    elif key in {'co', 'cobalt'}:
                        backed = backed or bool(
                            raw_keys & {'co', 'coo', 'fe_ni_co'}
                        )
                    elif 'alloy' in key or 'feni' in key:
                        has_fe_source = bool(
                            raw_keys & {'fe', 'feo', 'fe2o3',
                                        'metallic_feni', 'fe_ni',
                                        'fe_ni_alloy', 'fe_ni_co'}
                        )
                        has_ni_source = bool(
                            raw_keys & {'ni', 'nio', 'metallic_feni',
                                        'fe_ni', 'fe_ni_alloy',
                                        'fe_ni_co'}
                        )
                        backed = backed or (has_fe_source and has_ni_source)
                if key in {'hydrocarbons', 'organics_hydrocarbons',
                           'co_ch4_propellant', 'ch4_nh3_hcn'}:
                    backed = backed or bool(
                        raw_keys & {'c', 'carbon_content',
                                    'carbonaceous_organic', 'organics'}
                    )
                if key in {'nh3', 'nh3_hcn'}:
                    backed = backed or bool(
                        raw_keys & {'nh3', 'ch4_nh3_hcn',
                                    'c', 'carbon_content',
                                    'carbonaceous_organic', 'organics'}
                    )
                if key in {'sulfuric_acid_feedstock'}:
                    backed = backed or bool(raw_keys & {'so3', 'sulfate',
                                                        'sulfates'})
                if key in {'nacl_kcl_salts'}:
                    backed = backed or bool(raw_keys & {'cl', 'halide',
                                                        'halides'})
                if key == 'o2_extra':
                    backed = backed or bool(
                        raw_keys & {'so3', 'clo4', 'perchlorate',
                                    'perchlorates'}
                    )
                if key == 'carbonate_salts':
                    backed = backed or bool(
                        raw_keys & {'c', 'carbon_content', 'carbonate',
                                    'carbonaceous_organic', 'carbonates'}
                    )
                if not backed:
                    unbacked += kg
        return unbacked

    @staticmethod
    def _empty_stage0_buckets() -> Dict[str, Dict[str, float]]:
        return {
            'gas_volatiles': {},
            'salt_phase': {},
            'sulfide_matte': {},
            'metal_alloy': {},
            'terminal_slag': {},
        }

    @classmethod
    def _merge_stage0_buckets(
        cls,
        target: Dict[str, Dict[str, float]],
        additions: Mapping[str, Mapping[str, float]],
    ) -> None:
        locations = {
            cls._normalized_component_key(component): (bucket_name, component)
            for bucket_name, bucket in target.items()
            for component in bucket
        }
        for bucket_name, bucket in additions.items():
            if bucket_name not in target:
                continue
            for component, kg in bucket.items():
                key = cls._normalized_component_key(component)
                if key in locations:
                    old_bucket, old_component = locations[key]
                    target[old_bucket].pop(old_component, None)
                target[bucket_name][component] = (
                    target[bucket_name].get(component, 0.0) + kg)
                locations[key] = (bucket_name, component)

    @staticmethod
    def _bucket_mass(buckets: Mapping[str, Mapping[str, float]]) -> float:
        return sum(sum(bucket.values()) for bucket in buckets.values())

    @staticmethod
    def _stage0_products_from_buckets(
        buckets: Mapping[str, Mapping[str, float]]
    ) -> Dict[str, float]:
        products: Dict[str, float] = {}
        for bucket_name in ('gas_volatiles', 'salt_phase', 'sulfide_matte'):
            bucket = buckets.get(bucket_name, {})
            for component, kg in bucket.items():
                products[component] = products.get(component, 0.0) + kg
        return products

    @classmethod
    def _component_masses_from_wt_pct(
        cls, values: Mapping[str, Any], mass_kg: float
    ) -> Dict[str, float]:
        masses: Dict[str, float] = {}
        for component, raw_value in values.items():
            kg = cls._mass_from_wt_pct(raw_value, mass_kg)
            if kg is not None and kg > 0.0:
                masses[str(component)] = kg
        return masses

    @classmethod
    def _component_masses_from_named_section(
        cls, values: Mapping[str, Any], mass_kg: float
    ) -> Dict[str, float]:
        masses: Dict[str, float] = {}
        for raw_name, raw_value in values.items():
            name = cls._component_name_from_field(str(raw_name))
            if str(raw_name).endswith('_kg_per_tonne'):
                kg = cls._mass_from_kg_per_tonne(raw_value, mass_kg)
            else:
                kg = cls._mass_from_wt_pct(raw_value, mass_kg)
            if kg is not None and kg > 0.0:
                masses[name] = masses.get(name, 0.0) + kg
        return masses

    @staticmethod
    def _normalize_component_masses(
        masses: Dict[str, float], target_mass_kg: float
    ) -> None:
        total = sum(kg for kg in masses.values() if kg > 0.0)
        if total <= 0.0 or target_mass_kg <= 0.0:
            return
        scale = target_mass_kg / total
        for component, kg in list(masses.items()):
            masses[component] = kg * scale

    @staticmethod
    def _merge_masses(target: Dict[str, float],
                      additions: Mapping[str, float]) -> None:
        for component, kg in additions.items():
            target[component] = target.get(component, 0.0) + kg

    @classmethod
    def _classify_stage0_components(
        cls, components: Mapping[str, float]
    ) -> Dict[str, Dict[str, float]]:
        buckets = {
            'gas_volatiles': {},
            'salt_phase': {},
            'sulfide_matte': {},
            'metal_alloy': {},
            'terminal_slag': {},
        }
        for component, kg in components.items():
            bucket_name = cls._stage0_bucket_for_name(component)
            if bucket_name is not None:
                buckets[bucket_name][component] = kg
        return buckets

    @classmethod
    def _stage0_bucket_for_name(cls, component: str) -> Optional[str]:
        key = cls._normalized_component_key(component)
        if key in STAGE0_GAS_COMPONENTS or key.startswith('h2o'):
            return 'gas_volatiles'
        if key.startswith(('co_', 'ch4_', 'nh3_', 'hydrocarbon')):
            return 'gas_volatiles'
        if key in STAGE0_SALT_COMPONENTS:
            return 'salt_phase'
        if key.startswith(('nacl', 'kcl', 'carbonate_salt',
                           'sulfuric_acid')):
            return 'salt_phase'
        if key in STAGE0_SULFIDE_COMPONENTS:
            return 'sulfide_matte'
        if key.startswith('s_'):
            return 'sulfide_matte'
        if key in STAGE0_METAL_ALLOY_COMPONENTS:
            return 'metal_alloy'
        if 'alloy' in key or 'feni' in key:
            return 'metal_alloy'
        if key in STAGE0_TERMINAL_SLAG_COMPONENTS:
            return 'terminal_slag'
        if key.startswith(('ree', 'zr', 'th', 'u')):
            return 'terminal_slag'
        return None

    @classmethod
    def _mass_from_wt_pct(cls, value: Any, mass_kg: float) -> Optional[float]:
        number = cls._representative_number(value)
        if number is None:
            return None
        return mass_kg * number / 100.0

    @classmethod
    def _mass_from_kg_per_tonne(
        cls, value: Any, batch_mass_kg: float
    ) -> Optional[float]:
        number = cls._representative_number(value)
        if number is None:
            return None
        return batch_mass_kg * number / 1000.0

    @staticmethod
    def _representative_number(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                return (float(value[0]) + float(value[1])) / 2.0
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _component_name_from_field(raw_name: str) -> str:
        for suffix in ('_wt_pct', '_kg_per_tonne'):
            if raw_name.endswith(suffix):
                return raw_name[:-len(suffix)]
        return raw_name

    @staticmethod
    def _normalized_component_key(component: str) -> str:
        return component.strip().lower().replace('-', '_').replace(' ', '_')

    def start_campaign(self, campaign: CampaignPhase):
        """Begin a campaign phase.  Sets atmosphere, temp targets, etc."""
        self.melt.campaign = campaign
        self.melt.campaign_hour = 0
        self.paused_for_decision = False
        self.pending_decision = None

        # Record start state for campaign summary
        self._campaign_start_hour = self.melt.hour
        self._campaign_start_mass = self.melt.total_mass_kg
        self._campaign_start_composition = dict(self.melt.composition_kg)
        self._campaign_start_condensation = dict(self.train.total_by_species())
        self._campaign_start_energy = self.energy_cumulative_kWh
        self._campaign_start_O2 = self._oxygen_total_kg()

        # Configure atmosphere and targets from setpoints
        self.campaign_mgr.configure_campaign(self.melt, campaign)

        # Initialize shuttle inventory when entering C3 phases
        if campaign in (CampaignPhase.C3_K, CampaignPhase.C3_NA):
            self._init_shuttle_inventory(campaign)

        # Initialize thermite Mg inventory when entering C6
        if campaign == CampaignPhase.C6:
            self._init_thermite_inventory()

        # Initialize MRE voltage sequence and reset step tracking      [Step 9c]
        if campaign in (CampaignPhase.MRE_BASELINE, CampaignPhase.C5):
            self._mre_voltage_sequence = self._build_mre_voltage_sequence()
            self._mre_voltage_step_idx = 0
            self._mre_hold_hours = 0
            self._mre_effective_current_A = 0.0
            self.melt.mre_low_current_hours = 0

        # Pass C4 max temp to campaign manager
        self.campaign_mgr.c4_max_temp_C = self.c4_max_temp_C

    def is_complete(self) -> bool:
        return self.melt.campaign == CampaignPhase.COMPLETE

    def product_ledger(self) -> Dict[str, float]:
        """
        Return output products accumulated outside the remaining melt.

        The atom ledger is the source of truth. Condenser stage dictionaries are
        UI projections and must not mint product mass.
        """
        products: Dict[str, float] = {}
        for account in (
            'terminal.offgas',
            'terminal.stage0_salt_phase',
            'terminal.stage0_sulfide_matte',
            'terminal.drain_tap_material',
            'process.condensation_train',
            'process.overhead_gas',
        ):
            self._merge_masses(
                products,
                {
                    species: kg
                    for species, kg in self.atom_ledger.kg_by_account(
                        account).items()
                    if species != OXYGEN_SPECIES
                },
            )
        self._merge_masses(products, self._unspent_additive_reagents_kg())
        return products

    def _terminal_slag_kg(self) -> float:
        return (
            self.atom_ledger.total_kg_by_account('process.cleaned_melt')
            + self.atom_ledger.total_kg_by_account('terminal.slag')
        )

    def _ledger_total_mass_kg(self) -> float:
        return sum(self.atom_ledger.total_kg_by_account().values())

    def _flow_mass_out_kg(self) -> float:
        totals = self.atom_ledger.total_kg_by_account()
        accounts = set(FLOW_MASS_ACCOUNTS)
        accounts.update(
            account for account in totals
            if account.startswith('reservoir.reagent.')
        )
        return sum(float(totals.get(account, 0.0)) for account in accounts)

    def _finalize_record(self) -> None:
        """Populate final batch ledger fields when a batch completes."""
        oxygen_partition = self._oxygen_terminal_partition_kg()
        self._sync_oxygen_kg_counters(oxygen_partition)
        self.record.products_kg = self.product_ledger()
        self.record.oxygen_total_kg = oxygen_partition['total']
        self.record.oxygen_stored_kg = oxygen_partition['stored']
        self.record.oxygen_vented_kg = oxygen_partition['vented']
        self.record.terminal_slag_kg = self._terminal_slag_kg()
        self.record.energy_total_kWh = self.energy_cumulative_kWh
        self.record.total_hours = self.melt.hour
        self.record.completed = True

    def _ledger_o2_kg(self, account: str) -> float:
        species_kg = self.atom_ledger.kg_by_account(account)
        return max(0.0, float(species_kg.get(OXYGEN_SPECIES, 0.0)))

    def _train_o2_kg(self) -> float:
        return sum(
            max(0.0, float(stage.collected_kg.get(OXYGEN_SPECIES, 0.0)))
            for stage in self.train.stages
        )

    def _oxygen_terminal_partition_kg(self) -> Dict[str, float]:
        stored_by_source = {
            account: self._ledger_o2_kg(account)
            for account in OXYGEN_STORED_ACCOUNTS
        }
        vented_by_source = {
            account: self._ledger_o2_kg(account)
            for account in OXYGEN_VENTED_ACCOUNTS
        }
        stored_kg = sum(stored_by_source.values())
        vented_kg = sum(vented_by_source.values())
        return {
            'stored': stored_kg,
            'vented': vented_kg,
            'total': stored_kg + vented_kg,
            'melt_offgas_stored': stored_by_source.get(
                OXYGEN_MELT_OFFGAS_ACCOUNT, 0.0),
            'mre_anode_stored': stored_by_source.get(
                OXYGEN_MRE_ANODE_ACCOUNT, 0.0),
            'melt_offgas_vented': vented_by_source.get(
                OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT, 0.0),
        }

    def _sync_oxygen_kg_counters(
        self, partition: Optional[Mapping[str, float]] = None
    ) -> None:
        oxygen_partition = partition or self._oxygen_terminal_partition_kg()
        self.O2_stored_cumulative_kg = oxygen_partition['stored']
        self.O2_vented_cumulative_kg = oxygen_partition['vented']
        self.oxygen_cumulative_kg = oxygen_partition['total']

    def _oxygen_stored_kg(self) -> float:
        return self._oxygen_terminal_partition_kg()['stored']

    def _oxygen_vented_kg(self) -> float:
        return self._oxygen_terminal_partition_kg()['vented']

    def _oxygen_total_kg(self) -> float:
        return self._oxygen_terminal_partition_kg()['total']

    def _condensation_totals_with_terminal_oxygen(self) -> Dict[str, float]:
        totals = {
            species: float(kg)
            for species, kg in self.atom_ledger.kg_by_account(
                'process.condensation_train').items()
            if kg > 1e-12
        }
        oxygen_partition = self._oxygen_terminal_partition_kg()
        melt_offgas_stored = oxygen_partition['melt_offgas_stored']
        if melt_offgas_stored > OXYGEN_ACCOUNTING_TOLERANCE_KG:
            totals[OXYGEN_SPECIES] = melt_offgas_stored
        else:
            totals.pop(OXYGEN_SPECIES, None)
        return totals

    def _overhead_gas_totals(self) -> Dict[str, float]:
        return {
            species: float(kg)
            for species, kg in self.atom_ledger.kg_by_account(
                'process.overhead_gas').items()
            if kg > 1e-12
        }

    def _drain_overhead_gas_to_terminal(self) -> None:
        gas_kg = self._overhead_gas_totals()
        if not gas_kg:
            return
        self.atom_ledger.apply(
            LedgerTransition.move(
                f'drain_overhead_gas_{self.melt.hour}',
                'process.overhead_gas',
                'terminal.offgas',
                gas_kg,
                reason='overhead vapor stream leaves current-tick gas volume',
            )
        )

    def _debit_vented_oxygen(self, vented_kg: float) -> None:
        vented_kg = max(0.0, float(vented_kg))
        if vented_kg <= OXYGEN_ACCOUNTING_TOLERANCE_KG:
            return
        stored_kg = self._ledger_o2_kg(OXYGEN_MELT_OFFGAS_ACCOUNT)
        if vented_kg > stored_kg + OXYGEN_ACCOUNTING_TOLERANCE_KG:
            raise AccountingError(
                f"cannot vent {vented_kg:.12g} kg O2; only "
                f"{stored_kg:.12g} kg is in {OXYGEN_MELT_OFFGAS_ACCOUNT}"
            )
        self.atom_ledger.apply(
            LedgerTransition.move(
                'vent_terminal_oxygen',
                OXYGEN_MELT_OFFGAS_ACCOUNT,
                OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
                {OXYGEN_SPECIES: vented_kg},
                reason='O2 vented to vacuum',
            )
        )
        for stage in self.train.stages:
            stage.collected_kg.pop(OXYGEN_SPECIES, None)
        self._sync_oxygen_kg_counters()

    def _unspent_additive_reagents_kg(self) -> Dict[str, float]:
        reagents = (
            set(self.record.additives_kg)
            | set(self._activated_additive_reagents)
        )
        unspent: Dict[str, float] = {}
        for reagent in sorted(reagents):
            kg = (
                self.atom_ledger.kg_by_account(
                    f'reservoir.reagent.{reagent}').get(reagent, 0.0)
                + self.atom_ledger.kg_by_account(
                    'process.reagent_inventory').get(reagent, 0.0)
            )
            if kg > 1e-9:
                unspent[f'unspent_{reagent}_reagent'] = kg
        return unspent

    def _capture_campaign_summary(self, campaign_name: str) -> dict:
        """Capture a summary of what happened during the just-completed campaign."""
        duration_h = self.melt.hour - self._campaign_start_hour
        end_mass = self.melt.total_mass_kg
        mass_lost = self._campaign_start_mass - end_mass

        # Species extracted this campaign (delta in condensation totals)
        current_cond = self.train.total_by_species()
        species_extracted = {}
        for sp, kg in current_cond.items():
            start_kg = self._campaign_start_condensation.get(sp, 0.0)
            delta = kg - start_kg
            if delta > 0.001:
                species_extracted[sp] = round(delta, 3)

        return {
            'campaign': campaign_name,
            'duration_h': duration_h,
            'start_mass_kg': round(self._campaign_start_mass, 1),
            'end_mass_kg': round(end_mass, 1),
            'mass_lost_kg': round(mass_lost, 1),
            'energy_kWh': round(
                self.energy_cumulative_kWh - self._campaign_start_energy, 1),
            'O2_kg': round(
                self._oxygen_total_kg() - self._campaign_start_O2, 2),
            'species_extracted': species_extracted,
        }

    # ------------------------------------------------------------------
    # THE CORE LOOP
    # ------------------------------------------------------------------

    def step(self) -> HourSnapshot:
        """
        Advance the simulation by one hour.

        This is the main simulation loop.  Each call:

        1. Check if paused for a decision
        2. Update temperature (ramp toward campaign target)
        3. Calculate thermodynamic equilibrium (via melt backend)
        4. Calculate evaporation flux (Hertz-Knudsen-Langmuir)
        5. Route evaporated species through condensation train
        6. Update melt composition (subtract evaporated mass)
        7. Update overhead gas & turbine pressure
        8. Calculate energy consumption this hour
        9. Check campaign endpoint criteria
        10. Record snapshot

        Returns:
            HourSnapshot with full system state at this hour
        """
        # --- 1. Decision check ---
        if self.paused_for_decision:
            # Return current state without advancing
            return self._make_snapshot()
        self._melt_offgas_O2_kg_this_hr = 0.0
        self._mre_anode_O2_kg_this_hr = 0.0

        # --- 2. Temperature ramp ---
        self._update_temperature()

        # --- 3. Thermodynamic equilibrium ---
        # Query the melt backend for phase assemblage, activities,
        # and vapor pressures at the current T and composition.
        equilibrium = self._get_equilibrium()

        # --- 4. Evaporation flux ---
        # Hertz-Knudsen-Langmuir: how fast each species leaves the melt.
        # Only during pyrolysis campaigns (C0, C2A, C2B, C3 bakeout, C4).
        # Not during MRE (C5) — electrolysis produces O₂ at the anode.
        evap_flux = EvaporationFlux()
        if self.melt.campaign in (CampaignPhase.C0, CampaignPhase.C0B,
                                   CampaignPhase.C2A, CampaignPhase.C2B,
                                   CampaignPhase.C3_K, CampaignPhase.C3_NA,
                                   CampaignPhase.C4):
            evap_flux = self._calculate_evaporation(equilibrium)

        # --- 4b. Alkali shuttle injection (C3 only) ---        [THERMO-5]
        # During C3, the shuttle cycle alternates between injection
        # (alkali reduces target oxides) and bakeout (alkali oxide
        # evaporates, alkali recovered).  Injection modifies the melt
        # composition directly; bakeout uses normal evaporation.
        if self.melt.campaign in (CampaignPhase.C3_K, CampaignPhase.C3_NA):
            self._step_shuttle()
        else:
            self._shuttle_injected_this_hr = 0.0
            self._shuttle_reduced_this_hr = 0.0
            self._shuttle_metal_this_hr = 0.0
            self._shuttle_phase = ''

        # --- 5. Condensation routing ---
        # Send evaporated species through the 7-stage train.
        # Each stage collects species based on its temperature.
        if evap_flux.total_kg_hr > 0:
            self._route_to_condensation(evap_flux)

        # --- 6. Update melt composition ---
        # Subtract evaporated mass from the melt.
        self._update_melt_composition(evap_flux)

        # --- 6b. MRE step (C5 / MRE baseline) ---
        mre_O2_kg = 0.0
        mre_energy_kWh = 0.0
        if self.melt.campaign in (CampaignPhase.C5,
                                   CampaignPhase.MRE_BASELINE):
            mre_O2_kg = self._step_mre()
            self._mre_anode_O2_kg_this_hr = mre_O2_kg
            mre_energy_kWh = self._mre_energy_this_hr
        else:
            self._mre_voltage_V = 0.0
            self._mre_current_A = 0.0
            self._mre_effective_current_A = 0.0
            self._mre_metals_this_hr = {}
            self._mre_energy_this_hr = 0.0
            self._mre_anode_O2_kg_this_hr = 0.0

        # --- 6c. Mg thermite step (C6) ---                      [THERMO-7]
        if self.melt.campaign == CampaignPhase.C6:
            self._step_thermite()
        else:
            self._thermite_Al2O3_reduced_this_hr = 0.0
            self._thermite_Al_produced_this_hr = 0.0
            self._thermite_Mg_consumed_this_hr = 0.0

        # --- 7. Overhead gas (with turbine capacity feedback) ---   [LOOP-2]
        # Pass the turbine spec so overhead model can enforce capacity limits,
        # compute O₂ venting, and calculate transport saturation.
        turbine_spec = self._get_turbine_spec()
        melt_offgas_O2_kg_hr = max(0.0, self._melt_offgas_O2_kg_this_hr)
        self.overhead = self.overhead_model.update(
            evap_flux,
            self.melt,
            self.train,
            turbine_spec=turbine_spec,
            actual_O2_kg_hr=melt_offgas_O2_kg_hr,
            actual_O2_mol_hr=melt_offgas_O2_kg_hr / OXYGEN_MOLAR_MASS_KG_PER_MOL,
            mre_anode_O2_mol_hr=(
                self._mre_anode_O2_kg_this_hr / OXYGEN_MOLAR_MASS_KG_PER_MOL))

        # Track cumulative O₂ vented and stored
        self._debit_vented_oxygen(self.overhead.O2_vented_kg_hr)
        self._drain_overhead_gas_to_terminal()
        self._sync_oxygen_kg_counters()

        # --- 8. Energy ---
        energy = self.energy_tracker.calculate_hour(
            self.melt, self.overhead, evap_flux,
            mre_kWh=mre_energy_kWh,  # Actual MRE energy this hour
        )
        self.energy_cumulative_kWh += energy.total_kWh

        # --- 9. Endpoint check ---
        campaign_done = self.campaign_mgr.check_endpoint(
            self.melt, evap_flux, self.train, self.record)
        if campaign_done:
            # Capture campaign summary before transitioning
            finishing_campaign = self.melt.campaign.name
            self._last_campaign_summary = self._capture_campaign_summary(
                finishing_campaign)

            next_campaign = self.campaign_mgr.get_next_campaign(
                self.melt.campaign, self.record)
            if next_campaign == CampaignPhase.COMPLETE:
                self.melt.campaign = CampaignPhase.COMPLETE
            elif next_campaign is None:
                # Decision needed before proceeding
                self.pending_decision = self.campaign_mgr.get_decision(
                    self.melt.campaign, self.record)
                self.paused_for_decision = True
            else:
                self.start_campaign(next_campaign)

        # --- 10. Record snapshot ---
        self.melt.hour += 1
        self.melt.campaign_hour += 1
        snapshot = self._make_snapshot()
        snapshot.evap_flux = evap_flux
        snapshot.energy = energy
        snapshot.energy_cumulative_kWh = self.energy_cumulative_kWh
        snapshot.oxygen_produced_kg = self._oxygen_total_kg()
        self.record.snapshots.append(snapshot)
        if self.is_complete():
            self._finalize_record()

        return snapshot

    # ------------------------------------------------------------------
    # Step sub-methods
    # ------------------------------------------------------------------

    def _update_temperature(self):
        """
        Ramp temperature toward the campaign's target.

        The ramp rate (°C/hr) is set by the campaign setpoints,
        then throttled based on gas train feedback:

        Loop 3 — Metals train throttle:                       [LOOP-3]
            When evaporation exceeds pipe transport capacity,
            ΔT/dt is scaled down proportionally:
                scale = max(0, 2.0 - transport_saturation / 100)
            At 100% saturation → nominal ramp
            At 200% saturation → ramp = 0 (hold T)

        Loop 4 — Volatiles train throttle (C0/C0b only):      [LOOP-4]
            For volatile-heavy feedstocks (KREEP, highland),
            the C0 ramp is modulated to keep offgas rate within
            the volatiles train capacity.  Same formula as Loop 3
            but keyed to volatiles-specific flow.

        The primary control is throttling; venting is the safety valve.
        """
        target_T, ramp_rate = self.campaign_mgr.get_temp_target(
            self.melt.campaign, self.melt.campaign_hour, self.melt)

        if target_T is None:
            self._last_nominal_ramp = 0.0
            self._last_actual_ramp = 0.0
            self._last_throttle_reason = ''
            return  # Isothermal hold or MRE — no ramp

        nominal_ramp = ramp_rate
        actual_ramp = ramp_rate
        throttle_reason = ''

        # --- Loop 3: Metals train transport saturation throttle ---
        sat_pct = self.overhead.transport_saturation_pct
        if sat_pct > 100.0:
            # Linear scale: 100% → full ramp, 200% → zero ramp
            scale = max(0.0, 2.0 - sat_pct / 100.0)
            actual_ramp *= scale
            throttle_reason = f'pipe saturated ({sat_pct:.0f}%)'

        # --- Loop 3b: Turbine overload (milder throttle) ---
        if self.overhead.turbine_limited:
            turb_util = self.overhead.turbine_utilization_pct
            if turb_util > 120.0:
                # Gentle throttle: only kicks in above 120% utilization
                turb_scale = max(0.3, 2.2 - turb_util / 100.0)
                actual_ramp *= turb_scale
                if throttle_reason:
                    throttle_reason += f'; turbine overloaded ({turb_util:.0f}%)'
                else:
                    throttle_reason = f'turbine overloaded ({turb_util:.0f}%)'

        # --- Loop 4: Volatiles train throttle (C0/C0b only) ---
        if self.melt.campaign in (CampaignPhase.C0, CampaignPhase.C0B):
            vol_train = getattr(self, '_volatiles_train_spec', None)
            if vol_train and vol_train.max_throughput_kg_hr > 0:
                # Estimate current volatiles offgas rate from evaporation
                # Na, K, H₂O, S, Cl species are volatiles
                volatile_species = {'Na', 'K'}  # Main C0 volatiles
                vol_rate = sum(
                    self.overhead.composition.get(sp, 0.0) * 100.0  # rough mass proxy
                    for sp in volatile_species
                )
                # Better estimate: use the actual evap flux if available
                # (from the previous hour's snapshot)
                if self.record.snapshots:
                    last = self.record.snapshots[-1]
                    vol_rate = sum(
                        last.evap_flux.species_kg_hr.get(sp, 0.0)
                        for sp in volatile_species
                    )
                vol_sat = (vol_rate / vol_train.max_throughput_kg_hr) * 100.0
                if vol_sat > 100.0:
                    vol_scale = max(0.0, 2.0 - vol_sat / 100.0)
                    actual_ramp *= vol_scale
                    vol_msg = f'volatiles train saturated ({vol_sat:.0f}%)'
                    if throttle_reason:
                        throttle_reason += f'; {vol_msg}'
                    else:
                        throttle_reason = vol_msg

        # Store for snapshot
        self._last_nominal_ramp = nominal_ramp
        self._last_actual_ramp = actual_ramp
        self._last_throttle_reason = throttle_reason

        # Apply the (possibly throttled) ramp
        delta = target_T - self.melt.temperature_C
        if abs(delta) < 0.1:
            self.melt.temperature_C = target_T
        elif actual_ramp > 0:
            step = min(abs(delta), actual_ramp)
            self.melt.temperature_C += math.copysign(step, delta)

    def _get_turbine_spec(self):
        """
        Get the turbine spec from the auto-designed plant equipment.

        Returns None if equipment hasn't been sized yet (the overhead
        model handles None gracefully by not enforcing limits).
        """
        if self._equipment is None:
            # Auto-size equipment for this batch
            from simulator.equipment import EquipmentDesigner
            designer = EquipmentDesigner()
            feedstock = self.feedstocks.get(self.record.feedstock_key, {})
            self._equipment = designer.design_for_batch(
                self.record.batch_mass_kg, feedstock)
            # Also store volatiles train spec for Loop 4 throttle
            self._volatiles_train_spec = self._equipment.volatiles_train
        return self._equipment.turbine

    # ------------------------------------------------------------------
    # Decision handling
    # ------------------------------------------------------------------

    def apply_decision(self, decision_type: DecisionType, choice: str):
        """
        Apply an operator decision and resume simulation.

        Args:
            decision_type: Which decision was made
            choice:        The chosen option string
        """
        self.record.decisions.append((decision_type, choice))

        if decision_type == DecisionType.ROOT_BRANCH:
            if choice == 'mre_baseline':
                self.record.track = 'mre_baseline'
                self.start_campaign(CampaignPhase.MRE_BASELINE)
            else:
                self.record.track = 'pyrolysis'
                self.start_campaign(CampaignPhase.C0)

        elif decision_type == DecisionType.PATH_AB:
            self.record.path = choice  # 'A' or 'B'
            if choice == 'A':
                self.start_campaign(CampaignPhase.C2A)
            else:
                self.start_campaign(CampaignPhase.C2B)

        elif decision_type == DecisionType.BRANCH_ONE_TWO:
            self.record.branch = choice  # 'one' or 'two'
            if choice == 'two':
                self.start_campaign(CampaignPhase.C4)
            else:
                self.start_campaign(CampaignPhase.C5)

        elif decision_type == DecisionType.TI_RETENTION:
            # Handled within C5 campaign logic
            pass

        elif decision_type == DecisionType.C6_PROCEED:
            if choice == 'yes':
                self.start_campaign(CampaignPhase.C6)
            else:
                self.melt.campaign = CampaignPhase.COMPLETE

        self.paused_for_decision = False
        self.pending_decision = None
        if self.is_complete():
            self._finalize_record()

    # ------------------------------------------------------------------
    # Snapshot construction
    # ------------------------------------------------------------------

    def _make_snapshot(self) -> HourSnapshot:
        """Build an HourSnapshot from current state."""
        oxygen_partition = self._oxygen_terminal_partition_kg()
        condensation_totals = self._condensation_totals_with_terminal_oxygen()
        # Mass balance check
        mass_in = self.record.batch_mass_kg + sum(
            self.record.additives_kg.values()) + sum(
            self.inventory.stage0_external_inputs_kg.values())
        mass_out = self._flow_mass_out_kg()
        error_pct = 0.0
        if mass_in > 0:
            error_pct = abs(mass_in - mass_out) / mass_in * 100.0

        return HourSnapshot(
            hour=self.melt.hour,
            campaign=self.melt.campaign,
            temperature_C=self.melt.temperature_C,
            melt_mass_kg=self.melt.total_mass_kg,
            composition_wt_pct=self.melt.composition_wt_pct(),
            inventory=self.inventory.copy(),
            overhead=self.overhead,
            condensation_totals=condensation_totals,
            energy_cumulative_kWh=self.energy_cumulative_kWh,
            oxygen_produced_kg=oxygen_partition['total'],
            mass_in_kg=mass_in,
            mass_out_kg=mass_out,
            mass_balance_error_pct=error_pct,
            # Gas train feedback
            ramp_throttled=self._last_actual_ramp < self._last_nominal_ramp * 0.99,
            nominal_ramp_rate_C_hr=self._last_nominal_ramp,
            actual_ramp_rate_C_hr=self._last_actual_ramp,
            throttle_reason=self._last_throttle_reason,
            O2_vented_kg_hr=self.overhead.O2_vented_kg_hr,
            O2_vented_mol_hr=self.overhead.O2_vented_mol_hr,
            O2_vented_cumulative_kg=oxygen_partition['vented'],
            O2_stored_kg=oxygen_partition['stored'],
            melt_offgas_O2_stored_kg=oxygen_partition['melt_offgas_stored'],
            melt_offgas_O2_vented_kg=oxygen_partition['melt_offgas_vented'],
            mre_anode_O2_stored_kg=oxygen_partition['mre_anode_stored'],
            melt_offgas_O2_mol_hr=self.overhead.melt_offgas_O2_mol_hr,
            mre_anode_O2_mol_hr=self.overhead.mre_anode_O2_mol_hr,
            turbine_shaft_power_kW=self.overhead.turbine_shaft_power_kW,
            # Alkali shuttle
            shuttle_phase=self._shuttle_phase,
            shuttle_injected_kg_hr=self._shuttle_injected_this_hr,
            shuttle_reduced_kg_hr=self._shuttle_reduced_this_hr,
            shuttle_metal_produced_kg_hr=self._shuttle_metal_this_hr,
            shuttle_K_inventory_kg=self.shuttle_K_inventory_kg,
            shuttle_Na_inventory_kg=self.shuttle_Na_inventory_kg,
            shuttle_cycle=(self.shuttle_cycle_K
                           if self.melt.campaign == CampaignPhase.C3_K
                           else self.shuttle_cycle_Na),
            # MRE electrolysis
            mre_voltage_V=self._mre_voltage_V,
            mre_current_A=self._mre_current_A,
            mre_metals_kg_hr=dict(self._mre_metals_this_hr),
        )
