from pathlib import Path

import yaml

from simulator.core import Atmosphere, CampaignPhase, PyrolysisSimulator
from simulator.melt_backend.base import StubBackend


def _sim(feedstocks):
    backend = StubBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


def test_stage0_lunar_feedstock_uses_hard_vacuum():
    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )

    sim.load_batch("lunar")
    sim.start_campaign(CampaignPhase.C0)

    assert sim.melt.atmosphere is Atmosphere.HARD_VACUUM
    assert sim.melt.p_total_mbar == 0.0
    assert sim.melt.pO2_mbar == 0.0


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
    sim.start_campaign(CampaignPhase.C0)
    snapshot = sim.step()

    assert sim.melt.atmosphere is Atmosphere.CO2_BACKPRESSURE
    assert sim.melt.p_total_mbar == 6.0
    assert sim.melt.pO2_mbar == 0.0
    assert snapshot.overhead.pressure_mbar == 6.0
    assert snapshot.overhead.composition["CO2"] == 5.76


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
