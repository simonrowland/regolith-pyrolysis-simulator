from __future__ import annotations

import pytest

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
