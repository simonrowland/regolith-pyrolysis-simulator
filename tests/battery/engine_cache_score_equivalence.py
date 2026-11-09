from __future__ import annotations

from pathlib import Path
from typing import Mapping

from simulator.battery.enums import Engine, Rail
from simulator.battery.score import _score_store_to_jsonl, load_score_context
from simulator.engine_cache import EngineCache
from tests.battery.test_engine_cache_equivalence import _canonical_residual_rows


ROOT = Path(__file__).resolve().parents[2]
_OPENIMCC_CALL_KINDS = (
    "equilibrate_cell",
    "evaluate",
    "evaluate_redox",
    "evaluate_gas",
    "residue_vapor_pressure",
)


def assert_residue_score_cache_equivalence(
    tmp_path: Path,
    *,
    source: str,
) -> None:
    import pytest

    pytest.importorskip("openimcc")
    context = load_score_context(sources=(source,))

    def score(
        label: str,
        cache: EngineCache | None = None,
        *,
        workers: int = 1,
    ) -> tuple[bytes, Mapping[str, Mapping[str, object]] | None]:
        path = tmp_path / f"{label}.jsonl"
        _row_count, metadata = _score_store_to_jsonl(
            context,
            path,
            root=ROOT,
            engines=(Engine.OPENIMCC,),
            rail=Rail.RESIDUE_COMPOSITION,
            include_diagnostics=False,
            workers=workers,
            engine_cache=cache,
        )
        return (
            _canonical_residual_rows(path.with_name(path.name + ".partial")),
            metadata.engine_cache_counters,
        )

    off, _off_counts = score("off")
    cache_path = tmp_path / "openimcc-residue.sqlite"
    cold, cold_counts = score("cold", EngineCache(cache_path))
    warm, warm_counts = score("warm", EngineCache(cache_path))
    parallel_warm, parallel_counts = score(
        "parallel-warm", EngineCache(cache_path), workers=3
    )

    assert off
    assert cold == warm == parallel_warm == off
    assert cold_counts is not None
    assert warm_counts is not None
    assert parallel_counts is not None
    cold_calls = cold_counts[Engine.OPENIMCC.value]["calls_by_kind"]
    assert cold_calls["evaluate"] > 0
    assert cold_calls["evaluate_gas"] > 0
    for counts in (warm_counts, parallel_counts):
        engine_counts = counts[Engine.OPENIMCC.value]
        assert engine_counts["misses"] == 0
        assert engine_counts["hits"] > 0
        assert engine_counts["calls_by_kind"] == {
            kind: 0 for kind in _OPENIMCC_CALL_KINDS
        }
