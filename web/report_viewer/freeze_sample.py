#!/usr/bin/env python3
"""Freeze a real runner payload into the W-A0 run-artifact envelope — the DEMO sample the viewer reads.

Delegates to the canonical, contract-fixed `simulator.accounting.run_artifact.build_run_artifact` (single
source of truth — do NOT re-implement the reshape here). The artifact builder
emits the same canonical two-price cost block used by live runs.

Usage: python3 freeze_sample.py <payload.json> <out.json> [recipe.yaml]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.accounting.run_artifact import build_run_artifact
from simulator.optimize.recipe import recipe_schema_version
from simulator.recipe_io import load_recipe_patch

def main():
    src, out = sys.argv[1], sys.argv[2]
    with open(src) as f:
        payload = json.load(f)
    if len(sys.argv) > 3:
        payload["recipe_snapshot"] = {
            "setpoints_patch": load_recipe_patch(Path(sys.argv[3])),
            "pins": [],
            "recipe_schema_version": recipe_schema_version,
        }
    artifact = build_run_artifact(
        payload,
        run_id="canonical-lunar-full-yield",
        name="Canonical lunar full-yield pyrolysis demo",
    )
    with open(out, "w") as f:
        json.dump(artifact, f, indent=1)
    print(f"wrote {out}: status={artifact['execution_status']} timesteps={len(artifact['timesteps'])} "
          f"recipe_snapshot={'present' if 'recipe_snapshot' in artifact['header'] else 'omitted'}")


if __name__ == "__main__":
    main()
