#!/usr/bin/env python3
"""Measure off/cold/warm battery scoring on fixed real-engine slices."""

from __future__ import annotations

import argparse
import json
import os
import resource
import sqlite3
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.score import (  # noqa: E402
    _score_store_to_jsonl,
    load_score_context,
)
import simulator.battery.score as battery_score  # noqa: E402
from simulator.battery.enums import Engine, Rail  # noqa: E402
from simulator.battery.migrate import (  # noqa: E402
    iter_observation_store_paths,
    iter_work_store_paths,
    load_aliases,
    load_yaml,
    path_matches_source_tokens,
    source_filter_tokens,
    work_filename,
)
from scripts.battery_cache_equivalence import _canonical_rows  # noqa: E402


@dataclass(frozen=True)
class SmokeSlice:
    engine: Engine
    source: str
    rail: Rail
    observation_match: str | None = None


# Non-refused rows from the green whole-store census. Match each observation
# exactly so a row that refuses before its adapter cannot satisfy the cache gate.
SMOKE_SLICES = (
    SmokeSlice(
        Engine.ALPHAMELTS,
        "nazarov-2015-dhofar-silicides",
        Rail.SIO_EVOLUTION,
        "nazarov-2015-dhofar-silicides::nazarov_2015_table2_sio2::T=1833:h=677f0952267b",
    ),
    SmokeSlice(
        Engine.INTERNAL_ANALYTICAL,
        "kems-053-stolyarova-1991",
        Rail.SIO_EVOLUTION,
        "kems-053-stolyarova-1991::stolyarova_1991_sio2_gas_partial_pressure_1933k_ion_comparison::T=1933.00:h=2ce25ff1442e",
    ),
    SmokeSlice(
        Engine.MAGEMIN,
        "stolyarova-1996-cao-alumina-silica-kems",
        Rail.MELT_ACTIVITY,
        "stolyarova-1996-cao-alumina-silica-kems::stolyarova_1996_table3_sio2_activity_column_5_1933k::T=1933.00:h=9d04049614eb",
    ),
    SmokeSlice(
        Engine.THERMOENGINE,
        "nazarov-2015-dhofar-silicides",
        Rail.SIO_EVOLUTION,
        "nazarov-2015-dhofar-silicides::nazarov_2015_table2_sio::T=1833:h=d2752f80345e",
    ),
    SmokeSlice(
        Engine.VAPOROCK,
        "kems-053-stolyarova-1991",
        Rail.SIO_EVOLUTION,
        "kems-053-stolyarova-1991::stolyarova_1991_sio_partial_pressure_1933k_complete_evaporation::T=1933.00:h=fb536d3ae94b",
    ),
    SmokeSlice(
        Engine.OPENIMCC,
        "kems-053-stolyarova-1991",
        Rail.SIO_EVOLUTION,
        "kems-053-stolyarova-1991::stolyarova_1991_sio2_gas_partial_pressure_1933k_ion_comparison::T=1933.00:h=2ce25ff1442e",
    ),
)
DEFAULT_SLICES = SMOKE_SLICES
ENGINE_ORDER = (
    Engine.OPENIMCC,
    Engine.ALPHAMELTS,
    Engine.INTERNAL_ANALYTICAL,
    Engine.MAGEMIN,
    Engine.THERMOENGINE,
    Engine.VAPOROCK,
)
DEFAULT_WORKS = tuple(dict.fromkeys(case.source for case in DEFAULT_SLICES))


def _link_smoke_sources(
    root: Path,
    subset_root: Path,
    sources: tuple[str, ...],
) -> None:
    literature = root / "data" / "literature"
    subset_literature = subset_root / "data" / "literature"
    tokens = source_filter_tokens(sources)
    experiment_ids: set[str] = set()
    stores = (
        ("extracts-v2", iter_observation_store_paths),
        ("observations-v2", iter_observation_store_paths),
    )
    for store_name, list_paths in stores:
        store = literature / store_name
        if not store.is_dir():
            continue
        paths = list_paths(store)
        for source in paths:
            if not path_matches_source_tokens(source, store, tokens):
                continue
            document = load_yaml(source)
            if not isinstance(document, Mapping):
                continue
            observations = document.get("observations") or []
            for observation in observations:
                if not isinstance(observation, Mapping):
                    continue
                experiment_id = observation.get("experiment_id")
                if experiment_id:
                    experiment_ids.add(str(experiment_id))
            destination = subset_literature / store_name / source.relative_to(store)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source.resolve())

    works = literature / "works"
    subset_works = subset_literature / "works"
    aliases = load_aliases(works / "ALIASES.yaml")
    source_work_ids = {
        experiment_id.split("::", 1)[0].split(":", 1)[0]
        for experiment_id in experiment_ids
    }
    work_ids = {aliases.get(work_id, work_id) for work_id in source_work_ids}
    for work_id in sorted(work_ids):
        source = works / work_filename(work_id)
        if not source.is_file():
            raise RuntimeError(f"smoke source work is missing: {work_id}")
        destination = subset_works / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source.resolve())

    extracts = literature / "extracts"
    if extracts.is_dir():
        for source in sorted(extracts.glob("*.yaml")):
            if not path_matches_source_tokens(source, extracts, tokens):
                continue
            destination = subset_literature / "extracts" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source.resolve())


