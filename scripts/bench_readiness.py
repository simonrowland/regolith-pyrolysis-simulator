#!/usr/bin/env python3
"""Print per-source Bench consumer readiness as deterministic JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.migrate import (  # noqa: E402
    load_migrated_benches,
    load_migrated_store,
    to_plain,
)
from simulator.battery.waypoints import (  # noqa: E402
    ConsumerReadiness,
    GapReason,
    ReadinessGap,
    ReadinessStatus,
    consumer_readiness,
)


def _missing_bench_readiness() -> tuple[ConsumerReadiness, ...]:
    gap = ReadinessGap("bench", GapReason.MISSING_EVIDENCE, ("experiment.bench_id",))
    return tuple(
        ConsumerReadiness(consumer, ReadinessStatus.GAP, (gap,))
        for consumer in ("kems", "rps", "engine_point")
    )


def _aggregate(items: list[tuple[ConsumerReadiness, ...]]) -> tuple[ConsumerReadiness, ...]:
    aggregated = []
    for consumer in ("kems", "rps", "engine_point"):
        records = [
            item
            for group in items
            for item in group
            if item.consumer == consumer
        ]
        gaps = tuple(gap for item in records for gap in item.gaps)
        if any(item.status is ReadinessStatus.GAP for item in records):
            status = ReadinessStatus.GAP
        elif any(item.status is ReadinessStatus.READY for item in records):
            status = ReadinessStatus.READY
        else:
            status = ReadinessStatus.NOT_APPLICABLE
        aggregated.append(ConsumerReadiness(consumer, status, gaps))
    return tuple(aggregated)


def report(root: Path) -> dict[str, object]:
    works, experiments, _ = load_migrated_store(root)
    benches = load_migrated_benches(root)
    by_source: dict[str, list[dict[str, object]]] = {}
    source_readiness: dict[str, list[tuple[ConsumerReadiness, ...]]] = {}
    summary: dict[str, dict[str, int]] = {}
    for experiment in sorted(experiments.values(), key=lambda item: item.experiment_id):
        work = works.get(experiment.work_id or "")
        source_ids = work.source_ids if work is not None else (experiment.work_id or "unknown",)
        bench = benches.get(experiment.bench_id or "")
        readiness = (
            _missing_bench_readiness()
            if bench is None
            else consumer_readiness(experiment, bench)
        )
        for source_id in source_ids:
            by_source.setdefault(source_id, []).append(
                {
                    "work_id": experiment.work_id,
                    "experiment_id": experiment.experiment_id,
                    "bench_id": experiment.bench_id,
                    "consumers": to_plain(readiness),
                }
            )
            source_readiness.setdefault(source_id, []).append(readiness)
    rows: list[dict[str, object]] = []
    for source_id in sorted(by_source):
        readiness = _aggregate(source_readiness[source_id])
        rows.append(
            {
                "source_id": source_id,
                "consumers": to_plain(readiness),
                "experiments": by_source[source_id],
            }
        )
        for item in readiness:
            counts = summary.setdefault(item.consumer, {})
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
    return {
        "schema_version": "bench_readiness.v1",
        "sources": rows,
        "summary": summary,
        "source_count": len(rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    print(json.dumps(report(args.root), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
