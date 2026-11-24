from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from simulator.battery.enums import Engine, Rail
from simulator.battery.score import _score_store_to_jsonl, load_score_context
from simulator.engine_cache import _engine_cache_path_scope
from tests.battery.test_engine_cache_equivalence import _canonical_residual_rows


ROOT = Path(__file__).resolve().parents[2]


def assert_residue_score_cache_equivalence(
    tmp_path: Path,
    *,
    source: str,
    test_experiment_ids: tuple[str, ...],
    required_refusal_reason: str | None,
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
        with _engine_cache_path_scope(cache_path):
            path = tmp_path / f"{label}.jsonl"
            _row_count, metadata = _score_store_to_jsonl(
                context,
                path,
                root=ROOT,
                engines=(Engine.OPENIMCC,),
                rail=Rail.RESIDUE_COMPOSITION,
                include_diagnostics=False,
                workers=workers,
                _test_experiment_ids=test_experiment_ids,
            )
        return (
            _canonical_residual_rows(path.with_name(path.name + ".partial")),
            metadata.engine_cache_counters or {},
        )

    off, _off_counts = score("off")
    off_parallel, _off_parallel_counts = score("off-parallel", workers=3)
    serial_cache = tmp_path / "openimcc-residue-serial.sqlite"
    parallel_cache = tmp_path / "openimcc-residue-parallel.sqlite"
    cold, cold_counts = score("cold", serial_cache)
    cold_parallel, cold_parallel_counts = score(
        "cold-parallel", parallel_cache, workers=3
    )
    warm, warm_counts = score("warm", serial_cache)
    parallel_warm, parallel_counts = score(
        "parallel-warm", parallel_cache, workers=3
    )

    assert off
    assert len(off.splitlines()) <= 20
    if required_refusal_reason is None:
        # Hashimoto's admitted cells are its five modeled oxide channels. They
        # have no source-level unsupported-species refusal path.
        assert b'"outside_supported_species"' not in off
    else:
        assert f'"reason":"{required_refusal_reason}"'.encode() in off
    if source == "kems-012-sossi-2019":
        rows = off.splitlines()
        numeric_ti_row = False
        for row in rows:
            payload = json.loads(row)
            request = payload.get("candidate_request")
            numeric = payload.get("numeric")
            if (
                isinstance(request, Mapping)
                and request.get("experiment_id") == test_experiment_ids[0]
                and "_ti_" in str(payload.get("key") or "").casefold()
                and isinstance(numeric, Mapping)
                and numeric.get("value") is not None
            ):
                numeric_ti_row = True
                break
        assert numeric_ti_row, (
            "same Sossi cohort's Ti row must have a numeric result"
        )
        typed_missing = [
            row
            for row in rows
            if b'"reason":"starting_component_ppm_missing"' in row
        ]
        assert typed_missing, "Sossi missing starting ppm must be a typed refusal"
        assert len(typed_missing) < len(rows), (
            "a missing starting ppm for one species must not suppress other Sossi rows"
        )
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
    cache_runs = (cold_counts, cold_parallel_counts, warm_counts, parallel_counts)
    engine_entries = [counts.get(Engine.OPENIMCC.value) for counts in cache_runs]
    if any(engine_entries):
        for counts in (cold_counts, cold_parallel_counts):
            engine = counts.get(Engine.OPENIMCC.value)
            assert engine is not None
            assert engine["misses"] > 0
            assert engine["stores"] > 0
        for counts in (warm_counts, parallel_counts):
            engine = counts.get(Engine.OPENIMCC.value)
            assert engine is not None
            assert engine["hits"] > 0
            assert engine["misses"] == 0
    elif source != "kems-012-sossi-2019":
        raise AssertionError("cache gate produced no OpenIMCC cache counters")


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
