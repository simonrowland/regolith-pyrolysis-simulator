"""Shared producer for the coating diagnostic test and its regenerator."""

from __future__ import annotations

import json

from simulator.runner import PyrolysisRun


def produce_default_coating_diagnostic() -> tuple[bytes, dict[str, object]]:
    payload = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=24,
        additives_kg={},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "goal-18-fixture",
        },
    ).run()

    engagement = payload.pop("melt_redox_gate_floor_fallback_engagement")
    actual_bytes = (
        json.dumps(
            payload,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return actual_bytes, engagement
