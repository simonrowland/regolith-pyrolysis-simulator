from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.campaigns import CampaignManager
from simulator.core import Atmosphere, CampaignPhase, MeltState


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _setpoints() -> dict:
    return yaml.safe_load((DATA_DIR / "setpoints.yaml").read_text()) or {}


def _stage(setpoints: dict, name: str) -> dict:
    for stage in setpoints["campaigns"]["C2A_staged"]["stages"]:
        if stage.get("name") == name:
            return stage
    raise AssertionError(f"missing C2A_staged stage {name}")


@pytest.mark.parametrize(
    ("campaign", "pO2_mbar", "p_total_mbar", "atmosphere"),
    [
        (CampaignPhase.C0, 0.0, 0.0, Atmosphere.HARD_VACUUM),
        (CampaignPhase.C0B, 9.0, 9.0, Atmosphere.CONTROLLED_O2_FLOW),
        (CampaignPhase.C2A, 0.0, 10.0, Atmosphere.PN2_SWEEP),
        (CampaignPhase.C2A_STAGED, 0.0, 10.0, Atmosphere.PN2_SWEEP),
        (CampaignPhase.C2B, 1.5, 1.5, Atmosphere.CONTROLLED_O2),
        (CampaignPhase.C3_K, 1.0, 1.0, Atmosphere.CONTROLLED_O2),
        (CampaignPhase.C3_NA, 1.0, 1.0, Atmosphere.CONTROLLED_O2),
        (CampaignPhase.C4, 0.2, 0.2, Atmosphere.CONTROLLED_O2),
        (CampaignPhase.C5, 50.0, 50.0, Atmosphere.O2_BACKPRESSURE),
        (CampaignPhase.C6, 0.2, 0.2, Atmosphere.CONTROLLED_O2),
        (CampaignPhase.MRE_BASELINE, 50.0, 50.0, Atmosphere.O2_BACKPRESSURE),
    ],
)
def test_configure_campaign_pressure_defaults_match_legacy_constants(
    campaign: CampaignPhase,
    pO2_mbar: float,
    p_total_mbar: float,
    atmosphere: Atmosphere,
):
    melt = MeltState()
    CampaignManager(_setpoints()).configure_campaign(melt, campaign)

    assert melt.pO2_mbar == pytest.approx(pO2_mbar)
    assert melt.p_total_mbar == pytest.approx(p_total_mbar)
    assert melt.atmosphere is atmosphere


def test_campaign_pressure_default_override_reaches_melt_state():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2B"]["pO2_mbar_default"] = 0.75

    melt = MeltState()
    CampaignManager(setpoints).configure_campaign(melt, CampaignPhase.C2B)

    assert melt.pO2_mbar == pytest.approx(0.75)
    assert melt.p_total_mbar == pytest.approx(1.5)


def test_c2a_continuous_ramp_rates_use_yaml_midpoints():
    setpoints = _setpoints()
    bands = setpoints["campaigns"]["C2A_continuous"]["dT_dt_C_per_hr"]
    assert (
        (float(bands["early_ramp_1050_1320C"][0])
         + float(bands["early_ramp_1050_1320C"][1])) / 2.0
    ) == 15.0
    assert (
        (float(bands["peak_SiO_window_1400_1600C"][0])
         + float(bands["peak_SiO_window_1400_1600C"][1])) / 2.0
    ) == 7.5

    manager = CampaignManager(setpoints)
    _, early_ramp = manager.get_temp_target(
        CampaignPhase.C2A,
        0,
        MeltState(campaign=CampaignPhase.C2A, temperature_C=1200.0),
    )
    _, peak_ramp = manager.get_temp_target(
        CampaignPhase.C2A,
        0,
        MeltState(campaign=CampaignPhase.C2A, temperature_C=1400.0),
    )

    assert early_ramp == 15.0
    assert peak_ramp == 7.5


