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
        scratch_out_dir=tmp_path / "source-report",
    )

    assert report["extract_rows"] == 197
    assert report["migrated_observations"] == 350
    assert report["candidates"]["admitted"]["count"] == 254
    assert report["candidates"]["pending"]["count"] == 85
    assert report["hard_issue_count"] == 0
    assert report["scoring"]["engine_observation_events"] == 2712
    assert report["scoring"]["numeric_residuals"] == {
        "distinct_observations": 0,
        "engine_observation_events": 0,
    }
    assert report["scoring"]["score_eligible"] == {
        "distinct_observations": 0,
        "engine_observation_events": 0,
    }
    assert report["scoring"]["inside_band"] == {
        "distinct_observations": 0,
        "engine_observation_events": 0,
    }

    expected_refusals: Counter[str] = Counter()
    expected_detail_reasons: Counter[tuple[str, str | None]] = Counter()
    for key, count in refusal_census(scored_residuals).items():
        reason, separator, detail_reason = key.partition(":")
        expected_refusals[reason] += count
        expected_detail_reasons[(reason, detail_reason if separator else None)] += count
    assert expected_refusals
    actual_refusals = {
        row["reason"]: (row["distinct_observations"], row["engine_observation_events"])
        for row in report["scoring"]["refusals_by_reason"]
    }
    assert actual_refusals == {
        "attempted_unavailable": (21, 21),
        "identity_incomplete": (5, 10),
        "identity_unknown": (85, 680),
        "underdetermined_apparatus": (45, 69),
        "unsupported": (254, 1932),
    }
    assert all(
        len(row["example_observation_ids"]) == 3
        for row in report["scoring"]["refusals_by_reason"]
    )
    # The exact reason tally makes omitting any refusal reason fail this test.
    actual_refusal_counts = {
        row["reason"]: row["engine_observation_events"]
        for row in report["scoring"]["refusals_by_reason"]
    }
    assert actual_refusal_counts == dict(expected_refusals)
    actual_detail_reasons = {
        (row["reason"], detail["reason"]): detail["engine_observation_events"]
        for row in report["scoring"]["refusals_by_reason"]
        for detail in row["detail_reasons"]
    }
    assert actual_detail_reasons == dict(expected_detail_reasons)

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
        row["reason"]: (row["distinct_observations"], row["engine_observation_events"])
        for row in source_battery_report._score_rows(scored_residuals)[
            "refusals_by_reason"
        ]
    }
    with pytest.raises(AssertionError):
        assert mutated_refusals == {
            "attempted_unavailable": (21, 21),
            "identity_incomplete": (5, 10),
            "identity_unknown": (85, 680),
            "underdetermined_apparatus": (45, 69),
            "unsupported": (254, 1932),
        }


def test_landed_extract_without_rows_is_reported_explicitly(tmp_path: Path) -> None:
    report = source_battery_report.build_source_report(
        "yang-2025-na2o-cao-silica-thermodynamics",
        engines=(Engine.INTERNAL_ANALYTICAL,),
        scratch_out_dir=tmp_path / "empty-source-report",
    )

    assert report["extract_rows"] == 0
    assert report["extract_has_rows"] is False
    assert "extract has no rows" in source_battery_report._markdown(report)
