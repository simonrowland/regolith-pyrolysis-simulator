from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.state import CampaignPhase
from tests.chemistry.conftest import _build_sim


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _diagnostic_sim():
    setpoints = deepcopy(_load_yaml("setpoints.yaml"))
    setpoints["freeze_gate"] = dict(setpoints.get("freeze_gate", {}) or {})
    setpoints["freeze_gate"]["enabled"] = False
    c2a = setpoints["campaigns"]["C2A_continuous"]
    c2a["target_yield_threshold"] = 0.99
    c2a["max_hold_hr"] = 99
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        setpoints,
    )
    sim.start_campaign(CampaignPhase.C2A)
    return sim


def test_step_emits_extraction_completeness_side_channel() -> None:
    sim = _diagnostic_sim()

    sim.step()

    diag = sim._last_extraction_completeness_diagnostic
    assert diag["campaign"] == "C2A"
    assert "SiO" in diag["completeness_by_target_species"]
    assert diag["completeness_by_target_species"]["SiO"] is not None
    assert diag["would_be_soft_advance_by_target_species"]["SiO"][
        "would_advance"
    ] is False
    assert diag["would_be_hard_floor_advance"] is None
    assert diag["would_be_cap_advance"] is False
    assert "extraction_completeness" not in sim.record.snapshots[-1].__dict__


def test_completeness_diagnostic_does_not_change_campaign_advancement() -> None:
    with_diagnostic = _diagnostic_sim()
    without_diagnostic = _diagnostic_sim()
    without_diagnostic._update_extraction_completeness_diagnostic = lambda: None
    for sim in (with_diagnostic, without_diagnostic):
        sim.melt.campaign_hour = 30

    with_diagnostic.step()
    without_diagnostic.step()

    assert (
        with_diagnostic.melt.campaign,
        with_diagnostic.melt.hour,
        with_diagnostic.melt.campaign_hour,
        len(with_diagnostic.record.snapshots),
        with_diagnostic.paused_for_decision,
    ) == (
        without_diagnostic.melt.campaign,
        without_diagnostic.melt.hour,
        without_diagnostic.melt.campaign_hour,
        len(without_diagnostic.record.snapshots),
        without_diagnostic.paused_for_decision,
    )
    assert (
        with_diagnostic._last_extraction_completeness_diagnostic["campaign"]
        == "C2A"
    )
