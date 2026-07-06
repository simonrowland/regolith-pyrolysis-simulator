from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.campaigns import CampaignManager
from simulator.state import (
    BatchRecord,
    CampaignPhase,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _setpoints() -> dict:
    return yaml.safe_load((DATA_DIR / "setpoints.yaml").read_text()) or {}


def _melt(
    campaign: CampaignPhase,
    hour: int,
    *,
    temperature_C: float = 25.0,
    composition_kg: dict[str, float] | None = None,
    voltage_V: float = 0.0,
    current_A: float = 100.0,
    low_current_hours: int = 0,
) -> MeltState:
    return MeltState(
        campaign=campaign,
        campaign_hour=hour,
        temperature_C=temperature_C,
        composition_kg=composition_kg or {
            "SiO2": 50.0,
            "Al2O3": 20.0,
            "CaO": 30.0,
        },
        mre_voltage_V=voltage_V,
        mre_current_A=current_A,
        mre_low_current_hours=low_current_hours,
    )


def _flux(total_kg_hr: float = 0.0, **species_kg_hr: float) -> EvaporationFlux:
    return EvaporationFlux(
        total_kg_hr=total_kg_hr,
        species_kg_hr=species_kg_hr,
    )


@pytest.mark.parametrize(
    (
        "campaign",
        "hour",
        "temperature_C",
        "flux",
        "record",
        "composition_kg",
        "voltage_V",
        "current_A",
        "low_current_hours",
        "expected",
    ),
    [
        (CampaignPhase.C0, 9, 940.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C0, 10, 940.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C0, 25, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C0B, 3, 1199.9, _flux(), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C0B, 3, 1200.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C2A, 18, 25.0, _flux(0.1), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C2A, 18, 25.0, _flux(0.099), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C2A, 30, 25.0, _flux(99.0), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C2A_STAGED, 7, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C2A_STAGED, 8, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C2B, 8, 25.0, _flux(Fe=0.05), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C2B, 8, 25.0, _flux(Fe=0.049), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C2B, 20, 25.0, _flux(Fe=9.0), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_K, 2, 25.0, _flux(), BatchRecord(path="A_staged"), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C3_K, 3, 25.0, _flux(), BatchRecord(path="A_staged"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_K, 12, 25.0, _flux(), BatchRecord(path="A"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_K, 25, 25.0, _flux(), BatchRecord(path="B"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_NA, 3, 25.0, _flux(), BatchRecord(path="A_staged"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_NA, 18, 25.0, _flux(), BatchRecord(path="A"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_NA, 35, 25.0, _flux(), BatchRecord(path="B"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C4, 6, 25.0, _flux(Mg=0.02), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C4, 6, 25.0, _flux(Mg=0.019), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C4, 20, 25.0, _flux(Mg=9.0), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C5, 14, 25.0, _flux(), BatchRecord(branch="two"), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C5, 15, 25.0, _flux(), BatchRecord(branch="two"), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C5, 10, 25.0, _flux(), BatchRecord(branch="two"), None, 1.6, 4.0, 1, False),
        (CampaignPhase.C5, 10, 25.0, _flux(), BatchRecord(branch="two"), None, 1.6, 4.0, 3, True),
        (CampaignPhase.C5, 800, 25.0, _flux(), BatchRecord(branch="two"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C5, 799, 25.0, _flux(), BatchRecord(branch="one"), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C5, 800, 25.0, _flux(), BatchRecord(branch="one"), None, 0.0, 100.0, 0, True),
        (
            CampaignPhase.C6,
            0,
            25.0,
            _flux(),
            BatchRecord(),
            {"SiO2": 10.0, "Al2O3": 7.5, "CaO": 82.5},
            0.0,
            100.0,
            0,
            False,
        ),
        (
            CampaignPhase.C6,
            0,
            25.0,
            _flux(),
            BatchRecord(),
            {"SiO2": 10.0, "Al2O3": 7.4, "CaO": 82.6},
            0.0,
            100.0,
            0,
            True,
        ),
        (CampaignPhase.C6, 20, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.MRE_BASELINE, 0, 25.0, _flux(), BatchRecord(), None, 2.44, 9.0, 2, False),
        (CampaignPhase.MRE_BASELINE, 0, 25.0, _flux(), BatchRecord(), None, 2.45, 9.0, 2, True),
        (CampaignPhase.MRE_BASELINE, 120, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
    ],
)
def test_configured_campaign_endpoints_match_legacy_trip_points(
    campaign: CampaignPhase,
    hour: int,
    temperature_C: float,
    flux: EvaporationFlux,
    record: BatchRecord,
    composition_kg: dict[str, float] | None,
    voltage_V: float,
    current_A: float,
    low_current_hours: int,
    expected: bool,
):
    manager = CampaignManager(_setpoints())
    melt = _melt(
        campaign,
        hour,
        temperature_C=temperature_C,
        composition_kg=composition_kg,
        voltage_V=voltage_V,
        current_A=current_A,
        low_current_hours=low_current_hours,
    )

    assert manager.check_endpoint(melt, flux, CondensationTrain(), record) is expected


def test_c5_low_current_endpoint_is_gated_on_final_rung():
    # The C5 cell dispatches at the stage voltage cap for EVERY ladder hold
    # (2026-07-06 rung fix), so at-cap + low-current no longer means "ladder
    # done" by itself. The endpoint's low-current signal must only count on
    # the FINAL declared rung; earlier rungs' depletion belongs to the
    # ladder-advance logic. Tri-state contract: None (no ladder bookkeeping,
    # legacy states) keeps the legacy trip; explicit False blocks it.
    manager = CampaignManager(_setpoints())
    record = BatchRecord(branch="two")

    def melt_at_cap(low_hours: int) -> MeltState:
        return _melt(
            CampaignPhase.C5,
            10,
            voltage_V=1.6,
            current_A=4.0,
            low_current_hours=low_hours,
        )

    early_rung = melt_at_cap(3)
    early_rung.mre_c5_on_final_rung = False
    assert manager.check_endpoint(early_rung, _flux(), CondensationTrain(), record) is False

    final_rung = melt_at_cap(3)
    final_rung.mre_c5_on_final_rung = True
    assert manager.check_endpoint(final_rung, _flux(), CondensationTrain(), record) is True

    legacy = melt_at_cap(3)
    assert legacy.mre_c5_on_final_rung is None
    assert manager.check_endpoint(legacy, _flux(), CondensationTrain(), record) is True

    complete = _melt(CampaignPhase.C5, 10, voltage_V=0.0, current_A=0.0)
    complete.mre_c5_ladder_complete = True
    assert manager.check_endpoint(complete, _flux(), CondensationTrain(), record) is True


def test_campaign_endpoint_caps_and_classes_are_materialized():
    campaigns = _setpoints()["campaigns"]

    assert campaigns["C0"]["max_hold_hr"] == 25
    assert campaigns["C0b_p_cleanup"]["max_hold_hr"] == 3
    assert campaigns["C2A_continuous"]["max_hold_hr"] == 30
    assert campaigns["C2A_staged"]["max_hold_hr"] == 9
    assert campaigns["C2B"]["max_hold_hr"] == 20
    assert campaigns["C3"]["max_hold_hr"]["C3_K"] == {
        "A_staged": 3,
        "A": 12,
        "default": 25,
    }
    assert campaigns["C3"]["max_hold_hr"]["C3_NA"] == {
        "A_staged": 3,
        "A": 18,
        "default": 35,
    }
    assert campaigns["C4"]["max_hold_hr"] == 20
    assert campaigns["C5"]["max_hold_hr"] == {
        "branch_two": 800,
        "branch_one": 800,
    }
    assert campaigns["mre_baseline"]["max_hold_hr"] == 120
    assert campaigns["C6"]["max_hold_hr"] == 20

    assert {
        key: campaigns[key]["termination_class"]
        for key in (
            "C0",
            "C0b_p_cleanup",
            "C2A_continuous",
            "C2A_staged",
            "C2B",
            "C3",
            "C4",
            "C5",
            "mre_baseline",
            "C6",
        )
    } == {
        "C0": "C",
        "C0b_p_cleanup": "C",
        "C2A_continuous": "C",
        "C2A_staged": "A",
        "C2B": "C",
        "C3": "A",
        "C4": "C",
        "C5": "A",
        "mre_baseline": "C",
        "C6": "A",
    }
