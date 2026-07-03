"""
Core simulator state models and physical constants.

This module is intentionally data-only: enums, constants, and dataclasses used
by the simulation engine and subsystem models. Keep process behavior in the
engine/subsystem modules so agents can read contracts without loading the full
simulation loop.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Iterable, List, Tuple

from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.condensation_routing import target_species_for_stage_number

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

PRESSURE_PARTIAL_TOTAL_TOL_MBAR = 1e-9

# --- Metal products extracted from the melt ---
# Each metal is obtained by reducing or evaporating its parent oxide.
METAL_SPECIES = [
    'Na', 'K', 'Fe', 'Mg', 'Si', 'Ti', 'Al', 'Ca', 'Cr', 'Mn',
    'Ni', 'Co',
]

# --- Volatile / gas species ---
# Tracked in the overhead gas and condensation train.
GAS_SPECIES = [
    'O2', 'SiO', 'CrO2', 'N2', 'H2O', 'CO2', 'S2',
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
    'FeSi': _AW['Fe'] + _AW['Si'],
    # Gases
    'O2': 2 * _AW['O'], 'O': _AW['O'], 'SiO': _AW['Si'] + _AW['O'],
    'CrO2': _AW['Cr'] + 2 * _AW['O'],
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

# Operator-controlled induction-stirring ceiling. See ``MeltState.
# stir_factor`` doc for the two consumer subsystems (evaporation +
# condensation). The "melt-flying-out-of-the-pot" upper bound — typical
# industrial ceiling for 1-tonne crucible induction stirring (the recipe
# setpoint window is 4-8× per ``data/setpoints.yaml § induction_
# stirring``; 10× is the empirical ceiling beyond which the melt surface
# breaks up). All override paths use ``clamp_stir_factor`` so both
# consumers honour this ceiling.
#
# Phase B (0.5.3 2-axis stirring): the same ``MAX_STIR_FACTOR`` ceiling
# applies PER-AXIS to ``StirState.axial`` and ``StirState.radial``.
# Industrial multi-coil EM stirrers carry independent budgets on each
# axis — the melt-surface-breakup ceiling is set by total kinetic
# energy injected at the worst single axis, not the L2 sum. A
# component-wise clamp is the right operator-boundary contract.
MAX_STIR_FACTOR = 10.0


def clamp_stir_factor(value: float) -> float:
    """Return ``value`` clamped to the operator-facing range
    ``[0.0, MAX_STIR_FACTOR]``, mapping non-finite (NaN, +/-inf) inputs
    and ``bool`` to the fail-closed default ``0.0``.

    Two consumer subsystems disagree on the meaning of the lower bound:

    - ``engines/builtin/evaporation_flux.py``: linear multiplier on
      H-K-L evaporation flux. ``stir_factor=0`` is a LEGITIMATE
      operator signal meaning "halt evaporation" (debug pause, manual
      hold). Pre-Phase B this halt path was operator-accessible and
      the simulator preserved it; this helper preserves it too.
    - ``simulator/condensation.py``: drives the Sherwood-enhancement
      ``Sh_eff = 3.66 × √stir_factor`` in the series-resistance flux.
      Sherwood physics has its OWN floor at ``Sh = 3.66`` (laminar
      pipe asymptote, BSL Eq 14.4-9); a ``stir_factor=0`` here must
      not collapse Sh to zero. That physics floor lives in
      ``_stirring_enhanced_sherwood`` so this canonical helper can
      keep the operator-facing convention intact.

    Defensive behaviour (codex /code-review max-effort sweep,
    pre-0.5.2 Phase B):

    - Non-finite (NaN, +/-inf) → ``0.0``. An upstream numerical
      instability poisoning ``melt.stir_factor`` halts evaporation
      rather than silently propagating NaN into the ledger.
      ``_stirring_enhanced_sherwood`` then floors Sh at the laminar
      baseline, so condensation stays defined.
    - ``bool`` rejected → ``0.0``. ``True`` would coerce to ``1.0``
      (the laminar-baseline no-stir) and ``False`` to ``0.0`` — both
      kill stirring silently when a YAML/JSON deserialiser hands a
      boolean. Reject so the misconfiguration is at least
      audit-visible (operating-history records the 0.0 with
      ``stir_factor_clamped: True``).
    - Non-numeric strings or other unconvertible inputs → ``0.0``.

    Python pitfall guard: ``min(MAX, NaN) == MAX`` and ``max(0.0, NaN)
    == NaN`` make a naive ``max(0.0, min(MAX, x))`` an attack surface;
    the explicit ``math.isfinite`` check covers it.

    Phase B (0.5.3 2-axis stirring): this scalar helper is preserved as
    the per-axis operator-boundary clamp. ``clamp_stir_state`` (below)
    is the companion 2-axis helper that applies ``clamp_stir_factor``
    component-wise to ``StirState.axial`` and ``StirState.radial``.
    Scalar inputs to ``clamp_stir_state`` map to the axial axis only
    (backward-compat with the 0.5.2 single-axis writer surface).
    """
    # bool is a subclass of int; reject explicitly before float()
    # would otherwise coerce True->1.0 / False->0.0.
    if isinstance(value, bool):
        return 0.0
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(raw):
        return 0.0
    return max(0.0, min(float(MAX_STIR_FACTOR), raw))


# Set of valid StirState dict-input keys for the typo audit in
# ``clamp_stir_state``. Keep in lockstep with the ``StirState``
# dataclass fields below.
_KNOWN_STIR_STATE_KEYS = frozenset(('axial', 'radial'))


@dataclass
class StirState:
    """Two-axis induction-stirring state (0.5.3 Phase B).

    Industrial multi-coil EM stirrers expose two independent control
    axes that map to physically distinct mixing modes:

    - ``axial``: vertical EM stirring. Drives bottom-to-surface
      circulation in the melt → surface renewal → linear H-K-L
      evaporation-flux multiplier (consumer:
      ``engines/builtin/evaporation_flux.py``). Default 6.0 preserves
      the 0.5.2 single-axis C2A setpoint (mid-band of the documented
      ``data/setpoints.yaml § induction_stirring`` 4-8× window).
    - ``radial``: horizontal / azimuthal EM stirring. Drives in-plane
      vortex in the gas just above the melt → gas-side boundary-layer
      Sherwood enhancement (consumer:
      ``simulator/condensation.py::_stirring_enhanced_sherwood``).
      Default 1.0 = no-stir laminar baseline (``Sh = 3.66``, BSL
      Eq 14.4-9). Operator can dial up to 10× per-axis.

    Per-axis ``MAX_STIR_FACTOR = 10.0`` ceiling. Each axis preserves
    the scalar 0.5.2 semantics when the other is at its default
    (axial=1.0 OR radial=1.0 → laminar baseline on that consumer).

    Backward-compat (deprecation cycle, 0.5.3 → 0.5.4):

    - ``MeltState.stir_factor`` property reads ``stir_state.axial`` and
      writes ``stir_state.axial``. Scalar operator-override paths
      (``simulator/session.py::SimSession.adjust("stir_factor", ...)``,
      ``simulator/campaigns.py`` overrides) keep accepting a scalar →
      mapped to ``axial`` only. New ``session.adjust("stir_state",
      {axial, radial})`` path drives both axes. 0.5.4 may remove the
      scalar path entirely once campaigns migrate; this dataclass +
      helper land first so the seam is stable.

    Design rationale (B1 office-hours, 2026-05-28): axial × radial
    decomposition (option 1) was selected over mean/turbulence (option
    2) and Re/f_renewal (option 3) because it maps directly to
    industrial multi-coil EM stirrer design, preserves the scalar
    interpretation when one axis is at 1.0, and cleanly separates the
    melt-side surface-renewal effect (evap H-K-L) from the gas-side
    boundary-layer effect (condensation Sh).
    """

    axial: float = 6.0
    radial: float = 1.0


def clamp_stir_state(value: Any) -> StirState:
    """Return ``value`` coerced to a defensively-bounded ``StirState``.

    The operator-boundary 2-axis companion to ``clamp_stir_factor``.
    Accepts the union of input shapes the 0.5.3 writer surface
    produces and routes each axis through ``clamp_stir_factor`` so the
    fail-closed semantics carry component-wise:

    - ``StirState``: each axis re-clamped independently (idempotent
      for already-valid input; sanitises a hand-constructed instance
      whose axes drift out of range).
    - ``Mapping`` (e.g. dict from YAML override or
      ``session.adjust("stir_state", ...)``): pull ``axial`` and
      ``radial`` keys (default 1.0 = laminar baseline if a key is
      missing, since a partial dict signals the operator only meant to
      touch one axis). Each value clamped via ``clamp_stir_factor``.
    - Scalar (float-coercible): legacy single-axis writer → mapped to
      ``axial`` only, ``radial`` set to ``1.0`` (laminar baseline).
      This is the backward-compat path for 0.5.2 callers that pass a
      bare float through ``session.adjust("stir_factor", ...)`` or a
      campaign override with ``'stir_factor': <float>``.
    - ``bool``: explicitly rejected (bool is a Python int subclass; the
      coercion would silently lie ``True``→1.0 / ``False``→0.0). Both
      axes fail-closed to 0.0.
    - Non-finite (NaN, ±inf) or non-numeric inputs: each axis
      fail-closed to 0.0 (mirrors ``clamp_stir_factor``).
    - ``None``: both axes fail-closed to 0.0 (corrupt-state recovery).

    Returns a fresh ``StirState`` instance — never mutates the caller's
    dict / dataclass. Both axes are guaranteed to be finite floats in
    ``[0.0, MAX_STIR_FACTOR]``.
    """

    # Reject bool BEFORE the Mapping / scalar branches (bool is not a
    # Mapping and would coerce through float() via the scalar branch
    # otherwise — same defensive contract as clamp_stir_factor).
    if isinstance(value, bool):
        return StirState(axial=0.0, radial=0.0)
    if value is None:
        return StirState(axial=0.0, radial=0.0)
    if isinstance(value, StirState):
        return StirState(
            axial=clamp_stir_factor(value.axial),
            radial=clamp_stir_factor(value.radial),
        )
    # Mapping check uses collections.abc.Mapping for duck-typing
    # YAML-loaded dicts, OrderedDict, etc.
    from collections.abc import Mapping as _Mapping
    if isinstance(value, _Mapping):
        # Missing keys default to 1.0 (laminar baseline). A dict
        # carrying only one axis signals "operator only meant to
        # tweak this axis" — preserving the other as the no-stir
        # baseline keeps that lever explicit.
        raw_axial = value.get('axial', 1.0)
        raw_radial = value.get('radial', 1.0)
        # Typo audit (0.5.4 W2, 0.5.3 Phase B P3 #2 deferral): silently
        # dropping unknown keys lets an operator typo like
        # ``{'radail': 8}`` evaporate without trace — operator thinks
        # they dialed radial up to 8, simulator runs with radial=1.0
        # (laminar Sh). Surface unknown keys via UserWarning so the
        # misconfiguration is audit-visible. Behaviour unchanged: any
        # extras are still ignored, only the warning is new.
        unknown_keys = set(value.keys()) - _KNOWN_STIR_STATE_KEYS
        if unknown_keys:
            warnings.warn(
                f"clamp_stir_state: ignoring unknown StirState keys "
                f"{sorted(unknown_keys)}; valid keys are "
                f"{sorted(_KNOWN_STIR_STATE_KEYS)}. "
                f"Common typos: 'radail' → 'radial', 'axail' → 'axial'.",
                UserWarning,
                stacklevel=2,
            )
        return StirState(
            axial=clamp_stir_factor(raw_axial),
            radial=clamp_stir_factor(raw_radial),
        )
    # Scalar fallback: any float-coercible value lands on the axial
    # axis (legacy 0.5.2 single-axis writer surface). Non-coercible
    # inputs (e.g. arbitrary object) map to fail-closed 0.0 via the
    # inner clamp_stir_factor.
    return StirState(
        axial=clamp_stir_factor(value),
        radial=1.0,
    )


# ============================================================================
# SECTION 2: ENUMERATIONS
# ============================================================================

class CampaignPhase(Enum):
    """Which campaign the furnace is currently running."""
    IDLE = auto()             # Not processing — waiting for batch
    C0 = auto()               # Vacuum bakeoff
    C0B = auto()              # P-cleanup (mild oxidative hold)
    C2A = auto()              # Continuous adaptive pN₂ ramp (Path A)
    C2A_STAGED = auto()       # Staged pN₂ bakeout (Path A staged)
    C2B = auto()              # pO₂-managed Fe pyrolysis (Path B)
    C3_K = auto()             # Alkali shuttle — K phase
    C3_NA = auto()            # Alkali shuttle — Na phase
    C4 = auto()               # Mg selective pyrolysis
    C5 = auto()               # Limited MRE (electrolysis)
    C6 = auto()               # Mg thermite reduction
    C7_CA_ALUMINOTHERMIC = auto()  # Aluminothermic Ca recovery
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
    C7_PROCEED = auto()       # Proceed with C7 Ca aluminothermy?

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
    chloride_salt_phase_kg: Dict[str, float] = field(default_factory=dict)
    cation_sulfate_feed_kg: Dict[str, float] = field(default_factory=dict)
    sulfide_matte_kg: Dict[str, float] = field(default_factory=dict)
    metal_alloy_kg: Dict[str, float] = field(default_factory=dict)
    terminal_slag_components_kg: Dict[str, float] = field(default_factory=dict)
    stage0_external_inputs_kg: Dict[str, float] = field(default_factory=dict)
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
            chloride_salt_phase_kg=dict(self.chloride_salt_phase_kg),
            cation_sulfate_feed_kg=dict(self.cation_sulfate_feed_kg),
            sulfide_matte_kg=dict(self.sulfide_matte_kg),
            metal_alloy_kg=dict(self.metal_alloy_kg),
            terminal_slag_components_kg=dict(
                self.terminal_slag_components_kg),
            stage0_external_inputs_kg=dict(self.stage0_external_inputs_kg),
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
class OxygenReservoirState:
    melt_intrinsic_fO2_log: float = -9.0
    reference_T_K: float | None = None
    headspace_ledger_pO2_bar: float = 1e-9
    headspace_transport_pO2_bar: float = 1e-9
    headspace_control_floor_pO2_bar: float = 0.0
    k_O_m_s: float = 0.0
    k_O_source: str = ""
    effective_melt_depth_m: float = 0.0
    tau_hr: float = 0.0
    melt_redox_capacity_mol_per_ln_fO2: float = 0.0
    headspace_capacity_mol_per_ln_pO2: float = 0.0
    exchange_o2_mol: float = 0.0
    exchange_o2_kg: float = 0.0
    exchange_direction: str = ""
    exchange_clamped: bool = False
    exchange_transition_name: str = ""
    redox_source_terms_mol_o2_equiv: Dict[str, float] = field(default_factory=dict)
    redox_source_applied_terms_mol_o2_equiv: Dict[str, float] = field(
        default_factory=dict
    )
    redox_source_skipped_terms_mol_o2_equiv: Dict[str, float] = field(
        default_factory=dict
    )
    redox_source_skipped_reasons_by_label: Dict[str, str] = field(
        default_factory=dict
    )
    redox_source_terms_applied: bool = False
    redox_source_skip_reason: str = ""
    redox_source_net_mol_o2_equiv: float = 0.0
    redox_source_delta_ln_fO2: float = 0.0
    redox_source_delta_log10_fO2: float = 0.0
    redox_source_refusal_context: Dict[str, Any] = field(default_factory=dict)
    ferric_divergence: Dict[str, Any] = field(default_factory=dict)


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
    # SSO-R intrinsic melt redox state, log10(fO2/bar); seeded from the
    # legacy intrinsic estimate, then advanced by the oxygen reservoir.
    melt_fO2_log: float = -9.0
    oxygen_reservoir: OxygenReservoirState = field(
        default_factory=OxygenReservoirState)
    ambient_pressure_mbar: float = 0.0
    # Site pressure floor for bodies without hard vacuum, e.g. Mars ~6 mbar.
    ambient_atmosphere: str = ''
    background_gas_species: str = ''
    # Lab/preset carrier gas species used to populate overhead gas state.
    background_gas_mole_fraction: float = 0.0

    # --- Process state ---
    campaign: CampaignPhase = CampaignPhase.IDLE
    hour: int = 0                   # Hours since batch start
    campaign_hour: int = 0          # Hours since current campaign started

    # --- Stirring ---
    stir_state: 'StirState' = field(default_factory=StirState)
    # Induction stirring acceleration factor (4-8×, hard-clamped at
    # ``MAX_STIR_FACTOR`` per-axis). 0.5.3 Phase B: replaced the
    # single-scalar ``stir_factor`` field with a 2-axis ``StirState``
    # dataclass (axial × radial). The legacy ``stir_factor`` attribute
    # is preserved as a property below that aliases to
    # ``stir_state.axial`` for the deprecation cycle. Two subsystems
    # consume the axes:
    #
    #   1. ``engines/builtin/evaporation_flux.py``: reads ``axial`` as
    #      the linear multiplier on the Hertz-Knudsen evaporation rate
    #      (surface renewal + thermal cycling boost from vertical EM
    #      stirring drives melt-side surface refresh).
    #   2. ``simulator/condensation.py`` (Phase B 0.5.3 split): reads
    #      ``radial`` for the stir-enhanced Sherwood number
    #      ``Sh = 3.66·√radial`` in the series-resistance
    #      boundary-layer flux (gas-side in-plane vortex drives bulk-
    #      to-wall mass transport).
    #
    # Both consumers honour ``MAX_STIR_FACTOR`` per-axis; override paths
    # (``simulator/campaigns.py`` campaign overrides, ``simulator/
    # session.py`` ``session.adjust(...)``) funnel through
    # ``clamp_stir_factor`` (per-axis) and ``clamp_stir_state`` (whole
    # dataclass) so a bad override cannot inflate either consumer
    # beyond the documented physical ceiling. Pre-0.5.2 the evaporation
    # path silently accepted arbitrary multipliers; codex /review
    # concern-diverse subagent flagged the clamp-asymmetry (Phase B
    # P1). 0.5.3 B1 office-hours framing chose option 1 (axial × radial)
    # over options 2 (mean/turbulence) and 3 (Re/f_renewal) — see
    # ``StirState`` doc for the rationale.

    # --- MRE state (for endpoint detection) ---
    c5_enabled: bool = False
    mre_target_species: str = ""
    mre_max_voltage_V: float = 0.0
    mre_voltage_V: float = 0.0
    mre_current_A: float = 0.0            # Effective (Faradaic) current
    mre_low_current_hours: int = 0        # Consecutive hours below threshold

    # --- Derived quantities (set by step()) ---
    total_mass_kg: float = 0.0
    melt_surface_area_m2: float = 0.2  # Crucible opening

    @property
    def stir_factor(self) -> float:
        """Backward-compat alias for ``stir_state.axial`` (0.5.3 Phase B).

        Pre-0.5.3 ``MeltState.stir_factor`` was the scalar induction-
        stirring field; 0.5.3 split it into ``StirState`` (axial ×
        radial). The legacy attribute is preserved as a property so
        operator code, golden fixtures, and legacy callers that read
        ``melt.stir_factor`` keep working through the deprecation
        cycle. The property maps to ``stir_state.axial`` because the
        scalar value historically drove BOTH consumers; in the 2-axis
        split the axial axis carries the evaporation H-K-L multiplier
        (legacy 6.0 default) while the radial axis defaults to 1.0
        (laminar Sherwood baseline). 0.5.4 may remove this property
        once campaigns / operator UIs migrate to ``stir_state``
        directly.
        """
        return self.stir_state.axial

    @stir_factor.setter
    def stir_factor(self, value: float) -> None:
        """Backward-compat setter: writes ``stir_state.axial``.

        Does NOT clamp — clamping is the operator-boundary writer's
        responsibility (``simulator/session.py``,
        ``simulator/campaigns.py`` both pre-clamp via
        ``clamp_stir_factor``). Pre-0.5.3 the field was a bare float
        with no setter clamp either; preserving that contract keeps
        legacy attribute-set patterns byte-compatible
        (``melt.stir_factor = 6.0`` post-construction).

        **Construction-time legacy compat is NOT supported** (Phase B
        chunk-review P1 honesty fix): ``MeltState(stir_factor=X)`` was
        never a tested construction shape and raises ``TypeError`` in
        0.5.3+ because the dataclass ``__init__`` exposes
        ``stir_state``, not ``stir_factor``. Legacy callers that
        constructed MeltState with a scalar stir_factor kwarg must
        migrate to either (a) construct then attribute-assign
        ``MeltState(...).stir_factor = X``, or (b) pass an explicit
        ``stir_state=StirState(axial=X)``. No known callers do this in
        the current tree; the deprecation surface is read-via-property
        + write-via-setter only.
        """
        self.stir_state.axial = float(value)

    def validate_melt_pressures(self) -> None:
        try:
            pO2 = float(self.pO2_mbar)
            p_total = float(self.p_total_mbar)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "melt_pressure_invalid_nonfinite: "
                f"pO2_mbar={self.pO2_mbar!r} "
                f"p_total_mbar={self.p_total_mbar!r}"
            ) from exc
        if not math.isfinite(pO2) or not math.isfinite(p_total):
            raise ValueError(
                "melt_pressure_invalid_nonfinite: "
                f"pO2_mbar={self.pO2_mbar!r} "
                f"p_total_mbar={self.p_total_mbar!r}"
            )
        if pO2 < 0.0 or p_total < 0.0:
            raise ValueError(
                "melt_pressure_invalid_negative: "
                f"pO2_mbar={pO2:.12g} p_total_mbar={p_total:.12g}"
            )
        tolerance = max(
            PRESSURE_PARTIAL_TOTAL_TOL_MBAR,
            1e-12 * max(1.0, abs(pO2), abs(p_total)),
        )
        if pO2 - p_total > tolerance:
            raise ValueError(
                "melt_pressure_partial_exceeds_total: "
                f"pO2_mbar={pO2:.12g} > p_total_mbar={p_total:.12g}; "
                "oxygen partial pressure cannot exceed total pressure"
            )

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
        from simulator.accounting.queries import condensation_stage_purity_pct

        return condensation_stage_purity_pct(self, species)


PIPE_SEGMENT_NAMES = tuple(
    f'stage_{stage_number}_to_stage_{stage_number + 1}'
    for stage_number in range(7)
)
PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX = 'process.wall_deposit_segment_'
PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS = tuple(
    f'{PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX}{name}'
    for name in PIPE_SEGMENT_NAMES
)


def _validated_wall_deposit_account(account: str) -> str:
    account_name = str(account)
    if not account_name.startswith(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX):
        raise ValueError(
            f"wall deposit account {account_name!r} must start with "
            f"{PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX!r}"
        )
    suffix = account_name.removeprefix(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX)
    if not suffix:
        raise ValueError("wall deposit account suffix is required")
    return account_name


def declared_wall_deposit_accounts(
    extra_accounts: Iterable[str] = (),
) -> tuple[str, ...]:
    extras = sorted({
        _validated_wall_deposit_account(str(account))
        for account in extra_accounts
    })
    return tuple(dict.fromkeys((
        *PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS,
        *extras,
    )))


@dataclass(frozen=True)
class PipeSegment:
    """Interstage pipe segment where cold-wall deposition can occur."""

    name: str
    upstream_stage: str
    downstream_stage: str
    wall_temperature_C: float
    length_m: float
    inner_diameter_m: float
    role: str = ''
    declared_area_m2: float | None = None
    view_factor_from_melt: float | None = None
    line_of_sight_to_melt: bool | None = None
    source_class: str = ''
    sensitivity_marker: str = ''
    extraction_note: str = ''
    liner_material: str = ''

    @property
    def surface_area_m2(self) -> float:
        """Cylindrical wall area for the segment."""

        if self.declared_area_m2 is not None:
            return max(0.0, float(self.declared_area_m2))
        return (
            math.pi
            * max(0.0, float(self.inner_diameter_m))
            * max(0.0, float(self.length_m))
        )

    @property
    def wall_deposit_account(self) -> str:
        """Ledger account used for this segment's wall deposit."""

        return f'{PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX}{self.name}'


