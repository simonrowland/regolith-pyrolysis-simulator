"""
Regolith Pyrolysis Simulator — Core Data Structures & Simulation Loop
=====================================================================

★ TIER 1: CHEMIST-READABLE ★

This file is the heart of the simulator.  It defines all state
objects and runs the hour-by-hour step() loop that models the
Oxygen Shuttle pyrometallurgical process.

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
    Mass            kg (composition_kg dict), g (trace species)
    Pressure        mbar (overhead), bar (O₂ accumulator)
    Time            hours (simulation timestep = 1 h)
    Energy          kWh (electrical only; solar-thermal assumed)
    Evaporation     kg/hr (Hertz-Knudsen flux × surface area)
    Vapor pressure  Pa (Antoine → converted as needed)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple


# ============================================================================
# SECTION 1: CONSTANTS
# ============================================================================

# --- Oxide species tracked in the melt ---
# These are the major oxides present in silicate melts.
# The simulator tracks their absolute mass (kg) in the melt at each hour.
OXIDE_SPECIES = [
    'SiO2', 'TiO2', 'Al2O3', 'FeO', 'MgO',
    'CaO', 'Na2O', 'K2O', 'Cr2O3', 'MnO', 'P2O5',
]

# --- Metal products extracted from the melt ---
# Each metal is obtained by reducing or evaporating its parent oxide.
METAL_SPECIES = [
    'Na', 'K', 'Fe', 'Mg', 'Si', 'Ti', 'Al', 'Ca', 'Cr', 'Mn',
]

# --- Volatile / gas species ---
# Tracked in the overhead gas and condensation train.
GAS_SPECIES = [
    'O2', 'SiO', 'N2', 'H2O', 'CO2', 'S2',
]

# --- Molar masses (g/mol) ---
# Used for stoichiometric conversions (oxide → metal + O₂).
MOLAR_MASS = {
    # Oxides
    'SiO2':  60.08,  'TiO2':  79.87,  'Al2O3': 101.96,
    'FeO':   71.84,  'MgO':   40.30,  'CaO':   56.08,
    'Na2O':  61.98,  'K2O':   94.20,  'Cr2O3': 151.99,
    'MnO':   70.94,  'P2O5':  141.94,
    # Metals
    'Na': 22.99,  'K':  39.10,  'Fe': 55.85,  'Mg': 24.31,
    'Si': 28.09,  'Ti': 47.87,  'Al': 26.98,  'Ca': 40.08,
    'Cr': 52.00,  'Mn': 54.94,
    # Gases
    'O2': 32.00,  'O':  16.00,  'SiO': 44.08,
    'N2': 28.01,  'H2O': 18.02, 'CO2': 44.01, 'S2': 64.13,
}

# --- Oxide → Metal mapping ---
# For each oxide, how many metal atoms and how many O atoms
# are released per formula unit during reduction.
#   oxide_key: (metal_key, n_metal_atoms, n_oxygen_atoms, metal_mass_per_oxide_mass)
OXIDE_TO_METAL = {
    'Na2O':  ('Na', 2, 1),   # Na₂O  → 2 Na + ½ O₂
    'K2O':   ('K',  2, 1),   # K₂O   → 2 K  + ½ O₂
    'FeO':   ('Fe', 1, 1),   # FeO   → Fe   + ½ O₂
    'MgO':   ('Mg', 1, 1),   # MgO   → Mg   + ½ O₂
    'SiO2':  ('Si', 1, 2),   # SiO₂  → Si   + O₂
    'TiO2':  ('Ti', 1, 2),   # TiO₂  → Ti   + O₂
    'Al2O3': ('Al', 2, 3),   # Al₂O₃ → 2 Al + 1½ O₂
    'CaO':   ('Ca', 1, 1),   # CaO   → Ca   + ½ O₂
    'Cr2O3': ('Cr', 2, 3),   # Cr₂O₃ → 2 Cr + 1½ O₂
    'MnO':   ('Mn', 1, 1),   # MnO   → Mn   + ½ O₂
}

# Compute stoichiometric mass ratios once:
# For oxide → (kg_metal_per_kg_oxide, kg_O2_per_kg_oxide)
STOICH_RATIOS: Dict[str, Tuple[float, float]] = {}
for _oxide, (_metal, _n_met, _n_oxy) in OXIDE_TO_METAL.items():
    _M_oxide = MOLAR_MASS[_oxide]
    _M_metal = MOLAR_MASS[_metal]
    _kg_metal = (_n_met * _M_metal) / _M_oxide
    _kg_O2 = (_n_oxy * MOLAR_MASS['O'] ) / _M_oxide  # mass of O atoms
    STOICH_RATIOS[_oxide] = (_kg_metal, _kg_O2)

# Physical constants
BOLTZMANN = 1.380649e-23      # J/K
FARADAY = 96485.3321          # C/mol (for electrolysis)
GAS_CONSTANT = 8.31446        # J/(mol·K)
STEFAN_BOLTZMANN = 5.670374e-8  # W/(m²·K⁴)


# ============================================================================
# SECTION 2: ENUMERATIONS
# ============================================================================

class CampaignPhase(Enum):
    """Which campaign the furnace is currently running."""
    IDLE = auto()             # Not processing — waiting for batch
    C0 = auto()               # Vacuum bakeoff
    C0B = auto()              # P-cleanup (mild oxidative hold)
    C2A = auto()              # Continuous adaptive pN₂ ramp (Path A)
    C2B = auto()              # pO₂-managed Fe pyrolysis (Path B)
    C3_K = auto()             # Alkali shuttle — K phase
    C3_NA = auto()            # Alkali shuttle — Na phase
    C4 = auto()               # Mg selective pyrolysis
    C5 = auto()               # Limited MRE (electrolysis)
    C6 = auto()               # Mg thermite reduction
    MRE_BASELINE = auto()     # Standard MRE baseline (root branch alt)
    COMPLETE = auto()         # Batch finished

class DecisionType(Enum):
    """Decision points where the operator must choose."""
    ROOT_BRANCH = auto()      # Pyrolysis Track vs Standard MRE
    PATH_AB = auto()          # Path A (SiO₂ extraction) vs Path B (CMAS preservation)
    BRANCH_ONE_TWO = auto()   # Branch One (full MRE) vs Branch Two (Mg pyro + thermite)
    TI_RETENTION = auto()     # Retain TiO₂ for C6 Al-Ti alloy vs extract in C5
    CA_HARVEST = auto()       # Optional Ca harvest at end of C4
    C6_PROCEED = auto()       # Proceed with Mg thermite? (needs Mg inventory)

class Atmosphere(Enum):
    """Atmosphere above the melt."""
    HARD_VACUUM = auto()      # pO₂ ~1e-9 bar (C0)
    CONTROLLED_O2 = auto()    # pO₂ managed by turbine + bleed (C2B, C3, C4)
    PN2_SWEEP = auto()        # Recirculating N₂ at 5-15 mbar (C2A)
    O2_BACKPRESSURE = auto()  # O₂ at 0.01-0.1 bar (C5 MRE)
    CONTROLLED_O2_FLOW = auto()  # O₂ flow/sweep (C0b P-cleanup)


# ============================================================================
# SECTION 3: DATA STRUCTURES
# ============================================================================

@dataclass
class MeltState:
    """
    The state of the molten regolith at a single moment.

    The melt is a silicate liquid held in a crucible under
    solar-concentrated heating.  Its composition evolves as
    metals evaporate off and are removed by condensation,
    or are reduced electrochemically (MRE).
    """
    # --- Composition ---
    composition_kg: Dict[str, float] = field(default_factory=dict)
    # Absolute mass of each oxide species in the melt (kg).
    # Example: {'SiO2': 445.0, 'FeO': 165.0, 'MgO': 90.0, ...}

    # --- Conditions ---
    temperature_C: float = 25.0
    atmosphere: Atmosphere = Atmosphere.HARD_VACUUM
    pO2_mbar: float = 0.0          # Controlled oxygen partial pressure
    p_total_mbar: float = 0.0      # Total pressure above melt
    fO2_log: float = -9.0          # log₁₀(fO₂/bar) for MELTS calc

    # --- Process state ---
    campaign: CampaignPhase = CampaignPhase.IDLE
    hour: int = 0                   # Hours since batch start
    campaign_hour: int = 0          # Hours since current campaign started

    # --- Stirring ---
    stir_factor: float = 6.0
    # Induction stirring acceleration factor (4-8×).
    # Multiplies the Hertz-Knudsen evaporation rate to account
    # for continuous surface renewal and thermal cycling.

    # --- MRE state (for endpoint detection) ---
    mre_voltage_V: float = 0.0
    mre_current_A: float = 0.0            # Effective (Faradaic) current
    mre_low_current_hours: int = 0        # Consecutive hours below threshold

    # --- Derived quantities (set by step()) ---
    total_mass_kg: float = 0.0
    melt_surface_area_m2: float = 0.2  # Crucible opening

    def composition_wt_pct(self) -> Dict[str, float]:
        """Current composition as weight percent."""
        total = sum(self.composition_kg.values())
        if total <= 0:
            return {sp: 0.0 for sp in OXIDE_SPECIES}
        return {sp: (self.composition_kg.get(sp, 0.0) / total) * 100.0
                for sp in OXIDE_SPECIES}

    def update_total_mass(self):
        """Recalculate total melt mass from composition."""
        self.total_mass_kg = sum(self.composition_kg.values())


@dataclass
class CondensationStage:
    """
    One stage of the condensation train.

    Each stage operates at a fixed temperature range and
    preferentially collects species whose condensation
    temperature falls within that range.
    """
    stage_number: int
    label: str
    temp_range_C: Tuple[float, float]   # (low, high)
    target_species: List[str]           # Primary species collected here
    collected_kg: Dict[str, float] = field(default_factory=dict)
    # Running total of each species condensed in this stage (kg)

    def total_collected_kg(self) -> float:
        return sum(self.collected_kg.values())

    def purity_pct(self, species: str) -> float:
        """Purity of a specific species in this stage's product."""
        total = self.total_collected_kg()
        if total <= 0:
            return 0.0
        return (self.collected_kg.get(species, 0.0) / total) * 100.0


