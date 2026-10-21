"""Thermal-input pins and automatic C2A deadline regressions."""
from pathlib import Path

import pytest
import yaml

from simulator.campaigns import CampaignManager
from simulator.state import BatchRecord, CampaignPhase, CondensationTrain, EvaporationFlux, MeltState


def _manager():
    return CampaignManager(yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "data/setpoints.yaml").read_text()))


@pytest.mark.parametrize(("temperature", "expected"), [(1150, 15), (1319, 15), (1320, 7.5), (1670, 7.5), (2200, 7.5)])
def test_active_c2a_ramp_pin(temperature, expected):
    manager = _manager()
    melt = MeltState(campaign=CampaignPhase.C2A, temperature_C=temperature)
    assert manager.get_temp_target(CampaignPhase.C2A, 0, melt) == (2200, expected)


@pytest.mark.parametrize(("refractory_pct", "hour", "expected"), [(0, 0, True), (59.62, 0, False), (59.62, 19, False)])
def test_c6_composition_and_hold_clock_pin(refractory_pct, hour, expected):
    manager = _manager()
    melt = MeltState(campaign=CampaignPhase.C6, temperature_C=1400, campaign_hour=hour,
        composition_kg={"SiO2": refractory_pct, "CaO": 100-refractory_pct})
    # At-target hold hours come from snapshots, not the elapsed preheat clock.
    assert manager.check_endpoint(melt, EvaporationFlux(), CondensationTrain(), BatchRecord()) is expected
