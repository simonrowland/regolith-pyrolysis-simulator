#!/usr/bin/env python3
"""Measure off/cold/warm battery scoring on a real OpenIMCC residue slice."""

from __future__ import annotations

import argparse
import json
import os
import resource
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.score import (  # noqa: E402
    _score_store_to_jsonl,
    is_store_stamp_payload,
    load_score_context,
)
from simulator.battery.enums import Engine, Rail  # noqa: E402

# This source/rail pair has a real OpenIMCC residue path. Keep the selection
# fixed so a smoke run cannot silently measure an empty or non-engine slice.
DEFAULT_WORKS = ("kems-015-hashimoto-1983",)
SMOKE_ENGINES = (Engine.OPENIMCC,)
SMOKE_RAIL = Rail.RESIDUE_COMPOSITION
TIMING_FIELDS = {
    "wall_s", "cpu_s", "elapsed_s", "process_cpu_seconds",
    "process_cpu_seconds_by_geometry", "traceback", "traceback_text",
}


def _canonical_rows(path: Path) -> list[str]:
    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): clean(item)
                for key, item in value.items()
                if str(key) not in TIMING_FIELDS
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not is_store_stamp_payload(payload):
                rows.append(json.dumps(clean(payload), sort_keys=True,
                                        ensure_ascii=False, separators=(",", ":")))
    return rows


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
    works: tuple[str, ...],
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
    output = output_dir / "residuals.jsonl"
    _count, metadata = _score_store_to_jsonl(
        context,
        output,
        root=root,
        engines=SMOKE_ENGINES,
        rail=SMOKE_RAIL,
        limit=limit,
        include_diagnostics=False,
        workers=workers,
    )
    rows = _canonical_rows(output.with_name(output.name + ".partial"))
    for engine, values in (metadata.engine_cache_counters or {}).items():
        target = counters.setdefault(engine, {})
        for name, count in values.items():
            target[name] = target.get(name, 0) + int(count)
    return rows, counters, time.perf_counter() - before_wall, _cpu_seconds() - before_cpu


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--limit", type=int, default=1,
                        help="observations in the selected source subset (default: 1)")
    parser.add_argument("--works", nargs="+", default=DEFAULT_WORKS,
                        help="source IDs loaded for the smoke; defaults to Hashimoto")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    root = args.root.resolve()
    work_ids = tuple(str(value) for value in args.works)
    context = load_score_context(root, sources=work_ids)
    tmp_parent = os.environ.get("TMPDIR")
    if not tmp_parent:
        parser.error("TMPDIR must name the dispatch scratch directory")

    with tempfile.TemporaryDirectory(prefix="cache-smoke-", dir=tmp_parent) as name:
        scratch = Path(name)
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
            f"engines={','.join(engine.value for engine in SMOKE_ENGINES)} "
            f"rail={SMOKE_RAIL.value} limit={args.limit}",
            flush=True,
        )
        for label, path, workers in runs:
            rows, engine_counts, wall, cpu = _run(
                context, root, scratch / label, works=work_ids, limit=args.limit,
                workers=workers, cache_path=path,
            )
            results[label] = rows
            counts[label] = engine_counts
            per_engine = " ".join(
                f"{engine.value}=hits:{engine_counts.get(engine.value, {}).get('hits', 0)}"
                f"/misses:{engine_counts.get(engine.value, {}).get('misses', 0)}"
                f"/stores:{engine_counts.get(engine.value, {}).get('stores', 0)}"
                for engine in SMOKE_ENGINES
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
    for engine in SMOKE_ENGINES:
        cold = counts["cold"].get(engine.value, {})
        warm = counts["warm"].get(engine.value, {})
        if cold.get("misses", 0) <= 0 or cold.get("stores", 0) <= 0:
            failures.append(f"{engine.value}:cold")
        if warm.get("hits", 0) <= 0 or warm.get("misses", 0) != 0:
            failures.append(f"{engine.value}:warm")
    if failures:
        print("cache smoke incomplete: " + ", ".join(failures), file=sys.stderr)
    return 0 if identical and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
