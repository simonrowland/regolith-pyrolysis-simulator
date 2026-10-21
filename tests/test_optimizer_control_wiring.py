"""Resolved-input pins captured before the class-B owner consolidation."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.campaigns import CampaignManager
from simulator.overhead import OverheadGasModel
from simulator.recipe import RecipePatch, RecipeSchema, RecipeValidationError
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
    manager.configure_campaign(MeltState(temperature_C=1670), CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == 99.0
    assert manager.c4_max_temp_C == 1670.0
    assert manager._configured_max_hold_hr(CampaignPhase.C3_K, "C3_K", "A") == 12.0
    assert manager._configured_max_hold_hr(CampaignPhase.C3_NA, "C3_NA", "default") == 35.0


def test_custom_c4_absence_pin():
    from simulator.optimize.evaluate import _c4_default_hold_temp_C
    setpoints = copy.deepcopy(_setpoints())
    setpoints["campaigns"]["C4"]["temp_range_C"] = [1580, 1650]
    assert _c4_default_hold_temp_C(setpoints) == 1650.0
    assert CampaignManager(setpoints).c4_max_temp_C == 1650.0


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
    assert manager._max_hold_hr(CampaignPhase.C2A) == 40.0
    assert "min_hold_hr" not in manager._campaign_overrides(CampaignPhase.C2A)


@pytest.mark.parametrize("hours", [40, 124])
def test_post_mg_maximum_reads_the_searched_owner(hours):
    manager = _manager({("campaigns", "C2A_continuous", "max_hold_hr"): hours})
    manager.get_next_campaign(CampaignPhase.C4, BatchRecord(path="A", branch="two"))
    melt = MeltState(campaign=CampaignPhase.C2A)
    manager.configure_campaign(melt, CampaignPhase.C2A)
    assert manager._max_hold_hr(CampaignPhase.C2A) == hours


@pytest.mark.parametrize("path", [tuple(path.split(".")) for path in ("campaigns.C0.temp_range_C", "campaigns.C0b_p_cleanup.temp_range_C", "campaigns.C2B.temp_range_C", "campaigns.C2A_continuous.dT_dt_C_per_hr.early_ramp_1050_1320C", "campaigns.C4.temp_range_C", "campaigns.C5.temp_range_C")])
def test_consumed_ranges_refuse_scalars_at_validation(path):
    schema = RecipeSchema()
    spec = schema.spec_for(path)
    value = (spec.low + spec.high) / 2
    with pytest.raises(RecipeValidationError, match="pair"):
        RecipePatch({path: value}).validated(schema)
    RecipePatch({path: [spec.low, spec.high]}).validated(schema)
    with pytest.raises(RecipeValidationError, match="pair"):
        RecipePatch({path: [value]}).validated(schema)


def test_c4_requires_a_nonempty_temperature_window():
    with pytest.raises(RecipeValidationError, match="strictly increasing"):
        RecipePatch({("campaigns", "C4", "temp_range_C"): [1625, 1625]}).validated()


@pytest.mark.parametrize("path", [
    "overhead_headspace.liner_temperature_C.default_C",
    *[f"overhead_headspace.pipe_segment_temperatures_C.segments.stage_{i}_to_stage_{i+1}.default_C" for i in range(7)],
])
def test_searched_wall_defaults_move_the_active_schedule(path):
    schema = RecipeSchema()
    spec = schema.spec_for(tuple(path.split(".")))
    values = [spec.low + (spec.high - spec.low) * f for f in (0.25, 0.75)]
    resolved = []
    for value in values:
        manager = _manager({tuple(path.split(".")): value})
        model = OverheadGasModel(manager.setpoints["overhead_headspace"])
        melt = MeltState(campaign=CampaignPhase.C2A_STAGED)
        if "segments" in path:
            name = path.split(".")[-2]
            resolved.append(model.resolve_pipe_segment_temperatures_C([name], melt)[name])
        else:
            resolved.append(model.resolve_pipe_temperature_C(melt))
    assert resolved[1] - resolved[0] == pytest.approx(values[1] - values[0])


def test_redundant_search_axes_are_diagnostics_only():
    schema = RecipeSchema()
    for path in (
        "campaigns.C0b_p_cleanup.pO2_mbar_default",
        "campaigns.C2A_continuous.p_total_mbar_default",
        "campaigns.C2A_staged.p_total_mbar",
        "campaigns.C2A_staged.p_total_mbar_default",
        "campaigns.C2B.pO2_mbar_default",
        "overhead_headspace.pipe_segment_temperatures_C.default_C",
    ):
        assert not schema.spec_for(tuple(path.split("."))).search_enabled


@pytest.mark.parametrize(("path", "phase", "values"), [
    ("campaigns.C0b_p_cleanup.duration_hr", CampaignPhase.C0B, (1.0, 2.0)),
    ("campaigns.C2A_continuous.duration_hr", CampaignPhase.C2A, (40.0, 124.0)),
])
def test_manual_duration_updates_the_live_endpoint(path, phase, values):
    for value in values:
        manager = _manager({tuple(path.split(".")): [value, value]})
        assert manager._max_hold_hr(phase) == value


@pytest.mark.parametrize(("path", "branch", "values"), [
    ("campaigns.C3.duration_after_pathA_hr", "A", (12.0, 16.0)),
    ("campaigns.C3.duration_after_pathB_hr_per_phase", "default", (23.75, 31.25)),
])
def test_c3_durations_update_both_phase_endpoints(path, branch, values):
    for value in values:
        manager = _manager({tuple(path.split(".")): [value, value]})
        for phase in (CampaignPhase.C3_K, CampaignPhase.C3_NA):
            assert manager._configured_max_hold_hr(phase, phase.name, branch) == value


@pytest.mark.parametrize(("phase", "key"), [(CampaignPhase.C3_K, "K_phase"), (CampaignPhase.C3_NA, "Na_phase")])
def test_c3_bakeout_pressure_reaches_the_phase_command(phase, key):
    for value in (0.75, 1.25):
        manager = _manager({("campaigns", "C3", key, "pO2_bakeout_mbar"): value})
        melt = MeltState(campaign=phase, campaign_hour=3)
        manager.configure_campaign(melt, phase)
        manager.get_temp_target(phase, melt.campaign_hour, melt)
        assert melt.pO2_mbar == value
        melt.campaign_hour = 6
        manager.get_temp_target(phase, melt.campaign_hour, melt)
        assert melt.pO2_mbar == 1.0


def test_c0b_manual_target_and_existing_yaml_ramp_reach_temperature_command():
    for value in (1215.0, 1285.0):
        manager = _manager({("campaigns", "C0b_p_cleanup", "temp_range_C"): [value, value]})
        melt = MeltState(campaign=CampaignPhase.C0B)
        assert manager.get_temp_target(CampaignPhase.C0B, 0, melt) == (value, 30.0)
        manager.campaigns["C0b_p_cleanup"]["dT_dt_C_per_hr"] = 45.0
        assert manager.get_temp_target(CampaignPhase.C0B, 0, melt) == (value, 45.0)


def test_supplied_schema_mapping_owns_bounds():
    setpoints = _setpoints()
    setpoints["campaigns"]["C3"]["Na_phase"]["Na_total_kg"][1] = 180
    setpoints["campaigns"]["C3"]["K_phase"]["K_per_cycle_kg"][1] = 38
    setpoints["campaigns"]["C5"]["allow_mre_voltage_cap_upper_bound_V"] = 1.5
    schema = RecipeSchema(setpoints=setpoints)
    assert schema.spec_for(("campaigns", "C3", "alkali_dosing", "Na_kg")).high == 180
    assert schema.spec_for(("campaigns", "C3", "alkali_dosing", "K_kg")).high == 76
    assert schema.spec_for(("campaigns", "C5", "allow_mre_voltage_cap_V")).high == 1.5
    with pytest.raises(RecipeValidationError, match="upper bound"):
        RecipePatch({("campaigns", "C5", "allow_mre_voltage_cap_V"): 1.6}).validated(schema)


def test_manual_duration_cannot_shadow_an_explicit_maximum():
    patch = RecipePatch({
        ("campaigns", "C2A_continuous", "duration_hr"): 40.0,
        ("campaigns", "C2A_continuous", "max_hold_hr"): 124,
    })
    with pytest.raises(RecipeValidationError, match="both set one endpoint"):
        RecipeSchema().to_setpoints_patch(patch)


def test_shared_c3_pressure_still_actuates_bakeout_without_a_phase_command():
    for value in (0.75, 1.25):
        manager = _manager({
            ("campaigns", "C3", "pO2_mbar_default"): value,
            ("campaigns", "C3", "p_total_mbar_default"): 1.5,
        })
        melt = MeltState(campaign=CampaignPhase.C3_NA, campaign_hour=3)
        manager.configure_campaign(melt, CampaignPhase.C3_NA)
        manager.get_temp_target(CampaignPhase.C3_NA, melt.campaign_hour, melt)
        assert melt.pO2_mbar == value


def test_c3_without_manual_phase_pressure_preserves_evolved_pressure():
    manager = CampaignManager(_setpoints())
    melt = MeltState(campaign=CampaignPhase.C3_NA, campaign_hour=3)
    manager.configure_campaign(melt, CampaignPhase.C3_NA)
    melt.pO2_mbar = 0.3
    manager.get_temp_target(CampaignPhase.C3_NA, 3, melt)
    assert melt.pO2_mbar == 0.3


def test_c3_pair_pressure_is_materialized_and_operator_override_still_wins():
    manager = _manager({("campaigns", "C3", "Na_phase", "pO2_bakeout_mbar"): [0.5, 1.0]})
    melt = MeltState(campaign=CampaignPhase.C3_NA, campaign_hour=3)
    manager.configure_campaign(melt, CampaignPhase.C3_NA)
    manager.get_temp_target(CampaignPhase.C3_NA, 3, melt)
    assert melt.pO2_mbar == 0.75
    manager.overrides["C3_NA"] = {"pO2_mbar": 0.9}
    manager.configure_campaign(melt, CampaignPhase.C3_NA)
    manager.get_temp_target(CampaignPhase.C3_NA, 3, melt)
    assert melt.pO2_mbar == 0.9


@pytest.mark.parametrize("values", [
    {("overhead_headspace", "liner_temperature_C", "default_C"): 1600.0},
    {("campaigns", "C0b_p_cleanup", "duration_hr"): [1.0, 2.0]},
    {("campaigns", "C3", "duration_after_pathA_hr"): [12.0, 16.0]},
    {("campaigns", "C3", "Na_phase", "pO2_bakeout_mbar"): [0.5, 1.0]},
])
def test_actuated_public_recipes_remain_replayable(values):
    from simulator.recipe_io import normalize_recipe_patch
    compiled = RecipeSchema().to_setpoints_patch(RecipePatch(values))
    assert normalize_recipe_patch(compiled) == compiled


def test_wall_request_merge_is_idempotent_and_preserves_its_inputs():
    from simulator.runner import _deep_merge_setpoints
    base = _setpoints()
    original = copy.deepcopy(base)
    patch = {"overhead_headspace": {"liner_temperature_C": {"default_C": 1600.0}}}
    first = _deep_merge_setpoints(base, patch)
    assert _deep_merge_setpoints(first, patch) == first
    assert base == original
    assert patch == {"overhead_headspace": {"liner_temperature_C": {"default_C": 1600.0}}}


@pytest.mark.parametrize("phase", [CampaignPhase.C3_K, CampaignPhase.C3_NA])
@pytest.mark.parametrize(("path", "field", "limit"), [
    ("A", "duration_after_pathA_hr", 16),
    ("B", "duration_after_pathB_hr_per_phase", 30),
])
def test_c3_duration_reaches_the_selected_path_endpoint(phase, path, field, limit):
    from simulator.state import CondensationTrain, EvaporationFlux
    manager = _manager({("campaigns", "C3", field): [limit, limit]})
    melt = MeltState(campaign=phase, campaign_hour=limit - 2)
    record = BatchRecord(path=path)
    train = CondensationTrain.create_default()
    assert not manager.check_endpoint(melt, EvaporationFlux(), train, record)
    melt.campaign_hour += 1
    assert manager.check_endpoint(melt, EvaporationFlux(), train, record)
