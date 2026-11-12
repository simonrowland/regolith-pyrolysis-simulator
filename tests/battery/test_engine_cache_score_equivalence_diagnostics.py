from __future__ import annotations

import pytest

from tests.battery.test_engine_cache_equivalence import _canonical_residual_rows
from tests.battery.engine_cache_score_equivalence import _assert_runs_match_off


def test_equivalence_failure_names_run_and_shows_first_row_diff() -> None:
    off = b'row-0\nrow-1\nrow-2\nrow-3\n'
    cold = b'row-0\nrow-1-changed\nrow-2\nrow-3-changed\n'

    with pytest.raises(AssertionError) as error:
        _assert_runs_match_off(off, {"cold": cold})

    message = str(error.value)
    assert "cold differs from off" in message
    assert "--- off" in message
    assert "+++ cold" in message
    assert "-row-1" in message
    assert "+row-1-changed" in message
    assert "row-3" not in message


def test_matching_runs_pass() -> None:
    _assert_runs_match_off(b"row-0\n", {"cold": b"row-0\n"})


def test_canonical_residual_rows_exclude_process_timing(tmp_path) -> None:
    off = tmp_path / "off.jsonl"
    cold = tmp_path / "cold.jsonl"
    off.write_text(
        '{"id":"residue-1","provenance":{"integration":'
        '{"steps":4,"process_cpu_seconds":1.2,'
        '"process_cpu_seconds_by_geometry":{"main":1.2}}}}\n',
        encoding="utf-8",
    )
    cold.write_text(
        '{"id":"residue-1","provenance":{"integration":'
        '{"steps":4,"process_cpu_seconds":8.4,'
        '"process_cpu_seconds_by_geometry":{"main":8.4}}}}\n',
        encoding="utf-8",
    )

    assert _canonical_residual_rows(off) == _canonical_residual_rows(cold)
