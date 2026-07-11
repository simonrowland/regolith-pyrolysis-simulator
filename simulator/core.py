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
from collections import deque
from dataclasses import dataclass
import logging
import math
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Optional, Tuple

from simulator.account_ids import (
    C7_AL_CREDIT_ACCOUNT,
    CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
    OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
    OXYGEN_SPECIES,
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_STORED_ACCOUNTS,
    OXYGEN_VENTED_ACCOUNTS,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
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
from simulator.accounting.ledger import (
    KNOWN_LEDGER_ACCOUNTS,
    KNOWN_LEDGER_ACCOUNT_PREFIXES,
)
from simulator.condensation_routing import (
    designated_stage_number,
    target_species_for_stage_number,
)
from simulator.cost_ledger import CostImportContext, CostLedger
from simulator.feedstock_guard import assert_feedstock_loadable
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR, feedstock_body
from simulator.fe_redox import (
    calphad_ferrous_feo_activity_diagnostic,
    feo_iw_log10_fO2_bar,
    feot_equivalent_wt_pct,
    floor_vacuum_pressure_bar,
    KRESS91_LIQUID_CALIBRATION_MIN_T_C,
    kress91_ln_fO2_temperature_delta,
    kress91_split,
    melt_mol_fractions_for_kress91,
)
from simulator.melt_regime import MeltRegime, melt_regime
from simulator.lab_geometry import LabGeometryError, parse_lab_geometry
from simulator.lab_schedule import LabScheduleValidationError, interpolate_schedule_points
from simulator.accounting.completeness import (
    CompletionContractBlocked,
    DEFAULT_RESIDUAL_SPECIES_BY_TARGET,
    TargetExtractionCompleteness,
    aggregate_extraction_completeness,
    completion_contracts_for_campaign,
    extraction_completeness_by_target,
    vapor_contract_completeness,
)
from simulator.alphamelts_reference_pressure import (
    alphamelts_condensed_phase_pressure_bar,
    annotate_alphamelts_reference_pressure,
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
    OxygenReservoirState,
    OXIDE_SPECIES,
    OXIDE_TO_METAL,
    OverheadGas,
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX,
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS,
    ProcessInventory,
    STOICH_RATIOS,
)
from simulator.equilibrium import EquilibriumMixin
from simulator.evaporation import EvaporationMixin
from simulator.extraction import ExtractionMixin
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.melt_backend.sulfsat import (
    SulfSatGate,
    SulfurSaturationResult,
)
from simulator.chemistry.kernel import (
    ChemistryIntent,
    ChemistryKernel,
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
    OXYGEN_SINK_CHANNEL_MODE_KEY,
    ProviderRegistry,
    normalize_chemistry_kernel_config,
    normalize_oxygen_sink_channel_mode,
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

_PRESERVE_REFERENCE_T_K = object()
_VALID_DECISION_CHOICES = {
    DecisionType.ROOT_BRANCH: ('pyrolysis', 'mre_baseline'),
    DecisionType.PATH_AB: ('A', 'A_staged', 'B'),
    DecisionType.BRANCH_ONE_TWO: ('two', 'one'),
    DecisionType.TI_RETENTION: ('retain', 'extract'),
    DecisionType.C6_PROCEED: ('yes', 'no'),
    DecisionType.C7_PROCEED: ('yes', 'no'),
}
_RESOLVE_MELT_REDOX_GATE_AUTHORITY = object()
_MELT_REDOX_GATE_FALLBACK_HISTORY_MAXLEN = 256
DEGRADED_PATH_ENGAGEMENT_KEYS = (
    'condensation_antoine_extrapolation',
    'capture_budget_regularizer',
    'transport_d_ab_proxy',
    'unmeasured_alpha_evaporation_fallback',
    'pipe_m_avg_fallback',
)


def _deep_merge_condenser_geometry(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> Dict[str, Any]:
    """Deep-merge one condenser geometry layer with override precedence."""

    merged = copy.deepcopy(dict(base or {}))
    for key, value in dict(override or {}).items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge_condenser_geometry(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _canonicalize_condenser_geometry_stage_keys(
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    from simulator.overhead import canonical_stage_area_key

    resolved = copy.deepcopy(dict(config or {}))
    for stage_map_key in (
        'stage_area_ratios',
        'stage_area_ratio_sources',
    ):
        raw_stage_map = resolved.get(stage_map_key)
        if not isinstance(raw_stage_map, Mapping):
            continue
        canonical_stage_map: Dict[str, Any] = {}
        for stage, value in raw_stage_map.items():
            canonical_stage_map[canonical_stage_area_key(stage)] = (
                copy.deepcopy(value)
            )
        resolved[stage_map_key] = canonical_stage_map
    return resolved


@dataclass(frozen=True)
class _MeltRedoxLiquidusFloorFallback:
    source: str
    reason: str
    liquidus_status: Literal['unavailable', 'not_converged', 'invalid']


_MeltRedoxGateAuthority = (
    Mapping[str, Any] | _MeltRedoxLiquidusFloorFallback | None
)


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
    'clo4', 'so3', 'salt', 'salts',
    'perchlorate', 'perchlorates', 'sulfate', 'sulfates',
}
STAGE0_CHLORIDE_SALT_COMPONENTS = {
    'cl', 'nacl', 'kcl', 'halide', 'halides', 'nacl_kcl_salts',
}
# CaF2/MgF2 survive furnace T; HF-route defluorination (SiO2 + steam) is out-of-scope.
STAGE0_REFRACTORY_FLUORIDE_COMPONENTS = {
    'caf2', 'mgf2', 'fluorite',
}
STAGE0_VOLATILE_FLUORIDE_COMPONENTS = {
    'naf', 'fluoride', 'fluorides',
}
STAGE0_UNMODELED_NITRATE_MARKERS = frozenset({
    'nitrate', 'nitrates', 'no3',
})
STAGE0_CHLORIDE_SALT_ACCOUNT = 'terminal.stage0_chloride_salt_phase'
STAGE0_CHLORIDE_SALT_DISPOSITION = (
    'separated_chloride_salt_fouling_risk'
)
STAGE0_CARBONATE_COMPONENTS = frozenset({
    'carbonate', 'carbonates', 'carbonate_salts',
    'mgco3', 'caco3', 'feco3', 'na2co3', 'k2co3',
})
STAGE0_CATION_SULFATE_COMPONENTS = frozenset({
    'caso4', 'mgso4', 'feso4',
})
STAGE0_CATION_SULFATE_OXIDE_PRODUCTS = {
    'CaSO4': 'CaO',
    'MgSO4': 'MgO',
    'FeSO4': 'Fe2O3',
}
STAGE0_CATION_SULFATE_OXIDE_STOICH = {
    'CaSO4': {'feed': 1.0, 'C': 1.0, 'oxide': 1.0, 'SO2': 1.0, 'CO': 1.0},
    'MgSO4': {'feed': 1.0, 'C': 1.0, 'oxide': 1.0, 'SO2': 1.0, 'CO': 1.0},
    'FeSO4': {'feed': 2.0, 'C': 1.0, 'oxide': 1.0, 'SO2': 2.0, 'CO': 1.0},
}
STAGE0_CATION_SULFATE_SULFIDE_PRODUCTS = {
    'CaSO4': 'CaS',
    'MgSO4': 'MgS',
    'FeSO4': 'FeS',
}
STAGE0_CATION_SULFATE_SULFIDE_STOICH = {
    'CaSO4': {'feed': 1.0, 'C': 4.0, 'sulfide': 1.0, 'CO': 4.0},
    'MgSO4': {'feed': 1.0, 'C': 4.0, 'sulfide': 1.0, 'CO': 4.0},
    'FeSO4': {'feed': 1.0, 'C': 4.0, 'sulfide': 1.0, 'CO': 4.0},
}
STAGE0_CARBONATE_METAL_OXIDE_STOICH = (
    ('Mg', 'MgO', 1.0),
    ('Ca', 'CaO', 1.0),
    ('Fe', 'FeO', 1.0),
    ('Na', 'Na2O', 2.0),
    ('K', 'K2O', 2.0),
)
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
    'unreported_loi_residual', 'unreported_residual', 'loi_residual',
}
FO2_BUFFER_ACCOUNT = 'reservoir.fo2_buffer'
FE_REDOX_OXYGEN_SOURCE_OVERHEAD = 'overhead_gas'
FE_REDOX_OXYGEN_SOURCE_FO2_BUFFER = 'fo2_buffer'
FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS = (
    'evaporative_metal_loss_internal'
)
WALL_DEPOSIT_ACCOUNT = 'process.wall_deposit'
KRESS_CARMICHAEL_1991_REFERENCE = (
    'Kress and Carmichael 1991 Contrib Mineral Petrol 108:82-92 '
    'doi:10.1007/BF00307328'
)
FLOW_MASS_ACCOUNTS = (
    'process.cleaned_melt',
    C7_AL_CREDIT_ACCOUNT,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
    'process.raw_feedstock',
    'process.condensation_train',
    WALL_DEPOSIT_ACCOUNT,
    *PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS,
    'process.overhead_gas',
    'process.metal_phase',
    'process.reagent_inventory',
    'terminal.offgas',
    'terminal.stage0_salt_phase',
    STAGE0_CHLORIDE_SALT_ACCOUNT,
    'terminal.stage0_sulfide_matte',
    'terminal.drain_tap_material',
    'terminal.slag',
    CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
    OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
)
FLOW_MASS_EXCLUDED_ACCOUNTS = (
    'process.stage0_carbonate_feed',
    'process.stage0_perchlorate_feed',
    'process.stage0_salt_feed',
    'process.stage0_volatile_feed',
)
BACKEND_REACTIVE_ACCOUNTS = (
    'process.cleaned_melt',
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
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
OXYGEN_RESERVOIR_NOOP_MOL = 1e-15
OXYGEN_RESERVOIR_REDOX_SOURCE_MIN_FO2_LOG10_BAR = -1.0e11
OXYGEN_RESERVOIR_REDOX_SOURCE_MAX_FO2_LOG10_BAR = 1.0e11
# Coarse absolute ferric-fraction tripwire until SSO-R ch2 re-speciation
# samples implied and ledger speciation at the same boundary.
# TODO(SSO-R ch2 re-speciation): replace with the final respeciation gate.
FERRIC_DIVERGENCE_WARNING_THRESHOLD = 0.05
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
# TERMINAL_RUMP_CLASS_TOLERANCE_PCT: single source is simulator/accounting/queries.py
# (BUG-026 / SC-09 — removed an unused duplicate that previously lived here).
DEFAULT_OVERHEAD_HEADSPACE_CONFIG = {
    'enabled': False,
    'volume_m3': None,
    'temperature_model': 'melt',
    'temperature_offset_K': None,
    'bleed_model': 'poiseuille',
    'conductance_kg_s': None,
    'conductance_kg_s_per_bar': None,
    'downstream_pressure_bar': None,
    'liner_temperature_C': 1500.0,
}
DEFAULT_FREEZE_GATE_CONFIG = {
    'enabled': False,
}
KNUDSEN_NOT_APPLICABLE_ZERO_OVERHEAD_FLOW = 'not-applicable-zero-overhead-flow'
BACKEND_FALLBACK_EXCEPTIONS = (RuntimeError, ImportError)
STAGE0_DEFAULT_TEMP_RANGE_C = (20.0, 950.0)
STAGE0_CARBON_CLEANUP_TEMP_RANGE_C = (20.0, 1050.0)
STAGE0_FOULANT_PHASE1_TEMP_C = 1050.0
STAGE0_FOULANT_PHASE1_OVERHEAD_BAR = 0.2
STAGE0_FOULANT_PHASE2_TEMP_C = 1350.0
STAGE0_FOULANT_PHASE2_OVERHEAD_BAR = 0.001
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


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PoisonedHourState:
    """Failure state for an hour aborted after ledger commits."""

    hour: int
    committed_transition_count: int
    aborting_exception_summary: str


class PoisonedHourError(RuntimeError):
    """Raised when retry would replay a partially committed hour."""

    def __init__(self, state: PoisonedHourState):
        self.state = state
        super().__init__(
            f'simulator hour {state.hour} is poisoned after '
            f'{state.committed_transition_count} ledger transition(s) committed '
            f'before abort ({state.aborting_exception_summary}); retry refused; '
            'create a fresh simulator or reload the batch'
        )


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

    def __init__(
        self,
        melt_backend,
        setpoints: dict,
        feedstocks: dict,
        vapor_pressures: dict,
        *,
        materials: dict | None = None,
        allow_lab_geometry_temperature_profiles: bool = False,
    ):
        """
        Args:
            melt_backend: A MeltBackend instance (AlphaMELTS,
                          internal-analytical, etc.)
                          for thermodynamic equilibrium calculations.
            setpoints:    Campaign parameters loaded from setpoints.yaml.
                          May contain a top-level ``chemistry_kernel``
                          block whose ``allow_fallback_vapor`` flag
                          permits an explicitly registered vapor-pressure
                          fallback. Builtin Antoine/Ellingham is the
                          default pressure provider; VapoRock is diagnostic-only;
                          the flag defaults to ``False`` (loud
                          :class:`ProviderUnavailableError` instead of
                          silent fallback).
            feedstocks:   Feedstock compositions from feedstocks.yaml.
            vapor_pressures: Antoine parameters from vapor_pressures.yaml.
            materials: Material surface/liner data from materials.yaml.
        """
        self.backend = melt_backend
        self._last_backend_error = ''
        # Per-call outcome of EquilibriumResult evaluations
        # ('ok' / 'not_converged' / 'out_of_domain' / 'unavailable').
        # The latest value is a UI diagnostic; the history lets batch
        # evaluation classify a candidate that hit a real backend domain edge.
        self._last_backend_status = 'ok'
        self._backend_status_history: list[str] = []
        self._last_backend_diagnostics: Dict[str, Any] = {}
        self._last_out_of_domain_diagnostics: Dict[str, Any] = {}
        self._last_vapor_pressures_source: dict[str, str] = {}
        self._backend_failed = False
        self._stage0_carbon_cleanup_specs: list[dict] = []
        self._stage0_carbonate_decomposition_specs: list[dict] = []
        self._stage0_perchlorate_cleanup_specs: list[dict] = []
        self._stage0_foulant_diagnostics: list[dict] = []
        self._foulant_diagnostics_enabled: bool = True
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
        self.materials = copy.deepcopy(materials) if materials is not None else None
        self.lab_geometry = parse_lab_geometry(
            self.setpoints.get("lab_geometry"),
            allow_temperature_profiles=allow_lab_geometry_temperature_profiles,
        )
        self._base_species_formula_registry = self._load_species_formula_registry()
        self.species_formula_registry = dict(self._base_species_formula_registry)
        self.atom_ledger = self._new_atom_ledger()
        self.cost_ledger = CostLedger(
            import_context=CostImportContext.from_config(
                self.setpoints.get('cost_model')
            )
        )

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
        # VAPOR_PRESSURE authority is builtin Antoine/Ellingham.
        # VapoRock is retained as a diagnostic shadow only; it must not
        # provide the pressure dict consumed by evaporation.
        kernel_config = normalize_chemistry_kernel_config(
            setpoints.get('chemistry_kernel', {}) or {}
        )
        self.oxygen_sink_channel_mode = normalize_oxygen_sink_channel_mode(
            kernel_config.get(OXYGEN_SINK_CHANNEL_MODE_KEY)
        )
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
        self._overhead_condenser_geometry_config = (
            self._resolve_condenser_geometry_config()
        )
        self._freeze_gate_config = self._resolve_freeze_gate_config()
        self._freeze_gate_liquid_fraction_cache: Optional[Dict[str, Any]] = None
        self._freeze_gate_liquid_fraction_curve_memo: Dict[
            tuple,
            Dict[str, Any],
        ] = {}
        self._freeze_gate_curve_in_progress: bool = False
        self._last_freeze_gate_diagnostic: Dict[str, Any] = {}
        self._melt_redox_liquidus_gate_fallback_diagnostics: deque[
            Dict[str, Any]
        ] = deque(maxlen=_MELT_REDOX_GATE_FALLBACK_HISTORY_MAXLEN)
        self._melt_redox_liquidus_gate_fallback_hourly: deque[
            Dict[str, Any]
        ] = deque(maxlen=_MELT_REDOX_GATE_FALLBACK_HISTORY_MAXLEN)
        self._melt_redox_liquidus_gate_fallback_count = 0
        self._degraded_path_engagement: Dict[str, Dict[str, Any]] = {}
        self._melt_redox_gate_authority_this_tick: object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        )
        self._melt_redox_gate_authority_tick_hour: int | None = None
        self._poisoned_hour: PoisonedHourState | None = None
        self._last_overhead_gas_equilibrium: Dict[str, Any] = {}
        self._last_vapor_pressure_diagnostic: Dict[str, Any] = {}
        self._last_evaporation_flux_diagnostic: Dict[str, Any] = {}
        self._last_partial_melt_offgassing_diagnostic: Dict[str, Any] = {}
        self._last_extraction_completeness_diagnostic: Dict[str, Any] = {}
        self._last_overlap_evaporation_diagnostic: Dict[str, Any] = {}
        self._feedstock_recovered_reagent_kg_by_species: Dict[str, float] = {}
        self._non_feedstock_reagent_element_kg_by_account: Dict[
            str, Dict[str, float]
        ] = {}
        self._redox_source_terms_this_hr: Dict[str, float] = {}
        self._redox_source_context_this_hr: Dict[str, Any] = {}
        self._redox_source_delta_ln_this_hr = 0.0
        self._pt0_determinism_store: Any | None = None
        self._last_reduced_real_cache_state: str | None = None
        self._rump_expectation_warnings: list[str] = []
        # Shuttle-physics-gate refusals: the post-V1c JANAF Ellingham +
        # S1b shuttle T-acceptance gate refuses K→FeO at any practical
        # melt T and Na→FeO above the 1181.5 °C crossover.  When the
        # engine returns ``status='refused'`` the extraction caller
        # records the structured diagnostic here so the recipe's
        # silent no-op cannot mask an invalid operating regime.
        # Autoreview r3 P2 (2026-05-27): the prior code treated any
        # transition-less kernel result the same -- benign skip and
        # thermodynamic refusal were indistinguishable.
        self._last_shuttle_refusal_diagnostic: Dict[str, Any] = {}
        self._shuttle_refusal_history: list[Dict[str, Any]] = []
        self._last_c6_refusal_diagnostic: Dict[str, Any] = {}
        self._c6_campaign_refused = False
        self._last_c3_na_hold_adjustment: Dict[str, Any] = {}

        # --- Current state ---
        self.melt = MeltState()
        self.inventory = ProcessInventory()
        self.train = CondensationTrain.create_default()
        self.overhead = OverheadGas()

        # --- Batch record ---
        self.record = BatchRecord()
        self.energy_electrical_plus_evaporation_cumulative_kWh = 0.0
        self.energy_cumulative_breakdown_kWh: Dict[str, float] = {}
        self.oxygen_cumulative_kg = 0.0
        self._last_condensed_by_stage_species_delta: Dict[
            Tuple[int, str], float] = {}
        self._last_wall_deposit_by_segment_species_delta: Dict[
            Tuple[str, str], float] = {}
        self._last_impurity_delta: Dict[Tuple[int, str], float] = {}
        self._last_native_fe_partition_diagnostic: Dict[str, Any] = {}
        self._last_native_fe_saturation_event: Dict[str, Any] = {}
        self._native_fe_vapor_residual_capacity_mol_this_hr: float | None = None

        # --- Gas train feedback state ---
        self.O2_vented_cumulative_kg = 0.0      # Total O₂ vented to vacuum
        self.O2_stored_cumulative_kg = 0.0      # Total O₂ in accumulator
        self._mre_anode_O2_kg_this_hr = 0.0
        self._o2_bubbler_injected_kg = 0.0
        self._o2_bubbler_absorbed_kg = 0.0
        self._o2_bubbler_passthrough_kg = 0.0
        self._o2_bubbler_vented_kg = 0.0
        self._o2_bubbler_overhead_passthrough_pending_kg = 0.0
        self._o2_bubbler_external_o2_in_overhead_mol = 0.0
        self._o2_bubbler_injected_cumulative_kg = 0.0
        self._o2_bubbler_absorbed_cumulative_kg = 0.0
        self._o2_bubbler_passthrough_cumulative_kg = 0.0
        self._o2_bubbler_vented_cumulative_kg = 0.0
        self._last_o2_bubbler_diagnostic: Dict[str, Any] = {
            'status': 'ok',
            'reason': 'not_run',
        }
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
        self._mre_uncertified_yield: Dict[str, Any] = {}
        self._mre_ellingham_ladder_diagnostic: Dict[str, Any] = {}
        self._mre_voltage_step_idx = 0
        self._mre_hold_hours = 0
        self._mre_rung_ever_effective = False
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
        self._campaign_start_electrical_plus_evaporation_energy = 0.0
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
                materials=self.materials,
            )
            self._condensation_model.apply_setpoints_overrides(
                self.setpoints
            )
            if self.lab_geometry is not None:
                self._condensation_model.configure_lab_geometry(
                    self.lab_geometry
                )
        return self._condensation_model

    @property
    def overhead_model(self):
        if self._overhead_model is None:
            from simulator.overhead import OverheadGasModel
            self._overhead_model = OverheadGasModel(
                self._overhead_headspace_config,
                condenser_geometry_config=self._overhead_condenser_geometry_config,
                degraded_path_engagement_recorder=(
                    self._record_degraded_path_engagement
                ),
            )
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
        assert_feedstock_loadable(feedstock_key, fs)

        additives = dict(additives_kg or {})
        ledger_additives = dict(additives)
        self._activated_additive_reagents = set()
        self.species_formula_registry = self._registry_for_feedstock(fs)
        self.atom_ledger = self._new_atom_ledger()
        self.inventory = self._build_process_inventory(
            fs, mass_kg, feedstock_key=feedstock_key,
        )
        self._stage0_carbon_cleanup_specs = []
        self._stage0_carbonate_decomposition_specs = []
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
        self._backend_status_history = []
        self._last_backend_diagnostics = {}
        self._last_out_of_domain_diagnostics = {}
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
        self.melt.body = feedstock_body(fs)
        self.melt.p_total_mbar = self.melt.ambient_pressure_mbar
        self.melt.pO2_mbar = 0.0
        base_intrinsic_fO2_log = self._compute_intrinsic_melt_fO2()
        self.melt.fO2_log = base_intrinsic_fO2_log
        self.melt.melt_fO2_log = base_intrinsic_fO2_log
        self.melt.campaign = CampaignPhase.IDLE
        self.melt.hour = 0
        self.melt.campaign_hour = 0
        self.melt.update_total_mass()

        # Reset condensation train
        self.train = CondensationTrain.create_default()
        self.overhead = OverheadGas()
        self._last_overhead_gas_equilibrium = {}
        self._last_vapor_pressure_diagnostic = {}
        self._last_native_fe_partition_diagnostic = {}
        self._native_fe_vapor_residual_capacity_mol_this_hr = None
        self._rump_expectation_warnings = []
        self._last_shuttle_refusal_diagnostic = {}
        self._shuttle_refusal_history = []
        self._last_c6_refusal_diagnostic = {}
        self._c6_campaign_refused = False
        self._last_c3_na_hold_adjustment = {}
        self._c3_alkali_credit_drawn_kg_by_species = {}
        self._feedstock_recovered_reagent_kg_by_species = {}
        self._non_feedstock_reagent_element_kg_by_account = {}
        self._equipment = None
        self._configure_overhead_headspace()
        self._configure_freeze_gate()
        self._refresh_oxygen_reservoir_without_exchange(
            melt_intrinsic_fO2_log=base_intrinsic_fO2_log,
            reference_T_K=None,
        )

        # Record
        self.record = BatchRecord(
            feedstock_key=feedstock_key,
            feedstock_label=fs.get('label', feedstock_key),
            batch_mass_kg=mass_kg,
            additives_kg=additives,
            initial_inventory=self.inventory.copy(),
        )
        self.energy_electrical_plus_evaporation_cumulative_kWh = 0.0
        self.energy_cumulative_breakdown_kWh = {}
        self._energy_tracker = None
        self.oxygen_cumulative_kg = 0.0
        self.O2_vented_cumulative_kg = 0.0
        self.O2_stored_cumulative_kg = 0.0
        self._mre_anode_O2_kg_this_hr = 0.0
        self._o2_bubbler_injected_kg = 0.0
        self._o2_bubbler_absorbed_kg = 0.0
        self._o2_bubbler_passthrough_kg = 0.0
        self._o2_bubbler_vented_kg = 0.0
        self._o2_bubbler_overhead_passthrough_pending_kg = 0.0
        self._o2_bubbler_external_o2_in_overhead_mol = 0.0
        self._o2_bubbler_injected_cumulative_kg = 0.0
        self._o2_bubbler_absorbed_cumulative_kg = 0.0
        self._o2_bubbler_passthrough_cumulative_kg = 0.0
        self._o2_bubbler_vented_cumulative_kg = 0.0
        self._last_o2_bubbler_diagnostic = {
            'status': 'ok',
            'reason': 'not_run',
        }
        self._last_nominal_ramp = 0.0
        self._last_actual_ramp = 0.0
        self._last_throttle_reason = ''

        # Reset shuttle state
        self.shuttle_K_inventory_kg = 0.0
        self.shuttle_Na_inventory_kg = 0.0
        self.shuttle_cycle_K = 0
        # Reset thermite state
        self.thermite_Mg_inventory_kg = 0.0
        self.shuttle_cycle_Na = 0
        self._shuttle_injected_this_hr = 0.0
        self._shuttle_reduced_this_hr = 0.0
        self._shuttle_metal_this_hr = 0.0
        self._shuttle_phase = ''
        self._c7_al_credit_input_kg = 0.0
        self._c7_al_credit_funded = False
        self._last_c7_diagnostic = {}
        self._last_c7_refusal_diagnostic = {}
        self._c7_product_report = {}
        self._c7_aluminothermic_applied = False
        self._c7_ca_shuttle_applied = False

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

    def _reagent_account_policies(self) -> tuple[AccountPolicy, ...]:
        return (
            AccountPolicy.reservoir(
                FO2_BUFFER_ACCOUNT,
                credit_limit_kg_by_species={OXYGEN_SPECIES: 1e15},
            ),
            AccountPolicy.reservoir(
                'reservoir.reagent.C',
                credit_limit_kg_by_species={'C': 1e15},
            ),
            AccountPolicy.reservoir(
                'reservoir.reagent.Na',
                credit_limit_kg_by_species={'Na': 1e15},
            ),
            AccountPolicy.reservoir(
                'reservoir.reagent.K',
                credit_limit_kg_by_species={'K': 1e15},
            ),
        )

    def _new_atom_ledger(self) -> AtomLedger:
        return AtomLedger(
            registry=self.species_formula_registry,
            account_policies=(
                self._backend_account_policies()
                + self._reagent_account_policies()
            ),
            allowed_accounts=KNOWN_LEDGER_ACCOUNTS,
            allowed_account_prefixes=KNOWN_LEDGER_ACCOUNT_PREFIXES,
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
    # VAPOR_PRESSURE stays out of this table only because it needs the
    # simulator-owned vapor_pressures.yaml payload. It is still builtin
    # authoritative; VapoRock is wired separately as a diagnostic
    # shadow. See ``_register_vapor_pressure_pair``. The fourteen providers in
    # this table plus the separately wired builtin vapor-pressure provider are
    # the released builtin registration surface.
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
            'engines.builtin.ca_aluminothermic_step',
            'BuiltinCaAluminothermicStepProvider',
            (ChemistryIntent.CA_ALUMINOTHERMIC_STEP,),
            False,
            'CA_ALUMINOTHERMIC_STEP -- AUTHORITATIVE: optional C7 Ca '
            'aluminothermy + dedicated Ca capture.',
        ),
        (
            'engines.builtin.native_fe_saturation',
            'BuiltinNativeFeSaturationProvider',
            (ChemistryIntent.NATIVE_FE_SATURATION,),
            False,
            'NATIVE_FE_SATURATION -- AUTHORITATIVE: FeO -> Fe + 0.5 O2 '
            'pre-storm native-metal exsolution.',
        ),
        (
            'engines.builtin.fe_redox_respeciation',
            'BuiltinFeRedoxRespeciationProvider',
            (ChemistryIntent.FE_REDOX_RESPECIATION,),
            False,
            'FE_REDOX_RESPECIATION -- AUTHORITATIVE: 2FeO + 0.5O2 '
            '<-> Fe2O3 cleaned-melt repartition from scalar Kress91 fO2.',
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
            'engines.builtin.oxygen_bubbler',
            'BuiltinOxygenBubblerProvider',
            (ChemistryIntent.OXYGEN_BUBBLER,),
            False,
            'OXYGEN_BUBBLER -- AUTHORITATIVE: unabsorbed bubbler O2 '
            'moves from reservoir.fo2_buffer to process.overhead_gas.',
        ),
        (
            'engines.builtin.oxygen_reservoir_exchange',
            'BuiltinOxygenReservoirExchangeProvider',
            (ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE,),
            False,
            'OXYGEN_RESERVOIR_EXCHANGE -- AUTHORITATIVE: pure O2 move '
            'between reservoir.fo2_buffer and process.overhead_gas.',
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
            if class_name == 'BuiltinCondensationRouteProvider':
                provider = provider_cls(
                    wall_deposit_accounts=(
                        self._condensation_route_wall_deposit_accounts()
                    )
                )
            else:
                provider = (
                    provider_cls(self.vapor_pressures)
                    if needs_vapor_pressures
                    else provider_cls()
                )
            self._chem_registry.register_idempotent(provider, list(intents))

        # VAPOR_PRESSURE is wired separately from the builtin-registration
        # loop because it needs simulator-owned vapor_pressures.yaml and
        # a VapoRock diagnostic shadow.
        self._register_vapor_pressure_pair()

        # Register the AlphaMELTS diagnostic provider when the active
        # backend is an AlphaMELTSBackend (\goal ALPHAMELTS-DIAGNOSTIC-GATE,
        # #8). The provider wraps the today-hook adapter; if the user has
        # selected a different backend (internal-analytical / FactSAGE /
        # MAGEMin / VapoRock)
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
            oxygen_sink_channel_mode=self.oxygen_sink_channel_mode,
        )

    def _condensation_route_wall_deposit_accounts(self) -> tuple[str, ...]:
        if self.lab_geometry is None:
            return ()
        return self.lab_geometry.wall_deposit_accounts

    def _register_vapor_pressure_pair(self) -> None:
        """Wire builtin-authoritative VAPOR_PRESSURE plus VapoRock shadow.

        Builtin Antoine/Ellingham owns the pressure surface consumed by
        evaporation. VapoRock runs as a diagnostic shadow when available
        and may return non-authoritative/empty diagnostics without
        blocking the authoritative builtin result.

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

        builtin_provider = BuiltinVaporPressureProvider(self.vapor_pressures)
        self._chem_registry.register_idempotent(
            builtin_provider,
            [ChemistryIntent.VAPOR_PRESSURE],
        )

        # VapoRockProvider receives the same vapor_pressures.yaml payload
        # so its diagnostic output is filtered onto the simulator species
        # universe. It is shadow-only: the builtin result remains the
        # authoritative pressure dict even when VapoRock reports
        # non_authoritative or empty diagnostics.
        vaporock_provider = VapoRockProvider(
            vapor_pressure_data=self.vapor_pressures,
        )
        self._chem_registry.register_idempotent(
            vaporock_provider,
            [ChemistryIntent.VAPOR_PRESSURE],
            shadow=True,
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
        * Users may select a different backend (internal-analytical /
          FactSAGE / etc.);
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
        temperature_C_override: float | None = None,
    ) -> IntentResult:
        """Dispatch one intent through the kernel with melt-derived controls.

        Pulls ``temperature_C`` and ``pressure_bar`` from ``self.melt`` so
        every committing site quotes the same source-of-truth state. The
        temperature override is reserved for non-committing operating-point
        sweeps; callers must never commit a proposal evaluated under it.
        Returns the ``IntentResult``
        unchanged -- including ``transition``, ``diagnostic``,
        ``warnings``.

        F-B1: this is the dispatch half of the pre/post-interleave
        split.  Use :meth:`_dispatch_and_commit` instead when the site
        does NOT need bookkeeping between dispatch and commit.
        """
        kernel = self._require_chem_kernel()
        control_inputs = dict(control_inputs)
        temperature_C = (
            float(self.melt.temperature_C)
            if temperature_C_override is None
            else float(temperature_C_override)
        )
        pressure_bar = float(self.melt.p_total_mbar) / 1000.0
        store = _pt0_determinism_store_for(self)
        account_mol_overrides = None
        if store is not None and getattr(store, 'quantize_live_controls', False):
            controls = store.quantized_controls(self, fO2_log=fO2_log)
            if temperature_C_override is None:
                temperature_C = float(controls['temperature_C'])
            pressure_bar = float(controls['pressure_bar'])
            fO2_log = controls['fO2_log']
            if fO2_log is not None and 'melt_fO2_log' in control_inputs:
                control_inputs['melt_fO2_log'] = float(fO2_log)
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
        *,
        diagnostic: Mapping[str, Any] | None = None,
        control_inputs: Mapping[str, Any] | None = None,
        transition_source: str = '',
        transition_meta: Mapping[str, Any] | None = None,
    ) -> LedgerTransition:
        """Commit a proposal returned by :meth:`_dispatch_only`.

        F-B1: the commit half of the pre/post-interleave split.  The
        kernel re-runs the full pre-commit validator stack inside
        ``commit_batch`` (defence in depth) so this helper stays a
        thin pass-through.
        """
        kernel = self._require_chem_kernel()
        balances_before = self.atom_ledger.kg_by_account()
        transition = kernel.commit_batch(
            intent,
            proposal,
            transition_source=transition_source,
            transition_meta=transition_meta,
        )
        self._observe_o2_bubbler_external_o2_transition(
            intent,
            transition,
            balances_before,
        )
        self._observe_reagent_provenance_transition(
            transition,
            balances_before,
        )
        self._observe_cost_transition_best_effort(
            intent=intent,
            transition=transition,
            diagnostic=diagnostic,
            control_inputs=control_inputs,
            temperature_C=float(self.melt.temperature_C),
            strict=False,
        )
        return transition

    def _observe_reagent_provenance_transition(
        self,
        transition: LedgerTransition,
        balances_before: Mapping[str, Mapping[str, float]],
    ) -> None:
        non_feedstock_source_kg: Dict[str, float] = {}
        for lot in transition.debits:
            account = str(lot.account)
            account_before = balances_before.get(account, {}) or {}
            if account == 'process.reagent_inventory':
                for species, kg in lot.species_kg.items():
                    reagent = str(species)
                    debited_kg = max(0.0, float(kg))
                    if debited_kg <= 1.0e-12:
                        continue
                    total_before_kg = max(
                        0.0,
                        float(account_before.get(reagent, 0.0) or 0.0),
                    )
                    feedstock_kg = self._consume_feedstock_recovered_reagent(
                        reagent,
                        debited_kg,
                        total_before_kg,
                    )
                    non_feedstock_kg = max(0.0, debited_kg - feedstock_kg)
                    if non_feedstock_kg > 1.0e-12:
                        non_feedstock_source_kg[reagent] = (
                            non_feedstock_source_kg.get(reagent, 0.0)
                            + non_feedstock_kg
                        )
                continue
            tracked = getattr(
                self,
                '_non_feedstock_reagent_element_kg_by_account',
                {},
            )
            account_tracked = tracked.get(account, {}) if isinstance(tracked, dict) else {}
            for element in tuple(account_tracked):
                debited_element_kg = self._transition_lot_element_kg(
                    lot,
                    element,
                )
                if debited_element_kg <= 1.0e-12:
                    continue
                total_element_kg = self._account_element_kg(
                    account_before,
                    element,
                )
                moved_kg = self._consume_non_feedstock_reagent_element(
                    account,
                    element,
                    debited_element_kg,
                    total_element_kg,
                )
                if moved_kg > 1.0e-12:
                    non_feedstock_source_kg[element] = (
                        non_feedstock_source_kg.get(element, 0.0) + moved_kg
                    )
        for element, source_kg in non_feedstock_source_kg.items():
            self._credit_non_feedstock_reagent_element(
                transition,
                element,
                source_kg,
            )

    def _consume_feedstock_recovered_reagent(
        self,
        species: str,
        debited_kg: float,
        total_before_kg: float,
    ) -> float:
        balances = getattr(self, '_feedstock_recovered_reagent_kg_by_species', None)
        if not isinstance(balances, dict):
            balances = {}
            self._feedstock_recovered_reagent_kg_by_species = balances
        live_kg = max(0.0, float(balances.get(species, 0.0) or 0.0))
        if live_kg <= 1.0e-12 or debited_kg <= 1.0e-12 or total_before_kg <= 1.0e-12:
            return 0.0
        live_kg = min(live_kg, total_before_kg)
        consumed_kg = min(live_kg, debited_kg * live_kg / total_before_kg)
        remaining_kg = max(0.0, float(balances.get(species, 0.0) or 0.0) - consumed_kg)
        if remaining_kg <= 1.0e-12:
            balances.pop(species, None)
        else:
            balances[species] = remaining_kg
        return consumed_kg

    def _consume_non_feedstock_reagent_element(
        self,
        account: str,
        element: str,
        debited_element_kg: float,
        total_element_kg: float,
    ) -> float:
        balances = getattr(
            self,
            '_non_feedstock_reagent_element_kg_by_account',
            None,
        )
        if not isinstance(balances, dict):
            balances = {}
            self._non_feedstock_reagent_element_kg_by_account = balances
        account_balances = balances.get(account, {})
        if not isinstance(account_balances, dict):
            return 0.0
        live_kg = max(0.0, float(account_balances.get(element, 0.0) or 0.0))
        if live_kg <= 1.0e-12 or debited_element_kg <= 1.0e-12 or total_element_kg <= 1.0e-12:
            return 0.0
        live_kg = min(live_kg, total_element_kg)
        consumed_kg = min(live_kg, debited_element_kg * live_kg / total_element_kg)
        remaining_kg = max(
            0.0,
            float(account_balances.get(element, 0.0) or 0.0) - consumed_kg,
        )
        if remaining_kg <= 1.0e-12:
            account_balances.pop(element, None)
        else:
            account_balances[element] = remaining_kg
        if not account_balances:
            balances.pop(account, None)
        return consumed_kg

    def _credit_non_feedstock_reagent_element(
        self,
        transition: LedgerTransition,
        element: str,
        source_kg: float,
    ) -> None:
        credit_element_kg: list[tuple[str, float]] = []
        for lot in transition.credits:
            kg = self._transition_lot_element_kg(lot, element)
            if kg > 1.0e-12:
                credit_element_kg.append((str(lot.account), kg))
        total_credit_kg = sum(kg for _account, kg in credit_element_kg)
        if source_kg <= 1.0e-12 or total_credit_kg <= 1.0e-12:
            return
        for account, kg in credit_element_kg:
            self._add_non_feedstock_reagent_element(
                account,
                element,
                source_kg * kg / total_credit_kg,
            )

    def _add_non_feedstock_reagent_element(
        self,
        account: str,
        element: str,
        kg: float,
    ) -> None:
        if kg <= 1.0e-12:
            return
        balances = getattr(
            self,
            '_non_feedstock_reagent_element_kg_by_account',
            None,
        )
        if not isinstance(balances, dict):
            balances = {}
            self._non_feedstock_reagent_element_kg_by_account = balances
        account_balances = balances.setdefault(str(account), {})
        account_balances[str(element)] = (
            float(account_balances.get(str(element), 0.0) or 0.0)
            + float(kg)
        )

    def _transition_lot_element_kg(self, lot: Any, element: str) -> float:
        return self._species_mapping_element_kg(lot.species_kg, element)

    def _account_element_kg(
        self,
        species_kg: Mapping[str, float],
        element: str,
    ) -> float:
        return self._species_mapping_element_kg(species_kg, element)

    def _species_mapping_element_kg(
        self,
        species_kg: Mapping[str, float],
        element: str,
    ) -> float:
        total_kg = 0.0
        for species, kg in species_kg.items():
            amount_kg = max(0.0, float(kg or 0.0))
            if amount_kg <= 1.0e-12:
                continue
            total_kg += self._species_element_kg(str(species), element, amount_kg)
        return total_kg

    def _species_element_kg(
        self,
        species: str,
        element: str,
        species_kg: float,
    ) -> float:
        formula = resolve_species_formula(species, self.species_formula_registry)
        count = float(formula.elements.get(element, 0.0) or 0.0)
        if count <= 0.0:
            return 0.0
        element_formula = resolve_species_formula(element, self.species_formula_registry)
        return (
            float(species_kg)
            * count
            * element_formula.molar_mass_kg_per_mol()
            / formula.molar_mass_kg_per_mol()
        )

    def _cleaned_melt_reduction_source_terms_from_transition(
        self,
        transition: LedgerTransition,
        *,
        label: str,
        target_oxides: Tuple[str, ...],
    ) -> Dict[str, float]:
        target_set = {str(species) for species in target_oxides}
        debit_o2_equiv_mol = self._transition_account_o2_equiv_mol(
            transition,
            side='debits',
            account='process.cleaned_melt',
            species_filter=target_set,
        )
        credit_o2_equiv_mol = self._transition_account_o2_equiv_mol(
            transition,
            side='credits',
            account='process.cleaned_melt',
            species_filter=target_set,
        )
        o2_equiv_mol = -(debit_o2_equiv_mol - credit_o2_equiv_mol)
        if abs(o2_equiv_mol) <= OXYGEN_RESERVOIR_NOOP_MOL:
            return {}
        return {str(label): o2_equiv_mol}

    def _transition_account_o2_equiv_mol(
        self,
        transition: LedgerTransition,
        *,
        side: str,
        account: str,
        species_filter: set[str] | None = None,
    ) -> float:
        if side not in {'debits', 'credits'}:
            raise ValueError(f"transition side must be 'debits' or 'credits', got {side!r}")
        lots = transition.debits if side == 'debits' else transition.credits
        o2_equiv_mol = 0.0
        for lot in lots:
            if lot.account != account:
                continue
            for species, kg in lot.species_kg.items():
                species_name = str(species)
                if species_filter is not None and species_name not in species_filter:
                    continue
                formula = resolve_species_formula(
                    species_name,
                    self.species_formula_registry,
                )
                oxygen_atoms = float(formula.elements.get('O', 0.0) or 0.0)
                if oxygen_atoms <= 0.0:
                    continue
                species_mol = float(kg) / formula.molar_mass_kg_per_mol()
                o2_equiv_mol += 0.5 * oxygen_atoms * species_mol
        return o2_equiv_mol

    def _transition_species_mol(
        self,
        transition: LedgerTransition,
        *,
        side: str,
        account: str,
        species: str,
    ) -> float:
        if side not in {'debits', 'credits'}:
            raise ValueError(f"transition side must be 'debits' or 'credits', got {side!r}")
        lots = transition.debits if side == 'debits' else transition.credits
        formula = resolve_species_formula(species, self.species_formula_registry)
        mol = 0.0
        for lot in lots:
            if lot.account != account:
                continue
            kg = float(lot.species_kg.get(species, 0.0) or 0.0)
            if kg <= 0.0:
                continue
            mol += kg / formula.molar_mass_kg_per_mol()
        return mol

    def _mre_anode_o2_redox_source_terms_from_transition(
        self,
        transition: LedgerTransition,
        *,
        label: str,
    ) -> Dict[str, float]:
        o2_mol = self._transition_species_mol(
            transition,
            side='credits',
            account=OXYGEN_MRE_ANODE_ACCOUNT,
            species=OXYGEN_SPECIES,
        )
        if abs(o2_mol) <= OXYGEN_RESERVOIR_NOOP_MOL:
            return {}
        return {str(label): -o2_mol}

    def _apply_mre_anode_o2_redox_source_terms(
        self,
        transition: LedgerTransition,
        *,
        label: str,
        exchange_direction: str,
    ) -> OxygenReservoirState | None:
        terms = self._mre_anode_o2_redox_source_terms_from_transition(
            transition,
            label=label,
        )
        if not terms:
            return None
        return self._apply_oxygen_reservoir_redox_source_terms(
            terms,
            exchange_direction=exchange_direction,
        )

    def _evaporative_redox_source_terms_from_transition(
        self,
        transition: LedgerTransition,
    ) -> Dict[str, float]:
        prefix = 'evaporate_'
        if not transition.name.startswith(prefix):
            return {}
        vapor_species = transition.name[len(prefix):]
        vapor_formula = resolve_species_formula(
            vapor_species,
            self.species_formula_registry,
        )
        vapor_oxygen_atoms = float(vapor_formula.elements.get('O', 0.0) or 0.0)
        overhead_o2_credit_mol = self._transition_species_mol(
            transition,
            side='credits',
            account='process.overhead_gas',
            species=OXYGEN_SPECIES,
        )
        overhead_o2_debit_mol = self._transition_species_mol(
            transition,
            side='debits',
            account='process.overhead_gas',
            species=OXYGEN_SPECIES,
        )
        net_overhead_o2_mol = overhead_o2_credit_mol - overhead_o2_debit_mol
        if vapor_oxygen_atoms > 0.0:
            if abs(net_overhead_o2_mol) <= OXYGEN_RESERVOIR_NOOP_MOL:
                return {}
            return {'redox_source:evaporative_oxygen_loss': -net_overhead_o2_mol}

        # Elemental vapor classes (Na/K/Fe/Mg/Ca in current data) are
        # ledger-balanced as parent oxide -> metal vapor + O2.  The metal
        # loss leaves the debited oxide oxygen behind in the melt redox
        # couple, so the fO2 source is positive and derives from the actual
        # cleaned-melt debit.  If a future provider debits a reduced metal
        # species directly, this term naturally falls to zero.
        cleaned_melt_debit_o2_equiv_mol = self._transition_account_o2_equiv_mol(
            transition,
            side='debits',
            account='process.cleaned_melt',
        )
        cleaned_melt_credit_o2_equiv_mol = self._transition_account_o2_equiv_mol(
            transition,
            side='credits',
            account='process.cleaned_melt',
        )
        metal_loss_o2_equiv_mol = (
            cleaned_melt_debit_o2_equiv_mol
            - cleaned_melt_credit_o2_equiv_mol
        )
        if abs(metal_loss_o2_equiv_mol) <= OXYGEN_RESERVOIR_NOOP_MOL:
            return {}
        return {'redox_source:evaporative_metal_loss': metal_loss_o2_equiv_mol}

    def _apply_evaporative_redox_source_terms(
        self,
        transition: LedgerTransition,
        *,
        exchange_direction: str,
    ) -> OxygenReservoirState | None:
        terms = self._evaporative_redox_source_terms_from_transition(transition)
        if not terms:
            return None
        internal_o2_mol = max(
            0.0,
            float(terms.get('redox_source:evaporative_metal_loss', 0.0) or 0.0),
        )
        if internal_o2_mol > OXYGEN_RESERVOIR_NOOP_MOL:
            self._fe_redox_internal_o2_capacity_mol_this_hr = (
                float(
                    getattr(
                        self,
                        '_fe_redox_internal_o2_capacity_mol_this_hr',
                        0.0,
                    )
                )
                + internal_o2_mol
            )
        return self._apply_oxygen_reservoir_redox_source_terms(
            terms,
            exchange_direction=exchange_direction,
        )

    def _c6_back_reduction_redox_source_terms_from_transition(
        self,
        transition: LedgerTransition,
        *,
        label: str,
    ) -> Dict[str, float]:
        si_mol = self._transition_species_mol(
            transition,
            side='credits',
            account='process.metal_phase',
            species='Si',
        )
        al_mol = self._transition_species_mol(
            transition,
            side='debits',
            account='process.metal_phase',
            species='Al',
        )
        sio2 = resolve_species_formula('SiO2', self.species_formula_registry)
        al2o3 = resolve_species_formula('Al2O3', self.species_formula_registry)
        si_reduction_o2 = 0.5 * float(sio2.elements.get('O', 0.0) or 0.0) * si_mol
        al_oxidation_o2 = (
            0.5
            * float(al2o3.elements.get('O', 0.0) or 0.0)
            / float(al2o3.elements.get('Al', 1.0) or 1.0)
            * al_mol
        )
        o2_equiv_mol = al_oxidation_o2 - si_reduction_o2
        # The 4 Al : 3 Si back-reduction is stoichiometrically net-zero in
        # O2-equivalent; the kg<->mol round trip leaves a float residual
        # that GROWS with dose and can exceed the absolute NOOP floor at
        # production-scale thermite (grok ch2b review: ~1.4e-14 mol at
        # 50-100 kg Mg), leaking a spurious back-reduction label + fO2
        # nudge. Snap to zero when the net is float noise RELATIVE to the
        # gross O2 traffic of the transition — this preserves the
        # nets-to-zero contract at any dose without masking a real
        # stoichiometric imbalance (which scales with gross, not eps).
        gross_o2_mol = abs(al_oxidation_o2) + abs(si_reduction_o2)
        if abs(o2_equiv_mol) <= max(
            OXYGEN_RESERVOIR_NOOP_MOL,
            1e-12 * gross_o2_mol,
        ):
            return {}
        return {str(label): o2_equiv_mol}

    def _apply_c6_back_reduction_redox_source_terms(
        self,
        transition: LedgerTransition,
        *,
        label: str,
        exchange_direction: str,
    ) -> OxygenReservoirState | None:
        terms = self._c6_back_reduction_redox_source_terms_from_transition(
            transition,
            label=label,
        )
        if not terms:
            return None
        return self._apply_oxygen_reservoir_redox_source_terms(
            terms,
            exchange_direction=exchange_direction,
        )

    def _c7_aluminothermic_redox_source_terms_from_transition(
        self,
        transition: LedgerTransition,
        *,
        label: str,
    ) -> Dict[str, float]:
        ca_mol = self._transition_species_mol(
            transition,
            side='credits',
            account='process.overhead_gas',
            species='Ca',
        )
        if ca_mol <= OXYGEN_RESERVOIR_NOOP_MOL:
            return {}
        in_situ_al_mol = self._transition_species_mol(
            transition,
            side='debits',
            account='process.metal_phase',
            species='Al',
        )
        al2o3 = resolve_species_formula('Al2O3', self.species_formula_registry)
        al_oxidation_o2 = (
            0.5
            * float(al2o3.elements.get('O', 0.0) or 0.0)
            / float(al2o3.elements.get('Al', 1.0) or 1.0)
            * in_situ_al_mol
        )
        o2_equiv_mol = al_oxidation_o2 - (0.5 * ca_mol)
        if abs(o2_equiv_mol) <= OXYGEN_RESERVOIR_NOOP_MOL:
            return {}
        return {str(label): o2_equiv_mol}

    def _apply_c7_aluminothermic_redox_source_terms(
        self,
        transition: LedgerTransition,
        *,
        label: str,
        exchange_direction: str,
    ) -> OxygenReservoirState | None:
        terms = self._c7_aluminothermic_redox_source_terms_from_transition(
            transition,
            label=label,
        )
        if not terms:
            return None
        return self._apply_oxygen_reservoir_redox_source_terms(
            terms,
            exchange_direction=exchange_direction,
        )

    def _apply_transition_redox_source_terms(
        self,
        transition: LedgerTransition,
        *,
        label: str,
        target_oxides: Tuple[str, ...],
        exchange_direction: str,
    ) -> OxygenReservoirState | None:
        terms = self._cleaned_melt_reduction_source_terms_from_transition(
            transition,
            label=label,
            target_oxides=target_oxides,
        )
        if not terms:
            return None
        return self._apply_oxygen_reservoir_redox_source_terms(
            terms,
            exchange_direction=exchange_direction,
        )

    @staticmethod
    def _composed_oxygen_reservoir_direction(
        existing_direction: str,
        redox_direction: str,
        *,
        skip_reason: str = '',
    ) -> str:
        # Pipe-delimited components preserve the same-hour headspace exchange
        # label while appending the redox source-term outcome.
        redox_component = str(redox_direction or 'redox_source_terms')
        if skip_reason:
            redox_component = f'{redox_component}:skipped:{skip_reason}'
        components = [
            part for part in str(existing_direction or '').split('|') if part
        ]
        if redox_component not in components:
            components.append(redox_component)
        return '|'.join(components)

    def _accumulate_redox_source_terms_for_hour(
        self,
        attr_name: str,
        terms: Mapping[str, float],
    ) -> None:
        if terms and not getattr(self, '_redox_source_context_this_hr', {}):
            self._redox_source_context_this_hr = (
                self._redox_source_context_for_current_state()
            )
        aggregate = getattr(self, attr_name, None)
        if aggregate is None:
            aggregate = {}
            setattr(self, attr_name, aggregate)
        for label, mol in terms.items():
            key = str(label)
            aggregate[key] = float(aggregate.get(key, 0.0)) + float(mol)

    def _redox_source_context_for_current_state(self) -> Dict[str, Any]:
        campaign = getattr(self.melt, 'campaign', CampaignPhase.IDLE)
        return {
            'campaign': getattr(campaign, 'name', str(campaign)),
            'hour': int(getattr(self.melt, 'hour', 0)),
            'campaign_hour': int(getattr(self.melt, 'campaign_hour', 0)),
        }

    def _stamp_redox_source_context_for_current_state(
        self,
        *,
        force: bool = False,
    ) -> None:
        terms = dict(getattr(self, '_redox_source_terms_this_hr', {}) or {})
        if terms and (
            force or not getattr(self, '_redox_source_context_this_hr', {})
        ):
            self._redox_source_context_this_hr = (
                self._redox_source_context_for_current_state()
            )

    def _record_redox_source_skip_reasons_for_hour(
        self,
        terms: Mapping[str, float],
        reason: str,
    ) -> None:
        reasons = getattr(self, '_redox_source_skip_reasons_this_hr', None)
        if reasons is None:
            reasons = {}
            self._redox_source_skip_reasons_this_hr = reasons
        for label in terms:
            key = str(label)
            existing = str(reasons.get(key, ''))
            parts = [part for part in existing.split('|') if part]
            if reason not in parts:
                parts.append(reason)
            reasons[key] = '|'.join(parts)

    def _record_cost_diagnostic_error(
        self,
        context: str,
        exc: Exception,
    ) -> None:
        message = (
            f"cost_observation_error: {context}: "
            f"{type(exc).__name__}: {exc}"
        )
        try:
            warnings = getattr(self.cost_ledger, "_warnings", None)
            if isinstance(warnings, list):
                warnings.append(message)
        except Exception:
            pass
        _LOGGER.warning("%s", message, exc_info=True)

    def _observe_cost_transition_best_effort(self, **kwargs: Any) -> None:
        try:
            self.cost_ledger.observe_transition(**kwargs)
        except Exception as exc:  # noqa: BLE001 -- cost is diagnostic-only
            self._record_cost_diagnostic_error("observe_transition", exc)

    def _move_cost_inventory_lots_best_effort(self, **kwargs: Any) -> None:
        try:
            self.cost_ledger.move_inventory_lots(**kwargs)
        except Exception as exc:  # noqa: BLE001 -- cost is diagnostic-only
            self._record_cost_diagnostic_error("move_inventory_lots", exc)

    def _move_cost_product_lots_best_effort(self, **kwargs: Any) -> None:
        try:
            self.cost_ledger.move_product_lots(**kwargs)
        except Exception as exc:  # noqa: BLE001 -- cost is diagnostic-only
            self._record_cost_diagnostic_error("move_product_lots", exc)

    def _dispatch_and_commit(
        self,
        intent: ChemistryIntent,
        *,
        control_inputs: Mapping[str, Any],
        transition_source: str = '',
        transition_meta: Mapping[str, Any] | None = None,
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
        self._commit_proposal(
            intent,
            proposal,
            diagnostic=result.diagnostic,
            control_inputs=control_inputs,
            transition_source=transition_source,
            transition_meta=transition_meta,
        )
        return result

    def _o2_bubbler_external_o2_overhead_mol(self) -> float:
        return max(
            0.0,
            float(getattr(
                self,
                '_o2_bubbler_external_o2_in_overhead_mol',
                0.0,
            ) or 0.0),
        )

    def _observe_o2_bubbler_external_o2_transition(
        self,
        intent: ChemistryIntent,
        transition: LedgerTransition,
        balances_before: Mapping[str, Mapping[str, float]],
    ) -> None:
        external_o2_mol = self._o2_bubbler_external_o2_overhead_mol()
        o2_molar_mass = OXYGEN_MOLAR_MASS_KG_PER_MOL
        overhead_before_kg = float(
            (balances_before.get('process.overhead_gas', {}) or {}).get(
                OXYGEN_SPECIES,
                0.0,
            )
            or 0.0
        )
        overhead_before_mol = (
            max(0.0, overhead_before_kg / o2_molar_mass)
            if o2_molar_mass > 0.0
            else 0.0
        )
        overhead_debit_mol = self._transition_species_mol(
            transition,
            side='debits',
            account='process.overhead_gas',
            species=OXYGEN_SPECIES,
        )
        if overhead_debit_mol > OXYGEN_RESERVOIR_NOOP_MOL:
            external_before_mol = min(external_o2_mol, overhead_before_mol)
            if (
                external_before_mol > OXYGEN_RESERVOIR_NOOP_MOL
                and overhead_before_mol > OXYGEN_RESERVOIR_NOOP_MOL
            ):
                consumed_external_mol = min(
                    external_before_mol,
                    overhead_debit_mol * external_before_mol / overhead_before_mol,
                )
                external_o2_mol = max(0.0, external_o2_mol - consumed_external_mol)

        if intent == ChemistryIntent.OXYGEN_BUBBLER:
            external_o2_mol += self._transition_species_mol(
                transition,
                side='credits',
                account='process.overhead_gas',
                species=OXYGEN_SPECIES,
            )

        live_overhead_o2_mol = max(
            0.0,
            float(
                self.atom_ledger.mol_by_account('process.overhead_gas').get(
                    OXYGEN_SPECIES,
                    0.0,
                )
                or 0.0
            ),
        )
        self._o2_bubbler_external_o2_in_overhead_mol = min(
            max(0.0, external_o2_mol),
            live_overhead_o2_mol,
        )

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

    def _resolve_condenser_geometry_config(
        self, campaign: Optional[CampaignPhase] = None
    ) -> Dict[str, Any]:
        config = copy.deepcopy(
            dict(self.setpoints.get('condenser_geometry', {}) or {})
        )
        campaign_key = campaign.name if campaign is not None else None
        if campaign_key:
            campaign_cfg = (
                self.setpoints.get('campaigns', {}) or {}
            ).get(campaign_key, {}) or {}
            config = _deep_merge_condenser_geometry(
                config,
                dict(campaign_cfg.get('condenser_geometry', {}) or {}),
            )
            runtime_override = self.campaign_mgr.overrides.get(campaign_key, {})
            config = _deep_merge_condenser_geometry(
                config,
                dict(runtime_override.get('condenser_geometry', {}) or {})
            )
        return _canonicalize_condenser_geometry_stage_keys(config)

    def _configure_overhead_headspace(
        self, campaign: Optional[CampaignPhase] = None
    ) -> None:
        self._overhead_headspace_config = (
            self._resolve_overhead_headspace_config(campaign)
        )
        self._overhead_condenser_geometry_config = (
            self._resolve_condenser_geometry_config(campaign)
        )
        if self._overhead_model is not None:
            self._overhead_model.configure_condenser_geometry(
                self._overhead_condenser_geometry_config)
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
        previous_config = getattr(self, '_freeze_gate_config', None)
        self._freeze_gate_config = self._resolve_freeze_gate_config(campaign)
        self._freeze_gate_liquid_fraction_cache = None
        if previous_config != self._freeze_gate_config:
            self._freeze_gate_liquid_fraction_curve_memo = {}
        self._freeze_gate_curve_in_progress = False
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

    @staticmethod
    def _normalize_condensation_carrier_gas(
        value: Any,
        *,
        allow_unset: bool = True,
    ) -> str:
        if value is None:
            return '' if allow_unset else 'N2'
        text = str(value).strip()
        if not text:
            if allow_unset:
                return ''
            raise ValueError(
                'condensation carrier_gas must be non-empty when provided'
            )
        upper = text.upper().replace(' ', '').replace('_', '').replace('-', '')
        if upper in {'N2', 'PN2', 'N2SWEEP', 'PN2SWEEP'}:
            return 'N2'
        if upper in {'AR', 'PAR'}:
            return 'Ar'
        if upper in {'CO2', 'PCO2', 'CO2BACKPRESSURE'}:
            return 'CO2'
        if upper.endswith('%CO2'):
            try:
                co2_percent = float(upper[:-4])
            except ValueError:
                co2_percent = 0.0
            if 0.0 < co2_percent <= 100.0:
                return 'CO2'
        raise ValueError(
            f'Unsupported condensation carrier_gas {value!r}; supported '
            'carrier gases: N2/pN2, Ar/pAr, CO2/pCO2'
        )

    def _resolve_condensation_carrier_gas(self) -> str:
        background = self._normalize_condensation_carrier_gas(
            getattr(self.melt, 'background_gas_species', ''))
        if background:
            return background

        atmosphere_name = str(getattr(self.melt.atmosphere, 'name', '') or '')
        ambient_atmosphere = str(
            getattr(self.melt, 'ambient_atmosphere', '') or '')
        if (
            atmosphere_name == 'CO2_BACKPRESSURE'
            or self._normalize_condensation_carrier_gas(ambient_atmosphere) == 'CO2'
        ):
            return 'CO2'

        campaign_name = str(getattr(self.melt.campaign, 'name', '') or '')
        campaign_keys = {
            'C2A': ('C2A_continuous', 'C2A'),
            'C2A_STAGED': ('C2A_staged', 'C2A_STAGED'),
        }.get(campaign_name, (campaign_name,))
        campaigns = self.setpoints.get('campaigns', {}) or {}
        if isinstance(campaigns, Mapping):
            for key in campaign_keys:
                cfg = campaigns.get(key, {}) or {}
                if not isinstance(cfg, Mapping):
                    continue
                if 'carrier_gas' not in cfg or cfg.get('carrier_gas') is None:
                    continue
                carrier = self._normalize_condensation_carrier_gas(
                    cfg.get('carrier_gas'),
                    allow_unset=False,
                )
                if carrier:
                    return carrier

        return 'N2'

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
            stage_area_m2_by_stage=transport['stage_area_m2_by_stage'],
            stage_area_geometry_provenance_notice=transport.get(
                'stage_area_geometry_provenance_notice', {}),
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
            carrier_gas=self._resolve_condensation_carrier_gas(),
            campaign_name=getattr(self.melt.campaign, 'name', ''),
            campaign_hour=float(self.melt.campaign_hour),
        )

    def _apply_c2a_knudsen_pressure_adjustment(self) -> None:
        if self.melt.campaign not in (
            CampaignPhase.C2A,
            CampaignPhase.C2A_STAGED,
        ):
            return

        from simulator.campaigns import (
            C2A_STAGED_PN2_SWEEP_MAX_MBAR,
            C2A_STAGED_PN2_SWEEP_MIN_MBAR,
        )

        carrier_gas = self._resolve_condensation_carrier_gas()
        adjustment = self.condensation_model.adjust_c2a_pressure_setpoint(
            requested_p_total_mbar=float(self.melt.p_total_mbar),
            pO2_mbar=float(self.melt.pO2_mbar),
            gas_temperature_C=(
                self.overhead_model.resolve_pipe_temperature_C(self.melt)
            ),
            pipe_diameter_m=self.overhead_model.pipe_diameter_m,
            pN2_min_mbar=C2A_STAGED_PN2_SWEEP_MIN_MBAR,
            pN2_max_mbar=C2A_STAGED_PN2_SWEEP_MAX_MBAR,
            carrier_gas=carrier_gas,
        )
        if adjustment.get('status') != 'applied':
            return

        applied_pN2_mbar = float(adjustment['applied_pN2_mbar'])
        applied_total_mbar = float(adjustment['applied_p_total_mbar'])
        self.melt.p_total_mbar = applied_total_mbar
        self.melt.background_gas_species = carrier_gas
        self.melt.background_gas_mole_fraction = 1.0
        self.overhead.composition[carrier_gas] = applied_pN2_mbar
        self.overhead.pressure_mbar = max(
            float(self.overhead.pressure_mbar),
            applied_total_mbar,
        )
        staged_control = self.campaign_mgr.last_c2a_staged_gas_control
        if isinstance(staged_control, dict):
            staged_control['pN2_mbar'] = applied_pN2_mbar
            staged_control['p_total_mbar'] = applied_total_mbar
            staged_control['knudsen_pressure_adjustment'] = dict(adjustment)
        self.melt.validate_melt_pressures()

    def _active_lab_schedule(self) -> Mapping[str, Any] | None:
        getter = getattr(self.campaign_mgr, "_lab_schedule", None)
        if not callable(getter):
            return None
        return getter(self.melt.campaign)

    def _active_surface_temperature_schedule(
        self,
    ) -> Mapping[str, Any]:
        schedule = self._active_lab_schedule()
        if schedule is None:
            return {}
        raw = schedule.get("surface_temperature_C", {})
        return raw if isinstance(raw, Mapping) else {}

    def validate_lab_surface_temperature_resolver(self) -> None:
        surface_schedule = self._active_surface_temperature_schedule()
        if self.lab_geometry is None:
            if surface_schedule:
                raise LabGeometryError(
                    "lab_surface_temperature_schedule_without_geometry",
                    "lab_schedule.surface_temperature_C requires lab_geometry",
                )
            return
        self._resolve_lab_surface_temperatures(
            surface_schedule,
            sample_time_h=0.0,
        )

    def _resolve_lab_surface_temperatures(
        self,
        surface_schedule: Mapping[str, Any],
        *,
        sample_time_h: float,
    ) -> dict[str, float]:
        if self.lab_geometry is None:
            if surface_schedule:
                raise LabGeometryError(
                    "lab_surface_temperature_schedule_without_geometry",
                    "lab_schedule.surface_temperature_C requires lab_geometry",
                )
            return {}
        temperatures_C: dict[str, float] = {}
        for surface in self.lab_geometry.surfaces:
            profile_key = str(getattr(surface, "temperature_profile", "") or "")
            if profile_key:
                if profile_key not in surface_schedule:
                    raise LabScheduleValidationError(
                        "lab_schedule_missing_surface_temperature: "
                        f"{profile_key}"
                    )
                points = surface_schedule[profile_key]
                temperatures_C[surface.surface_id] = interpolate_schedule_points(
                    points,
                    sample_time_h,
                )
                continue
            if surface.surface_id in surface_schedule:
                temperatures_C[surface.surface_id] = interpolate_schedule_points(
                    surface_schedule[surface.surface_id],
                    sample_time_h,
                )
                continue
            if surface.temperature_C is None:
                raise LabGeometryError(
                    "missing_lab_surface_temperature_schedule",
                    (
                        f"{surface.surface_id}: temperature_C/wall_temperature_C "
                        "or lab_schedule.surface_temperature_C profile is required"
                    ),
                )
            temperatures_C[surface.surface_id] = float(surface.temperature_C)
        return temperatures_C

    def _apply_lab_surface_temperatures(self, *, sample_time_h: float) -> None:
        surface_schedule = self._active_surface_temperature_schedule()
        if self.lab_geometry is None:
            if surface_schedule:
                raise LabGeometryError(
                    "lab_surface_temperature_schedule_without_geometry",
                    "lab_schedule.surface_temperature_C requires lab_geometry",
                )
            return
        temperatures_C = self._resolve_lab_surface_temperatures(
            surface_schedule,
            sample_time_h=sample_time_h,
        )
        if temperatures_C:
            self.condensation_model.update_pipe_segment_temperatures(
                temperatures_C
            )

    def _headspace_downstream_pressure_bar(self) -> float:
        configured = self._overhead_headspace_config.get(
            'downstream_pressure_bar')
        if configured is not None:
            configured_pressure_bar = float(configured)
            if not math.isfinite(configured_pressure_bar):
                return math.nan
            return max(0.0, configured_pressure_bar)
        atmosphere_name = getattr(self.melt.atmosphere, 'name', '')
        if atmosphere_name in {
            'CONTROLLED_O2',
            'CONTROLLED_O2_FLOW',
            'O2_BACKPRESSURE',
        }:
            return max(0.0, float(self.melt.pO2_mbar) / 1000.0)
        return 0.0

    def _sync_c2a_staged_overhead_gas_control(self) -> None:
        control = getattr(
            self.campaign_mgr, 'last_c2a_staged_gas_control', None)
        if self.melt.campaign != CampaignPhase.C2A_STAGED:
            return
        if not isinstance(control, Mapping):
            return

        pO2_mbar = max(0.0, float(control.get('pO2_mbar', 0.0) or 0.0))
        p_total_mbar = max(
            0.0, float(control.get('p_total_mbar', 0.0) or 0.0))
        pN2_mbar = max(
            0.0,
            float(control.get('pN2_mbar', p_total_mbar - pO2_mbar) or 0.0),
        )

        composition: Dict[str, float] = {}
        if pN2_mbar > 0.0:
            composition['N2'] = pN2_mbar
        if pO2_mbar > 0.0:
            composition['O2'] = pO2_mbar
        self.overhead.composition = composition
        self.overhead.pressure_mbar = p_total_mbar

    def _headspace_bleed_conductance_kg_s(
        self,
        *,
        species_kg_for_M_avg: Optional[Mapping[str, float]] = None,
    ) -> float:
        configured = self._overhead_headspace_config.get('conductance_kg_s')
        if configured is None:
            configured = self._overhead_headspace_config.get(
                'conductance_kg_s_per_bar')
        if configured is not None:
            return max(0.0, float(configured))
        if species_kg_for_M_avg is None:
            species_kg_for_M_avg = self._overhead_holdup_species_kg()
        p_mean_Pa = max(float(self.melt.p_total_mbar) * 100.0, 1.0)
        return max(
            0.0,
            float(self.overhead_model._pipe_conductance(
                p_mean_Pa,
                self.melt.temperature_C,
                species_kg_for_M_avg=species_kg_for_M_avg,
            )),
        )

    def _headspace_bleed_conductance_kg_s_per_bar(self) -> float:
        return self._headspace_bleed_conductance_kg_s()

    def _overhead_holdup_mol(self) -> Dict[str, float]:
        return {
            species: float(mol)
            for species, mol in self.atom_ledger.mol_by_account(
                'process.overhead_gas').items()
            if float(mol) > 0.0
        }

    def _overhead_holdup_species_kg(
        self,
        holdup_mol: Optional[Mapping[str, float]] = None,
    ) -> Dict[str, float]:
        basis = self._overhead_holdup_mol() if holdup_mol is None else holdup_mol
        species_kg: Dict[str, float] = {}
        for species, mol in dict(basis or {}).items():
            amount_mol = max(0.0, float(mol))
            if amount_mol <= 0.0:
                continue
            formula = resolve_species_formula(
                species,
                self.atom_ledger.registry,
            )
            species_kg[species] = amount_mol * formula.molar_mass_kg_per_mol()
        return species_kg

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

    def _oxygen_exchange_config(self) -> Dict[str, Any]:
        sso_r = self.setpoints.get('sso_r', {}) or {}
        oxygen_exchange = {}
        if isinstance(sso_r, Mapping):
            oxygen_exchange = dict(sso_r.get('oxygen_exchange', {}) or {})
        return oxygen_exchange

    def _oxygen_exchange_k_m_s(self, T_K: float) -> tuple[float, str]:
        config = self._oxygen_exchange_config()
        k_ref = float(config.get('k_O_ref_m_s', 2.0e-5))
        k_min = float(config.get('k_O_min_m_s', 5.0e-6))
        k_max = float(config.get('k_O_max_m_s', 5.0e-5))
        T_ref = float(config.get('T_ref_K', 1773.15))
        Ea = float(config.get('Ea_J_mol', 150000.0))
        if min(k_ref, k_min, k_max, T_ref) <= 0.0 or k_min > k_max:
            raise ValueError(
                'invalid sso_r.oxygen_exchange k_O config: '
                f'k_ref={k_ref:g} k_min={k_min:g} '
                f'k_max={k_max:g} T_ref={T_ref:g}'
            )
        if bool(config.get('temperature_dependence_enabled', True)):
            raw = k_ref * math.exp((-Ea / GAS_CONSTANT) * (1.0 / T_K - 1.0 / T_ref))
            source = (
                'findings:baseline_2e-5_arrhenius_Ea_150kJ_'
                'clamped_5e-6_5e-5'
            )
        else:
            raw = k_ref
            source = 'findings:baseline_2e-5_clamped_temperature_dependence_disabled'
        return min(k_max, max(k_min, raw)), source

    def _oxygen_exchange_effective_melt_depth_m(self) -> float:
        config = self._oxygen_exchange_config()
        depth = float(config.get('effective_melt_depth_m', 0.2))
        if not math.isfinite(depth) or depth <= 0.0:
            raise ValueError(
                'sso_r.oxygen_exchange.effective_melt_depth_m must be > 0; '
                f'got {depth!r}'
            )
        return depth

    def _headspace_control_floor_pO2_bar(self) -> float:
        atmosphere_name = str(getattr(self.melt.atmosphere, 'name', '') or '')
        if atmosphere_name in {
            'CONTROLLED_O2',
            'CONTROLLED_O2_FLOW',
            'O2_BACKPRESSURE',
        }:
            return max(0.0, float(self.melt.pO2_mbar) / 1000.0)
        return 0.0

    def _headspace_ledger_pO2_bar_from_o2_mol(self, o2_mol: float) -> float:
        volume_m3 = self._headspace_volume_m3()
        T_head_K = self._headspace_temperature_K()
        if volume_m3 <= 0.0 or T_head_K <= 0.0:
            return 0.0
        return max(0.0, float(o2_mol)) * GAS_CONSTANT * T_head_K / (
            volume_m3 * 1.0e5
        )

    def _headspace_o2_mol_for_pO2_bar(self, pO2_bar: float) -> float:
        volume_m3 = self._headspace_volume_m3()
        T_head_K = self._headspace_temperature_K()
        if volume_m3 <= 0.0 or T_head_K <= 0.0:
            return 0.0
        return (
            max(0.0, float(pO2_bar))
            * 1.0e5
            * volume_m3
            / (GAS_CONSTANT * T_head_K)
        )

    def _headspace_floor_o2_mol(self) -> float:
        return self._headspace_o2_mol_for_pO2_bar(self._vacuum_floor_bar())

    def _effective_headspace_floor_o2_mol(self) -> float:
        return self._headspace_o2_mol_for_pO2_bar(
            max(
                self._headspace_control_floor_pO2_bar(),
                self._vacuum_floor_bar(),
            )
        )

    def _pn2_sweep_transport_pO2_bar(self, head_o2_mol: float) -> float:
        """PN2 sweep transport pO2 for vapor dispatch.

        Formula follows docs-private/research/2026-07-03-live-po2-probe/
        findings.md "Design answer": PN2_SWEEP requested pO2 is incoming
        sweep-gas composition, while O2 made this tick is a ledger product
        drained by the sweep. Transport therefore uses
        max(vacuum_floor, incoming_sweep_pO2, post_sweep_residual_pO2),
        not the pre-sweep closed-headspace ledger pO2.
        """
        incoming_sweep_pO2 = max(0.0, float(self.melt.pO2_mbar) / 1000.0)
        residual_o2_mol = max(0.0, float(head_o2_mol))
        if self._overhead_headspace_enabled() and residual_o2_mol > 0.0:
            holdup_mol = {
                species: max(0.0, float(mol))
                for species, mol in self.atom_ledger.mol_by_account(
                    'process.overhead_gas'
                ).items()
                if max(0.0, float(mol)) > 0.0
            }
            total_mol = sum(holdup_mol.values())
            total_kg = 0.0
            holdup_kg: Dict[str, float] = {}
            for species, mol in holdup_mol.items():
                formula = resolve_species_formula(
                    species,
                    self.atom_ledger.registry,
                )
                species_kg = mol * formula.molar_mass_kg_per_mol()
                holdup_kg[species] = species_kg
                total_kg += species_kg
            if total_mol > 0.0 and total_kg > 0.0:
                volume_m3 = self._headspace_volume_m3()
                T_head_K = self._headspace_temperature_K()
                p_total_bar = 0.0
                if volume_m3 > 0.0 and T_head_K > 0.0:
                    p_total_bar = (
                        total_mol * GAS_CONSTANT * T_head_K
                        / (volume_m3 * 1.0e5)
                    )
                p_downstream_bar = self._headspace_downstream_pressure_bar()
                from engines.builtin.overhead_bleed import (
                    compressible_pressure_capacity_fraction,
                )
                # DERIVATION: conductance is the kg/s capacity at P1 against
                # vacuum. The shared compressible-Poiseuille helper supplies
                # the finite-P2 capacity fraction; fraction * C * seconds is kg.
                pressure_capacity_fraction = (
                    compressible_pressure_capacity_fraction(
                        p_total_bar,
                        p_downstream_bar,
                    )
                )
                bleed_kg = (
                    self._headspace_bleed_conductance_kg_s(
                        species_kg_for_M_avg=holdup_kg,
                    )
                    * pressure_capacity_fraction
                    * 3600.0
                )
                if bleed_kg > 0.0:
                    avg_molar_mass = total_kg / total_mol
                    bleed_total_mol = min(total_mol, bleed_kg / avg_molar_mass)
                    bled_o2_mol = min(
                        residual_o2_mol,
                        residual_o2_mol * bleed_total_mol / total_mol,
                    )
                    residual_o2_mol = max(0.0, residual_o2_mol - bled_o2_mol)
        residual_pO2 = self._headspace_ledger_pO2_bar_from_o2_mol(
            residual_o2_mol
        )
        return max(
            incoming_sweep_pO2,
            residual_pO2,
            self._vacuum_floor_bar(),
        )

    def _headspace_transport_pO2_bar_from_ledger(
        self,
        ledger_pO2_bar: float,
        *,
        head_o2_mol: Optional[float] = None,
    ) -> float:
        if str(getattr(self.melt.atmosphere, 'name', '') or '') == 'PN2_SWEEP':
            if head_o2_mol is None:
                head_o2_mol = self._headspace_o2_mol_for_pO2_bar(
                    ledger_pO2_bar
                )
            return self._pn2_sweep_transport_pO2_bar(head_o2_mol)
        return max(
            float(ledger_pO2_bar),
            self._headspace_control_floor_pO2_bar(),
            self._vacuum_floor_bar(),
        )

    def _refresh_oxygen_reservoir_transport_pO2_for_vapor(
        self,
    ) -> OxygenReservoirState:
        head_o2_mol = max(0.0, float(
            self.atom_ledger.mol_by_account('process.overhead_gas').get(
                OXYGEN_SPECIES,
                0.0,
            )
        ))
        ledger_pO2 = self._headspace_ledger_pO2_bar_from_o2_mol(head_o2_mol)
        reservoir = self.melt.oxygen_reservoir
        reservoir.headspace_ledger_pO2_bar = ledger_pO2
        reservoir.headspace_transport_pO2_bar = (
            self._headspace_transport_pO2_bar_from_ledger(
                ledger_pO2,
                head_o2_mol=head_o2_mol,
            )
        )
        reservoir.headspace_control_floor_pO2_bar = (
            self._headspace_control_floor_pO2_bar()
        )
        self._sync_oxygen_reservoir_mirror()
        return reservoir

    def _vapor_pressure_transport_pO2_bar(self) -> float:
        transport_pO2 = getattr(self, '_headspace_transport_pO2_bar', None)
        if callable(transport_pO2):
            return float(transport_pO2())
        return float(self._commanded_pO2_bar())

    def _vapor_pressure_dispatch_pO2_bar(self) -> float:
        pO2_bar = self._vapor_pressure_transport_pO2_bar()
        store = _pt0_determinism_store_for(self)
        if store is not None and getattr(store, 'quantize_live_controls', False):
            return float(store.quantized_pO2_bar(self, pO2_bar=pO2_bar))
        return pO2_bar

    def _cleaned_melt_fe_atom_mol(self) -> float:
        total_fe_mol = 0.0
        for species, mol in self.atom_ledger.mol_by_account(
            'process.cleaned_melt'
        ).items():
            if float(mol) <= 0.0:
                continue
            formula = resolve_species_formula(species, self.species_formula_registry)
            total_fe_mol += float(mol) * float(formula.elements.get('Fe', 0.0))
        return max(0.0, total_fe_mol)

    def _fe3_over_sigma_fe_at_fO2(
        self,
        comp: Mapping[str, float],
        *,
        fO2_log: float,
        T_K: float,
        pressure_bar: float,
    ) -> float:
        mol_fractions = melt_mol_fractions_for_kress91(comp)
        if not mol_fractions:
            return 0.0
        return float(kress91_split(
            fO2_log=fO2_log,
            mol_fractions=mol_fractions,
            T_K=T_K,
            pressure_bar=pressure_bar,
        )['fe3'])

    def _melt_redox_capacity_mol_per_ln_fO2(
        self,
        *,
        fO2_log: float,
        T_K: float,
    ) -> float:
        total_fe_mol = self._cleaned_melt_fe_atom_mol()
        if total_fe_mol <= 0.0:
            return 0.0
        comp = self._melt_oxide_wt_pct()
        pressure_bar = floor_vacuum_pressure_bar(
            float(self.melt.p_total_mbar) / 1000.0,
            floor_bar=self._vacuum_floor_bar(),
        )
        eps_log10 = 0.001
        eps_ln = math.log(10.0) * eps_log10
        x_plus = self._fe3_over_sigma_fe_at_fO2(
            comp,
            fO2_log=fO2_log + eps_log10,
            T_K=T_K,
            pressure_bar=pressure_bar,
        )
        x_minus = self._fe3_over_sigma_fe_at_fO2(
            comp,
            fO2_log=fO2_log - eps_log10,
            T_K=T_K,
            pressure_bar=pressure_bar,
        )
        derivative = max(0.0, (x_plus - x_minus) / (2.0 * eps_ln))
        return (total_fe_mol / 4.0) * derivative

    def _sync_oxygen_reservoir_mirror(self) -> None:
        fO2_log = self._finite_oxygen_reservoir_fO2_log(
            self.melt.oxygen_reservoir.melt_intrinsic_fO2_log,
            context='sync_oxygen_reservoir_mirror',
            source_terms_mol_o2_equiv=getattr(
                self,
                '_redox_source_terms_this_hr',
                {},
            ),
        )
        self.melt.fO2_log = fO2_log
        self.melt.melt_fO2_log = fO2_log

    def _current_melt_redox_fO2_log(self) -> float:
        reservoir = getattr(self.melt, 'oxygen_reservoir', None)
        raw = getattr(reservoir, 'melt_intrinsic_fO2_log', None)
        if raw is None:
            raw = getattr(self.melt, 'melt_fO2_log', None)
        if raw is None:
            raw = -9.0
        try:
            fO2_log = float(raw)
        except (TypeError, ValueError):
            self._raise_nonfinite_oxygen_reservoir_fO2(
                raw,
                context='current_melt_redox_fO2_log',
            )
        if not math.isfinite(fO2_log):
            self._raise_nonfinite_oxygen_reservoir_fO2(
                raw,
                context='current_melt_redox_fO2_log',
            )
        return fO2_log

    def _current_melt_redox_reference_T_K(self) -> Optional[float]:
        reservoir = getattr(self.melt, 'oxygen_reservoir', None)
        raw = getattr(reservoir, 'reference_T_K', None)
        if raw is None:
            return None
        try:
            reference_T_K = float(raw)
        except (TypeError, ValueError) as exc:
            raise AccountingError(
                'melt fO2 reference_T_K must be finite positive or None; '
                f'got {raw!r}'
            ) from exc
        if not math.isfinite(reference_T_K) or reference_T_K <= 0.0:
            raise AccountingError(
                'melt fO2 reference_T_K must be finite positive or None; '
                f'got {raw!r}'
            )
        return reference_T_K

    def _oxygen_reservoir_guard_context(
        self,
        *,
        context: str,
        source_terms_mol_o2_equiv: Mapping[str, float] | None = None,
        net_o2_equiv_mol: float | None = None,
        melt_redox_capacity_mol_per_ln_fO2: float | None = None,
        delta_ln_fO2: float | None = None,
        candidate_fO2_log: float | None = None,
    ) -> Dict[str, Any]:
        def _json_safe_number(value: object) -> object:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return repr(value)
            if math.isfinite(number):
                return number
            return repr(value)

        reservoir = getattr(self.melt, 'oxygen_reservoir', None)
        head_o2_mol = float(
            self.atom_ledger.mol_by_account('process.overhead_gas').get(
                OXYGEN_SPECIES,
                0.0,
            )
            or 0.0
        )
        payload: Dict[str, Any] = {
            'context': str(context),
            'temperature_C': float(getattr(self.melt, 'temperature_C', 0.0) or 0.0),
            'temperature_K': float(getattr(self.melt, 'temperature_C', 0.0) or 0.0)
            + 273.15,
            'headspace_o2_mol': head_o2_mol,
            'reservoir_fO2_log': _json_safe_number(
                getattr(reservoir, 'melt_intrinsic_fO2_log', None)
            ),
            'reference_T_K': _json_safe_number(
                getattr(reservoir, 'reference_T_K', None)
            ),
            'headspace_ledger_pO2_bar': _json_safe_number(
                getattr(reservoir, 'headspace_ledger_pO2_bar', None)
            ),
            'headspace_transport_pO2_bar': _json_safe_number(
                getattr(reservoir, 'headspace_transport_pO2_bar', None)
            ),
            'headspace_control_floor_pO2_bar': _json_safe_number(
                getattr(reservoir, 'headspace_control_floor_pO2_bar', None)
            ),
            'exchange_direction': getattr(reservoir, 'exchange_direction', ''),
        }
        if source_terms_mol_o2_equiv is not None:
            payload['source_terms_mol_o2_equiv'] = {
                str(label): _json_safe_number(mol)
                for label, mol in source_terms_mol_o2_equiv.items()
            }
        if net_o2_equiv_mol is not None:
            payload['net_o2_equiv_mol'] = _json_safe_number(net_o2_equiv_mol)
        if melt_redox_capacity_mol_per_ln_fO2 is not None:
            payload['melt_redox_capacity_mol_per_ln_fO2'] = _json_safe_number(
                melt_redox_capacity_mol_per_ln_fO2
            )
        if delta_ln_fO2 is not None:
            payload['delta_ln_fO2'] = _json_safe_number(delta_ln_fO2)
        if candidate_fO2_log is not None:
            payload['candidate_fO2_log'] = _json_safe_number(candidate_fO2_log)
        return payload

    def _raise_nonfinite_oxygen_reservoir_fO2(
        self,
        raw: object,
        *,
        context: str,
        source_terms_mol_o2_equiv: Mapping[str, float] | None = None,
        net_o2_equiv_mol: float | None = None,
        melt_redox_capacity_mol_per_ln_fO2: float | None = None,
        delta_ln_fO2: float | None = None,
        candidate_fO2_log: float | None = None,
    ) -> None:
        attribution = self._oxygen_reservoir_guard_context(
            context=context,
            source_terms_mol_o2_equiv=source_terms_mol_o2_equiv,
            net_o2_equiv_mol=net_o2_equiv_mol,
            melt_redox_capacity_mol_per_ln_fO2=melt_redox_capacity_mol_per_ln_fO2,
            delta_ln_fO2=delta_ln_fO2,
            candidate_fO2_log=candidate_fO2_log,
        )
        raise AccountingError(
            'authoritative melt_intrinsic_fO2_log must be finite; '
            f'got {raw!r}; attribution={attribution!r}'
        )

    def _finite_oxygen_reservoir_fO2_log(
        self,
        raw: object,
        *,
        context: str,
        source_terms_mol_o2_equiv: Mapping[str, float] | None = None,
        net_o2_equiv_mol: float | None = None,
        melt_redox_capacity_mol_per_ln_fO2: float | None = None,
        delta_ln_fO2: float | None = None,
        candidate_fO2_log: float | None = None,
    ) -> float:
        try:
            fO2_log = float(raw)
        except (TypeError, ValueError):
            self._raise_nonfinite_oxygen_reservoir_fO2(
                raw,
                context=context,
                source_terms_mol_o2_equiv=source_terms_mol_o2_equiv,
                net_o2_equiv_mol=net_o2_equiv_mol,
                melt_redox_capacity_mol_per_ln_fO2=(
                    melt_redox_capacity_mol_per_ln_fO2
                ),
                delta_ln_fO2=delta_ln_fO2,
                candidate_fO2_log=candidate_fO2_log,
            )
        if not math.isfinite(fO2_log):
            self._raise_nonfinite_oxygen_reservoir_fO2(
                raw,
                context=context,
                source_terms_mol_o2_equiv=source_terms_mol_o2_equiv,
                net_o2_equiv_mol=net_o2_equiv_mol,
                melt_redox_capacity_mol_per_ln_fO2=(
                    melt_redox_capacity_mol_per_ln_fO2
                ),
                delta_ln_fO2=delta_ln_fO2,
                candidate_fO2_log=candidate_fO2_log,
            )
        return fO2_log

    def _melt_redox_liquidus_floor_fallback(
        self,
        *,
        source: str,
        reason: str,
        liquidus_status: Literal['unavailable', 'not_converged', 'invalid'],
    ) -> _MeltRedoxLiquidusFloorFallback:
        fallback = _MeltRedoxLiquidusFloorFallback(
            source=source,
            reason=reason,
            liquidus_status=liquidus_status,
        )
        diagnostic = {
            'status': 'liquidus_unavailable_floor_fallback',
            'source': source,
            'reason': reason,
            'liquidus_status': liquidus_status,
            'floor_T_C': KRESS91_LIQUID_CALIBRATION_MIN_T_C,
            'hour': int(self.melt.hour),
            'campaign_hour': int(self.melt.campaign_hour),
            'campaign': self.melt.campaign.name,
        }
        self._last_melt_redox_liquidus_gate_diagnostic = dict(diagnostic)
        fallback_diagnostics = getattr(
            self,
            '_melt_redox_liquidus_gate_fallback_diagnostics',
            None,
        )
        if not isinstance(fallback_diagnostics, deque):
            fallback_diagnostics = deque(
                fallback_diagnostics or (),
                maxlen=_MELT_REDOX_GATE_FALLBACK_HISTORY_MAXLEN,
            )
        fallback_diagnostics.append(dict(diagnostic))
        self._melt_redox_liquidus_gate_fallback_diagnostics = fallback_diagnostics
        self._melt_redox_liquidus_gate_fallback_count = int(
            getattr(self, '_melt_redox_liquidus_gate_fallback_count', 0) or 0
        ) + 1
        fallback_hourly = getattr(
            self,
            '_melt_redox_liquidus_gate_fallback_hourly',
            None,
        )
        if not isinstance(fallback_hourly, deque):
            fallback_hourly = deque(
                fallback_hourly or (),
                maxlen=_MELT_REDOX_GATE_FALLBACK_HISTORY_MAXLEN,
            )
        hour_key = (
            diagnostic['campaign'],
            diagnostic['hour'],
            diagnostic['campaign_hour'],
        )
        if fallback_hourly:
            last_hour = fallback_hourly[-1]
            last_hour_key = (
                last_hour.get('campaign'),
                last_hour.get('hour'),
                last_hour.get('campaign_hour'),
            )
        else:
            last_hour_key = None
        if last_hour_key == hour_key:
            updated_hour = dict(fallback_hourly[-1])
            updated_hour['count'] = int(updated_hour.get('count', 0)) + 1
            fallback_hourly[-1] = updated_hour
        else:
            fallback_hourly.append({
                'campaign': diagnostic['campaign'],
                'hour': diagnostic['hour'],
                'campaign_hour': diagnostic['campaign_hour'],
                'count': 1,
            })
        self._melt_redox_liquidus_gate_fallback_hourly = fallback_hourly
        return fallback

    def _melt_redox_liquidus_gate_fallback_summary(self) -> Dict[str, Any]:
        total_count = int(
            getattr(self, '_melt_redox_liquidus_gate_fallback_count', 0) or 0
        )
        if total_count <= 0:
            return {}
        recent = getattr(
            self,
            '_melt_redox_liquidus_gate_fallback_diagnostics',
            (),
        )
        hourly = getattr(
            self,
            '_melt_redox_liquidus_gate_fallback_hourly',
            (),
        )
        return {
            'engaged': True,
            'total_count': total_count,
            'history_maxlen': _MELT_REDOX_GATE_FALLBACK_HISTORY_MAXLEN,
            'recent': [dict(item) for item in recent],
            'recent_hourly': [dict(item) for item in hourly],
        }

    def _record_degraded_path_engagement(
        self,
        path: str,
        *,
        count: int,
    ) -> None:
        if path not in DEGRADED_PATH_ENGAGEMENT_KEYS:
            raise ValueError(f'unknown degraded path engagement key: {path!r}')
        count = int(count)
        if count <= 0:
            return

        summary = self._degraded_path_engagement.setdefault(
            path,
            {'total_count': 0, 'by_hour': []},
        )
        # Diagnostic count is the number of records/species/calls that exercised
        # this path, never a mass or mole quantity used by simulation arithmetic.
        summary['total_count'] = int(summary['total_count']) + count
        campaign = str(getattr(self.melt.campaign, 'name', self.melt.campaign))
        hour_row = {
            'campaign': campaign,
            'hour': int(self.melt.hour),
            'campaign_hour': int(self.melt.campaign_hour),
            'count': count,
        }
        by_hour = summary['by_hour']
        hour_key = (campaign, hour_row['hour'], hour_row['campaign_hour'])
        if by_hour:
            previous = by_hour[-1]
            previous_key = (
                previous.get('campaign'),
                previous.get('hour'),
                previous.get('campaign_hour'),
            )
        else:
            previous_key = None
        if previous_key == hour_key:
            by_hour[-1] = {
                **previous,
                'count': int(previous.get('count', 0)) + count,
            }
        else:
            by_hour.append(hour_row)

    def _degraded_path_engagement_summary(self) -> Dict[str, Dict[str, Any]]:
        return {
            path: {
                'total_count': int(summary.get('total_count', 0) or 0),
                'by_hour': [
                    dict(row) for row in list(summary.get('by_hour', ()) or ())
                ],
            }
            for path, summary in self._degraded_path_engagement.items()
        }

    @staticmethod
    def _melt_redox_gate_authority_provenance(
        gate_authority: _MeltRedoxGateAuthority,
    ) -> Dict[str, Any]:
        if isinstance(gate_authority, _MeltRedoxLiquidusFloorFallback):
            return {
                'kind': 'fallback',
                'fallback_status': 'liquidus_unavailable_floor_fallback',
                'source': gate_authority.source,
                'reason': gate_authority.reason,
                'liquidus_status': gate_authority.liquidus_status,
                'floor_T_C': KRESS91_LIQUID_CALIBRATION_MIN_T_C,
            }
        if isinstance(gate_authority, Mapping):
            return {
                'kind': 'real',
                'fallback_status': 'not_engaged',
                'source': gate_authority.get('source', 'liquidus_solidus'),
                'solidus_T_C': gate_authority.get('solidus_T_C'),
                'liquidus_T_C': gate_authority.get('liquidus_T_C'),
            }
        return {
            'kind': 'real',
            'fallback_status': 'not_engaged',
            'source': 'none:liquidus_gate_in_progress',
        }

    def _melt_redox_transition_provenance(
        self,
        gate_authority: _MeltRedoxGateAuthority,
    ) -> tuple[str, Dict[str, Any]]:
        provenance = self._melt_redox_gate_authority_provenance(
            gate_authority
        )
        return (
            f"melt_redox_gate_authority:{provenance['kind']}",
            {'melt_redox_gate_authority': provenance},
        )

    def _resolved_melt_redox_gate_authority(
        self,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> _MeltRedoxGateAuthority:
        if gate_authority is _RESOLVE_MELT_REDOX_GATE_AUTHORITY:
            pinned_hour = getattr(
                self,
                '_melt_redox_gate_authority_tick_hour',
                None,
            )
            pinned_authority = getattr(
                self,
                '_melt_redox_gate_authority_this_tick',
                _RESOLVE_MELT_REDOX_GATE_AUTHORITY,
            )
            if (
                pinned_hour == int(self.melt.hour)
                and pinned_authority is not _RESOLVE_MELT_REDOX_GATE_AUTHORITY
            ):
                return pinned_authority
            return self._melt_redox_liquidus_gate_curve()
        if (
            gate_authority is None
            or isinstance(gate_authority, Mapping)
            or isinstance(gate_authority, _MeltRedoxLiquidusFloorFallback)
        ):
            return gate_authority
        raise TypeError(f'invalid melt redox gate authority: {gate_authority!r}')

    def _establish_melt_redox_gate_authority_for_current_hour(
        self,
    ) -> _MeltRedoxGateAuthority:
        current_hour = int(self.melt.hour)
        pinned_hour = getattr(
            self,
            '_melt_redox_gate_authority_tick_hour',
            None,
        )
        pinned_authority = getattr(
            self,
            '_melt_redox_gate_authority_this_tick',
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY,
        )
        if (
            pinned_hour == current_hour
            and pinned_authority is not _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ):
            return pinned_authority
        authority = self._melt_redox_liquidus_gate_curve()
        self._melt_redox_gate_authority_this_tick = authority
        self._melt_redox_gate_authority_tick_hour = current_hour
        return authority

    def _clear_melt_redox_gate_authority_for_completed_hour(
        self,
        completed_hour: int,
    ) -> None:
        if getattr(
            self,
            '_melt_redox_gate_authority_tick_hour',
            None,
        ) != int(completed_hour):
            return
        self._melt_redox_gate_authority_this_tick = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        )
        self._melt_redox_gate_authority_tick_hour = None

    def _melt_redox_liquidus_gate_curve(
        self,
    ) -> Mapping[str, Any] | _MeltRedoxLiquidusFloorFallback | None:
        pressure_bar = float(self.melt.p_total_mbar) / 1000.0
        fO2_log = float(self._current_melt_redox_fO2_log())
        redox_key_fO2_log = self._freeze_gate_redox_key_fO2_log(
            fO2_log=fO2_log,
        )
        key = self._freeze_gate_cache_key(
            pressure_bar=pressure_bar,
            fO2_log=redox_key_fO2_log,
        )
        cache = getattr(self, '_freeze_gate_liquid_fraction_cache', None)
        curve = cache.get('curve') if isinstance(cache, dict) else None
        if not (
            isinstance(cache, dict)
            and cache.get('key') == key
            and isinstance(curve, Mapping)
        ):
            curve = None
        memo = getattr(self, '_freeze_gate_liquid_fraction_curve_memo', None)
        if curve is None and isinstance(memo, dict):
            memoized_curve = memo.get(key)
            if isinstance(memoized_curve, Mapping):
                curve = dict(memoized_curve)

        gate_curve_in_progress = (
            bool(getattr(self, '_freeze_gate_curve_in_progress', False))
            or (
                isinstance(cache, dict)
                and cache.get('status') == 'computing'
            )
        )
        if curve is None and gate_curve_in_progress:
            # During curve construction this redox capacity read is bootstrap
            # context, not ledger authority; the outer stored curve serves later
            # redox reads after the in-progress provider call finishes.
            self._last_melt_redox_liquidus_gate_diagnostic = {
                'status': 'unavailable',
                'source': 'none:liquidus_gate_in_progress',
            }
            return None
        if curve is None:
            try:
                curve = self._freeze_gate_curve()
            except Exception as exc:  # noqa: BLE001 - optional liquidus engines
                reason = str(exc)
                liquidus_status: Literal['unavailable', 'not_converged'] = (
                    'not_converged'
                    if 'status=not_converged' in reason
                    else 'unavailable'
                )
                return self._melt_redox_liquidus_floor_fallback(
                    source=f'none:liquidus_{liquidus_status}',
                    reason=reason,
                    liquidus_status=liquidus_status,
                )

        try:
            solidus_T_C = float(curve['solidus_T_C'])
            liquidus_T_C = float(curve['liquidus_T_C'])
        except (KeyError, TypeError, ValueError) as exc:
            if (
                isinstance(self._freeze_gate_liquid_fraction_cache, dict)
                and self._freeze_gate_liquid_fraction_cache.get('key') == key
            ):
                self._freeze_gate_liquid_fraction_cache = None
            if isinstance(memo, dict):
                memo.pop(key, None)
            return self._melt_redox_liquidus_floor_fallback(
                source='none:invalid_liquidus_curve',
                reason=str(exc),
                liquidus_status='invalid',
            )
        if (
            not math.isfinite(solidus_T_C)
            or not math.isfinite(liquidus_T_C)
            or not solidus_T_C < liquidus_T_C
        ):
            if (
                isinstance(self._freeze_gate_liquid_fraction_cache, dict)
                and self._freeze_gate_liquid_fraction_cache.get('key') == key
            ):
                self._freeze_gate_liquid_fraction_cache = None
            if isinstance(memo, dict):
                memo.pop(key, None)
            return self._melt_redox_liquidus_floor_fallback(
                source='none:invalid_liquidus_bounds',
                reason=(
                    'invalid liquidus bounds: '
                    f'solidus_T_C={solidus_T_C!r}, '
                    f'liquidus_T_C={liquidus_T_C!r}'
                ),
                liquidus_status='invalid',
            )

        normalized_curve = dict(curve)
        normalized_curve['solidus_T_C'] = solidus_T_C
        normalized_curve['liquidus_T_C'] = liquidus_T_C
        validated_cache = self._freeze_gate_liquid_fraction_cache
        if not (
            isinstance(validated_cache, dict)
            and validated_cache.get('key') == key
            and isinstance(validated_cache.get('curve'), Mapping)
            and dict(validated_cache['curve']) == normalized_curve
        ):
            self._freeze_gate_liquid_fraction_cache = {
                'key': key,
                'curve': dict(normalized_curve),
            }
        if isinstance(memo, dict):
            memo[key] = dict(normalized_curve)
        self._last_melt_redox_liquidus_gate_diagnostic = {
            'status': 'ok',
            'source': normalized_curve.get('source', 'liquidus_solidus'),
            'solidus_T_C': solidus_T_C,
            'liquidus_T_C': liquidus_T_C,
        }
        return normalized_curve

    def _melt_redox_liquid_fraction_factor(
        self,
        T_K: float,
        *,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> float:
        curve = self._resolved_melt_redox_gate_authority(gate_authority)
        if curve is None:
            return 0.0
        if isinstance(curve, _MeltRedoxLiquidusFloorFallback):
            temperature_C = float(T_K) - 273.15
            # An unavailable liquidus is a measurement failure, not evidence
            # of solidification. The calibrated Kress91 floor is the remaining
            # lower authority; zeroing capacity would assert solid without data.
            liquid_fraction = (
                1.0
                if temperature_C > KRESS91_LIQUID_CALIBRATION_MIN_T_C
                else 0.0
            )
            self._last_melt_redox_liquid_fraction_diagnostic = {
                'status': 'liquidus_unavailable_floor_fallback',
                'source': curve.source,
                'reason': curve.reason,
                'liquidus_status': curve.liquidus_status,
                'liquid_fraction': liquid_fraction,
                'floor_T_C': KRESS91_LIQUID_CALIBRATION_MIN_T_C,
            }
            return liquid_fraction
        try:
            liquid_fraction = float(
                self._interpolate_freeze_gate_curve(
                    curve,
                    float(T_K) - 273.15,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            self._last_melt_redox_liquid_fraction_diagnostic = {
                'status': 'invalid',
                'source': 'none:invalid_liquid_fraction_curve',
                'reason': str(exc),
            }
            return 0.0
        if not math.isfinite(liquid_fraction):
            self._last_melt_redox_liquid_fraction_diagnostic = {
                'status': 'invalid',
                'source': 'none:nonfinite_liquid_fraction',
                'liquid_fraction': liquid_fraction,
            }
            return 0.0
        liquid_fraction = max(0.0, min(1.0, liquid_fraction))
        self._last_melt_redox_liquid_fraction_diagnostic = {
            'status': 'ok',
            'source': curve.get('source', 'liquidus_solidus'),
            'liquid_fraction': liquid_fraction,
            'solidus_T_C': curve.get('solidus_T_C'),
            'liquidus_T_C': curve.get('liquidus_T_C'),
        }
        return liquid_fraction

    def _melt_redox_source_capacity_mol_per_ln_fO2(
        self,
        *,
        fO2_log: float,
        T_K: float,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> float:
        C_m_full = self._melt_redox_capacity_mol_per_ln_fO2(
            fO2_log=fO2_log,
            T_K=T_K,
        )
        liquid_fraction = self._melt_redox_liquid_fraction_factor(
            T_K,
            gate_authority=gate_authority,
        )
        # Derivation: Kress91 capacity is proportional to melt Fe inventory;
        # freeze-gate liquid_fraction is the active residual-liquid fraction,
        # so C_m_effective = C_m_full * liquid_fraction and tends to 0 at solidus.
        return C_m_full * liquid_fraction

    def _melt_redox_temperature_shift_is_liquid(
        self,
        T_K: float,
        *,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> bool:
        curve = self._resolved_melt_redox_gate_authority(gate_authority)
        if curve is None:
            return False
        if isinstance(curve, _MeltRedoxLiquidusFloorFallback):
            temperature_C = float(T_K) - 273.15
            is_liquid = temperature_C > KRESS91_LIQUID_CALIBRATION_MIN_T_C
            self._last_melt_regime_diagnostic = {
                'status': 'liquidus_unavailable_floor_fallback',
                'source': curve.source,
                'reason': curve.reason,
                'liquidus_status': curve.liquidus_status,
                'redox_temperature_shift_threshold_T_C': (
                    KRESS91_LIQUID_CALIBRATION_MIN_T_C
                ),
                'temperature_C': temperature_C,
                'is_liquid': is_liquid,
            }
            return is_liquid
        liquidus_T_C = float(curve['liquidus_T_C'])
        threshold_T_C = max(
            liquidus_T_C,
            KRESS91_LIQUID_CALIBRATION_MIN_T_C,
        )
        regime_diagnostic = {}
        regime = melt_regime(
            temperature_K=float(T_K),
            solidus_K=threshold_T_C + 273.15,
            epsilon=0.0,
            solidus_boundary='liquid',
            diagnostic=regime_diagnostic,
            diagnostic_site='core.redox_temperature_shift.liquidus_threshold',
            legacy_predicate=(
                'temperature_C >= max(SILICATE_LIQUIDUS, '
                'KRESS91_LIQUID_CALIBRATION_MIN_T_C)'
            ),
        )
        regime_diagnostic.update({
            'liquidus_T_C': liquidus_T_C,
            'redox_temperature_shift_threshold_T_C': threshold_T_C,
            'liquidus_source': curve.get('source', 'liquidus_solidus'),
        })
        # Intentional telemetry for the t-125 uncertainty stack; its consumer
        # lands after the predicate-unification work.
        self._last_melt_regime_diagnostic = regime_diagnostic
        return regime != MeltRegime.FROZEN

    def _re_reference_melt_fO2_to_temperature(
        self,
        temperature_K: Optional[float] = None,
        *,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> None:
        fO2_log, reference_T_K = self._melt_fO2_reference_state_at_temperature(
            temperature_K,
            gate_authority=gate_authority,
        )
        reservoir = self.melt.oxygen_reservoir
        reservoir.melt_intrinsic_fO2_log = fO2_log
        reservoir.reference_T_K = reference_T_K
        self._sync_oxygen_reservoir_mirror()

    def _melt_fO2_reference_state_at_temperature(
        self,
        temperature_K: Optional[float] = None,
        *,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> tuple[float, float | None]:
        T_now = (
            float(temperature_K)
            if temperature_K is not None
            else float(self.melt.temperature_C) + 273.15
        )
        if not math.isfinite(T_now) or T_now <= 0.0:
            raise AccountingError(
                'melt fO2 temperature re-reference requires finite positive T_K; '
                f'got {temperature_K!r}'
            )
        reference_T_K = self._current_melt_redox_reference_T_K()
        if (
            reference_T_K is not None
            and math.isclose(T_now, reference_T_K, rel_tol=0.0, abs_tol=1.0e-9)
        ):
            return self._current_melt_redox_fO2_log(), reference_T_K
        if not self._melt_redox_temperature_shift_is_liquid(
            T_now,
            gate_authority=gate_authority,
        ):
            # Sub-solidus redox is quenched: Kress91 is a liquid relation, so
            # glass has no equilibrium fO2 to re-reference. Diagnostics below
            # solidus intentionally read the last liquid couple.
            return self._current_melt_redox_fO2_log(), reference_T_K
        if reference_T_K is None:
            # load_batch seeds at 25 C; Kress91 is a liquid relation, so the
            # seed is treated as defined at the first liquid tick instead.
            return self._current_melt_redox_fO2_log(), T_now

        base_ln_fO2 = self._current_melt_redox_fO2_log() * math.log(10.0)
        pressure_bar = floor_vacuum_pressure_bar(
            float(self.melt.p_total_mbar) / 1000.0,
            floor_bar=self._vacuum_floor_bar(),
        )
        delta_ln_fO2 = kress91_ln_fO2_temperature_delta(
            reference_T_K,
            T_now,
            reference_pressure_bar=pressure_bar,
            target_pressure_bar=pressure_bar,
        )
        # Premise: Kress91 returns the fixed-redox endpoint shift in natural-log
        # fO2, including the -3.36*dG(T) family whose omission reaches 0.049 dex
        # per +100 C in-band and 0.083 dex per +100 C across reachable excursions.
        # Algebra: ln(fO2_new) = ln(fO2_old) + delta_ln(fO2), then / ln(10).
        # Units: every logarithm and the resulting log10(fO2/bar) are dimensionless.
        # Sanity: zero temperature shift returns the original log10 fO2 exactly.
        candidate_fO2_log = (
            base_ln_fO2 + delta_ln_fO2
        ) / math.log(10.0)
        fO2_log = self._finite_oxygen_reservoir_fO2_log(
            candidate_fO2_log,
            context='temperature_re_reference',
            delta_ln_fO2=delta_ln_fO2,
            candidate_fO2_log=candidate_fO2_log,
        )
        return fO2_log, T_now

    def _refresh_oxygen_reservoir_without_exchange(
        self,
        *,
        melt_intrinsic_fO2_log: Optional[float] = None,
        reference_T_K: object = _PRESERVE_REFERENCE_T_K,
        exchange_direction: str = 'none:initialized',
    ) -> OxygenReservoirState:
        fO2_raw = (
            melt_intrinsic_fO2_log
            if melt_intrinsic_fO2_log is not None
            else self.melt.oxygen_reservoir.melt_intrinsic_fO2_log
        )
        fO2_log = self._finite_oxygen_reservoir_fO2_log(
            fO2_raw,
            context='refresh_oxygen_reservoir_without_exchange',
        )
        if reference_T_K is _PRESERVE_REFERENCE_T_K:
            reference_T = self._current_melt_redox_reference_T_K()
        elif reference_T_K is None:
            reference_T = None
        else:
            try:
                reference_T = float(reference_T_K)
            except (TypeError, ValueError) as exc:
                raise AccountingError(
                    'melt fO2 reference_T_K must be finite positive or None; '
                    f'got {reference_T_K!r}'
                ) from exc
            if not math.isfinite(reference_T) or reference_T <= 0.0:
                raise AccountingError(
                    'melt fO2 reference_T_K must be finite positive or None; '
                    f'got {reference_T_K!r}'
                )
        head_o2_mol = max(0.0, float(
            self.atom_ledger.mol_by_account('process.overhead_gas').get(
                OXYGEN_SPECIES,
                0.0,
            )
        ))
        ledger_pO2 = self._headspace_ledger_pO2_bar_from_o2_mol(head_o2_mol)
        reservoir = OxygenReservoirState(
            melt_intrinsic_fO2_log=fO2_log,
            reference_T_K=reference_T,
            headspace_ledger_pO2_bar=ledger_pO2,
            headspace_transport_pO2_bar=(
                self._headspace_transport_pO2_bar_from_ledger(
                    ledger_pO2,
                    head_o2_mol=head_o2_mol,
                )
            ),
            headspace_control_floor_pO2_bar=self._headspace_control_floor_pO2_bar(),
            exchange_direction=exchange_direction,
        )
        self.melt.oxygen_reservoir = reservoir
        self._sync_oxygen_reservoir_mirror()
        return reservoir

    def _apply_oxygen_reservoir_redox_source_terms(
        self,
        source_terms_mol_o2_equiv: Mapping[str, float],
        *,
        exchange_direction: str = 'redox_source_terms',
        temperature_K: Optional[float] = None,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> OxygenReservoirState:
        terms: Dict[str, float] = {}
        for label, raw_mol in source_terms_mol_o2_equiv.items():
            mol = float(raw_mol)
            if not math.isfinite(mol):
                raise ValueError(
                    f'redox source term {label!r} must be finite, got {raw_mol!r}'
                )
            terms[str(label)] = mol
        if terms:
            self._accumulate_redox_source_terms_for_hour(
                '_redox_source_terms_this_hr',
                terms,
            )
        net_o2_equiv_mol = sum(terms.values())
        T_K = (
            float(temperature_K)
            if temperature_K is not None
            else float(self.melt.temperature_C) + 273.15
        )
        if not math.isfinite(T_K) or T_K <= 0.0:
            raise AccountingError(
                'oxygen reservoir redox source terms require finite positive T_K; '
                f'got {temperature_K!r}'
            )
        gate_authority = self._resolved_melt_redox_gate_authority(
            gate_authority
        )
        self._re_reference_melt_fO2_to_temperature(
            T_K,
            gate_authority=gate_authority,
        )
        base_fO2_log = self._current_melt_redox_fO2_log()
        reference_T_K = self._current_melt_redox_reference_T_K()
        head_o2_mol = max(0.0, float(
            self.atom_ledger.mol_by_account('process.overhead_gas').get(
                OXYGEN_SPECIES,
                0.0,
            )
        ))
        ledger_pO2 = self._headspace_ledger_pO2_bar_from_o2_mol(head_o2_mol)
        C_m = self._melt_redox_source_capacity_mol_per_ln_fO2(
            fO2_log=base_fO2_log,
            T_K=T_K,
            gate_authority=gate_authority,
        )
        reservoir = self.melt.oxygen_reservoir
        existing_direction = str(getattr(reservoir, 'exchange_direction', '') or '')
        reservoir.melt_intrinsic_fO2_log = base_fO2_log
        reservoir.reference_T_K = reference_T_K
        reservoir.headspace_ledger_pO2_bar = ledger_pO2
        reservoir.headspace_transport_pO2_bar = (
            self._headspace_transport_pO2_bar_from_ledger(
                ledger_pO2,
                head_o2_mol=head_o2_mol,
            )
        )
        reservoir.headspace_control_floor_pO2_bar = (
            self._headspace_control_floor_pO2_bar()
        )
        reservoir.melt_redox_capacity_mol_per_ln_fO2 = C_m
        reservoir.headspace_capacity_mol_per_ln_pO2 = max(
            head_o2_mol,
            self._effective_headspace_floor_o2_mol(),
        )
        applied_delta_ln = 0.0
        skip_reason = ''
        refusal_context: Dict[str, Any] = {}
        if C_m <= OXYGEN_RESERVOIR_NOOP_MOL:
            # At or below the project's negligible-mol numerical floor (NOOP_MOL = 1e-15) the
            # melt retains no MEANINGFUL authoritative differential redox capacity. A saturated/
            # exhausted melt drives C_m into this band (observed down to denormalized ~1e-293 mol
            # per ln fO2 after the C3 alkali shuttle). Dividing a residual source term by such a
            # C_m yields an absurd candidate fO2 (finite ~1e9..1e287, or overflow to non-finite),
            # so APPLYING it would corrupt the reservoir. Diagnose the honest root cause
            # (no_melt_redox_capacity) at the floor instead. A full-grid SSO-R scan (review
            # codex-7466, 2026-07-08) found 846/15082 real source-term calls in this band and
            # confirmed the floor REFUSES 37 real rows the prior branch would have applied as
            # absurd fO2 jumps (candidate log10 fO2 up to ~1.9e9) — a correctness fix, and NOT a
            # fail-open: skipped terms are recorded as refusal, never as success. The graded
            # range/saturation refusals below still apply ABOVE the floor (C_m > NOOP_MOL), where
            # a real capacity meets an out-of-range or non-finite demand.
            # Physical rationale (owner-confirmed 2026-07-08): in this project's default vacuum regime
            # the O2 tied to a floored term sits below lunar-atmosphere pressure (~1e-15 bar) — the
            # free-molecular / pure-ballistic-escape regime — so it leaves the system without
            # re-equilibrating with the melt. Applying vs refusing the tiny redox term therefore does
            # not change the physics: that oxygen escapes ballistically either way, so the floor is
            # moot (hence correct) precisely where it fires.
            skip_reason = 'no_melt_redox_capacity'
        elif abs(net_o2_equiv_mol) < OXYGEN_RESERVOIR_NOOP_MOL:
            skip_reason = 'below_threshold'
        else:
            x_m = base_fO2_log * math.log(10.0)
            applied_delta_ln = net_o2_equiv_mol / C_m
            candidate_fO2_log = (
                x_m + applied_delta_ln
            ) / math.log(10.0)
            if (
                not math.isfinite(applied_delta_ln)
                or not math.isfinite(candidate_fO2_log)
            ):
                skip_reason = 'redox_capacity_saturation_refusal'
                refusal_context = self._oxygen_reservoir_guard_context(
                    context='redox_source_terms_saturation_refusal',
                    source_terms_mol_o2_equiv=terms,
                    net_o2_equiv_mol=net_o2_equiv_mol,
                    melt_redox_capacity_mol_per_ln_fO2=C_m,
                    delta_ln_fO2=applied_delta_ln,
                    candidate_fO2_log=candidate_fO2_log,
                )
                applied_delta_ln = 0.0
            elif not (
                OXYGEN_RESERVOIR_REDOX_SOURCE_MIN_FO2_LOG10_BAR
                <= candidate_fO2_log
                <= OXYGEN_RESERVOIR_REDOX_SOURCE_MAX_FO2_LOG10_BAR
            ):
                skip_reason = 'redox_candidate_fO2_out_of_range_refusal'
                refusal_context = self._oxygen_reservoir_guard_context(
                    context='redox_source_terms_fO2_range_refusal',
                    source_terms_mol_o2_equiv=terms,
                    net_o2_equiv_mol=net_o2_equiv_mol,
                    melt_redox_capacity_mol_per_ln_fO2=C_m,
                    delta_ln_fO2=applied_delta_ln,
                    candidate_fO2_log=candidate_fO2_log,
                )
                refusal_context['candidate_fO2_log_min'] = (
                    OXYGEN_RESERVOIR_REDOX_SOURCE_MIN_FO2_LOG10_BAR
                )
                refusal_context['candidate_fO2_log_max'] = (
                    OXYGEN_RESERVOIR_REDOX_SOURCE_MAX_FO2_LOG10_BAR
                )
                applied_delta_ln = 0.0
            else:
                reservoir.melt_intrinsic_fO2_log = (
                    self._finite_oxygen_reservoir_fO2_log(
                        candidate_fO2_log,
                        context='redox_source_terms',
                        source_terms_mol_o2_equiv=terms,
                        net_o2_equiv_mol=net_o2_equiv_mol,
                        melt_redox_capacity_mol_per_ln_fO2=C_m,
                        delta_ln_fO2=applied_delta_ln,
                        candidate_fO2_log=candidate_fO2_log,
                    )
                )
        if terms:
            if skip_reason:
                self._accumulate_redox_source_terms_for_hour(
                    '_redox_source_skipped_terms_this_hr',
                    terms,
                )
                self._record_redox_source_skip_reasons_for_hour(
                    terms,
                    skip_reason,
                )
                if refusal_context:
                    self._redox_source_refusal_context_this_hr = refusal_context
            else:
                self._accumulate_redox_source_terms_for_hour(
                    '_redox_source_applied_terms_this_hr',
                    terms,
                )
            reservoir.exchange_direction = (
                self._composed_oxygen_reservoir_direction(
                    existing_direction,
                    exchange_direction,
                    skip_reason=skip_reason,
                )
            )
        self._redox_source_delta_ln_this_hr = (
            float(getattr(self, '_redox_source_delta_ln_this_hr', 0.0))
            + applied_delta_ln
        )
        self.melt.oxygen_reservoir = reservoir
        self._sync_oxygen_reservoir_mirror()
        breakdown = self._redox_source_breakdown_diagnostic()
        reservoir.redox_source_terms_mol_o2_equiv = dict(
            breakdown.get('terms_mol_o2_equiv_by_label', {})
        )
        reservoir.redox_source_net_mol_o2_equiv = float(
            breakdown.get('net_mol_o2_equiv', 0.0)
        )
        reservoir.redox_source_delta_ln_fO2 = float(
            breakdown.get('delta_ln_fO2', 0.0)
        )
        reservoir.redox_source_delta_log10_fO2 = float(
            breakdown.get('delta_log10_fO2', 0.0)
        )
        reservoir.redox_source_applied_terms_mol_o2_equiv = dict(
            breakdown.get('applied_terms_mol_o2_equiv_by_label', {})
        )
        reservoir.redox_source_skipped_terms_mol_o2_equiv = dict(
            breakdown.get('skipped_terms_mol_o2_equiv_by_label', {})
        )
        reservoir.redox_source_skipped_reasons_by_label = dict(
            breakdown.get('skipped_reasons_by_label', {})
        )
        reservoir.redox_source_terms_applied = bool(
            breakdown.get('redox_source_terms_applied', False)
        )
        reservoir.redox_source_skip_reason = str(
            breakdown.get('redox_source_skip_reason', '')
        )
        reservoir.redox_source_refusal_context = dict(
            breakdown.get('redox_source_refusal_context', {}) or {}
        )
        reservoir.ferric_divergence = dict(
            breakdown.get('ferric_divergence', {})
        )
        self.melt.oxygen_reservoir = reservoir
        self._sync_oxygen_reservoir_mirror()
        return reservoir

    @staticmethod
    def _finite_float_or_none(value: object) -> float | None:
        if value is None:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    def _o2_bubbler_target_fO2_log(
        self,
        raw_target_fO2_log: object,
    ) -> tuple[float | None, str]:
        explicit = self._finite_float_or_none(raw_target_fO2_log)
        if explicit is not None:
            return explicit, 'explicit_fO2_log'
        if raw_target_fO2_log is not None:
            return None, 'invalid_target_fO2_log'
        atmosphere_name = str(getattr(self.melt.atmosphere, 'name', '') or '')
        if atmosphere_name not in {
            'CONTROLLED_O2',
            'CONTROLLED_O2_FLOW',
            'O2_BACKPRESSURE',
        }:
            return None, 'no_target_for_uncontrolled_atmosphere'
        pO2_mbar = self._finite_float_or_none(getattr(self.melt, 'pO2_mbar', None))
        if pO2_mbar is None or pO2_mbar <= 0.0:
            return None, 'no_positive_controlled_pO2'
        return math.log10(pO2_mbar / 1000.0), 'controlled_o2_pO2_mbar'

    def _reset_o2_bubbler_telemetry_for_hour(self) -> None:
        self._o2_bubbler_injected_kg = 0.0
        self._o2_bubbler_absorbed_kg = 0.0
        self._o2_bubbler_passthrough_kg = 0.0
        self._o2_bubbler_vented_kg = 0.0
        self._o2_bubbler_overhead_passthrough_pending_kg = 0.0
        self._last_o2_bubbler_diagnostic = {
            'status': 'ok',
            'reason': 'not_run',
        }

    def _apply_o2_bubbler(
        self,
        *,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> Dict[str, Any]:
        controls = self.campaign_mgr.o2_bubbler_controls(self.melt.campaign)
        raw_rate = controls.get('o2_bubbler_kg_per_hr')
        raw_eta = controls.get('o2_bubbler_eta_absorb_default')
        raw_target = controls.get('o2_bubbler_target_fO2_log')
        rate_kg_hr = self._finite_float_or_none(raw_rate)
        if rate_kg_hr is None:
            if raw_rate is None:
                reason = 'rate_absent'
            else:
                reason = 'invalid_rate_kg_per_hr'
            diagnostic = {'status': 'ok', 'reason': reason, 'injected_mol': 0.0}
            self._last_o2_bubbler_diagnostic = diagnostic
            return diagnostic
        rate_kg_hr = max(0.0, rate_kg_hr)

        if raw_eta is None:
            eta_absorb = 0.75
        else:
            eta_absorb = self._finite_float_or_none(raw_eta)
            if eta_absorb is None:
                diagnostic = {
                    'status': 'ok',
                    'reason': 'invalid_eta_absorb',
                    'rate_kg_per_hr': rate_kg_hr,
                    'raw_eta_absorb': raw_eta,
                    'injected_mol': 0.0,
                }
                self._last_o2_bubbler_diagnostic = diagnostic
                return diagnostic
        if eta_absorb < 0.0 or eta_absorb > 1.0:
            diagnostic = {
                'status': 'ok',
                'reason': 'invalid_eta_absorb',
                'rate_kg_per_hr': rate_kg_hr,
                'eta_absorb': eta_absorb,
                'injected_mol': 0.0,
            }
            self._last_o2_bubbler_diagnostic = diagnostic
            return diagnostic

        target_fO2_log, target_source = self._o2_bubbler_target_fO2_log(raw_target)
        if target_fO2_log is None:
            diagnostic = {
                'status': 'ok',
                'reason': target_source,
                'rate_kg_per_hr': rate_kg_hr,
                'eta_absorb': eta_absorb,
                'injected_mol': 0.0,
            }
            self._last_o2_bubbler_diagnostic = diagnostic
            return diagnostic

        T_K = float(self.melt.temperature_C) + 273.15
        commanded_mol = rate_kg_hr / OXYGEN_MOLAR_MASS_KG_PER_MOL
        gate_authority = self._resolved_melt_redox_gate_authority(
            gate_authority
        )
        self._re_reference_melt_fO2_to_temperature(
            T_K,
            gate_authority=gate_authority,
        )
        current_fO2_log = self._current_melt_redox_fO2_log()
        C_m = self._melt_redox_source_capacity_mol_per_ln_fO2(
            fO2_log=current_fO2_log,
            T_K=T_K,
            gate_authority=gate_authority,
        )
        target_delta_log10 = target_fO2_log - current_fO2_log
        target_need_mol = 0.0
        reason = 'applied'
        actual_injected_mol = 0.0
        absorbed_mol = 0.0
        passthrough_mol = 0.0
        absorption_respeciation: Dict[str, Any] = {}
        if rate_kg_hr <= 0.0:
            reason = 'rate_zero'
        elif commanded_mol <= 0.0:
            reason = 'commanded_zero'
        elif target_delta_log10 <= 0.0:
            reason = 'at_or_above_target'
        elif C_m <= OXYGEN_RESERVOIR_NOOP_MOL:
            requested_absorbed_mol = max(0.0, commanded_mol * eta_absorb)
            reason = 'no_melt_redox_capacity'
            if requested_absorbed_mol > OXYGEN_RESERVOIR_NOOP_MOL:
                reservoir = self._apply_oxygen_reservoir_redox_source_terms(
                    {'redox_source:o2_bubbler': requested_absorbed_mol},
                    exchange_direction='redox_source:o2_bubbler',
                    temperature_K=T_K,
                    gate_authority=gate_authority,
                )
                reason = str(
                    getattr(reservoir, 'redox_source_skip_reason', '')
                    or reason
                )
            absorption_respeciation = {
                'status': 'refused',
                'reason': reason,
                'oxygen_source': FE_REDOX_OXYGEN_SOURCE_FO2_BUFFER,
                'applied_o2_mol': 0.0,
                'internal_o2_capacity_mol': requested_absorbed_mol,
            }
        else:
            target_need_mol = max(
                0.0,
                C_m * math.log(10.0) * target_delta_log10,
            )
            if target_need_mol <= OXYGEN_RESERVOIR_NOOP_MOL:
                reason = 'below_threshold'
            else:
                eta_for_cap = max(eta_absorb, 1.0e-12)
                actual_injected_mol = min(
                    commanded_mol,
                    target_need_mol / eta_for_cap,
                )
                absorbed_mol = min(actual_injected_mol * eta_absorb, target_need_mol)
                passthrough_mol = max(0.0, actual_injected_mol - absorbed_mol)
                if actual_injected_mol <= OXYGEN_RESERVOIR_NOOP_MOL:
                    reason = 'below_threshold'

        if absorbed_mol > OXYGEN_RESERVOIR_NOOP_MOL:
            if not self._melt_redox_temperature_shift_is_liquid(
                T_K,
                gate_authority=gate_authority,
            ):
                passthrough_mol = actual_injected_mol
                absorbed_mol = 0.0
                reason = 'deferred_not_liquid'
        if actual_injected_mol > OXYGEN_RESERVOIR_NOOP_MOL:
            self.atom_ledger.load_external_mol(
                FO2_BUFFER_ACCOUNT,
                {OXYGEN_SPECIES: actual_injected_mol},
                source='external O2 bubbler injection',
            )
        if absorbed_mol > OXYGEN_RESERVOIR_NOOP_MOL:
            requested_absorbed_mol = absorbed_mol
            target_absorption_fO2_log = (
                current_fO2_log
                + requested_absorbed_mol / (C_m * math.log(10.0))
            )
            absorption_respeciation = self._apply_fe_redox_respeciation(
                oxygen_source=FE_REDOX_OXYGEN_SOURCE_FO2_BUFFER,
                fO2_log_override=target_absorption_fO2_log,
                internal_o2_capacity_mol=requested_absorbed_mol,
                update_reservoir_state=False,
                gate_authority=gate_authority,
            )
            applied_absorbed_mol = max(0.0, float(
                absorption_respeciation.get('applied_o2_mol', 0.0) or 0.0
            ))
            if applied_absorbed_mol > OXYGEN_RESERVOIR_NOOP_MOL:
                absorbed_mol = min(applied_absorbed_mol, requested_absorbed_mol)
                applied_delta_ln = absorbed_mol / C_m
                terms = {'redox_source:o2_bubbler': absorbed_mol}
                self._accumulate_redox_source_terms_for_hour(
                    '_redox_source_terms_this_hr',
                    terms,
                )
                self._accumulate_redox_source_terms_for_hour(
                    '_redox_source_applied_terms_this_hr',
                    terms,
                )
                self._redox_source_delta_ln_this_hr = (
                    float(getattr(self, '_redox_source_delta_ln_this_hr', 0.0))
                    + applied_delta_ln
                )
                reservoir = self.melt.oxygen_reservoir
                candidate_fO2_log = (
                    current_fO2_log * math.log(10.0) + applied_delta_ln
                ) / math.log(10.0)
                reservoir.melt_intrinsic_fO2_log = (
                    self._finite_oxygen_reservoir_fO2_log(
                        candidate_fO2_log,
                        context='redox_source_terms:o2_bubbler',
                        source_terms_mol_o2_equiv=terms,
                        net_o2_equiv_mol=absorbed_mol,
                        melt_redox_capacity_mol_per_ln_fO2=C_m,
                        delta_ln_fO2=applied_delta_ln,
                        candidate_fO2_log=candidate_fO2_log,
                    )
                )
                reservoir.reference_T_K = T_K
                reservoir.melt_redox_capacity_mol_per_ln_fO2 = C_m
                reservoir.exchange_direction = (
                    self._composed_oxygen_reservoir_direction(
                        str(getattr(reservoir, 'exchange_direction', '') or ''),
                        'redox_source:o2_bubbler',
                    )
                )
                self.melt.oxygen_reservoir = reservoir
                self._sync_oxygen_reservoir_mirror()
                breakdown = self._redox_source_breakdown_diagnostic()
                reservoir.redox_source_terms_mol_o2_equiv = dict(
                    breakdown.get('terms_mol_o2_equiv_by_label', {})
                )
                reservoir.redox_source_net_mol_o2_equiv = float(
                    breakdown.get('net_mol_o2_equiv', 0.0)
                )
                reservoir.redox_source_delta_ln_fO2 = float(
                    breakdown.get('delta_ln_fO2', 0.0)
                )
                reservoir.redox_source_delta_log10_fO2 = float(
                    breakdown.get('delta_log10_fO2', 0.0)
                )
                reservoir.redox_source_applied_terms_mol_o2_equiv = dict(
                    breakdown.get('applied_terms_mol_o2_equiv_by_label', {})
                )
                reservoir.redox_source_terms_applied = True
                reservoir.redox_source_skip_reason = ''
                reservoir.ferric_divergence = dict(
                    breakdown.get('ferric_divergence', {})
                )
                self.melt.oxygen_reservoir = reservoir
                self._sync_oxygen_reservoir_mirror()
                if (
                    requested_absorbed_mol - absorbed_mol
                    > OXYGEN_RESERVOIR_NOOP_MOL
                ):
                    reason = 'partial_absorption'
            else:
                absorbed_mol = 0.0
                reason = str(
                    absorption_respeciation.get('reason')
                    or 'fe_redox_respeciation_refused'
                )
        passthrough_mol = max(0.0, actual_injected_mol - absorbed_mol)
        if passthrough_mol > OXYGEN_RESERVOIR_NOOP_MOL:
            transition_source, transition_meta = (
                self._melt_redox_transition_provenance(gate_authority)
            )
            result = self._dispatch_and_commit(
                ChemistryIntent.OXYGEN_BUBBLER,
                control_inputs={
                    'injected_mol': actual_injected_mol,
                    'absorbed_mol': absorbed_mol,
                    'passthrough_mol': passthrough_mol,
                    'source': 'redox_source:o2_bubbler',
                },
                transition_source=transition_source,
                transition_meta=transition_meta,
            )
            if result.transition is None:
                self._chem_no_op_dispatch_count += 1
            self._refresh_oxygen_reservoir_without_exchange(
                exchange_direction='redox_source:o2_bubbler:passthrough',
            )

        injected_kg = actual_injected_mol * OXYGEN_MOLAR_MASS_KG_PER_MOL
        absorbed_kg = absorbed_mol * OXYGEN_MOLAR_MASS_KG_PER_MOL
        passthrough_kg = passthrough_mol * OXYGEN_MOLAR_MASS_KG_PER_MOL
        self._o2_bubbler_injected_kg = injected_kg
        self._o2_bubbler_absorbed_kg = absorbed_kg
        self._o2_bubbler_passthrough_kg = passthrough_kg
        self._o2_bubbler_overhead_passthrough_pending_kg = passthrough_kg
        self._o2_bubbler_injected_cumulative_kg = (
            float(getattr(self, '_o2_bubbler_injected_cumulative_kg', 0.0) or 0.0)
            + injected_kg
        )
        self._o2_bubbler_absorbed_cumulative_kg = (
            float(getattr(self, '_o2_bubbler_absorbed_cumulative_kg', 0.0) or 0.0)
            + absorbed_kg
        )
        self._o2_bubbler_passthrough_cumulative_kg = (
            float(
                getattr(
                    self,
                    '_o2_bubbler_passthrough_cumulative_kg',
                    0.0,
                )
                or 0.0
            )
            + passthrough_kg
        )
        diagnostic = {
            'status': 'ok',
            'reason': reason,
            'rate_kg_per_hr': rate_kg_hr,
            'eta_absorb': eta_absorb,
            'target_fO2_log': target_fO2_log,
            'target_source': target_source,
            'current_fO2_log_before': current_fO2_log,
            'melt_redox_capacity_mol_per_ln_fO2': C_m,
            'commanded_mol': commanded_mol,
            'target_need_mol': target_need_mol,
            'injected_mol': actual_injected_mol,
            'absorbed_mol': absorbed_mol,
            'passthrough_mol': passthrough_mol,
            'injected_kg': injected_kg,
            'absorbed_kg': absorbed_kg,
            'passthrough_kg': passthrough_kg,
            'absorption_respeciation': dict(absorption_respeciation),
        }
        self._last_o2_bubbler_diagnostic = diagnostic
        return diagnostic

    def _attribute_o2_bubbler_vented_from_bleed(
        self,
        bleed_result: IntentResult | None,
    ) -> None:
        pending_kg = max(
            0.0,
            float(
                getattr(
                    self,
                    '_o2_bubbler_overhead_passthrough_pending_kg',
                    0.0,
                )
                or 0.0
            ),
        )
        if pending_kg <= 0.0 or bleed_result is None:
            return
        diagnostic = dict(bleed_result.diagnostic or {})
        vented_kg = max(0.0, float(diagnostic.get('o2_vented_kg', 0.0) or 0.0))
        external_vented_kg = max(
            0.0,
            float(diagnostic.get('external_o2_vented_kg', 0.0) or 0.0),
        )
        if external_vented_kg > 0.0:
            vented_kg = external_vented_kg
        attributed = min(pending_kg, vented_kg)
        self._o2_bubbler_vented_kg = attributed
        self._o2_bubbler_vented_cumulative_kg = (
            float(getattr(self, '_o2_bubbler_vented_cumulative_kg', 0.0) or 0.0)
            + attributed
        )
        self._o2_bubbler_overhead_passthrough_pending_kg = max(
            0.0,
            pending_kg - float(diagnostic.get('bled_o2_kg', 0.0) or 0.0),
        )

    def _reset_redox_source_diagnostics_for_hour(self) -> None:
        self._redox_source_terms_this_hr = {}
        self._redox_source_applied_terms_this_hr = {}
        self._redox_source_skipped_terms_this_hr = {}
        self._redox_source_skip_reasons_this_hr = {}
        self._redox_source_context_this_hr = {}
        self._redox_source_refusal_context_this_hr = {}
        self._redox_source_delta_ln_this_hr = 0.0
        self._last_fe_redox_respeciation_diagnostic = {}
        self._fe_redox_respeciation_diagnostics_this_hr = []
        self._fe_redox_internal_o2_capacity_mol_this_hr = 0.0
        self._fe_redox_internal_o2_consumed_mol_this_hr = 0.0

    def _ledger_ferric_fraction_diagnostic(self) -> Dict[str, Any]:
        melt_mol = self.atom_ledger.mol_by_account('process.cleaned_melt')
        feo_mol = max(0.0, float(melt_mol.get('FeO', 0.0) or 0.0))
        fe2o3_mol = max(0.0, float(melt_mol.get('Fe2O3', 0.0) or 0.0))
        oxidized_fe_mol = feo_mol + 2.0 * fe2o3_mol
        threshold = float(FERRIC_DIVERGENCE_WARNING_THRESHOLD)
        if oxidized_fe_mol <= 0.0:
            return {
                'status': 'no_oxidized_iron',
                'implied_ferric_fraction': 0.0,
                'ledger_ferric_fraction': 0.0,
                'delta_abs': 0.0,
                'warning_threshold_abs': threshold,
                'warning_threshold_ferric_fraction_abs': threshold,
                'sampling_context': 'current_ledger_vs_current_reservoir',
                'warning': False,
            }
        split = self._compute_fe_redox_split_diagnostic()
        implied = max(0.0, min(1.0, float(split.get('ferric_frac', 0.0) or 0.0)))
        ledger = (2.0 * fe2o3_mol) / oxidized_fe_mol
        delta = implied - ledger
        warning = abs(delta) > threshold
        diagnostic = {
            'status': 'warning' if warning else 'ok',
            'implied_ferric_fraction': implied,
            'ledger_ferric_fraction': ledger,
            'delta_abs': abs(delta),
            'delta_signed': delta,
            'warning_threshold_abs': threshold,
            'warning_threshold_ferric_fraction_abs': threshold,
            'sampling_context': 'current_ledger_vs_current_reservoir',
            'warning': warning,
        }
        if warning:
            last_respeciation = dict(
                getattr(self, '_last_fe_redox_respeciation_diagnostic', {}) or {}
            )
            attribution = str(
                last_respeciation.get('residual_attribution')
                or 'respeciation_pending'
            )
            diagnostic['attribution'] = attribution
            diagnostic['reason'] = attribution
        return diagnostic

    def _fe_redox_respeciation_divergence_attribution(
        self,
        diagnostic: Mapping[str, Any],
    ) -> str:
        exchange_direction = str(
            getattr(self.melt.oxygen_reservoir, 'exchange_direction', '') or ''
        )
        reason = str(diagnostic.get('reason', '') or '')
        oxygen_source = str(diagnostic.get('oxygen_source', '') or '')
        unfunded_o2_mol = max(
            0.0,
            float(diagnostic.get('unfunded_o2_mol', 0.0) or 0.0),
        )
        if 'managed_headspace_to_melt' in exchange_direction and reason in {
            'fe_redox_respeciation_o2_unavailable',
            'fe_redox_respeciation_internal_o_unavailable',
            '',
        }:
            return 'managed_floor_unbacked'
        if (
            oxygen_source == FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS
            and unfunded_o2_mol > OXYGEN_RESERVOIR_NOOP_MOL
        ):
            return 'evaporative_internal_o_unbacked'
        if (
            oxygen_source == FE_REDOX_OXYGEN_SOURCE_FO2_BUFFER
            and unfunded_o2_mol > OXYGEN_RESERVOIR_NOOP_MOL
        ):
            return 'fo2_buffer_o_unbacked'
        if reason == 'fe_redox_respeciation_internal_o_unavailable':
            return 'evaporative_internal_o_unbacked'
        if reason == 'fe_redox_respeciation_buffer_o_unavailable':
            return 'fo2_buffer_o_unbacked'
        if reason == 'fe_redox_respeciation_o2_unavailable':
            return 'headspace_o2_unavailable'
        if reason == 'fe_redox_respeciation_feo_unavailable':
            return 'feo_unavailable'
        if reason == 'fe_redox_respeciation_fe2o3_unavailable':
            return 'fe2o3_unavailable'
        return 'respeciation_pending'

    def _remaining_fe_redox_internal_o2_capacity_mol(self) -> float:
        return max(
            0.0,
            float(
                getattr(
                    self,
                    '_fe_redox_internal_o2_capacity_mol_this_hr',
                    0.0,
                )
                or 0.0
            )
            - float(
                getattr(
                    self,
                    '_fe_redox_internal_o2_consumed_mol_this_hr',
                    0.0,
                )
                or 0.0
            ),
        )

    def _has_remaining_fe_redox_internal_o2_capacity(self) -> bool:
        return (
            self._remaining_fe_redox_internal_o2_capacity_mol()
            > OXYGEN_RESERVOIR_NOOP_MOL
        )

    def _apply_fe_redox_respeciation(
        self,
        *,
        oxygen_source: str = FE_REDOX_OXYGEN_SOURCE_OVERHEAD,
        fO2_log_override: Optional[float] = None,
        internal_o2_capacity_mol: Optional[float] = None,
        update_reservoir_state: bool = True,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> Dict[str, Any]:
        # Gate authority is a measurement input to this atomic ledger operation.
        # Consulting two measurements could authorize the fO2 re-reference with
        # fallback physics, then refuse its transition with recovered physics.
        gate_authority = self._resolved_melt_redox_gate_authority(
            gate_authority
        )
        T_K = max(1.0, float(self.melt.temperature_C) + 273.15)
        oxygen_source = str(oxygen_source or FE_REDOX_OXYGEN_SOURCE_OVERHEAD)
        buffer_capacity_mol = 0.0
        if internal_o2_capacity_mol is not None:
            buffer_capacity_mol = max(0.0, float(internal_o2_capacity_mol))
        elif oxygen_source == FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS:
            buffer_capacity_mol = (
                self._remaining_fe_redox_internal_o2_capacity_mol()
            )
        elif oxygen_source == FE_REDOX_OXYGEN_SOURCE_FO2_BUFFER:
            buffer_capacity_mol = max(0.0, float(
                self.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT).get(
                    OXYGEN_SPECIES,
                    0.0,
                )
                or 0.0
            ))
        if not math.isfinite(buffer_capacity_mol):
            raise AccountingError(
                'Fe redox respeciation buffer oxygen capacity must be finite; '
                f'got {internal_o2_capacity_mol!r}'
            )
        if not self._melt_redox_temperature_shift_is_liquid(
            T_K,
            gate_authority=gate_authority,
        ):
            diagnostic = {
                'respeciation_status': 'skipped_solid',
                'status': 'ok',
                'direction': 'none',
                'reason': 'fe_redox_respeciation_not_liquid',
                'oxygen_source': oxygen_source,
                'internal_o2_capacity_mol': buffer_capacity_mol,
            }
            self._sync_oxygen_reservoir_mirror()
            divergence = self._ledger_ferric_fraction_diagnostic()
            if divergence.get('warning'):
                divergence['attribution'] = 'sub_liquid_respeciation_deferred'
                divergence['reason'] = 'sub_liquid_respeciation_deferred'
                diagnostic['residual_attribution'] = (
                    'sub_liquid_respeciation_deferred'
                )
            diagnostic['ferric_divergence_after'] = divergence
            self._last_fe_redox_respeciation_diagnostic = diagnostic
            self._fe_redox_respeciation_diagnostics_this_hr = list(
                getattr(self, '_fe_redox_respeciation_diagnostics_this_hr', []) or []
            )
            self._fe_redox_respeciation_diagnostics_this_hr.append(dict(diagnostic))
            self.melt.oxygen_reservoir.ferric_divergence = dict(divergence)
            return diagnostic
        fO2_log, reference_T_K = self._melt_fO2_reference_state_at_temperature(
            T_K,
            gate_authority=gate_authority,
        )
        if fO2_log_override is not None:
            fO2_log = self._finite_oxygen_reservoir_fO2_log(
                float(fO2_log_override),
                context='fe_redox_respeciation_override',
                candidate_fO2_log=float(fO2_log_override),
            )
        control_inputs = {
            'source': 'scalar Kress91 fO2 ledger re-speciation',
            'o2_account': (
                FO2_BUFFER_ACCOUNT
                if oxygen_source in {
                    FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS,
                    FE_REDOX_OXYGEN_SOURCE_FO2_BUFFER,
                }
                else 'process.overhead_gas'
            ),
            'oxygen_source': oxygen_source,
            'internal_o2_capacity_mol': buffer_capacity_mol,
        }
        result = self._dispatch_only(
            ChemistryIntent.FE_REDOX_RESPECIATION,
            control_inputs=control_inputs,
            fO2_log=fO2_log,
        )
        diagnostic = dict(result.diagnostic or {})
        diagnostic['status'] = str(result.status)
        diagnostic['gate_authority'] = (
            self._melt_redox_gate_authority_provenance(gate_authority)
        )
        proposal = result.transition
        if proposal is None:
            self._chem_no_op_dispatch_count += 1
        else:
            transition_source, transition_meta = (
                self._melt_redox_transition_provenance(gate_authority)
            )
            transition = self._commit_proposal(
                ChemistryIntent.FE_REDOX_RESPECIATION,
                proposal,
                diagnostic=diagnostic,
                control_inputs=control_inputs,
                transition_source=transition_source,
                transition_meta=transition_meta,
            )
            diagnostic['transition_name'] = transition.name
            self._project_cleaned_melt_from_atom_ledger()
            if oxygen_source == FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS:
                self._fe_redox_internal_o2_consumed_mol_this_hr = (
                    float(
                        getattr(
                            self,
                            '_fe_redox_internal_o2_consumed_mol_this_hr',
                            0.0,
                        )
                        or 0.0
                    )
                    + max(
                        0.0,
                        float(diagnostic.get('o2_debit_mol', 0.0) or 0.0),
                    )
                )
        if update_reservoir_state:
            reservoir = self.melt.oxygen_reservoir
            reservoir.melt_intrinsic_fO2_log = fO2_log
            reservoir.reference_T_K = reference_T_K
            self._sync_oxygen_reservoir_mirror()
        divergence = self._ledger_ferric_fraction_diagnostic()
        if (
            divergence.get('warning')
            or str(diagnostic.get('status', '') or '') == 'refused'
            or max(0.0, float(diagnostic.get('unfunded_o2_mol', 0.0) or 0.0))
            > OXYGEN_RESERVOIR_NOOP_MOL
        ):
            attribution = self._fe_redox_respeciation_divergence_attribution(
                diagnostic
            )
            divergence['attribution'] = attribution
            divergence['reason'] = attribution
            diagnostic['residual_attribution'] = attribution
        diagnostic['ferric_divergence_after'] = divergence
        self._last_fe_redox_respeciation_diagnostic = diagnostic
        self._fe_redox_respeciation_diagnostics_this_hr = list(
            getattr(self, '_fe_redox_respeciation_diagnostics_this_hr', []) or []
        )
        self._fe_redox_respeciation_diagnostics_this_hr.append(dict(diagnostic))
        self.melt.oxygen_reservoir.ferric_divergence = dict(
            diagnostic['ferric_divergence_after']
        )
        return diagnostic

    def _redox_source_breakdown_diagnostic(self) -> Dict[str, Any]:
        def _filtered_terms(attr_name: str) -> Dict[str, float]:
            return {
                str(label): float(mol)
                for label, mol in sorted(
                    dict(getattr(self, attr_name, {}) or {}).items()
                )
                if abs(float(mol)) > OXYGEN_RESERVOIR_NOOP_MOL
            }

        terms = _filtered_terms('_redox_source_terms_this_hr')
        respeciation_attempts = [
            dict(attempt)
            for attempt in (
                getattr(self, '_fe_redox_respeciation_diagnostics_this_hr', []) or []
            )
        ]
        if not terms and not respeciation_attempts:
            return {}
        applied_terms = _filtered_terms('_redox_source_applied_terms_this_hr')
        skipped_terms = _filtered_terms('_redox_source_skipped_terms_this_hr')
        all_skip_reasons = dict(
            getattr(self, '_redox_source_skip_reasons_this_hr', {}) or {}
        )
        skipped_reasons = {
            label: str(all_skip_reasons.get(label, ''))
            for label in skipped_terms
            if all_skip_reasons.get(label)
        }
        skip_reason = '|'.join(
            sorted({
                reason
                for value in skipped_reasons.values()
                for reason in str(value).split('|')
                if reason
            })
        )
        delta_ln = float(getattr(self, '_redox_source_delta_ln_this_hr', 0.0))
        source_context = dict(
            getattr(self, '_redox_source_context_this_hr', {}) or {}
        )
        if not source_context:
            source_context = self._redox_source_context_for_current_state()
        refusal_context = dict(
            getattr(self, '_redox_source_refusal_context_this_hr', {}) or {}
        )
        return {
            'terms_mol_o2_equiv_by_label': terms,
            'applied_terms_mol_o2_equiv_by_label': applied_terms,
            'skipped_terms_mol_o2_equiv_by_label': skipped_terms,
            'skipped_reasons_by_label': skipped_reasons,
            'redox_source_terms_applied': bool(applied_terms),
            'redox_source_skip_reason': skip_reason,
            'net_mol_o2_equiv': sum(terms.values()),
            'delta_ln_fO2': delta_ln,
            'delta_log10_fO2': delta_ln / math.log(10.0),
            'ferric_divergence': self._ledger_ferric_fraction_diagnostic(),
            'fe_redox_respeciation': dict(
                getattr(self, '_last_fe_redox_respeciation_diagnostic', {}) or {}
            ),
            'fe_redox_respeciation_attempts': respeciation_attempts,
            'source_context': source_context,
            'redox_source_refusal_context': refusal_context,
            'source_campaign': str(source_context.get('campaign', '')),
            'source_hour': int(source_context.get('hour', 0)),
            'source_campaign_hour': int(source_context.get('campaign_hour', 0)),
        }

    def _apply_oxygen_reservoir_exchange(
        self,
        *,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> OxygenReservoirState:
        gate_authority = self._resolved_melt_redox_gate_authority(
            gate_authority
        )
        T_K = float(self.melt.temperature_C) + 273.15
        if not math.isfinite(T_K) or T_K <= 0.0:
            raise AccountingError(
                'oxygen reservoir exchange requires finite positive T_K; '
                f'temperature_C={self.melt.temperature_C!r}'
            )
        self._re_reference_melt_fO2_to_temperature(
            T_K,
            gate_authority=gate_authority,
        )
        base_fO2_log = self._current_melt_redox_fO2_log()
        reference_T_K = self._current_melt_redox_reference_T_K()
        head_o2_mol = max(0.0, float(
            self.atom_ledger.mol_by_account('process.overhead_gas').get(
                OXYGEN_SPECIES,
                0.0,
            )
        ))
        ledger_pO2 = self._headspace_ledger_pO2_bar_from_o2_mol(head_o2_mol)
        transport_pO2 = self._headspace_transport_pO2_bar_from_ledger(
            ledger_pO2,
            head_o2_mol=head_o2_mol,
        )
        control_floor = self._headspace_control_floor_pO2_bar()
        k_O, k_source = self._oxygen_exchange_k_m_s(T_K)
        h_eff_m = self._oxygen_exchange_effective_melt_depth_m()
        tau_s = h_eff_m / k_O
        alpha = 1.0 - math.exp(-3600.0 / tau_s)
        C_m = self._melt_redox_source_capacity_mol_per_ln_fO2(
            fO2_log=base_fO2_log,
            T_K=T_K,
            gate_authority=gate_authority,
        )
        n_floor_mol = self._headspace_floor_o2_mol()
        effective_floor_mol = self._effective_headspace_floor_o2_mol()
        C_h = max(head_o2_mol, effective_floor_mol)

        reservoir = OxygenReservoirState(
            melt_intrinsic_fO2_log=base_fO2_log,
            reference_T_K=reference_T_K,
            headspace_ledger_pO2_bar=ledger_pO2,
            headspace_transport_pO2_bar=transport_pO2,
            headspace_control_floor_pO2_bar=control_floor,
            k_O_m_s=k_O,
            k_O_source=k_source,
            effective_melt_depth_m=h_eff_m,
            tau_hr=tau_s / 3600.0,
            melt_redox_capacity_mol_per_ln_fO2=C_m,
            headspace_capacity_mol_per_ln_pO2=C_h,
        )

        if C_m <= OXYGEN_RESERVOIR_NOOP_MOL:
            reservoir.exchange_direction = 'none:no_melt_redox_capacity'
            self.melt.oxygen_reservoir = reservoir
            self._sync_oxygen_reservoir_mirror()
            return reservoir
        if C_h <= 0.0:
            reservoir.exchange_direction = 'none:no_headspace_capacity'
            self.melt.oxygen_reservoir = reservoir
            self._sync_oxygen_reservoir_mirror()
            return reservoir

        x_m = base_fO2_log * math.log(10.0)
        effective_transport_pO2 = max(transport_pO2, self._vacuum_floor_bar())
        if not math.isfinite(effective_transport_pO2) or effective_transport_pO2 <= 0.0:
            raise AccountingError(
                'oxygen reservoir exchange requires finite positive transport pO2; '
                f"attribution={self._oxygen_reservoir_guard_context(context='exchange_transport_pO2')!r}"
            )
        x_h = math.log(effective_transport_pO2)
        denominator = (1.0 / C_m + 1.0 / C_h)
        dn_to_headspace = alpha * ((x_m - x_h) / denominator)
        if not math.isfinite(dn_to_headspace):
            self._raise_nonfinite_oxygen_reservoir_fO2(
                dn_to_headspace,
                context='oxygen_reservoir_exchange_delta',
                melt_redox_capacity_mol_per_ln_fO2=C_m,
            )
        dn_ledger_to_headspace = dn_to_headspace
        exchange_clamped = False
        if dn_to_headspace < 0.0:
            max_effective_absorbable = max(0.0, C_h - n_floor_mol)
            if abs(dn_to_headspace) > max_effective_absorbable:
                dn_to_headspace = -max_effective_absorbable
                exchange_clamped = True
            max_real_absorbable = max(0.0, head_o2_mol - n_floor_mol)
            dn_ledger_to_headspace = max(dn_to_headspace, -max_real_absorbable)

        if abs(dn_to_headspace) < OXYGEN_RESERVOIR_NOOP_MOL:
            dn_to_headspace = 0.0
            dn_ledger_to_headspace = 0.0
            reservoir.exchange_direction = (
                'none:headspace_o2_clamped'
                if exchange_clamped
                else 'none:below_threshold'
            )
        else:
            if abs(dn_ledger_to_headspace) >= OXYGEN_RESERVOIR_NOOP_MOL:
                result = self._dispatch_and_commit(
                    ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE,
                    control_inputs={
                        'dn_to_headspace_mol': dn_ledger_to_headspace,
                    },
                )
                reservoir.exchange_transition_name = (
                    result.transition.reason
                    if result.transition is not None
                    else ''
                )
            reservoir.exchange_direction = (
                'melt_to_headspace'
                if dn_to_headspace > 0.0
                else (
                    'headspace_to_melt'
                    if dn_ledger_to_headspace < 0.0
                    else 'managed_headspace_to_melt'
                )
            )

        x_m_after = x_m - dn_to_headspace / C_m
        candidate_fO2_log = x_m_after / math.log(10.0)
        reservoir.melt_intrinsic_fO2_log = self._finite_oxygen_reservoir_fO2_log(
            candidate_fO2_log,
            context='oxygen_reservoir_exchange',
            melt_redox_capacity_mol_per_ln_fO2=C_m,
            delta_ln_fO2=(-dn_to_headspace / C_m),
            candidate_fO2_log=candidate_fO2_log,
        )
        post_head_o2_mol = max(0.0, float(
            self.atom_ledger.mol_by_account('process.overhead_gas').get(
                OXYGEN_SPECIES,
                0.0,
            )
        ))
        post_ledger_pO2 = self._headspace_ledger_pO2_bar_from_o2_mol(
            post_head_o2_mol
        )
        reservoir.headspace_ledger_pO2_bar = post_ledger_pO2
        reservoir.headspace_transport_pO2_bar = (
            self._headspace_transport_pO2_bar_from_ledger(
                post_ledger_pO2,
                head_o2_mol=post_head_o2_mol,
            )
        )
        reservoir.exchange_o2_mol = dn_to_headspace
        reservoir.exchange_o2_kg = (
            dn_to_headspace * OXYGEN_MOLAR_MASS_KG_PER_MOL
        )
        reservoir.exchange_clamped = exchange_clamped
        self.melt.oxygen_reservoir = reservoir
        self._sync_oxygen_reservoir_mirror()
        return reservoir

    def _dispatch_overhead_bleed(
        self,
        *,
        turbine_spec=None,
        force_drain_all: bool = False,
        o2_vented_kg: Optional[float] = None,
    ) -> IntentResult:
        diagnostic = self._overhead_gas_equilibrium_diagnostic()
        species_kg_for_M_avg = self._overhead_holdup_species_kg()
        controls: Dict[str, Any] = {
            'headspace_volume_m3': self._headspace_volume_m3(),
            'headspace_temperature_K': self._headspace_temperature_K(),
            'bleed_conductance_kg_s': (
                self._headspace_bleed_conductance_kg_s(
                    species_kg_for_M_avg=species_kg_for_M_avg,
                )
            ),
            'p_total_bar': float(diagnostic.get('p_total_bar', 0.0) or 0.0),
            'p_downstream_bar': self._headspace_downstream_pressure_bar(),
            'dt_hr': 1.0,
            'force_drain_all': bool(force_drain_all),
            'max_o2_flow_kg_hr': float(
                getattr(turbine_spec, 'max_O2_flow_kg_hr', 0.0) or 0.0
            ),
            'external_o2_in_overhead_mol': (
                self._o2_bubbler_external_o2_overhead_mol()
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
            return math.log10(self._vacuum_floor_bar())
        comp = self._melt_oxide_wt_pct()
        feo = max(0.0, float(comp.get('FeO', 0.0)))
        fe2o3 = max(0.0, float(comp.get('Fe2O3', 0.0)))
        alkali = max(0.0, float(comp.get('Na2O', 0.0))) + max(
            0.0, float(comp.get('K2O', 0.0)))
        # IW buffer fit: anchored at log10(fO2/bar) ~= -7.98 at 1873 K,
        # matching the Phase 1 contract's Kress91 basalt reference.
        # The ferric branch below is NOT dead: sulfate-bearing feedstocks
        # can populate Fe2O3 in cleaned_melt via Stage-0 FeSO4->Fe2O3
        # (core.py:186-194; data/foulant_thermo.yaml:199-215;
        # test_stage0_cation_routing.py:433-434). The ferric 0.25
        # coefficient plus 1e-12 clamp, and the alkali 0.01 / 0.15 cap,
        # are ungrounded constants in the live intrinsic-fO2 path:
        # intrinsic fO2 sets melt.fO2_log every tick (core.py:5398) and
        # feeds evaporation/equilibrium (evaporation.py:255). SSO-R task
        # #41 grounds/replaces this with explicit Fe3+/Fe2+ policy
        # (Kress & Carmichael 1991; fO2 as state variable, fO2->split)
        # in docs-private/research/2026-06-18-staged-selectivity-optimizer/
        # sso-r-fe-redox-design.md. That replacement is golden-affecting
        # for sulfate feedstocks, so it is gated/re-baselined there, not a
        # replace the placeholder implementation.
        log_iw = -27215.0 / T_K + 6.57
        redox_offset = 0.0
        if feo > 0.0 and fe2o3 > 0.0:
            redox_offset += 0.25 * math.log10(max(fe2o3 / feo, 1.0e-12))
        redox_offset += min(0.15, alkali * 0.01)
        return max(
            math.log10(self._vacuum_floor_bar()),
            min(0.0, log_iw + redox_offset),
        )

    def _compute_fe_redox_split_diagnostic(
        self,
        temperature_K: Optional[float] = None,
    ) -> Dict[str, Any]:
        T_K = (
            float(temperature_K)
            if temperature_K is not None
            else float(self.melt.temperature_C) + 273.15
        )
        comp = self._melt_oxide_wt_pct()
        fO2_log = float(
            getattr(
                self.melt.oxygen_reservoir,
                'melt_intrinsic_fO2_log',
                getattr(self.melt, 'melt_fO2_log', -9.0),
            )
        )
        pressure_bar = floor_vacuum_pressure_bar(
            float(self.overhead.pressure_mbar) * 1.0e-3,
            floor_bar=self._vacuum_floor_bar(),
        )
        log_iw = (
            -27215.0 / T_K + 6.57
            if T_K > 0.0
            else math.log10(self._vacuum_floor_bar())
        )
        base = {
            'fO2_log': float(fO2_log),
            'temperature_K': float(T_K),
            'pressure_bar': float(pressure_bar),
            'iw_log': float(log_iw),
            'native_fe_saturation': bool(fO2_log <= log_iw),
            'native_fe_threshold': 'IW',
            'reference': KRESS_CARMICHAEL_1991_REFERENCE,
            'diagnostic_only': True,
        }
        feot_wt = self._feot_equivalent_wt_pct(comp)
        if T_K <= 0.0 or feot_wt <= 0.0:
            return {
                **base,
                'status': 'no_iron',
                'fe3_over_sigma_fe': 0.0,
                'ferric_frac': 0.0,
                'ferrous_frac': 0.0,
                'native_fe_frac': 0.0,
                'fe2o3_over_feo_molar': 0.0,
                'fe2o3_equiv_wt_pct': 0.0,
                'feo_equiv_wt_pct': 0.0,
                'source': 'none:no_iron',
            }

        split = self._fe_redox_split_inline_kress91(
            comp,
            T_K=T_K,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )
        return self._fe_redox_split_payload(
            base,
            split,
            comp=comp,
            T_K=T_K,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )

    @staticmethod
    def _feot_equivalent_wt_pct(comp: Mapping[str, float]) -> float:
        return feot_equivalent_wt_pct(comp)

    def _fe_redox_split_inline_kress91(
        self,
        comp: Mapping[str, float],
        *,
        T_K: float,
        pressure_bar: float,
        fO2_log: float,
    ) -> Dict[str, Any]:
        x = melt_mol_fractions_for_kress91(comp)
        if not x:
            return {
                'status': 'no_iron',
                'fe3_over_sigma_fe': 0.0,
                'fe2o3_over_feo_molar': 0.0,
                'fe2o3_equiv_wt_pct': 0.0,
                'feo_equiv_wt_pct': 0.0,
                'source': 'simulator.fe_redox:kress91_split:no_iron',
                'authoritative': False,
                'extrapolation': False,
                'high_uncertainty': False,
            }
        kress_split = kress91_split(
            fO2_log=fO2_log,
            mol_fractions=x,
            T_K=T_K,
            pressure_bar=pressure_bar,
        )
        ratio = kress_split['ratio']
        fe3 = kress_split['fe3']
        x_fe2o3 = kress_split['x_fe2o3']
        x_feo = kress_split['x_feo']
        weighted_total = (
            x.get('SiO2', 0.0) * 60.0843
            + x.get('TiO2', 0.0) * 79.8788
            + x.get('Al2O3', 0.0) * 101.961
            + x.get('MnO', 0.0) * 70.9375
            + x.get('MgO', 0.0) * 40.3044
            + x.get('CaO', 0.0) * 56.0774
            + x.get('Na2O', 0.0) * 61.9789
            + x.get('K2O', 0.0) * 94.196
            + x.get('P2O5', 0.0) * 141.937
            + x_fe2o3 * 159.687
            + x_feo * 71.844
        )
        if weighted_total <= 0.0:
            fe2o3_wt = 0.0
            feo_wt = 0.0
        else:
            fe2o3_wt = 100.0 * x_fe2o3 * 159.687 / weighted_total
            feo_wt = 100.0 * x_feo * 71.844 / weighted_total
        return {
            'status': 'ok',
            'fe3_over_sigma_fe': fe3,
            'fe2o3_over_feo_molar': ratio,
            'fe2o3_equiv_wt_pct': fe2o3_wt,
            'feo_equiv_wt_pct': feo_wt,
            'source': 'simulator.fe_redox:kress91_split',
            'temperature_band_case': kress_split.get('temperature_band_case'),
            'temperature_band_status': kress_split.get('temperature_band_status'),
            'temperature_band_source': kress_split.get('temperature_band_source'),
            'authoritative': bool(kress_split.get('authoritative', False)),
            'extrapolation': bool(kress_split.get('extrapolation', False)),
            'high_uncertainty': bool(kress_split.get('high_uncertainty', False)),
        }

    def _fe_redox_split_payload(
        self,
        base: Mapping[str, Any],
        split: Mapping[str, Any],
        *,
        comp: Mapping[str, float],
        T_K: float,
        pressure_bar: float,
        fO2_log: float,
    ) -> Dict[str, Any]:
        fe3 = min(1.0, max(0.0, float(split.get('fe3_over_sigma_fe', 0.0))))
        native_extent = self._compute_native_fe_saturation_extent(
            comp,
            fe3_over_sigma_fe=fe3,
            T_K=T_K,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )
        native_state = dict(native_extent['native_fe_state'])
        native = min(1.0, max(0.0, float(
            native_extent.get('native_fe_frac', 0.0) or 0.0,
        )))
        ferrous = max(0.0, 1.0 - fe3 - native)
        return {
            **base,
            **native_state,
            'status': str(split.get('status', 'ok')),
            'fe3_over_sigma_fe': fe3,
            'ferric_frac': fe3,
            'ferrous_frac': ferrous,
            'native_fe_frac': native,
            'fe2o3_over_feo_molar': float(
                split.get('fe2o3_over_feo_molar', 0.0),
            ),
            'fe2o3_equiv_wt_pct': float(
                split.get('fe2o3_equiv_wt_pct', 0.0),
            ),
            'feo_equiv_wt_pct': float(split.get('feo_equiv_wt_pct', 0.0)),
            'source': str(split.get('source', 'unknown')),
            'temperature_band_case': split.get('temperature_band_case'),
            'temperature_band_status': split.get('temperature_band_status'),
            'temperature_band_source': split.get('temperature_band_source'),
            'authoritative': bool(split.get('authoritative', False)),
            'extrapolation': bool(split.get('extrapolation', False)),
            'high_uncertainty': bool(split.get('high_uncertainty', False)),
            **(
                {'native_fe_partition': dict(
                    getattr(self, '_last_native_fe_partition_diagnostic', {})
                    or {}
                )}
                if getattr(self, '_last_native_fe_partition_diagnostic', {})
                else {}
            ),
            # Surface the native-Fe saturation event (deferred /
            # below-threshold / partitioned) so the live HourSnapshot and
            # runner output show WHY there is (or is not) a partition this
            # tick — otherwise the deferred-not-liquid label is write-only.
            **(
                {'native_fe_saturation_event': dict(
                    getattr(self, '_last_native_fe_saturation_event', {})
                    or {}
                )}
                if getattr(self, '_last_native_fe_saturation_event', {})
                else {}
            ),
        }

    def _native_fe_saturation_state(
        self,
        comp: Mapping[str, float],
        *,
        fe3_over_sigma_fe: float,
        T_K: float,
        pressure_bar: float,
        fO2_log: float,
    ) -> Dict[str, Any]:
        fe3 = min(1.0, max(0.0, float(fe3_over_sigma_fe)))
        ferrous_available = max(0.0, 1.0 - fe3)
        if ferrous_available <= 0.0 or self._feot_equivalent_wt_pct(comp) <= 0.0:
            return {
                'native_fe_saturation': False,
                'native_fe_threshold': 'FeO_activity_saturation',
                'native_fe_frac': 0.0,
            }
        try:
            activity = calphad_ferrous_feo_activity_diagnostic(
                comp_wt=comp,
                fO2_log=fO2_log,
                T_K=T_K,
                pressure_bar=pressure_bar,
            )
            a_feo = float(activity.get('a_FeO_authoritative', 0.0) or 0.0)
            pure_feo_iw = feo_iw_log10_fO2_bar(T_K, a_feo=1.0)
        except Exception as exc:
            raise AccountingError(
                'native Fe saturation split requires grounded FeO activity; '
                'CALPHAD/Holzheid FeO activity diagnostic failed'
            ) from exc
        if a_feo <= 0.0 or not math.isfinite(a_feo):
            return {
                'native_fe_saturation': False,
                'native_fe_threshold': 'FeO_activity_saturation',
                'native_fe_frac': 0.0,
                'native_fe_activity_source': 'unavailable:no_positive_a_FeO',
            }
        # FeO(l) = Fe(metal) + 1/2 O2; Holzheid et al. 1997
        # doi:10.1016/S0009-2541(97)00030-2 grounds FeO(l) DeltaG in
        # feo_iw_log10_fO2_bar(), so a_FeO,sat = 10^((log fO2 - IW)/2).
        saturation_log10 = (float(fO2_log) - pure_feo_iw) / 2.0
        if saturation_log10 > 300.0:
            a_feo_saturation = float('inf')
        elif saturation_log10 < -300.0:
            a_feo_saturation = 0.0
        else:
            a_feo_saturation = 10.0 ** saturation_log10
        if a_feo_saturation >= a_feo:
            native = 0.0
        else:
            native = ferrous_available * (1.0 - a_feo_saturation / a_feo)
        native = min(ferrous_available, max(0.0, native))
        return {
            'native_fe_saturation': native > 0.0,
            'native_fe_threshold': 'FeO_activity_saturation',
            'native_fe_frac': native,
            'native_fe_activity': a_feo,
            'native_fe_saturation_activity': a_feo_saturation,
            'native_fe_pure_feo_iw_log': pure_feo_iw,
            'native_fe_activity_source': (
                'Holzheid1997 DOI 10.1016/S0009-2541(97)00030-2 '
                'FeO(l)=Fe+0.5O2 saturation'
            ),
        }

    def _compute_native_fe_saturation_extent(
        self,
        comp: Optional[Mapping[str, float]] = None,
        *,
        fe3_over_sigma_fe: Optional[float] = None,
        T_K: Optional[float] = None,
        pressure_bar: Optional[float] = None,
        fO2_log: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Authoritative native-Fe saturation extent for provider controls.

        The redox diagnostic may copy these fields into its snapshot, but the
        ledger move must be sized here, not from a diagnostic-only payload.
        """
        temperature_K = (
            float(T_K)
            if T_K is not None
            else float(self.melt.temperature_C) + 273.15
        )
        comp_wt = dict(comp) if comp is not None else self._melt_oxide_wt_pct()
        melt_fO2_log = (
            float(fO2_log)
            if fO2_log is not None
            else float(
                getattr(
                    self.melt.oxygen_reservoir,
                    'melt_intrinsic_fO2_log',
                    getattr(self.melt, 'melt_fO2_log', -9.0),
                )
            )
        )
        pressure = (
            float(pressure_bar)
            if pressure_bar is not None
            else floor_vacuum_pressure_bar(
                float(self.overhead.pressure_mbar) * 1.0e-3,
                floor_bar=self._vacuum_floor_bar(),
            )
        )
        feot_wt = self._feot_equivalent_wt_pct(comp_wt)
        if temperature_K <= 0.0 or feot_wt <= 0.0:
            native_state = {
                'native_fe_saturation': False,
                'native_fe_threshold': 'FeO_activity_saturation',
                'native_fe_frac': 0.0,
            }
        else:
            if fe3_over_sigma_fe is None:
                split = self._fe_redox_split_inline_kress91(
                    comp_wt,
                    T_K=temperature_K,
                    pressure_bar=pressure,
                    fO2_log=melt_fO2_log,
                )
                fe3 = float(split.get('fe3_over_sigma_fe', 0.0) or 0.0)
            else:
                fe3 = float(fe3_over_sigma_fe)
            native_state = self._native_fe_saturation_state(
                comp_wt,
                fe3_over_sigma_fe=fe3,
                T_K=temperature_K,
                pressure_bar=pressure,
                fO2_log=melt_fO2_log,
            )
        native_frac = min(1.0, max(0.0, float(
            native_state.get('native_fe_frac', 0.0) or 0.0,
        )))
        cleaned_melt_mol = self.atom_ledger.mol_by_account(
            'process.cleaned_melt')
        feo_mol = max(0.0, float(cleaned_melt_mol.get('FeO', 0.0) or 0.0))
        total_fe_mol = self._cleaned_melt_fe_atom_mol()
        native_fe_mol = min(feo_mol, total_fe_mol * native_frac)
        return {
            'native_fe_state': {**native_state, 'native_fe_frac': native_frac},
            'native_fe_frac': native_frac,
            'native_fe_mol': native_fe_mol,
            'feo_available_mol': feo_mol,
            'total_fe_mol': total_fe_mol,
        }

    def _native_fe_frac_for_trial_fO2(self, fO2_log: float) -> float:
        previous = self._current_melt_redox_fO2_log()
        previous_reference_T_K = self._current_melt_redox_reference_T_K()
        try:
            self.melt.oxygen_reservoir.melt_intrinsic_fO2_log = float(fO2_log)
            self._sync_oxygen_reservoir_mirror()
            extent = self._compute_native_fe_saturation_extent()
            return max(0.0, float(extent.get('native_fe_frac', 0.0) or 0.0))
        finally:
            self.melt.oxygen_reservoir.melt_intrinsic_fO2_log = previous
            self.melt.oxygen_reservoir.reference_T_K = previous_reference_T_K
            self._sync_oxygen_reservoir_mirror()

    def _native_fe_saturation_target_fO2_log(
        self,
        base_fO2_log: float,
        *,
        tolerance: float = 1.0e-12,
    ) -> float:
        if self._native_fe_frac_for_trial_fO2(base_fO2_log) <= tolerance:
            return float(base_fO2_log)
        low = float(base_fO2_log)
        high = low
        for _ in range(64):
            high += 0.25
            if self._native_fe_frac_for_trial_fO2(high) <= tolerance:
                break
        else:
            return high
        for _ in range(48):
            mid = 0.5 * (low + high)
            if self._native_fe_frac_for_trial_fO2(mid) > tolerance:
                low = mid
            else:
                high = mid
        return high

    def _stage3_fe_wt_pct_diagnostic(self) -> Dict[str, float]:
        if len(self.train.stages) <= 3:
            return {
                'stage_3_fe_kg': 0.0,
                'stage_3_total_kg': 0.0,
                'stage_3_fe_wt_pct': 0.0,
            }
        collected = dict(getattr(self.train.stages[3], 'collected_kg', {}) or {})
        fe_kg = max(0.0, float(collected.get('Fe', 0.0) or 0.0))
        total_kg = sum(
            max(0.0, float(kg or 0.0))
            for kg in collected.values()
        )
        return {
            'stage_3_fe_kg': fe_kg,
            'stage_3_total_kg': total_kg,
            'stage_3_fe_wt_pct': (
                100.0 * fe_kg / total_kg
                if total_kg > 0.0
                else 0.0
            ),
        }

    def _native_fe_partition_diagnostic(
        self,
        native_fe_available_mol: float,
    ) -> Dict[str, Any]:
        native_fe_available_mol = max(0.0, float(native_fe_available_mol))
        T_K = max(1.0, float(self.melt.temperature_C) + 273.15)
        fe_row = dict((self.vapor_pressures.get('metals', {}) or {}).get('Fe') or {})
        if not fe_row:
            raise AccountingError(
                'native Fe vapor partition requires data/vapor_pressures.yaml '
                'metals.Fe'
            )

        from engines.builtin.evaporation_flux import (
            DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA,
            DEFAULT_MELT_SURFACE_RENEWAL_SOURCE,
            _evaluate_alpha_control,
            _series_resistance_evaporation_flux_kg_m2_s,
        )
        from engines.builtin.vapor_pressure import (
            COEFF_BLOCK_PURE_COMPONENT,
            _pow10_pressure_or_raise,
            vapor_pressure_antoine_coefficients,
            vapor_pressure_source_label,
        )

        antoine, coefficient_block = vapor_pressure_antoine_coefficients(
            fe_row,
            temperature_K=T_K,
        )
        if coefficient_block != COEFF_BLOCK_PURE_COMPONENT:
            raise AccountingError(
                'native Fe vapor partition requires metals.Fe.'
                'pure_component_antoine; refusing legacy Fe vapor fit'
            )
        A = float(antoine.get('A', 0.0) or 0.0)
        B = float(antoine.get('B', 0.0) or 0.0)
        C = float(antoine.get('C', 0.0) or 0.0)
        if A <= 0.0 or T_K + C <= 0.0:
            raise AccountingError(
                'native Fe vapor partition received invalid Fe '
                'pure_component_antoine coefficients'
            )
        P_reference_Pa = _pow10_pressure_or_raise(
            A - B / (T_K + C),
            species='Fe',
            field='P_reference_Pa',
        )

        molar_mass_g_mol = float(fe_row.get('molar_mass_g_mol', 0.0) or 0.0)
        if molar_mass_g_mol <= 0.0:
            raise AccountingError(
                'native Fe vapor partition requires metals.Fe.molar_mass_g_mol'
            )
        molar_mass_kg_mol = molar_mass_g_mol / 1000.0

        alpha_data = fe_row.get('evaporation_alpha') or {}
        if not isinstance(alpha_data, Mapping) or 'value' not in alpha_data:
            raise AccountingError(
                'native Fe vapor partition requires grounded Fe '
                'evaporation_alpha'
            )
        alpha_spec = alpha_data['value']
        alpha, alpha_evaluation = _evaluate_alpha_control('Fe', T_K, alpha_spec)

        kernel_config = dict(
            getattr(self, 'setpoints', {}).get('chemistry_kernel', {}) or {}
        )
        series_config = dict(
            kernel_config.get('evaporation_series_resistance', {}) or {}
        )
        carrier_resolver = getattr(self, '_resolve_condensation_carrier_gas', None)
        carrier_gas = (
            carrier_resolver()
            if callable(carrier_resolver)
            else 'N2'
        )
        overhead_pressure_mbar = float(
            getattr(self.melt, 'p_total_mbar', 0.0) or 0.0
        )
        pressure_source = 'melt.p_total_mbar'
        if overhead_pressure_mbar <= 0.0:
            overhead_pressure_mbar = float(
                getattr(self.overhead, 'pressure_mbar', 0.0) or 0.0
            )
            pressure_source = 'overhead.pressure_mbar'
        overhead_pressure_pa = max(0.0, overhead_pressure_mbar) * 100.0
        P_bulk_Pa = max(
            0.0,
            float(getattr(self.overhead, 'composition', {}).get('Fe', 0.0) or 0.0)
            * 100.0,
        )
        gas_temperature_K = float(
            getattr(self.overhead, 'headspace_temperature_K', 0.0) or T_K
        )
        melt_resistance_enabled = bool(
            series_config.get('melt_resistance_enabled', True)
        )
        gas_resistance_enabled = bool(
            series_config.get('gas_resistance_enabled', True)
        )
        melt_surface_renewal_base = float(
            series_config.get(
                'melt_surface_renewal_base_kg_s_m2_pa',
                DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA,
            )
            or DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA
        )
        melt_surface_renewal_source = str(
            series_config.get(
                'melt_surface_renewal_source',
                DEFAULT_MELT_SURFACE_RENEWAL_SOURCE,
            )
        )
        series_flux = _series_resistance_evaporation_flux_kg_m2_s(
            species='Fe',
            P_eq_pa=P_reference_Pa,
            P_bulk_pa=P_bulk_Pa,
            T_surface_K=T_K,
            molar_mass_kg_mol=molar_mass_kg_mol,
            alpha_i=alpha,
            pipe_diameter_m=float(
                getattr(self.overhead_model, 'pipe_diameter_m', 0.12)
            ),
            overhead_pressure_pa=overhead_pressure_pa,
            axial_stir_factor=clamp_stir_factor(self.melt.stir_state.axial),
            radial_stir_factor=clamp_stir_factor(self.melt.stir_state.radial),
            carrier_gas=carrier_gas,
            T_gas_K=gas_temperature_K,
            melt_resistance_enabled=melt_resistance_enabled,
            gas_resistance_enabled=gas_resistance_enabled,
            melt_surface_renewal_base_kg_s_m2_pa=melt_surface_renewal_base,
            melt_surface_renewal_source=melt_surface_renewal_source,
        )
        capacity_kg_hr = max(
            0.0,
            float(series_flux.flux_kg_s_m2)
            * max(0.0, float(self.melt.melt_surface_area_m2))
            * 3600.0,
        )
        capacity_mol_hr = capacity_kg_hr / molar_mass_kg_mol
        vapor_mol = min(native_fe_available_mol, capacity_mol_hr)
        tap_mol = native_fe_available_mol - vapor_mol
        pool = native_fe_available_mol
        diagnostic = {
            'native_fe_pool_mol': pool,
            'native_fe_vapor_mol': vapor_mol,
            'native_fe_tap_mol': tap_mol,
            'native_fe_vapor_capacity_mol_hr': capacity_mol_hr,
            'native_fe_vapor_capacity_kg_hr': capacity_kg_hr,
            'ordinary_melt_fe_residual_capacity_mol_hr': max(
                0.0,
                capacity_mol_hr - vapor_mol,
            ),
            'capacity_allocation_rule': 'pool_first_residual',
            'native_pool_activity_argument': (
                'a_Fe(pool) ~= 1 outcompetes dilute melt FeO activity'
            ),
            'native_fe_vapor_escape_fraction_of_pool': (
                vapor_mol / pool if pool > 0.0 else 0.0
            ),
            'P_reference_Antoine_Pa': P_reference_Pa,
            'P_eq_Pa': P_reference_Pa,
            'P_bulk_Pa': P_bulk_Pa,
            'activity_factor': 1.0,
            'temperature_K': T_K,
            'overhead_pressure_pa': overhead_pressure_pa,
            'overhead_pressure_source': pressure_source,
            'carrier_gas': str(carrier_gas),
            'alpha_Fe': float(alpha),
            'alpha_source': str(alpha_data.get('source', '')),
            'alpha_s_evaluation': dict(alpha_evaluation),
            'source_label': vapor_pressure_source_label(
                'native_fe_partition',
                fe_row,
                coefficient_block=coefficient_block,
                temperature_K=T_K,
            ),
            'series_resistance': series_flux.as_diagnostic(),
        }
        return diagnostic

    def _apply_native_fe_saturation_split(
        self,
        *,
        sample_time_h: float | None = None,
        gate_authority: _MeltRedoxGateAuthority | object = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        ),
    ) -> Dict[str, Any]:
        gate_authority = self._resolved_melt_redox_gate_authority(
            gate_authority
        )
        T_K = max(1.0, float(self.melt.temperature_C) + 273.15)
        if not self._melt_redox_temperature_shift_is_liquid(
            T_K,
            gate_authority=gate_authority,
        ):
            split = self._compute_fe_redox_split_diagnostic()
            event = {
                'native_fe_event': 'deferred_not_liquid_for_redox',
                'native_fe_event_reason': 'deferred_not_liquid_for_redox',
                'native_fe_event_status': 'deferred',
                'temperature_C': float(self.melt.temperature_C),
            }
            self._last_native_fe_partition_diagnostic = {}
            self._last_native_fe_saturation_event = dict(event)
            return {**split, **event}

        self._re_reference_melt_fO2_to_temperature(
            T_K,
            gate_authority=gate_authority,
        )
        native_extent = self._compute_native_fe_saturation_extent()
        split = self._compute_fe_redox_split_diagnostic()
        native_frac = max(
            0.0,
            float(native_extent.get('native_fe_frac', 0.0) or 0.0),
        )
        if native_frac <= 1.0e-12:
            event = {
                'native_fe_event': 'no_native_fe_below_threshold',
                'native_fe_event_reason': 'native_fe_frac_below_threshold',
                'native_fe_event_status': 'ok',
            }
            self._last_native_fe_saturation_event = dict(event)
            return {**split, **event}
        native_fe_mol = max(
            0.0,
            float(native_extent.get('native_fe_mol', 0.0) or 0.0),
        )
        if native_fe_mol <= 1.0e-12:
            event = {
                'native_fe_event': 'no_native_fe_below_threshold',
                'native_fe_event_reason': 'native_fe_mol_below_threshold',
                'native_fe_event_status': 'ok',
            }
            self._last_native_fe_saturation_event = dict(event)
            return {**split, **event}
        partition = self._native_fe_partition_diagnostic(native_fe_mol)

        control_inputs = {
            'native_fe_mol': native_fe_mol,
            'native_fe_vapor_mol': float(
                partition.get('native_fe_vapor_mol', 0.0)
            ),
            'native_fe_frac': native_frac,
            'source': 'native Fe saturation FeO split',
        }
        kernel_result = self._dispatch_only(
            ChemistryIntent.NATIVE_FE_SATURATION,
            control_inputs=control_inputs,
        )
        proposal = kernel_result.transition
        diagnostic = dict(kernel_result.diagnostic or {})
        split_commit_status = str(
            getattr(kernel_result, 'status', '')
            or diagnostic.get('status', '')
            or 'ok'
        )
        transition = None
        if proposal is None:
            self._chem_no_op_dispatch_count += 1
            split_commit_status = (
                split_commit_status
                if split_commit_status and split_commit_status != 'ok'
                else 'no_commit'
            )
        else:
            transition_source, transition_meta = (
                self._melt_redox_transition_provenance(gate_authority)
            )
            transition = self._commit_proposal(
                ChemistryIntent.NATIVE_FE_SATURATION,
                proposal,
                diagnostic=diagnostic,
                control_inputs=control_inputs,
                transition_source=transition_source,
                transition_meta=transition_meta,
            )
        self._project_cleaned_melt_from_atom_ledger()
        self._project_drain_tap_from_atom_ledger()
        committed_tap_mol = (
            self._transition_species_mol(
                transition,
                side='credits',
                account='terminal.drain_tap_material',
                species='Fe',
            )
            if transition is not None
            else 0.0
        )
        requested_vapor_mol = float(partition.get('native_fe_vapor_mol', 0.0) or 0.0)
        if transition is None:
            vapor_route = {
                'native_fe_vapor_route_status': 'suppressed_no_committed_split',
                'native_fe_vapor_route_suppressed_mol': requested_vapor_mol,
            }
        else:
            vapor_route = self._route_native_fe_vapor_to_condensation(
                requested_vapor_mol,
                sample_time_h=sample_time_h,
            )
        self._project_cleaned_melt_from_atom_ledger()
        committed_vapor_mol = float(
            vapor_route.get('native_fe_vapor_mol', 0.0) or 0.0
        )
        committed_pool_mol = committed_vapor_mol + committed_tap_mol
        residual_capacity_mol = max(
            0.0,
            float(partition.get('native_fe_vapor_capacity_mol_hr', 0.0) or 0.0)
            - committed_vapor_mol,
        )
        self._native_fe_vapor_residual_capacity_mol_this_hr = residual_capacity_mol
        partition.update({
            'native_fe_split_commit_status': split_commit_status,
            'native_fe_vapor_route_status': str(
                vapor_route.get('native_fe_vapor_route_status', '')
                or 'unknown'
            ),
            'native_fe_vapor_route_suppressed_mol': float(
                vapor_route.get('native_fe_vapor_route_suppressed_mol', 0.0)
                or 0.0
            ),
            'native_fe_pool_mol': committed_pool_mol,
            'native_fe_vapor_mol': committed_vapor_mol,
            'native_fe_tap_mol': committed_tap_mol,
            'ordinary_melt_fe_residual_capacity_mol_hr': residual_capacity_mol,
            'native_fe_uncondensed_mol': float(
                vapor_route.get('native_fe_uncondensed_mol', 0.0) or 0.0
            ),
            'native_fe_uncondensed_fraction_of_pool': (
                float(vapor_route.get('native_fe_uncondensed_mol', 0.0) or 0.0)
                / committed_pool_mol
                if committed_pool_mol > 0.0
                else 0.0
            ),
            'native_fe_condensed_kg': float(
                vapor_route.get('native_fe_condensed_kg', 0.0) or 0.0
            ),
            'native_fe_vapor_escape_fraction_of_pool': (
                committed_vapor_mol / committed_pool_mol
                if committed_pool_mol > 0.0
                else 0.0
            ),
        })
        self._last_native_fe_partition_diagnostic = dict(partition)
        base_fO2_log = self._current_melt_redox_fO2_log()
        target_fO2_log = self._native_fe_saturation_target_fO2_log(base_fO2_log)
        C_m = self._melt_redox_source_capacity_mol_per_ln_fO2(
            fO2_log=base_fO2_log,
            T_K=max(1.0, float(self.melt.temperature_C) + 273.15),
            gate_authority=gate_authority,
        )
        needed_o2_equiv_mol = max(
            0.0,
            C_m * (target_fO2_log - base_fO2_log) * math.log(10.0),
        )
        emitted_o2_mol = (
            self._transition_species_mol(
                transition,
                side='credits',
                account='process.overhead_gas',
                species=OXYGEN_SPECIES,
            )
            if transition is not None
            else 0.0
        ) + float(
            vapor_route.get('native_fe_overhead_o2_mol', 0.0) or 0.0
        )
        self._apply_oxygen_reservoir_redox_source_terms(
            {'redox_source:native_fe_saturation_split': min(
                emitted_o2_mol,
                needed_o2_equiv_mol,
            )},
            exchange_direction='redox_source:native_fe_saturation_split',
            gate_authority=gate_authority,
        )
        if transition is None:
            event_reason = str(
                diagnostic.get('reason_refused')
                or diagnostic.get('reason')
                or 'native_fe_saturation_split_no_commit'
            )
            event = {
                'native_fe_event': 'native_fe_saturation_no_commit',
                'native_fe_event_reason': event_reason,
                'native_fe_event_status': (
                    'refused' if split_commit_status == 'refused' else 'no_commit'
                ),
            }
        else:
            event = {
                'native_fe_event': 'native_fe_partitioned_saturation',
                'native_fe_event_reason': 'native_fe_saturation_split_applied',
                'native_fe_event_status': 'ok',
            }
        self._last_native_fe_saturation_event = dict(event)
        return {**split, 'native_fe_partition': dict(partition), **event}

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
        self.cost_ledger = CostLedger(
            import_context=CostImportContext.from_config(
                self.setpoints.get('cost_model')
            )
        )
        # Per-batch diagnostic state must start clean -- the planner's
        # shadow trace would otherwise accumulate without bound across
        # campaigns / loop sessions.
        self._chem_kernel.clear_shadow_trace()
        # F-A4: the per-batch no-op dispatch counter mirrors the shadow
        # trace lifetime -- a fresh batch starts from zero.
        self._chem_no_op_dispatch_count = 0
        self._melt_redox_liquidus_gate_fallback_diagnostics = deque(
            maxlen=_MELT_REDOX_GATE_FALLBACK_HISTORY_MAXLEN
        )
        self._melt_redox_liquidus_gate_fallback_hourly = deque(
            maxlen=_MELT_REDOX_GATE_FALLBACK_HISTORY_MAXLEN
        )
        self._melt_redox_liquidus_gate_fallback_count = 0
        self._degraded_path_engagement = {}
        self._melt_redox_gate_authority_this_tick = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        )
        self._melt_redox_gate_authority_tick_hour = None
        self._poisoned_hour = None
        label = str(feedstock.get('label', feedstock_key))
        oxidation_specs, oxidized_offgas_kg = (
            self._stage0_oxidation_transition_specs(feedstock))
        carbon_specs = list(self._stage0_carbon_cleanup_specs)
        carbonate_specs = list(self._stage0_carbonate_decomposition_specs)
        carbon_offgas_kg: Dict[str, float] = {}
        for spec in carbon_specs:
            self._merge_masses(carbon_offgas_kg, spec['products_kg'])
        carbonate_offgas_kg: Dict[str, float] = {}
        carbonate_oxide_kg: Dict[str, float] = {}
        for spec in carbonate_specs:
            self._merge_masses(
                carbonate_offgas_kg, spec.get('offgas_products_kg') or {})
            self._merge_masses(
                carbonate_oxide_kg, spec.get('oxide_products_kg') or {})
        cation_sulfate_oxide_kg: Dict[str, float] = {}
        cation_sulfate_sulfide_kg: Dict[str, float] = {}
        for spec in carbon_specs:
            self._merge_masses(
                cation_sulfate_oxide_kg, spec.get('oxide_products_kg') or {})
            self._merge_masses(
                cation_sulfate_sulfide_kg, spec.get('sulfide_products_kg') or {})
        perchlorate_specs = list(self._stage0_perchlorate_cleanup_specs)
        perchlorate_salt_kg: Dict[str, float] = {}
        for spec in perchlorate_specs:
            self._merge_masses(perchlorate_salt_kg, spec['salt_products_kg'])
        generated_offgas_kg = dict(oxidized_offgas_kg)
        self._merge_masses(generated_offgas_kg, carbon_offgas_kg)
        self._merge_masses(generated_offgas_kg, carbonate_offgas_kg)
        terminal_offgas_external = self._subtract_species_kg(
            self.inventory.gas_volatiles_kg,
            generated_offgas_kg,
            context=f'{label} Stage 0 oxidation products',
        )
        terminal_salt_external = dict(self.inventory.salt_phase_kg)
        terminal_chloride_external = self._subtract_species_kg(
            self.inventory.chloride_salt_phase_kg,
            perchlorate_salt_kg,
            context=f'{label} Stage 0 chloride salt products',
        )
        kernel_credited_melt_kg = dict(carbonate_oxide_kg)
        self._merge_masses(kernel_credited_melt_kg, cation_sulfate_oxide_kg)
        terminal_melt_external = self._subtract_species_kg(
            self.inventory.melt_oxide_kg,
            kernel_credited_melt_kg,
            context=f'{label} Stage 0 melt oxide products',
        )
        terminal_sulfide_external = self._subtract_species_kg(
            self.inventory.sulfide_matte_kg,
            cation_sulfate_sulfide_kg,
            context=f'{label} Stage 0 sulfide matte products',
        )

        self._load_ledger_account(
            'process.cleaned_melt',
            terminal_melt_external,
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
            STAGE0_CHLORIDE_SALT_ACCOUNT,
            terminal_chloride_external,
            source=(
                f'{label} Stage 0 separated chloride salt '
                '(re-condensation/fouling risk)'
            ),
        )
        self._load_ledger_account(
            'terminal.stage0_sulfide_matte',
            terminal_sulfide_external,
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
            self.cost_ledger.seed_external_material(
                account=account,
                species=species,
                quantity_kg=kg,
                provenance={
                    'source': f'batch additive {species}',
                    'source_tag': 'owner-ratify-placeholder:external-reagent-seed',
                    'ticket': 'COST-PARAM-REAGENT-KG',
                },
            )

        self._record_stage0_oxidation_transitions(label, oxidation_specs)
        self._record_stage0_carbonate_decomposition_transitions(
            label, carbonate_specs)
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

    def _record_stage0_carbonate_decomposition_transitions(
        self,
        label: str,
        specs: list[dict],
    ) -> None:
        """Kernel-route Stage 0 carbonate thermal-decomposition transitions."""
        from engines.builtin.stage0_pretreatment import (
            REACTION_FAMILY_CARBONATE_DECOMPOSITION,
        )

        feed_account = 'process.stage0_carbonate_feed'
        for spec in specs:
            species = str(spec['species'])
            feed_kg = float(spec['feed_kg'])
            oxide_products_kg = dict(spec.get('oxide_products_kg') or {})
            offgas_products_kg = dict(spec.get('offgas_products_kg') or {})
            if feed_kg <= 1e-12:
                continue
            self.atom_ledger.load_external(
                feed_account,
                {species: feed_kg},
                source=f'{label} Stage 0 {species} carbonate feed',
            )
            self._dispatch_and_commit(
                ChemistryIntent.STAGE0_PRETREATMENT,
                control_inputs={
                    'reaction_family': REACTION_FAMILY_CARBONATE_DECOMPOSITION,
                    'species': species,
                    'feed_kg': feed_kg,
                    'oxide_products_kg': oxide_products_kg,
                    'offgas_products_kg': offgas_products_kg,
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
        semantics (process.stage0_salt_feed, reservoir.stage0_process_gas)
        -- bringing source mass IN from the feedstock inventory is
        distinct from the chemistry-transition payload the kernel
        commits.  C reductant is drawn from ``reservoir.reagent.C`` into
        ``process.reagent_inventory`` before the transition loop (not
        per-transition ``load_external``).
        """
        from engines.builtin.stage0_pretreatment import (
            REACTION_FAMILY_BOUDOUARD,
            REACTION_FAMILY_CATION_SULFATE_CARBON,
            REACTION_FAMILY_SULFATE_CARBON,
        )

        required_c_kg = float(self.inventory.carbon_reductant_required_kg)
        if specs and required_c_kg > 1e-12:
            self._activate_stage0_carbon_reagent(required_c_kg)

        # Map legacy spec names to provider reaction-family
        # discriminators.  The provider rejects anything outside
        # VALID_REACTION_FAMILIES as ``unsupported``; the map below
        # mirrors the two carbon-cleanup reaction IDs the legacy
        # ``_stage0_carbon_cleanup_reaction_ids`` validates exactly.
        SPEC_FAMILY = {
            'stage0_sulfate_carbon_cleanup': REACTION_FAMILY_SULFATE_CARBON,
            'stage0_cation_sulfate_carbon_cleanup': (
                REACTION_FAMILY_CATION_SULFATE_CARBON
            ),
            'stage0_boudouard_carbon_cleanup': REACTION_FAMILY_BOUDOUARD,
        }

        for spec in specs:
            name = str(spec.get('name') or '')
            family = SPEC_FAMILY.get(name)
            if family is None:
                raise AccountingError(
                    f'unsupported Stage 0 carbon cleanup spec {name!r}'
                )
            debits_payload: list[tuple[str, dict[str, float]]] = []
            for account, species_kg in spec['debits']:
                payload = self._ledger_species_kg(species_kg)
                if not payload:
                    continue
                account_name = str(account)
                if account_name == 'process.reagent_inventory':
                    debits_payload.append((account_name, dict(payload)))
                    continue
                self.atom_ledger.load_external(
                    account_name,
                    payload,
                    source=f"{label} {name} feed",
                )
                debits_payload.append((account_name, dict(payload)))
            if not debits_payload:
                continue

            control_inputs = {
                'reaction_family': family,
                'debits': tuple(debits_payload),
                'products_kg': dict(spec.get('products_kg') or {}),
            }
            if family == REACTION_FAMILY_CATION_SULFATE_CARBON:
                control_inputs['oxide_products_kg'] = dict(
                    spec.get('oxide_products_kg') or {})
                control_inputs['sulfide_products_kg'] = dict(
                    spec.get('sulfide_products_kg') or {})
            self._dispatch_and_commit(
                ChemistryIntent.STAGE0_PRETREATMENT,
                control_inputs=control_inputs,
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
        crediting ``terminal.stage0_chloride_salt_phase`` (Cl) + ``terminal.
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
        fO2_log = self._current_melt_redox_fO2_log()
        self._sync_oxygen_reservoir_mirror()
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
        fO2_log = self._current_melt_redox_fO2_log()
        self._sync_oxygen_reservoir_mirror()
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
        spent_reductant_residue = self.atom_ledger.kg_by_account(
            SPENT_REDUCTANT_RESIDUE_ACCOUNT)
        projected_melt = {
            species: float(kg)
            for species, kg in ledger_melt.items()
        }
        for species, kg in spent_reductant_residue.items():
            projected_melt[species] = projected_melt.get(species, 0.0) + float(kg)
        # Project the *full* cleaned_melt account, not just OXIDE_SPECIES: a
        # FactSAGE LIQUID/SOLID phase can legitimately carry non-oxide species
        # (dissolved metallic Fe/Si, a solid mineral). Truncating to oxides
        # would drop that mass.  Na spent-reductant residue is also
        # melt-resident, but kept in its own ledger account so vapor/yield
        # completeness cannot mistake it for feedstock Na.
        self.melt.composition_kg = projected_melt
        # melt_oxide_kg keeps its oxide-only contract (Stage 0 reload path,
        # feedstock inventory snapshots); the non-oxide remainder stays in
        # melt.composition_kg / total_mass_kg only.
        self.inventory.melt_oxide_kg = {
            oxide: float(projected_melt.get(oxide, 0.0))
            for oxide in OXIDE_SPECIES
        }
        self.melt.update_total_mass()

    def _project_drain_tap_from_atom_ledger(self) -> None:
        self.inventory.metal_alloy_kg = {
            species: float(kg)
            for species, kg in self.atom_ledger.kg_by_account(
                'terminal.drain_tap_material').items()
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
            if account == 'process.overhead_gas':
                # Overhead metal vapor is already-evaporated product, not a
                # melt-equilibrium input to feed back into the backend solve.
                for species in METAL_SPECIES:
                    species_mol.pop(species, None)
                species_mol.pop(OXYGEN_SPECIES, None)
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
            return self._record_equilibrium_status(self._internal_analytical_equilibrium())
        if self._backend_failed:
            if cache_write_through:
                raise RuntimeError(
                    self._last_backend_error
                    or 'reduced-real cache miss requires a live backend'
                )
            if not self._backend_allows_internal_analytical_fallback():
                raise RuntimeError(
                    self._last_backend_error
                    or 'configured backend is disabled after failure'
                )
            return self._record_equilibrium_status(self._internal_analytical_equilibrium())
        if not self.backend.is_available():
            if cache_write_through:
                raise RuntimeError(
                    self._last_backend_error
                    or f'{type(self.backend).__name__} is unavailable'
                )
            if not self._backend_allows_internal_analytical_fallback():
                raise RuntimeError(
                    self._last_backend_error
                    or f'{type(self.backend).__name__} is unavailable'
                )
            return self._record_equilibrium_status(self._internal_analytical_equilibrium())

        diagnostic_silicate_equilibrium = False
        try:
            backend_composition_by_account = (
                self._backend_composition_mol_by_account())
            self._validate_backend_account_scope_support(
                backend_composition_by_account)
            intrinsic_fO2_log = float(
                self.melt.oxygen_reservoir.melt_intrinsic_fO2_log)
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
            # Phase 3 owns cache-key identity for this live reservoir control;
            # Phase 1 only mirrors the material-exchange reservoir state.
            self._sync_oxygen_reservoir_mirror()
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
                is_alphamelts_backend = self._is_alphamelts_backend(
                    self.backend
                )
                backend_pressure_bar = (
                    alphamelts_condensed_phase_pressure_bar(
                        pressure_bar,
                        transport=getattr(self.backend, '_mode', None),
                    )
                    if is_alphamelts_backend
                    else pressure_bar
                )
                backend_kwargs = {
                    'temperature_C': temperature_C,
                    'composition_mol': backend_composition_mol,
                    'species_formula_registry': self.species_formula_registry,
                    'fO2_log': intrinsic_fO2_log,
                    'pressure_bar': backend_pressure_bar,
                }
                if self._backend_accepts_kwarg('vapor_transport_pO2_bar'):
                    backend_kwargs['vapor_transport_pO2_bar'] = (
                        self._vapor_pressure_dispatch_pO2_bar()
                    )
                if self._backend_accepts_kwarg('composition_mol_by_account'):
                    backend_kwargs['composition_mol_by_account'] = (
                        backend_composition_by_account)
                if is_alphamelts_backend:
                    backend_kwargs['subprocess_run_mode'] = 'isothermal'
                result = self.backend.equilibrate(**backend_kwargs)
                if is_alphamelts_backend:
                    result = annotate_alphamelts_reference_pressure(
                        result,
                        physical_pressure_bar=pressure_bar,
                        evaluation_pressure_bar=backend_pressure_bar,
                    )
        except AccountingError:
            raise
        except BACKEND_FALLBACK_EXCEPTIONS as exc:
            self._last_backend_error = str(exc)
            self._disable_backend_after_failure()
            if cache_write_through:
                raise
            if not self._backend_allows_internal_analytical_fallback():
                raise
            return self._record_equilibrium_status(self._internal_analytical_equilibrium())
        except ValueError as exc:
            self._last_backend_error = str(exc)
            self._disable_backend_after_failure()
            if cache_write_through:
                raise
            if not self._backend_allows_internal_analytical_fallback():
                raise
            return self._record_equilibrium_status(self._internal_analytical_equilibrium())

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
            if not self._backend_allows_internal_analytical_fallback():
                raise RuntimeError(self._last_backend_error)
            return self._record_equilibrium_status(self._internal_analytical_equilibrium())
        if transition is not None:
            self._validate_backend_ledger_transition(transition)
            balances_before = self.atom_ledger.kg_by_account()
            self._require_chem_kernel().commit_validated_transition(
                ChemistryIntent.BACKEND_EQUILIBRIUM,
                transition,
            )
            self._observe_reagent_provenance_transition(
                transition,
                balances_before,
            )
            self._project_cleaned_melt_from_atom_ledger()
        return self._record_equilibrium_status(result)

    def _record_equilibrium_status(self, result):
        """Record the per-call backend outcome and run the post-equilibrium
        SULFUR_SATURATION_GATE; returns ``result`` unchanged."""
        self._last_backend_status = getattr(result, 'status', 'ok')
        self._backend_status_history.append(str(self._last_backend_status))
        self._last_backend_diagnostics = dict(
            getattr(result, 'diagnostics', {}) or {}
        )
        if (
            str(self._last_backend_status) == 'out_of_domain'
            and self._last_backend_diagnostics
        ):
            self._last_out_of_domain_diagnostics = dict(
                self._last_backend_diagnostics
            )
        # VAPOR_PRESSURE intent — kernel-authoritative.
        #
        # \goal BUILTIN-ENGINE-EXTRACTION (#7), first flip landed. The
        # BuiltinVaporPressureProvider is the authoritative source for
        # vapor pressures; this call replaces result.vapor_pressures_Pa
        # with the kernel diagnostic so downstream consumers
        # (_calculate_evaporation, _route_to_condensation) read from the
        # kernel-owned path. The legacy _internal_analytical_equilibrium still computes
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
        legacy internal analytical path) produces an EquilibriumResult.

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
            The activities dict is replaced too (the internal analytical path
            set both
            atomically and downstream code keys off the same source).
          - If the equilibrium result proves zero liquid fraction,
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
        regime_diagnostic: dict[str, Any] = {}
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
            if (
                melt_regime(
                    liquid_fraction=liquid_fraction,
                    epsilon=0.0,
                    diagnostic=regime_diagnostic,
                    diagnostic_site='core.vapor_pressure.no_liquid_phase',
                    legacy_predicate='liquid_fraction == 0.0',
                )
                == MeltRegime.FROZEN
            ):
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
        pO2_bar = self._vapor_pressure_dispatch_pO2_bar()
        reservoir = getattr(self.melt, 'oxygen_reservoir', None)
        intrinsic_fO2_log = getattr(
            reservoir,
            'melt_intrinsic_fO2_log',
            None,
        )
        if intrinsic_fO2_log is None:
            intrinsic_fO2_log = getattr(self.melt, 'melt_fO2_log', None)
        if intrinsic_fO2_log is None:
            current_fO2 = getattr(self, '_current_melt_redox_fO2_log', None)
            if callable(current_fO2):
                intrinsic_fO2_log = current_fO2()
            else:
                intrinsic_fO2_log = getattr(
                    result,
                    'fO2_log',
                    getattr(self.melt, 'fO2_log', -9.0),
                )
        intrinsic_fO2_log = float(intrinsic_fO2_log)
        vacuum_floor = (
            float(self._vacuum_floor_bar())
            if callable(getattr(self, '_vacuum_floor_bar', None))
            else DEFAULT_VACUUM_FLOOR_BAR
        )
        ambient_pressure_bar = (
            float(getattr(self.melt, 'ambient_pressure_mbar', 0.0) or 0.0)
            / 1000.0
        )
        kernel_result = self._dispatch_only(
            ChemistryIntent.VAPOR_PRESSURE,
            control_inputs={
                'pO2_bar': pO2_bar,
                'intrinsic_fO2_log': intrinsic_fO2_log,
                'vacuum_floor_bar': vacuum_floor,
                'body': getattr(self.melt, 'body', ''),
                'ambient_pressure_bar': (
                    ambient_pressure_bar if ambient_pressure_bar > 0.0 else None
                ),
            },
            fO2_log=intrinsic_fO2_log,
        )
        diagnostic = dict(kernel_result.diagnostic or {})
        diagnostic['backend_vapor_pressures_source'] = dict(backend_sources)
        diagnostic['backend_vapor_pressures_Pa'] = dict(backend_vp)
        diagnostic.update(regime_diagnostic)
        kernel_vp = diagnostic.get('vapor_pressures_Pa') or {}
        if kernel_vp:
            kernel_source = self._kernel_vapor_pressure_source(diagnostic)
            kernel_sources = diagnostic.get('vapor_pressures_source')
            if not isinstance(kernel_sources, Mapping):
                kernel_sources = {}
            merged_vp = dict(kernel_vp)
            merged_sources = {
                str(species): str(
                    kernel_sources.get(species)
                    or kernel_sources.get(str(species))
                    or kernel_source
                )
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
            # internal-analytical/AlphaMELTS pressures with no operator-visible
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
        sources = diagnostic.get('vapor_pressures_source')
        if isinstance(sources, Mapping):
            labels = {str(source) for source in sources.values() if source}
            if len(labels) == 1:
                return next(iter(labels))
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

    def _backend_allows_internal_analytical_fallback(self) -> bool:
        return (
            self.backend is None
            or isinstance(self.backend, InternalAnalyticalBackend)
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
        cation_sulfate_ran = False
        for reaction_id in reaction_ids:
            if reaction_id == 'sulfate_so3_to_so2_co':
                carbon_mol_remaining = self._apply_stage0_sulfate_carbon_reaction(
                    carbon_mol_remaining, specs)
            elif reaction_id == 'co2_boudouard_to_co':
                if not cation_sulfate_ran:
                    carbon_mol_remaining = (
                        self._apply_stage0_cation_sulfate_carbon_reactions(
                            feedstock, carbon_mol_remaining, specs))
                    cation_sulfate_ran = True
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
        if not cation_sulfate_ran:
            carbon_mol_remaining = (
                self._apply_stage0_cation_sulfate_carbon_reactions(
                    feedstock, carbon_mol_remaining, specs)
            )

        carbon_kg_remaining = carbon_mol_remaining * resolve_species_formula(
            'C', self.species_formula_registry).molar_mass_kg_per_mol()
        if carbon_kg_remaining > 1e-9:
            raise AccountingError(
                'Stage 0 carbon cleanup reactions do not consume required C: '
                f'{carbon_kg_remaining:.6g} kg remains'
            )

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
                ('process.reagent_inventory', {'C': c_consumed_kg}),
            ),
            'products_kg': products_kg,
        })
        return carbon_mol_remaining - extent_mol

    def _apply_stage0_cation_sulfate_carbon_reactions(
        self,
        feedstock: Mapping[str, Any],
        carbon_mol_remaining: float,
        specs: list[dict],
    ) -> float:
        if carbon_mol_remaining <= 1e-12:
            return 0.0
        cleanup = feedstock.get('stage0_carbon_cleanup') or {}
        if not isinstance(cleanup, Mapping):
            return carbon_mol_remaining
        product_mode = str(
            cleanup.get('cation_sulfate_product', 'oxide')
        ).lower()
        to_sulfide = product_mode == 'sulfide'
        cation_feed = dict(self.inventory.cation_sulfate_feed_kg)
        if not cation_feed:
            return carbon_mol_remaining

        molar_species = {'C', 'SO2', 'CO'}
        molar_species.update(STAGE0_CATION_SULFATE_OXIDE_PRODUCTS.values())
        molar_species.update(STAGE0_CATION_SULFATE_SULFIDE_PRODUCTS.values())
        molar = {
            species: resolve_species_formula(
                species, self.species_formula_registry
            ).molar_mass_kg_per_mol()
            for species in molar_species
        }
        for species, feed_kg in list(cation_feed.items()):
            if feed_kg <= 1e-12:
                continue
            feed_formula = resolve_species_formula(
                species, self.species_formula_registry)
            feed_mol = feed_kg / feed_formula.molar_mass_kg_per_mol()
            if feed_mol <= 1e-12:
                continue
            if to_sulfide:
                sulfide_species = STAGE0_CATION_SULFATE_SULFIDE_PRODUCTS.get(
                    species)
                stoich = STAGE0_CATION_SULFATE_SULFIDE_STOICH.get(species)
                if sulfide_species is None:
                    raise AccountingError(
                        f'unsupported cation-sulfate sulfide product for '
                        f'{species!r}'
                    )
                if stoich is None:
                    raise AccountingError(
                        f'unsupported cation-sulfate sulfide stoich for '
                        f'{species!r}'
                    )
                feed_coeff = stoich['feed']
                c_per_mol = stoich['C'] / feed_coeff
                co_per_mol = stoich['CO'] / feed_coeff
                sulfide_per_mol = stoich['sulfide'] / feed_coeff
                extent_mol = min(carbon_mol_remaining / c_per_mol, feed_mol)
                if extent_mol <= 1e-12:
                    continue
                c_consumed_kg = extent_mol * c_per_mol * molar['C']
                products_kg = {'CO': co_per_mol * extent_mol * molar['CO']}
                sulfide_products_kg = {
                    sulfide_species: (
                        sulfide_per_mol * extent_mol * molar[sulfide_species]
                    ),
                }
                oxide_products_kg: Dict[str, float] = {}
            else:
                oxide_species = STAGE0_CATION_SULFATE_OXIDE_PRODUCTS.get(
                    species)
                stoich = STAGE0_CATION_SULFATE_OXIDE_STOICH.get(species)
                if oxide_species is None:
                    raise AccountingError(
                        f'unsupported cation-sulfate oxide product for '
                        f'{species!r}'
                    )
                if stoich is None:
                    raise AccountingError(
                        f'unsupported cation-sulfate oxide stoich for '
                        f'{species!r}'
                    )
                feed_coeff = stoich['feed']
                c_per_mol = stoich['C'] / feed_coeff
                so2_per_mol = stoich['SO2'] / feed_coeff
                co_per_mol = stoich['CO'] / feed_coeff
                oxide_per_mol = stoich['oxide'] / feed_coeff
                extent_mol = min(carbon_mol_remaining / c_per_mol, feed_mol)
                if extent_mol <= 1e-12:
                    continue
                c_consumed_kg = extent_mol * c_per_mol * molar['C']
                products_kg = {
                    'SO2': so2_per_mol * extent_mol * molar['SO2'],
                    'CO': co_per_mol * extent_mol * molar['CO'],
                }
                oxide_products_kg = {
                    oxide_species: (
                        oxide_per_mol * extent_mol * molar[oxide_species]
                    ),
                }
                sulfide_products_kg = {}

            consumed_kg = extent_mol * feed_formula.molar_mass_kg_per_mol()
            self._decrease_inventory_species(
                self.inventory.cation_sulfate_feed_kg, species, consumed_kg)
            self._merge_masses(self.inventory.gas_volatiles_kg, products_kg)
            self._merge_masses(self.inventory.stage0_products_kg, products_kg)
            self._merge_masses(
                self.inventory.melt_oxide_kg, oxide_products_kg)
            self._merge_masses(
                self.inventory.sulfide_matte_kg, sulfide_products_kg)
            specs.append({
                'name': 'stage0_cation_sulfate_carbon_cleanup',
                'debits': (
                    ('process.stage0_salt_feed', {species: consumed_kg}),
                    ('process.reagent_inventory', {
                        'C': c_consumed_kg}),
                ),
                'products_kg': products_kg,
                'oxide_products_kg': oxide_products_kg,
                'sulfide_products_kg': sulfide_products_kg,
            })
            carbon_mol_remaining -= extent_mol * c_per_mol
        return carbon_mol_remaining

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
                ('process.reagent_inventory', {'C': c_consumed_kg}),
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
        self._merge_masses(
            self.inventory.chloride_salt_phase_kg, salt_products_kg)
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
        self,
        feedstock: Mapping[str, Any],
        mass_kg: float,
        *,
        feedstock_key: str | None = None,
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
        self._validate_stage0_unmodeled_nitrate_components(raw)
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
        foulant_carrier_snapshot = self._snapshot_stage0_foulant_carriers(
            buckets)
        stage0_external_inputs = self._apply_stage0_offgas_chemistry(
            feedstock, buckets)
        carbonate_specs: list[dict] = []
        self._decompose_stage0_carbonates(
            feedstock, buckets, melt, carbonate_specs, stage0_temp_range)
        self._stage0_carbonate_decomposition_specs = carbonate_specs

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
        self._emit_stage0_foulant_diagnostics(
            feedstock,
            foulant_carrier_snapshot,
            melt,
            feedstock_key=feedstock_key,
        )

        return ProcessInventory(
            raw_components_kg=raw,
            melt_oxide_kg=melt,
            residual_components_kg=residual,
            stage0_products_kg=stage0_products,
            gas_volatiles_kg=buckets['gas_volatiles'],
            salt_phase_kg=buckets['salt_phase'],
            chloride_salt_phase_kg=buckets['chloride_salt_phase'],
            cation_sulfate_feed_kg=buckets['cation_sulfate_feed'],
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

    def _decompose_stage0_carbonates(
        self,
        feedstock: Mapping[str, Any],
        buckets: Dict[str, Dict[str, float]],
        melt: Dict[str, float],
        specs: list[dict],
        stage0_temp_range: Tuple[float, float],
    ) -> None:
        carbonate_bucket = buckets.get('carbonate_feed') or {}
        stage0_final_temp_c = float(stage0_temp_range[1])
        for species, feed_kg in list(carbonate_bucket.items()):
            if feed_kg <= 1e-12:
                continue
            carbonate_bucket.pop(species, None)
            residual_carbonates_kg: Dict[str, float] = {}
            for component, component_kg in self._expand_carbonate_salts_foulant_feed(
                species, feed_kg,
            ):
                oxide_products_kg, offgas_products_kg, consumed_kg, residual_kg = (
                    self._carbonate_decomposition_products(
                        component, component_kg, melt, stage0_final_temp_c)
                )
                if consumed_kg > 1e-12:
                    self._merge_masses(melt, oxide_products_kg)
                    self._merge_masses(
                        buckets.setdefault('gas_volatiles', {}),
                        offgas_products_kg)
                    specs.append({
                        'species': component,
                        'feed_kg': consumed_kg,
                        'oxide_products_kg': oxide_products_kg,
                        'offgas_products_kg': offgas_products_kg,
                    })
                if residual_kg > 1e-12:
                    residual_carbonates_kg[component] = (
                        residual_carbonates_kg.get(component, 0.0)
                        + residual_kg
                    )
            self._merge_masses(
                buckets.setdefault('salt_phase', {}), residual_carbonates_kg)

    def _carbonate_decomposition_products(
        self,
        species: str,
        feed_kg: float,
        melt: Mapping[str, float],
        stage0_final_temp_c: float,
    ) -> Tuple[Dict[str, float], Dict[str, float], float, float]:
        return self._decompose_single_carbonate_species(
            species, feed_kg, melt, stage0_final_temp_c)

    def _decompose_single_carbonate_species(
        self,
        species: str,
        feed_kg: float,
        melt: Mapping[str, float],
        stage0_final_temp_c: float,
    ) -> Tuple[Dict[str, float], Dict[str, float], float, float]:
        formula = resolve_species_formula(
            species, self.species_formula_registry)
        feed_molar_mass = formula.molar_mass_kg_per_mol()
        species_mol = feed_kg / feed_molar_mass
        extent = self._stage0_carbonate_decomposition_extent(
            species, species_mol, melt, stage0_final_temp_c)
        if extent <= 1e-12:
            return {}, {}, 0.0, feed_kg
        consumed_mol = species_mol * extent
        consumed_kg = consumed_mol * feed_molar_mass
        residual_kg = max(0.0, feed_kg - consumed_kg)
        atom_mol = formula.atom_moles(consumed_mol)
        carbon_mol = atom_mol.get('C', 0.0)
        co2_kg = carbon_mol * resolve_species_formula(
            'CO2', self.species_formula_registry).molar_mass_kg_per_mol()
        oxide_kg: Dict[str, float] = {}
        for metal, oxide, atoms_per_oxide in STAGE0_CARBONATE_METAL_OXIDE_STOICH:
            metal_mol = atom_mol.get(metal, 0.0)
            if metal_mol <= 1e-12:
                continue
            oxide_mol = metal_mol / atoms_per_oxide
            oxide_kg[oxide] = (
                oxide_mol
                * resolve_species_formula(
                    oxide, self.species_formula_registry
                ).molar_mass_kg_per_mol()
            )
        return oxide_kg, {'CO2': co2_kg}, consumed_kg, residual_kg

    def _stage0_carbonate_decomposition_extent(
        self,
        species: str,
        species_mol: float,
        melt: Mapping[str, float],
        stage0_final_temp_c: float,
    ) -> float:
        if species_mol <= 1e-12:
            return 0.0

        from engines.builtin.foulant_disposition import chi_decomp

        registry = self._load_foulant_registry_cached()
        thermal_extent = chi_decomp(
            # data/foulant_thermo.yaml REF-012 NIST WebBook dG rows give
            # CaCO3 onset ~=814 C and MgCO3 onset ~=422 C (350-540 C band).
            species, stage0_final_temp_c, 0.0, 0.0, registry).extent
        if species != 'Na2CO3':
            return max(0.0, min(1.0, thermal_extent))
        sio2_mol_available = (
            float(melt.get('SiO2', 0.0))
            / resolve_species_formula(
                'SiO2', self.species_formula_registry).molar_mass_kg_per_mol()
        )
        sio2_gate = min(1.0, sio2_mol_available / species_mol)
        return max(0.0, min(1.0, thermal_extent * sio2_gate))

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
            'chloride_salt_phase': {},
            'carbonate_feed': {},
            'cation_sulfate_feed': {},
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
        for bucket_name in (
            'gas_volatiles', 'salt_phase', 'chloride_salt_phase', 'sulfide_matte',
        ):
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
        buckets = cls._empty_stage0_buckets()
        for component, kg in components.items():
            bucket_name = cls._stage0_bucket_for_name(component)
            if bucket_name is not None:
                buckets[bucket_name][component] = kg
        return buckets

    @classmethod
    def _is_stage0_carbonate_component(cls, component: str) -> bool:
        key = cls._normalized_component_key(component)
        if key in STAGE0_CARBONATE_COMPONENTS:
            return True
        return key.startswith('carbonate_salt')

    @classmethod
    def _is_stage0_cation_sulfate_component(cls, component: str) -> bool:
        key = cls._normalized_component_key(component)
        if key in STAGE0_CATION_SULFATE_COMPONENTS:
            return True
        return str(component).strip() in STAGE0_CATION_SULFATE_OXIDE_PRODUCTS

    @classmethod
    def _is_stage0_nitrate_component(cls, component: str) -> bool:
        key = cls._normalized_component_key(component)
        if key in STAGE0_UNMODELED_NITRATE_MARKERS:
            return True
        if 'nitrate' in key:
            return True
        if key.endswith('no3') or key.endswith('_no3'):
            return True
        return False

    @classmethod
    def _validate_stage0_unmodeled_nitrate_components(
        cls, raw: Mapping[str, float]
    ) -> None:
        for component, kg in raw.items():
            if kg <= 0.0:
                continue
            if cls._is_stage0_nitrate_component(component):
                raise ValueError(
                    f'Stage 0 does not model nitrate component {component!r}; '
                    'nitrates are unmodeled — use an oxide surrogate or '
                    'remove the nitrate key'
                )
            key = cls._normalized_component_key(component)
            if key == 'f':
                raise ValueError(
                    "Stage 0 fluoride routing requires an explicit key "
                    "(CaF2, NaF, fluorite, fluoride); bare 'f' is not accepted"
                )

    @classmethod
    def _is_stage0_refractory_fluoride_component(cls, component: str) -> bool:
        key = cls._normalized_component_key(component)
        return key in STAGE0_REFRACTORY_FLUORIDE_COMPONENTS

    @classmethod
    def _is_stage0_volatile_fluoride_component(cls, component: str) -> bool:
        key = cls._normalized_component_key(component)
        return key in STAGE0_VOLATILE_FLUORIDE_COMPONENTS

    @classmethod
    def _is_stage0_chloride_salt_component(cls, component: str) -> bool:
        key = cls._normalized_component_key(component)
        if key in STAGE0_CHLORIDE_SALT_COMPONENTS:
            return True
        return key.startswith(('nacl', 'kcl'))

    @staticmethod
    def _foulant_thermo_path() -> "Path":
        from pathlib import Path

        return Path(__file__).resolve().parents[1] / "data" / "foulant_thermo.yaml"

    @staticmethod
    def _carbon_partition_path() -> "Path":
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[1]
            / "data"
            / "stage0_carbon_partition.yaml"
        )

    def _load_foulant_registry_cached(self):
        from engines.builtin.foulant_disposition import load_foulant_registry

        cached = getattr(self, "_cached_foulant_registry", None)
        if cached is None:
            cached = load_foulant_registry(self._foulant_thermo_path())
            self._cached_foulant_registry = cached
        return cached

    def _load_carbon_partition_config(self) -> dict:
        import yaml

        cached = getattr(self, "_cached_carbon_partition_config", None)
        if cached is None:
            with self._carbon_partition_path().open(encoding="utf-8") as handle:
                cached = yaml.safe_load(handle) or {}
            self._cached_carbon_partition_config = cached
        return cached

    @staticmethod
    def _foulant_volatilization_phase_specs() -> tuple[dict, ...]:
        return (
            {
                "phase": 1,
                "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
                "p_overhead_bar": STAGE0_FOULANT_PHASE1_OVERHEAD_BAR,
                "pO2_bar": STAGE0_FOULANT_PHASE1_OVERHEAD_BAR,
            },
            {
                "phase": 2,
                "T_C": STAGE0_FOULANT_PHASE2_TEMP_C,
                "p_overhead_bar": STAGE0_FOULANT_PHASE2_OVERHEAD_BAR,
                "pO2_bar": STAGE0_FOULANT_PHASE2_OVERHEAD_BAR,
            },
        )

    @staticmethod
    def _snapshot_stage0_foulant_carriers(
        buckets: Mapping[str, Mapping[str, float]],
    ) -> dict[str, dict[str, float]]:
        return {
            bucket: dict(species)
            for bucket, species in buckets.items()
            if species
        }

    def _expand_carbonate_salts_foulant_feed(
        self, component: str, feed_kg: float,
    ) -> list[tuple[str, float]]:
        key = self._normalized_component_key(component)
        if key != "carbonate_salts":
            return [(component, feed_kg)]
        component_specs = (
            ("MgCO3", 1.0),
            ("CaCO3", 1.0),
            ("Na2CO3", 1.0),
        )
        component_molar = [
            (
                comp_id,
                moles,
                resolve_species_formula(
                    comp_id, self.species_formula_registry,
                ).molar_mass_kg_per_mol(),
            )
            for comp_id, moles in component_specs
        ]
        total_group_mass = sum(
            moles * molar_mass for _, moles, molar_mass in component_molar)
        expanded: list[tuple[str, float]] = []
        for comp_id, moles, molar_mass in component_molar:
            comp_kg = feed_kg * (moles * molar_mass / total_group_mass)
            if comp_kg > 1e-12:
                expanded.append((comp_id, comp_kg))
        return expanded

    def _expand_chloride_foulant_feed(
        self,
        component: str,
        feed_kg: float,
        feedstock: Mapping[str, Any],
    ) -> list[tuple[str, float, dict[str, Any]]]:
        key = self._normalized_component_key(component)
        if key in {"nacl", "kcl"}:
            return [(
                component,
                feed_kg,
                {
                    "feed_basis": "salt_mass",
                    "source_feed_kg": feed_kg,
                },
            )]
        if key not in {"cl", "halide", "halides", "nacl_kcl_salts"}:
            return [(
                component,
                feed_kg,
                {
                    "feed_basis": "as_reported",
                    "source_feed_kg": feed_kg,
                },
            )]
        comp = feedstock.get("composition_wt_pct") or {}
        na = float(comp.get("Na2O", 0.0) or 0.0)
        k = float(comp.get("K2O", 0.0) or 0.0)
        if na + k < 1e-12:
            na_frac, k_frac = 0.5, 0.5
        else:
            total = na + k
            na_frac = na / total
            k_frac = k / total
        cl_molar_mass = resolve_species_formula(
            "Cl",
            self.species_formula_registry,
        ).molar_mass_kg_per_mol()
        expanded: list[tuple[str, float, dict[str, Any]]] = []
        if feed_kg * na_frac > 1e-12:
            cl_kg = feed_kg * na_frac
            salt_molar_mass = resolve_species_formula(
                "NaCl",
                self.species_formula_registry,
            ).molar_mass_kg_per_mol()
            expanded.append((
                "NaCl",
                cl_kg * salt_molar_mass / cl_molar_mass,
                {
                    "feed_basis": "elemental_Cl",
                    "source_feed_kg": feed_kg,
                    "source_cl_kg": cl_kg,
                    "chloride_split_fraction": na_frac,
                    "salt_mass_conversion": "M_NaCl/M_Cl",
                },
            ))
        if feed_kg * k_frac > 1e-12:
            cl_kg = feed_kg * k_frac
            salt_molar_mass = resolve_species_formula(
                "KCl",
                self.species_formula_registry,
            ).molar_mass_kg_per_mol()
            expanded.append((
                "KCl",
                cl_kg * salt_molar_mass / cl_molar_mass,
                {
                    "feed_basis": "elemental_Cl",
                    "source_feed_kg": feed_kg,
                    "source_cl_kg": cl_kg,
                    "chloride_split_fraction": k_frac,
                    "salt_mass_conversion": "M_KCl/M_Cl",
                },
            ))
        return expanded or [(
            component,
            feed_kg,
            {
                "feed_basis": "elemental_Cl",
                "source_feed_kg": feed_kg,
            },
        )]

    def _resolve_foulant_carrier_key(self, component: str) -> str | None:
        registry = self._load_foulant_registry_cached()
        key = self._normalized_component_key(component)
        if key in {"carbonate_salts", "carbonates"}:
            return None
        if key == "carbonate":
            return registry.alias_to_carrier.get(
                "carbonate",
                registry.alias_to_carrier.get("caco3"),
            )
        return registry.alias_to_carrier.get(
            component,
            registry.alias_to_carrier.get(key),
        )

    def _dispatch_stage0_foulant_diagnostic(
        self, control_inputs: Mapping[str, Any]
    ) -> dict | None:
        from engines.builtin.stage0_pretreatment import (
            BuiltinStage0PretreatmentProvider,
        )
        from simulator.chemistry.kernel.dto import ProviderAccountView

        provider = BuiltinStage0PretreatmentProvider()
        view = ProviderAccountView(
            accounts={},
            species_formula_registry=self.species_formula_registry,
        )
        request = IntentRequest(
            intent=ChemistryIntent.STAGE0_PRETREATMENT,
            account_view=view,
            temperature_C=STAGE0_FOULANT_PHASE1_TEMP_C,
            pressure_bar=STAGE0_FOULANT_PHASE1_OVERHEAD_BAR,
            control_inputs=dict(control_inputs),
        )
        result = provider.dispatch(request)
        if result.status != "ok" or not result.diagnostic:
            return None
        return dict(result.diagnostic)

    def _emit_stage0_foulant_diagnostics(
        self,
        feedstock: Mapping[str, Any],
        carrier_snapshot: Mapping[str, Mapping[str, float]],
        melt: Mapping[str, float],
        *,
        feedstock_key: str | None,
    ) -> None:
        from engines.builtin.stage0_pretreatment import (
            REACTION_FAMILY_CARBONATE_DECOMPOSITION,
            REACTION_FAMILY_INERT_TO_RUMP,
            REACTION_FAMILY_PARTITION_CARBON,
            REACTION_FAMILY_PERCHLORATE,
            REACTION_FAMILY_SILICATE_DISPLACEMENT,
            REACTION_FAMILY_SULFATE_DECOMP,
            REACTION_FAMILY_VOLATILIZATION,
        )

        self._stage0_foulant_diagnostics = []
        if not self._foulant_diagnostics_enabled:
            return

        foulant_registry = self._load_foulant_registry_cached()
        thermo_path = str(self._foulant_thermo_path())
        phase_specs = self._foulant_volatilization_phase_specs()
        common = {
            "foulant_registry": foulant_registry,
            "foulant_thermo_path": thermo_path,
        }

        for component, feed_kg in (
            carrier_snapshot.get("chloride_salt_phase") or {}
        ).items():
            for carrier_name, split_kg, basis in self._expand_chloride_foulant_feed(
                component, feed_kg, feedstock,
            ):
                carrier_key = self._resolve_foulant_carrier_key(carrier_name)
                if carrier_key is None:
                    continue
                entry = foulant_registry.carriers.get(carrier_key)
                if entry is None or entry.reaction_family != "volatilization":
                    continue
                diag = self._dispatch_stage0_foulant_diagnostic({
                    **common,
                    "reaction_family": REACTION_FAMILY_VOLATILIZATION,
                    "carrier": entry.carrier_key,
                    "feed_kg": split_kg,
                    "source_component": component,
                    **basis,
                    "phase_specs": phase_specs,
                })
                if diag is not None:
                    diag.update({
                        "source_component": component,
                        **basis,
                    })
                    self._stage0_foulant_diagnostics.append(diag)

        for component, feed_kg in (carrier_snapshot.get("salt_phase") or {}).items():
            key = self._normalized_component_key(component)
            if key not in {"clo4", "perchlorate", "perchlorates"}:
                continue
            carrier_key = self._resolve_foulant_carrier_key(component)
            if carrier_key is None:
                continue
            entry = foulant_registry.carriers.get(carrier_key)
            if entry is None or entry.reaction_family != "perchlorate":
                continue
            molar = {
                species: resolve_species_formula(
                    species,
                    self.species_formula_registry,
                ).molar_mass_kg_per_mol()
                for species in ("ClO4", "Cl", "O2")
            }
            extent_mol = feed_kg / molar["ClO4"]
            diag = self._dispatch_stage0_foulant_diagnostic({
                **common,
                "reaction_family": REACTION_FAMILY_PERCHLORATE,
                "debits": (
                    ("process.stage0_perchlorate_feed", {"ClO4": feed_kg}),
                ),
                "salt_products_kg": {"Cl": extent_mol * molar["Cl"]},
                "oxygen_products_kg": {"O2": 2.0 * extent_mol * molar["O2"]},
            })
            if diag is not None:
                diag.update({
                    "carrier": entry.carrier_key,
                    "feed_kg": feed_kg,
                    "source_component": component,
                    "source_basis": "pseudo_ClO4",
                    "pseudo_species_caveat": (
                        "diagnostic mirrors legacy ClO4 -> Cl + 2 O2 "
                        "pseudo-species cleanup; no Mg/Ca cation route"
                    ),
                    "stage0_phase": "phase_1_oxidizing",
                    "phase": 1,
                    "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
                })
                self._stage0_foulant_diagnostics.append(diag)

        salt_phase_sulfate_proxy = {
            "so3": "CaSO4",
            "sulfate": "CaSO4",
            "sulfates": "CaSO4",
        }
        sulfate_sources: list[tuple[str, float, str]] = []
        for component, feed_kg in (
            carrier_snapshot.get("cation_sulfate_feed") or {}
        ).items():
            carrier_key = self._resolve_foulant_carrier_key(component)
            if carrier_key is not None:
                sulfate_sources.append((component, feed_kg, carrier_key))
        for component, feed_kg in (carrier_snapshot.get("salt_phase") or {}).items():
            key = self._normalized_component_key(component)
            proxy = salt_phase_sulfate_proxy.get(key)
            if proxy is not None:
                sulfate_sources.append((component, feed_kg, proxy))
        for component, feed_kg, carrier_key in sulfate_sources:
            entry = foulant_registry.carriers.get(carrier_key)
            if entry is None or entry.reaction_family != "sulfate_decomp":
                continue
            for phase in phase_specs:
                diag = self._dispatch_stage0_foulant_diagnostic({
                    **common,
                    "reaction_family": REACTION_FAMILY_SULFATE_DECOMP,
                    "carrier": entry.carrier_key,
                    "feed_kg": feed_kg,
                    "source_component": component,
                    "T_C": phase["T_C"],
                    "pO2_bar": phase["pO2_bar"],
                })
                if diag is not None:
                    diag["phase"] = phase["phase"]
                    diag["stage0_phase"] = (
                        "phase_1_oxidizing"
                        if int(phase["phase"]) == 1
                        else "phase_2_vacuum"
                    )
                    self._stage0_foulant_diagnostics.append(diag)

        for component, feed_kg in (carrier_snapshot.get("carbonate_feed") or {}).items():
            if feed_kg <= 1e-12:
                continue
            for carrier_name, split_kg in self._expand_carbonate_salts_foulant_feed(
                component, feed_kg,
            ):
                carrier_key = self._resolve_foulant_carrier_key(carrier_name)
                if carrier_key is None:
                    carrier_key = carrier_name
                entry = foulant_registry.carriers.get(carrier_key)
                if entry is None:
                    continue
                if entry.reaction_family == "silicate_displacement":
                    diag = self._dispatch_stage0_foulant_diagnostic({
                        **common,
                        "reaction_family": REACTION_FAMILY_SILICATE_DISPLACEMENT,
                        "carrier": entry.carrier_key,
                        "feed_kg": split_kg,
                        "source_component": component,
                        "T_C": STAGE0_FOULANT_PHASE2_TEMP_C,
                        "melt_sio2_kg": float(melt.get("SiO2", 0.0)),
                    })
                    if diag is not None:
                        diag["stage0_phase"] = "phase_2_vacuum"
                        self._stage0_foulant_diagnostics.append(diag)
                elif entry.reaction_family == "carbonate_decomposition":
                    diag = self._dispatch_stage0_foulant_diagnostic({
                        **common,
                        "reaction_family": REACTION_FAMILY_CARBONATE_DECOMPOSITION,
                        "diagnostic_only": True,
                        "species": entry.carrier_key,
                        "feed_kg": split_kg,
                        "source_component": component,
                        "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
                    })
                    if diag is not None:
                        diag["stage0_phase"] = "phase_1_oxidizing"
                        self._stage0_foulant_diagnostics.append(diag)

        for component, feed_kg in (carrier_snapshot.get("terminal_slag") or {}).items():
            if not self._is_stage0_refractory_fluoride_component(component):
                continue
            carrier_key = self._resolve_foulant_carrier_key(component) or component
            diag = self._dispatch_stage0_foulant_diagnostic({
                **common,
                "reaction_family": REACTION_FAMILY_INERT_TO_RUMP,
                "carrier": carrier_key,
                "feed_kg": feed_kg,
            })
            if diag is not None:
                self._stage0_foulant_diagnostics.append(diag)

        carbon_row = None
        if feedstock_key:
            carbon_row = (
                self._load_carbon_partition_config()
                .get("phase_partitions", {})
                .get(feedstock_key)
            )
        for component, feed_kg in (carrier_snapshot.get("gas_volatiles") or {}).items():
            key = self._normalized_component_key(component)
            if key not in {
                "c",
                "carbon_content",
                "carbonaceous_organic",
                "organics",
                "hydrocarbons",
            }:
                continue
            if carbon_row is None:
                continue
            diag = self._dispatch_stage0_foulant_diagnostic({
                **common,
                "reaction_family": REACTION_FAMILY_PARTITION_CARBON,
                "carrier": component,
                "feed_kg": feed_kg,
                "carbon_partition_row": carbon_row,
                "phase_specs": phase_specs,
            })
            if diag is not None:
                diag["stage0_phase"] = "phase_1_oxidizing"
                self._stage0_foulant_diagnostics.append(diag)

    @classmethod
    def _stage0_bucket_for_name(cls, component: str) -> Optional[str]:
        key = cls._normalized_component_key(component)
        if key in STAGE0_GAS_COMPONENTS or key.startswith('h2o'):
            return 'gas_volatiles'
        if key.startswith(('co_', 'ch4_', 'nh3_', 'hydrocarbon')):
            return 'gas_volatiles'
        if cls._is_stage0_carbonate_component(component):
            return 'carbonate_feed'
        if cls._is_stage0_cation_sulfate_component(component):
            return 'cation_sulfate_feed'
        if cls._is_stage0_refractory_fluoride_component(component):
            return 'terminal_slag'
        if cls._is_stage0_volatile_fluoride_component(component):
            return 'chloride_salt_phase'
        if cls._is_stage0_chloride_salt_component(component):
            return 'chloride_salt_phase'
        if key in STAGE0_SALT_COMPONENTS:
            return 'salt_phase'
        if key.startswith('sulfuric_acid'):
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
        self._campaign_start_electrical_plus_evaporation_energy = (
            self.energy_electrical_plus_evaporation_cumulative_kWh
        )
        self._campaign_start_O2 = self._oxygen_total_kg()

        # Configure atmosphere and targets from setpoints
        self.campaign_mgr.configure_campaign(self.melt, campaign)
        self.melt.validate_melt_pressures()
        self._configure_overhead_headspace(campaign)
        self._configure_freeze_gate(campaign)
        self._refresh_oxygen_reservoir_without_exchange(
            exchange_direction='none:campaign_start',
        )

        # Initialize shuttle inventory when entering C3 phases
        if campaign in (CampaignPhase.C3_K, CampaignPhase.C3_NA):
            self._init_shuttle_inventory(campaign)
        if campaign == CampaignPhase.C3_NA and self.record.path == 'A_staged':
            self._recompute_staged_na_fe_hold_setpoint()

        # Initialize thermite Mg inventory when entering C6
        if campaign == CampaignPhase.C6:
            self._last_c6_refusal_diagnostic = {}
            self._c6_campaign_refused = False
            self._init_thermite_inventory()

        if campaign == CampaignPhase.C7_CA_ALUMINOTHERMIC:
            self._c7_aluminothermic_applied = False
            self._c7_ca_shuttle_applied = False
            self._init_c7_al_credit()

        # Initialize MRE voltage sequence and reset step tracking      [Step 9c]
        if campaign in (CampaignPhase.MRE_BASELINE, CampaignPhase.C5):
            self._mre_voltage_sequence = self._build_mre_voltage_sequence()
            self._mre_voltage_step_idx = 0
            self._mre_hold_hours = 0
            self._mre_effective_current_A = 0.0
            self._mre_rung_ever_effective = False
            self.melt.mre_low_current_hours = 0
            # Ladder bookkeeping must not leak across campaign (re)starts:
            # a sticky complete flag/key would trip the C5 endpoint (or
            # short-circuit the ladder) on the first hour of a fresh C5.
            self.melt.mre_c5_ladder_complete = False
            self.melt.mre_c5_on_final_rung = None
            self.melt.mre_declared_rung_V = 0.0
            if hasattr(self, '_mre_c5_sequence_complete_key'):
                del self._mre_c5_sequence_complete_key

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

    _OVERLAP_EVAPORATION_RATE_EPS_KG_HR = 1e-6
    _EVAP_PLANE_SELECTIVITY_EPS_KG_HR = 1e-12

    def _c2a_staged_selectivity_targets(
        self,
        rates: Mapping[str, float],
    ) -> tuple[str, ...]:
        if self.melt.campaign != CampaignPhase.C2A_STAGED:
            return ()
        stage_getter = getattr(
            self.campaign_mgr,
            "_c2a_staged_active_stage",
            None,
        )
        stage = (
            stage_getter(self.melt.campaign_hour)
            if callable(stage_getter)
            else None
        )
        if not isinstance(stage, Mapping):
            return ()

        available_species = set(rates)
        return tuple(
            species
            for species in self._diagnostic_target_species(stage.get("target_species"))
            if species in available_species
        )

    def _evap_plane_selectivity_diagnostic(
        self, evap_flux: EvaporationFlux,
    ) -> Dict[str, Any]:
        rates: Dict[str, float] = {}
        for species, raw_rate in sorted((evap_flux.species_kg_hr or {}).items()):
            try:
                rate = float(raw_rate)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(rate) or rate <= self._EVAP_PLANE_SELECTIVITY_EPS_KG_HR:
                continue
            rates[str(species)] = rate

        total_flux = sum(rates.values())
        diagnostic: Dict[str, Any] = {
            "total_flux_kg_hr": total_flux,
            "per_species_fraction": {},
        }
        if total_flux <= self._EVAP_PLANE_SELECTIVITY_EPS_KG_HR:
            return diagnostic

        diagnostic["per_species_fraction"] = {
            species: rate / total_flux
            for species, rate in rates.items()
        }
        target_species = self._c2a_staged_selectivity_targets(rates)
        if target_species:
            target_flux = sum(rates.get(species, 0.0) for species in target_species)
            diagnostic.update({
                "target_species": list(target_species),
                "target_flux_kg_hr": target_flux,
                "target_selectivity": target_flux / total_flux,
            })
        return diagnostic

    @staticmethod
    def _endpoint_species_monitored(cfg: Mapping[str, Any]) -> tuple[str, ...]:
        for key in ("endpoint", "soft_endpoint"):
            endpoint = cfg.get(key)
            if not isinstance(endpoint, Mapping):
                continue
            monitored = PyrolysisSimulator._diagnostic_target_species(
                endpoint.get("species_monitored"))
            if monitored:
                return monitored
        return ()

    def _update_overlap_evaporation_diagnostic(
        self, evap_flux: EvaporationFlux,
    ) -> None:
        """Report off-target evaporation without gating campaign completion.

        Campaign ``target_species`` drives completion contracts only. Species
        assigned to later campaigns (e.g. Mg during C2A) must remain visible
        when their vapor windows overlap the current thermal regime.
        """
        config_getter = getattr(self.campaign_mgr, "_campaign_config", None)
        cfg = config_getter(self.melt.campaign) if callable(config_getter) else {}
        if not isinstance(cfg, Mapping):
            cfg = {}
        target_species = self._diagnostic_target_species(cfg.get("target_species"))
        target_set = set(target_species)
        endpoint_watch = set(self._endpoint_species_monitored(cfg))
        off_target: Dict[str, Dict[str, Any]] = {}
        for species, raw_rate in sorted((evap_flux.species_kg_hr or {}).items()):
            rate_kg_hr = float(raw_rate)
            if rate_kg_hr <= self._OVERLAP_EVAPORATION_RATE_EPS_KG_HR:
                continue
            if species in target_set:
                continue
            stage_number = designated_stage_number(species)
            off_target[species] = {
                "rate_kg_hr": rate_kg_hr,
                "designated_stage_number": stage_number,
                "future_campaign_stage_targets": list(
                    target_species_for_stage_number(stage_number)
                ) if stage_number is not None else [],
                "listed_in_endpoint_watch": (
                    species in endpoint_watch if endpoint_watch else None
                ),
                "gates_completion": False,
            }
        self._last_overlap_evaporation_diagnostic = {
            "campaign": self.melt.campaign.name,
            "campaign_hour": self.melt.campaign_hour,
            "temperature_C": self.melt.temperature_C,
            "completion_target_species": target_species,
            "endpoint_species_monitored": tuple(sorted(endpoint_watch)),
            "off_target_evaporation": off_target,
            "off_target_total_kg_hr": sum(
                row["rate_kg_hr"] for row in off_target.values()
            ),
        }

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
            spent_reductant_residue = (
                queries.spent_reductant_residue_by_species()
            )
            by_target = extraction_completeness_by_target(
                target_species,
                DEFAULT_RESIDUAL_SPECIES_BY_TARGET,
                products,
                rump,
                process_inventory_residual_kg=spent_reductant_residue,
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
                feedstock_recovered_reagent_mol = None
                credit_line_reagent_mol = None
                external_additive_reagent_mol = None
                denominator_basis_source = None
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
                feedstock_recovered_reagent_mol = (
                    result.feedstock_recovered_reagent_target_equiv_mol
                )
                credit_line_reagent_mol = result.credit_line_reagent_target_equiv_mol
                external_additive_reagent_mol = (
                    result.external_additive_reagent_target_equiv_mol
                )
                denominator_basis_source = result.denominator_basis_source
            completeness[target] = fraction
            detail[target] = {
                "product_target_equiv_mol": product_mol,
                "residual_target_equiv_mol": residual_mol,
                "denominator_target_equiv_mol": denom_mol,
                "wall_deposit_target_equiv_mol": wall_mol,
                "reagent_target_equiv_mol": reagent_mol,
                "gross_product_target_equiv_mol": gross_product_mol,
                "contract_id": contract_id,
                "feedstock_recovered_reagent_target_equiv_mol": (
                    feedstock_recovered_reagent_mol
                ),
                "credit_line_reagent_target_equiv_mol": credit_line_reagent_mol,
                "external_additive_reagent_target_equiv_mol": (
                    external_additive_reagent_mol
                ),
                "denominator_basis_source": denominator_basis_source,
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
                raw_liquid_fraction = self._freeze_gate_liquid_fraction_factor()
            except Exception as exc:
                hard_reason = f"unknown: {exc}"
            else:
                liquid_fraction = float(raw_liquid_fraction)
                hard_regime_diagnostic: dict[str, Any] = {}
                hard_would_advance = (
                    melt_regime(
                        liquid_fraction=liquid_fraction,
                        epsilon=0.0,
                        invalid_liquid_fraction_regime=MeltRegime.PARTIAL,
                        diagnostic=hard_regime_diagnostic,
                        diagnostic_site='core.extraction_hard_floor',
                        legacy_predicate='liquid_fraction == 0.0',
                    )
                    == MeltRegime.FROZEN
                )
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
            "process_inventory_spent_reductant_kg": spent_reductant_residue,
            "would_be_soft_advance_by_target_species": soft,
            "would_be_soft_advance_aggregate": aggregate_soft,
            "liquid_fraction": liquid_fraction,
            "would_be_hard_floor_advance": hard_would_advance,
            "hard_floor_status": hard_reason,
            "would_be_cap_advance": cap_would_advance,
            "cap_status": cap_reason,
        }
        if self._freeze_gate_enabled() and 'hard_regime_diagnostic' in locals():
            self._last_extraction_completeness_diagnostic.update(
                hard_regime_diagnostic
            )

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

    def _spent_reductant_residue_by_species(self) -> Dict[str, float]:
        return AccountingQueries(self).spent_reductant_residue_by_species()

    def _terminal_residual_buckets(self) -> Dict[str, Dict[str, float]]:
        return AccountingQueries(self).terminal_residual_buckets()

    def _ledger_total_mass_kg(self) -> float:
        return sum(self.atom_ledger.total_kg_by_account().values())

    def _flow_mass_out_kg(self) -> float:
        totals = self.atom_ledger.total_kg_by_account()
        accounts = set(FLOW_MASS_ACCOUNTS)
        accounts.update(
            account for account in totals
            if account.startswith('reservoir.')
            or account.startswith(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX)
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
        self.record.energy_electrical_plus_evaporation_kWh = (
            self.energy_electrical_plus_evaporation_cumulative_kWh
        )
        energy_breakdown = dict(self.energy_cumulative_breakdown_kWh)
        self.record.energy_breakdown_kWh = energy_breakdown
        self.record.energy_electrical_kWh = energy_breakdown.get('electrical', 0.0)
        self.record.energy_evaporation_thermal_kWh = energy_breakdown.get(
            'evaporation_thermal', 0.0)
        self.record.energy_latent_kWh = energy_breakdown.get('latent', 0.0)
        self.record.energy_dissociation_kWh = energy_breakdown.get(
            'dissociation', 0.0)
        self.record.energy_scope = 'electrical_plus_known_evaporation_enthalpy'
        self.record.furnace_heat_status = 'partial'
        tracker = self._energy_tracker
        self.record.energy_by_campaign = (
            dict(tracker.by_campaign) if tracker is not None else {})
        self.record.energy_by_campaign_breakdown = (
            {
                campaign: dict(values)
                for campaign, values in tracker.by_campaign_breakdown.items()
            }
            if tracker is not None
            else {}
        )
        self.record.cost_rollup = self.cost_ledger.summary()
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

    def _consumed_additive_reagents_kg(self) -> Dict[str, float]:
        consumed: Dict[str, float] = {}
        if 'C' in self._activated_additive_reagents:
            required_kg = float(self.inventory.carbon_reductant_required_kg)
            if required_kg > 1e-9:
                consumed['consumed_C_reagent'] = required_kg
        return consumed

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

        summary = {
            'campaign': campaign_name,
            'duration_h': duration_h,
            'start_mass_kg': round(self._campaign_start_mass, 1),
            'end_mass_kg': round(end_mass, 1),
            'mass_lost_kg': round(mass_lost, 1),
            'energy_electrical_plus_evaporation_kWh': round(
                self.energy_electrical_plus_evaporation_cumulative_kWh
                - self._campaign_start_electrical_plus_evaporation_energy,
                1,
            ),
            'energy_scope': 'electrical_plus_known_evaporation_enthalpy',
            'furnace_heat_status': 'partial',
            'O2_kg': round(
                self._oxygen_total_kg() - self._campaign_start_O2, 2),
            'species_extracted': species_extracted,
            'rump_expectation': rump_expectation,
        }
        if campaign_name == 'C7_CA_ALUMINOTHERMIC':
            summary['c7_product_report'] = dict(
                getattr(self, '_c7_product_report', {}) or {})
        elif campaign_name == 'C6':
            summary['c6_refusal_diagnostic'] = dict(
                getattr(self, '_last_c6_refusal_diagnostic', {}) or {})
        return summary

    # ------------------------------------------------------------------
    # THE CORE LOOP
    # ------------------------------------------------------------------

    def step(self) -> HourSnapshot:
        """Advance one hour, refusing replay after a partial commit."""
        poisoned = self._poisoned_hour
        if poisoned is not None:
            raise PoisonedHourError(poisoned)

        # Conservative fail-closed residuals accepted for future hardening:
        # completion-bookkeeping failures can poison an advanced hour,
        # load_external* mutations are uncounted, and empty appended
        # transitions can poison unchanged balances.
        attempt_hour = int(self.melt.hour)
        transition_count_before = len(self.atom_ledger.transitions)
        try:
            return self._step_one_hour()
        except BaseException as exc:
            self._pending_shuttle_bakeout_cycle_increment = ''
            committed_transition_count = max(
                0,
                len(self.atom_ledger.transitions) - transition_count_before,
            )
            if committed_transition_count:
                # AtomLedger is append-only. Whole-hour rollback would require
                # compensating transitions and is a separate design change.
                self._poisoned_hour = PoisonedHourState(
                    hour=attempt_hour,
                    committed_transition_count=committed_transition_count,
                    aborting_exception_summary='exception summary unavailable',
                )
                try:
                    self._poisoned_hour = PoisonedHourState(
                        hour=attempt_hour,
                        committed_transition_count=committed_transition_count,
                        aborting_exception_summary=(
                            f'{type(exc).__name__}: {exc}'
                        ),
                    )
                except BaseException:
                    pass
            raise

    def _step_one_hour(self) -> HourSnapshot:
        """
        Advance the simulation by one hour.

        This is the main simulation loop.  Each call:

        1. Check if paused for a decision
        2. Update temperature (ramp toward campaign target)
        3. Apply passive oxygen-reservoir exchange from carried-in headspace
        4. Apply configured O2 bubbler redox source/pass-through
        5. Commit C3/MRE/C6/C7 source-producing reactions
        6. Repartition FeO/Fe2O3 from the post-source redox state
        7. Apply native-Fe split from the re-speciated melt state
        8. Refresh thermodynamic equilibrium (via melt backend)
        9. Calculate evaporation flux and route it through condensation
        10. Repartition any evaporation-induced FeO/Fe2O3 redox shift
        11. Update overhead gas & turbine pressure
        12. Calculate energy consumption this hour
        13. Check campaign endpoint criteria
        14. Record snapshot

        Returns:
            HourSnapshot with full system state at this hour
        """
        self._last_condensed_by_stage_species_delta = {}
        self._last_wall_deposit_by_segment_species_delta = {}
        self._last_impurity_delta = {}
        self._last_native_fe_partition_diagnostic = {}
        self._last_native_fe_saturation_event = {}
        self._native_fe_vapor_residual_capacity_mol_this_hr = None
        self._last_partial_melt_offgassing_diagnostic = {}
        self._last_extraction_completeness_diagnostic = {}
        self._last_overlap_evaporation_diagnostic = {}
        self._pending_shuttle_bakeout_cycle_increment = ''
        self._reset_redox_source_diagnostics_for_hour()
        self._reset_o2_bubbler_telemetry_for_hour()

        # --- 1. Decision check ---
        if self.paused_for_decision:
            # Return current state without advancing
            return self._make_snapshot()
        self._mre_anode_O2_kg_this_hr = 0.0

        sample_time_h = float(self.melt.campaign_hour) + 1.0
        self.campaign_mgr.apply_lab_schedule_controls(
            self.melt,
            self.melt.campaign,
            sample_time_h=sample_time_h,
        )
        self.campaign_mgr.apply_c2a_staged_gas_controls(self.melt)
        self._sync_c2a_staged_overhead_gas_control()
        self.melt.validate_melt_pressures()
        self.validate_lab_surface_temperature_resolver()

        # --- 2. Temperature ramp and carried-in passive exchange ---
        self._update_temperature()
        self._apply_c2a_knudsen_pressure_adjustment()
        self._establish_melt_redox_gate_authority_for_current_hour()
        self._apply_oxygen_reservoir_exchange()
        self._apply_o2_bubbler()

        c3_shuttle_campaign = self.melt.campaign in (
            CampaignPhase.C3_K, CampaignPhase.C3_NA,
        )
        mre_O2_kg = 0.0
        mre_energy_kWh = 0.0

        # --- 3. Source-producing reactions before native Fe split ---
        # Alkali shuttle injection (C3 only) ---                 [THERMO-5]
        # During C3, the shuttle cycle alternates between injection
        # (alkali reduces target oxides) and bakeout (alkali oxide
        # evaporates, alkali recovered).  Injection modifies the melt
        # composition directly; bakeout uses normal evaporation.
        if c3_shuttle_campaign:
            self._step_shuttle()
        else:
            self._shuttle_injected_this_hr = 0.0
            self._shuttle_reduced_this_hr = 0.0
            self._shuttle_metal_this_hr = 0.0
            self._shuttle_phase = ''

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
            self._mre_ellingham_ladder_diagnostic = {}
            self._mre_energy_this_hr = 0.0
            self._mre_anode_O2_kg_this_hr = 0.0

        c6_hold_reached = False
        if self.melt.campaign == CampaignPhase.C6:
            c6_target_T, _ = self.campaign_mgr.get_temp_target(
                self.melt.campaign, self.melt.campaign_hour, self.melt)
            c6_hold_reached = (
                c6_target_T is not None
                and abs(float(c6_target_T) - self.melt.temperature_C) < 0.1
            )
        if c6_hold_reached:
            self._step_thermite()
        else:
            self._thermite_Al2O3_reduced_this_hr = 0.0
            self._thermite_Al_produced_this_hr = 0.0
            self._thermite_Mg_consumed_this_hr = 0.0

        if self.melt.campaign == CampaignPhase.C7_CA_ALUMINOTHERMIC:
            self._step_c7_ca_aluminothermic()

        evaporation_campaign = self.melt.campaign in (
            CampaignPhase.C0, CampaignPhase.C0B,
            CampaignPhase.C2A, CampaignPhase.C2A_STAGED,
            CampaignPhase.C2B, CampaignPhase.C3_K,
            CampaignPhase.C3_NA, CampaignPhase.C4,
        )
        self._apply_fe_redox_respeciation()
        self._apply_native_fe_saturation_split(sample_time_h=sample_time_h)
        self._refresh_oxygen_reservoir_transport_pO2_for_vapor()

        # --- 4. Thermodynamic equilibrium ---
        # Query the melt backend for phase assemblage, activities,
        # and vapor pressures at the post-source, post-native composition.
        equilibrium = self._get_equilibrium()

        # --- 4b. Evaporation flux ---
        # Hertz-Knudsen-Langmuir: how fast each species leaves the melt.
        # Only during pyrolysis campaigns (C0, C2A, C2B, C3 bakeout, C4).
        # Not during MRE (C5) — electrolysis produces O₂ at the anode.
        evap_flux = EvaporationFlux()
        if evaporation_campaign:
            evap_flux = self._calculate_evaporation(equilibrium)
            evap_flux = self._apply_analytic_evaporation_depletion(evap_flux)

        # --- 5. Condensation routing ---
        # Send evaporated species through the 8-stage train.
        # Each stage collects species based on its temperature.
        if evap_flux.total_kg_hr > 0:
            self._configure_condensation_operating_conditions(evap_flux)
            self._apply_lab_surface_temperatures(sample_time_h=sample_time_h)
            self._route_to_condensation(evap_flux)
        self._pending_knudsen_zero_overhead_flow_marker = None

        # --- 6. Update melt composition ---
        # Subtract evaporated mass from the melt.
        self._update_melt_composition(evap_flux)
        if self._has_remaining_fe_redox_internal_o2_capacity():
            self._apply_fe_redox_respeciation(
                oxygen_source=FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS,
            )
        else:
            self._apply_fe_redox_respeciation()

        # --- 7. Overhead gas (with turbine capacity feedback) ---   [LOOP-2]
        # Pass the turbine spec so overhead model can enforce capacity limits,
        # compute O₂ venting, and calculate transport saturation.
        turbine_spec = self._get_turbine_spec()
        # The AtomLedger is the canonical quantity authority (see AGENTS.md),
        # so the turbine/vent decision is fed strictly the actual finite O2
        # holdup in process.overhead_gas. This is NOT max()'d with a per-tick
        # production counter: the holdup already includes this tick's real
        # overhead O2 plus any O2 carried over from prior ticks not yet drained.
        # max()-ing the two
        # overlapping quantities would let the turbine see carried-over O2 as
        # fresh throughput, or mask the case where holdup < this-hour output.
        finite_headspace_enabled = self._overhead_headspace_enabled()
        bleed_result = None
        if finite_headspace_enabled:
            bleed_result = self._dispatch_overhead_bleed(
                turbine_spec=turbine_spec)
            self._attribute_o2_bubbler_vented_from_bleed(bleed_result)
            self._refresh_oxygen_reservoir_transport_pO2_for_vapor()
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
            bleed_conductance_kg_s=(
                self._headspace_bleed_conductance_kg_s()))

        # Track cumulative O₂ vented and stored
        if not finite_headspace_enabled:
            bleed_result = self._dispatch_overhead_bleed(
                turbine_spec=turbine_spec,
                force_drain_all=True,
                o2_vented_kg=self.overhead.O2_vented_kg_hr,
            )
            self._attribute_o2_bubbler_vented_from_bleed(bleed_result)
            self._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        self._sync_oxygen_kg_counters()
        self._refresh_knudsen_zero_overhead_flow_marker(evap_flux)

        # --- 8. Energy ---
        energy = self.energy_tracker.calculate_hour(
            self.melt, self.overhead, evap_flux,
            mre_kWh=mre_energy_kWh,  # Actual MRE energy this hour
            vapor_pressures=self.vapor_pressures,
        )
        self.energy_electrical_plus_evaporation_cumulative_kWh += (
            energy.electrical_plus_evaporation_kWh
        )
        self.energy_cumulative_breakdown_kWh = (
            self.energy_tracker.cumulative_breakdown())

        self._update_overlap_evaporation_diagnostic(evap_flux)
        self._update_extraction_completeness_diagnostic()
        evap_plane_selectivity = self._evap_plane_selectivity_diagnostic(
            evap_flux,
        )
        fe_redox_split = self._compute_fe_redox_split_diagnostic()

        # --- 9. Endpoint check ---
        next_campaign: CampaignPhase | None = None
        pending_decision: Any | None = None
        complete_after_snapshot = False
        c6_refused = (
            self.melt.campaign == CampaignPhase.C6
            and self._c6_campaign_refused
        )
        campaign_done = c6_refused or self.campaign_mgr.check_endpoint(
            self.melt, evap_flux, self.train, self.record)
        if campaign_done:
            # Capture campaign summary before transitioning
            finishing_campaign = self.melt.campaign.name
            self._last_campaign_summary = self._capture_campaign_summary(
                finishing_campaign)

            next_campaign = self.campaign_mgr.get_next_campaign(
                self.melt.campaign, self.record)
            if next_campaign == CampaignPhase.COMPLETE:
                complete_after_snapshot = True
                next_campaign = None
            elif next_campaign is None:
                # Decision needed before proceeding
                pending_decision = self.campaign_mgr.get_decision(
                    self.melt.campaign, self.record)

        # --- 10. Record snapshot ---
        completed_hour = int(self.melt.hour)
        self.melt.hour += 1
        self.melt.campaign_hour += 1
        self._stamp_redox_source_context_for_current_state(force=True)
        snapshot = self._make_snapshot()
        snapshot.evap_flux = evap_flux
        snapshot.evap_plane_selectivity = evap_plane_selectivity
        snapshot.partial_melt_offgassing_diagnostic = dict(
            self._last_partial_melt_offgassing_diagnostic
        )
        snapshot.fe_redox_split = fe_redox_split
        snapshot.redox_source_breakdown = self._redox_source_breakdown_diagnostic()
        snapshot.energy = energy
        snapshot.energy_electrical_plus_evaporation_cumulative_kWh = (
            self.energy_electrical_plus_evaporation_cumulative_kWh
        )
        snapshot.energy_cumulative_breakdown_kWh = dict(
            self.energy_cumulative_breakdown_kWh)
        snapshot.oxygen_produced_kg = self._oxygen_total_kg()
        self.record.snapshots.append(snapshot)
        pending_shuttle_cycle = str(
            getattr(self, '_pending_shuttle_bakeout_cycle_increment', '') or '')
        if pending_shuttle_cycle == CampaignPhase.C3_K.name:
            self.shuttle_cycle_K += 1
        elif pending_shuttle_cycle == CampaignPhase.C3_NA.name:
            self.shuttle_cycle_Na += 1
        self._pending_shuttle_bakeout_cycle_increment = ''
        self._clear_melt_redox_gate_authority_for_completed_hour(completed_hour)

        if complete_after_snapshot:
            self.melt.campaign = CampaignPhase.COMPLETE
        elif pending_decision is not None:
            self.pending_decision = pending_decision
            self.paused_for_decision = True
        elif next_campaign is not None:
            self.start_campaign(next_campaign)

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

        target_T = float(target_T)
        ramp_rate = float(ramp_rate)
        if not math.isfinite(target_T):
            raise ValueError(
                f'campaign temperature target must be finite; got {target_T!r}')
        if not math.isfinite(ramp_rate) or ramp_rate < 0.0:
            raise ValueError(
                f'campaign ramp rate must be finite and non-negative; got {ramp_rate!r}')

        nominal_ramp = ramp_rate
        actual_ramp = ramp_rate
        throttle_reason = ''

        # --- Loop 3: Metals train transport saturation throttle ---
        sat_pct = float(self.overhead.transport_saturation_pct)
        if not math.isfinite(sat_pct):
            raise ValueError(
                'overhead.transport_saturation_pct must be finite before '
                f'temperature mutation; got {sat_pct!r}')
        if sat_pct > 100.0:
            # Linear scale: 100% → full ramp, 200% → zero ramp
            scale = max(0.0, 2.0 - sat_pct / 100.0)
            actual_ramp *= scale
            throttle_reason = f'pipe saturated ({sat_pct:.0f}%)'

        # --- Loop 3b: Turbine overload (milder throttle) ---
        if self.overhead.turbine_limited:
            turb_util = float(self.overhead.turbine_utilization_pct)
            if not math.isfinite(turb_util):
                raise ValueError(
                    'overhead.turbine_utilization_pct must be finite before '
                    f'temperature mutation; got {turb_util!r}')
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
                if not math.isfinite(vol_sat):
                    raise ValueError(
                        'volatiles transport saturation must be finite before '
                        f'temperature mutation; got {vol_sat!r}')
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
            equipment_setpoints = dict(self.setpoints)
            equipment_setpoints['condenser_geometry'] = copy.deepcopy(
                self._overhead_condenser_geometry_config
            )
            designer = EquipmentDesigner(equipment_setpoints)
            feedstock = self.feedstocks.get(self.record.feedstock_key, {})
            self._equipment = designer.design_for_batch(
                self.record.batch_mass_kg,
                feedstock,
                lab_geometry=self.lab_geometry,
            )
            # Also store volatiles train spec for Loop 4 throttle
            self._volatiles_train_spec = self._equipment.volatiles_train
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
        choice = str(choice)
        if decision_type not in _VALID_DECISION_CHOICES:
            raise ValueError(f"unsupported decision type {decision_type.name}")
        pending = self.pending_decision
        if pending is not None:
            if pending.decision_type is not decision_type:
                raise ValueError(
                    f"pending decision is {pending.decision_type.name}, "
                    f"got {decision_type.name}"
                )
            valid_choices = tuple(str(option) for option in pending.options)
        else:
            valid_choices = _VALID_DECISION_CHOICES.get(decision_type, ())
        if valid_choices and choice not in valid_choices:
            raise ValueError(
                f"invalid {decision_type.name} choice {choice!r}; "
                f"valid choices: {', '.join(valid_choices)}"
            )
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
            elif self.melt.c5_enabled:
                self.start_campaign(CampaignPhase.C5)
            else:
                self.melt.campaign = CampaignPhase.COMPLETE

        elif decision_type == DecisionType.TI_RETENTION:
            # Handled within C5 campaign logic
            pass

        elif decision_type == DecisionType.C6_PROCEED:
            if choice == 'yes':
                self.start_campaign(CampaignPhase.C6)
            else:
                self.melt.campaign = CampaignPhase.COMPLETE

        elif decision_type == DecisionType.C7_PROCEED:
            if choice == 'yes':
                self.start_campaign(CampaignPhase.C7_CA_ALUMINOTHERMIC)
            else:
                self.melt.campaign = CampaignPhase.COMPLETE

        else:
            raise ValueError(f"unsupported decision type {decision_type.name}")

        self.paused_for_decision = False
        self.pending_decision = None
        if self.is_complete():
            self._finalize_record()

    # ------------------------------------------------------------------
    # Snapshot construction
    # ------------------------------------------------------------------

    def _refresh_knudsen_zero_overhead_flow_marker(
        self,
        evap_flux: EvaporationFlux,
    ) -> None:
        if not self._total_overhead_flow_is_finite_zero(evap_flux):
            self._pending_knudsen_zero_overhead_flow_marker = None
            return
        self._pending_knudsen_zero_overhead_flow_marker = {
            'status': 'not_applicable',
            'reason': KNUDSEN_NOT_APPLICABLE_ZERO_OVERHEAD_FLOW,
            'provenance': KNUDSEN_NOT_APPLICABLE_ZERO_OVERHEAD_FLOW,
            'evap_flux_total_kg_hr': 0.0,
            'O2_vented_kg_hr': 0.0,
            'O2_vented_mol_hr': 0.0,
            'melt_offgas_O2_mol_hr': 0.0,
            'mre_anode_O2_mol_hr': 0.0,
            'turbine_flow_kg_hr': 0.0,
            'turbine_flow_mol_hr': 0.0,
        }

    def _total_overhead_flow_is_finite_zero(
        self,
        evap_flux: EvaporationFlux,
    ) -> bool:
        overhead = self.overhead
        return (
            self._evap_flux_is_finite_zero(evap_flux)
            and self._mapping_is_finite_zero(self._last_condensed_by_stage_species_delta)
            and self._mapping_is_finite_zero(
                self._last_wall_deposit_by_segment_species_delta
            )
            and self._mapping_is_finite_zero(getattr(overhead, 'composition', {}))
            and self._value_is_finite_zero(getattr(overhead, 'O2_vented_kg_hr', None))
            and self._value_is_finite_zero(getattr(overhead, 'O2_vented_mol_hr', None))
            and self._value_is_finite_zero(
                getattr(overhead, 'melt_offgas_O2_mol_hr', None)
            )
            and self._value_is_finite_zero(
                getattr(overhead, 'mre_anode_O2_mol_hr', None)
            )
            and self._value_is_finite_zero(getattr(overhead, 'turbine_flow_kg_hr', None))
            and self._value_is_finite_zero(
                getattr(overhead, 'turbine_flow_mol_hr', None)
            )
        )

    @classmethod
    def _mapping_is_finite_zero(cls, value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        return all(cls._value_is_finite_zero(item) for item in value.values())

    @classmethod
    def _value_is_finite_zero(cls, value: object) -> bool:
        return cls._finite_float_or_none(value) == 0.0

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
        marker = getattr(self, '_pending_knudsen_zero_overhead_flow_marker', None)
        if isinstance(marker, Mapping):
            return dict(marker)
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
        # Prefer the diagnostic's JSON-safe projection: the model may carry
        # ``math.inf`` for zero-pressure/free-molecular ticks, while runner
        # exports require finite values or omission.
        kn = diag.get('knudsen_number', getattr(model, 'knudsen_number', None))
        if kn is not None:
            try:
                finite_kn = float(kn)
            except (TypeError, ValueError):
                finite_kn = None
            if finite_kn is not None and math.isfinite(finite_kn):
                summary['knudsen_number'] = finite_kn
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

    @staticmethod
    def _evap_flux_is_finite_zero(evap_flux: EvaporationFlux) -> bool:
        try:
            total = float(getattr(evap_flux, 'total_kg_hr', None))
        except (TypeError, ValueError):
            return False
        if not math.isfinite(total) or total != 0.0:
            return False
        species = getattr(evap_flux, 'species_kg_hr', {}) or {}
        if not isinstance(species, Mapping):
            return False
        for value in species.values():
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(numeric) or numeric != 0.0:
                return False
        return True

    def _make_snapshot(self) -> HourSnapshot:
        """Build an HourSnapshot from current state."""
        oxygen_partition = self._oxygen_terminal_partition_kg()
        condensation_totals = self._condensation_totals_with_terminal_oxygen()
        self._stamp_redox_source_context_for_current_state()
        redox_source_breakdown = self._redox_source_breakdown_diagnostic()
        oxygen_reservoir_snapshot = dict(vars(self.melt.oxygen_reservoir))
        melt_redox_fallback_summary = (
            self._melt_redox_liquidus_gate_fallback_summary()
        )
        if melt_redox_fallback_summary:
            oxygen_reservoir_snapshot['melt_redox_gate_fallback'] = (
                melt_redox_fallback_summary
            )
        if redox_source_breakdown:
            source_context = dict(
                redox_source_breakdown.get('source_context', {}) or {}
            )
            oxygen_reservoir_snapshot.update({
                'redox_source_terms_mol_o2_equiv': dict(
                    redox_source_breakdown.get('terms_mol_o2_equiv_by_label', {})
                ),
                'redox_source_net_mol_o2_equiv': float(
                    redox_source_breakdown.get('net_mol_o2_equiv', 0.0)
                ),
                'redox_source_delta_ln_fO2': float(
                    redox_source_breakdown.get('delta_ln_fO2', 0.0)
                ),
                'redox_source_delta_log10_fO2': float(
                    redox_source_breakdown.get('delta_log10_fO2', 0.0)
                ),
                'redox_source_applied_terms_mol_o2_equiv': dict(
                    redox_source_breakdown.get(
                        'applied_terms_mol_o2_equiv_by_label',
                        {},
                    )
                ),
                'redox_source_skipped_terms_mol_o2_equiv': dict(
                    redox_source_breakdown.get(
                        'skipped_terms_mol_o2_equiv_by_label',
                        {},
                    )
                ),
                'redox_source_skipped_reasons_by_label': dict(
                    redox_source_breakdown.get('skipped_reasons_by_label', {})
                ),
                'redox_source_terms_applied': bool(
                    redox_source_breakdown.get(
                        'redox_source_terms_applied',
                        False,
                    )
                ),
                'redox_source_skip_reason': str(
                    redox_source_breakdown.get('redox_source_skip_reason', '')
                ),
                'ferric_divergence': dict(
                    redox_source_breakdown.get('ferric_divergence', {})
                ),
                'redox_source_context': source_context,
                'redox_source_campaign': str(source_context.get('campaign', '')),
                'redox_source_hour': int(source_context.get('hour', 0)),
                'redox_source_campaign_hour': int(
                    source_context.get('campaign_hour', 0)
                ),
            })
        # Mass balance check
        mass_in = self.record.batch_mass_kg + sum(
            self.record.additives_kg.values()) + sum(
            self.inventory.stage0_external_inputs_kg.values()) + float(
            getattr(self, '_c7_al_credit_input_kg', 0.0) or 0.0) + float(
            getattr(self, '_o2_bubbler_injected_cumulative_kg', 0.0) or 0.0)
        mass_out = self._flow_mass_out_kg()
        error_pct = 0.0
        error_category = ''
        if mass_in > 0:
            error_pct = abs(mass_in - mass_out) / mass_in * 100.0
        elif mass_out > 0:
            error_pct = None
            error_category = 'zero_input_basis_breach'

        snapshot = HourSnapshot(
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
            energy_electrical_plus_evaporation_cumulative_kWh=(
                self.energy_electrical_plus_evaporation_cumulative_kWh
            ),
            energy_cumulative_breakdown_kWh=dict(
                self.energy_cumulative_breakdown_kWh),
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
            c3_alkali_credit_drawn_kg_by_species=dict(
                getattr(self, '_c3_alkali_credit_drawn_kg_by_species', {}) or {}
            ),
            c3_alkali_credit_outstanding_kg_by_species=(
                self._c3_alkali_credit_outstanding_kg_by_species()
            ),
            feedstock_recovered_reagent_kg_by_species=dict(
                getattr(
                    self,
                    '_feedstock_recovered_reagent_kg_by_species',
                    {},
                ) or {}
            ),
            non_feedstock_reagent_element_kg_by_account={
                str(account): dict(element_kg)
                for account, element_kg in (
                    getattr(
                        self,
                        '_non_feedstock_reagent_element_kg_by_account',
                        {},
                    ) or {}
                ).items()
            },
            shuttle_cycle=(
                self.shuttle_cycle_K
                + (
                    1
                    if getattr(
                        self,
                        '_pending_shuttle_bakeout_cycle_increment',
                        '',
                    ) == CampaignPhase.C3_K.name
                    else 0
                )
                if self.melt.campaign == CampaignPhase.C3_K
                else self.shuttle_cycle_Na
                + (
                    1
                    if getattr(
                        self,
                        '_pending_shuttle_bakeout_cycle_increment',
                        '',
                    ) == CampaignPhase.C3_NA.name
                    else 0
                )
            ),
            # MRE electrolysis
            mre_voltage_V=self._mre_voltage_V,
            mre_current_A=self._mre_current_A,
            mre_declared_rung_V=float(
                getattr(self.melt, 'mre_declared_rung_V', 0.0)
            ),
            mre_metals_kg_hr=dict(self._mre_metals_this_hr),
            mre_uncertified_yield=dict(self._mre_uncertified_yield),
            mre_ellingham_ladder_diagnostic=dict(
                self._mre_ellingham_ladder_diagnostic
            ),
            c2a_staged_gas=dict(
                getattr(
                    self.campaign_mgr,
                    'last_c2a_staged_gas_control',
                    {},
                ) or {}
            ),
            redox_source_breakdown=redox_source_breakdown,
            o2_bubbler_injected_kg=float(
                getattr(self, '_o2_bubbler_injected_kg', 0.0) or 0.0
            ),
            o2_bubbler_absorbed_kg=float(
                getattr(self, '_o2_bubbler_absorbed_kg', 0.0) or 0.0
            ),
            o2_bubbler_passthrough_kg=float(
                getattr(self, '_o2_bubbler_passthrough_kg', 0.0) or 0.0
            ),
            o2_bubbler_vented_kg=float(
                getattr(self, '_o2_bubbler_vented_kg', 0.0) or 0.0
            ),
            # 0.5.4 W8 (M2 historical-audit closure): per-species drift
            # between ``process.metal_phase`` ledger and the
            # ``train.stages[*].collected_kg`` UI projection. Empty dict
            # means all metals in sync. Diagnostic only — the global
            # ``mass_balance_error_pct`` ≤5e-12 % gate remains hard.
            metal_projection_drift_kg=self._audit_metal_projection_drift(),
            oxygen_reservoir=oxygen_reservoir_snapshot,
            # 0.5.4.1 E3: Knudsen-regime warning sticker from the
            # latest condensation pass. Empty dict on ticks that
            # didn't trigger a condensation route.
            knudsen_regime_summary=self._latest_knudsen_summary(),
        )
        if error_category:
            setattr(snapshot, 'mass_balance_error_category', error_category)
        return snapshot