def _db_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with sqlite3.connect(path) as connection:
        return int(connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0])


def _cache_storage_report(path: Path) -> list[str]:
    if not path.exists():
        return []
    cold_path = path.with_name(path.name + ".cold")
    cold_rows: dict[bytes, int] = {}
    if cold_path.exists():
        with sqlite3.connect(cold_path) as connection:
            cold_rows = dict(
                connection.execute(
                    "SELECT key, length(value) FROM engine_outputs_cold"
                ).fetchall()
            )
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT engine, key, length(value) FROM engine_outputs"
        ).fetchall()

    sizes: dict[str, list[tuple[int, int]]] = {}
    for engine, key, hot_bytes in rows:
        sizes.setdefault(str(engine), []).append(
            (int(hot_bytes), int(cold_rows.get(key, 0)))
        )
    report = []
    for engine in ENGINE_ORDER:
        values = sizes.get(engine.value, [])
        if not values:
            report.append(f"cache-size {engine.value}=rows:0")
            continue
        hot = sorted(value[0] for value in values)
        cold = sorted(value[1] for value in values)
        p95_index = (95 * len(values) + 99) // 100 - 1
        report.append(
            f"cache-size {engine.value}=rows:{len(values)}"
            f"/hot_value_mean_B:{sum(hot) / len(hot):.1f}"
            f"/hot_value_p95_B:{hot[p95_index]}"
            f"/cold_value_mean_B:{sum(cold) / len(cold):.1f}"
            f"/cold_value_p95_B:{cold[p95_index]}"
        )
    file_bytes = sum(
        candidate.stat().st_size
        for base in (path, cold_path)
        for candidate in (
            base,
            base.with_name(base.name + "-wal"),
            base.with_name(base.name + "-shm"),
        )
        if candidate.exists()
    )
    if rows:
        mean_hot_row = sum(int(row[2]) + 32 for row in rows) / len(rows)
        report.append(
            f"cache-size observed_hot_payload_projection_B="
            f"1M:{mean_hot_row * 1_000_000:.0f}"
            f"/10M:{mean_hot_row * 10_000_000:.0f}"
            f" observed_file_bytes:{file_bytes}"
        )
    return report