def test_c2a_continuous_ramp_rate_band_malformed_fails_loud():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2A_continuous"]["dT_dt_C_per_hr"][
        "early_ramp_1050_1320C"
    ] = [10]

    with pytest.raises(
        ValueError,
        match=r"C2A_continuous\.dT_dt_C_per_hr\.early_ramp_1050_1320C",
    ):
        CampaignManager(setpoints).get_temp_target(
            CampaignPhase.C2A,
            0,
            MeltState(campaign=CampaignPhase.C2A, temperature_C=1200.0),
        )


def test_melt_pressure_validator_refuses_partial_pressure_above_total():
    melt = MeltState()
    melt.p_total_mbar = 1.0
    melt.pO2_mbar = 1.0 + 5e-10
    melt.validate_melt_pressures()

    melt.pO2_mbar = 1.1
    with pytest.raises(ValueError, match="melt_pressure_partial_exceeds_total"):
        melt.validate_melt_pressures()


def test_configure_campaign_refuses_pO2_default_above_total():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2B"]["pO2_mbar_default"] = 2.0
    setpoints["campaigns"]["C2B"]["p_total_mbar_default"] = 1.0

    with pytest.raises(ValueError, match="melt_pressure_partial_exceeds_total"):
        CampaignManager(setpoints).configure_campaign(MeltState(), CampaignPhase.C2B)


def test_present_but_nonnumeric_campaign_pressure_default_raises():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2B"]["pO2_mbar_default"] = "bad"

    with pytest.raises(ValueError, match="Invalid numeric campaign setpoint"):
        CampaignManager(setpoints).configure_campaign(MeltState(), CampaignPhase.C2B)


def test_mre_baseline_pressure_defaults_are_yaml_sourced_and_decoupled():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C5"]["pO2_mbar_default"] = 12.0
    setpoints["campaigns"]["C5"]["p_total_mbar_default"] = 12.0

    melt = MeltState()
    CampaignManager(setpoints).configure_campaign(melt, CampaignPhase.MRE_BASELINE)

    assert melt.pO2_mbar == pytest.approx(50.0)
    assert melt.p_total_mbar == pytest.approx(50.0)

    setpoints["campaigns"]["mre_baseline"]["pO2_mbar_default"] = 40.0
    setpoints["campaigns"]["mre_baseline"]["p_total_mbar_default"] = 41.0

    melt = MeltState()
    CampaignManager(setpoints).configure_campaign(melt, CampaignPhase.MRE_BASELINE)

    assert melt.pO2_mbar == pytest.approx(40.0)
    assert melt.p_total_mbar == pytest.approx(41.0)


def test_c2a_staged_gas_cover_switch_is_stage_atomic():
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").update({
        "gas_cover_mode": "po2_hold",
        "pO2_mbar": 1.0,
        "p_total_mbar": 7.0,
    })
    _stage(setpoints, "sio_window").update({
        "gas_cover_mode": "pn2_sweep",
        "pO2_mbar": 1.0e-6,
        "p_total_mbar": 10.000001,
    })
    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C2A_STAGED)

    manager.configure_campaign(melt, CampaignPhase.C2A_STAGED)
    first = dict(manager.last_c2a_staged_gas_control or {})
    assert melt.atmosphere is Atmosphere.CONTROLLED_O2
    assert first["stage_name"] == "alkali_early_fe"
    assert first["gas_cover_mode"] == "po2_hold"
    assert first["pO2_mbar"] == pytest.approx(1.0)
    assert first["p_total_mbar"] == pytest.approx(7.0)

    melt.campaign_hour = 4
    manager.apply_c2a_staged_gas_controls(melt)
    second = dict(manager.last_c2a_staged_gas_control or {})
    assert melt.atmosphere is Atmosphere.PN2_SWEEP
    assert second["stage_name"] == "sio_window"
    assert second["gas_cover_mode"] == "pn2_sweep"
    assert second["pO2_mbar"] == pytest.approx(1.0e-6)
    assert second["p_total_mbar"] == pytest.approx(10.000001)
    assert second["pN2_mbar"] == pytest.approx(10.0)


