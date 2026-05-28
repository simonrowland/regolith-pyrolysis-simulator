"""
Campaign Manager — Sequencing, Ramp Rates & Endpoint Detection
===============================================================

Manages the progression of campaigns (C0 → C0b → C2A/C2B → C3 → C4 → C5 → C6),
temperature ramp profiles, atmosphere configuration, and endpoint detection
for each campaign phase.

Each campaign has:
    - Temperature target(s) and ramp rate (°C/hr)
    - Atmosphere settings (vacuum, pO₂, pN₂)
    - Endpoint criteria (IR signal decay, current decay, self-termination)
    - Next-campaign logic (may require operator decision)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Dict, List, Optional, Tuple

from simulator.state import StirState, clamp_stir_factor, clamp_stir_state
from simulator.core import (
    Atmosphere, BatchRecord, CampaignPhase, CondensationTrain,
    DecisionPoint, DecisionType, EvaporationFlux, MeltState,
)


class CampaignManager:
    """
    Manages campaign sequencing and endpoint detection.

    Reads campaign parameters from setpoints.yaml and controls
    the transition between campaigns, including prompting for
    operator decisions (Path A/B, Branch One/Two).
    """

    def __init__(self, setpoints: dict):
        self.setpoints = setpoints
        self.campaigns = setpoints.get('campaigns', {})
        # User-configurable overrides
        self.c4_max_temp_C = 1670.0  # Max T for C4 Mg pyrolysis (default)

        # Runtime overrides from UI (keyed by campaign name)
        # Structure: {'C2A': {'ramp_rate': 10.0, 'pO2_mbar': 1.0,
        #                     'stir_factor': 8.0, 'max_hours': 25}}
        self.overrides: Dict[str, dict] = {}

    _CONFIG_KEY_BY_PHASE = {
        CampaignPhase.C2A: 'C2A_continuous',
        CampaignPhase.C2A_STAGED: 'C2A_staged',
    }

    @staticmethod
    def _is_noninteractive_test_batch(record: BatchRecord) -> bool:
        return str(getattr(record, 'feedstock_key', '')).startswith('debug_')

    @staticmethod
    def _record_auto_decision(record: BatchRecord,
                              decision_type: DecisionType,
                              choice: str) -> None:
        decision = (decision_type, choice)
        if decision not in record.decisions:
            record.decisions.append(decision)

    def _campaign_config_key(self, campaign: CampaignPhase) -> str:
        return self._CONFIG_KEY_BY_PHASE.get(campaign, campaign.name)

    def _campaign_config(self, campaign: CampaignPhase) -> dict:
        cfg = self.campaigns.get(self._campaign_config_key(campaign), {})
        return cfg if isinstance(cfg, dict) else {}

    def _campaign_overrides(self, campaign: CampaignPhase) -> dict:
        merged: dict = {}
        for key in (self._campaign_config_key(campaign), campaign.name):
            ovr = self.overrides.get(key, {})
            if isinstance(ovr, dict):
                merged.update(ovr)
        return merged

    @staticmethod
    def _float(value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # Campaign configuration
    # ------------------------------------------------------------------

    def configure_campaign(self, melt: MeltState, campaign: CampaignPhase):
        """
        Set gas-side atmosphere and process parameters for a campaign.
        ``melt.fO2_log`` is engine-computed from melt composition per tick.

        Called when starting a new campaign phase.
        """
        if campaign == CampaignPhase.C0:
            ambient_pressure = max(
                0.0, float(getattr(melt, 'ambient_pressure_mbar', 0.0) or 0.0))
            if ambient_pressure > 0:
                melt.atmosphere = Atmosphere.CO2_BACKPRESSURE
                melt.p_total_mbar = ambient_pressure
            else:
                melt.atmosphere = Atmosphere.HARD_VACUUM
                melt.p_total_mbar = 0.0
            melt.pO2_mbar = 0.0

        elif campaign == CampaignPhase.C0B:
            melt.atmosphere = Atmosphere.CONTROLLED_O2_FLOW
            melt.pO2_mbar = 9.0  # midpoint of [3, 15]
            melt.p_total_mbar = 9.0

        elif campaign in (CampaignPhase.C2A, CampaignPhase.C2A_STAGED):
            melt.atmosphere = Atmosphere.PN2_SWEEP
            melt.pO2_mbar = 0.0   # Fe-granule sorbent keeps pO₂ → 0
            melt.p_total_mbar = 10.0  # midpoint of [5, 15] mbar N₂

        elif campaign == CampaignPhase.C2B:
            melt.atmosphere = Atmosphere.CONTROLLED_O2
            melt.pO2_mbar = 1.5   # midpoint of [0.8, 2.3]
            melt.p_total_mbar = 1.5

        elif campaign in (CampaignPhase.C3_K, CampaignPhase.C3_NA):
            melt.atmosphere = Atmosphere.CONTROLLED_O2
            melt.pO2_mbar = 1.0   # midpoint of [0.5, 1.5]
            melt.p_total_mbar = 1.0

        elif campaign == CampaignPhase.C4:
            melt.atmosphere = Atmosphere.CONTROLLED_O2
            melt.pO2_mbar = 0.2   # midpoint of [0.08, 0.35]
            melt.p_total_mbar = 0.2

        elif campaign == CampaignPhase.C5:
            melt.atmosphere = Atmosphere.O2_BACKPRESSURE
            melt.pO2_mbar = 50.0  # 0.05 bar midpoint of [0.01, 0.1]
            melt.p_total_mbar = 50.0

        elif campaign == CampaignPhase.C6:
            melt.atmosphere = Atmosphere.CONTROLLED_O2
            melt.pO2_mbar = 0.2
            melt.p_total_mbar = 0.2

        elif campaign == CampaignPhase.MRE_BASELINE:
            melt.atmosphere = Atmosphere.O2_BACKPRESSURE
            melt.pO2_mbar = 50.0
            melt.p_total_mbar = 50.0

        # Apply runtime overrides (pO₂, stir_factor)
        ovr = self._campaign_overrides(campaign)
        if 'pO2_mbar' in ovr:
            # 0.5.4 W5 milestone-review P1 (codex /challenge
            # 2026-05-28): mirror the active-path atmosphere switch
            # at ``simulator/session.py:276-298`` here too. Pre-fix
            # an operator who set
            # ``session.adjust("campaign_override",
            # campaign="C2A", field="pO2_mbar", value=1.0)`` while
            # C0 was active stored the override correctly (active
            # campaign C0 unaffected), but when C2A later became
            # active via ``configure_campaign()`` the override was
            # applied as a bare ``melt.pO2_mbar`` write — without
            # switching ``melt.atmosphere`` away from the C2A
            # default ``PN2_SWEEP``. Result: commanded-pO2 floor
            # stays disabled because PN2_SWEEP isn't in
            # ``_O2_CONTROLLED_ATMOSPHERES``. Now: a positive
            # override pO2 forces atmosphere to CONTROLLED_O2 at
            # transition time too, restoring the SiO suppression
            # lever's transition-time consistency with the
            # active-path fix.  ``pO2_mbar == 0`` leaves atmosphere
            # alone (operator clearing the setpoint, NOT requesting
            # controlled-O2).
            override_pO2 = float(ovr['pO2_mbar'])
            melt.pO2_mbar = override_pO2
            melt.p_total_mbar = max(melt.p_total_mbar, melt.pO2_mbar)
            if override_pO2 > 0.0:
                melt.atmosphere = Atmosphere.CONTROLLED_O2
        # 0.5.3 Phase B chunk-review P2 (codex 2026-05-28): per-axis
        # merge precedence. Before this fix, when an operator passed
        # BOTH ``{stir_factor: 6, stir_state: {radial: 8}}``, the whole-
        # ``stir_state`` write erased the explicit axial=6 (because
        # ``clamp_stir_state({radial: 8})`` defaults the missing axial
        # to 1.0 laminar). That's not what the operator meant — they
        # asked for axial=6 (from stir_factor) AND radial=8 (from
        # stir_state.radial). Resolve per-axis instead of whole-dict:
        #
        #   1. ``stir_factor`` (if present) sets the axial axis.
        #   2. ``stir_state`` (if present) sets the radial axis, AND
        #      overrides axial ONLY if it explicitly carries an axial
        #      key. Otherwise axial keeps the stir_factor value from
        #      step 1 (or the prior melt.stir_state.axial if neither
        #      override is supplied).
        has_stir_factor = 'stir_factor' in ovr
        has_stir_state = 'stir_state' in ovr
        if has_stir_factor:
            # 0.5.2 Phase B P1: route through ``clamp_stir_factor`` so
            # campaign YAML overrides honour ``MAX_STIR_FACTOR``.
            # 0.5.3 Phase B: ``stir_factor`` field touches AXIAL only
            # (via the backward-compat property setter on MeltState).
            melt.stir_factor = clamp_stir_factor(ovr['stir_factor'])
        if has_stir_state:
            raw_state = ovr['stir_state']
            new_state = clamp_stir_state(raw_state)
            # Per-axis merge: when both override fields are present and
            # ``stir_state`` does NOT explicitly carry axial, preserve
            # the ``stir_factor``-set axial. The dict-shape check is
            # the only reliable "did the operator mention axial?"
            # signal we have — a StirState or scalar input is
            # whole-dataclass-replaces by design.
            if (has_stir_factor
                    and isinstance(raw_state, Mapping)
                    and 'axial' not in raw_state):
                # Keep the axial value just set by stir_factor; replace
                # only radial from stir_state. Construct a fresh
                # StirState to honour the dataclass invariants.
                melt.stir_state = StirState(
                    axial=melt.stir_state.axial,
                    radial=new_state.radial,
                )
            else:
                # Either no concurrent stir_factor, or stir_state
                # explicitly named axial — whole-dataclass replace.
                melt.stir_state = new_state

    # ------------------------------------------------------------------
    # Temperature ramp
    # ------------------------------------------------------------------

    def get_temp_target(self, campaign: CampaignPhase,
                        campaign_hour: int,
                        melt: MeltState) -> Tuple[Optional[float], float]:
        """
        Get the target temperature and ramp rate for a campaign.

        Returns:
            (target_T_C, ramp_rate_C_per_hr)
            target_T is None for isothermal holds or MRE campaigns.
        """
        result = self._get_base_temp_target(campaign, campaign_hour, melt)
        return self._apply_ramp_override(campaign, result[0], result[1])

    def _get_base_temp_target(self, campaign: CampaignPhase,
                               campaign_hour: int,
                               melt: MeltState) -> Tuple[Optional[float], float]:
        """Base temperature targets before runtime overrides."""
        if campaign == CampaignPhase.C0:
            # Ramp from current T to 950°C at 50°C/hr
            return (950.0, 50.0)

        elif campaign == CampaignPhase.C0B:
            # Isothermal hold at midpoint of [1180, 1320]
            return (1250.0, 30.0)

        elif campaign == CampaignPhase.C2A:
            # Continuous ramp 1050 → 1600°C
            # Ramp rate varies: 15°C/hr early, 7.5°C/hr at peak SiO window
            if melt.temperature_C < 1320:
                return (1600.0, 15.0)  # early ramp
            else:
                return (1600.0, 7.5)   # peak SiO window — slower

        elif campaign == CampaignPhase.C2A_STAGED:
            cfg = self._campaign_config(campaign)
            stages = cfg.get('stages', [])
            if not isinstance(stages, list) or not stages:
                return (1750.0, 150.0)
            hour = max(0, int(campaign_hour))
            elapsed = 0
            selected = stages[-1]
            for stage in stages:
                if not isinstance(stage, dict):
                    continue
                duration = max(1, int(self._float(stage.get('duration_h'), 1.0)))
                if hour < elapsed + duration:
                    selected = stage
                    break
                elapsed += duration

            target = self._float(selected.get('target_C'), 1750.0)
            if selected.get('name') == 'fe_hot_hold':
                ovr = self._campaign_overrides(campaign)
                target = self._float(
                    ovr.get('hold_temp_C'),
                    self._float(cfg.get('default_hold_T_C'), target),
                )
                ceiling = self._float(cfg.get('furnace_ceiling_C'), 1800.0)
                target = min(target, ceiling)
            ramp = self._float(selected.get('ramp_rate_C_per_hr'), 150.0)
            return (target, ramp)

        elif campaign == CampaignPhase.C2B:
            # Ramp 1320 → 1480°C (pO₂-controlled)
            return (1480.0, 10.0)

        elif campaign in (CampaignPhase.C3_K, CampaignPhase.C3_NA):
            # Legacy C3 alternates injection/bakeout. V1c staged Na cleanup
            # overrides both targets to the cool FeO window near 1150 C.
            # Alternate between injection T and bakeout T
            ovr = self._campaign_overrides(campaign)
            inject_target = self._float(ovr.get('inject_target_C'), 1275.0)
            bakeout_target = self._float(ovr.get('bakeout_target_C'), 1600.0)
            ramp_rate = self._float(ovr.get('ramp_rate'), 50.0)
            cycle_period = 6  # hours per inject-bakeout cycle
            if campaign_hour % cycle_period < 3:
                return (inject_target, ramp_rate)  # injection phase
            else:
                return (bakeout_target, ramp_rate)  # bakeout phase

        elif campaign == CampaignPhase.C4:
            # Mg pyrolysis at 1580 up to user-configurable max T
            # Higher T → more Mg extraction but risk of freezing
            # refractory-enriched melt (liquidus rises as composition
            # becomes more aluminous/calcic after Fe/Ti/SiO₂ removal)
            target = min(self.c4_max_temp_C, 1900.0)  # safety cap
            return (target, 10.0)

        elif campaign == CampaignPhase.C5:
            # MRE: hold at process temperature
            return (1575.0, 5.0)

        elif campaign == CampaignPhase.C6:
            # Mg/Al crossover is ~1573 C under V1c JANAF constants.
            return (1500.0, 10.0)

        elif campaign == CampaignPhase.MRE_BASELINE:
            # Standard MRE: heat to melting then hold
            return (1575.0, 20.0)

        return (None, 0.0)

    def _apply_ramp_override(self, campaign: CampaignPhase,
                              target_T: Optional[float],
                              ramp_rate: float) -> Tuple[Optional[float], float]:
        """Apply runtime ramp rate override if set."""
        ovr = self._campaign_overrides(campaign)
        if 'ramp_rate' in ovr:
            ramp_rate = float(ovr['ramp_rate'])
        return (target_T, ramp_rate)

    # ------------------------------------------------------------------
    # Endpoint detection
    # ------------------------------------------------------------------

    def check_endpoint(self, melt: MeltState,
                       evap_flux: EvaporationFlux,
                       train: CondensationTrain,
                       record: BatchRecord) -> bool:
        """
        Check if the current campaign has reached its endpoint.

        Endpoints are defined by:
        - C0:   IR signal decay < 5% of peak (volatile emission stops)
        - C0b:  P-species IR decay < 5% of peak
        - C2A:  Na/K/Fe/SiO all decay to < 5% peak
        - C2B:  Fe signal decays to < 5% peak
        - C3:   pO₂ returns to setpoint, holds 30 min
        - C4:   Mg signal decays to background
        - C5:   Current decays to < 10 A at target voltage
        - C6:   Self-terminating (liquidus > 1700°C)

        For the simulator, we approximate these with simpler checks
        based on evaporation rate thresholds and duration limits.

        Returns True if the campaign should end.
        """
        campaign = melt.campaign

        # Check user-specified max_hours override first
        ovr = self._campaign_overrides(campaign)
        if 'max_hours' in ovr:
            max_h = float(ovr['max_hours'])
            if max_h > 0 and melt.campaign_hour >= max_h:
                return True

        if campaign == CampaignPhase.C0:
            # End when T reaches 950°C and evaporation rate is low
            if melt.temperature_C >= 940 and melt.campaign_hour >= 10:
                return True
            # Also end if we've been running too long
            if melt.campaign_hour >= 25:
                return True

        elif campaign == CampaignPhase.C0B:
            # IR-endpoint: P cleanup typically 0.5-2.5 hours
            if melt.campaign_hour >= 3 and melt.temperature_C >= 1200:
                return True

        elif campaign == CampaignPhase.C2A:
            # Long campaign: 18-28 hours
            # End when evaporation rate drops below threshold
            total_rate = evap_flux.total_kg_hr
            if melt.campaign_hour >= 18 and total_rate < 0.1:
                return True
            if melt.campaign_hour >= 30:
                return True

        elif campaign == CampaignPhase.C2A_STAGED:
            cfg = self._campaign_config(campaign)
            stages = cfg.get('stages', [])
            total_hours = 0
            if isinstance(stages, list):
                for stage in stages:
                    if isinstance(stage, dict):
                        total_hours += max(
                            1, int(self._float(stage.get('duration_h'), 1.0)))
            total_hours = total_hours or 9
            if melt.campaign_hour + 1 >= total_hours:
                return True

        elif campaign == CampaignPhase.C2B:
            # Fe pyrolysis — shorter than C2A
            fe_rate = evap_flux.species_kg_hr.get('Fe', 0.0)
            if melt.campaign_hour >= 8 and fe_rate < 0.05:
                return True
            if melt.campaign_hour >= 20:
                return True

        elif campaign == CampaignPhase.C3_K:
            if record.path == 'A_staged':
                staged_hours = int(self._float(
                    self._campaign_overrides(campaign).get('staged_duration_h'),
                    3.0,
                ))
                if melt.campaign_hour >= max(1, staged_hours):
                    return True
            # K shuttle: 0-1 cycles after Path A, 2 after Path B
            max_hours = 12 if record.path == 'A' else 25
            if melt.campaign_hour >= max_hours:
                return True

        elif campaign == CampaignPhase.C3_NA:
            # Autoreview r6 P2 (2026-05-27): the V1c-recipe-retune
            # migration retargeted ``C2A_STAGED -> C3_NA`` (was
            # ``C2A_STAGED -> C3_K`` pre-V1c) and the
            # ``na_shuttle_stage`` override now sets
            # ``staged_duration_h`` as the cool cleanup endpoint.  The
            # C3_K branch above honors the override via the
            # ``record.path == 'A_staged'`` check; this branch did not,
            # so staged runs (``record.path == 'A_staged'``) fell into
            # the ``else`` arm of the ternary and ran C3_NA for the
            # default 35 hours instead of the intended ~3-hour cool
            # cleanup.  Mirror the C3_K handling so the staged endpoint
            # is honored at its configured value.
            if record.path == 'A_staged':
                staged_hours = int(self._float(
                    self._campaign_overrides(campaign).get('staged_duration_h'),
                    3.0,
                ))
                if melt.campaign_hour >= max(1, staged_hours):
                    return True
            # Na shuttle: 1 cycle after Path A, 2 after Path B
            max_hours = 18 if record.path == 'A' else 35
            if melt.campaign_hour >= max_hours:
                return True

        elif campaign == CampaignPhase.C4:
            # Mg pyrolysis — IR-controlled
            mg_rate = evap_flux.species_kg_hr.get('Mg', 0.0)
            if melt.campaign_hour >= 6 and mg_rate < 0.02:
                return True
            if melt.campaign_hour >= 20:
                return True

        elif campaign == CampaignPhase.C5:
            # MRE: current decay
            # Simplified: end after estimated duration
            if record.branch == 'two':
                if melt.campaign_hour >= 15:
                    return True
            else:
                if melt.campaign_hour >= 30:
                    return True

        elif campaign == CampaignPhase.C6:
            # Self-terminating when residual SiO₂ + Al₂O₃ < 15-20 wt%
            comp = melt.composition_wt_pct()
            refractory_pct = comp.get('SiO2', 0.0) + comp.get('Al2O3', 0.0)
            if refractory_pct < 17.5:
                return True
            if melt.campaign_hour >= 20:
                return True

        elif campaign == CampaignPhase.MRE_BASELINE:
            # Current-decay endpoint: when effective current drops below
            # 10 A for 3 consecutive hours at max voltage, the melt is
            # exhausted and electrolysis should stop.
            if melt.mre_voltage_V >= 2.45 and melt.mre_current_A < 10.0:
                melt.mre_low_current_hours += 1
            else:
                melt.mre_low_current_hours = 0
            if melt.mre_low_current_hours >= 3:
                return True
            # Safety cutoff
            if melt.campaign_hour >= 120:
                return True

        return False

    # ------------------------------------------------------------------
    # Campaign transitions
    # ------------------------------------------------------------------

    def get_next_campaign(self, current: CampaignPhase,
                          record: BatchRecord) -> Optional[CampaignPhase]:
        """
        Determine the next campaign after the current one ends.

        Returns:
            CampaignPhase for the next campaign,
            CampaignPhase.COMPLETE if the batch is done,
            or None if a decision is needed first.
        """
        # --- MRE-only track: skip pyrolysis campaigns entirely ---
        # After C0 (degas), go straight to MRE_BASELINE. No C0b, no decisions.
        if record.track == 'mre_baseline':
            if current in (CampaignPhase.C0, CampaignPhase.C0B):
                return CampaignPhase.MRE_BASELINE
            elif current == CampaignPhase.MRE_BASELINE:
                return CampaignPhase.COMPLETE
            else:
                return CampaignPhase.COMPLETE

        # --- Pyrolysis track ---
        if current == CampaignPhase.C0:
            # Check if P-cleanup is needed
            # For simplicity, always do C0b for lunar feedstocks
            return CampaignPhase.C0B

        elif current == CampaignPhase.C0B:
            # Seal volatiles train gate valve
            # Decision needed: Path A or B
            if self._is_noninteractive_test_batch(record):
                record.path = 'A'
                self._record_auto_decision(record, DecisionType.PATH_AB, 'A')
                return CampaignPhase.C2A
            return None  # Triggers PATH_AB decision

        elif current == CampaignPhase.C2A:
            # After Path A C2A → C3 (K phase)
            return CampaignPhase.C3_K

        elif current == CampaignPhase.C2A_STAGED:
            # Staged Path A cools before handing residual FeO to the V1c
            # Na-only cleanup window. K/FeO is refused at this temperature.
            cfg = self._campaign_config(CampaignPhase.C2A_STAGED)
            na_stage = cfg.get('na_shuttle_stage', {})
            if not isinstance(na_stage, dict):
                na_stage = cfg.get('k_shuttle_stage', {})
            if isinstance(na_stage, dict):
                c3 = self.overrides.setdefault('C3_NA', {})
                target = self._float(na_stage.get('target_C'), 1150.0)
                c3.setdefault('inject_target_C', target)
                c3.setdefault('bakeout_target_C', target)
                c3.setdefault(
                    'ramp_rate',
                    self._float(na_stage.get('ramp_rate_C_per_hr'), 600.0),
                )
                c3.setdefault(
                    'staged_duration_h',
                    self._float(na_stage.get('duration_h'), 3.0),
                )
            record.path = 'A_staged'
            return CampaignPhase.C3_NA

        elif current == CampaignPhase.C2B:
            # After Path B C2B → C3 (K phase)
            return CampaignPhase.C3_K

        elif current == CampaignPhase.C3_K:
            if record.path == 'A_staged':
                return CampaignPhase.COMPLETE
            # K phase → Na phase
            return CampaignPhase.C3_NA

        elif current == CampaignPhase.C3_NA:
            # After C3 → Branch decision needed
            if self._is_noninteractive_test_batch(record):
                record.branch = 'two'
                self._record_auto_decision(
                    record, DecisionType.BRANCH_ONE_TWO, 'two')
                return CampaignPhase.C4
            return None  # Triggers BRANCH_ONE_TWO decision

        elif current == CampaignPhase.C4:
            # After Mg pyrolysis → C5 limited MRE
            return CampaignPhase.C5

        elif current == CampaignPhase.C5:
            # After C5 → C6 decision (need Mg inventory)
            if record.branch == 'two':
                if self._is_noninteractive_test_batch(record):
                    self._record_auto_decision(
                        record, DecisionType.C6_PROCEED, 'yes')
                    return CampaignPhase.C6
                return None  # Triggers C6_PROCEED decision
            else:
                return CampaignPhase.COMPLETE

        elif current == CampaignPhase.C6:
            return CampaignPhase.COMPLETE

        elif current == CampaignPhase.MRE_BASELINE:
            return CampaignPhase.COMPLETE

        return CampaignPhase.COMPLETE

    def get_decision(self, current: CampaignPhase,
                     record: BatchRecord) -> DecisionPoint:
        """
        Build a DecisionPoint for the operator when a decision is needed.
        """
        if current == CampaignPhase.C0B:
            return DecisionPoint(
                decision_type=DecisionType.PATH_AB,
                options=['A', 'A_staged', 'B'],
                recommendation='A',
                context=(
                    'Path A: Continuous pN₂ ramp extracts Na/K/Fe/SiO₂. '
                    'Path A_staged: staged pN₂ ramp separates alkali, '
                    'SiO, hot Fe hold, then cool Na cleanup. '
                    'Path B: pO₂-managed Fe-only pyrolysis preserving '
                    'CMAS glass for tapping as Material 1.'
                ),
            )

        elif current == CampaignPhase.C3_NA:
            return DecisionPoint(
                decision_type=DecisionType.BRANCH_ONE_TWO,
                options=['two', 'one'],
                recommendation='two',
                context=(
                    'Branch Two (preferred): C4 Mg pyrolysis + C6 Mg thermite. '
                    'MRE ≤1.6 V, ~1200-2000 kWh/t, electrode life 5-10×. '
                    'Branch One (fallback): skip C4, full MRE to 2.5 V, '
                    '~2650-4050 kWh/t, electrode life 2-3×.'
                ),
            )

        elif current == CampaignPhase.C5:
            return DecisionPoint(
                decision_type=DecisionType.C6_PROCEED,
                options=['yes', 'no'],
                recommendation='yes',
                context=(
                    'Proceed with C6 Mg thermite reduction? '
                    'Requires ~50-60 kg Mg from inventory. '
                    'Produces Al (+ Ti alloy if TiO₂ retained).'
                ),
            )

        # Fallback
        return DecisionPoint(
            decision_type=DecisionType.ROOT_BRANCH,
            options=['pyrolysis', 'mre_baseline'],
            recommendation='pyrolysis',
            context='Select processing track.',
        )
