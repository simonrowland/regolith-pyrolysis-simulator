"""Thermal-input pins, C2A depletion arming and runaway-guard regressions."""
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
def test_automatic_guard_is_separate_from_acquisition(entry_C, ceiling_C, expected):
    # Historical deadlines minus the 28 h window independently pin the
    # acquisition time; the window no longer ends extraction.
    # E.g. 1150->1320 at 15 C/h + 1320->2200 at 7.5 C/h = 128 2/3 h.
    manager = _manager()
    manager.furnace_max_T_C = ceiling_C
    melt = MeltState(campaign=CampaignPhase.C2A, temperature_C=entry_C)
    manager.configure_campaign(melt, CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == 10_000
    assert manager._campaign_overrides(CampaignPhase.C2A)['min_hold_hr'] == max(18, expected - 28)
    # The budget is anchored at entry, and must not recede as the melt heats.
    melt.temperature_C = ceiling_C
    assert manager._max_hold_hr(CampaignPhase.C2A) == 10_000
    manager.configure_campaign(melt, CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == 10_000


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
    assert manager._max_hold_hr(CampaignPhase.C2A) == 10_000
    manager.overrides["C2A"]["max_hours"] = 40
    assert manager._max_hold_hr(CampaignPhase.C2A) == 40


def test_automatic_guard_does_not_use_extraction_window():
    manager = _manager()
    manager.setpoints["campaigns"]["C2A_continuous"]["duration_h"] = [18, 40]
    manager.configure_campaign(MeltState(temperature_C=1670), CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == 10_000


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
    assert manager._max_hold_hr(CampaignPhase.C2A) == 10_000
    assert manager._campaign_overrides(CampaignPhase.C2A)['min_hold_hr'] == expected - 28


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
    for hour, rate, expected in [(30, 0, False), (71, 0, True), (71, 1, False), (98, 1, False), (9999, 1, True)]:
        melt.campaign_hour = hour
        assert manager.check_endpoint(melt, EvaporationFlux(total_kg_hr=rate), CondensationTrain(), record) is expected


def test_automatic_runaway_guard_is_flagged_and_depletion_is_not():
    manager = _manager()
    melt = MeltState(campaign=CampaignPhase.C2A, temperature_C=2200)
    manager.configure_campaign(melt, CampaignPhase.C2A)
    melt.campaign_hour = 9999
    assert manager.check_endpoint(melt, EvaporationFlux(total_kg_hr=1), CondensationTrain(), BatchRecord())
    assert manager.last_c2a_termination['safety_cap_bound'] is True
    assert manager.last_c2a_termination['reason'] == 'runaway_guard'
    melt.campaign_hour = 200
    from simulator.state import HourSnapshot
    record = BatchRecord(snapshots=[HourSnapshot(campaign=CampaignPhase.C2A,
        evap_flux=EvaporationFlux(total_kg_hr=1))])
    threshold = manager._endpoint_float(CampaignPhase.C2A,
        manager._configured_endpoint(CampaignPhase.C2A, 'soft_endpoint'), 'threshold_kg_hr')
    assert manager.check_endpoint(melt, EvaporationFlux(total_kg_hr=threshold/2), CondensationTrain(), record)
    assert manager.last_c2a_termination['reason'] == 'soft_depletion'
    assert manager.last_c2a_termination['safety_cap_bound'] is False


@pytest.mark.parametrize('key', ['max_hours', 'hold_time_h', 'duration_h'])
def test_live_duration_equal_to_guard_remains_an_operator_command(key):
    manager = _manager()
    melt = MeltState(campaign=CampaignPhase.C2A, temperature_C=2200, campaign_hour=9999)
    manager.configure_campaign(melt, CampaignPhase.C2A)
    manager.overrides['C2A'] = {key: 10_000}
    assert manager.check_endpoint(melt, EvaporationFlux(total_kg_hr=1), CondensationTrain(), BatchRecord())
    assert manager.last_c2a_termination['reason'] == 'declared_duration'
    assert manager.last_c2a_termination['safety_cap_bound'] is False


def test_zero_runtime_max_hours_falls_back_to_flagged_runaway_guard():
    manager = _manager()
    manager.overrides['C2A'] = {'max_hours': 0}
    melt = MeltState(campaign=CampaignPhase.C2A, temperature_C=2200, campaign_hour=9999)
    manager.configure_campaign(melt, CampaignPhase.C2A)
    assert manager.check_endpoint(melt, EvaporationFlux(total_kg_hr=1), CondensationTrain(), BatchRecord())
    assert manager.last_c2a_termination['reason'] == 'runaway_guard'
    assert manager.last_c2a_termination['safety_cap_bound'] is True
