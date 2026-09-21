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
from simulator.battery.enums import BenchIdentityBasis  # noqa: E402
from simulator.battery.records import (  # noqa: E402
    ApparatusGeometry,
    Bench,
    BenchIdentity,
    Located,
    State,
)
from simulator.battery.waypoints import (  # noqa: E402
    ConsumerReadiness,
    ENGINE_POINT_CONSUMERS,
    GapReason,
    ReadinessGap,
    ReadinessStatus,
    consumer_readiness,
)


def _missing_bench_readiness() -> tuple[ConsumerReadiness, ...]:
    gap = ReadinessGap("bench", GapReason.MISSING_EVIDENCE, ("experiment.bench_id",))
    return (
        ConsumerReadiness("kems", ReadinessStatus.GAP, (gap,)),
        ConsumerReadiness("rps", ReadinessStatus.GAP, (gap,)),
        *(
            ConsumerReadiness(
                "engine_point", ReadinessStatus.GAP, (gap,), engine
            )
            for engine in ENGINE_POINT_CONSUMERS
        ),
    )


def _implicit_bench(experiment) -> Bench:
    apparatus = experiment.apparatus
    pumping = experiment.pressure_environment.pumping or {}
    pumping_speed = pumping.get("pumping_speed_m3_s")
    if pumping_speed is not None and not pumping_speed.state.is_value:
        pumping_speed = None
    method = None
    if experiment.method.is_value:
        method = Located(State.of(experiment.method.value.value))
    geometry = None
    if apparatus is not None and apparatus.geometry is not None:
        raw_geometry = apparatus.geometry
        scalar_names = (
            "orifice_area_m2",
            "orifice_diameter_m",
            "clausing_factor",
            "orifice_to_sample_area_ratio",
            "exposed_area_m2",
            "chamber_length_m",
            "chamber_volume_m3",
            "orifice_channel_length_m",
            "orifice_count",
            "orifice_shape",
        )
        kwargs = {
            name: value
            for name in scalar_names
            if (value := getattr(raw_geometry, name)) is not None
            and value.state.is_value
        }
        for name in ("cell_internal_dimensions", "chamber_dimensions"):
            values = getattr(raw_geometry, name) or {}
            kept = {
                key: value for key, value in values.items() if value.state.is_value
            }
            if kept:
                kwargs[name] = kept
        if kwargs:
            geometry = ApparatusGeometry(**kwargs)
    return Bench(
        id=f"{experiment.experiment_id}::bench::implicit",
        work_id=experiment.work_id or "unknown",
        identity=BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
        method=method,
        geometry=geometry,
        pumping_speed_m3_s=pumping_speed,
    )


def _status(items: list[ConsumerReadiness]) -> ReadinessStatus:
    statuses = {item.status for item in items}
    if len(statuses) == 1:
        return next(iter(statuses))
    return ReadinessStatus.PARTIAL


def _deduplicated_gaps(
    records: list[tuple[str, ConsumerReadiness]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, tuple[str, ...]], set[str]] = {}
    for experiment_id, item in records:
        seen: set[tuple[str, str, tuple[str, ...]]] = set()
        for gap in item.gaps:
            key = (gap.waypoint, gap.reason.value, gap.missing)
            if key in seen:
                continue
            seen.add(key)
            grouped.setdefault(key, set()).add(experiment_id)
    return [
        {
            "waypoint": waypoint,
            "reason": reason,
            "missing": list(missing),
            "count": len(experiment_ids),
            "experiment_ids": sorted(experiment_ids),
        }
        for (waypoint, reason, missing), experiment_ids in sorted(grouped.items())
    ]


def _aggregate(
    items: list[tuple[str, tuple[ConsumerReadiness, ...]]],
    *,
    engine: str | None = None,
) -> dict[str, object]:
    selected = [
        (experiment_id, item)
        for experiment_id, group in items
        for item in group
        if item.engine == engine
        and (engine is None or item.consumer == "engine_point")
    ]
    records = [item for _, item in selected]
    return {
        "consumer": "engine_point" if engine else records[0].consumer,
        **({"engine": engine} if engine else {}),
        "status": _status(records).value,
        "gaps": _deduplicated_gaps(selected),
    }


def _collapse_engines(group: tuple[ConsumerReadiness, ...]) -> ConsumerReadiness:
    engines = [item for item in group if item.engine is not None]
    gaps = []
    seen = set()
    for item in engines:
        for gap in item.gaps:
            key = (gap.waypoint, gap.reason, gap.missing)
            if key not in seen:
                seen.add(key)
                gaps.append(gap)
    return ConsumerReadiness("engine_point", _status(engines), tuple(gaps))


