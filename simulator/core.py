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
    Mass            kg (composition_kg dict), g (trace species)
    Pressure        mbar (overhead), bar (O₂ accumulator)
    Time            hours (simulation timestep = 1 h)
    Energy          kWh (electrical only; solar-thermal assumed)
    Evaporation     kg/hr (Hertz-Knudsen flux × surface area)
    Vapor pressure  Pa (Antoine → converted as needed)
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

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
        self.melt.ambient_pressure_mbar = max(
            0.0, float(fs.get('surface_pressure_mbar') or 0.0))
        self.melt.ambient_atmosphere = str(fs.get('atmosphere', '') or '')
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
