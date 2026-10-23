"""The coating golden's producer and cache-counter projection."""

import json

from simulator.runner import PyrolysisRun


def produce_coating_golden() -> tuple[bytes, dict]:
    run = PyrolysisRun(
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
    )
    payload = run.run()
    if payload["status"] != "ok":
        raise RuntimeError(f"coating golden producer refused: {payload.get('reason', payload['status'])}")
    # b-238: this counter depends on the cross-run freeze-gate cache. Only
    # this block is excluded; the consumer separately checks its shape.
    engagement = payload.pop("melt_redox_gate_floor_fallback_engagement")
    preimage = (
        json.dumps(payload, indent=2, sort_keys=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    return preimage, engagement
