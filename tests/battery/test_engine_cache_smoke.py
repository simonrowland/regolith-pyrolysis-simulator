"""Real-engine parity and storage probe for every cache-format revision."""

from dataclasses import replace
import json
from pathlib import Path
import warnings

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
            assert json.loads(actual) == json.loads(expected), label
        assert results[label] == baseline, label
    warnings.warn("rows identical: yes", stacklevel=1)