def report(root: Path) -> dict[str, object]:
    works, experiments, _ = load_migrated_store(root)
    benches = load_migrated_benches(root)
    by_source: dict[str, list[dict[str, object]]] = {}
    source_readiness: dict[
        str, list[tuple[str, tuple[ConsumerReadiness, ...]]]
    ] = {}
    source_information: dict[str, set[str]] = {}
    for experiment in sorted(experiments.values(), key=lambda item: item.experiment_id):
        work = works.get(experiment.work_id or "")
        source_ids = work.source_ids if work is not None else (experiment.work_id or "unknown",)
        bench = benches.get(experiment.bench_id or "")
        implicit = experiment.bench_id is None
        if implicit:
            bench = _implicit_bench(experiment)
        readiness = (
            _missing_bench_readiness()
            if bench is None
            else consumer_readiness(experiment, bench)
        )
        consumers = tuple(item for item in readiness if item.engine is None)
        consumers += (_collapse_engines(readiness),)
        engines = tuple(item for item in readiness if item.engine is not None)
        for source_id in source_ids:
            by_source.setdefault(source_id, []).append(
                {
                    "work_id": experiment.work_id,
                    "experiment_id": experiment.experiment_id,
                    "bench_id": experiment.bench_id,
                    "consumers": to_plain(consumers),
                    "engines": to_plain(engines),
                    "informational_gaps": (
                        [
                            {
                                "waypoint": "bench_link",
                                "reason": "implicit_legacy_bench",
                                "missing": ["experiment.bench_id"],
                            }
                        ]
                        if implicit
                        else []
                    ),
                }
            )
            source_readiness.setdefault(source_id, []).append(
                (experiment.experiment_id, readiness)
            )
            if implicit:
                source_information.setdefault(source_id, set()).add(
                    experiment.experiment_id
                )
    rows: list[dict[str, object]] = []
    empty_counts = {status.value: 0 for status in ReadinessStatus}
    by_consumer: dict[str, dict[str, int]] = {
        consumer: dict(empty_counts) for consumer in ("kems", "rps", "engine_point")
    }
    by_engine: dict[str, dict[str, int]] = {
        engine: dict(empty_counts) for engine in ENGINE_POINT_CONSUMERS
    }
    blockers: dict[tuple[str, str], dict[str, set[str]]] = {}
    for source_id in sorted(by_source):
        source_items = source_readiness[source_id]
        consumer_rows = []
        for consumer in ("kems", "rps"):
            subset = [
                (experiment_id, tuple(item for item in group if item.consumer == consumer))
                for experiment_id, group in source_items
            ]
            consumer_rows.append(_aggregate(subset))
        collapsed = [
            (experiment_id, (_collapse_engines(group),))
            for experiment_id, group in source_items
        ]
        consumer_rows.append(_aggregate(collapsed))
        engine_rows = [
            _aggregate(source_items, engine=engine)
            for engine in ENGINE_POINT_CONSUMERS
        ]
        info_ids = sorted(source_information.get(source_id, set()))
        rows.append(
            {
                "source_id": source_id,
                "consumers": consumer_rows,
                "engines": engine_rows,
                "informational_gaps": (
                    [
                        {
                            "waypoint": "bench_link",
                            "reason": "implicit_legacy_bench",
                            "missing": ["experiment.bench_id"],
                            "count": len(info_ids),
                            "experiment_ids": info_ids,
                        }
                    ]
                    if info_ids
                    else []
                ),
                "experiments": by_source[source_id],
            }
        )
        for item in consumer_rows:
            counts = by_consumer[str(item["consumer"])]
            status = str(item["status"])
            counts[status] = counts.get(status, 0) + 1
        for item in engine_rows:
            counts = by_engine[str(item["engine"])]
            status = str(item["status"])
            counts[status] = counts.get(status, 0) + 1
        for experiment_id, group in source_items:
            seen = set()
            for item in group:
                if item.status is not ReadinessStatus.GAP:
                    continue
                for gap in item.gaps:
                    key = (gap.waypoint, gap.reason.value)
                    if key in seen:
                        continue
                    seen.add(key)
                    entry = blockers.setdefault(
                        key, {"sources": set(), "experiments": set()}
                    )
                    entry["sources"].add(source_id)
                    entry["experiments"].add(experiment_id)
    top_blockers = [
        {
            "waypoint": waypoint,
            "reason": reason,
            "source_count": len(ids["sources"]),
            "experiment_count": len(ids["experiments"]),
        }
        for (waypoint, reason), ids in sorted(
            blockers.items(),
            key=lambda item: (
                -len(item[1]["sources"]),
                -len(item[1]["experiments"]),
                item[0],
            ),
        )[:10]
    ]
    return {
        "schema_version": "bench_readiness.v2",
        "sources": rows,
        "summary": {
            "by_consumer": by_consumer,
            "by_engine": by_engine,
            "top_blocking_waypoints": top_blockers,
        },
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
