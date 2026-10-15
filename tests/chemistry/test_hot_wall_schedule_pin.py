"""Green wall-schedule pins before the d-148 ownership refactor.

These capture the pre-change numbers; they do not endorse cold upstream walls.
"""

import pytest

from simulator.overhead import OverheadGasModel
from simulator.state import CampaignPhase, MeltState


@pytest.mark.parametrize(
    "campaign,hour,hot_C,downstream_C",
    [
        (CampaignPhase.C0, 8.0, 1500.0, (350.0, 200.0, 80.0)),
        (CampaignPhase.C2A, 3.0, 1425.0, (1425.0, 1425.0, 1425.0)),
        (CampaignPhase.C2A, 8.0, 1650.0, (1650.0, 1650.0, 1650.0)),
        (CampaignPhase.C2A_STAGED, 8.0, 1800.0, (1800.0, 1800.0, 1800.0)),
    ],
)
def test_green_wall_schedule_pin(setpoints_data, campaign, hour, hot_C, downstream_C):
    model = OverheadGasModel(setpoints_data["overhead_headspace"])
    melt = MeltState(
        temperature_C=2100.0, campaign=campaign, campaign_hour=hour,
    )
    names = tuple(f"stage_{number}_to_stage_{number + 1}" for number in range(7))

    assert model.resolve_pipe_temperature_C(melt) == hot_C
    assert model.resolve_pipe_segment_temperatures_C(names, melt) == dict(
        zip(names, (hot_C,) * 4 + downstream_C)
    )