def _cpu_seconds() -> float:
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def _run(
    context: Any,
    root: Path,
    output_dir: Path,
    *,
    slices: tuple[SmokeSlice, ...],
    limit: int,
    workers: int,
    cache_path: Path | None,
) -> tuple[list[str], dict[str, dict[str, int]], float, float]:
    if cache_path is None:
        os.environ.pop("REGOLITH_ENGINE_CACHE", None)
    else:
        os.environ["REGOLITH_ENGINE_CACHE"] = str(cache_path)
    before_wall, before_cpu = time.perf_counter(), _cpu_seconds()
    counters: dict[str, dict[str, int]] = {}
    rows: list[str] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, Rail, str | None], list[SmokeSlice]] = {}
    for case in slices:
        groups.setdefault(
            (case.source, case.rail, case.observation_match), []
        ).append(case)
    for index, group in enumerate(groups.values()):
        case = group[0]
        engines = tuple(item.engine for item in group)
        engine_label = "-".join(engine.value for engine in engines)
        output = output_dir / f"{index:02d}-{engine_label}.jsonl"
        original_prepare = battery_score._prepare_score_run
        if case.observation_match is None:
            exact_ids = None
        else:
            exact_ids = {case.observation_match}

        def prepare_exact_refs(*args: Any, **kwargs: Any) -> Any:
            prepared = original_prepare(*args, **kwargs)
            if exact_ids is None:
                return prepared
            refs = tuple(
                observation
                for observation in prepared.refs
                if observation.observation_id in exact_ids
            )
            if not refs:
                engine_names = ", ".join(engine.value for engine in engines)
                raise RuntimeError(
                    f"{engine_names}: no prepared observation matched "
                    f"{case.observation_match!r}"
                )
            ref_ids = {observation.observation_id for observation in refs}
            return replace(
                prepared,
                refs=refs,
                series_point_counts={
                    observation_id: count
                    for observation_id, count in prepared.series_point_counts.items()
                    if observation_id in ref_ids
                },
            )

        battery_score._prepare_score_run = prepare_exact_refs
        try:
            _count, metadata = _score_store_to_jsonl(
                context,
                output,
                root=root,
                engines=engines,
                rail=case.rail,
                work_id=case.source,
                limit=None if exact_ids is not None else limit,
                include_diagnostics=True,
                workers=workers,
            )
        finally:
            battery_score._prepare_score_run = original_prepare
        rows.extend(_canonical_rows(output.with_name(output.name + ".partial")))
        for engine, values in (metadata.engine_cache_counters or {}).items():
            target = counters.setdefault(engine, {})
            for name, count in values.items():
                target[name] = target.get(name, 0) + int(count)
    return rows, counters, time.perf_counter() - before_wall, _cpu_seconds() - before_cpu


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--limit", type=int, default=1,
                        help="observations per engine slice (default: 1)")
    parser.add_argument("--works", nargs="+", default=None,
                        help="source IDs from the fixed smoke slices to load")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    root = args.root.resolve()
    work_ids = tuple(str(value) for value in (args.works or DEFAULT_WORKS))
    known_sources = {case.source for case in SMOKE_SLICES}
    unknown_sources = sorted(set(work_ids) - known_sources)
    if unknown_sources:
        parser.error("unsupported smoke sources: " + ", ".join(unknown_sources))
    slices = (
        tuple(case for case in SMOKE_SLICES if case.source in work_ids)
        if args.works
        else DEFAULT_SLICES
    )
    selected_engines = {case.engine for case in slices}
    missing_engines = [
        engine.value for engine in ENGINE_ORDER if engine not in selected_engines
    ]
    if missing_engines:
        parser.error("selected sources do not exercise: " + ", ".join(missing_engines))
    tmp_parent = os.environ.get("TMPDIR")
    if not tmp_parent:
        parser.error("TMPDIR must name the dispatch scratch directory")

    with tempfile.TemporaryDirectory(prefix="cache-smoke-", dir=tmp_parent) as name:
        scratch = Path(name)
        subset_root = scratch / "subset-store"
        _link_smoke_sources(root, subset_root, work_ids)
        context = load_score_context(subset_root, sources=work_ids)
        if not context.observations:
            raise RuntimeError("selected smoke sources loaded no observations")
        context = replace(context, root=root)
        cache_path = scratch / "engine-cache.sqlite"
        results: dict[str, list[str]] = {}
        counts: dict[str, dict[str, dict[str, int]]] = {}
        runs = (
            ("off", None, 1),
            ("cold", cache_path, 1),
            ("warm", cache_path, 1),
            ("warm-workers-2", cache_path, 2),
        )
        print(
            f"works={','.join(work_ids)} "
            f"engines={','.join(engine.value for engine in ENGINE_ORDER)} "
            f"slices={len(slices)} limit={args.limit}",
            flush=True,
        )
        for label, path, workers in runs:
            rows, engine_counts, wall, cpu = _run(
                context, root, scratch / label, slices=slices, limit=args.limit,
                workers=workers, cache_path=path,
            )
            results[label] = rows
            counts[label] = engine_counts
            per_engine = " ".join(
                f"{engine.value}=hits:{engine_counts.get(engine.value, {}).get('hits', 0)}"
                f"/misses:{engine_counts.get(engine.value, {}).get('misses', 0)}"
                f"/stores:{engine_counts.get(engine.value, {}).get('stores', 0)}"
                f"/store_error:{engine_counts.get(engine.value, {}).get('store_error', 0)}"
                f"/read_error:{engine_counts.get(engine.value, {}).get('read_error', 0)}"
                for engine in ENGINE_ORDER
            )
            print(
                f"run={label} wall_s={wall:.2f} process_cpu_s={cpu:.2f} "
                f"db_rows={_db_rows(cache_path) if path else 0} {per_engine}",
                flush=True,
            )

        for line in _cache_storage_report(cache_path):
            print(line, flush=True)

    identical = bool(results["off"]) and all(
        results["off"] == results[name]
        for name in ("cold", "warm", "warm-workers-2")
    )
    print(f"rows identical: {'yes' if identical else 'no'}", flush=True)
    failures = []
    for engine in ENGINE_ORDER:
        cold = counts["cold"].get(engine.value, {})
        warm = counts["warm"].get(engine.value, {})
        parallel_warm = counts["warm-workers-2"].get(engine.value, {})
        if cold.get("misses", 0) <= 0 or cold.get("stores", 0) <= 0:
            failures.append(f"{engine.value}:cold")
        if cold.get("store_error", 0) > 0:
            failures.append(f"{engine.value}:cold-store-error")
        if warm.get("hits", 0) <= 0 or warm.get("misses", 0) != 0:
            failures.append(f"{engine.value}:warm")
        if warm.get("store_error", 0) > 0:
            failures.append(f"{engine.value}:warm-store-error")
        if parallel_warm.get("hits", 0) <= 0 or parallel_warm.get("misses", 0) != 0:
            failures.append(f"{engine.value}:warm-workers-2")
        if parallel_warm.get("store_error", 0) > 0:
            failures.append(f"{engine.value}:warm-workers-2-store-error")
    if failures:
        print("cache smoke incomplete: " + ", ".join(failures), file=sys.stderr)
    return 0 if identical and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
