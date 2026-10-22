from copy import deepcopy
from pathlib import Path

import yaml

from simulator.core import Atmosphere, CampaignPhase, PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend


C0_SETPOINTS = {
    "temp_range_C": [20, 950],
    "dT_dt_C_per_hr": 200,
    "max_hold_hr": 25,
    "carrier_gas": "Ar",
    "p_total_mbar_default": 1.0,
    "pO2_mbar_default": 0.0,
    "alkali_retention_budget_fraction": 1.0e-3,
}


def _sim(feedstocks):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    # Stepping crosses the strict VR-11 vapour boundary even though these tests
    # assert only Stage-0 atmosphere state. Use the canonical schema-v2 catalog
    # instead of the retired empty schema-v1 compatibility shape.
    vapor_pressures = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "vapor_pressures.yaml").read_text()
    )
    return PyrolysisSimulator(
        backend,
        {"campaigns": {"C0": deepcopy(C0_SETPOINTS)}},
        feedstocks,
        vapor_pressures,
    )


def test_stage0_lunar_feedstock_uses_configured_argon_cover():
    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )

    sim.load_batch("lunar")
    sim.start_campaign(CampaignPhase.C0)

    assert sim.melt.atmosphere is Atmosphere.ARGON_FLOW
    assert sim.melt.background_gas_species == "Ar"
    assert sim.melt.p_total_mbar == 1.0
    assert sim.melt.pO2_mbar == 0.0


def test_c0_start_predicts_exit_from_live_flux_and_caps_the_ramp():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    sim = _sim(feedstocks)
    sim.load_batch("lunar_mare_low_ti", 1000.0)
    sim._get_turbine_spec()
    sim.campaign_mgr.last_c0_exit = {"backstop": "maximum_hold"}
    sim.start_campaign(CampaignPhase.C0)

    prediction = sim.campaign_mgr.c0_exit_prediction
    assert sim.campaign_mgr.last_c0_exit is None
    assert prediction["prediction_status"] == "ok"
    assert prediction["T_exit_C"] == 950.0
    assert prediction["t_evac_hr"] == 0.0
    assert prediction["alkali_retention_budget_fraction"] == 1.0e-3
    assert prediction["Na_plus_K_feed_loss_fraction"] <= 1.0e-3
    target_C, _ramp = sim.campaign_mgr.get_temp_target(
        CampaignPhase.C0, 0, sim.melt
    )
    assert target_C == prediction["T_exit_C"]


def test_c0_comet_missing_alkali_inventory_does_not_predict_zero_loss():
    feedstocks = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "feedstocks.yaml").read_text()
    )
    sim = _sim(feedstocks)
    sim.load_batch("comet_nucleus", 1000.0)
    sim._get_turbine_spec()
    sim.start_campaign(CampaignPhase.C0)

    prediction = sim.campaign_mgr.c0_exit_prediction
    assert prediction["prediction_status"] == (
        "unavailable_no_initial_NaK_inventory"
    )
    assert prediction["T_exit_C"] is None
    assert prediction["M_C0_gas_kg"] > 0.0


def test_stage0_mars_feedstock_uses_configured_argon_cover():
    sim = _sim(
        {
            "mars": {
                "label": "Mars",
                "composition_wt_pct": {"SiO2": 100.0},
                "surface_pressure_mbar": 6,
                "atmosphere": "96% CO2",
            }
        }
    )

    sim.load_batch("mars")
    sim.start_campaign(CampaignPhase.C0)
    snapshot = sim.step()

    assert sim.melt.atmosphere is Atmosphere.ARGON_FLOW
    assert sim.melt.background_gas_species == "Ar"
    assert sim.melt.p_total_mbar == 1.0
    assert sim.melt.pO2_mbar == 0.0
    assert snapshot.overhead.pressure_mbar >= 1.0
    assert snapshot.overhead.composition["Ar"] == 1.0


def test_all_builtin_mars_feedstocks_define_co2_environment():
    data_path = Path(__file__).parent.parent / "data" / "feedstocks.yaml"
    feedstocks = yaml.safe_load(data_path.read_text())

    for key, feedstock in feedstocks.items():
        if not key.startswith("mars_"):
            continue
        sim = _sim({key: feedstock})
        required_c = 0.0
        if PyrolysisSimulator._uses_mars_carbon_cleanup(feedstock):
            required_c = PyrolysisSimulator._carbon_reductant_required_kg(
                feedstock, 1000.0)
        additives = {"C": required_c} if required_c > 0.0 else None
        sim.load_batch(key, additives_kg=additives)

        assert sim.melt.ambient_pressure_mbar > 0.0
        assert sim.melt.ambient_atmosphere == "96% CO2"
