"""
Core simulator state models and physical constants.

This module is intentionally data-only: enums, constants, and dataclasses used
by the simulation engine and subsystem models. Keep process behavior in the
engine/subsystem modules so agents can read contracts without loading the full
simulation loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Tuple

from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL

# ============================================================================
# SECTION 1: CONSTANTS
# ============================================================================

# --- Oxide species tracked in the melt ---
# These are the major basaltic oxides plus compatible Ni/Co trace oxides.
# The simulator tracks their absolute mass (kg) in the melt at each hour.
OXIDE_SPECIES = [
    'SiO2', 'TiO2', 'Al2O3', 'FeO', 'Fe2O3', 'MgO',
    'CaO', 'Na2O', 'K2O', 'Cr2O3', 'MnO', 'P2O5',
    'NiO', 'CoO',
]

# --- Metal products extracted from the melt ---
# Each metal is obtained by reducing or evaporating its parent oxide.
METAL_SPECIES = [
    'Na', 'K', 'Fe', 'Mg', 'Si', 'Ti', 'Al', 'Ca', 'Cr', 'Mn',
    'Ni', 'Co',
]

# --- Volatile / gas species ---
# Tracked in the overhead gas and condensation train.
GAS_SPECIES = [
    'O2', 'SiO', 'N2', 'H2O', 'CO2', 'S2',
]

# --- Molar masses (g/mol) ---
# Used for stoichiometric conversions (oxide → metal + O₂).
_AW = ATOMIC_WEIGHTS_G_PER_MOL
MOLAR_MASS = {
    # Oxides
    'SiO2': _AW['Si'] + 2 * _AW['O'],
    'TiO2': _AW['Ti'] + 2 * _AW['O'],
    'Al2O3': 2 * _AW['Al'] + 3 * _AW['O'],
    'FeO': _AW['Fe'] + _AW['O'],
    'Fe2O3': 2 * _AW['Fe'] + 3 * _AW['O'],
    'MgO': _AW['Mg'] + _AW['O'],
    'CaO': _AW['Ca'] + _AW['O'],
    'Na2O': 2 * _AW['Na'] + _AW['O'],
    'K2O': 2 * _AW['K'] + _AW['O'],
    'Cr2O3': 2 * _AW['Cr'] + 3 * _AW['O'],
    'MnO': _AW['Mn'] + _AW['O'],
    'P2O5': 2 * _AW['P'] + 5 * _AW['O'],
    'NiO': _AW['Ni'] + _AW['O'],
    'CoO': _AW['Co'] + _AW['O'],
    # Metals
    'Na': _AW['Na'], 'K': _AW['K'], 'Fe': _AW['Fe'], 'Mg': _AW['Mg'],
    'Si': _AW['Si'], 'Ti': _AW['Ti'], 'Al': _AW['Al'], 'Ca': _AW['Ca'],
    'Cr': _AW['Cr'], 'Mn': _AW['Mn'], 'Ni': _AW['Ni'], 'Co': _AW['Co'],
    # Gases
    'O2': 2 * _AW['O'], 'O': _AW['O'], 'SiO': _AW['Si'] + _AW['O'],
    'N2': 2 * _AW['N'], 'H2O': 2 * _AW['H'] + _AW['O'],
    'CO2': _AW['C'] + 2 * _AW['O'], 'S2': 2 * _AW['S'],
}

# --- Oxide → Metal mapping ---
# For each oxide, how many metal atoms and how many O atoms
# are released per formula unit during reduction.
#   oxide_key: (metal_key, n_metal_atoms, n_oxygen_atoms, metal_mass_per_oxide_mass)
OXIDE_TO_METAL = {
    'Na2O':  ('Na', 2, 1),   # Na₂O  → 2 Na + ½ O₂
    'K2O':   ('K',  2, 1),   # K₂O   → 2 K  + ½ O₂
    'FeO':   ('Fe', 1, 1),   # FeO   → Fe   + ½ O₂
    'Fe2O3': ('Fe', 2, 3),   # Fe₂O₃ → 2 Fe + 1½ O₂
    'MgO':   ('Mg', 1, 1),   # MgO   → Mg   + ½ O₂
    'SiO2':  ('Si', 1, 2),   # SiO₂  → Si   + O₂
    'TiO2':  ('Ti', 1, 2),   # TiO₂  → Ti   + O₂
    'Al2O3': ('Al', 2, 3),   # Al₂O₃ → 2 Al + 1½ O₂
    'CaO':   ('Ca', 1, 1),   # CaO   → Ca   + ½ O₂
    'Cr2O3': ('Cr', 2, 3),   # Cr₂O₃ → 2 Cr + 1½ O₂
    'MnO':   ('Mn', 1, 1),   # MnO   → Mn   + ½ O₂
    'NiO':   ('Ni', 1, 1),   # NiO   → Ni   + ½ O₂
    'CoO':   ('Co', 1, 1),   # CoO   → Co   + ½ O₂
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
    CO2_BACKPRESSURE = auto()    # Mars surface CO₂ pressure floor during C0


# ============================================================================
# SECTION 3: DATA STRUCTURES
# ============================================================================

@dataclass
class ProcessInventory:
    """
    Feedstock inventory outside the silicate melt contract.

    MeltState owns only the cleaned melt oxides used by melt backends. This
    structure keeps raw feedstock provenance, Stage 0 volatile/trap products,
    and separated salt, sulfide, and metal phase inventories explicit without
    feeding them into melt equilibrium calculations.
    """
    raw_components_kg: Dict[str, float] = field(default_factory=dict)
    melt_oxide_kg: Dict[str, float] = field(default_factory=dict)
    residual_components_kg: Dict[str, float] = field(default_factory=dict)
    stage0_products_kg: Dict[str, float] = field(default_factory=dict)
    gas_volatiles_kg: Dict[str, float] = field(default_factory=dict)
    salt_phase_kg: Dict[str, float] = field(default_factory=dict)
    sulfide_matte_kg: Dict[str, float] = field(default_factory=dict)
    metal_alloy_kg: Dict[str, float] = field(default_factory=dict)
    terminal_slag_components_kg: Dict[str, float] = field(default_factory=dict)
    stage0_profile: str = 'bulk_preservation'
    cleaned_melt_source: str = 'composition_wt_pct'
    stage0_temp_range_C: Tuple[float, float] = (20.0, 950.0)
    carbon_reductant_required_kg: float = 0.0
    stage0_mass_balance_delta_kg: float = 0.0

    def copy(self) -> 'ProcessInventory':
        """Return a detached copy for records and snapshots."""
        return ProcessInventory(
            raw_components_kg=dict(self.raw_components_kg),
            melt_oxide_kg=dict(self.melt_oxide_kg),
            residual_components_kg=dict(self.residual_components_kg),
            stage0_products_kg=dict(self.stage0_products_kg),
            gas_volatiles_kg=dict(self.gas_volatiles_kg),
            salt_phase_kg=dict(self.salt_phase_kg),
            sulfide_matte_kg=dict(self.sulfide_matte_kg),
            metal_alloy_kg=dict(self.metal_alloy_kg),
            terminal_slag_components_kg=dict(
                self.terminal_slag_components_kg),
            stage0_profile=self.stage0_profile,
            cleaned_melt_source=self.cleaned_melt_source,
            stage0_temp_range_C=tuple(self.stage0_temp_range_C),
            carbon_reductant_required_kg=self.carbon_reductant_required_kg,
            stage0_mass_balance_delta_kg=self.stage0_mass_balance_delta_kg,
        )

    def residual_mass_kg(self) -> float:
        """Mass outside the current silicate melt calculation."""
        return sum(self.residual_components_kg.values())

    @property
    def drain_tap_kg(self) -> Dict[str, float]:
        """Separated native/alloy metal available to the drain-tap ledger."""
        return self.metal_alloy_kg


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
    ambient_pressure_mbar: float = 0.0
    # Site pressure floor for bodies without hard vacuum, e.g. Mars ~6 mbar.
    ambient_atmosphere: str = ''

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
                              (900, 1200), ['SiO']),
            CondensationStage(3, 'Alkali/Mg Cyclone',
                              (350, 700), ['Na', 'K', 'Mg']),
            CondensationStage(4, 'Vortex Dust Filter',
                              (200, 350), []),
            CondensationStage(5, 'Turbine-Compressor',
                              (50, 200), []),
            CondensationStage(6, 'Turbine Outlet Monitor',
                              (20, 50), []),
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

    turbine_flow_mol_hr: float = 0.0
    # O₂ molar flow through turbine

    pipe_conductance_kg_hr: float = 50.0
    # Maximum transport capacity of collection pipe (kg/hr)
    # Depends on pipe diameter, pressure, temperature

    # --- Gas train feedback fields ---
    turbine_limited: bool = False
    # True when O₂ production exceeds turbine max capacity

    O2_vented_kg_hr: float = 0.0
    # O₂ vented to lunar vacuum this hour (excess beyond turbine max)

    O2_vented_mol_hr: float = 0.0
    # O₂ molar flow vented this hour

    melt_offgas_O2_mol_hr: float = 0.0
    # O₂ molar flow generated by melt/offgas chemistry this hour

    mre_anode_O2_mol_hr: float = 0.0
    # O₂ molar flow generated at MRE anodes this hour

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
    inventory: ProcessInventory = field(default_factory=ProcessInventory)

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

    melt_offgas_O2_stored_kg: float = 0.0
    # Cumulative melt/offgas O₂ in accumulator

    melt_offgas_O2_vented_kg: float = 0.0
    # Cumulative melt/offgas O₂ vented to vacuum

    mre_anode_O2_stored_kg: float = 0.0
    # Cumulative MRE anode O₂ stored separately from gas-train throughput

    melt_offgas_O2_mol_hr: float = 0.0
    # O₂ molar flow generated by melt/offgas chemistry this hour

    mre_anode_O2_mol_hr: float = 0.0
    # O₂ molar flow generated at MRE anodes this hour

    O2_vented_mol_hr: float = 0.0
    # O₂ molar flow vented this hour

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
    initial_inventory: ProcessInventory = field(default_factory=ProcessInventory)

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
    oxygen_stored_kg: float = 0.0
    oxygen_vented_kg: float = 0.0
    terminal_slag_kg: float = 0.0

    # Energy
    energy_total_kWh: float = 0.0
    energy_by_campaign: Dict[str, float] = field(default_factory=dict)

    # Status
    completed: bool = False
    total_hours: int = 0
