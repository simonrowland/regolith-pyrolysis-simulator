"""
Energy Tracker
===============

Tracks electrical energy consumption per campaign stage and
cumulative per batch.  Solar-thermal energy (concentrator) is
assumed provided but not tracked in the electrical budget.

Electrical consumers:
    - Turbine/compressor:  O₂ compression from mbar → ~3 bar
    - Condenser cooling:   Active cooling at lower-T stages
    - MRE electrolysis:    C5 / MRE baseline

Reference values (per tonne regolith, Branch Two):
    Turbine compression:    15-30 kWh
    MRE (≤1.6 V):          600-1200 kWh
    Total electrical:       1200-2000 kWh

Branch One (full MRE to 2.5 V): 2650-4050 kWh total.
"""

from __future__ import annotations

from simulator.core import (
    EnergyRecord, EvaporationFlux, MeltState, OverheadGas,
)


class EnergyTracker:
    """
    Calculates hourly electrical energy consumption.

    Solar-thermal energy for the concentrator is not tracked
    here — the concentrator is assumed to maintain whatever
    temperature the campaign requires.
    """

    def __init__(self):
        self.cumulative_kWh = 0.0
        self.by_campaign: dict = {}

    def calculate_hour(self, melt: MeltState,
                       overhead: OverheadGas,
                       evap_flux: EvaporationFlux,
                       mre_kWh: float = 0.0) -> EnergyRecord:
        """
        Calculate electrical energy consumed this hour.

        Args:
            melt:       Current melt state
            overhead:   Overhead gas state (for turbine calc)
            evap_flux:  Evaporation flux (for condenser calc)
            mre_kWh:    MRE energy consumed this hour (from electrolysis model)

        Returns:
            EnergyRecord for this hour
        """
        record = EnergyRecord()

        # --- Turbine compression ---
        # Uses the actual shaft power computed by the overhead model,
        # which already accounts for turbine capacity capping (Loop 2).
        # Vented O₂ doesn't consume compression energy.
        # Fallback to estimate if shaft power not set.
        if overhead.turbine_shaft_power_kW > 0:
            record.turbine_kWh = overhead.turbine_shaft_power_kW  # kW × 1 hr = kWh
        else:
            # Legacy fallback: ~20 kWh per tonne O₂
            O2_kg_hr = overhead.turbine_flow_kg_hr * 0.3
            record.turbine_kWh = O2_kg_hr * 0.02

        # --- Condenser cooling ---
        # Radiation cooling is free (lunar vacuum).
        # Active cooling needed only at lower-T stages where
        # radiation is insufficient.  Small contribution.
        condenser_heat_W = evap_flux.total_kg_hr * 50.0  # ~50 W per kg/hr
        record.condenser_kWh = condenser_heat_W / 1000.0  # W → kWh (1 hr)

        # --- MRE ---
        record.mre_kWh = mre_kWh

        record.sum_total()

        # Track cumulative
        self.cumulative_kWh += record.total_kWh
        campaign_key = melt.campaign.name
        self.by_campaign[campaign_key] = (
            self.by_campaign.get(campaign_key, 0.0) + record.total_kWh)

        return record
