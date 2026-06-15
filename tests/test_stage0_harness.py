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
    _capture_cleaned_melt_kg,
    default_max_stage0_hours,
    run_stage0_harness,
    run_stage0_harness_from_config,
)
from simulator.optimize.evalspec import REQUIRED_DATA_DIGEST_KEYS
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
    assert result.verdicts is not None
    assert result.verdicts["verdict_a"]["warn_only"] is True


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


def test_comet_runtime_emits_uncertain_carbon_partition_interval():
    result = run_stage0_harness_from_config(_session_config("comet_nucleus"))

    events = [
        event
        for entry in result.disposition_timeline
        for event in entry.by_group["refractory_carbon"]
        if event.get("reaction_family") == "partition_carbon"
    ]
    uncertain = [
        event
        for event in events
        if event.get("disposition") == "uncertain_partition"
    ]

    assert uncertain
    event = uncertain[0]
    assert event["interval_required"] is True
    assert event["feed_kg"] > 0.0
    assert event["declared_c_mol"] > 0.0
    assert event["declared_C_kg"] > 0.0
    assert event["refractory_fraction_interval"] == [0.0, 1.0]
    assert event["refractory_C_mol_interval"] == pytest.approx([
        0.0,
        event["declared_c_mol"],
    ])
    assert "burned_kg" not in event
    assert "refractory_C_kg" not in event


def test_carbon_burned_mass_uses_declared_c_basis_not_carrier_kg():
    session = SimSession().start(_session_config("ci_carbonaceous_chondrite"))
    result = run_stage0_harness(session)

    burned_events = [
        event
        for entry in result.disposition_timeline
        for event in entry.by_group["trapped_gasses"]
        if event.get("reaction_family") == "partition_carbon"
        and event.get("disposition") == "burned"
    ]
    residual_events = [
        event
        for entry in result.disposition_timeline
        for event in entry.by_group["refractory_carbon"]
        if event.get("reaction_family") == "partition_carbon"
        and event.get("disposition") == "residual"
    ]

    assert burned_events
    assert residual_events
    burned = burned_events[0]
    residual = residual_events[0]

    assert burned["mass_basis"] == "declared_C"
    assert burned["burned_kg"] == pytest.approx(burned["burned_C_kg"])
    assert burned["burned_kg"] == pytest.approx(burned["labile_C_kg"])
    assert burned["burned_kg"] < burned["feed_kg"]
    assert burned["labile_carrier_equivalent_kg"] < burned["feed_kg"]

    assert residual["mass_basis"] == "declared_C"
    assert residual["refractory_mol"] > 0.0
    assert residual["refractory_residual_mol"] > 0.0
    assert residual["refractory_C_kg"] > 0.0
    assert residual["refractory_residual_C_kg"] > 0.0
    assert residual["refractory_residual_C_kg"] <= residual["refractory_C_kg"]
    ledger = session.simulator.atom_ledger.kg_by_account("process.cleaned_melt")
    for species, kg in result.cleaned_melt_kg.items():
        assert ledger[species] == pytest.approx(kg, rel=0.0, abs=1e-12)
    assert "stage0_carbon_partition" not in REQUIRED_DATA_DIGEST_KEYS


def test_cleaned_melt_matches_ledger_projection():
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    result = run_stage0_harness(session)
    ledger = session.simulator.atom_ledger.kg_by_account("process.cleaned_melt")

    for species, kg in result.cleaned_melt_kg.items():
        assert ledger[species] == pytest.approx(kg, rel=0.0, abs=1e-12)


def test_capture_cleaned_melt_does_not_mutate_melt_state():
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    session.advance()
    sim = session.simulator
    prior_comp = dict(sim.melt.composition_kg)
    prior_oxide = dict(sim.inventory.melt_oxide_kg)
    prior_total = sim.melt.total_mass_kg

    _capture_cleaned_melt_kg(sim)

    assert sim.melt.composition_kg == prior_comp
    assert sim.inventory.melt_oxide_kg == prior_oxide
    assert sim.melt.total_mass_kg == prior_total


def test_disposition_timeline_assigns_by_campaign_phase():
    result = run_stage0_harness_from_config(
        _session_config("ci_carbonaceous_chondrite"),
    )

    hours_with_diag = [
        entry.hour
        for entry in result.disposition_timeline
        if any(events for events in entry.by_group.values())
    ]
    assert len(hours_with_diag) >= 2
    assert len(set(hours_with_diag)) >= 2


def test_harness_shadow_parity_with_full_run_truncated():
    config = _session_config("lunar_mare_low_ti")
    harness_result = run_stage0_harness_from_config(config)

    session = SimSession().start(config)
    for _ in range(harness_result.total_hours):
        session.advance()

    sim = session.simulator
    assert sim.melt.hour == harness_result.total_hours
    assert _capture_cleaned_melt_kg(sim) == harness_result.cleaned_melt_kg
    assert sim.melt.campaign.name == "C0B"
    assert harness_result.stop_reason == "c0b_path_ab_pause"
