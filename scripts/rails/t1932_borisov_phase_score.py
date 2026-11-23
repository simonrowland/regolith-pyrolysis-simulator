#!/usr/bin/env python3
"""Score Borisov & Veksler included experimental phase rows on this tree.

No arguments. The import root is the current working directory. Prints one
JSON object: sheet counts for the whole extract, and for the included
experimental sheet only, how many equilibrium phase-composition rows
produced a value on each engine in SCORE_ENGINE_SET. Other sheets are
counted and not scored. A missing extract raises. An empty experimental
list still prints and exits 0.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


SOURCE_ID = "borisov-veksler-2021-k-fe-supplement"
EXPERIMENTAL_SHEET = "included experimental data"


def main() -> int:
    root = Path.cwd()
    sys.path.insert(0, str(root))

    from simulator.battery.enums import ExecutionState, Quantity
    from simulator.battery.score import SCORE_ENGINE_SET, predict_with_engine
    from tests.battery.factories import migrate_one_extract

    extract = (
        root / "data" / "literature" / "extracts" / f"{SOURCE_ID}.yaml"
    )
    if not extract.is_file():
        raise FileNotFoundError(extract)

    migrator = migrate_one_extract(root, extract, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    prefix = f"{SOURCE_ID}::"
    sheet_counts: Counter[str] = Counter()
    rows = []
    for observation in result.observations.values():
        if not observation.observation_id.startswith(prefix):
            continue
        table = observation.locator.table or ""
        sheet_counts[table] += 1
        quantity = observation.identity.quantity
        if table != EXPERIMENTAL_SHEET:
            continue
        if (
            quantity is None
            or not quantity.is_value
            or quantity.value is not Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        ):
            continue
        rows.append(observation)

    produced_by_engine: Counter[str] = Counter()
    refusal_counts: Counter[str] = Counter()
    scored = 0
    for observation in rows:
        experiment = result.experiments.get(observation.experiment_id)
        any_produced = False
        for engine in SCORE_ENGINE_SET:
            prediction = predict_with_engine(
                engine, observation, experiment=experiment
            )
            if prediction.execution.state is ExecutionState.PRODUCED:
                produced_by_engine[engine.value] += 1
                any_produced = True
                continue
            reason = prediction.refusal_reason
            token = reason.value if reason is not None else "none"
            refusal_counts[f"{engine.value}:{token}"] += 1
        if any_produced:
            scored += 1

    payload = {
        "source_id": SOURCE_ID,
        "experimental_sheet": EXPERIMENTAL_SHEET,
        "sheet_counts": dict(sheet_counts),
        "experimental_phase_composition_rows": len(rows),
        "scored_observations": scored,
        "produced_by_engine": dict(produced_by_engine),
        "refusal_counts": dict(refusal_counts),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
