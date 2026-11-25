"""Report-only limits must disclose changed outcomes omitted from the table."""

from __future__ import annotations

import pytest

from simulator.battery.enums import Engine
from simulator.battery import score_report


def _status_diff_report() -> str:
    from simulator.battery.score import render_score_report_from_payloads

    return render_score_report_from_payloads(
        [],
        engines=(Engine.INTERNAL_ANALYTICAL,),
        hostname="test",
        status_diff=[
            {
                "old_key": f"changed-{index}",
                "old": "match",
                "new": "mismatch",
                "axis": "outcome",
            }
            for index in range(205)
        ],
    )


def _assert_status_diff_is_disclosed(report: str) -> None:
    assert "`changed-199`" in report
    assert "`changed-200`" not in report
    assert "5 more status changes omitted" in report


def test_payload_report_discloses_status_diff_truncation() -> None:
    _assert_status_diff_is_disclosed(_status_diff_report())


def _calibration_report(monkeypatch: pytest.MonkeyPatch, tmp_path) -> str:
    from scripts import calibration_battery
    from simulator.battery import score

    residuals = tmp_path / "data" / "battery" / "residuals.jsonl"
    residuals.parent.mkdir(parents=True)
    residuals.write_text("", encoding="utf-8")
    diffs = [
        {
            "old_key": f"changed-{index}",
            "old": "match",
            "new": "mismatch",
            "axis": "outcome",
        }
        for index in range(205)
    ]
    monkeypatch.setattr(calibration_battery, "ROOT", tmp_path)
    monkeypatch.setattr(score, "load_residuals_jsonl", lambda _path: ())
    monkeypatch.setattr(score, "status_diff_rows", lambda **_kwargs: (diffs, []))
    return calibration_battery.v21_view_section([])


def test_calibration_battery_view_discloses_status_diff_truncation(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _assert_status_diff_is_disclosed(_calibration_report(monkeypatch, tmp_path))


def test_mutation_restoring_silent_status_diff_truncation_is_caught(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    def silently_truncated_status_diff(status_diff, *, limit=None):
        visible = status_diff if limit is None else status_diff[:limit]
        return [
            "| old key | old | new | axis |",
            "|---|---|---|---|",
            *(
                f"| `{row.get('old_key')}` | {row.get('old')} | "
                f"{row.get('new')} | {row.get('axis')} |"
                for row in visible
            ),
        ]

    monkeypatch.setattr(
        score_report, "_status_diff_report_lines", silently_truncated_status_diff
    )
    with pytest.raises(AssertionError, match="5 more status changes omitted"):
        _assert_status_diff_is_disclosed(_status_diff_report())
    with pytest.raises(AssertionError, match="5 more status changes omitted"):
        _assert_status_diff_is_disclosed(_calibration_report(monkeypatch, tmp_path))
