"""Early-melt Stage-0 harness (chunk H1)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.backends import BackendSelectionPolicy
from simulator.core import PyrolysisSimulator
from simulator.session import SimSession, SimSessionConfig
from simulator.stage0_harness import (
    FOULANT_GROUPS,
    Stage0HarnessError,
    default_max_stage0_hours,
    run_stage0_harness,
    run_stage0_harness_from_config,
)
from simulator.state import CampaignPhase

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(name: str) -> dict:
    with (DATA_DIR / name).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _feedstocks(*, include_debug: bool = False) -> dict:
    feedstocks = _load_yaml("feedstocks.yaml")
    if include_debug:
        feedstocks.update(_load_yaml("debug_feedstocks.yaml"))
    return feedstocks


def _session_config(feedstock_id: str, **overrides) -> SimSessionConfig:
    values = {
        "feedstock_id": feedstock_id,
        "feedstocks": _feedstocks(),
        "setpoints": _load_yaml("setpoints.yaml"),
        "vapor_pressures": _load_yaml("vapor_pressures.yaml"),
        "campaign": "C0",
        "backend_name": "stub",
        "backend_policy": BackendSelectionPolicy.RUNNER_STRICT,
    }
    if feedstock_id == "mars_sulfate_rich":
        fs = values["feedstocks"][feedstock_id]
        values["additives_kg"] = {
            "C": PyrolysisSimulator._carbon_reductant_required_kg(fs, 1000.0),
        }
    values.update(overrides)
    return SimSessionConfig(**values)


def test_default_max_stage0_hours_derives_from_setpoints():
    setpoints = _load_yaml("setpoints.yaml")
    expected = (
        float(setpoints["campaigns"]["C0"]["max_hold_hr"])
        + float(setpoints["campaigns"]["C0b_p_cleanup"]["max_hold_hr"])
        + 8.0
    )
    assert default_max_stage0_hours(setpoints) == pytest.approx(expected)


def test_real_feedstock_stops_at_c0b_path_ab_pause():
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    result = run_stage0_harness(session)

    assert result.early_melt_reached is True
    assert result.stop_reason == "c0b_path_ab_pause"
    assert result.total_hours < 150
    assert session.simulator.melt.campaign == CampaignPhase.C0B
    assert session.simulator.paused_for_decision is True
    assert result.cleaned_melt_kg
    assert result.verdicts is None


def test_debug_feedstock_stops_on_campaign_leave():
    session = SimSession().start(
        _session_config(
            "debug_pure_feo",
            feedstocks=_feedstocks(include_debug=True),
        )
    )
    result = run_stage0_harness(session)

    assert result.stop_reason == "campaign_left_stage0"
    assert session.simulator.melt.campaign == CampaignPhase.C2A
    assert result.total_hours < 150


def test_max_stage0_hours_guard_fails_loud():
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    with pytest.raises(Stage0HarnessError) as excinfo:
        run_stage0_harness(session, max_stage0_hours=1.0)

    assert excinfo.value.reason == "stage0_did_not_converge"
    assert session.simulator.melt.campaign in (
        CampaignPhase.C0,
        CampaignPhase.C0B,
    )


def test_disposition_timeline_grouped_and_ratified_phases():
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    result = run_stage0_harness(session)

    assert result.disposition_timeline
    for entry in result.disposition_timeline:
        assert set(entry.by_group) == set(FOULANT_GROUPS)
        if entry.campaign == "C0":
            assert entry.stage0_phase == "phase_2_vacuum"
            assert entry.ratified_ceiling_C == pytest.approx(1350.0)
        elif entry.campaign == "C0B":
            assert entry.stage0_phase == "phase_1_oxidizing"
            assert entry.ratified_ceiling_C == pytest.approx(1050.0)


@pytest.mark.parametrize("feedstock_key", ["mars_sulfate_rich", "ci_carbonaceous_chondrite"])
def test_messy_feedstock_produces_nonempty_bakeoff_timeline(feedstock_key):
    result = run_stage0_harness_from_config(_session_config(feedstock_key))

    assert result.disposition_timeline
    has_group_event = any(
        any(events for events in entry.by_group.values())
        for entry in result.disposition_timeline
    )
    assert has_group_event


def test_mars_sulfate_diagnostic_splits_land_in_timeline():
    result = run_stage0_harness_from_config(_session_config("mars_sulfate_rich"))

    mineral_events = [
        event
        for entry in result.disposition_timeline
        for event in entry.by_group["other_mineral_contaminant"]
    ]
    sulfate_events = [
        event for event in mineral_events
        if event.get("reaction_family") == "sulfate_decomp"
    ]
    assert sulfate_events
    assert any(event.get("source") == "diagnostic" for event in sulfate_events)


def test_cleaned_melt_matches_ledger_projection():
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    result = run_stage0_harness(session)
    sim = session.simulator
    sim._project_cleaned_melt_from_atom_ledger()
    ledger = sim.atom_ledger.kg_by_account("process.cleaned_melt")

    for species, kg in result.cleaned_melt_kg.items():
        assert ledger[species] == pytest.approx(kg, rel=0.0, abs=1e-12)