#!/usr/bin/env python3
"""Freeze a real runner payload into the W-A0 run-artifact envelope — the DEMO sample the viewer reads.

Delegates to the canonical, contract-fixed `simulator.accounting.run_artifact.build_run_artifact` (single
source of truth — do NOT re-implement the reshape here). Then injects a DEMO two-price cost_block so the
settings inspector / cost panel can be demonstrated; no backend emits cost_block yet — production
emission arrives with W-A5a (held for golden coordination).

Usage: python3 freeze_sample.py <payload.json> <out.json>
"""
import json
import sys

from simulator.accounting.run_artifact import build_run_artifact

# Owner-ratified two-price energy model (T-7) — DEMO defaults so the cost panel renders. Production: W-A5a.
DEMO_COST_BLOCK = {
    "electrical_cost_per_kWh": 10.00,
    "solar_heat_cost_per_kWh": 0.05,
    "_provenance": "demo defaults (owner T-7); production values from W-A5a",
}


def main():
    src, out = sys.argv[1], sys.argv[2]
    with open(src) as f:
        payload = json.load(f)
    artifact = build_run_artifact(
        payload,
        run_id="sample-fullseq-lunar-197h",
        name="Full-sequence lunar (C0->C6, demo)",
    )
    # DEMO-only enrichment (clearly provenance-tagged): the real payload has no cost_block.
    artifact["header"]["cost_block"] = DEMO_COST_BLOCK
    with open(out, "w") as f:
        json.dump(artifact, f, indent=1)
    print(f"wrote {out}: status={artifact['execution_status']} timesteps={len(artifact['timesteps'])} "
          f"recipe_snapshot={'present' if 'recipe_snapshot' in artifact['header'] else 'omitted'}")


if __name__ == "__main__":
    main()
