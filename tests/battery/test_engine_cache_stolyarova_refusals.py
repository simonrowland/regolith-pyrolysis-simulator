from __future__ import annotations

from pathlib import Path

from simulator.battery.enums import Engine
from simulator.battery.score import load_score_context
from scripts.battery_cache_equivalence import _run_slice


def test_stolyarova_vaporock_and_thermoengine_refusals_hit_warm_with_parity(
    tmp_path: Path,
) -> None:
    source = "kems-053-stolyarova-1991"
    engines = (Engine.VAPOROCK, Engine.THERMOENGINE)
    root = Path(__file__).resolve().parents[2]
    context = load_score_context(root, sources=(source,))
    cache = tmp_path / "engine-cache.sqlite"

    cold_rows, cold_counts = _run_slice(
        context,
        tmp_path / "cold.jsonl",
        root=root,
        engines=engines,
        rail=None,
        work=source,
        limit=100_000,
        workers=1,
        cache_path=cache,
    )
    warm_rows, warm_counts = _run_slice(
        context,
        tmp_path / "warm.jsonl",
        root=root,
        engines=engines,
        rail=None,
        work=source,
        limit=100_000,
        workers=1,
        cache_path=cache,
    )

    assert cold_rows
    assert cold_rows == warm_rows
    expected_warm_hits = {
        Engine.VAPOROCK: 684,
        Engine.THERMOENGINE: 24,
    }
    for engine in engines:
        cold = cold_counts[engine.value]
        warm = warm_counts[engine.value]
        assert cold["misses"] > 0
        assert warm["misses"] == 0
        assert warm["hits"] == expected_warm_hits[engine], repr(warm["hits"])