@dataclass
class CondensationTrain:
    """
    The complete metals condensation train (8 stages, indexed 0-7).

    Stage 0: Hot duct (>1400°C) — IR spectroscopy, no condensation
    Stage 1: Fe condenser (1100-1400°C)
    Stage 2: Cr oxide harvester (1100-1300°C)
    Stage 3: SiO zone (900-1200°C) — removable fused silica baffles
    Stage 4: Alkali/Mg cyclone (350-700°C)
    Stage 5: Vortex dust filter (200-350°C)
    Stage 6: Turbine/compressor — pressure regulation
    Stage 7: O₂ accumulator (~3 bar)

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
        """Build the standard 8-stage metals train."""
        stages = [
            CondensationStage(0, 'Hot Duct (IR)',
                              (1400, 1600), []),
            CondensationStage(1, 'Fe Condenser',
                              (1100, 1400),
                              target_species_for_stage_number(1)),
            CondensationStage(2, 'Cr Oxide Harvester',
                              (1100, 1300),
                              target_species_for_stage_number(2)),
            CondensationStage(3, 'SiO Zone',
                              (900, 1200),
                              target_species_for_stage_number(3)),
            CondensationStage(4, 'Alkali/Mg Cyclone',
                              (350, 700),
                              target_species_for_stage_number(4)),
            CondensationStage(5, 'Vortex Dust Filter',
                              (200, 350), []),
            CondensationStage(6, 'Turbine-Compressor',
                              (50, 200), []),
            CondensationStage(7, 'Turbine Outlet Monitor',
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

    headspace_volume_m3: float = 0.0
    # Gas-occupied volume between melt surface and turbine inlet

    headspace_temperature_K: float = 0.0
    # Gas temperature used for finite-headspace ideal-gas pressure

    bleed_conductance_kg_s_per_bar: float = 0.0
    # Headspace bleed conductance coefficient

    p_downstream_bar: float = 0.0
    # Downstream sink pressure for the bleed model

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
    condensed_by_stage_species_delta: Dict[Tuple[int, str], float] = field(
        default_factory=dict)
    wall_deposit_by_segment_species_delta: Dict[Tuple[str, str], float] = field(
        default_factory=dict)
    impurity_delta: Dict[Tuple[int, str], float] = field(default_factory=dict)

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
    mre_uncertified_yield: Dict[str, Any] = field(default_factory=dict)
    # Per-tick MRE yield entries that came from heuristic / unanchored
    # chemistry branches. Empty dict means the tick produced no uncertified
    # yield.

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

    stage0_O2_stored_kg: float = 0.0
    # Cumulative Stage 0 low-temperature O₂ stored separately from melt offgas

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

    c3_alkali_credit_drawn_kg_by_species: Dict[str, float] = field(default_factory=dict)
    # Gross Na/K reagent drawn from the recycled C3 credit line this run

    c3_alkali_credit_outstanding_kg_by_species: Dict[str, float] = field(default_factory=dict)
    # Net Na/K makeup still owed to the recycled C3 credit line

    feedstock_recovered_reagent_kg_by_species: Dict[str, float] = field(default_factory=dict)
    # Live feedstock-derived elemental reagent balance in process.reagent_inventory

    non_feedstock_reagent_element_kg_by_account: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # Live additive/credit-derived target-element kg by ledger account

    shuttle_cycle: int = 0
    # Current inject-bakeout cycle number within the C3 phase

    # --- Evaporation-plane selectivity diagnostic (SSO-1) ---
    evap_plane_selectivity: Dict[str, Any] = field(default_factory=dict)
    # Per-tick vapor-flux selectivity surface. Diagnostic only: reports
    # total evolved-vapor flux, per-species flux fractions, and when the
    # staged C2A context resolves a staged target species (the stage's
    # target_species intersected with the evolved-flux species), target/total
    # selectivity. Empty dict means no selectivity diagnostic was computed.

    # --- Fe redox split diagnostic (SSO-R Phase 1) ---
    fe_redox_split: Dict[str, Any] = field(default_factory=dict)
    # Per-tick Kress-Carmichael 1991 fO2 -> Fe3+/Fe2+ split. Diagnostic
    # only: reports read-only fractions and source metadata without mutating
    # melt fO2, a_FeO, evaporation, or the atom ledger.

    # --- Oxygen reservoir exchange diagnostic (SSO-R Phase 1) ---
    oxygen_reservoir: Dict[str, Any] = field(default_factory=dict)
    # Snapshot copy of MeltState.oxygen_reservoir after this tick's
    # pre-equilibrium O2 exchange.

    redox_source_breakdown: Dict[str, Any] = field(default_factory=dict)
    # Per-tick mol-O2-equivalent source terms applied to the melt redox scalar.
    # Diagnostic only; mass movement remains in AtomLedger transitions.

    # --- Knudsen regime warning sticker (0.5.4.1 E3) ---
    knudsen_regime_summary: Dict[str, Any] = field(default_factory=dict)
    # Per-tick Knudsen-regime visibility surface. Carries the
    # canonical fields from ``CondensationModel.
    # last_knudsen_regime_diagnostic``:
    #
    #   - ``status`` (str): ``ok`` / ``warning`` / ``refused``
    #   - ``knudsen_number`` (float): the actual Kn at this tick's
    #     condensation pass
    #   - ``knudsen_regime`` (str): ``viscous`` / ``transition`` /
    #     ``free_molecular``
    #   - ``regime_factor`` (float): the F3 viscous-flow attenuation
    #     factor (1 → pure viscous; 0 → pure ballistic)
    #   - ``warnings`` (tuple[str, ...]): operator-facing strings
    #     when Kn approaches the boundary (e.g., ``Kn=5e-3 near the
    #     0.01 viscous-flow cutoff``); empty when state is clean
    #
    # Complements the F3 hard refusal at ``Kn ≥ 10`` with earlier-
    # warning visibility: an operator who sees the warning surface
    # before the refusal fires can lower ramp rates / adjust pN2
    # sweep proactively. Empty dict on ticks that didn't trigger a
    # condensation route (degenerate case).

    # --- Metal-projection drift audit (0.5.4 W8 / M2 closure) ---
    metal_projection_drift_kg: Dict[str, float] = field(default_factory=dict)
    # Per-species drift (ledger_kg - projection_kg) for metal species
    # whose ``process.metal_phase`` account (canonical AtomLedger entry)
    # differs from the sum across
    # ``train.stages[*].collected_kg`` (UI projection) by more than
    # ``ExtractionMixin._LEDGER_KG_TOL = 1e-9 kg``. The audit iterates
    # the UNION of species across both surfaces (0.5.4
    # milestone-review P2 fix, codex /challenge 2026-05-28) so a
    # projection-only stale state (UI carries phantom kg with no
    # ledger backing) surfaces with negative drift. Diagnostic only —
    # the global ≤5e-12 % closure on ``mass_balance_error_pct`` remains
    # the hard gate. Empty dict means all metal species are in sync
    # across BOTH surfaces. Drift typically arises in transit-of-
    # flight ticks where a recipe has credited metal to the ledger
    # but the projection sweep hasn't run yet; the steady-state
    # expectation is that values converge to zero within ~1-2 ticks
    # for a stable campaign. M2 historical-audit closure (2026-05-28).


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
    cost_rollup: Dict[str, Any] = field(default_factory=dict)

    # Status
    completed: bool = False
    total_hours: int = 0
