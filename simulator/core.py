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
    Metals train (C2A onward) — linear 8-stage: hot duct → Fe → Cr oxide →
        SiO → alkali/Mg cyclone → vortex filter → turbine → O₂ accumulator

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

import inspect
import copy
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from simulator.account_ids import (
    CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
    OXYGEN_SPECIES,
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_STORED_ACCOUNTS,
    OXYGEN_VENTED_ACCOUNTS,
)
from simulator.accounting import (
    AccountPolicy,
    AccountingQueries,
    AccountingError,
    AtomLedger,
    LedgerTransition,
    coerce_species_formula,
    load_species_formulas,
    resolve_species_formula,
)
from simulator.accounting.completeness import (
    CompletionContractBlocked,
    DEFAULT_RESIDUAL_SPECIES_BY_TARGET,
    TargetExtractionCompleteness,
    aggregate_extraction_completeness,
    completion_contracts_for_campaign,
    extraction_completeness_by_target,
    vapor_contract_completeness,
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
    clamp_stir_factor,
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
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS,
    ProcessInventory,
    STOICH_RATIOS,
)
from simulator.equilibrium import EquilibriumMixin
from simulator.evaporation import EvaporationMixin
from simulator.extraction import ExtractionMixin
from simulator.melt_backend.base import StubBackend
from simulator.melt_backend.sulfsat import (
    SulfSatGate,
    SulfurSaturationResult,
)
from simulator.chemistry.kernel import (
    ChemistryIntent,
    ChemistryKernel,
    IntentResult,
    LedgerTransitionProposal,
    ProviderRegistry,
)
# BuiltinVaporPressureProvider is imported lazily inside
# _build_chemistry_kernel: simulator/__init__.py -> simulator.core ->
# engines.builtin.vapor_pressure -> simulator.chemistry.kernel ->
# simulator.* would re-enter this module mid-init. Deferring the import
# to first kernel build breaks the cycle.

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
STRICT_STAGE0_FORMULA_COMPONENTS = {
    'organics',
    'hydrocarbons',
    'organics_hydrocarbons',
    'carbonaceous_organic',
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
FO2_BUFFER_ACCOUNT = 'reservoir.fo2_buffer'
WALL_DEPOSIT_ACCOUNT = 'process.wall_deposit'
FLOW_MASS_ACCOUNTS = (
    'process.cleaned_melt',
    'process.raw_feedstock',
    'process.condensation_train',
    WALL_DEPOSIT_ACCOUNT,
    *PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS,
    'process.overhead_gas',
    'process.metal_phase',
    'process.reagent_inventory',
    'terminal.offgas',
    'terminal.stage0_salt_phase',
    'terminal.stage0_sulfide_matte',
    'terminal.drain_tap_material',
    'terminal.slag',
    CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
)
FLOW_MASS_EXCLUDED_ACCOUNTS = (
    'process.stage0_carbon_reductant',
    'process.stage0_perchlorate_feed',
    'process.stage0_salt_feed',
    'process.stage0_volatile_feed',
)
BACKEND_REACTIVE_ACCOUNTS = (
    'process.cleaned_melt',
    'process.metal_phase',
    'process.overhead_gas',
)
BACKEND_LEDGER_TRANSITION_NAMES = (
    'factsage_equilibrium_phase_update',
)
BACKEND_ACCOUNT_SCOPED_ONLY = (
    'process.metal_phase',
    'process.overhead_gas',
)
OXYGEN_MOLAR_MASS_KG_PER_MOL = MOLAR_MASS[OXYGEN_SPECIES] / 1000.0
OXYGEN_ACCOUNTING_TOLERANCE_KG = 1e-9
TERMINAL_RUMP_ACCOUNTS = (
    'process.cleaned_melt',
    'terminal.slag',
)
TERMINAL_RUMP_REFRACTORY_OXIDES = frozenset({
    'CaO',
    'MgO',
    'Al2O3',
    'TiO2',
    'Cr2O3',
    'REE_oxides',
})
TERMINAL_RUMP_SILICATE_RESIDUAL = frozenset({'SiO2'})
TERMINAL_RUMP_UNEXTRACTED_METALS = frozenset({'Fe', 'Ni', 'Co', 'Mn'})
TERMINAL_RUMP_CLASS_TOLERANCE_PCT = 5e-12
DEFAULT_OVERHEAD_HEADSPACE_CONFIG = {
    'enabled': False,
    'volume_m3': None,
    'temperature_model': 'melt',
    'temperature_offset_K': None,
    'bleed_model': 'poiseuille',
    'conductance_kg_s_per_bar': None,
    'downstream_pressure_bar': None,
    'liner_temperature_C': 1500.0,
}
DEFAULT_FREEZE_GATE_CONFIG = {
    'enabled': False,
}
BACKEND_FALLBACK_EXCEPTIONS = (RuntimeError, ImportError)
STAGE0_DEFAULT_TEMP_RANGE_C = (20.0, 950.0)
STAGE0_CARBON_CLEANUP_TEMP_RANGE_C = (20.0, 1050.0)
DEFAULT_CARBONACEOUS_MELT_KG_PER_TONNE = 725.0

# Atomic mass of sulfur (g/mol) used by the SulfSat gate when projecting
# Stage 0 sulfide / sulfate inventories onto a per-million melt-mass
# concentration. Kept local to avoid importing the periodic-table mass
# table at module load.
_SULFUR_ATOMIC_MASS_G_PER_MOL = 32.065
# Sulfur mass fraction of the common sulfate / sulfide carriers Stage 0
# tracks: SO3 (S/SO3 = 32/80), FeS / oldhamite-style sulfides (S/FeS
# ~= 0.36, close enough for an order-of-magnitude S_input_ppm). These
# are blended into a single S_input_ppm estimate; the gate itself
# normalises composition and is insensitive to small errors in this
# starting concentration.
_SULFUR_FRACTION_BY_CARRIER = {
    'SO3': 32.065 / 80.063,
    'SO4': 32.065 / 96.062,
    'FeS': 32.065 / 87.911,
    'CaS': 32.065 / 72.143,
    'S': 1.0,
}


def _pt0_determinism_store_for(owner: Any) -> Any | None:
    pt0_store = getattr(owner, '_pt0_store', None)
    if callable(pt0_store):
        return pt0_store()
    return getattr(owner, '_pt0_determinism_store', None)


class PyrolysisSimulator(EquilibriumMixin, EvaporationMixin, ExtractionMixin):
    """
    Hour-by-hour simulator for the Oxygen Shuttle process.

    This is the main simulation engine.  It manages:
    - Melt state evolution (composition, temperature)
    - Evaporation kinetics (Hertz-Knudsen with stirring)
    - Condensation routing (8-stage train)
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
                          May contain a top-level ``chemistry_kernel``
                          block whose ``allow_fallback_vapor`` flag
                          opts the kernel into demoting a missing
                          VapoRock to the builtin Antoine fallback (goal
                          #10 ``VAPOROCK-AUTHORITY-PROMOTION``); the
                          flag defaults to ``False`` (loud
                          :class:`ProviderUnavailableError` instead of
                          silent fallback).
            feedstocks:   Feedstock compositions from feedstocks.yaml.
            vapor_pressures: Antoine parameters from vapor_pressures.yaml.
        """
        self.backend = melt_backend
        self._last_backend_error = ''
        # Per-call outcome of the most recent EquilibriumResult
        # ('ok' / 'not_converged' / 'out_of_domain' / 'unavailable').
        # Descriptive only - no control-flow branch reads this.
        self._last_backend_status = 'ok'
        self._last_vapor_pressures_source: dict[str, str] = {}
        self._backend_failed = False
        self._stage0_carbon_cleanup_specs: list[dict] = []
        self._stage0_perchlorate_cleanup_specs: list[dict] = []
        # SULFUR_SATURATION_GATE intent (PySulfSat). Lazy-probe: when
        # the optional [sulfur] extra is absent, the gate stays
        # un-initialised and ``is_available()`` returns False, which
        # causes the Stage 0 + post-equilibrium hooks below to record
        # an 'unavailable' result and fall back to builtin partitioning.
        # The gate itself never emits a LedgerTransition (binding spec
        # §4 — it is a diagnostic gate, not a writer).
        self._sulfsat_gate = SulfSatGate()
        self._sulfsat_gate.initialize({})
        # Latest SulfurSaturationResult captured by either the Stage 0
        # hook (``_record_stage0_sulfsat_result``) or the post-equilibrium
        # hook in ``_get_equilibrium``. Available to the UI / diagnostics
        # without forcing a recompute. None until the first call.
        self._last_sulfur_saturation_result: SulfurSaturationResult | None = None
        self.setpoints = copy.deepcopy(setpoints)
        self.feedstocks = copy.deepcopy(feedstocks)
        self.vapor_pressures = copy.deepcopy(vapor_pressures)
        self._base_species_formula_registry = self._load_species_formula_registry()
        self.species_formula_registry = dict(self._base_species_formula_registry)
        self.atom_ledger = self._new_atom_ledger()

        # --- Chemistry-engine kernel ---
        # Per F-B6 (Cluster B cleanup): the kernel facade is built lazily
        # by :meth:`_seed_atom_ledger` (triggered from :meth:`load_batch`).
        # Constructing it at __init__ time against an empty ledger was a
        # wasted build whose shadow trace baseline did not match the one
        # the post-load_batch kernel started with (only _seed_atom_ledger
        # called ``clear_shadow_trace``).  The provider registry IS
        # lifetime-scoped and stays here -- it persists across batches
        # and is reused by every kernel rebuild.
        #
        # Any dispatch attempt before ``load_batch`` raises (see
        # :meth:`_require_chem_kernel`).
        self._chem_registry: ProviderRegistry = ProviderRegistry()
        self._chem_kernel: Optional[ChemistryKernel] = None
        # Goal #10 ``VAPOROCK-AUTHORITY-PROMOTION``: VapoRock is the
        # authoritative VAPOR_PRESSURE provider; the builtin Antoine
        # provider is registered as fallback.  The kernel only retries
        # the fallback when the user opted in via
        # ``setpoints['chemistry_kernel']['allow_fallback_vapor'] =
        # True`` -- the default is loud
        # :class:`ProviderUnavailableError` if VapoRock is missing.
        kernel_config = setpoints.get('chemistry_kernel', {}) or {}
        # Codex challenge pre-0.5.1 P2 (2026-05-27): a plain ``bool(...)``
        # coerces the strings ``"false"``, ``"no"``, ``"0"`` to True
        # (any non-empty string is truthy in Python). Setpoints come
        # from YAML which preserves intended bool types, but a
        # programmatic setpoints patch or a hand-typed
        # config can easily pass a string here -- silently opting the
        # run into fallback mode against operator intent. Explicit
        # string-aware coercion: known false-y strings are False; bool
        # values pass through; anything else falls back to ``bool(...)``
        # for backward compat with int/list/dict overrides.
        _fallback_raw = kernel_config.get('allow_fallback_vapor', False)
        if isinstance(_fallback_raw, str):
            self._allow_fallback_vapor: bool = (
                _fallback_raw.strip().lower()
                not in {'', 'false', '0', 'no', 'off', 'none'}
            )
        else:
            self._allow_fallback_vapor: bool = bool(_fallback_raw)
        # F-A4: counter for CONDENSATION_ROUTE (and other) dispatches
        # where the kernel returned ``transition is None``.  A replay
        # tool sees how many no-op dispatches happened without polluting
        # the planner's shadow trace.  Reset by ``_seed_atom_ledger``.
        self._chem_no_op_dispatch_count: int = 0
        # Parity flag from the VAPOR_PRESSURE flip in
        # \goal BUILTIN-ENGINE-EXTRACTION (#7). The shadow comparator
        # that validated builtin-vs-kernel parity across a full smoke run
        # was removed at flip time (the kernel IS the new authoritative
        # source — comparing against itself is moot). The flag stays True
        # as a permanent documentation marker that the flip has landed
        # and subsequent intent flips can rely on the kernel path here.
        self._kernel_vapor_pressure_parity: Dict[str, Any] = {
            'clean': True,
            'first_discrepancy': None,
        }
        self._overhead_headspace_config = (
            self._resolve_overhead_headspace_config()
        )
        self._freeze_gate_config = self._resolve_freeze_gate_config()
        self._freeze_gate_liquid_fraction_cache: Optional[Dict[str, Any]] = None
        self._last_freeze_gate_diagnostic: Dict[str, Any] = {}
        self._last_overhead_gas_equilibrium: Dict[str, Any] = {}
        self._last_vapor_pressure_diagnostic: Dict[str, Any] = {}
        self._last_evaporation_flux_diagnostic: Dict[str, Any] = {}
        self._last_extraction_completeness_diagnostic: Dict[str, Any] = {}
        self._pt0_determinism_store: Any | None = None
        self._last_reduced_real_cache_state: str | None = None
        self._rump_expectation_warnings: list[str] = []
        # Shuttle-physics-gate refusals: the post-V1c JANAF Ellingham +
        # S1b shuttle T-acceptance gate refuses K→FeO at any practical
        # melt T and Na→FeO above the 1173 °C crossover.  When the
        # engine returns ``status='refused'`` the extraction caller
        # records the structured diagnostic here so the recipe's
        # silent no-op cannot mask an invalid operating regime.
        # Autoreview r3 P2 (2026-05-27): the prior code treated any
        # transition-less kernel result the same -- benign skip and
        # thermodynamic refusal were indistinguishable.
        self._last_shuttle_refusal_diagnostic: Dict[str, Any] = {}
        self._shuttle_refusal_history: list[Dict[str, Any]] = []

        # --- Current state ---
        self.melt = MeltState()
        self.inventory = ProcessInventory()
        self.train = CondensationTrain.create_default()
        self.overhead = OverheadGas()

        # --- Batch record ---
        self.record = BatchRecord()
        self.energy_cumulative_kWh = 0.0
        self.oxygen_cumulative_kg = 0.0
        self._last_condensed_by_stage_species_delta: Dict[
            Tuple[int, str], float] = {}
        self._last_wall_deposit_by_segment_species_delta: Dict[
            Tuple[str, str], float] = {}
        self._last_impurity_delta: Dict[Tuple[int, str], float] = {}

        # --- Gas train feedback state ---
        self.O2_vented_cumulative_kg = 0.0      # Total O₂ vented to vacuum
        self.O2_stored_cumulative_kg = 0.0      # Total O₂ in accumulator
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
    # PT-0 reduced-real determinism proof hook
    # ------------------------------------------------------------------

    def configure_pt0_determinism_store(self, store: Any | None) -> None:
        self._pt0_determinism_store = store

    def _pt0_store(self) -> Any | None:
        return getattr(self, "_pt0_determinism_store", None)

    # ------------------------------------------------------------------
    # Lazy-loaded subsystem models
    # ------------------------------------------------------------------

    @property
    def condensation_model(self):
        if self._condensation_model is None:
            from simulator.condensation import CondensationModel
            # 0.5.4.1 review-cluster-C (P2 #1, 2026-05-28):
            # build the CondensationModel first, then apply the
            # setpoints YAML overrides ONTO THE INSTANCE rather
            # than the module-level fallback dict. This isolates
            # multi-tenant sims so a per-SID web session or a
            # per-run runner can use distinct setpoints without
            # cross-contamination. Replaces the prior
            # ``apply_setpoints_condensation_temperature_overrides``
            # module-mutation call.
            self._condensation_model = CondensationModel(
                self.train,
                vapor_pressure_data=self.vapor_pressures,
                wall_temperature_C=self.overhead_model.pipe_temperature_C,
            )
            self._condensation_model.apply_setpoints_overrides(
                self.setpoints
            )
        return self._condensation_model

    @property
    def overhead_model(self):
        if self._overhead_model is None:
            from simulator.overhead import OverheadGasModel
            self._overhead_model = OverheadGasModel(
                self._overhead_headspace_config)
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

        additives = dict(additives_kg or {})
        ledger_additives = dict(additives)
        self.species_formula_registry = self._registry_for_feedstock(fs)
        self.atom_ledger = self._new_atom_ledger()
        self.inventory = self._build_process_inventory(fs, mass_kg)
        self._stage0_carbon_cleanup_specs = []
        self._stage0_perchlorate_cleanup_specs = []
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
        self._apply_stage0_carbon_reductant_reactions(fs, ledger_additives)
        self._apply_stage0_perchlorate_reactions(fs)
        self._seed_atom_ledger(feedstock_key, fs, ledger_additives)
        self._project_cleaned_melt_from_atom_ledger()
        # NOTE: ``_seed_atom_ledger`` now rebuilds ``self._chem_kernel``
        # to point at the freshly created ledger (the STAGE0_PRETREATMENT
        # intent flip needs the kernel up BEFORE the Stage 0 cleanup
        # transitions are recorded -- so the rebuild moved into
        # ``_seed_atom_ledger`` directly after the ``_new_atom_ledger``
        # reset).  Provider registry persists; only the kernel facade
        # is rebuilt per batch.
        self._last_backend_error = ''
        self._last_backend_status = 'ok'
        self._backend_failed = False

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
        self.melt.fO2_log = self._compute_intrinsic_melt_fO2()
        self.melt.campaign = CampaignPhase.IDLE
        self.melt.hour = 0
        self.melt.campaign_hour = 0
        self.melt.update_total_mass()

        # Reset condensation train
        self.train = CondensationTrain.create_default()
        self.overhead = OverheadGas()
        self._last_overhead_gas_equilibrium = {}
        self._last_vapor_pressure_diagnostic = {}
        self._rump_expectation_warnings = []
        self._last_shuttle_refusal_diagnostic = {}
        self._shuttle_refusal_history = []
        self._equipment = None
        self._configure_overhead_headspace()
        self._configure_freeze_gate()

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
            expanded.pop('template_formula', None)
            or expanded.pop('template', None)
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

    def _backend_account_policies(self) -> tuple[AccountPolicy, ...]:
        provider = getattr(self.backend, 'ledger_account_policies', None)
        if not callable(provider):
            return ()
        return tuple(provider())

    def _new_atom_ledger(self) -> AtomLedger:
        return AtomLedger(
            registry=self.species_formula_registry,
            account_policies=self._backend_account_policies(),
        )

    # ------------------------------------------------------------------
    # F-B7: Table-driven builtin provider registration.
    #
    # Each row is ``(import_module, class_name, [intents],
    # needs_vapor_pressures, doc)``.  Mirrors the kernel-authoritative
    # builtin intents wired by ``\goal BUILTIN-ENGINE-EXTRACTION`` (#7);
    # the ordering matches the historical straight-line block so any
    # reviewer diff stays stable.  Adding a builtin engine = one new row
    # here; no changes to ``_build_chemistry_kernel`` itself.
    #
    # Goal #10 ``VAPOROCK-AUTHORITY-PROMOTION`` moved VAPOR_PRESSURE out
    # of this authoritative-only table: VapoRock is registered
    # authoritative separately, and the builtin Antoine provider is
    # registered as the fallback slot.  See
    # ``_register_vapor_pressure_pair`` for the wiring.  The six
    # remaining authoritative builtins (EVAPORATION_FLUX,
    # EVAPORATION_TRANSITION, CONDENSATION_ROUTE, ELECTROLYSIS_STEP,
    # METALLOTHERMIC_STEP, STAGE0_PRETREATMENT) are unchanged.
    #
    # ``needs_vapor_pressures`` is retained for forward-compatibility
    # in case a future authoritative builtin needs the same
    # simulator-owned data; today no row sets it.  Everything in this
    # table is stateless and consumes per-call inputs via control_inputs.
    # ------------------------------------------------------------------
    _BUILTIN_PROVIDER_REGISTRATIONS: tuple[
        tuple[str, str, tuple[ChemistryIntent, ...], bool, str], ...
    ] = (
        (
            'engines.builtin.evaporation_flux',
            'BuiltinEvaporationFluxProvider',
            (ChemistryIntent.EVAPORATION_FLUX,),
            False,
            'EVAPORATION_FLUX -- Hertz-Knudsen-Langmuir per-species '
            'flux (READ-ONLY).',
        ),
        (
            'engines.builtin.evaporation_transition',
            'BuiltinEvaporationTransitionProvider',
            (ChemistryIntent.EVAPORATION_TRANSITION,),
            False,
            'EVAPORATION_TRANSITION -- AUTHORITATIVE: debits '
            'cleaned_melt, credits overhead_gas + condensation_train.',
        ),
        (
            'engines.builtin.condensation_route',
            'BuiltinCondensationRouteProvider',
            (ChemistryIntent.CONDENSATION_ROUTE,),
            False,
            'CONDENSATION_ROUTE -- AUTHORITATIVE: debits overhead_gas, '
            'credits condensation_train with SiO disproportionation.',
        ),
        (
            'engines.builtin.electrolysis_step',
            'BuiltinElectrolysisStepProvider',
            (ChemistryIntent.ELECTROLYSIS_STEP,),
            False,
            'ELECTROLYSIS_STEP -- AUTHORITATIVE: MRE Nernst/Faraday; '
            'debits cleaned_melt, credits metal_phase + '
            'terminal.oxygen_mre_anode_stored.',
        ),
        (
            'engines.builtin.metallothermic_step',
            'BuiltinMetallothermicStepProvider',
            (ChemistryIntent.METALLOTHERMIC_STEP,),
            False,
            'METALLOTHERMIC_STEP -- AUTHORITATIVE: Na/K shuttle + Mg '
            'thermite (single intent, three reaction families).',
        ),
        (
            'engines.builtin.stage0_pretreatment',
            'BuiltinStage0PretreatmentProvider',
            (ChemistryIntent.STAGE0_PRETREATMENT,),
            False,
            'STAGE0_PRETREATMENT -- AUTHORITATIVE: volatile/salt/'
            'sulfide/halide cleanup (single intent, four reaction '
            'families).',
        ),
        (
            'engines.builtin.overhead_gas_equilibrium',
            'BuiltinOverheadGasEquilibriumProvider',
            (ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM,),
            False,
            'OVERHEAD_GAS_EQUILIBRIUM -- READ-ONLY: finite headspace '
            'partial pressures from process.overhead_gas.',
        ),
        (
            'engines.builtin.overhead_bleed',
            'BuiltinOverheadBleedProvider',
            (ChemistryIntent.OVERHEAD_BLEED,),
            False,
            'OVERHEAD_BLEED -- AUTHORITATIVE: pure-move routing from '
            'overhead_gas to terminal offgas / melt-offgas O2 bins.',
        ),
        (
            'engines.builtin.backend_equilibrium',
            'BuiltinBackendEquilibriumProvider',
            (ChemistryIntent.BACKEND_EQUILIBRIUM,),
            False,
            'BACKEND_EQUILIBRIUM -- AUTHORITATIVE: validates existing '
            'backend LedgerTransition objects before kernel commit.',
        ),
    )

    def _build_chemistry_kernel(self) -> ChemistryKernel:
        """Construct the kernel pointing at the current ledger + registry.

        Rebuilt on every ``load_batch`` because the simulator re-creates
        ``self.atom_ledger`` per batch (the species-formula registry can
        also change with feedstock). The provider registry persists for
        the lifetime of the simulator; only the kernel facade is rebuilt.

        F-B7 (Cluster B): replaces the prior 7-call straight-line
        ``register_idempotent`` block with the
        :data:`_BUILTIN_PROVIDER_REGISTRATIONS` table.  Same provider
        set, same registration order, same authority -- the table is
        the single source of truth so a new builtin engine adds one
        row instead of a new ``register_idempotent`` call.

        Each ``register_idempotent`` invocation is a no-op on subsequent
        batches once the simulator has registered the provider for the
        intent (the registry persists; only the kernel facade is rebuilt
        per batch).  Conflicting authoritative registrations for an
        intent still raise -- idempotence covers "same provider, same
        authority", never silent provider swaps.
        """

        # Lazy imports break the package-init cycle (see header comment
        # on the chemistry-kernel imports above): doing the imports
        # inside the loop keeps the registration table the single
        # source of truth for which builtins exist.
        import importlib

        for (
            module_path,
            class_name,
            intents,
            needs_vapor_pressures,
            _doc,
        ) in self._BUILTIN_PROVIDER_REGISTRATIONS:
            provider_cls = getattr(
                importlib.import_module(module_path), class_name)
            provider = (
                provider_cls(self.vapor_pressures)
                if needs_vapor_pressures
                else provider_cls()
            )
            self._chem_registry.register_idempotent(provider, list(intents))

        # Goal #10 ``VAPOROCK-AUTHORITY-PROMOTION``: VapoRock takes the
        # authoritative VAPOR_PRESSURE slot; the builtin Antoine provider
        # is demoted to the fallback slot.  The pair is wired separately
        # from the builtin-registration loop because the authority swap
        # involves two slots, two providers, and a config-driven fallback
        # opt-in that the loop's single ``register_idempotent`` call
        # cannot express.
        self._register_vapor_pressure_pair()

        # Register the AlphaMELTS diagnostic provider when the active
        # backend is an AlphaMELTSBackend (\goal ALPHAMELTS-DIAGNOSTIC-GATE,
        # #8). The provider wraps the today-hook adapter; if the user has
        # selected a different backend (Stub / FactSAGE / MAGEMin / VapoRock)
        # nothing is registered for SILICATE_LIQUIDUS / SILICATE_EQUILIBRIUM
        # and the kernel raises ProviderUnavailableError for those intents
        # -- the equilibrium call site at simulator/equilibrium.py guards
        # the dispatch on registry membership.
        self._register_alphamelts_provider_if_available()
        # Register the MAGEMin SHADOW provider for the same two intents
        # (\goal MAGEMIN-SHADOW-PARITY, #9).  Only registered when an
        # authoritative provider already exists for the intent -- the
        # registry's shadow slot is fine without authority, but a shadow
        # with no authoritative counterpart can never run (the planner
        # raises ProviderUnavailableError on dispatch), so we save the
        # work and avoid a misleading "shadow registered" surface in
        # the trace.
        self._register_magemin_shadow_if_available()

        # Goal #10 and FG4: thread per-intent fallback opt-ins into the
        # kernel. VAPOR_PRESSURE fallback stays config-gated; the freeze
        # gate's scalar liquid-fraction intent opts into its explicit
        # MAGEMin fallback slot because that intent exists only to answer
        # the gate's narrow liquid_fraction(T) question.
        allow_fallback = {ChemistryIntent.GATE_LIQUID_FRACTION}
        if self._allow_fallback_vapor:
            allow_fallback.add(ChemistryIntent.VAPOR_PRESSURE)
        allow_fallback_intents: frozenset[ChemistryIntent] = frozenset(
            allow_fallback
        )

        return ChemistryKernel(
            ledger=self.atom_ledger,
            registry=self._chem_registry,
            species_formula_registry=self.species_formula_registry,
            allow_fallback_intents=allow_fallback_intents,
        )

    def _register_vapor_pressure_pair(self) -> None:
        """Wire the authoritative + fallback VAPOR_PRESSURE pair.

        Goal #10 ``VAPOROCK-AUTHORITY-PROMOTION``: VapoRock becomes
        authoritative; the builtin Antoine/Ellingham provider is
        registered in the registry's fallback slot so the kernel can
        retry it when (a) VapoRock raises
        :class:`ProviderUnavailableError` AND (b) the simulator opted
        into fallback via ``allow_fallback_vapor`` in
        ``setpoints['chemistry_kernel']``.

        Both registrations are idempotent: ``_build_chemistry_kernel``
        is rebuilt per batch but the registry persists for the lifetime
        of the simulator, so the second batch's call is a no-op.  A
        conflicting registration (e.g. a different VapoRock build
        registered under the same provider_id) raises -- silent swaps
        are forbidden, matching the rest of the kernel posture.
        """

        from engines.builtin.vapor_pressure import (
            BuiltinVaporPressureProvider,
        )
        from engines.vaporock import VapoRockProvider

        # VapoRockProvider receives the same vapor_pressures.yaml
        # payload the builtin reads.  The provider uses the payload's
        # ``metals`` + ``oxide_vapors`` keys to filter its (richer)
        # output back onto the species universe the downstream
        # EVAPORATION_FLUX step has parent_oxide + Antoine metadata
        # for.  Without this filter VapoRock's ~30-species output
        # crashes the per-species stoichiometry validator and breaks
        # the mass-balance hard constraint.
        vaporock_provider = VapoRockProvider(
            vapor_pressure_data=self.vapor_pressures,
        )
        self._chem_registry.register_idempotent(
            vaporock_provider,
            [ChemistryIntent.VAPOR_PRESSURE],
        )

        builtin_provider = BuiltinVaporPressureProvider(self.vapor_pressures)
        self._chem_registry.register_idempotent(
            builtin_provider,
            [ChemistryIntent.VAPOR_PRESSURE],
            fallback=True,
        )

    def _register_alphamelts_provider_if_available(self) -> None:
        """Register AlphaMELTSProvider when the active backend supports it.

        \\goal ALPHAMELTS-DIAGNOSTIC-GATE (#8): AlphaMELTS is registered
        as the authoritative (diagnostic-only) provider for
        SILICATE_LIQUIDUS, SILICATE_EQUILIBRIUM, and
        EQUILIBRIUM_CRYSTALLIZATION. The provider wraps the live
        :class:`AlphaMELTSBackend` instance so the subprocess and
        PetThermoTools paths stay owned by the today-hook adapter (goal #1
        hardened it; this goal only adds the kernel envelope around it).
        The registration is conditional because:

        * Only one authoritative provider may exist per intent
          (:class:`ProviderRegistry` enforces this).
        * Users may select a different backend (Stub / FactSAGE / etc.);
          registering AlphaMELTS unconditionally would crash when the
          backend is not an AlphaMELTSBackend (the provider would have
          nowhere to delegate).

        The duck-typed check (presence of ``_mode``, ``is_available``,
        ``get_engine_version``) lets the registration accept future
        AlphaMELTS subclasses without a hard isinstance gate.
        """
        backend = self._provider_registration_backend(self.backend)
        if backend is None:
            return
        if not self._is_alphamelts_backend(backend):
            return
        from engines.alphamelts import AlphaMELTSProvider

        provider = AlphaMELTSProvider(backend=backend)
        self._chem_registry.register_idempotent(
            provider,
            [
                ChemistryIntent.SILICATE_LIQUIDUS,
                ChemistryIntent.SILICATE_EQUILIBRIUM,
                ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
            ],
        )

    @staticmethod
    def _provider_registration_backend(backend: Any) -> Any | None:
        if type(backend).__name__ == 'CachedRealBackend':
            return getattr(backend, '_live_backend', None)
        return backend

    @staticmethod
    def _is_alphamelts_backend(backend: Any) -> bool:
        """Duck-type check for the AlphaMELTSBackend class.

        Avoids a hard import on ``simulator.melt_backend.alphamelts`` at
        registration time (the adapter has optional dependencies). The
        check matches the class name on the instance's MRO; this is the
        same idiom used elsewhere in this module for VapoRock /
        FactSAGE backend detection.
        """
        backend = PyrolysisSimulator._provider_registration_backend(backend)
        if backend is None:
            return False
        for cls in type(backend).__mro__:
            if cls.__name__ == 'AlphaMELTSBackend':
                return True
        return False

    def _register_freeze_gate_liquid_fraction_providers(self) -> None:
        """Register providers for the freeze gate scalar intent on demand."""
        backend = self._provider_registration_backend(self.backend)
        if self._is_alphamelts_backend(backend):
            from engines.alphamelts import AlphaMELTSProvider

            self._chem_registry.register_idempotent(
                AlphaMELTSProvider(backend=backend),
                [ChemistryIntent.GATE_LIQUID_FRACTION],
            )
        from engines.magemin import MAGEMinShadowProvider

        provider = MAGEMinShadowProvider()
        self._chem_registry.register_idempotent(
            provider,
            [ChemistryIntent.GATE_LIQUID_FRACTION],
            fallback=True,
        )

    def _register_magemin_shadow_if_available(self) -> None:
        """Register MAGEMinShadowProvider as a kernel shadow when possible.

        \\goal MAGEMIN-SHADOW-PARITY (#9): MAGEMin runs as a shadow
        alongside the authoritative SILICATE_LIQUIDUS /
        SILICATE_EQUILIBRIUM provider (AlphaMELTS today). The shadow
        provider is constructed lazily -- it does not require a live
        MAGEMin binary at registration time, and returns
        ``status='unavailable'`` cleanly when the binary is absent.

        We only register the shadow when there is already an
        authoritative provider for those intents. A shadow without an
        authoritative counterpart is dead weight: the planner raises
        :class:`ProviderUnavailableError` before any shadow can run.
        Skipping the registration keeps the trace consumer's view of
        "is MAGEMin live in this batch?" honest.

        Goal-spec checklist 1 binds this -- ``engines/magemin/provider.py``
        registers as shadow provider for the two intents.
        """
        intents = (
            ChemistryIntent.SILICATE_LIQUIDUS,
            ChemistryIntent.SILICATE_EQUILIBRIUM,
        )
        # Only register when an authoritative provider already exists
        # (AlphaMELTS or any future SILICATE_LIQUIDUS owner).  Skip
        # otherwise: the shadow has no one to parity-check against.
        if not any(
            self._chem_registry.authoritative_for(intent) is not None
            for intent in intents
        ):
            return
        from engines.magemin import MAGEMinShadowProvider

        provider = MAGEMinShadowProvider()
        self._chem_registry.register_idempotent(
            provider,
            list(intents),
            shadow=True,
        )

    # ------------------------------------------------------------------
    # F-B1: Dispatch helpers
    #
    # The pre-cleanup code repeated the
    #   kernel_result = self._chem_kernel.dispatch(intent, T=..., P=...,
    #       control_inputs={...})
    #   proposal = kernel_result.transition
    #   if proposal is not None:
    #       self._chem_kernel.commit_batch(intent, proposal)
    # idiom at 9 ledger-mutating call sites across simulator/{core,
    # evaporation,extraction}.py, and the temperature/pressure pull
    # from ``self.melt`` was identical at every site.  Two helpers
    # collapse the boilerplate while preserving the pre/post bookkeeping
    # interleave that the MRE + thermite back-reduction need.
    #
    # ``_dispatch_and_commit`` covers the 9 simple sites; it dispatches
    # the intent, commits the proposal if one was returned, increments
    # the F-A4 no-op counter when the kernel skipped, and returns the
    # ``IntentResult`` so callers can read diagnostics.
    #
    # ``_dispatch_only`` + ``_commit_proposal`` are the split form: the
    # MRE site needs to capture per-account balances BEFORE the commit
    # (so the post-commit delta isolates THIS tick's transfer), and the
    # thermite back-reduction needs to gate the commit on a separate
    # condition.  Both call sites use the split helpers so the
    # bookkeeping can sit between them.
    #
    # Read-only intents (VAPOR_PRESSURE, EVAPORATION_FLUX) skip the
    # commit entirely; ``_dispatch_only`` handles them too -- the
    # helper is the single gateway into ``self._chem_kernel.dispatch``
    # from every simulator call site.
    # ------------------------------------------------------------------
    def _require_chem_kernel(self) -> ChemistryKernel:
        """Return the active kernel; raise if no batch has been loaded.

        F-B6 (Cluster B): ``__init__`` no longer pre-builds a kernel
        against an empty ledger; ``load_batch -> _seed_atom_ledger``
        is the single construction point.  Catching the pre-load
        dispatch path here turns the failure into a clear
        ``RuntimeError`` instead of an ``AttributeError`` on ``None``.
        """
        kernel = self._chem_kernel
        if kernel is None:
            raise RuntimeError(
                'ChemistryKernel has not been built yet; '
                'PyrolysisSimulator.load_batch must run before any '
                'kernel dispatch (the kernel is constructed lazily by '
                '_seed_atom_ledger, which load_batch invokes).'
            )
        return kernel

    def _dispatch_only(
        self,
        intent: ChemistryIntent,
        *,
        control_inputs: Mapping[str, Any],
        fO2_log: Optional[float] = None,
        fe_redox_policy: str = 'intrinsic',
    ) -> IntentResult:
        """Dispatch one intent through the kernel with melt-derived controls.

        Pulls ``temperature_C`` and ``pressure_bar`` from ``self.melt``
        so every site quotes the same source-of-truth state (no
        per-site re-derivation drift).  Returns the ``IntentResult``
        unchanged -- including ``transition``, ``diagnostic``,
        ``warnings``.

        F-B1: this is the dispatch half of the pre/post-interleave
        split.  Use :meth:`_dispatch_and_commit` instead when the site
        does NOT need bookkeeping between dispatch and commit.
        """
        kernel = self._require_chem_kernel()
        temperature_C = float(self.melt.temperature_C)
        pressure_bar = float(self.melt.p_total_mbar) / 1000.0
        store = _pt0_determinism_store_for(self)
        account_mol_overrides = None
        if store is not None and getattr(store, 'quantize_live_controls', False):
            controls = store.quantized_controls(self, fO2_log=fO2_log)
            temperature_C = float(controls['temperature_C'])
            pressure_bar = float(controls['pressure_bar'])
            fO2_log = controls['fO2_log']
            canonicalizer = getattr(
                store,
                'canonical_composition_mol_by_account',
                None,
            )
            if not callable(canonicalizer):
                raise RuntimeError(
                    'PT-0 live-control quantization requires composition '
                    'canonicalization support'
                )
            account_mol_overrides = canonicalizer(self)
        return kernel.dispatch(
            intent,
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            fe_redox_policy=fe_redox_policy,
            control_inputs=control_inputs,
            account_mol_overrides=account_mol_overrides,
        )

    def _commit_proposal(
        self,
        intent: ChemistryIntent,
        proposal: LedgerTransitionProposal,
    ) -> None:
        """Commit a proposal returned by :meth:`_dispatch_only`.

        F-B1: the commit half of the pre/post-interleave split.  The
        kernel re-runs the full pre-commit validator stack inside
        ``commit_batch`` (defence in depth) so this helper stays a
        thin pass-through.
        """
        kernel = self._require_chem_kernel()
        kernel.commit_batch(intent, proposal)

    def _dispatch_and_commit(
        self,
        intent: ChemistryIntent,
        *,
        control_inputs: Mapping[str, Any],
    ) -> IntentResult:
        """Dispatch + (if a proposal is returned) commit, in one call.

        F-B1: the simple-site helper covering the 9 dispatch+commit
        callers.  If the provider returns ``transition is None`` the
        per-batch ``_chem_no_op_dispatch_count`` is bumped (F-A4) so a
        replay tool can distinguish "kernel skipped" from "called and
        no-op".  The caller still owns post-commit projection /
        snapshot bookkeeping; the helper only handles the kernel-facing
        plumbing.
        """
        result = self._dispatch_only(intent, control_inputs=control_inputs)
        proposal = result.transition
        if proposal is None:
            # F-A4: counter ticks once per "called and no-op" dispatch.
            # Cheap (single int increment); the planner's shadow trace
            # stays untouched so existing trace replay tools keep their
            # current invariants.
            self._chem_no_op_dispatch_count += 1
            return result
        self._commit_proposal(intent, proposal)
        return result

    def _resolve_overhead_headspace_config(
        self, campaign: Optional[CampaignPhase] = None
    ) -> Dict[str, Any]:
        config = dict(DEFAULT_OVERHEAD_HEADSPACE_CONFIG)
        config.update(dict(self.setpoints.get('overhead_headspace', {}) or {}))
        campaign_key = campaign.name if campaign is not None else None
        if campaign_key:
            campaign_cfg = (
                self.setpoints.get('campaigns', {}) or {}
            ).get(campaign_key, {}) or {}
            config.update(dict(campaign_cfg.get('overhead_headspace', {}) or {}))
            runtime_override = self.campaign_mgr.overrides.get(campaign_key, {})
            config.update(
                dict(runtime_override.get('overhead_headspace', {}) or {})
            )
        return config

    def _configure_overhead_headspace(
        self, campaign: Optional[CampaignPhase] = None
    ) -> None:
        self._overhead_headspace_config = (
            self._resolve_overhead_headspace_config(campaign)
        )
        if self._overhead_model is not None:
            self._overhead_model.configure_headspace(
                self._overhead_headspace_config)

    def _overhead_headspace_enabled(self) -> bool:
        return bool(self._overhead_headspace_config.get('enabled', False))

    def _resolve_freeze_gate_config(
        self, campaign: Optional[CampaignPhase] = None
    ) -> Dict[str, Any]:
        config = dict(DEFAULT_FREEZE_GATE_CONFIG)
        config.update(dict(self.setpoints.get('freeze_gate', {}) or {}))
        campaign_key = campaign.name if campaign is not None else None
        if campaign_key:
            campaign_cfg = (
                self.setpoints.get('campaigns', {}) or {}
            ).get(campaign_key, {}) or {}
            config.update(dict(campaign_cfg.get('freeze_gate', {}) or {}))
            runtime_override = self.campaign_mgr.overrides.get(campaign_key, {})
            config.update(dict(runtime_override.get('freeze_gate', {}) or {}))
        return config

    def _configure_freeze_gate(
        self, campaign: Optional[CampaignPhase] = None
    ) -> None:
        self._freeze_gate_config = self._resolve_freeze_gate_config(campaign)
        self._freeze_gate_liquid_fraction_cache = None
        self._last_freeze_gate_diagnostic = {}

    def _freeze_gate_enabled(self) -> bool:
        return bool(self._freeze_gate_config.get('enabled', False))

    def _headspace_volume_m3(self) -> float:
        configured = self._overhead_headspace_config.get('volume_m3')
        if configured is not None:
            return max(0.0, float(configured))
        equipment = self._equipment
        if equipment is not None:
            return max(0.0, float(getattr(equipment, 'headspace_volume_m3', 0.0)))
        return 0.085

    def _headspace_temperature_K(self) -> float:
        melt_T_K = float(self.melt.temperature_C) + 273.15
        offset = self._overhead_headspace_config.get('temperature_offset_K')
        if offset is not None:
            return max(1.0, melt_T_K + float(offset))
        model = str(
            self._overhead_headspace_config.get('temperature_model') or 'melt'
        )
        if model == 'lumped':
            return max(1.0, melt_T_K - 100.0)
        return max(1.0, melt_T_K)

    def _configure_condensation_operating_conditions(
        self,
        evap_flux: EvaporationFlux,
    ) -> None:
        transport = self.overhead_model.estimate_transport_state(
            evap_flux,
            self.melt,
        )
        self.condensation_model.configure_operating_conditions(
            wall_temperature_C=transport['pipe_temperature_C'],
            overhead_pressure_mbar=transport['pressure_mbar'],
            pipe_diameter_m=self.overhead_model.pipe_diameter_m,
            gas_temperature_C=transport['pipe_temperature_C'],
            pipe_segment_temperatures_C=(
                self.overhead_model.resolve_pipe_segment_temperatures_C(
                    [
                        segment.name
                        for segment in self.condensation_model.pipe_segments
                    ],
                    self.melt,
                )
            ),
            # 0.5.2 Phase B (series-resistance + stir-Sherwood): the
            # induction-stirring knob lives on the melt state, set by
            # campaign overrides (``simulator/campaigns.py:152``) and
            # defaults to ``6.0`` for the standard 4-8× range. Pass it
            # through so the boundary-layer flux uses the enhanced
            # Sherwood rather than the laminar 3.66 asymptote. Route
            # via ``clamp_stir_factor`` for symmetry with the operator-
            # boundary writers in campaigns.py / session.py and so a
            # ``None`` field (corrupt-state recovery, partially-built
            # melt) maps to the fail-closed default rather than raising
            # ``TypeError`` on ``float(None)``. Codex /code-review
            # max-effort, Phase B.
            #
            # 0.5.3 Phase B (2-axis stirring): split the single scalar
            # ``stir_factor`` into ``stir_state.axial`` (evap H-K-L
            # multiplier) + ``stir_state.radial`` (Sh enhancement).
            # The legacy ``stir_factor=`` kwarg on condensation carries
            # the axial value for audit-history continuity (the legacy
            # field is preserved on ``CondensationModel`` so a
            # downstream auditor can compare requested vs applied per
            # axis); ``radial_stir_factor=`` carries the canonical Sh
            # driver. Both run through ``clamp_stir_factor`` per-axis
            # — see ``simulator/state.py::clamp_stir_state`` for the
            # 2-axis defensive contract.
            stir_factor=clamp_stir_factor(
                getattr(
                    getattr(self.melt, 'stir_state', None),
                    'axial',
                    None,
                )
            ),
            radial_stir_factor=clamp_stir_factor(
                getattr(
                    getattr(self.melt, 'stir_state', None),
                    'radial',
                    None,
                )
            ),
            campaign_name=getattr(self.melt.campaign, 'name', ''),
            campaign_hour=float(self.melt.campaign_hour),
        )

    def _headspace_downstream_pressure_bar(self) -> float:
        configured = self._overhead_headspace_config.get(
            'downstream_pressure_bar')
        if configured is not None:
            return max(0.0, float(configured))
        atmosphere_name = getattr(self.melt.atmosphere, 'name', '')
        if atmosphere_name in {
            'CONTROLLED_O2',
            'CONTROLLED_O2_FLOW',
            'O2_BACKPRESSURE',
        }:
            return max(0.0, float(self.melt.pO2_mbar) / 1000.0)
        return 0.0

    def _headspace_bleed_conductance_kg_s_per_bar(self) -> float:
        configured = self._overhead_headspace_config.get(
            'conductance_kg_s_per_bar')
        if configured is not None:
            return max(0.0, float(configured))
        p_mean_Pa = max(float(self.melt.p_total_mbar) * 100.0, 1.0)
        return max(
            0.0,
            float(self.overhead_model._pipe_conductance(
                p_mean_Pa, self.melt.temperature_C)),
        )

    def _overhead_holdup_mol(self) -> Dict[str, float]:
        return {
            species: float(mol)
            for species, mol in self.atom_ledger.mol_by_account(
                'process.overhead_gas').items()
            if float(mol) > 0.0
        }

    def _overhead_gas_equilibrium_diagnostic(self) -> Dict[str, Any]:
        if self._chem_kernel is None:
            return {}
        result = self._dispatch_only(
            ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM,
            control_inputs={
                'headspace_volume_m3': self._headspace_volume_m3(),
                'headspace_temperature_K': self._headspace_temperature_K(),
            },
        )
        diagnostic = dict(result.diagnostic or {})
        self._last_overhead_gas_equilibrium = diagnostic
        return diagnostic

    def _dispatch_overhead_bleed(
        self,
        *,
        turbine_spec=None,
        force_drain_all: bool = False,
        o2_vented_kg: Optional[float] = None,
    ) -> IntentResult:
        diagnostic = self._overhead_gas_equilibrium_diagnostic()
        controls: Dict[str, Any] = {
            'headspace_volume_m3': self._headspace_volume_m3(),
            'headspace_temperature_K': self._headspace_temperature_K(),
            'bleed_conductance_kg_s_per_bar': (
                self._headspace_bleed_conductance_kg_s_per_bar()
            ),
            'p_total_bar': float(diagnostic.get('p_total_bar', 0.0) or 0.0),
            'p_downstream_bar': self._headspace_downstream_pressure_bar(),
            'dt_hr': 1.0,
            'force_drain_all': bool(force_drain_all),
            'max_o2_flow_kg_hr': float(
                getattr(turbine_spec, 'max_O2_flow_kg_hr', 0.0) or 0.0
            ),
        }
        if o2_vented_kg is not None:
            controls['o2_vented_kg'] = max(0.0, float(o2_vented_kg))
        result = self._dispatch_and_commit(
            ChemistryIntent.OVERHEAD_BLEED,
            control_inputs=controls,
        )
        if (result.diagnostic or {}).get('bled_o2_mol', 0.0) > 0.0:
            for stage in self.train.stages:
                stage.collected_kg.pop(OXYGEN_SPECIES, None)
        self._sync_oxygen_kg_counters()
        return result

    def _compute_intrinsic_melt_fO2(
        self, temperature_K: Optional[float] = None
    ) -> float:
        T_K = (
            float(temperature_K)
            if temperature_K is not None
            else float(self.melt.temperature_C) + 273.15
        )
        if T_K <= 0.0:
            return -9.0
        comp = self._melt_oxide_wt_pct()
        feo = max(0.0, float(comp.get('FeO', 0.0)))
        fe2o3 = max(0.0, float(comp.get('Fe2O3', 0.0)))
        alkali = max(0.0, float(comp.get('Na2O', 0.0))) + max(
            0.0, float(comp.get('K2O', 0.0)))
        # IW buffer fit: anchored at log10(fO2/bar) ~= -7.98 at 1873 K,
        # matching the Phase 1 contract's Kress91 basalt reference. The
        # composition term is intentionally small until an explicit Fe3+/Fe2+
        # policy lands.
        log_iw = -27215.0 / T_K + 6.57
        redox_offset = 0.0
        if feo > 0.0 and fe2o3 > 0.0:
            redox_offset += 0.25 * math.log10(max(fe2o3 / feo, 1.0e-12))
        redox_offset += min(0.15, alkali * 0.01)
        return max(-9.0, min(0.0, log_iw + redox_offset))

    def _seed_atom_ledger(
        self,
        feedstock_key: str,
        feedstock: Mapping[str, Any],
        additives_kg: Mapping[str, float],
    ) -> None:
        """Seed atom ledger from current kg inventory projections.

        The STAGE0_PRETREATMENT intent flip routes the Stage 0 cleanup
        transitions through ``self._chem_kernel`` -- so the kernel
        facade MUST be rebuilt to point at the freshly created ledger
        BEFORE ``_record_stage0_*_transitions`` runs.  Build the kernel
        immediately after the ledger reset; ``load_batch`` no longer
        rebuilds it after :meth:`_seed_atom_ledger` returns.
        """
        self.atom_ledger = self._new_atom_ledger()
        self._chem_kernel = self._build_chemistry_kernel()
        # Per-batch diagnostic state must start clean -- the planner's
        # shadow trace would otherwise accumulate without bound across
        # campaigns / loop sessions.
        self._chem_kernel.clear_shadow_trace()
        # F-A4: the per-batch no-op dispatch counter mirrors the shadow
        # trace lifetime -- a fresh batch starts from zero.
        self._chem_no_op_dispatch_count = 0
        label = str(feedstock.get('label', feedstock_key))
        oxidation_specs, oxidized_offgas_kg = (
            self._stage0_oxidation_transition_specs(feedstock))
        carbon_specs = list(self._stage0_carbon_cleanup_specs)
        carbon_offgas_kg: Dict[str, float] = {}
        for spec in carbon_specs:
            self._merge_masses(carbon_offgas_kg, spec['products_kg'])
        perchlorate_specs = list(self._stage0_perchlorate_cleanup_specs)
        perchlorate_salt_kg: Dict[str, float] = {}
        for spec in perchlorate_specs:
            self._merge_masses(perchlorate_salt_kg, spec['salt_products_kg'])
        generated_offgas_kg = dict(oxidized_offgas_kg)
        self._merge_masses(generated_offgas_kg, carbon_offgas_kg)
        terminal_offgas_external = self._subtract_species_kg(
            self.inventory.gas_volatiles_kg,
            generated_offgas_kg,
            context=f'{label} Stage 0 oxidation products',
        )
        terminal_salt_external = self._subtract_species_kg(
            self.inventory.salt_phase_kg,
            perchlorate_salt_kg,
            context=f'{label} Stage 0 salt products',
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
            terminal_salt_external,
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
        self._record_stage0_carbon_cleanup_transitions(label, carbon_specs)
        self._record_stage0_perchlorate_cleanup_transitions(
            label, perchlorate_specs)
        # SULFUR_SATURATION_GATE — Stage 0 hook. Refines the sulfate /
        # sulfide partitioning diagnostic when PySulfSat is available;
        # otherwise records an 'unavailable' result so the builtin Stage
        # 0 bucketing remains authoritative.
        self._run_stage0_sulfsat_gate()

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
        """Kernel-route the Stage 0 complete-oxidation transitions.

        STAGE0_PRETREATMENT intent -- kernel-authoritative since
        ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) seventh flip.  The
        :class:`BuiltinStage0PretreatmentProvider` mirrors the legacy
        ``complete_oxidation`` stoichiometry line-for-line and emits
        one :class:`LedgerTransitionProposal` per spec entry
        (per-species) debiting ``process.stage0_volatile_feed`` (and
        ``reservoir.stage0_oxidant`` when O2-deficient) and crediting
        ``terminal.offgas`` (CO2/H2O/N2) + ``terminal.oxygen_stage0_stored``
        (O2 coproduct when the feed is O2-surplus, e.g. peroxides).
        :meth:`ChemistryKernel.commit_batch` is the sole writable path
        into the ledger for this intent after the flip; the legacy
        ``self.atom_ledger.record`` direct mutation is gone.

        The ``load_external`` calls are kept here -- they bring source
        mass IN to the process accounts from the feedstock inventory
        (legacy seeding semantics), which is distinct from the
        chemistry-transition payload the kernel commits.
        """
        from engines.builtin.stage0_pretreatment import (
            REACTION_FAMILY_COMPLETE_OXIDATION,
        )

        feed_account = 'process.stage0_volatile_feed'
        oxidant_account = 'reservoir.stage0_oxidant'
        for spec in specs:
            species = str(spec['species'])
            feed_kg = float(spec['feed_kg'])
            products_kg = dict(spec['products_kg'])
            oxidant_kg = float(spec['oxidant_kg'])

            # Seed the feed + oxidant accounts (legacy
            # load_external semantics; the proposal layer expects the
            # accounts to hold material before it debits them).
            self.atom_ledger.load_external(
                feed_account,
                {species: feed_kg},
                source=f'{label} Stage 0 {species} feed',
            )
            if oxidant_kg > 1e-12:
                self.atom_ledger.load_external(
                    oxidant_account,
                    {'O2': oxidant_kg},
                    source=f'{label} Stage 0 controlled O2 oxidant',
                )

            # F-B1: dispatch + commit through the shared helper.  The
            # kernel's commit_batch path is still the ONLY writable
            # entry into the AtomLedger for STAGE0_PRETREATMENT.
            self._dispatch_and_commit(
                ChemistryIntent.STAGE0_PRETREATMENT,
                control_inputs={
                    'reaction_family': REACTION_FAMILY_COMPLETE_OXIDATION,
                    'species': species,
                    'feed_kg': feed_kg,
                    'products_kg': products_kg,
                    'oxidant_kg': oxidant_kg,
                },
            )

    def _record_stage0_carbon_cleanup_transitions(
        self,
        label: str,
        specs: list[dict],
    ) -> None:
        """Kernel-route Stage 0 carbon cleanup transitions.

        STAGE0_PRETREATMENT intent -- kernel-authoritative since
        ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) seventh flip.  Each
        spec is dispatched as either ``sulfate_carbon`` (SO3 + C ->
        SO2 + CO) or ``boudouard`` (C + CO2 -> 2 CO) family based on
        the legacy spec name; the provider emits a
        :class:`LedgerTransitionProposal` debiting the spec's feed
        accounts and crediting ``terminal.offgas`` with the reactions'
        products.  :meth:`ChemistryKernel.commit_batch` is the sole
        writable path into the ledger for this intent after the flip;
        the legacy ``self.atom_ledger.record`` direct mutation is gone.

        The ``load_external`` calls are kept here for legacy seeding
        semantics (process.stage0_salt_feed,
        process.stage0_carbon_reductant, reservoir.stage0_process_gas)
        -- bringing source mass IN from the feedstock inventory is
        distinct from the chemistry-transition payload the kernel
        commits.
        """
        from engines.builtin.stage0_pretreatment import (
            REACTION_FAMILY_BOUDOUARD,
            REACTION_FAMILY_SULFATE_CARBON,
        )

        # Map legacy spec names to provider reaction-family
        # discriminators.  The provider rejects anything outside
        # VALID_REACTION_FAMILIES as ``unsupported``; the map below
        # mirrors the two carbon-cleanup reaction IDs the legacy
        # ``_stage0_carbon_cleanup_reaction_ids`` validates exactly.
        SPEC_FAMILY = {
            'stage0_sulfate_carbon_cleanup': REACTION_FAMILY_SULFATE_CARBON,
            'stage0_boudouard_carbon_cleanup': REACTION_FAMILY_BOUDOUARD,
        }

        for spec in specs:
            name = str(spec.get('name') or '')
            family = SPEC_FAMILY.get(name)
            if family is None:
                raise AccountingError(
                    f'unsupported Stage 0 carbon cleanup spec {name!r}'
                )
            # Seed source accounts (legacy load_external semantics).
            debits_payload: list[tuple[str, dict[str, float]]] = []
            for account, species_kg in spec['debits']:
                payload = self._ledger_species_kg(species_kg)
                if not payload:
                    continue
                self.atom_ledger.load_external(
                    account,
                    payload,
                    source=f"{label} {name} feed",
                )
                debits_payload.append((str(account), dict(payload)))
            if not debits_payload:
                continue

            # F-B1: dispatch + commit through the shared helper.
            self._dispatch_and_commit(
                ChemistryIntent.STAGE0_PRETREATMENT,
                control_inputs={
                    'reaction_family': family,
                    'debits': tuple(debits_payload),
                    'products_kg': dict(spec.get('products_kg') or {}),
                },
            )

    def _record_stage0_perchlorate_cleanup_transitions(
        self,
        label: str,
        specs: list[dict],
    ) -> None:
        """Kernel-route Stage 0 perchlorate cleanup transitions.

        STAGE0_PRETREATMENT intent -- kernel-authoritative since
        ``\\goal BUILTIN-ENGINE-EXTRACTION`` (#7) seventh flip.  Each
        spec is dispatched as ``perchlorate`` family (ClO4 -> Cl +
        2 O2) -- the provider emits a :class:`LedgerTransitionProposal`
        debiting ``process.stage0_perchlorate_feed`` (ClO4) and
        crediting ``terminal.stage0_salt_phase`` (Cl) + ``terminal.
        oxygen_stage0_stored`` (O2).
        :meth:`ChemistryKernel.commit_batch` is the sole writable path
        into the ledger for this intent after the flip; the legacy
        ``self.atom_ledger.record`` direct mutation is gone.

        The ``load_external`` call is kept here for legacy seeding
        semantics (process.stage0_perchlorate_feed) -- bringing source
        mass IN from the salt-phase inventory is distinct from the
        chemistry-transition payload the kernel commits.
        """
        from engines.builtin.stage0_pretreatment import (
            REACTION_FAMILY_PERCHLORATE,
        )

        for spec in specs:
            debits_payload: list[tuple[str, dict[str, float]]] = []
            for account, species_kg in spec['debits']:
                payload = self._ledger_species_kg(species_kg)
                if not payload:
                    continue
                self.atom_ledger.load_external(
                    account,
                    payload,
                    source=f"{label} {spec['name']} feed",
                )
                debits_payload.append((str(account), dict(payload)))
            if not debits_payload:
                continue

            # F-B1: dispatch + commit through the shared helper.
            self._dispatch_and_commit(
                ChemistryIntent.STAGE0_PRETREATMENT,
                control_inputs={
                    'reaction_family': REACTION_FAMILY_PERCHLORATE,
                    'debits': tuple(debits_payload),
                    'salt_products_kg': dict(
                        spec.get('salt_products_kg') or {}),
                    'oxygen_products_kg': dict(
                        spec.get('oxygen_products_kg') or {}),
                },
            )

    # ------------------------------------------------------------------
    # SULFUR_SATURATION_GATE — PySulfSat hook
    # ------------------------------------------------------------------

    def _stage0_sulfur_input_ppm(self) -> float:
        """
        Estimate the total-S concentration (ppm by cleaned-melt mass)
        carried in the Stage 0 sulfate / sulfide inventory.

        ``inventory.salt_phase_kg`` may carry SO3 / SO4 from carbonaceous
        feedstocks; ``inventory.sulfide_matte_kg`` carries FeS / oldhamite
        / elemental S. Both are folded onto an equivalent S mass and
        divided by the cleaned-melt mass to give a per-million-of-melt
        concentration the gate can use as ``S_input_ppm``. Returns 0 when
        Stage 0 produced no sulfur-bearing inventory.
        """
        melt_total_kg = sum(self.inventory.melt_oxide_kg.values())
        if melt_total_kg <= 0.0:
            return 0.0
        sulfur_kg = 0.0
        for source in (
            self.inventory.salt_phase_kg,
            self.inventory.sulfide_matte_kg,
        ):
            for species, kg in source.items():
                if kg is None or float(kg) <= 0.0:
                    continue
                fraction = _SULFUR_FRACTION_BY_CARRIER.get(
                    str(species),
                    None,
                )
                if fraction is None:
                    key = str(species).lower()
                    # Best-effort fallback: any *S-bearing* component
                    # name (sulfate / sulfide variants) contributes its
                    # full mass as the upper bound on the S input — the
                    # gate clamps against SCSS/SCAS afterwards.
                    if 'so3' in key or 'so4' in key or 'sulfate' in key:
                        fraction = _SULFUR_FRACTION_BY_CARRIER['SO3']
                    elif 'sulfide' in key or 'fes' in key or 'cas' in key:
                        fraction = _SULFUR_FRACTION_BY_CARRIER['FeS']
                    elif key == 's' or key.startswith('s_'):
                        fraction = 1.0
                    else:
                        continue
                sulfur_kg += float(kg) * fraction
        if sulfur_kg <= 0.0:
            return 0.0
        return (sulfur_kg / melt_total_kg) * 1.0e6

    def _melt_oxide_wt_pct(self) -> Dict[str, float]:
        """Return cleaned-melt oxide composition in wt% (zero-total safe)."""
        total = sum(self.inventory.melt_oxide_kg.values())
        if total <= 0.0:
            return {}
        return {
            species: (kg / total) * 100.0
            for species, kg in self.inventory.melt_oxide_kg.items()
            if kg > 0.0
        }

    def _run_stage0_sulfsat_gate(self) -> None:
        """
        Run the SULFUR_SATURATION_GATE at the end of Stage 0.

        Records the result on ``self._last_sulfur_saturation_result`` so
        the UI / diagnostics can read the SCSS / SCAS / S6+ partitioning
        without re-running the gate. The result never mutates the atom
        ledger; Stage 0 keeps its builtin sulfate / sulfide bucketing
        authoritative. When the gate reports ``out_of_range`` the
        warning is preserved on the result; the caller is expected to
        log it but not redirect inventory.
        """
        s_input_ppm = self._stage0_sulfur_input_ppm()
        if s_input_ppm <= 0.0:
            self._last_sulfur_saturation_result = None
            return
        comp_wt = self._melt_oxide_wt_pct()
        if not comp_wt:
            self._last_sulfur_saturation_result = None
            return
        # The Stage 0 reload pinpoints the melt at room temperature; the
        # SCSS / SCAS empirical fits are calibrated above ~1000 K. Using
        # a representative liquidus temperature (1473 K) for the Stage 0
        # diagnostic keeps the gate output meaningful before the melt
        # has been heated, without claiming a temperature it has not
        # reached. Post-equilibrium calls override this with the actual
        # melt T.
        T_K = 1473.0
        P_bar = max(self.melt.p_total_mbar / 1000.0, 1.0e-6)
        fO2_log = self._compute_intrinsic_melt_fO2(T_K)
        self.melt.fO2_log = fO2_log
        self._last_sulfur_saturation_result = (
            self._sulfsat_gate.compute_sulfur_saturation(
                liquid_comp_wt=comp_wt,
                T_K=T_K,
                P_bar=P_bar,
                fO2_log=fO2_log,
                S_input_ppm=s_input_ppm,
            )
        )

    def _attach_post_equilibrium_sulfsat(
        self, result: 'Any'
    ) -> None:
        """
        Post-equilibrium SULFUR_SATURATION_GATE hook.

        Called from ``_get_equilibrium`` after a successful backend
        equilibration. If Stage 0 sulfide / sulfate inventory is
        non-zero, calls the gate at the melt's current T / P / fO2 and
        attaches the result to ``result.sulfur_saturation`` (and
        ``self._last_sulfur_saturation_result``). When the gate reports
        ``out_of_range`` or ``unavailable`` the warning is appended to
        ``result.warnings`` so existing diagnostic surfaces (UI,
        telemetry) pick it up without a schema change. The atom ledger
        is never mutated here — the gate has no ledger authority
        (binding spec §4 forbids it).
        """
        s_input_ppm = self._stage0_sulfur_input_ppm()
        if s_input_ppm <= 0.0:
            return
        comp_wt = self._melt_oxide_wt_pct()
        if not comp_wt:
            return
        T_K = float(self.melt.temperature_C) + 273.15
        if T_K <= 0.0:
            return
        P_bar = max(self.melt.p_total_mbar / 1000.0, 1.0e-6)
        fO2_log = self._compute_intrinsic_melt_fO2(T_K)
        self.melt.fO2_log = fO2_log
        sulfur_result = self._sulfsat_gate.compute_sulfur_saturation(
            liquid_comp_wt=comp_wt,
            T_K=T_K,
            P_bar=P_bar,
            fO2_log=fO2_log,
            S_input_ppm=s_input_ppm,
        )
        self._last_sulfur_saturation_result = sulfur_result
        try:
            result.sulfur_saturation = sulfur_result
        except AttributeError:
            # Older EquilibriumResult variants (test fakes) may not have
            # the field; the gate's diagnostic still lives on the
            # simulator and the test-suite mocks can pick it up there.
            pass
        if sulfur_result.calibration_status != 'in_range':
            warnings_list = getattr(result, 'warnings', None)
            if isinstance(warnings_list, list):
                for note in sulfur_result.warnings:
                    warnings_list.append(
                        f'SulfSat gate ({sulfur_result.calibration_status}): '
                        f'{note}'
                    )

    def _project_cleaned_melt_from_atom_ledger(self) -> None:
        ledger_melt = self.atom_ledger.kg_by_account('process.cleaned_melt')
        # Project the *full* cleaned_melt account, not just OXIDE_SPECIES: a
        # FactSAGE LIQUID/SOLID phase can legitimately carry non-oxide species
        # (dissolved metallic Fe/Si, a solid mineral). Truncating to oxides
        # would drop that mass and the MeltState projection would no longer
        # mass-close against the ledger account.
        self.melt.composition_kg = {
            species: float(kg)
            for species, kg in ledger_melt.items()
        }
        # melt_oxide_kg keeps its oxide-only contract (Stage 0 reload path,
        # feedstock inventory snapshots); the non-oxide remainder stays in
        # melt.composition_kg / total_mass_kg only.
        self.inventory.melt_oxide_kg = {
            oxide: float(ledger_melt.get(oxide, 0.0))
            for oxide in OXIDE_SPECIES
        }
        self.melt.update_total_mass()

    def _backend_composition_kg(self) -> Dict[str, float]:
        ledger_melt = self.atom_ledger.kg_by_account('process.cleaned_melt')
        return {
            species: kg
            for species, raw_kg in ledger_melt.items()
            if (kg := float(raw_kg)) > 1e-12
        }

    def _backend_composition_mol(self) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for species_mol in self._backend_composition_mol_by_account().values():
            for species, mol in species_mol.items():
                totals[species] = totals.get(species, 0.0) + mol
        return {
            species: mol
            for species, mol in totals.items()
            if mol > 0.0
        }

    def _backend_composition_mol_by_account(
        self,
    ) -> Dict[str, Dict[str, float]]:
        composition: Dict[str, Dict[str, float]] = {}
        for account in BACKEND_REACTIVE_ACCOUNTS:
            species_mol = {
                species: mol
                for species, raw_mol in self.atom_ledger.mol_by_account(
                    account).items()
                if (mol := float(raw_mol)) > 0.0
            }
            if species_mol:
                composition[account] = species_mol
        return composition

    def _validate_backend_account_scope_support(
        self,
        composition_by_account: Mapping[str, Mapping[str, float]],
    ) -> None:
        if self._backend_accepts_kwarg('composition_mol_by_account'):
            return
        unsupported = {
            account: sorted(species_mol)
            for account, species_mol in composition_by_account.items()
            if account in BACKEND_ACCOUNT_SCOPED_ONLY and species_mol
        }
        if unsupported:
            raise AccountingError(
                'backend must accept composition_mol_by_account before '
                f'consuming account-scoped metal/gas inputs; got {unsupported}'
            )

    def _get_equilibrium(self):
        """
        Query backend with the mol inventory from the atom ledger.

        Kg composition remains available as an adapter projection for legacy
        helper surfaces, but MeltState kg is not authoritative.

        ``EquilibriumResult.status`` is recorded on
        ``_last_backend_status`` for diagnostics.  It is descriptive only:
        the fallback decisions below are still driven by ``is_available()``
        and the raised-exception handlers, exactly as before.  Status
        surfaces existing state at the consumption point; it does not
        introduce a new control-flow branch.
        """
        store = _pt0_determinism_store_for(self)
        if store is not None and getattr(store, 'replay_enabled', False):
            return store.replay_equilibrium(self)
        cache_write_through = (
            store is not None
            and getattr(store, 'write_through_enabled', False)
        )
        if cache_write_through:
            cached_result = store.cached_equilibrium(self)
            if cached_result is not None:
                return cached_result
        if self.backend is None:
            if cache_write_through:
                raise RuntimeError(
                    'reduced-real cache miss requires a live backend; '
                    'no backend is configured'
                )
            return self._record_equilibrium_status(self._stub_equilibrium())
        if self._backend_failed:
            if cache_write_through:
                raise RuntimeError(
                    self._last_backend_error
                    or 'reduced-real cache miss requires a live backend'
                )
            if not self._backend_allows_stub_fallback():
                raise RuntimeError(
                    self._last_backend_error
                    or 'configured backend is disabled after failure'
                )
            return self._record_equilibrium_status(self._stub_equilibrium())
        if not self.backend.is_available():
            if cache_write_through:
                raise RuntimeError(
                    self._last_backend_error
                    or f'{type(self.backend).__name__} is unavailable'
                )
            if not self._backend_allows_stub_fallback():
                raise RuntimeError(
                    self._last_backend_error
                    or f'{type(self.backend).__name__} is unavailable'
                )
            return self._record_equilibrium_status(self._stub_equilibrium())

        diagnostic_silicate_equilibrium = False
        try:
            backend_composition_by_account = (
                self._backend_composition_mol_by_account())
            self._validate_backend_account_scope_support(
                backend_composition_by_account)
            intrinsic_fO2_log = self._compute_intrinsic_melt_fO2()
            temperature_C = float(self.melt.temperature_C)
            pressure_bar = float(self.melt.p_total_mbar) / 1000.0
            canonicalize_pt0_inputs = store is not None and getattr(
                store,
                'quantize_live_controls',
                False,
            )
            if canonicalize_pt0_inputs:
                controls = store.quantized_controls(
                    self,
                    fO2_log=intrinsic_fO2_log,
                )
                temperature_C = float(controls['temperature_C'])
                pressure_bar = float(controls['pressure_bar'])
                intrinsic_fO2_log = controls['fO2_log']
                canonicalizer = getattr(
                    store,
                    'canonical_composition_mol_by_account',
                    None,
                )
                if not callable(canonicalizer):
                    raise RuntimeError(
                        'PT-0 live-control quantization requires composition '
                        'canonicalization support'
                    )
                backend_composition_by_account = canonicalizer(
                    self,
                    backend_composition_by_account,
                )
            if canonicalize_pt0_inputs:
                backend_composition_mol: Dict[str, float] = {}
                for species_mol in backend_composition_by_account.values():
                    for species, mol in species_mol.items():
                        backend_composition_mol[species] = (
                            backend_composition_mol.get(species, 0.0)
                            + float(mol)
                        )
                backend_composition_mol = {
                    species: mol
                    for species, mol in backend_composition_mol.items()
                    if mol > 0.0
                }
            else:
                backend_composition_mol = self._backend_composition_mol()
            self.melt.fO2_log = intrinsic_fO2_log
            request_controls = {
                'temperature_C': temperature_C,
                'pressure_bar': pressure_bar,
                'fO2_log': intrinsic_fO2_log,
                'fe_redox_policy': 'intrinsic',
            }
            if (
                self._chem_registry.authoritative_for(
                    ChemistryIntent.SILICATE_EQUILIBRIUM
                ) is not None
            ):
                from engines.alphamelts.parser import (
                    diagnostics_to_equilibrium,
                )
                from engines.alphamelts.result import LiquidusDiagnostics

                kernel_result = self._dispatch_only(
                    ChemistryIntent.SILICATE_EQUILIBRIUM,
                    control_inputs={},
                    fO2_log=intrinsic_fO2_log,
                    fe_redox_policy='intrinsic',
                )
                if kernel_result.transition is not None:
                    raise RuntimeError(
                        'SILICATE_EQUILIBRIUM returned a ledger transition; '
                        'silicate equilibrium is diagnostic-only'
                    )
                diagnostic = dict(kernel_result.diagnostic or {})
                result = diagnostics_to_equilibrium(
                    LiquidusDiagnostics(**diagnostic),
                    request_controls,
                )
                setattr(result, 'alphamelts_diagnostics', diagnostic)
                diagnostic_silicate_equilibrium = True
            else:
                backend_kwargs = {
                    'temperature_C': temperature_C,
                    'composition_mol': backend_composition_mol,
                    'species_formula_registry': self.species_formula_registry,
                    'fO2_log': intrinsic_fO2_log,
                    'pressure_bar': pressure_bar,
                }
                if self._backend_accepts_kwarg('composition_mol_by_account'):
                    backend_kwargs['composition_mol_by_account'] = (
                        backend_composition_by_account)
                result = self.backend.equilibrate(**backend_kwargs)
        except AccountingError:
            raise
        except BACKEND_FALLBACK_EXCEPTIONS as exc:
            self._last_backend_error = str(exc)
            self._disable_backend_after_failure()
            if cache_write_through:
                raise
            if not self._backend_allows_stub_fallback():
                raise
            return self._record_equilibrium_status(self._stub_equilibrium())
        except ValueError as exc:
            self._last_backend_error = str(exc)
            self._disable_backend_after_failure()
            if cache_write_through:
                raise
            if not self._backend_allows_stub_fallback():
                raise
            return self._record_equilibrium_status(self._stub_equilibrium())

        transition = getattr(result, 'ledger_transition', None)
        if (
            transition is None
            and not diagnostic_silicate_equilibrium
            and self._equilibrium_result_has_phase_species(result)
        ):
            self._last_backend_error = (
                'backend returned post-equilibrium phase material without an '
                'AtomLedger transition'
            )
            self._disable_backend_after_failure()
            if not self._backend_allows_stub_fallback():
                raise RuntimeError(self._last_backend_error)
            return self._record_equilibrium_status(self._stub_equilibrium())
        if transition is not None:
            self._validate_backend_ledger_transition(transition)
            self._require_chem_kernel().commit_validated_transition(
                ChemistryIntent.BACKEND_EQUILIBRIUM,
                transition,
            )
            self._project_cleaned_melt_from_atom_ledger()
        return self._record_equilibrium_status(result)

    def _record_equilibrium_status(self, result):
        """Record the per-call backend outcome and run the post-equilibrium
        SULFUR_SATURATION_GATE; returns ``result`` unchanged."""
        self._last_backend_status = getattr(result, 'status', 'ok')
        # VAPOR_PRESSURE intent — kernel-authoritative.
        #
        # \goal BUILTIN-ENGINE-EXTRACTION (#7), first flip landed. The
        # BuiltinVaporPressureProvider is the authoritative source for
        # vapor pressures; this call replaces result.vapor_pressures_Pa
        # with the kernel diagnostic so downstream consumers
        # (_calculate_evaporation, _route_to_condensation) read from the
        # kernel-owned path. The legacy _stub_equilibrium still computes
        # vapor pressures (the Antoine/Ellingham math IS the underlying
        # implementation behind the kernel) but that result is overwritten
        # here. Shadow parity verified clean across a full smoke run
        # (lunar + Mars + asteroid feedstocks, tolerance 1e-9 Pa rel +
        # 1e-9 Pa abs) before this flip landed; the shadow comparator was
        # removed at flip time per the goal spec ("comparing against the
        # same source is moot"). Subsequent intent flips
        # (EVAPORATION_FLUX, ...) sit on top of this same kernel.
        self._refresh_vapor_pressures_from_kernel(result)
        # SULFUR_SATURATION_GATE — post-equilibrium hook. Runs only when
        # Stage 0 left sulfide / sulfate inventory behind; otherwise
        # short-circuits without touching PySulfSat. Never mutates the
        # ledger (the gate has no LedgerTransition authority).
        self._attach_post_equilibrium_sulfsat(result)
        store = _pt0_determinism_store_for(self)
        if store is not None and getattr(store, 'capture_enabled', False):
            store.capture_equilibrium(self, result)
            self._last_reduced_real_cache_state = getattr(
                store,
                'last_cache_state',
                None,
            )
        return result

    def _refresh_vapor_pressures_from_kernel(self, result) -> None:
        """Refresh ``result.vapor_pressures_Pa`` from the kernel dispatch.

        Belongs to the VAPOR_PRESSURE flip in
        \\goal BUILTIN-ENGINE-EXTRACTION (#7). Called from
        :meth:`_record_equilibrium_status` after the backend (or the
        legacy stub) produces an EquilibriumResult.

        Behaviour:
          - Below 400 K both the legacy path and the kernel return an
            empty vapor-pressure dict; we leave the result untouched.
          - When the kernel returns a populated ``vapor_pressures_Pa``
            it replaces the equilibrium-result dict in place. For
            ThermoEngine-sourced species whose backend value agrees with
            the kernel diagnostic within parity tolerance, the Pa value
            still stays kernel-owned but the source tag is promoted to
            ``thermoengine`` so the report can prove the L5 activity path
            reached the evaporation surface without changing flux values.
            The activities dict is replaced too (the legacy stub set both
            atomically and downstream code keys off the same source).
          - If the equilibrium result proves ``liquid_fraction == 0``,
            no liquid surface exists.  Clear any backend vapor pressures
            and skip the kernel dispatch so the refractory rump is a
            physical zero, not a bulk-composition vapor source.
          - The kernel may return an empty dict legitimately (e.g. before
            the melt is seeded, or for an exotic feedstock with no known
            volatile species); leave the legacy result in that case so
            the existing zero-state behaviour is preserved.
          - If the kernel raises, propagate — silent fallback was
            explicitly forbidden by the goal spec.
        """

        T_C = float(self.melt.temperature_C)
        if T_C + 273.15 < 400:
            self._last_vapor_pressures_source = dict(
                getattr(result, 'vapor_pressures_source', {}) or {}
            )
            return

        backend_vp = dict(getattr(result, 'vapor_pressures_Pa', {}) or {})
        backend_sources = dict(
            getattr(result, 'vapor_pressures_source', {}) or {}
        )
        raw_liquid_fraction = getattr(result, 'liquid_fraction', None)
        # H1 permits None only for vapor-only results with no phase
        # assemblage.  That is not proof of a no-liquid rump, so the
        # physical-zero branch is exact-zero only.
        if raw_liquid_fraction is not None:
            try:
                liquid_fraction = float(raw_liquid_fraction)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    'Authoritative VAPOR_PRESSURE liquid_fraction_invalid: '
                    f'{raw_liquid_fraction!r}'
                ) from exc
            if (
                not math.isfinite(liquid_fraction)
                or liquid_fraction < 0.0
                or liquid_fraction > 1.0
            ):
                raise RuntimeError(
                    'Authoritative VAPOR_PRESSURE liquid_fraction_invalid: '
                    f'{raw_liquid_fraction!r}'
                )
            if liquid_fraction == 0.0:
                result.vapor_pressures_Pa = {}
                result.vapor_pressures_source = {}
                diagnostic = {
                    'status': 'ok',
                    'vapor_pressures_Pa': {},
                    'vapor_pressures_source': {},
                    'vapor_pressure_zero_reason': 'no_liquid_phase',
                    'liquid_fraction': 0.0,
                    'backend_vapor_pressures_source': dict(backend_sources),
                    'backend_vapor_pressures_Pa': dict(backend_vp),
                }
                self._last_vapor_pressures_source = {}
                self._last_vapor_pressure_diagnostic = diagnostic
                return
        # F-B1: VAPOR_PRESSURE is read-only -- no commit_batch follows.
        # The dispatch-only helper still routes melt-derived T/P through
        # the same single path the rest of the simulator uses.
        pO2_bar = self._commanded_pO2_bar()
        store = _pt0_determinism_store_for(self)
        if store is not None and getattr(store, 'quantize_live_controls', False):
            pO2_bar = store.quantized_pO2_bar(self)
        kernel_result = self._dispatch_only(
            ChemistryIntent.VAPOR_PRESSURE,
            control_inputs={'pO2_bar': pO2_bar},
            fO2_log=self._compute_intrinsic_melt_fO2(),
        )
        diagnostic = dict(kernel_result.diagnostic or {})
        diagnostic['backend_vapor_pressures_source'] = dict(backend_sources)
        diagnostic['backend_vapor_pressures_Pa'] = dict(backend_vp)
        kernel_vp = diagnostic.get('vapor_pressures_Pa') or {}
        if kernel_vp:
            kernel_source = self._kernel_vapor_pressure_source(diagnostic)
            merged_vp = dict(kernel_vp)
            merged_sources = {
                str(species): kernel_source
                for species in merged_vp
            }
            thermoengine_confirmed = []
            for species, kernel_value in kernel_vp.items():
                if backend_sources.get(species) != 'thermoengine':
                    continue
                if species not in backend_vp:
                    continue
                backend_value = backend_vp[species]
                if not self._vapor_pressure_values_agree(
                    backend_value, kernel_value
                ):
                    continue
                merged_sources[str(species)] = 'thermoengine'
                thermoengine_confirmed.append(str(species))
            result.vapor_pressures_Pa = merged_vp
            result.vapor_pressures_source = merged_sources
            diagnostic['vapor_pressures_source'] = dict(merged_sources)
            diagnostic['thermoengine_vapor_pressures_confirmed'] = tuple(
                sorted(thermoengine_confirmed)
            )
        else:
            # Autoreview r8 P1 (2026-05-27, post-0.5.0): the prior code
            # silently kept the pre-kernel backend vapor-pressure
            # surface whenever the kernel returned no pressures --
            # treating ``status='unavailable'`` (the authoritative
            # VapoRock import succeeded but the adapter call yielded no
            # result) identically to ``status='ok'`` with no
            # evaporation expected. Under ``allow_fallback_vapor=False``
            # that is a silent downgrade: the run continues on
            # stub/AlphaMELTS pressures with no operator-visible
            # signal. Now we distinguish:
            #
            #   - status='ok' / empty kernel_vp: legit (e.g. melt
            #     below evaporation threshold or oxide-only system);
            #     kernel-computed zero is authoritative.
            #   - status='unavailable' / 'failed' AND
            #     allow_fallback_vapor=False: raise loud so the
            #     operator sees the missing authoritative dispatch
            #     instead of inheriting silently.
            #   - status='unavailable' AND allow_fallback_vapor=True:
            #     keep backend with an explicit warning entry on the
            #     diagnostic (this is the documented fallback path).
            # Codex challenge pre-0.5.1 P1 (2026-05-27): malformed
            # status (None / "") must not authorise backend fallback.
            # The prior ``getattr(..., 'status', 'ok') or 'ok'`` form
            # treated missing OR empty status as success -- so any
            # provider that returned ``status=None`` (broken adapter)
            # or ``status=""`` (uninitialised) would silently appear
            # ``ok`` to the gate. Materialise the raw status first so
            # we can distinguish "missing/empty" from "ok".
            raw_status = getattr(kernel_result, 'status', None)
            if raw_status is None or str(raw_status).strip() == '':
                kernel_status = 'unknown'
            else:
                kernel_status = str(raw_status).strip().lower()
            # Autoreview pre-0.5.1 P1 (2026-05-27): the prior version only
            # caught {'unavailable', 'failed'}, but the VapoRock provider
            # also emits 'not_converged' and 'out_of_domain' (per
            # engines/vaporock/provider.py:211 the canonical kernel
            # status vocabulary is ``ok | not_converged | out_of_domain
            # | unavailable``). When kernel_vp is empty AND status is
            # ANY non-'ok' value, the run is silently downgrading to the
            # backend surface -- exactly what the r8 fix was meant to
            # prevent. Invert the check: anything non-'ok' (including
            # the new 'unknown' bucket for malformed status) with no
            # kernel pressures is a failure mode under
            # allow_fallback_vapor=False.
            if kernel_status == 'ok':
                result.vapor_pressures_Pa = {}
                result.vapor_pressures_source = {}
                diagnostic['vapor_pressures_Pa'] = {}
                diagnostic['vapor_pressures_source'] = {}
                diagnostic[
                    'vapor_pressure_zero_reason'
                ] = 'kernel_ok_empty'
            else:
                if not self._allow_fallback_vapor:
                    # Codex challenge pre-0.5.1 P2 (2026-05-27): keep the
                    # exception message bounded so operator logs stay
                    # readable. Surface the status + the diagnostic keys
                    # only; full diagnostic dict is on
                    # ``self._last_vapor_pressure_diagnostic`` for the
                    # caller that wants the full dump.
                    diagnostic_keys = sorted(diagnostic.keys())
                    raise RuntimeError(
                        f"Authoritative VAPOR_PRESSURE dispatch returned "
                        f"status={kernel_status!r} with no pressures and "
                        f"allow_fallback_vapor=False; refusing to silently "
                        f"continue on backend vapor pressures. Diagnostic "
                        f"keys: {diagnostic_keys!r}"
                    )
                # Codex challenge pre-0.5.1 P2 (2026-05-27): defensive
                # warning-append. The diagnostic dict is free-form per
                # the provider contract, so the
                # ``kernel_vapor_pressure_warnings`` slot may already
                # hold a non-list value (None, str, etc.); coerce to
                # list before appending.
                existing_warnings = diagnostic.get(
                    'kernel_vapor_pressure_warnings')
                if not isinstance(existing_warnings, list):
                    existing_warnings = (
                        [existing_warnings]
                        if existing_warnings is not None else []
                    )
                existing_warnings.append(
                    f"VAPOR_PRESSURE returned status={kernel_status!r}; "
                    f"falling back to backend vapor pressures under "
                    f"allow_fallback_vapor=True."
                )
                diagnostic['kernel_vapor_pressure_warnings'] = existing_warnings
                if backend_sources:
                    result.vapor_pressures_source = dict(backend_sources)
                diagnostic['vapor_pressures_source'] = dict(
                    getattr(result, 'vapor_pressures_source', {}) or {}
                )
        kernel_activities = diagnostic.get('activities') or {}
        if kernel_activities:
            result.activity_coefficients = dict(kernel_activities)
        self._last_vapor_pressures_source = dict(
            getattr(result, 'vapor_pressures_source', {}) or {}
        )
        self._last_vapor_pressure_diagnostic = diagnostic

    @staticmethod
    def _kernel_vapor_pressure_source(diagnostic: Mapping[str, Any]) -> str:
        provider = diagnostic.get('kernel_fallback_used')
        if provider == 'builtin-vapor-pressure':
            return 'builtin_fallback'
        if 'vaporock_full_speciation_Pa' in diagnostic:
            return 'vaporock'
        return 'kernel_diagnostic'

    @staticmethod
    def _vapor_pressure_values_agree(left: Any, right: Any) -> bool:
        try:
            lhs = float(left)
            rhs = float(right)
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(lhs) and math.isfinite(rhs)):
            return False
        tolerance = max(1e-9, 1e-9 * max(abs(lhs), abs(rhs)))
        return abs(lhs - rhs) <= tolerance

    def _backend_accepts_kwarg(self, name: str) -> bool:
        try:
            signature = inspect.signature(self.backend.equilibrate)
        except (TypeError, ValueError):
            return False
        return name in signature.parameters

    def _backend_allows_stub_fallback(self) -> bool:
        return (
            self.backend is None
            or isinstance(self.backend, StubBackend)
        )

    def _disable_backend_after_failure(self) -> None:
        self._backend_failed = True
        if self.backend is not None and hasattr(self.backend, '_available'):
            setattr(self.backend, '_available', False)

    @staticmethod
    def _equilibrium_result_has_phase_species(result: Any) -> bool:
        for attr in ('phase_species_mol', 'phase_species_kg'):
            phase_species = getattr(result, attr, None)
            if not phase_species:
                continue
            for species in phase_species.values():
                if any(float(amount) > 0.0 for amount in dict(species).values()):
                    return True
        phase_masses = getattr(result, 'phase_masses_kg', None)
        if phase_masses and any(
            float(amount) > 0.0 for amount in dict(phase_masses).values()
        ):
            return True
        return False

    def _apply_stage0_carbon_reductant_reactions(
        self,
        feedstock: Mapping[str, Any],
        additives_kg: Dict[str, float],
    ) -> None:
        required_kg = max(0.0, float(
            self.inventory.carbon_reductant_required_kg))
        if required_kg <= 1e-12:
            return
        reaction_ids = self._stage0_carbon_cleanup_reaction_ids(feedstock)
        if not reaction_ids:
            raise AccountingError(
                'Stage 0 carbon cleanup requires explicit '
                'stage0_carbon_cleanup.reactions'
            )
        self._validate_stage0_carbon_reaction_order(reaction_ids)
        available_kg = max(0.0, float(additives_kg.get('C', 0.0)))
        if available_kg + 1e-12 < required_kg:
            raise AccountingError(
                f"Stage 0 carbon cleanup requires {required_kg:.6g} kg C; "
                f"only {available_kg:.6g} kg is available"
            )
        carbon_mol_remaining = required_kg / resolve_species_formula(
            'C', self.species_formula_registry).molar_mass_kg_per_mol()

        specs: list[dict] = []
        for reaction_id in reaction_ids:
            if reaction_id == 'sulfate_so3_to_so2_co':
                carbon_mol_remaining = self._apply_stage0_sulfate_carbon_reaction(
                    carbon_mol_remaining, specs)
            elif reaction_id == 'co2_boudouard_to_co':
                if not self._has_stage0_co2_source(feedstock):
                    raise AccountingError(
                        'co2_boudouard_to_co requires a declared CO2 atmosphere '
                        'or stage0_carbon_cleanup.co2_source'
                    )
                carbon_mol_remaining = self._apply_stage0_boudouard_reaction(
                    carbon_mol_remaining, specs)
            else:
                raise AccountingError(
                    f'unsupported Stage 0 carbon cleanup reaction {reaction_id!r}'
                )

        carbon_kg_remaining = carbon_mol_remaining * resolve_species_formula(
            'C', self.species_formula_registry).molar_mass_kg_per_mol()
        if carbon_kg_remaining > 1e-9:
            raise AccountingError(
                'Stage 0 carbon cleanup reactions do not consume required C: '
                f'{carbon_kg_remaining:.6g} kg remains'
            )

        additives_kg['C'] = max(0.0, available_kg - required_kg)
        if additives_kg['C'] <= 1e-12:
            additives_kg.pop('C', None)
        self._stage0_carbon_cleanup_specs = specs

    @staticmethod
    def _validate_stage0_carbon_reaction_order(reaction_ids: list[str]) -> None:
        if (
            'sulfate_so3_to_so2_co' in reaction_ids
            and 'co2_boudouard_to_co' in reaction_ids
            and reaction_ids.index('co2_boudouard_to_co')
            < reaction_ids.index('sulfate_so3_to_so2_co')
        ):
            raise AccountingError(
                'stage0_carbon_cleanup.reactions must run '
                'sulfate_so3_to_so2_co before co2_boudouard_to_co'
            )

    @staticmethod
    def _has_stage0_co2_source(feedstock: Mapping[str, Any]) -> bool:
        cleanup = feedstock.get('stage0_carbon_cleanup') or {}
        if isinstance(cleanup, Mapping) and cleanup.get('co2_source'):
            return True
        environment = feedstock.get('environment') or {}
        atmosphere = str(feedstock.get('atmosphere') or '')
        if isinstance(environment, Mapping):
            atmosphere = str(environment.get('atmosphere') or atmosphere)
        return 'co2' in atmosphere.lower()

    @classmethod
    def _stage0_carbon_cleanup_reaction_ids(
        cls, feedstock: Mapping[str, Any]
    ) -> list[str]:
        cleanup = feedstock.get('stage0_carbon_cleanup') or {}
        if not isinstance(cleanup, Mapping):
            raise ValueError('stage0_carbon_cleanup must be a mapping')
        raw_reactions = cleanup.get('reactions') or []
        if not isinstance(raw_reactions, (list, tuple)):
            raise ValueError('stage0_carbon_cleanup.reactions must be a list')
        reactions: list[str] = []
        for item in raw_reactions:
            if isinstance(item, Mapping):
                reaction_id = str(item.get('id', '')).strip()
            else:
                reaction_id = str(item).strip()
            if reaction_id:
                reactions.append(reaction_id)
        return reactions

    def _apply_stage0_sulfate_carbon_reaction(
        self, carbon_mol_remaining: float, specs: list[dict]
    ) -> float:
        if carbon_mol_remaining <= 1e-12:
            return 0.0
        so3_kg = max(0.0, float(self.inventory.salt_phase_kg.get('SO3', 0.0)))
        if so3_kg <= 1e-12:
            return carbon_mol_remaining

        molar = {
            species: resolve_species_formula(
                species, self.species_formula_registry
            ).molar_mass_kg_per_mol()
            for species in ('C', 'SO3', 'SO2', 'CO')
        }
        extent_mol = min(carbon_mol_remaining, so3_kg / molar['SO3'])
        if extent_mol <= 1e-12:
            return carbon_mol_remaining

        so3_consumed_kg = extent_mol * molar['SO3']
        c_consumed_kg = extent_mol * molar['C']
        products_kg = {
            'SO2': extent_mol * molar['SO2'],
            'CO': extent_mol * molar['CO'],
        }
        self._decrease_inventory_species(
            self.inventory.salt_phase_kg, 'SO3', so3_consumed_kg)
        self._decrease_inventory_species(
            self.inventory.stage0_products_kg, 'SO3', so3_consumed_kg)
        self._merge_masses(self.inventory.gas_volatiles_kg, products_kg)
        self._merge_masses(self.inventory.stage0_products_kg, products_kg)
        specs.append({
            'name': 'stage0_sulfate_carbon_cleanup',
            'debits': (
                ('process.stage0_salt_feed', {'SO3': so3_consumed_kg}),
                ('process.stage0_carbon_reductant', {'C': c_consumed_kg}),
            ),
            'products_kg': products_kg,
        })
        return carbon_mol_remaining - extent_mol

    def _apply_stage0_boudouard_reaction(
        self, carbon_mol_remaining: float, specs: list[dict]
    ) -> float:
        if carbon_mol_remaining <= 1e-12:
            return 0.0
        molar = {
            species: resolve_species_formula(
                species, self.species_formula_registry
            ).molar_mass_kg_per_mol()
            for species in ('C', 'CO2', 'CO')
        }
        c_consumed_kg = carbon_mol_remaining * molar['C']
        co2_input_kg = carbon_mol_remaining * molar['CO2']
        products_kg = {'CO': 2.0 * carbon_mol_remaining * molar['CO']}
        self.inventory.stage0_external_inputs_kg['CO2'] = (
            self.inventory.stage0_external_inputs_kg.get('CO2', 0.0)
            + co2_input_kg
        )
        self._merge_masses(self.inventory.gas_volatiles_kg, products_kg)
        self._merge_masses(self.inventory.stage0_products_kg, products_kg)
        specs.append({
            'name': 'stage0_boudouard_carbon_cleanup',
            'debits': (
                ('process.stage0_carbon_reductant', {'C': c_consumed_kg}),
                ('reservoir.stage0_process_gas', {'CO2': co2_input_kg}),
            ),
            'products_kg': products_kg,
        })
        return 0.0

    def _apply_stage0_perchlorate_reactions(
        self,
        feedstock: Mapping[str, Any],
    ) -> None:
        cleanup = feedstock.get('stage0_perchlorate_cleanup') or {}
        if not cleanup:
            return
        if not isinstance(cleanup, Mapping):
            raise ValueError('stage0_perchlorate_cleanup must be a mapping')
        raw_reactions = cleanup.get('reactions') or []
        if not isinstance(raw_reactions, (list, tuple)):
            raise ValueError('stage0_perchlorate_cleanup.reactions must be a list')
        reactions = [str(item.get('id', '') if isinstance(item, Mapping) else item)
                     for item in raw_reactions]
        supported_reactions = {'perchlorate_to_chloride_o2'}
        for reaction_id in reactions:
            if reaction_id not in supported_reactions:
                raise AccountingError(
                    f"stage0_perchlorate_cleanup.reactions contains "
                    f"unsupported reaction {reaction_id!r}"
                )
        if 'perchlorate_to_chloride_o2' not in reactions:
            raise AccountingError(
                'stage0_perchlorate_cleanup.reactions must include '
                'perchlorate_to_chloride_o2'
            )
        clo4_kg = max(0.0, float(self.inventory.salt_phase_kg.get('ClO4', 0.0)))
        if clo4_kg <= 1e-12:
            return

        molar = {
            species: resolve_species_formula(
                species, self.species_formula_registry
            ).molar_mass_kg_per_mol()
            for species in ('ClO4', 'Cl', 'O2')
        }
        extent_mol = clo4_kg / molar['ClO4']
        salt_products_kg = {'Cl': extent_mol * molar['Cl']}
        oxygen_products_kg = {'O2': 2.0 * extent_mol * molar['O2']}
        self._decrease_inventory_species(
            self.inventory.salt_phase_kg, 'ClO4', clo4_kg)
        self._decrease_inventory_species(
            self.inventory.stage0_products_kg, 'ClO4', clo4_kg)
        self._merge_masses(self.inventory.salt_phase_kg, salt_products_kg)
        self._merge_masses(self.inventory.stage0_products_kg, salt_products_kg)
        specs = [{
            'name': 'stage0_perchlorate_cleanup',
            'debits': (
                ('process.stage0_perchlorate_feed', {'ClO4': clo4_kg}),
            ),
            'salt_products_kg': salt_products_kg,
            'oxygen_products_kg': oxygen_products_kg,
        }]
        self._stage0_perchlorate_cleanup_specs = specs

    @staticmethod
    def _decrease_inventory_species(
        values: Dict[str, float], species: str, kg: float
    ) -> None:
        remaining = float(values.get(species, 0.0)) - float(kg)
        if remaining < -1e-8:
            raise AccountingError(
                f'Stage 0 reaction consumes more {species} than available'
            )
        if remaining > 1e-12:
            values[species] = remaining
        else:
            values.pop(species, None)

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
            requires_local_metadata = (
                self._normalized_component_key(species)
                in STRICT_STAGE0_FORMULA_COMPONENTS
            ) or bool(getattr(base_formula, 'requires_feedstock_metadata', False))
            if not requires_local_metadata:
                continue
            if species not in entries:
                raise ValueError(
                    f"{feedstock.get('label', 'feedstock')} uses mixed "
                    f"species {species!r}; declare "
                    f"stage0_formula_inventory.{species} with explicit "
                    "formula/template_formula and furnace offgas metadata"
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
        legacy_template_keys = [
            key for key in ('template', 'generic_formula') if key in entry
        ]
        if legacy_template_keys:
            raise ValueError(
                f'stage0_formula_inventory.{species} must use '
                'template_formula instead of '
                + ', '.join(sorted(legacy_template_keys))
            )
        has_formula = any(
            key in entry
            for key in (
                'template_formula',
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
                'or template_formula'
            )
        for key in ('decomposition_temp_range_C', 'final_temp_C',
                    'cap_kg_per_tonne', 'offgas_mode', 'source'):
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
        offgas_mode = str(entry['offgas_mode']).lower()
        if offgas_mode not in {'complete_oxidation', 'oxidized',
                               'native_mixture'}:
            raise ValueError(
                f'stage0_formula_inventory.{species}.offgas_mode is unknown'
            )
        needs_oxygen_source = offgas_mode in {'complete_oxidation', 'oxidized'}
        if needs_oxygen_source and not entry.get('oxygen_source'):
            raise ValueError(
                f'stage0_formula_inventory.{species}.oxygen_source is required'
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
        for section_name in ('declared_stage0_products',
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
        raw_value = feedstock.get('stage0_carbon_reductant_kg_per_tonne')
        cleanup = feedstock.get('stage0_carbon_cleanup') or {}
        if not isinstance(cleanup, Mapping):
            raise ValueError('stage0_carbon_cleanup must be a mapping')
        if raw_value is None:
            raw_value = cleanup.get('carbon_reductant_kg_per_tonne')
        if raw_value is None:
            if cls._uses_mars_carbon_cleanup(feedstock):
                raise ValueError(
                    'mars_carbon_cleanup requires explicit '
                    'stage0_carbon_cleanup.carbon_reductant_kg_per_tonne'
                )
            return 0.0
        kg_per_tonne = cls._representative_number(raw_value)
        if kg_per_tonne is None or kg_per_tonne < 0.0:
            raise ValueError(
                'stage0_carbon_cleanup.carbon_reductant_kg_per_tonne '
                'must be a non-negative number or [low, high] range'
            )
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
        self.melt.validate_melt_pressures()
        self._configure_overhead_headspace(campaign)
        self._configure_freeze_gate(campaign)
        self.melt.fO2_log = self._compute_intrinsic_melt_fO2()

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

    @staticmethod
    def _diagnostic_target_species(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            targets = []
            for item in value:
                if item is None:
                    continue
                target = str(item)
                if target:
                    targets.append(target)
            return tuple(targets)
        return ()

    @staticmethod
    def _diagnostic_optional_float(
        cfg: Mapping[str, Any],
        key: str,
    ) -> tuple[Optional[float], str]:
        if key not in cfg or cfg.get(key) is None:
            return None, f"no {key} set"
        try:
            value = float(cfg[key])
        except (TypeError, ValueError) as exc:
            return None, f"invalid {key}: {exc}"
        if not math.isfinite(value):
            return None, f"invalid {key}: non-finite"
        return value, "set"

    def _update_extraction_completeness_diagnostic(self) -> None:
        config_getter = getattr(self.campaign_mgr, "_campaign_config", None)
        cfg = config_getter(self.melt.campaign) if callable(config_getter) else {}
        if not isinstance(cfg, Mapping):
            cfg = {}
        target_species = self._diagnostic_target_species(
            cfg.get("target_species"))
        if not target_species:
            self._last_extraction_completeness_diagnostic = {}
            return

        threshold, threshold_status = self._diagnostic_optional_float(
            cfg, "target_yield_threshold")
        if threshold is None and threshold_status == "no target_yield_threshold set":
            threshold_status = "no threshold set"
        max_hold_hr, max_hold_status = self._diagnostic_optional_float(
            cfg, "max_hold_hr")
        base = {
            "campaign": self.melt.campaign.name,
            "campaign_hour": self.melt.campaign_hour,
            "target_species": target_species,
            "target_yield_threshold": threshold,
            "target_yield_threshold_status": threshold_status,
            "max_hold_hr": max_hold_hr,
            "max_hold_hr_status": max_hold_status,
        }

        try:
            queries = AccountingQueries(self)
            products = queries.product_ledger()
            rump = queries.terminal_rump_by_species()
            by_target = extraction_completeness_by_target(
                target_species,
                DEFAULT_RESIDUAL_SPECIES_BY_TARGET,
                products,
                rump,
                require_residual_species=True,
            )
            campaign_key = self.campaign_mgr._campaign_config_key(
                self.melt.campaign)
            contract_by_target = {
                contract.target_key: contract
                for contract in completion_contracts_for_campaign(
                    self.setpoints,
                    campaign_key,
                )
            }
            for target in target_species:
                contract = contract_by_target.get(target)
                if contract is None:
                    by_target[target] = TargetExtractionCompleteness(
                        target,
                        None,
                        0.0,
                        0.0,
                        0.0,
                        "unknown: no completion contract",
                    )
                    continue
                try:
                    by_target[target] = vapor_contract_completeness(
                        contract,
                        queries,
                    )
                except CompletionContractBlocked as exc:
                    by_target[target] = TargetExtractionCompleteness(
                        target,
                        None,
                        0.0,
                        0.0,
                        0.0,
                        f"blocked: {exc}",
                        contract_id=contract.contract_id,
                    )
        except Exception as exc:
            by_target = {
                target: None
                for target in target_species
            }
            query_error = f"unknown: {exc}"
        else:
            query_error = ""

        completeness: Dict[str, Optional[float]] = {}
        detail: Dict[str, Dict[str, Any]] = {}
        soft: Dict[str, Dict[str, Any]] = {}
        for target in target_species:
            result = by_target.get(target)
            if result is None:
                fraction = None
                reason = query_error or "unknown: no result"
                product_mol = residual_mol = denom_mol = None
                wall_mol = reagent_mol = gross_product_mol = None
                contract_id = ""
            else:
                fraction = result.completeness_fraction
                reason = result.reason
                product_mol = result.product_target_equiv_mol
                residual_mol = result.residual_target_equiv_mol
                denom_mol = result.denominator_target_equiv_mol
                wall_mol = result.wall_deposit_target_equiv_mol
                reagent_mol = result.reagent_target_equiv_mol
                gross_product_mol = result.gross_product_target_equiv_mol
                contract_id = result.contract_id
            completeness[target] = fraction
            detail[target] = {
                "product_target_equiv_mol": product_mol,
                "residual_target_equiv_mol": residual_mol,
                "denominator_target_equiv_mol": denom_mol,
                "wall_deposit_target_equiv_mol": wall_mol,
                "reagent_target_equiv_mol": reagent_mol,
                "gross_product_target_equiv_mol": gross_product_mol,
                "contract_id": contract_id,
                "reason": reason,
            }
            if fraction is None:
                soft[target] = {
                    "would_advance": None,
                    "reason": reason,
                }
            elif threshold is None:
                soft[target] = {
                    "would_advance": None,
                    "reason": threshold_status,
                }
            else:
                soft[target] = {
                    "would_advance": fraction >= threshold,
                    "reason": "threshold set",
                }

        aggregate = aggregate_extraction_completeness(by_target, target_species)
        aggregate_status = (
            "n/a"
            if aggregate.completeness_fraction is None
            else "ok"
        )
        if aggregate.completeness_fraction is None:
            aggregate_soft = {
                "would_advance": None,
                "reason": aggregate.reason,
            }
        elif threshold is None:
            aggregate_soft = {
                "would_advance": None,
                "reason": threshold_status,
            }
        else:
            aggregate_soft = {
                "would_advance": aggregate.completeness_fraction >= threshold,
                "reason": "threshold set",
            }

        liquid_fraction = None
        hard_would_advance = None
        hard_reason = "freeze gate disabled"
        if self._freeze_gate_enabled():
            try:
                liquid_fraction = float(self._freeze_gate_liquid_fraction_factor())
            except Exception as exc:
                hard_reason = f"unknown: {exc}"
            else:
                hard_would_advance = liquid_fraction == 0.0
                hard_reason = "freeze gate enabled"

        cap_would_advance = None
        cap_reason = max_hold_status
        if max_hold_hr is not None:
            cap_would_advance = self.melt.campaign_hour >= max_hold_hr
            cap_reason = "max_hold_hr set"

        self._last_extraction_completeness_diagnostic = {
            **base,
            "completeness_by_target_species": completeness,
            "aggregate_completeness_fraction": aggregate.completeness_fraction,
            "aggregate_worst_target_species": aggregate.worst_target_species,
            "aggregate_status": aggregate_status,
            "aggregate_reason": aggregate.reason,
            "aggregate_policy": aggregate.aggregation,
            "detail_by_target_species": detail,
            "would_be_soft_advance_by_target_species": soft,
            "would_be_soft_advance_aggregate": aggregate_soft,
            "liquid_fraction": liquid_fraction,
            "would_be_hard_floor_advance": hard_would_advance,
            "hard_floor_status": hard_reason,
            "would_be_cap_advance": cap_would_advance,
            "cap_status": cap_reason,
        }

    def product_ledger(self) -> Dict[str, float]:
        """
        Return output products accumulated outside the remaining melt.

        The atom ledger is the source of truth. Condenser stage dictionaries are
        UI projections and must not mint product mass.
        """
        return AccountingQueries(self).product_ledger()

    def _terminal_slag_kg(self) -> float:
        return (
            self.atom_ledger.total_kg_by_account('process.cleaned_melt')
            + self.atom_ledger.total_kg_by_account('terminal.slag')
        )

    def _terminal_rump_by_species(self) -> Dict[str, float]:
        return AccountingQueries(self).terminal_rump_by_species()

    def _terminal_rump_by_class(self) -> Dict[str, float]:
        return AccountingQueries(self).terminal_rump_by_class()

    def _ledger_total_mass_kg(self) -> float:
        return sum(self.atom_ledger.total_kg_by_account().values())

    def _flow_mass_out_kg(self) -> float:
        totals = self.atom_ledger.total_kg_by_account()
        accounts = set(FLOW_MASS_ACCOUNTS)
        accounts.update(
            account for account in totals
            if account.startswith('reservoir.')
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
        return AccountingQueries(self).oxygen_terminal_partition_kg()

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
        return AccountingQueries(self).condensation_totals_with_terminal_oxygen()

    def _overhead_gas_totals(self) -> Dict[str, float]:
        return {
            species: float(kg)
            for species, kg in self.atom_ledger.kg_by_account(
                'process.overhead_gas').items()
            if kg > 1e-12 and species != OXYGEN_SPECIES
        }

    def _validate_backend_ledger_transition(
        self, transition: LedgerTransition
    ) -> None:
        if transition.name not in BACKEND_LEDGER_TRANSITION_NAMES:
            raise AccountingError(
                'backend equilibrium transition name must be one of '
                f'{list(BACKEND_LEDGER_TRANSITION_NAMES)}; got '
                f'{transition.name!r}'
            )
        allowed_accounts = set(BACKEND_REACTIVE_ACCOUNTS) | {FO2_BUFFER_ACCOUNT}
        forbidden_accounts = sorted({
            str(lot.account)
            for lot in tuple(transition.debits) + tuple(transition.credits)
            if str(lot.account) not in allowed_accounts
        })
        if forbidden_accounts:
            raise AccountingError(
                'backend equilibrium transition may only touch '
                f'{sorted(allowed_accounts)}; got {forbidden_accounts}'
            )
        touches_fo2_buffer = any(
            str(lot.account) == FO2_BUFFER_ACCOUNT
            for lot in tuple(transition.debits) + tuple(transition.credits)
        )
        if touches_fo2_buffer and not self.atom_ledger.account_policy(
            FO2_BUFFER_ACCOUNT).allow_negative:
            raise AccountingError(
                f'{FO2_BUFFER_ACCOUNT} requires an explicit backend account '
                'policy before backend equilibrium may debit or credit it'
            )

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

        rump_expectation = self._rump_expectation_diagnostic(campaign_name)
        warning = rump_expectation.get('warning')
        if warning:
            self._rump_expectation_warnings.append(str(warning))

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
            'rump_expectation': rump_expectation,
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
        self._last_condensed_by_stage_species_delta = {}
        self._last_wall_deposit_by_segment_species_delta = {}
        self._last_impurity_delta = {}
        self._last_extraction_completeness_diagnostic = {}

        # --- 1. Decision check ---
        if self.paused_for_decision:
            # Return current state without advancing
            return self._make_snapshot()
        self._mre_anode_O2_kg_this_hr = 0.0
        self.melt.validate_melt_pressures()

        # --- 2. Temperature ramp ---
        self._update_temperature()
        self.melt.fO2_log = self._compute_intrinsic_melt_fO2()

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
                                   CampaignPhase.C2A,
                                   CampaignPhase.C2A_STAGED,
                                   CampaignPhase.C2B,
                                   CampaignPhase.C3_K, CampaignPhase.C3_NA,
                                   CampaignPhase.C4):
            evap_flux = self._calculate_evaporation(equilibrium)
            evap_flux = self._apply_analytic_evaporation_depletion(evap_flux)

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
        # Send evaporated species through the 8-stage train.
        # Each stage collects species based on its temperature.
        if evap_flux.total_kg_hr > 0:
            self._configure_condensation_operating_conditions(evap_flux)
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
        # The AtomLedger is the canonical quantity authority (see AGENTS.md),
        # so the turbine/vent decision is fed strictly the actual finite O2
        # holdup in process.overhead_gas. This is NOT max()'d with a per-tick
        # production counter: the holdup already includes this tick's
        # evaporation O2 coproduct (credited in evaporation.py) plus any O2
        # carried over from prior ticks not yet drained. max()-ing the two
        # overlapping quantities would let the turbine see carried-over O2 as
        # fresh throughput, or mask the case where holdup < this-hour output.
        finite_headspace_enabled = self._overhead_headspace_enabled()
        bleed_result = None
        if finite_headspace_enabled:
            bleed_result = self._dispatch_overhead_bleed(
                turbine_spec=turbine_spec)
            bleed_diag = dict(bleed_result.diagnostic or {})
            melt_offgas_O2_kg_hr = float(
                bleed_diag.get('bled_o2_kg', 0.0) or 0.0)
        else:
            melt_offgas_O2_kg_hr = self._ledger_o2_kg(
                'process.overhead_gas')
        self.overhead = self.overhead_model.update(
            evap_flux,
            self.melt,
            self.train,
            turbine_spec=turbine_spec,
            actual_O2_kg_hr=melt_offgas_O2_kg_hr,
            actual_O2_mol_hr=melt_offgas_O2_kg_hr / OXYGEN_MOLAR_MASS_KG_PER_MOL,
            mre_anode_O2_mol_hr=(
                self._mre_anode_O2_kg_this_hr / OXYGEN_MOLAR_MASS_KG_PER_MOL),
            overhead_holdup_mol=self._overhead_holdup_mol(),
            existing_gas=self.overhead,
            headspace_volume_m3=self._headspace_volume_m3(),
            p_downstream_bar=self._headspace_downstream_pressure_bar(),
            bleed_conductance_kg_s_per_bar=(
                self._headspace_bleed_conductance_kg_s_per_bar()))

        # Track cumulative O₂ vented and stored
        if not finite_headspace_enabled:
            self._dispatch_overhead_bleed(
                turbine_spec=turbine_spec,
                force_drain_all=True,
                o2_vented_kg=self.overhead.O2_vented_kg_hr,
            )
        self._sync_oxygen_kg_counters()

        # --- 8. Energy ---
        energy = self.energy_tracker.calculate_hour(
            self.melt, self.overhead, evap_flux,
            mre_kWh=mre_energy_kWh,  # Actual MRE energy this hour
        )
        self.energy_cumulative_kWh += energy.total_kWh

        self._update_extraction_completeness_diagnostic()

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
            self.overhead_model.pipe_diameter_m = self._equipment.pipe.diameter_m
            self.overhead_model.pipe_length_m = self._equipment.pipe.length_m
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
            self.record.path = choice  # 'A', 'A_staged', or 'B'
            if choice == 'A':
                self.start_campaign(CampaignPhase.C2A)
            elif choice == 'A_staged':
                self.start_campaign(CampaignPhase.C2A_STAGED)
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

    def _latest_knudsen_summary(self) -> Dict[str, Any]:
        """0.5.4.1 E3: project the latest Knudsen-regime diagnostic
        from the condensation model into the snapshot-facing summary
        dict shape documented on ``HourSnapshot.
        knudsen_regime_summary``.

        Returns an empty dict when the condensation model hasn't
        run yet (degenerate tick — e.g., a pre-C2A warmup before any
        evap flux fires). Otherwise pulls the canonical fields off
        ``CondensationModel.last_knudsen_regime_diagnostic`` and
        normalises into a JSON-serialisable shape so the snapshot
        round-trips cleanly through ``runner.py`` output.
        """
        model = self._condensation_model
        if model is None:
            return {}
        diag = dict(getattr(model, 'last_knudsen_regime_diagnostic', {}) or {})
        if not diag:
            return {}
        # Project the diagnostic into the documented summary shape.
        summary: Dict[str, Any] = {}
        if 'status' in diag:
            summary['status'] = str(diag['status'])
        # Knudsen number from the model directly (more reliable than
        # parsing it back out of the diagnostic).
        kn = getattr(model, 'knudsen_number', None)
        if kn is not None:
            try:
                summary['knudsen_number'] = float(kn)
            except (TypeError, ValueError):
                pass
        regime = getattr(model, 'knudsen_regime', None)
        if regime is not None:
            summary['knudsen_regime'] = getattr(
                regime, 'value', str(regime)
            )
        regime_factor = getattr(model, 'regime_factor', None)
        if regime_factor is not None:
            try:
                summary['regime_factor'] = float(regime_factor)
            except (TypeError, ValueError):
                pass
        warnings = diag.get('warnings', ())
        summary['warnings'] = tuple(str(w) for w in warnings)
        return summary

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
            condensed_by_stage_species_delta=dict(
                self._last_condensed_by_stage_species_delta),
            wall_deposit_by_segment_species_delta=dict(
                self._last_wall_deposit_by_segment_species_delta),
            impurity_delta=dict(self._last_impurity_delta),
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
            stage0_O2_stored_kg=oxygen_partition['stage0_stored'],
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
            # 0.5.4 W8 (M2 historical-audit closure): per-species drift
            # between ``process.metal_phase`` ledger and the
            # ``train.stages[*].collected_kg`` UI projection. Empty dict
            # means all metals in sync. Diagnostic only — the global
            # ``mass_balance_error_pct`` ≤5e-12 % gate remains hard.
            metal_projection_drift_kg=self._audit_metal_projection_drift(),
            # 0.5.4.1 E3: Knudsen-regime warning sticker from the
            # latest condensation pass. Empty dict on ticks that
            # didn't trigger a condensation route.
            knudsen_regime_summary=self._latest_knudsen_summary(),
        )
