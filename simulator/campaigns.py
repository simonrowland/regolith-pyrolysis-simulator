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

from typing import Dict, List, Optional, Tuple

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

    # ------------------------------------------------------------------
    # Campaign configuration
    # ------------------------------------------------------------------

    def configure_campaign(self, melt: MeltState, campaign: CampaignPhase):
        """
        Set melt atmosphere and process parameters for a campaign.

        Called when starting a new campaign phase.
        """
        if campaign == CampaignPhase.C0:
            melt.atmosphere = Atmosphere.HARD_VACUUM
            melt.pO2_mbar = 0.0
            melt.p_total_mbar = 0.0
            melt.fO2_log = -9.0

        elif campaign == CampaignPhase.C0B:
            melt.atmosphere = Atmosphere.CONTROLLED_O2_FLOW
            melt.pO2_mbar = 9.0  # midpoint of [3, 15]
            melt.p_total_mbar = 9.0

        elif campaign == CampaignPhase.C2A:
            melt.atmosphere = Atmosphere.PN2_SWEEP
            melt.pO2_mbar = 0.0   # Fe-granule sorbent keeps pO₂ → 0
            melt.p_total_mbar = 10.0  # midpoint of [5, 15] mbar N₂
            melt.fO2_log = -8.0

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
        ovr = self.overrides.get(campaign.name, {})
        if 'pO2_mbar' in ovr:
            melt.pO2_mbar = float(ovr['pO2_mbar'])
            melt.p_total_mbar = max(melt.p_total_mbar, melt.pO2_mbar)
        if 'stir_factor' in ovr:
            melt.stir_factor = float(ovr['stir_factor'])

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

        elif campaign == CampaignPhase.C2B:
            # Ramp 1320 → 1480°C (pO₂-controlled)
            return (1480.0, 10.0)

        elif campaign in (CampaignPhase.C3_K, CampaignPhase.C3_NA):
            # Alkali shuttle: inject at 1200-1350°C, bakeout at 1520-1680°C
            # Alternate between injection T and bakeout T
            cycle_period = 6  # hours per inject-bakeout cycle
            if campaign_hour % cycle_period < 3:
                return (1275.0, 50.0)  # injection phase
            else:
                return (1600.0, 50.0)  # bakeout phase

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
            # Mg thermite at 1500-1700°C
            return (1600.0, 10.0)

        elif campaign == CampaignPhase.MRE_BASELINE:
            # Standard MRE: heat to melting then hold
            return (1575.0, 20.0)

        return (None, 0.0)

    def _apply_ramp_override(self, campaign: CampaignPhase,
                              target_T: Optional[float],
                              ramp_rate: float) -> Tuple[Optional[float], float]:
        """Apply runtime ramp rate override if set."""
        ovr = self.overrides.get(campaign.name, {})
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
        ovr = self.overrides.get(campaign.name, {})
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

        elif campaign == CampaignPhase.C2B:
            # Fe pyrolysis — shorter than C2A
            fe_rate = evap_flux.species_kg_hr.get('Fe', 0.0)
            if melt.campaign_hour >= 8 and fe_rate < 0.05:
                return True
            if melt.campaign_hour >= 20:
                return True

        elif campaign == CampaignPhase.C3_K:
            # K shuttle: 0-1 cycles after Path A, 2 after Path B
            max_hours = 12 if record.path == 'A' else 25
            if melt.campaign_hour >= max_hours:
                return True

        elif campaign == CampaignPhase.C3_NA:
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
            return None  # Triggers PATH_AB decision

        elif current == CampaignPhase.C2A:
            # After Path A C2A → C3 (K phase)
            return CampaignPhase.C3_K

        elif current == CampaignPhase.C2B:
            # After Path B C2B → C3 (K phase)
            return CampaignPhase.C3_K

        elif current == CampaignPhase.C3_K:
            # K phase → Na phase
            return CampaignPhase.C3_NA

        elif current == CampaignPhase.C3_NA:
            # After C3 → Branch decision needed
            return None  # Triggers BRANCH_ONE_TWO decision

        elif current == CampaignPhase.C4:
            # After Mg pyrolysis → C5 limited MRE
            return CampaignPhase.C5

        elif current == CampaignPhase.C5:
            # After C5 → C6 decision (need Mg inventory)
            if record.branch == 'two':
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
                options=['A', 'B'],
                recommendation='A',
                context=(
                    'Path A: Continuous pN₂ ramp extracts Na/K/Fe/SiO₂ '
                    '(8-20% faster, halved C3, fused silica product). '
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
