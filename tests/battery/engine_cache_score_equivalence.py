from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from simulator.battery.enums import Engine, Rail
from simulator.battery.score import _score_store_to_jsonl, load_score_context
from tests.battery.test_engine_cache_equivalence import _canonical_residual_rows


ROOT = Path(__file__).resolve().parents[2]


def assert_residue_score_cache_equivalence(
    tmp_path: Path,
    *,
    source: str,
    experiment_ids: tuple[str, ...],
) -> None:
    import pytest

    pytest.importorskip("openimcc")
    context = load_score_context(sources=(source,))

    def score(
        label: str,
        cache_path: Path | None = None,
        *,
        workers: int = 1,
    ) -> tuple[bytes, Mapping[str, Mapping[str, object]]]:
        previous = os.environ.get("REGOLITH_ENGINE_CACHE")
        if cache_path is None:
            os.environ.pop("REGOLITH_ENGINE_CACHE", None)
        else:
            os.environ["REGOLITH_ENGINE_CACHE"] = str(cache_path)
        try:
            path = tmp_path / f"{label}.jsonl"
            _row_count, metadata = _score_store_to_jsonl(
                context,
                path,
                root=ROOT,
                engines=(Engine.OPENIMCC,),
                rail=Rail.RESIDUE_COMPOSITION,
                include_diagnostics=False,
                workers=workers,
                experiment_ids=experiment_ids,
            )
        finally:
            if previous is None:
                os.environ.pop("REGOLITH_ENGINE_CACHE", None)
            else:
                os.environ["REGOLITH_ENGINE_CACHE"] = previous
        return (
            _canonical_residual_rows(path.with_name(path.name + ".partial")),
            metadata.engine_cache_counters or {},
        )

    off, _off_counts = score("off")
    serial_cache = tmp_path / "openimcc-residue-serial.sqlite"
    cold, cold_counts = score("cold", serial_cache)
    warm, warm_counts = score("warm", serial_cache)
    parallel_warm, parallel_counts = score("parallel-warm", serial_cache, workers=3)

    assert off
    assert len(off.splitlines()) <= 12
    _assert_runs_match_off(
        off,
        {
            "cold": cold,
            "warm": warm,
            "parallel-warm": parallel_warm,
        },
    )
    engine = cold_counts[Engine.OPENIMCC.value]
    assert engine["misses"] > 0
    assert engine["stores"] > 0
    for counts in (warm_counts, parallel_counts):
        engine = counts[Engine.OPENIMCC.value]
        assert engine["hits"] > 0
        assert engine["misses"] == 0


def _assert_runs_match_off(off: bytes, runs: Mapping[str, bytes]) -> None:
    import difflib

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