@dataclass
class CondensationTrain:
    """
    The complete metals condensation train (7 stages, indexed 0-6).

    Stage 0: Hot duct (>1400°C) — IR spectroscopy, no condensation
    Stage 1: Fe condenser (1100-1400°C)
    Stage 2: SiO zone (900-1200°C) — removable fused silica baffles
    Stage 3: Alkali/Mg cyclone (350-700°C)
    Stage 4: Vortex dust filter (200-350°C)
    Stage 5: Turbine/compressor — pressure regulation
    Stage 6: O₂ accumulator (~3 bar)

    The volatiles train (C0/C0b) is handled separately and sealed
    after devolatilisation.
    """
    stages: List[CondensationStage] = field(default_factory=list)

    # Volatiles train (sealed after C0/C0b)
    volatiles_collected_kg: Dict[str, float] = field(default_factory=dict)
    volatiles_gate_sealed: bool = False

    def total_by_species(self) -> Dict[str, float]:
        """Sum collected mass across all stages, per species."""
        totals: Dict[str, float] = {}
        for stage in self.stages:
            for sp, kg in stage.collected_kg.items():
                totals[sp] = totals.get(sp, 0.0) + kg
        return totals

    @staticmethod
    def create_default() -> 'CondensationTrain':
        """Build the standard 7-stage metals train."""
        stages = [
            CondensationStage(0, 'Hot Duct (IR)',
                              (1400, 1600), []),
            CondensationStage(1, 'Fe Condenser',
                              (1100, 1400), ['Fe']),
            CondensationStage(2, 'SiO Zone',
                              (900, 1200), ['SiO2']),
            CondensationStage(3, 'Alkali/Mg Cyclone',
                              (350, 700), ['Na', 'K', 'Mg']),
            CondensationStage(4, 'Vortex Dust Filter',
                              (200, 350), []),
            CondensationStage(5, 'Turbine-Compressor',
                              (50, 200), []),
            CondensationStage(6, 'O₂ Accumulator',
                              (20, 50), ['O2']),
        ]
        return CondensationTrain(stages=stages)


@dataclass
class EvaporationFlux:
    """
    Evaporation rates from the melt surface for one timestep.

    Calculated using the Hertz-Knudsen-Langmuir equation:
        J = α × (P_sat - P_ambient) / √(2π M R T)
    where α is the evaporation coefficient (sticking probability),
    multiplied by the induction stirring factor (4-8×).
    """
    species_kg_hr: Dict[str, float] = field(default_factory=dict)
    # Mass evaporation rate per species (kg/hr)
    # Positive = leaving the melt

    total_kg_hr: float = 0.0
    dominant_species: str = ''

    def update_totals(self):
        self.total_kg_hr = sum(self.species_kg_hr.values())
        if self.species_kg_hr:
            self.dominant_species = max(self.species_kg_hr,
                                        key=self.species_kg_hr.get)


