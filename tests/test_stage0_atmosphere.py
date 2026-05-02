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
    sim = _sim({"lunar": {"label": "Lunar", "composition_wt_pct": {}}})

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
                "composition_wt_pct": {},
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
