"""Real-engine parity and storage probe for every cache-format revision."""

from dataclasses import replace
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from statistics import median
import time
import warnings
import zlib

import pytest

from scripts import cache_smoke
from simulator import engine_cache

pytestmark = pytest.mark.engine_cache_off


def test_every_engine_smoke_rows_are_identical(tmp_path: Path, monkeypatch) -> None:
    import openimcc

    module = Path(openimcc.__file__)
    # Match the brief's `find . ... | sort | xargs shasum | shasum` proof.
    paths = sorted(module.parent.rglob("*.py"))
    manifest = "".join(
        f"{hashlib.sha1(path.read_bytes()).hexdigest()}  ./{path.relative_to(module.parent).as_posix()}\n"
        for path in paths
    )
    tree_hash = hashlib.sha1(manifest.encode()).hexdigest()
    warnings.warn(f"OpenIMCC imported:{module} source_tree_shasum:{tree_hash}", stacklevel=1)
    root = Path(__file__).resolve().parents[2]
    subset = tmp_path / "subset"
    cache_smoke._link_smoke_sources(root, subset, cache_smoke.DEFAULT_WORKS)
    context = replace(
        cache_smoke.load_score_context(subset, sources=cache_smoke.DEFAULT_WORKS),
        root=root,
    )
    cache = tmp_path / "engine-cache.sqlite"
    results = {}
    counters = {}
    report = []
    requests = {}
    original_call = engine_cache.cached_engine_call

    def capture_request(engine, request, solve):
        result = original_call(engine, request, solve)
        key = engine_cache.engine_cache_key(engine, engine_cache.ENGINE_CHEMISTRY_VERSION[engine], request)
        if (str(cache), key, False) in engine_cache._MEMORY_CACHE:
            requests[engine] = deepcopy(request)
        return result
    for label, path, workers in (
        ("off", None, 1),
        ("cold", cache, 1),
        ("warm", cache, 1),
        ("warm-workers-2", cache, 2),
    ):
        with monkeypatch.context() as scoped:
            if label == "cold":
                scoped.setattr(engine_cache, "cached_engine_call", capture_request)
            rows, counts, wall, cpu = cache_smoke._run(
                context, root, tmp_path / label,
                slices=cache_smoke.DEFAULT_SLICES, limit=1, workers=workers,
                cache_path=path,
            )
        assert rows, label
        report.append(f"run={label} wall_s={wall:.3f} process_cpu_s={cpu:.3f}")
        for engine in cache_smoke.ENGINE_ORDER:
            report.append(f"{label} {engine.value} {json.dumps(counts.get(engine.value, {}), sort_keys=True)}")
        results[label] = rows
        counters[label] = counts
    report.extend(cache_smoke._cache_storage_report(cache))
    warnings.warn("\n".join(report), stacklevel=1)
    baseline = results["off"]
    for label in ("cold", "warm", "warm-workers-2"):
        assert len(results[label]) == len(baseline), label
        for actual, expected in zip(results[label], baseline):
            assert json.loads(actual) == json.loads(expected), (
                label, json.loads(actual).get("refusal"),
                json.loads(expected).get("refusal"),
            )
        assert results[label] == baseline, label
    warnings.warn("rows identical: yes", stacklevel=1)
    for engine in cache_smoke.ENGINE_ORDER:
        assert engine.value in requests, f"{engine.value}: no cacheable cold request"
        cold = counters["cold"][engine.value]
        assert cold["misses"] == cold["stores"] > 0, (engine, cold)
        for label in ("warm", "warm-workers-2"):
            warm = counters[label][engine.value]
            assert warm["hits"] == cold["stores"], (engine, label, warm)
            assert warm["misses"] == warm["stores"] == warm["read_error"] == warm["store_error"] == 0
    def measure(call):
        batches = []
        for _ in range(5):
            started = time.perf_counter_ns()
            for _ in range(1000):
                call()
            batches.append((time.perf_counter_ns() - started) / 1_000_000)
        return median(batches)

    with engine_cache._engine_cache_path_scope(cache):
        for engine, request in sorted(requests.items()):
            version = engine_cache.ENGINE_CHEMISTRY_VERSION[engine]
            def unexpected_miss():
                raise AssertionError(f"{engine}: benchmark reached the engine")
            hit_us = measure(lambda: original_call(engine, request, unexpected_miss))
            key_us = measure(lambda: engine_cache.engine_cache_key(engine, version, request))
            warnings.warn(f"cache-speed {engine} median_hit_us:{hit_us:.3f} median_key_us:{key_us:.3f}", stacklevel=1)
    from simulator.engine_cache import pack, unpack

    def check_strings(value):
        if isinstance(value, str):
            assert len(value.encode("utf-8")) <= 64
        elif isinstance(value, dict):
            for name, item in value.items():
                check_strings(name)
                check_strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                check_strings(item)

    with sqlite3.connect(cache) as connection:
        stored = connection.execute(
            "SELECT engine,row_seq,value FROM engine_outputs"
        ).fetchall()
    cold_path = Path(str(cache) + ".cold")
    cold_rows = {}
    if cold_path.exists():
        with sqlite3.connect(cold_path) as connection:
            cold_rows = dict(connection.execute("SELECT row_seq,value FROM engine_values"))
    sizes = {}
    for engine, row_seq, value in stored:
        check_strings(unpack(value))
        assert len(value) <= 1024, (engine, [
            (index, len(pack(item))) for index, item in enumerate(unpack(value))
        ])
        cold = cold_rows.get(row_seq)
        sizes.setdefault(engine, []).append(len(cold) if cold is not None else 0)
        if cold is not None:
            check_strings(unpack(zlib.decompress(cold)))
            assert len(cold) <= 1024, engine
    for engine, values in sizes.items():
        assert sum(values) / len(values) <= 300, (engine, values)
