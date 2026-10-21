"""Resolved-input pins captured before the class-B owner consolidation."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from simulator.campaigns import CampaignManager
from simulator.overhead import OverheadGasModel
from simulator.recipe import RecipePatch, RecipeSchema
from simulator.state import BatchRecord, CampaignPhase, MeltState


def _setpoints():
    return yaml.safe_load((Path(__file__).resolve().parents[1] / "data/setpoints.yaml").read_text())


def _manager(values=None):
    schema = RecipeSchema()
    from simulator.runner import PyrolysisRun
    setpoints = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        setpoints_patch=schema.to_setpoints_patch(RecipePatch(values or {})),
    )._session_config().setpoints
    return CampaignManager(setpoints)


def test_default_resolved_input_pin():
    manager = CampaignManager(_setpoints())
    melt = MeltState(campaign=CampaignPhase.C0B)
    assert manager.get_temp_target(CampaignPhase.C0B, 0, melt) == (1250.0, 30.0)
    assert manager._max_hold_hr(CampaignPhase.C0B) == 3.0
    assert manager._max_hold_hr(CampaignPhase.C2A) == 30.0
    assert manager.c4_max_temp_C == 1670.0
    assert manager._configured_max_hold_hr(CampaignPhase.C3_K, "C3_K", "A") == 12.0
    assert manager._configured_max_hold_hr(CampaignPhase.C3_NA, "C3_NA", "default") == 35.0


def test_custom_c4_absence_pin():
    from simulator.optimize.evaluate import _c4_default_hold_temp_C
    setpoints = copy.deepcopy(_setpoints())
    setpoints["campaigns"]["C4"]["temp_range_C"] = [1580, 1650]
    assert _c4_default_hold_temp_C(setpoints) == 1650.0
    assert CampaignManager(setpoints).c4_max_temp_C == 1670.0


def test_wall_schedule_pin():
    # Existing schedules, including their ramp and downstream stages, are the
    # parity oracle for the refactor. The bug-fix tests probe patched defaults.
    model = OverheadGasModel()
    model.configure_headspace(_setpoints()["overhead_headspace"])
    for phase, hour, expected in (
        (CampaignPhase.C2A, 0, 1200.0),
        (CampaignPhase.C2A, 3, 1425.0),
        (CampaignPhase.C2A, 6, 1650.0),
        (CampaignPhase.C2A_STAGED, 0, 1800.0),
        (CampaignPhase.C4, 0, 1500.0),
    ):
        melt = MeltState(campaign=phase, campaign_hour=hour)
        assert model.resolve_pipe_temperature_C(melt) == expected


def test_post_mg_override_pin():
    manager = _manager({("campaigns", "C2A_continuous", "max_hold_hr"): 40})
    manager.get_next_campaign(CampaignPhase.C4, BatchRecord(path="A", branch="two"))
    melt = MeltState(campaign=CampaignPhase.C2A)
    manager.configure_campaign(melt, CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == 160.0
    assert manager._campaign_overrides(CampaignPhase.C2A)["min_hold_hr"] == 82.0
