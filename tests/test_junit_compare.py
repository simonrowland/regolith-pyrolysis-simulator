"""Fixture outcomes for the junit compare CLI.

Expected ids and counts are read off the XML fixtures by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.junit_compare import compare_junit, main

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "junit_compare"
BASE = FIXTURES / "base.xml"
TIP = FIXTURES / "tip.xml"


def test_new_fails_new_passes_and_changed_outcomes() -> None:
    report = compare_junit(BASE, TIP)

    assert report.base_cases == 6
    assert report.tip_cases == 8
    assert report.base_failing == 4
    assert report.tip_failing == 5
    assert report.base_skipped == 1
    assert report.tip_skipped == 0
    assert [(item.kind, item.case_id, item.base_outcome, item.tip_outcome) for item in report.changes] == [
        ("new_pass", "a.A::test_fail", "failed", "passed"),
        ("changed", "a.A::test_flaky", "failed", "failed"),
        ("changed", "a.A::test_ok2", "", "passed"),
        ("new_fail", "a.A::test_skip", "skipped", "failed"),
        ("new_fail", "a.C::test_new", "", "failed"),
    ]
    flaky = report.changes[1]
    assert "Obtained: 1.000000" in flaky.base_message
    assert "Obtained: 2.000000" in flaky.tip_message
    assert "/Users/" not in flaky.base_message
    assert "/Users/" not in flaky.tip_message
    assert "0x1a2b" not in flaky.base_message
    assert "0x9fff" not in flaky.tip_message
    assert "HOME" in flaky.tip_message
    assert "popen-gwN" in flaky.tip_message
    assert "Ns" in flaky.tip_message


def test_volatile_paths_and_timings_are_not_outcome_changes() -> None:
    report = compare_junit(BASE, TIP)
    changed_ids = {item.case_id for item in report.changes}

    assert "a.A::test_volatile" not in changed_ids
    assert "a.A::test_ok" not in changed_ids
    assert "a.B::test_err" not in changed_ids


def test_cli_prints_the_three_buckets(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(BASE), str(TIP)]) == 0
    text = capsys.readouterr().out

    assert "base_cases\t6\tbase_failing\t4\tbase_skipped\t1" in text
    assert "tip_cases\t8\ttip_failing\t5\ttip_skipped\t0" in text
    assert "new_fail\t2" in text
    assert "new_pass\t1" in text
    assert "changed\t2" in text
    assert "new_fail\ta.A::test_skip\tskipped\tfailed" in text
    assert "new_pass\ta.A::test_fail\tfailed\tpassed" in text
    assert "new_fail\ta.C::test_new\t\tfailed" in text
    assert "/Users/" not in text
    assert "a.A::test_volatile" not in text
