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


@pytest.mark.parametrize(("entry_C", "ceiling_C", "expected"), [
    (1670, 2200, 99), (1150, 2200, 157), (2200, 2200, 28),
    (1670, 1843, 52), (1200, 1300, 35),
])
def test_automatic_deadline_covers_acquisition_and_declared_window(entry_C, ceiling_C, expected):
    # Hand-integrated constant-rate segments in C/(C/h), rounded up once
    # before adding the declared 28 h extraction-window endpoint.
    # E.g. 1150->1320 at 15 C/h + 1320->2200 at 7.5 C/h = 128 2/3 h.
    manager = _manager()
    manager.furnace_max_T_C = ceiling_C
    melt = MeltState(campaign=CampaignPhase.C2A, temperature_C=entry_C)
    manager.configure_campaign(melt, CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == expected
    # The budget is anchored at entry, and must not recede as the melt heats.
    melt.temperature_C = ceiling_C
    assert manager._max_hold_hr(CampaignPhase.C2A) == expected
    manager.configure_campaign(melt, CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == 28


@pytest.mark.parametrize("hours", [18, 30, 40, 160])
def test_explicit_searched_deadline_is_not_the_automatic_default(hours):
    manager = _manager()
    manager.setpoints["campaigns"]["C2A_continuous"]["max_hold_hr"] = hours
    manager.get_next_campaign(CampaignPhase.C4, BatchRecord(path="A", branch="two"))
    manager.configure_campaign(MeltState(temperature_C=1670), CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == hours
    assert "min_hold_hr" not in manager._campaign_overrides(CampaignPhase.C2A)


def test_automatic_deadline_uses_runtime_ramp_and_retains_operator_cap():
    manager = _manager()
    manager.overrides["C2A"] = {"ramp_rate": 10}
    manager.configure_campaign(MeltState(temperature_C=1670), CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == 81
    manager.overrides["C2A"]["max_hours"] = 40
    assert manager._max_hold_hr(CampaignPhase.C2A) == 40


def test_automatic_deadline_reads_extraction_window_owner():
    manager = _manager()
    manager.setpoints["campaigns"]["C2A_continuous"]["duration_h"] = [18, 40]
    manager.configure_campaign(MeltState(temperature_C=1670), CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == 111


@pytest.mark.parametrize(('band', 'rate', 'entry_C', 'expected'), [
    ('early_ramp_1050_1320C', 10, 1150, 163),
    ('early_ramp_1050_1320C', 20, 1150, 154),
    ('peak_SiO_window_1400_1600C', 5, 1670, 134),
    ('peak_SiO_window_1400_1600C', 10, 1670, 81),
])
def test_automatic_deadline_reads_the_active_band_owner(band, rate, entry_C, expected):
    manager = _manager()
    manager.setpoints['campaigns']['C2A_continuous']['dT_dt_C_per_hr'][band] = [rate, rate]
    manager.configure_campaign(MeltState(temperature_C=entry_C), CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == expected


@pytest.mark.parametrize("temperature_C", [float("nan"), float("inf")])
def test_automatic_deadline_refuses_nonfinite_entry_temperature(temperature_C):
    from simulator.recipe_errors import MalformedRecipeError
    with pytest.raises(MalformedRecipeError, match="finite entry temperature"):
        _manager().configure_campaign(MeltState(temperature_C=temperature_C), CampaignPhase.C2A)


def test_automatic_deadline_keeps_soft_endpoint_and_safety_timeout_distinct():
    from simulator.state import HourSnapshot
    manager = _manager()
    melt = MeltState(campaign=CampaignPhase.C2A, temperature_C=1670)
    manager.configure_campaign(melt, CampaignPhase.C2A)
    record = BatchRecord(snapshots=[HourSnapshot(
        campaign=CampaignPhase.C2A, evap_flux=EvaporationFlux(total_kg_hr=1))])
    for hour, rate, expected in [(30, 0, False), (71, 0, True), (71, 1, False), (98, 1, True)]:
        melt.campaign_hour = hour
        assert manager.check_endpoint(melt, EvaporationFlux(total_kg_hr=rate), CondensationTrain(), record) is expected
