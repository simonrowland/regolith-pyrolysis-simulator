import pytest

from simulator.core import (
    CampaignPhase,
    EnergyRecord,
    EvaporationFlux,
    HourSnapshot,
    MeltState,
    OverheadGas,
)
from simulator.energy import EnergyTracker


def _na_vapor_pressures():
    return {
        "metals": {
            "Na": {
                "parent_oxide": "Na2O",
                "molar_mass_g_mol": 22.98976928,
            }
        }
    }


def test_energy_tracker_adds_thermal_diagnostics_without_changing_mre_kwh():
    tracker = EnergyTracker()
    melt = MeltState(campaign=CampaignPhase.C2A)
    overhead = OverheadGas(turbine_shaft_power_kW=2.0)
    evap_flux = EvaporationFlux(species_kg_hr={"Na": 1.0})
    evap_flux.update_totals()

    record = tracker.calculate_hour(
        melt,
        overhead,
        evap_flux,
        mre_kWh=11.0,
        vapor_pressures=_na_vapor_pressures(),
    )

    assert record.mre_kWh == 11.0
    assert record.electrical_total_kWh == pytest.approx(
        record.turbine_kWh + record.condenser_kWh + record.mre_kWh
    )
    assert record.latent_kWh > 0.0
    assert record.dissociation_kWh > 0.0
    assert record.solar_thermal_kWh == pytest.approx(
        record.latent_kWh + record.dissociation_kWh
    )
    assert record.total_kWh == pytest.approx(
        record.electrical_total_kWh + record.solar_thermal_kWh
    )
    assert tracker.cumulative_breakdown()["latent"] == pytest.approx(
        record.latent_kWh
    )


def test_hour_snapshot_exposes_decomposed_energy_total():
    energy = EnergyRecord(
        turbine_kWh=1.0,
        condenser_kWh=0.5,
        mre_kWh=3.0,
        latent_kWh=2.0,
        dissociation_kWh=4.0,
    )
    energy.sum_total()
    energy.thermal_breakdown_kWh = {
        "heat_in": energy.solar_thermal_kWh,
        "product_vapor_enthalpy_sink": energy.latent_kWh,
        "reaction_disproportionation_enthalpy_sink": energy.dissociation_kWh,
    }
    snapshot = HourSnapshot(
        hour=1,
        campaign=CampaignPhase.C2A,
        energy=energy,
        energy_cumulative_kWh=energy.total_kWh,
        energy_cumulative_breakdown_kWh={
            "electrical": energy.electrical_total_kWh,
            "solar_thermal": energy.solar_thermal_kWh,
            "latent": energy.latent_kWh,
            "dissociation": energy.dissociation_kWh,
            "thermal_total": energy.thermal_total_kWh,
            "total": energy.total_kWh,
        },
    )

    assert snapshot.energy.total_kWh == pytest.approx(10.5)
    assert snapshot.energy.electrical_total_kWh == pytest.approx(4.5)
    assert snapshot.energy.solar_thermal_kWh == pytest.approx(6.0)
    assert snapshot.energy.latent_kWh == pytest.approx(2.0)
    assert snapshot.energy.dissociation_kWh == pytest.approx(4.0)
    assert snapshot.energy_cumulative_breakdown_kWh["total"] == pytest.approx(10.5)