@dataclass
class OverheadGas:
    """
    State of the gas above the melt and in the piping.

    Includes turbine capacity feedback: when O₂ production exceeds
    the turbine's max flow, the excess is vented to lunar vacuum.
    Transport saturation (evap rate vs pipe conductance) gates the
    temperature ramp rate to prevent runaway evaporation.
    """
    pressure_mbar: float = 0.0
    composition: Dict[str, float] = field(default_factory=dict)
    # Partial pressures in mbar

    turbine_flow_kg_hr: float = 0.0
    # Mass flow rate through turbine (sets pO₂)

    pipe_conductance_kg_hr: float = 50.0
    # Maximum transport capacity of collection pipe (kg/hr)
    # Depends on pipe diameter, pressure, temperature

    # --- Gas train feedback fields ---
    turbine_limited: bool = False
    # True when O₂ production exceeds turbine max capacity

    O2_vented_kg_hr: float = 0.0
    # O₂ vented to lunar vacuum this hour (excess beyond turbine max)

    turbine_utilization_pct: float = 0.0
    # Turbine load as % of max O₂ throughput (0-100+)

    turbine_shaft_power_kW: float = 0.0
    # Actual shaft power consumed by the turbine this hour

    evap_exceeds_transport: bool = False
    # True when total evaporation rate exceeds pipe conductance

    transport_saturation_pct: float = 0.0
    # Evaporation rate as % of pipe conductance (0-100+)
    # >100% triggers ΔT/dt throttling


@dataclass
class EnergyRecord:
    """Electrical energy consumption for one timestep."""
    turbine_kWh: float = 0.0       # O₂ compression
    condenser_kWh: float = 0.0     # Active cooling (if needed)
    mre_kWh: float = 0.0          # Electrolysis
    total_kWh: float = 0.0

    def sum_total(self):
        self.total_kWh = self.turbine_kWh + self.condenser_kWh + self.mre_kWh


@dataclass
class HourSnapshot:
    """
    Complete system state at a single hour.

    One of these is recorded every simulation step.
    The full history of snapshots constitutes the batch record.
    """
    hour: int = 0
    campaign: CampaignPhase = CampaignPhase.IDLE

    # Melt
    temperature_C: float = 25.0
    melt_mass_kg: float = 0.0
    composition_wt_pct: Dict[str, float] = field(default_factory=dict)

    # Evaporation
    evap_flux: EvaporationFlux = field(default_factory=EvaporationFlux)

    # Overhead
    overhead: OverheadGas = field(default_factory=OverheadGas)

    # Condensation (cumulative totals at this hour)
    condensation_totals: Dict[str, float] = field(default_factory=dict)

    # Energy
    energy: EnergyRecord = field(default_factory=EnergyRecord)
    energy_cumulative_kWh: float = 0.0

    # O₂ produced (cumulative, kg)
    oxygen_produced_kg: float = 0.0

    # Mass balance check
    mass_in_kg: float = 0.0      # Total input (regolith + additives)
    mass_out_kg: float = 0.0     # Total output (products + melt remaining)
    mass_balance_error_pct: float = 0.0

    # MRE state (for C5 / MRE baseline)
    mre_voltage_V: float = 0.0
    mre_current_A: float = 0.0
    mre_metals_kg_hr: Dict[str, float] = field(default_factory=dict)

    # --- Gas train feedback ---
    ramp_throttled: bool = False
    # True when ΔT/dt has been reduced due to transport saturation

    nominal_ramp_rate_C_hr: float = 0.0
    # Campaign-defined ramp rate before any throttling (°C/hr)

    actual_ramp_rate_C_hr: float = 0.0
    # Ramp rate actually applied after throttling (°C/hr)

    throttle_reason: str = ''
    # Human-readable reason for throttling (e.g., 'pipe saturated',
    # 'turbine overloaded', 'volatiles train at capacity')

    O2_vented_kg_hr: float = 0.0
    # O₂ vented to lunar vacuum this hour

    O2_vented_cumulative_kg: float = 0.0
    # Total O₂ vented since batch start

    O2_stored_kg: float = 0.0
    # Cumulative O₂ in accumulator (compressed to ~3 bar)

    turbine_shaft_power_kW: float = 0.0
    # Turbine compression power this hour

    # --- Alkali shuttle (C3) ---
    shuttle_phase: str = ''
    # 'inject' or 'bakeout' during C3; empty otherwise

    shuttle_injected_kg_hr: float = 0.0
    # Mass of K or Na injected into the melt this hour (kg)

    shuttle_reduced_kg_hr: float = 0.0
    # Mass of oxide (FeO/TiO₂) reduced by shuttle this hour (kg)

    shuttle_metal_produced_kg_hr: float = 0.0
    # Mass of metal (Fe/Ti) produced by shuttle reduction this hour (kg)

    shuttle_K_inventory_kg: float = 0.0
    # K available for shuttle injection (from condenser + additives)

    shuttle_Na_inventory_kg: float = 0.0
    # Na available for shuttle injection (from condenser + additives)

    shuttle_cycle: int = 0
    # Current inject-bakeout cycle number within the C3 phase


@dataclass
class DecisionPoint:
    """A decision the operator must make."""
    decision_type: DecisionType
    line_id: str = ''       # For game mode: which furnace line
    options: List[str] = field(default_factory=list)
    recommendation: str = ''
    context: str = ''       # Explanation for the operator


@dataclass
class BatchRecord:
    """
    Full record of a single batch from load to completion.

    Contains the entire history of hourly snapshots,
    all decisions made, final product masses, and total energy.
    """
    batch_id: str = ''
    feedstock_key: str = ''
    feedstock_label: str = ''
    batch_mass_kg: float = 0.0
    additives_kg: Dict[str, float] = field(default_factory=dict)

    # History
    snapshots: List[HourSnapshot] = field(default_factory=list)
    decisions: List[Tuple[DecisionType, str]] = field(default_factory=list)
    # List of (decision_type, chosen_option)

    # Configuration
    track: str = 'pyrolysis'    # 'pyrolysis' or 'mre_baseline'
    path: str = ''              # 'A' or 'B' (set at PATH_AB decision)
    branch: str = ''            # 'one' or 'two' (set at BRANCH decision)

    # Final products (kg)
    products_kg: Dict[str, float] = field(default_factory=dict)
    oxygen_total_kg: float = 0.0
    terminal_slag_kg: float = 0.0

    # Energy
    energy_total_kWh: float = 0.0
    energy_by_campaign: Dict[str, float] = field(default_factory=dict)

    # Status
    completed: bool = False
    total_hours: int = 0


