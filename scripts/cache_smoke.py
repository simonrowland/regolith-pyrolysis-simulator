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
from simulator.battery.enums import Engine, Rail  # noqa: E402
from simulator.battery.migrate import (  # noqa: E402
    iter_observation_store_paths,
    iter_work_store_paths,
    dump_yaml,
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


# Real numeric rows selected from the controller's whole-store census. Keep
# these source/rail pairs in the smoke so each engine must make a cache call.
SMOKE_SLICES = (
    SmokeSlice(Engine.OPENIMCC, "guo-2021-mgo-activity-cmas-slag", Rail.MELT_ACTIVITY),
    SmokeSlice(
        Engine.OPENIMCC,
        "shornikov-2002-aluminosilicate-solid-phases",
        Rail.SIO_EVOLUTION,
    ),
    SmokeSlice(Engine.OPENIMCC, "sossi-2020-magma-ocean-redox", Rail.REDOX),
    SmokeSlice(Engine.ALPHAMELTS, "sossi-2020-magma-ocean-redox", Rail.REDOX),
    SmokeSlice(
        Engine.ALPHAMELTS,
        "shornikov-2002-aluminosilicate-solid-phases",
        Rail.SIO_EVOLUTION,
    ),
    SmokeSlice(
        Engine.ALPHAMELTS,
        "aithala-2026-magmatic-iron-redox-to-2100c",
        Rail.REDOX,
    ),
    SmokeSlice(
        Engine.INTERNAL_ANALYTICAL,
        "hemingway-haas-robinson-1982-usgs-b1544",
        Rail.THERMOCHEMISTRY,
        "usgs-b1544-lime:delta_fG:from_the_elements:T=1000",
    ),
    SmokeSlice(
        Engine.MAGEMIN,
        "hemingway-haas-robinson-1982-usgs-b1544",
        Rail.THERMOCHEMISTRY,
        "usgs-b1544-andalusite:S:shared:T=1000",
    ),
    SmokeSlice(
        Engine.THERMOENGINE,
        "hemingway-haas-robinson-1982-usgs-b1544",
        Rail.THERMOCHEMISTRY,
        "usgs-b1544-andalusite:S:shared:T=1000",
    ),
    SmokeSlice(
        Engine.VAPOROCK,
        "kelley-king-1961-usbm-b592",
        Rail.THERMOCHEMISTRY,
        "table-006-0018:S:entropy_recommended",
    ),
)
DEFAULT_SLICES = (
    SMOKE_SLICES[0],
    SMOKE_SLICES[3],
    SMOKE_SLICES[6],
    SMOKE_SLICES[7],
    SMOKE_SLICES[8],
    SMOKE_SLICES[9],
)
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
    slices: tuple[SmokeSlice, ...],
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
            source_cases = [
                case
                for case in slices
                if path_matches_source_tokens(
                    source, store, source_filter_tokens((case.source,))
                )
            ]
            filters: list[str] = []
            eligible = False
            relative = source.relative_to(store).as_posix()
            for case in source_cases:
                if case.observation_match is None:
                    eligible = True
                    continue
                fragment = case.observation_match.removeprefix(case.source + ":").split(":", 1)[0]
                matching_paths = [
                    candidate
                    for candidate in paths
                    if path_matches_source_tokens(
                        candidate, store, source_filter_tokens((case.source,))
                    )
                    and fragment in candidate.relative_to(store).as_posix()
                ]
                if not matching_paths or source in matching_paths:
                    filters.append(case.observation_match)
                    eligible = True
            if not eligible:
                continue
            document = load_yaml(source)
            if not isinstance(document, Mapping):
                continue
            observations = document.get("observations") or []
            if filters:
                observations = [
                    observation
                    for observation in observations
                    if isinstance(observation, Mapping)
                    and any(
                        term in str(observation.get("observation_id") or "")
                        for term in filters
                    )
                ]
                if not observations:
                    continue
                document = {**document, "observations": observations}
            for observation in observations:
                if not isinstance(observation, Mapping):
                    continue
                experiment_id = observation.get("experiment_id")
                if experiment_id:
                    experiment_ids.add(str(experiment_id))
            destination = subset_literature / store_name / source.relative_to(store)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if filters:
                dump_yaml(document, destination)
            else:
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
    for index, case in enumerate(slices):
        case_context = context
        if case.observation_match is not None:
            selected = {
                observation_id: observation
                for observation_id, observation in context.observations.items()
                if case.observation_match in observation_id
            }
            if not selected:
                raise RuntimeError(
                    f"{case.engine.value}: no observation matched "
                    f"{case.observation_match!r}"
                )
            case_context = replace(context, observations=selected)
        output = output_dir / f"{index:02d}-{case.engine.value}.jsonl"
        _count, metadata = _score_store_to_jsonl(
            case_context,
            output,
            root=root,
            engines=(case.engine,),
            rail=case.rail,
            work_id=case.source,
            limit=limit,
            include_diagnostics=True,
            workers=workers,
        )
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
        _link_smoke_sources(root, subset_root, work_ids, slices)
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
                for engine in ENGINE_ORDER
            )
            print(
                f"run={label} wall_s={wall:.2f} process_cpu_s={cpu:.2f} "
                f"db_rows={_db_rows(cache_path) if path else 0} {per_engine}",
                flush=True,
            )

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
        if warm.get("hits", 0) <= 0 or warm.get("misses", 0) != 0:
            failures.append(f"{engine.value}:warm")
        if parallel_warm.get("hits", 0) <= 0 or parallel_warm.get("misses", 0) != 0:
            failures.append(f"{engine.value}:warm-workers-2")
    if failures:
        print("cache smoke incomplete: " + ", ".join(failures), file=sys.stderr)
    return 0 if identical and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
