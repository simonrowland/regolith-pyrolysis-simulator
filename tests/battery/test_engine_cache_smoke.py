"""Real-engine parity and storage probe for every cache-format revision."""

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import warnings
import zlib

from scripts import cache_smoke


def test_every_engine_smoke_rows_are_identical(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    subset = tmp_path / "subset"
    cache_smoke._link_smoke_sources(root, subset, cache_smoke.DEFAULT_WORKS)
    context = replace(
        cache_smoke.load_score_context(subset, sources=cache_smoke.DEFAULT_WORKS),
        root=root,
    )
    cache = tmp_path / "engine-cache.sqlite"
    results = {}
    report = []
    for label, path, workers in (
        ("off", None, 1),
        ("cold", cache, 1),
        ("warm", cache, 1),
        ("warm-workers-2", cache, 2),
    ):
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
    from simulator.engine_cache_binary import pack, unpack

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
        stored = connection.execute("SELECT engine,key,value FROM engine_outputs").fetchall()
    cold_path = Path(str(cache) + ".cold")
    cold_rows = {}
    if cold_path.exists():
        with sqlite3.connect(cold_path) as connection:
            cold_rows = dict(connection.execute("SELECT key,value FROM engine_values"))
    sizes = {}
    for engine, key, value in stored:
        check_strings(unpack(value))
        assert len(value) <= 1024, (engine, [
            (index, len(pack(item))) for index, item in enumerate(unpack(value))
        ])
        cold = cold_rows.get(key)
        sizes.setdefault(engine, []).append(len(cold) if cold is not None else 0)
        if cold is not None:
            check_strings(unpack(zlib.decompress(cold)))
            assert len(cold) <= 1024, engine
    for engine, values in sizes.items():
        assert sum(values) / len(values) <= 300, (engine, values)
