#!/usr/bin/env python3
"""Freeze a real runner payload into the W-A0 run-artifact envelope — the DEMO sample the viewer reads.

Delegates to the canonical, contract-fixed `simulator.accounting.run_artifact.build_run_artifact` (single
source of truth — do NOT re-implement the reshape here). The artifact builder
emits the same canonical two-price cost block used by live runs.

Usage: python3 freeze_sample.py <payload.json> <out.json>
"""
import json
import sys

from simulator.accounting.run_artifact import build_run_artifact

def main():
    src, out = sys.argv[1], sys.argv[2]
    with open(src) as f:
        payload = json.load(f)
    artifact = build_run_artifact(
        payload,
        run_id="sample-fullseq-lunar-197h",
        name="Full-sequence lunar (C0->C6, demo)",
    )
    with open(out, "w") as f:
        json.dump(artifact, f, indent=1)
    print(f"wrote {out}: status={artifact['execution_status']} timesteps={len(artifact['timesteps'])} "
          f"recipe_snapshot={'present' if 'recipe_snapshot' in artifact['header'] else 'omitted'}")


if __name__ == "__main__":
    main()
