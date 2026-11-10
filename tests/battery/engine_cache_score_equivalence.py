from __future__ import annotations

import difflib
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
    off_parallel, _off_parallel_counts = score("off-parallel", workers=3)
    serial_cache_path = tmp_path / "openimcc-residue-serial.sqlite"
    parallel_cache_path = tmp_path / "openimcc-residue-parallel.sqlite"
    cold, cold_counts = score("cold", EngineCache(serial_cache_path), workers=1)
    cold_parallel, cold_parallel_counts = score(
        "cold-parallel", EngineCache(parallel_cache_path), workers=3
    )
    warm, warm_counts = score("warm", EngineCache(serial_cache_path), workers=1)
    parallel_warm, parallel_counts = score(
        "parallel-warm", EngineCache(parallel_cache_path), workers=3
    )

    assert off
    _assert_runs_match_off(
        off,
        {
            "off-parallel": off_parallel,
            "cold": cold,
            "cold-parallel": cold_parallel,
            "warm": warm,
            "parallel-warm": parallel_warm,
        },
    )
    assert cold_counts is not None
    assert cold_parallel_counts is not None
    assert warm_counts is not None
    assert parallel_counts is not None
    cold_calls = cold_counts[Engine.OPENIMCC.value]["calls_by_kind"]
    cold_parallel_calls = cold_parallel_counts[Engine.OPENIMCC.value]["calls_by_kind"]
    assert cold_calls["evaluate"] > 0
    assert cold_calls["evaluate_gas"] > 0
    assert cold_parallel_calls["evaluate"] > 0
    assert cold_parallel_calls["evaluate_gas"] > 0
    for counts in (warm_counts, parallel_counts):
        engine_counts = counts[Engine.OPENIMCC.value]
        assert engine_counts["misses"] == 0
        assert engine_counts["hits"] > 0
        assert engine_counts["calls_by_kind"] == {
            kind: 0 for kind in _OPENIMCC_CALL_KINDS
        }


def _assert_runs_match_off(off: bytes, runs: Mapping[str, bytes]) -> None:
    for run_name, rows in runs.items():
        if rows == off:
            continue
        diff = difflib.unified_diff(
            off.decode("utf-8").splitlines(),
            rows.decode("utf-8").splitlines(),
            fromfile="off",
            tofile=run_name,
            n=0,
            lineterm="",
        )
        first_hunk: list[str] = []
        found_hunk = False
        for line in diff:
            if line.startswith("@@"):
                if found_hunk:
                    break
                found_hunk = True
            if line.startswith(("--- ", "+++ ")) or found_hunk:
                first_hunk.append(line)
        raise AssertionError(
            f"{run_name} differs from off; first differing canonical rows:\n"
            + "\n".join(first_hunk)
        )
