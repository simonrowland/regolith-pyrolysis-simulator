"""
Energy Tracker
===============

Tracks electrical energy consumption per campaign stage and cumulative per
batch.  It also carries a diagnostic evaporation-enthalpy sink estimate
(latent + oxide-dissociation enthalpy of evaporated species).  That diagnostic
is partial furnace heat accounting: feed sensible heat, fusion, radiation, and
the full furnace heat path remain outside this tracker.

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
from simulator.thermal_budget import evaporation_enthalpy_budget


class EnergyTracker:
    """
    Calculates hourly electrical energy consumption.

    Full solar/furnace heat input is not tracked here.  The non-electrical
    term is only the known evaporation-enthalpy sink estimate.
    """

    def __init__(self):
        self.cumulative_kWh = 0.0
        self.electrical_cumulative_kWh = 0.0
        self.evaporation_thermal_cumulative_kWh = 0.0
        self.latent_cumulative_kWh = 0.0
        self.dissociation_cumulative_kWh = 0.0
        self.electrical_plus_evaporation_cumulative_kWh = 0.0
        self.by_campaign: dict = {}
        self.by_campaign_breakdown: dict = {}

    def reset(self) -> None:
        self.__init__()

    def calculate_hour(self, melt: MeltState,
                       overhead: OverheadGas,
                       evap_flux: EvaporationFlux,
                       mre_kWh: float = 0.0,
                       vapor_pressures: dict | None = None) -> EnergyRecord:
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

        # --- Evaporation-enthalpy diagnostic sinks ---
        # Ledger-neutral: reads the evaporation flux and cited enthalpy
        # coefficients, but does not debit/credit AtomLedger or mass state.
        thermal_budget = evaporation_enthalpy_budget(
            evap_flux.species_kg_hr,
            vapor_pressures=vapor_pressures,
        )
        record.evaporation_thermal_kWh = thermal_budget["evaporation_thermal_kWh"]
        record.energy_scope = thermal_budget["energy_scope"]
        record.furnace_heat_status = thermal_budget["furnace_heat_status"]
        record.latent_kWh = thermal_budget["latent_kWh"]
        record.dissociation_kWh = thermal_budget["dissociation_kWh"]
        record.evaporation_breakdown_kWh = dict(thermal_budget["heat_flows_kWh"])
        record.evaporation_sources = dict(thermal_budget["sources"])

        record.sum_scoped_energy()

        # Track cumulative
        self.cumulative_kWh += record.electrical_plus_evaporation_kWh
        self.electrical_cumulative_kWh += record.electrical_total_kWh
        self.evaporation_thermal_cumulative_kWh += record.evaporation_thermal_kWh
        self.latent_cumulative_kWh += record.latent_kWh
        self.dissociation_cumulative_kWh += record.dissociation_kWh
        self.electrical_plus_evaporation_cumulative_kWh += (
            record.electrical_plus_evaporation_kWh
        )
        campaign_key = melt.campaign.name
        self.by_campaign[campaign_key] = (
            self.by_campaign.get(campaign_key, 0.0)
            + record.electrical_plus_evaporation_kWh)
        campaign_breakdown = self.by_campaign_breakdown.setdefault(
            campaign_key,
            {
                "electrical": 0.0,
                "evaporation_thermal": 0.0,
                "latent": 0.0,
                "dissociation": 0.0,
                "electrical_plus_evaporation": 0.0,
            },
        )
        campaign_breakdown["electrical"] += record.electrical_total_kWh
        campaign_breakdown["evaporation_thermal"] += record.evaporation_thermal_kWh
        campaign_breakdown["latent"] += record.latent_kWh
        campaign_breakdown["dissociation"] += record.dissociation_kWh
        campaign_breakdown["electrical_plus_evaporation"] += (
            record.electrical_plus_evaporation_kWh
        )

        return record

    def cumulative_breakdown(self) -> dict:
        return {
            "electrical": self.electrical_cumulative_kWh,
            "evaporation_thermal": self.evaporation_thermal_cumulative_kWh,
            "latent": self.latent_cumulative_kWh,
            "dissociation": self.dissociation_cumulative_kWh,
            "electrical_plus_evaporation": self.cumulative_kWh,
        }
