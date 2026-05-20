"""Unit tests for the synchronous SimSession command core."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.backends import BackendSelectionPolicy
from simulator.session import (
    DecisionPolicy,
    SimSession,
    SimSessionConfig,
    drive_auto_apply,
)
from simulator.state import CampaignPhase, DecisionPoint, DecisionType, HourSnapshot


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(name: str) -> dict:
    with (DATA_DIR / name).open() as f:
        return yaml.safe_load(f) or {}


def _config(**overrides) -> SimSessionConfig:
    values = {
        "feedstock_id": "lunar_mare_low_ti",
        "feedstocks": _load_yaml("feedstocks.yaml"),
        "setpoints": _load_yaml("setpoints.yaml"),
        "vapor_pressures": _load_yaml("vapor_pressures.yaml"),
        "campaign": "C0",
        "backend_name": "stub",
        "backend_policy": BackendSelectionPolicy.RUNNER_STRICT,
    }
    values.update(overrides)
    return SimSessionConfig(**values)


class _FakeSim:
    def __init__(
        self,
        *,
        summaries: list[dict] | None = None,
        decision_after_step: DecisionPoint | None = None,
    ) -> None:
        self.melt = SimpleNamespace(
            hour=0,
            campaign=CampaignPhase.C0,
            pO2_mbar=0.0,
            stir_factor=1.0,
        )
        self.campaign_mgr = SimpleNamespace(c4_max_temp_C=1670.0, overrides={})
        self.c4_max_temp_C = 1670.0
        self._last_backend_error = ""
        self._last_campaign_summary = None
        self.pending_decision = None
        self.paused_for_decision = False
        self.applied_decisions: list[tuple[DecisionType, str]] = []
        self._summaries = list(summaries or [])
        self._decision_after_step = decision_after_step

    def step(self) -> HourSnapshot:
        self.melt.hour += 1
        if self._summaries:
            self._last_campaign_summary = self._summaries.pop(0)
        if self._decision_after_step is not None:
            self.pending_decision = self._decision_after_step
            self.paused_for_decision = True
            self._decision_after_step = None
        return self._make_snapshot()

    def apply_decision(self, decision_type: DecisionType, choice: str) -> None:
        self.applied_decisions.append((decision_type, choice))
        self.pending_decision = None
        self.paused_for_decision = False

    def is_complete(self) -> bool:
        return False

    def product_ledger(self) -> dict[str, float]:
        return {}

    def _make_snapshot(self) -> HourSnapshot:
        return HourSnapshot(
            hour=self.melt.hour,
            campaign=self.melt.campaign,
            temperature_C=25.0 + self.melt.hour,
        )


def _fake_session(fake: _FakeSim) -> SimSession:
    session = SimSession()
    session._sim = fake
    return session


def test_start_queries_and_result_document_factory():
    session = SimSession().start(
        _config(
            result_document_factory=lambda active: {
                "hour": active.snapshot().hour,
                "campaign": active.snapshot().campaign.name,
            }
        )
    )

    assert session.pending_decision() is None
    assert not session.is_complete()
    assert session.snapshot().campaign == CampaignPhase.C0
    assert session.result_document() == {"hour": 0, "campaign": "C0"}


def test_adjust_handles_only_session_parameters_with_live_override_effects():
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    session.adjust("stir_factor", 1.25)
    session.adjust("pO2_mbar", 2.5)
    session.adjust("c4_max_temp", 1660.0)
    session.adjust("campaign_override", 1.5, campaign="C2A", field="stir_factor")
    session.adjust("campaign_override", 0.75, campaign="C2A", field="pO2_mbar")

    assert sim.melt.stir_factor == pytest.approx(1.5)
    assert sim.melt.pO2_mbar == pytest.approx(0.75)
    assert sim.c4_max_temp_C == pytest.approx(1660.0)
    assert sim.campaign_mgr.c4_max_temp_C == pytest.approx(1660.0)
    assert sim.campaign_mgr.overrides["C2A"]["stir_factor"] == pytest.approx(1.5)
    assert sim.campaign_mgr.overrides["C2A"]["pO2_mbar"] == pytest.approx(0.75)
    with pytest.raises(ValueError, match="unsupported"):
        session.adjust("speed", 0.0)


def test_advance_is_policy_free_and_surfaces_decision_without_applying():
    assert DecisionPolicy.AUTO_APPLY is not DecisionPolicy.OPERATOR
    decision = DecisionPoint(
        DecisionType.PATH_AB,
        options=["A", "B"],
        recommendation="B",
        context="choose route",
    )
    fake = _FakeSim(decision_after_step=decision)
    session = _fake_session(fake)

    result = session.advance()

    assert result.decision_event == {
        "type": "PATH_AB",
        "options": ["A", "B"],
        "recommendation": "B",
        "context": "choose route",
    }
    assert fake.pending_decision is decision
    assert fake.applied_decisions == []


def test_auto_apply_driver_applies_recommendation_before_advancing():
    decision = DecisionPoint(
        DecisionType.PATH_AB,
        options=["A", "B"],
        recommendation="B",
        context="choose route",
    )
    fake = _FakeSim()
    fake.pending_decision = decision
    fake.paused_for_decision = True
    session = _fake_session(fake)
    operator_decisions: list[dict] = []

    results = list(drive_auto_apply(session, 1, operator_decisions=operator_decisions))

    assert len(results) == 1
    assert fake.applied_decisions == [(DecisionType.PATH_AB, "B")]
    assert operator_decisions[0]["choice"] == "B"
    assert operator_decisions[0]["recommendation"] == "B"


def test_consecutive_campaign_summaries_are_captured_and_cleared_in_order():
    fake = _FakeSim(
        summaries=[
            {"campaign": "C0", "hour": 1},
            {"campaign": "C0B", "hour": 2},
        ]
    )
    session = _fake_session(fake)

    first = session.advance()
    second = session.advance()

    assert [first.campaign_summary, second.campaign_summary] == [
        {"campaign": "C0", "hour": 1},
        {"campaign": "C0B", "hour": 2},
    ]
    assert fake._last_campaign_summary is None


def test_mre_baseline_track_start_tags_track_without_jumping_campaign():
    session = SimSession().start(_config(campaign="C0", track="mre_baseline"))

    assert session.simulator.record.track == "mre_baseline"
    assert session.simulator.melt.campaign == CampaignPhase.C0
    assert session.simulator.melt.campaign != CampaignPhase.MRE_BASELINE


def test_pause_resume_are_result_neutral_pacing_flags():
    paused = SimSession().start(_config())
    unpaused = SimSession().start(_config())
    paused_results = []
    unpaused_results = []

    for _ in range(3):
        paused.pause()
        paused.resume()
        paused_results.append(paused.advance().per_hour_summary)
        unpaused_results.append(unpaused.advance().per_hour_summary)

    assert paused_results == unpaused_results
