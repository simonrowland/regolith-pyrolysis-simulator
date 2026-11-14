from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from scripts import source_battery_report
from simulator.battery.enums import Engine
from simulator.battery.score import refusal_census


def test_landed_richter_source_report_known_counts_and_refusal_reason_tally(
    tmp_path: Path, monkeypatch
) -> None:
    scored_residuals = []
    production_score_store = source_battery_report.score_store

    def capture_score(context, *, engines=None):
        residuals, candidates = production_score_store(context, engines=engines)
        scored_residuals.extend(residuals)
        return residuals, candidates

    monkeypatch.setattr(source_battery_report, "score_store", capture_score)
    report = source_battery_report.build_source_report(
        "kems-010-richter-2007",
        engines=(Engine.INTERNAL_ANALYTICAL,),
        scratch_out_dir=tmp_path / "source-report",
    )

    assert report["extract_rows"] == 3
    assert report["migrated_observations"] == 350

    expected_refusals: Counter[str] = Counter()
    for key, count in refusal_census(scored_residuals).items():
        expected_refusals[key.partition(":")[0]] += count
    assert expected_refusals
    actual_refusals = {
        row["reason"]: row["engine_observation_events"]
        for row in report["scoring"]["refusals_by_reason"]
    }
    # The exact reason tally makes omitting any refusal reason fail this test.
    assert actual_refusals == dict(expected_refusals)

    dropped_reason = next(iter(expected_refusals))

    def tally_without_one_reason(residuals):
        return {
            key: count
            for key, count in refusal_census(residuals).items()
            if key.partition(":")[0] != dropped_reason
        }

    monkeypatch.setattr(
        source_battery_report, "refusal_census", tally_without_one_reason
    )
    mutated_refusals = {
        row["reason"]: row["engine_observation_events"]
        for row in source_battery_report._score_rows(scored_residuals)[
            "refusals_by_reason"
        ]
    }
    with pytest.raises(AssertionError):
        assert mutated_refusals == dict(expected_refusals)
