#!/usr/bin/env python3
"""Compare canonical battery residual rows with engine caching off, cold, warm.

The JSON slice spec accepts ``root``, ``engines``, ``rail``, ``work`` and
``limit``. Its default limit is 334 observations, about 2,000 engine rows for
the six battery engines, with no rail filter.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.migrate import canonicalize_rail  # noqa: E402
from simulator.battery.score import (  # noqa: E402
    SCORE_ENGINE_SET,
    _score_store_to_jsonl,
    engines_from_names,
    is_store_stamp_payload,
    load_score_context,
)
from simulator.engine_cache import EngineCache  # noqa: E402


def _without_cache_counters(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_cache_counters(item)
            for key, item in value.items()
            if str(key)
            not in {
                "engine_cache",
                "cache_counters",
                "wall_s",
                "cpu_s",
                "elapsed_s",
            }
        }
    if isinstance(value, list):
        return [_without_cache_counters(item) for item in value]
    return value


def _canonical_rows(path: Path) -> list[str]:
    rows: list[str] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            payload = json.loads(line)
            if is_store_stamp_payload(payload):
                continue
            rows.append(
                json.dumps(
                    _without_cache_counters(payload),
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
    return rows


def _run_slice(
    context,
    output_path: Path,
    *,
    root: Path,
    engines: tuple,
    rail: str | None,
    work: str | None,
    limit: int,
    workers: int,
    cache_path: Path | None,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    cache = None if cache_path is None else EngineCache(cache_path)
    _score_store_to_jsonl(
        context,
        output_path,
        root=root,
        engines=engines,
        rail=None if rail is None else canonicalize_rail(rail),
        work_id=work,
        limit=limit,
        workers=workers,
        engine_cache=cache,
    )
    residuals_path = output_path.with_name(output_path.name + ".partial")
    return _canonical_rows(residuals_path), {} if cache is None else cache.counters


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slice_spec", type=Path)
    args = parser.parse_args(argv)
    spec_path = args.slice_spec.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, Mapping):
        parser.error("slice spec must be a JSON object")

    root_value = spec.get("root")
    root = ROOT if root_value is None else Path(str(root_value))
    if not root.is_absolute():
        root = (spec_path.parent / root).resolve()
    engines_value = spec.get("engines")
    engines = (
        tuple(SCORE_ENGINE_SET)
        if engines_value is None
        else engines_from_names([str(name) for name in engines_value])
    )
    if not engines:
        parser.error("slice spec must select at least one engine")
    rail = None if spec.get("rail") is None else str(spec["rail"])
    work = None if spec.get("work") is None else str(spec["work"])
    limit = int(spec.get("limit", 334))
    if limit <= 0:
        parser.error("slice spec limit must be positive")
    workers = int(spec.get("workers", 3))
    if workers < 1:
        parser.error("slice spec workers must be at least 1")

    context = load_score_context(root)
    tmp_parent = os.environ.get("TMPDIR")
    if not tmp_parent:
        parser.error("TMPDIR must name the dispatch scratch directory")
    with tempfile.TemporaryDirectory(prefix="battery-cache-equivalence-", dir=tmp_parent) as scratch:
        scratch_path = Path(scratch)
        cache_path = scratch_path / "engine-cache.sqlite"
        outputs: dict[str, list[str]] = {}
        counters: dict[str, dict[str, dict[str, int]]] = {}
        for name, use_cache, run_workers in (
            ("off", False, 1),
            ("cold", True, 1),
            ("warm", True, 1),
            ("parallel-warm", True, workers),
        ):
            rows, counts = _run_slice(
                context,
                scratch_path / f"{name}.jsonl",
                root=root,
                engines=engines,
                rail=rail,
                work=work,
                limit=limit,
                workers=run_workers,
                cache_path=cache_path if use_cache else None,
            )
            outputs[name] = rows
            counters[name] = counts

    if not outputs["off"]:
        print("slice produced no canonical residual rows", file=sys.stderr)
        return 2
    warm_names = ("warm", "parallel-warm")
    warm_misses = sum(
        row["misses"]
        for name in warm_names
        for row in counters[name].values()
    )
    warm_calls = sum(
        sum(row["calls_by_kind"].values())
        for name in warm_names
        for row in counters[name].values()
    )
    cold_calls_by_engine = {
        engine.value: sum(counters["cold"][engine.value]["calls_by_kind"].values())
        for engine in engines
    }
    warm_hits_by_engine = {
        engine.value: min(
            counters[name][engine.value]["hits"] for name in warm_names
        )
        for engine in engines
    }
    unexercised = [
        engine.value
        for engine in engines
        if cold_calls_by_engine[engine.value] <= 0
        or warm_hits_by_engine[engine.value] <= 0
        or any(
            counters[name][engine.value]["misses"]
            or any(counters[name][engine.value]["calls_by_kind"].values())
            for name in warm_names
        )
    ]
    if any(
        outputs[name] != outputs["off"]
        for name in ("cold", "warm", "parallel-warm")
    ):
        expected = outputs["off"]
        for name in ("cold", "warm", "parallel-warm"):
            if outputs[name] != expected:
                print(
                    "\n".join(
                        difflib.unified_diff(
                            expected,
                            outputs[name],
                            fromfile="off",
                            tofile=name,
                            lineterm="",
                        )
                    ),
                    file=sys.stderr,
                )
        return 1
    if unexercised:
        print(
            "slice did not exercise every requested engine through cold and warm "
            f"calls: {', '.join(unexercised)}",
            file=sys.stderr,
        )
        return 1
    print(
        f"equivalent rows={len(outputs['off'])} engines={len(engines)} "
        f"cold_misses={sum(row['misses'] for row in counters['cold'].values())} "
        f"warm_misses={warm_misses} warm_calls={warm_calls} "
        f"warm_hits={sum(row['hits'] for row in counters['warm'].values())} "
        f"parallel_workers={workers}"
    )
    return 0 if warm_misses == 0 and warm_calls == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
