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
