"""Owner d-247: C0 valve window and C0B routing, with pre-change pins."""

import json
from pathlib import Path

import pytest

from simulator.runner import PyrolysisRun


PIN = Path(__file__).parent / "fixtures" / "c0_routing_pin.json"


def routing_run(feedstock):
    run = PyrolysisRun(
        feedstock_id=feedstock, campaign="C0", hours=24, additives_kg={},
        allow_fallback_vapor=True, allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "t1464-pin",
        },
    )
    session = run._start_session()
    payload = run._run_session(session)
    sim = session.simulator
    return payload, sim


def routing_values(payload, sim):
    return {
        "status": payload["status"],
        "final_state": payload["final_state"],
        "campaigns": [row["campaign"] for row in payload["per_hour_summary"]],
        "stages_kg": {
            str(stage.stage_number): dict(stage.collected_kg)
            for stage in sim.train.stages
        },
        "gate_sealed": sim.train.volatiles_gate_sealed,
    }


@pytest.mark.parametrize("feedstock", [
    "lunar_mare_low_ti", "ci_carbonaceous_chondrite",
])
def test_pre_valve_routing_pin(feedstock):
    """Historical numeric operand; retained across the owner value move."""
    payload, sim = routing_run(feedstock)
    expected = json.loads(PIN.read_text())[feedstock]
    assert routing_values(payload, sim) == expected
