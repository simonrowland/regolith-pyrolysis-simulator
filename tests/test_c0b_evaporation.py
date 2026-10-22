"""C0B's cleanup targets must not suppress incidental melt evaporation."""

import pytest

from simulator.core import PyrolysisSimulator
from simulator.runner import PyrolysisRun
from simulator.state import CampaignPhase


@pytest.mark.xfail(strict=True, reason="b-1025: C0B admits only P carriers")
def test_c0b_hour_uses_general_evaporation_owner(monkeypatch):
    owner = PyrolysisSimulator._calculate_evaporation
    comparisons = []

    def compare_at_same_state(sim, equilibrium, **kwargs):
        if sim.melt.campaign != CampaignPhase.C0B:
            return owner(sim, equilibrium, **kwargs)

        cover = (sim.melt.pO2_mbar, sim.melt.p_total_mbar)
        # C0 activates the same P carriers alongside ordinary melt channels.
        # Change only the request marker: retain C0B's equilibrium, inventory,
        # temperature, gas cover, geometry and owner-supplied kinetic inputs.
        sim.melt.campaign = CampaignPhase.C0
        try:
            expected = owner(sim, equilibrium, **kwargs)
        finally:
            sim.melt.campaign = CampaignPhase.C0B
        actual = owner(sim, equilibrium, **kwargs)
        assert (sim.melt.pO2_mbar, sim.melt.p_total_mbar) == cover
        comparisons.append((expected.species_kg_hr, actual.species_kg_hr))
        return actual

    monkeypatch.setattr(
        PyrolysisSimulator, "_calculate_evaporation", compare_at_same_state
    )
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti", campaign="C0B", hours=1,
        additives_kg={}, allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    session.simulator.melt.temperature_C = 950.0
    payload = run._run_session(session)

    assert payload["status"] == "ok"
    assert comparisons, "the run must exercise a C0B hour"
    for expected, actual in comparisons:
        assert expected["Na"] > 0.0
        assert expected["K"] > 0.0
        assert actual.get("Na", 0.0) > 0.0
        assert actual.get("K", 0.0) > 0.0
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-18)
    row = payload["per_hour_summary"][0]
    assert row["vapor_species_kg_hr"]["Na"] > 0.0
    assert row["vapor_species_kg_hr"]["K"] > 0.0