# ============================================================================
# SECTION 4: SIMULATION ENGINE
# ============================================================================

class PyrolysisSimulator:
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
        self.setpoints = setpoints
        self.feedstocks = feedstocks
        self.vapor_pressures = vapor_pressures

        # --- Current state ---
        self.melt = MeltState()
        self.train = CondensationTrain.create_default()
        self.overhead = OverheadGas()

        # --- Batch record ---
        self.record = BatchRecord()
        self.energy_cumulative_kWh = 0.0
        self.oxygen_cumulative_kg = 0.0

        # --- Gas train feedback state ---
        self.O2_vented_cumulative_kg = 0.0      # Total O₂ vented to vacuum
        self.O2_stored_cumulative_kg = 0.0      # Total O₂ in accumulator
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

        comp = fs.get('composition_wt_pct', {})

        # Convert wt% to absolute kg
        self.melt.composition_kg = {}
        for oxide in OXIDE_SPECIES:
            wt_pct = comp.get(oxide, 0.0)
            self.melt.composition_kg[oxide] = mass_kg * wt_pct / 100.0

        self.melt.temperature_C = 25.0
        self.melt.atmosphere = Atmosphere.HARD_VACUUM
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
            additives_kg=additives_kg or {},
        )
        self.energy_cumulative_kWh = 0.0
        self.oxygen_cumulative_kg = 0.0
        self.O2_vented_cumulative_kg = 0.0
        self.O2_stored_cumulative_kg = 0.0
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
        self._campaign_start_O2 = self.oxygen_cumulative_kg

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
                self.oxygen_cumulative_kg - self._campaign_start_O2, 2),
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
            mre_energy_kWh = self._mre_energy_this_hr
        else:
            self._mre_voltage_V = 0.0
            self._mre_current_A = 0.0
            self._mre_effective_current_A = 0.0
            self._mre_metals_this_hr = {}
            self._mre_energy_this_hr = 0.0

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
        self.overhead = self.overhead_model.update(
            evap_flux, self.melt, self.train, turbine_spec=turbine_spec)

        # Track cumulative O₂ vented and stored
        self.O2_vented_cumulative_kg += self.overhead.O2_vented_kg_hr
        # O₂ that went through the turbine is stored in the accumulator
        O2_compressed_hr = (evap_flux.total_kg_hr * 0.3
                            - self.overhead.O2_vented_kg_hr)
        self.O2_stored_cumulative_kg += max(0.0, O2_compressed_hr)

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
        snapshot.oxygen_produced_kg = self.oxygen_cumulative_kg
        self.record.snapshots.append(snapshot)

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

    def _get_equilibrium(self):
        """
        Query the melt backend for thermodynamic equilibrium.

        Returns an EquilibriumResult with phase assemblage,
        activity coefficients, and vapor pressures.  If the
        backend is unavailable, returns a stub result using
        Antoine-equation vapor pressures.
        """
        if self.backend is not None and self.backend.is_available():
            return self.backend.equilibrate(
                temperature_C=self.melt.temperature_C,
                composition_kg=self.melt.composition_kg,
                fO2_log=self.melt.fO2_log,
                pressure_bar=self.melt.p_total_mbar / 1000.0,
            )
        else:
            # Stub: use Antoine equations from vapor_pressures.yaml
            # with activity = 1.0 (ideal approximation)
            return self._stub_equilibrium()

    # --- Ellingham thermodynamic data for oxide equilibrium ---        [ELLI]
    #
    # Standard-state formation enthalpy (ΔH_f) and entropy (ΔS_f)
    # per mol O₂ for each oxide.  Used to compute the temperature-
    # dependent Gibbs free energy of formation:
    #
    #   ΔG_f(T) = ΔH_f - T × ΔS_f   (kJ/mol O₂)               [ELLI-1]
    #
    # The decomposition equilibrium constant is:
    #
    #   K = exp(ΔG_f / (R × T))   [K < 1 since ΔG_f < 0]       [ELLI-2]
    #
    # For the decomposition reaction per mol O₂:
    #   n_ox × oxide(melt) → n_M × Metal(liquid) + O₂(gas)
    #
    # The equilibrium liquid metal activity in the melt is:
    #
    #   a_M(l) = (K × a_oxide^n_ox / pO₂_bar)^(1/n_M)          [ELLI-3]
    #
    # The effective metal vapor pressure above the melt is:
    #
    #   P_metal(g) = a_M(l) × P_sat_pure(T)                     [ELLI-4]
    #
    # where P_sat_pure comes from Antoine equation (vapor_pressures.yaml).
    #
    # This naturally captures the full Ellingham hierarchy:
    #   Na, K (volatile, weak oxides):   high P_metal → easy pyrolysis
    #   Fe, Mn, Cr (moderate oxides):    P_metal depends on T and pO₂
    #   Mg (refractory):                 significant only at high T, low pO₂
    #   Ca, Al, Ti (very refractory):    negligible P_metal → need MRE/thermite
    #
    # Data: NIST-JANAF Thermochemical Tables, Kubaschewski et al.
    # Cross-verified against setpoints.yaml Ellingham values at 1600°C.
    #
    # Tuple: (ΔH_f kJ/mol_O₂, ΔS_f kJ/(mol·K), n_M, n_ox)
    #   n_M  = moles of metal per mol O₂ in the decomposition reaction
    #   n_ox = moles of oxide per mol O₂ in the decomposition reaction

    _ELLINGHAM_THERMO = {
        'Na': (-836.0, -0.275, 4, 2),      # 4Na + O₂ → 2Na₂O,  ΔG(1600°C) ≈ -321
        'K':  (-740.0, -0.225, 4, 2),      # 4K  + O₂ → 2K₂O,   ΔG(1600°C) ≈ -319
        'Fe': (-536.0, -0.088, 2, 2),      # 2Fe + O₂ → 2FeO,   ΔG(1600°C) ≈ -371
        'Mn': (-770.0, -0.165, 2, 2),      # 2Mn + O₂ → 2MnO,   ΔG(1600°C) ≈ -461
        'Cr': (-756.0, -0.137, 4/3, 2/3),  # 4/3Cr + O₂ → 2/3Cr₂O₃, ΔG ≈ -499
        'Mg': (-1200.0, -0.198, 2, 2),     # 2Mg + O₂ → 2MgO,   ΔG(1600°C) ≈ -829
        'Ca': (-1270.0, -0.198, 2, 2),     # 2Ca + O₂ → 2CaO,   ΔG(1600°C) ≈ -899
        'Al': (-1120.0, -0.214, 4/3, 2/3), # 4/3Al + O₂ → 2/3Al₂O₃, ΔG ≈ -719
        'Ti': (-945.0, -0.195, 1, 1),      # Ti + O₂ → TiO₂,    ΔG(1600°C) ≈ -580
    }

    def _stub_equilibrium(self):
        """
        Fallback equilibrium using Ellingham thermodynamics + Antoine
        vapor pressures.

        When no melt backend (AlphaMELTS/VapoRock) is available, we
        compute metal vapor pressures above the oxide melt by combining
        the oxide decomposition equilibrium (Ellingham) with the pure-metal
        vaporization curve (Antoine).

        The approach for each metal species:

        1. Compute oxide stability at current T:                  [ELLI-1]
               ΔG_f(T) = ΔH_f - T × ΔS_f   (kJ/mol O₂)

        2. Get the decomposition equilibrium constant:            [ELLI-2]
               K = exp(ΔG_f / (R × T))   [< 1 since ΔG_f < 0]

        3. Solve for equilibrium liquid metal activity:           [ELLI-3]
               a_M(l) = (K × a_oxide^n_ox / pO₂_bar)^(1/n_M)

        4. Get pure-metal vapor pressure from Antoine:
               P_sat = 10^(A − B/(T+C))   (Pa)

        5. Effective vapor pressure above the oxide melt:         [ELLI-4]
               P_metal = a_M(l) × P_sat

        This correctly captures:
        - Temperature dependence of BOTH oxide stability AND metal
          volatility (the two factors that control pyrolysis yield).
        - pO₂ dependence: higher pO₂ pushes equilibrium toward oxide,
          suppressing metal vapor.  This is the physics behind pO₂-
          managed campaigns (C2B, C3, C4).
        - Composition dependence: as an oxide is depleted, its activity
          drops and evaporation rate decreases.
        - The full Ellingham hierarchy emerges naturally:
            Na, K   → ΔG_f ≈ −320 kJ → high P_metal (easy pyrolysis)
            Fe      → ΔG_f ≈ −370 kJ → moderate P_metal (C2A/C2B target)
            Mn, Cr  → ΔG_f ≈ −460..−500 kJ → minor byproducts
            Mg      → ΔG_f ≈ −830 kJ → significant only at very high T
            Ca, Al  → ΔG_f ≈ −720..−900 kJ → negligible (need MRE/thermite)

        SiO vapor uses a separate equilibrium pathway because it
        evaporates as an oxide gas (SiO₂ → SiO + ½O₂), not as a
        metal.  The Antoine equation + √pO₂ correction is used.  [THERMO-8]
        """
        from simulator.melt_backend.base import EquilibriumResult

        T_K = self.melt.temperature_C + 273.15
        if T_K < 400:
            return EquilibriumResult(
                temperature_C=self.melt.temperature_C,
                pressure_bar=self.melt.p_total_mbar / 1000.0,
            )

        vapor_pressures = {}
        activities = {}

        # --- Determine the oxygen partial pressure (bar) ---
        #
        # The pO₂ at the melt surface enters the decomposition
        # equilibrium.  We use the highest of:
        #   - Actual overhead O₂ (from gas transport model)
        #   - Campaign setpoint pO₂ (turbine-managed)
        #   - Hard vacuum floor (10⁻⁹ bar ≈ lunar surface)
        pO2_bar = max(
            self.overhead.composition.get('O2', 0.0) / 1000.0,
            self.melt.pO2_mbar / 1000.0,
            1e-9,
        )

        # --- Melt composition for oxide activities ---
        comp_wt = self.melt.composition_wt_pct()

        # ================================================================
        # METAL SPECIES: Ellingham equilibrium + Antoine               [ELLI]
        # ================================================================
        #
        # For each metal, combine the oxide decomposition equilibrium
        # (how much liquid metal is "freed") with the pure-metal
        # vaporization (how much of that liquid metal enters the gas).

        metals_data = self.vapor_pressures.get('metals', {})

        for species, (dH_f, dS_f, n_M, n_ox) in self._ELLINGHAM_THERMO.items():
            sp_data = metals_data.get(species, {})
            if not sp_data:
                continue

            parent_oxide = sp_data.get('parent_oxide', '')
            if not parent_oxide:
                continue

            # --- Pure-metal P_sat from Antoine ---
            #
            # We extrapolate the Clausius-Clapeyron equation beyond its
            # validated range because:
            #   1. The form log10(P) = A - B/T is physically meaningful
            #      (Clausius-Clapeyron) even below the metal melting point
            #   2. The Ellingham K_decomp already provides the dominant
            #      physical constraint (K → 0 at low T), so extrapolation
            #      of P_sat introduces only a minor secondary error
            #   3. At low T, the product a_M × P_sat is negligible anyway
            #      because K_decomp is extremely small
            #
            # For Fe (mp 1538°C = 1811K), this allows computing meaningful
            # vapor pressures at 1400-1538°C where FeO decomposition in
            # the silicate melt IS physically real, even though pure solid
            # Fe has a slightly lower sublimation pressure.
            antoine = sp_data.get('antoine', {})
            A = antoine.get('A', 0)
            B = antoine.get('B', 0)
            C = antoine.get('C', 0)

            if A > 0 and T_K > 300:
                # Antoine: log10(P_Pa) = A - B / (T_K + C)
                log_P = A - B / (T_K + C)
                P_sat_pure_Pa = 10.0 ** log_P
            else:
                continue

            # --- Oxide activity (wt fraction proxy) ---           [ELLI-5]
            #
            # Without AlphaMELTS, we approximate the oxide activity
            # as the weight fraction.  This is crude but captures the
            # key behaviour: as an oxide depletes, its activity drops
            # and evaporation slows.  Real activities differ significantly
            # (e.g., γ(Na₂O) ≈ 10⁻² in CMAS melts [THERMO-10]), which
            # is why AlphaMELTS is preferred for quantitative work.
            a_oxide = comp_wt.get(parent_oxide, 0.0) / 100.0
            if a_oxide <= 1e-10:
                continue

            activities[species] = a_oxide

            # --- Ellingham decomposition equilibrium ---          [ELLI-1..3]
            #
            # ΔG_f(T) = ΔH_f - T × ΔS_f   (kJ/mol O₂)
            dG_f_kJ = dH_f - T_K * dS_f   # negative (formation favorable)

            # K_decomp = exp(ΔG_f / (R × T))
            # ΔG_f in kJ, R in J/(mol·K) → multiply by 1000
            K_decomp = math.exp(dG_f_kJ * 1000.0 / (GAS_CONSTANT * T_K))

            # a_M(l) = (K × a_oxide^n_ox / pO₂_bar)^(1/n_M)
            numerator = K_decomp * (a_oxide ** n_ox) / pO2_bar

            if numerator <= 0:
                continue

            a_M_liquid = numerator ** (1.0 / n_M)

            # Clamp to physical range (activity can't exceed 1.0 for
            # a pure substance, and metal pool formation changes regime)
            a_M_liquid = min(a_M_liquid, 1.0)

            # --- Effective vapor pressure ---                     [ELLI-4]
            #
            # P_metal = a_M(l) × P_sat_pure(T)
            P_effective_Pa = a_M_liquid * P_sat_pure_Pa

            if P_effective_Pa > 1e-15:
                vapor_pressures[species] = P_effective_Pa

        # ================================================================
        # OXIDE VAPOR SPECIES (SiO, FeO_vapor)                   [THERMO-8]
        # ================================================================
        #
        # These evaporate as oxide gases, not as metals.
        # SiO₂(melt) → SiO(g) + ½O₂(g), with p(SiO) ∝ 1/√pO₂.
        # The Antoine equation gives the reference vapor pressure,
        # then the √pO₂ suppression and oxide activity are applied.

        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for name, data in oxide_vapors_data.items():
            antoine = data.get('antoine', {})
            A = antoine.get('A', 0)
            B = antoine.get('B', 0)
            C = antoine.get('C', 0)
            valid = data.get('valid_range_K', [0, 9999])

            if A > 0 and valid[0] <= T_K <= valid[1]:
                log_P = A - B / (T_K + C)
                P_sat = 10.0 ** log_P
            else:
                continue

            # Oxide activity proxy (weight fraction)
            parent_oxide = data.get('parent_oxide', '')
            if parent_oxide:
                a_ox = comp_wt.get(parent_oxide, 0.0) / 100.0
                activities[name] = a_ox
                P_sat *= max(a_ox, 0.0)

            # SiO suppression by pO₂: p(SiO) ∝ 1/√pO₂         [THERMO-8]
            #
            # The Antoine equation gives P_SiO at hard vacuum
            # (pO₂ ≈ 10⁻⁹ bar).  At higher pO₂, the equilibrium
            # shifts toward SiO₂, suppressing SiO vapor:
            #   At 10⁻⁹ bar:  suppression = 1.0  (reference)
            #   At 10⁻⁶ bar:  suppression ≈ 0.032 (31× suppression)
            #   At 10⁻³ bar:  suppression ≈ 0.001 (1000× suppression)
            if name == 'SiO' and pO2_bar > 1e-9:
                suppression = math.sqrt(1e-9 / pO2_bar)
                P_sat *= suppression

            if P_sat > 1e-15:
                vapor_pressures[name] = P_sat

        return EquilibriumResult(
            temperature_C=self.melt.temperature_C,
            pressure_bar=self.melt.p_total_mbar / 1000.0,
            vapor_pressures_Pa=vapor_pressures,
            activity_coefficients=activities,
            fO2_log=math.log10(max(pO2_bar, 1e-20)),
        )

    def _calculate_evaporation(self, equilibrium) -> EvaporationFlux:
        """
        Calculate evaporation flux using the Hertz-Knudsen-Langmuir equation.

        For each volatile species, the mass flux from the melt surface is:

            J_i = α_i × stir_factor × A_surface × (P_sat_i - P_ambient_i)
                  / √(2π × M_i × R × T)                            [HK-1]

        where:
            α_i         = evaporation coefficient (~0.1-1.0 for metals)
            stir_factor = 4-8× acceleration from induction stirring
            A_surface   = melt surface area (m²)
            P_sat_i     = saturation vapor pressure from equilibrium (Pa)
            P_ambient_i = partial pressure above the melt (Pa)
            M_i         = molar mass (kg/mol)
            R           = gas constant (J/mol·K)
            T           = temperature (K)

        The SiO suppression under pO₂ control is handled automatically:
        when pO₂ is elevated, the equilibrium vapor pressure of SiO
        drops by the factor √(pO₂), reducing the driving force.

        Returns:
            EvaporationFlux with species_kg_hr dict
        """
        T_K = self.melt.temperature_C + 273.15
        flux = EvaporationFlux()

        if T_K < 400:  # Below any significant evaporation
            return flux

        vapor_pressures = equilibrium.vapor_pressures_Pa
        alpha = 0.5  # Evaporation coefficient (conservative mean)

        metals_data = self.vapor_pressures.get('metals', {})
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for species, P_sat_Pa in vapor_pressures.items():
            if P_sat_Pa <= 0:
                continue

            # Get species data from metals or oxide_vapors section
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            M_kg_mol = sp_data.get('molar_mass_g_mol',
                                    MOLAR_MASS.get(species, 50.0)) / 1000.0

            # Ambient partial pressure (Pa)                    [LOOP-1]
            # Uses the PREVIOUS hour's overhead partial pressures as
            # backpressure.  High evap → high overhead P → reduced driving
            # force → lower evap next hour (negative feedback, 1-hour lag).
            # Under hard vacuum (C0): overhead starts at ~0 but builds up
            # if evaporation outpaces transport.
            P_ambient_Pa = self.overhead.composition.get(species, 0.0) * 100.0  # mbar → Pa

            # For controlled-atmosphere species, also floor at the setpoint
            if species == 'O2' and self.melt.pO2_mbar > 0.001:
                P_ambient_Pa = max(P_ambient_Pa, self.melt.pO2_mbar * 100.0)

            # Hertz-Knudsen mass flux (kg/s per m²)        [HK-1]
            denominator = math.sqrt(2 * math.pi * M_kg_mol * GAS_CONSTANT * T_K)
            J_kg_s_m2 = alpha * (P_sat_Pa - P_ambient_Pa) / denominator

            if J_kg_s_m2 <= 0:
                continue

            # Total rate = flux × surface area × stirring × time
            rate_kg_hr = (J_kg_s_m2
                          * self.melt.melt_surface_area_m2
                          * self.melt.stir_factor
                          * 3600.0)  # s → hr

            # Don't evaporate more than is available in the melt
            parent_oxide = sp_data.get('parent_oxide', '')
            if parent_oxide:
                available_kg = self.melt.composition_kg.get(parent_oxide, 0.0)

                # For oxide vapors (e.g., SiO from SiO₂), use the
                # stoich_oxide_per_vapor ratio from the YAML
                oxide_per_vapor = sp_data.get('stoich_oxide_per_vapor')
                if oxide_per_vapor:
                    # kg_vapor_max = kg_oxide_available / oxide_per_vapor
                    max_vapor_kg = available_kg / oxide_per_vapor
                    rate_kg_hr = min(rate_kg_hr, max_vapor_kg)
                else:
                    # Metal species: use STOICH_RATIOS
                    stoich = STOICH_RATIOS.get(parent_oxide)
                    if stoich:
                        max_metal_kg = available_kg * stoich[0]
                        rate_kg_hr = min(rate_kg_hr, max_metal_kg)

            if rate_kg_hr > 1e-12:
                flux.species_kg_hr[species] = rate_kg_hr

        flux.update_totals()
        return flux

    def _route_to_condensation(self, evap_flux: EvaporationFlux):
        """
        Route evaporated species through the condensation train.

        Each species flows from Stage 0 (hot duct) downward through
        successive stages.  At each stage, a fraction condenses based
        on the condensation efficiency model:

            η = 1 - exp(-residence_time / τ_condensation)

        Species condense preferentially in stages where the stage
        temperature is well below the species' condensation temperature.

        The oxygen component of each evaporated metal oxide is
        released as O₂ and flows to the accumulator (Stage 6).
        """
        self.condensation_model.route(evap_flux, self.melt)

        # Track oxygen produced from evaporation
        # When a metal oxide evaporates and the metal condenses,
        # the oxygen is freed as O₂.
        metals_data = self.vapor_pressures.get('metals', {})
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            parent_oxide = sp_data.get('parent_oxide', '')

            # For oxide vapors (e.g., SiO), use the YAML stoich
            O2_per_vapor = sp_data.get('stoich_O2_per_vapor')
            if O2_per_vapor:
                O2_kg = rate_kg_hr * O2_per_vapor
                self.oxygen_cumulative_kg += O2_kg
                stage6 = self.train.stages[6]
                stage6.collected_kg['O2'] = (
                    stage6.collected_kg.get('O2', 0.0) + O2_kg)
            elif parent_oxide:
                # Metal species: compute from STOICH_RATIOS
                stoich = STOICH_RATIOS.get(parent_oxide)
                if stoich:
                    kg_metal_per_kg_oxide, kg_O2_per_kg_oxide = stoich
                    if kg_metal_per_kg_oxide > 0:
                        O2_kg = rate_kg_hr * (kg_O2_per_kg_oxide
                                               / kg_metal_per_kg_oxide)
                        self.oxygen_cumulative_kg += O2_kg
                        stage6 = self.train.stages[6]
                        stage6.collected_kg['O2'] = (
                            stage6.collected_kg.get('O2', 0.0) + O2_kg)

    def _update_melt_composition(self, evap_flux: EvaporationFlux):
        """
        Subtract evaporated mass from the melt.

        When metal X evaporates from the melt, we remove the
        corresponding oxide (X₂O, XO, XO₂, etc.) from the
        melt composition.  The metal goes to the condensation
        train; the oxygen goes to the O₂ accumulator.

        For oxide vapors (e.g., SiO from SiO₂), the stoichiometry
        is different: kg_SiO₂_removed = kg_SiO × (M_SiO₂ / M_SiO).
        """
        metals_data = self.vapor_pressures.get('metals', {})
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            parent_oxide = sp_data.get('parent_oxide', '')
            if not parent_oxide or parent_oxide not in self.melt.composition_kg:
                continue

            # For oxide vapors, use the YAML stoich ratio
            oxide_per_vapor = sp_data.get('stoich_oxide_per_vapor')
            if oxide_per_vapor:
                oxide_removed = rate_kg_hr * oxide_per_vapor
            else:
                # Metal species: use STOICH_RATIOS
                stoich = STOICH_RATIOS.get(parent_oxide)
                if stoich and stoich[0] > 0:
                    oxide_removed = rate_kg_hr / stoich[0]
                else:
                    continue

            current = self.melt.composition_kg[parent_oxide]
            self.melt.composition_kg[parent_oxide] = max(
                0.0, current - oxide_removed)

        self.melt.update_total_mass()

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

        # Update melt composition (subtract reduced oxides)
        for oxide, kg_removed in result.get('oxides_reduced_kg', {}).items():
            if oxide in self.melt.composition_kg:
                self.melt.composition_kg[oxide] = max(
                    0.0, self.melt.composition_kg[oxide] - kg_removed)

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
                self.train.stages[stage_idx].collected_kg[metal] = (
                    self.train.stages[stage_idx].collected_kg.get(metal, 0.0)
                    + kg_produced)

        self._mre_metals_this_hr = dict(result.get('metals_produced_kg', {}))

        self.melt.update_total_mass()

        # Route anodic O₂ to Stage 6 accumulator (mass balance).       [Step 5]
        O2_kg = result.get('O2_produced_kg', 0.0)
        self.oxygen_cumulative_kg += O2_kg
        if O2_kg > 1e-10:
            self.train.stages[6].collected_kg['O2'] = (
                self.train.stages[6].collected_kg.get('O2', 0.0) + O2_kg)

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
            # Primary source: user-supplied inventory additives
            self.shuttle_K_inventory_kg = self.record.additives_kg.get('K', 0.0)
            # Bonus: collect any K condensed during earlier campaigns
            for stage in self.train.stages:
                self.shuttle_K_inventory_kg += stage.collected_kg.get('K', 0.0)
            self.shuttle_cycle_K = 0

        elif campaign == CampaignPhase.C3_NA:
            # Primary source: user-supplied inventory additives
            self.shuttle_Na_inventory_kg = self.record.additives_kg.get('Na', 0.0)
            # Bonus: collect any Na condensed during earlier campaigns
            for stage in self.train.stages:
                self.shuttle_Na_inventory_kg += stage.collected_kg.get('Na', 0.0)
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

        # Apply to melt
        self.melt.composition_kg['FeO'] = max(
            0.0, self.melt.composition_kg.get('FeO', 0.0) - FeO_removed_kg)
        self.melt.composition_kg['K2O'] = (
            self.melt.composition_kg.get('K2O', 0.0) + K2O_added_kg)

        # Fe produced goes to condenser Stage 1 (liquid Fe drains to sump)
        stage1 = self.train.stages[1]
        stage1.collected_kg['Fe'] = (
            stage1.collected_kg.get('Fe', 0.0) + Fe_produced_kg)

        # Deduct K from shuttle inventory
        # (K comes from additives, not from a condenser stage)
        self.shuttle_K_inventory_kg -= K_used_kg

        # Update totals
        self.melt.update_total_mass()

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

            self.melt.composition_kg['Cr2O3'] = max(
                0.0, self.melt.composition_kg.get('Cr2O3', 0.0) - Cr2O3_removed)
            self.melt.composition_kg['Na2O'] = (
                self.melt.composition_kg.get('Na2O', 0.0) + Na2O_from_Cr)
            self.train.stages[1].collected_kg['Cr'] = (
                self.train.stages[1].collected_kg.get('Cr', 0.0) + Cr_produced)

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

            self.melt.composition_kg['TiO2'] = max(
                0.0, self.melt.composition_kg.get('TiO2', 0.0) - TiO2_removed)
            self.melt.composition_kg['Na2O'] = (
                self.melt.composition_kg.get('Na2O', 0.0) + Na2O_from_Ti)
            self.train.stages[1].collected_kg['Ti'] = (
                self.train.stages[1].collected_kg.get('Ti', 0.0) + Ti_produced)

            mol_Na -= mol_Na_for_Ti
            Na_used += (mol_Na_for_Ti * MOLAR_MASS['Na']) / 1000.0
            total_Na2O_added += Na2O_from_Ti
            total_metal_produced += Ti_produced
            total_oxide_reduced += TiO2_removed

        # Deduct Na from shuttle inventory
        # (Na comes from additives, not from a condenser stage)
        self.shuttle_Na_inventory_kg -= Na_used

        self.melt.update_total_mass()

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
        # Primary source: user additives
        self.thermite_Mg_inventory_kg = self.record.additives_kg.get('Mg', 0.0)

        # Bonus: collect Mg condensed in earlier campaigns (C4 Mg pyrolysis)
        for stage in self.train.stages:
            self.thermite_Mg_inventory_kg += stage.collected_kg.get('Mg', 0.0)

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

            # Apply back-reduction to melt
            self.melt.composition_kg['SiO2'] = max(
                0.0, self.melt.composition_kg.get('SiO2', 0.0) - SiO2_consumed_kg)
            self.melt.composition_kg['Al2O3'] = (
                self.melt.composition_kg.get('Al2O3', 0.0) + Al2O3_regenerated_kg)

            # Si product → condenser Stage 2
            self.train.stages[2].collected_kg['Si'] = (
                self.train.stages[2].collected_kg.get('Si', 0.0) + Si_produced_kg)

            # Net Al after back-reduction
            Al_produced_kg -= Al_lost_to_back_kg

            # Net Al₂O₃ removal (primary minus regenerated)
            Al2O3_removed_kg -= Al2O3_regenerated_kg

        # --- Apply primary reaction to melt ---
        self.melt.composition_kg['Al2O3'] = max(
            0.0, self.melt.composition_kg.get('Al2O3', 0.0) - Al2O3_removed_kg)
        self.melt.composition_kg['MgO'] = (
            self.melt.composition_kg.get('MgO', 0.0) + MgO_produced_kg)

        # Al product → condenser Stage 1 (liquid metal sump)
        self.train.stages[1].collected_kg['Al'] = (
            self.train.stages[1].collected_kg.get('Al', 0.0) + max(0.0, Al_produced_kg))

        # Deduct Mg from thermite inventory
        self.thermite_Mg_inventory_kg -= Mg_consumed_kg

        # Update melt totals
        self.melt.update_total_mass()

        # Track for snapshot / summary
        self._thermite_Al2O3_reduced_this_hr = max(0.0, Al2O3_removed_kg)
        self._thermite_Al_produced_this_hr = max(0.0, Al_produced_kg)
        self._thermite_Mg_consumed_this_hr = Mg_consumed_kg

    # ------------------------------------------------------------------
    # Equipment spec helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Snapshot construction
    # ------------------------------------------------------------------

    def _make_snapshot(self) -> HourSnapshot:
        """Build an HourSnapshot from current state."""
        # Mass balance check
        mass_in = self.record.batch_mass_kg + sum(
            self.record.additives_kg.values())
        # total_by_species() already includes O₂ in Stage 6 accumulator,
        # so don't add oxygen_cumulative_kg separately (avoid double-count)
        mass_out = (self.melt.total_mass_kg
                    + sum(self.train.total_by_species().values())
                    + sum(self.train.volatiles_collected_kg.values()))
        error_pct = 0.0
        if mass_in > 0:
            error_pct = abs(mass_in - mass_out) / mass_in * 100.0

        return HourSnapshot(
            hour=self.melt.hour,
            campaign=self.melt.campaign,
            temperature_C=self.melt.temperature_C,
            melt_mass_kg=self.melt.total_mass_kg,
            composition_wt_pct=self.melt.composition_wt_pct(),
            overhead=self.overhead,
            condensation_totals=self.train.total_by_species(),
            energy_cumulative_kWh=self.energy_cumulative_kWh,
            oxygen_produced_kg=self.oxygen_cumulative_kg,
            mass_in_kg=mass_in,
            mass_out_kg=mass_out,
            mass_balance_error_pct=error_pct,
            # Gas train feedback
            ramp_throttled=self._last_actual_ramp < self._last_nominal_ramp * 0.99,
            nominal_ramp_rate_C_hr=self._last_nominal_ramp,
            actual_ramp_rate_C_hr=self._last_actual_ramp,
            throttle_reason=self._last_throttle_reason,
            O2_vented_kg_hr=self.overhead.O2_vented_kg_hr,
            O2_vented_cumulative_kg=self.O2_vented_cumulative_kg,
            O2_stored_kg=self.O2_stored_cumulative_kg,
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
