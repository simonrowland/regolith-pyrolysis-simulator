from copy import deepcopy
from pathlib import Path

import yaml

from simulator.core import Atmosphere, CampaignPhase, PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend


C0_ENDPOINT_SETPOINTS = {
    "temp_range_C": [20, 950],
    "dT_dt_C_per_hr": 50,
    "max_hold_hr": 25,
    "soft_endpoint": {
        "min_hold_hr": 10,
        "temperature_min_C": 940,
    },
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
        {"campaigns": {"C0": deepcopy(C0_ENDPOINT_SETPOINTS)}},
        feedstocks,
        vapor_pressures,
    )


def test_stage0_lunar_feedstock_uses_hard_vacuum():
    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )

    sim.load_batch("lunar")
    assert sim._melt_headspace_total_pressure_bar() == 0.0
    sim.start_campaign(CampaignPhase.C0)
    assert sim._melt_headspace_total_pressure_bar() == 0.0

    assert sim.melt.atmosphere is Atmosphere.HARD_VACUUM
    assert sim.melt.p_total_mbar == 0.0
    assert sim.melt.pO2_mbar == 0.0


def test_initial_headspace_sizes_equipment_for_the_batch_being_loaded(monkeypatch):
    sim = _sim({
        "lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}},
    })
    sim.setpoints['overhead_headspace'] = {'enabled': True}
    sizing_batches = []
    original = sim._get_turbine_spec

    def record_sizing_batch():
        sizing_batches.append((sim.record.feedstock_key, sim.record.batch_mass_kg))
        return original()

    monkeypatch.setattr(sim, '_get_turbine_spec', record_sizing_batch)
    for mass_kg in (1000.0, 10.0):
        sizing_batches.clear()
        sim.load_batch('lunar', mass_kg=mass_kg)
        assert sizing_batches == [('lunar', mass_kg)]
        assert sim._equipment is not None


def test_stage0_mars_feedstock_uses_surface_co2_backpressure():
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
    assert sim._melt_headspace_composition_mbar == {"CO2": 5.76, "ambient_other": 0.24}
    sim.start_campaign(CampaignPhase.C0)
    assert sim._melt_headspace_composition_mbar == {"CO2": 5.76, "ambient_other": 0.24}
    snapshot = sim.step()

    assert sim.melt.atmosphere is Atmosphere.CO2_BACKPRESSURE
    assert sim.melt.p_total_mbar == 6.0
    assert sim.melt.pO2_mbar == 0.0
    assert snapshot.overhead.pressure_mbar == 6.0
    assert snapshot.overhead.composition["CO2"] == 5.76
    assert snapshot.overhead.composition["ambient_other"] == 0.24
    assert sum(snapshot.overhead.composition.values()) == 6.0


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
