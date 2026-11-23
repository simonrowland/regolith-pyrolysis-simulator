#!/usr/bin/env python3
"""Count scored equilibrium phase-composition rows per source on this tree.

No arguments. Run it with the seat (car or base) as the working directory.
The import root is that directory. Prints one JSON object the controller
can diff. Extracts without an equilibrium phase-composition row are
skipped. A missing extracts directory raises. An empty corpus still
prints and exits 0. A predict failure propagates. A migration failure is
recorded on that source and is not scored.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    root = Path.cwd()
    sys.path.insert(0, str(root))

    from simulator.battery.enums import ExecutionState, Quantity
    from simulator.battery.score import SCORE_ENGINE_SET, predict_with_engine
    from tests.battery.factories import migrate_one_extract

    extracts = root / "data" / "literature" / "extracts"
    if not extracts.is_dir():
        raise FileNotFoundError(extracts)

    sources = []
    totals_rows = 0
    totals_scored = 0
    totals_produced: Counter[str] = Counter()
    for path in sorted(extracts.glob("*.yaml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "quantity: equilibrium_phase_composition" not in text:
            continue
        source_id = path.stem
        try:
            migrator = migrate_one_extract(root, path, index={}, aliases={})
            migrator.finalize()
        except Exception as exc:
            sources.append(
                {
                    "source_id": source_id,
                    "migration_error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        result = migrator.result
        rows = []
        for observation in result.observations.values():
            quantity = observation.identity.quantity
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
        totals_rows += len(rows)
        totals_scored += scored
        totals_produced.update(produced_by_engine)
        sources.append(
            {
                "source_id": source_id,
                "phase_composition_rows": len(rows),
                "scored_observations": scored,
                "produced_by_engine": dict(produced_by_engine),
                "refusal_counts": dict(refusal_counts),
            }
        )

    payload = {
        "sources": sources,
        "totals": {
            "phase_composition_rows": totals_rows,
            "produced_by_engine": dict(totals_produced),
            "scored_observations": totals_scored,
            "sources": len(sources),
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
