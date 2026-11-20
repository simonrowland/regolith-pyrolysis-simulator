from __future__ import annotations

from simulator.state import CampaignPhase
from tests.chemistry.conftest import _build_sim


def test_lunar_feo_floor_numeric_pin_before_owner_move(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.start_campaign(CampaignPhase.C0)

    floor = sim._melt_metal_saturation_fO2_floor_log(1423.0)

    assert floor is not None
    assert floor[1] == "FeO"
    # Studio pin from the current production resolver before the owner move.
    assert floor[0] == float.fromhex("-0x1.df17726efa008p+3")