def test_c2a_staged_pn2_sweep_trace_po2_is_not_silent_or_phantom_o2():
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").update({
        "gas_cover_mode": "pn2_sweep",
        "pO2_mbar": 1.0e-6,
        "p_total_mbar": 10.000001,
    })
    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C2A_STAGED)

    manager.configure_campaign(melt, CampaignPhase.C2A_STAGED)
    gas = dict(manager.last_c2a_staged_gas_control or {})

    assert melt.atmosphere is Atmosphere.PN2_SWEEP
    assert melt.pO2_mbar == pytest.approx(1.0e-6)
    assert melt.p_total_mbar == pytest.approx(10.000001)
    assert gas["pN2_mbar"] == pytest.approx(10.0)
    assert gas["pn2_band_action"] == ""


def test_c2a_staged_pn2_sweep_clamps_to_operating_band():
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").update({
        "gas_cover_mode": "pn2_sweep",
        "pO2_mbar": 0.25,
        "p_total_mbar": 1.25,
    })
    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C2A_STAGED)

    manager.configure_campaign(melt, CampaignPhase.C2A_STAGED)
    gas = dict(manager.last_c2a_staged_gas_control or {})

    assert melt.atmosphere is Atmosphere.PN2_SWEEP
    assert gas["pN2_mbar"] == pytest.approx(5.0)
    assert gas["p_total_mbar"] == pytest.approx(5.25)
    assert gas["requested_p_total_mbar"] == pytest.approx(1.25)
    assert gas["pn2_band_action"] == "clamped_low"


@pytest.mark.parametrize(
    ("stage_patch", "message"),
    [
        ({"gas_cover_mode": "bad"}, "C2A_staged.stages.alkali_early_fe.gas_cover_mode"),
        (
            {"gas_cover_mode": "po2_hold", "pO2_mbar": 0.0, "p_total_mbar": 1.0},
            "C2A_staged.stages.alkali_early_fe.pO2_mbar",
        ),
        (
            {"gas_cover_mode": "po2_hold", "pO2_mbar": 2.0, "p_total_mbar": 1.0},
            "C2A_staged.stages.alkali_early_fe.p_total_mbar",
        ),
        (
            {"gas_cover_mode": "pn2_sweep", "pO2_mbar": 2.0, "p_total_mbar": 1.0},
            "C2A_staged.stages.alkali_early_fe.p_total_mbar",
        ),
    ],
)
def test_c2a_staged_invalid_gas_schedule_fails_loud(stage_patch, message):
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").update(stage_patch)

    with pytest.raises(ValueError, match=message):
        CampaignManager(setpoints).configure_campaign(
            MeltState(),
            CampaignPhase.C2A_STAGED,
        )


@pytest.mark.parametrize("stages_value", [None, [], "drop_key"])
def test_c2a_staged_without_stage_schedule_noops_gas_controls(stages_value):
    # A minimal/legacy C2A_staged config (no per-stage schedule) must configure
    # without raising and leave gas cover untouched — per-stage gas control is a
    # no-op when there is no schedule, preserving pre-schedule behavior.
    setpoints = deepcopy(_setpoints())
    c2a = setpoints["campaigns"]["C2A_staged"]
    if stages_value == "drop_key":
        c2a.pop("stages", None)
    else:
        c2a["stages"] = stages_value

    mgr = CampaignManager(setpoints)
    melt = MeltState()
    mgr.configure_campaign(melt, CampaignPhase.C2A_STAGED)

    assert mgr.last_c2a_staged_gas_control is None
    # Idempotent re-application also no-ops rather than raising.
    mgr.apply_c2a_staged_gas_controls(melt, CampaignPhase.C2A_STAGED)
    assert mgr.last_c2a_staged_gas_control is None
