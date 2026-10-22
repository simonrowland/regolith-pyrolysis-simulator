"""Fixture counts for the residual diagnostics CLI.

Expected counts are read off the jsonl fixtures by hand. They are not
produced by calling the counter under test a second way.
"""

from __future__ import annotations

import io
import tracemalloc
from decimal import Decimal
from pathlib import Path

import pytest

import scripts.battery_residuals_report as residuals_report
from scripts.battery_residuals_report import band_report, funnel_report, main, rowdiff_report

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "battery_residuals_report"
SAMPLE = FIXTURES / "sample.jsonl"
BASE = FIXTURES / "base.jsonl"
TIP = FIXTURES / "tip.jsonl"


def _cell(report, status: str, rail: str, engine: str, reason: str, detail: str) -> int:
    matched = [
        cell.n
        for cell in report.cells
        if (cell.status, cell.rail, cell.engine, cell.reason, cell.detail)
        == (status, rail, engine, reason, detail)
    ]
    assert len(matched) == 1
    return matched[0]


def test_funnel_counts_every_status_rail_engine_and_reason() -> None:
    report = funnel_report(SAMPLE)

    assert report.rows == 10
    assert len(report.cells) == 7
    assert _cell(report, "match", "melt_activity", "openimcc", "", "") == 4
    assert _cell(report, "match", "thermochemistry", "ellingham", "", "") == 1
    assert _cell(report, "mismatch", "melt_activity", "openimcc", "", "") == 1
    assert _cell(report, "no_band", "melt_activity", "openimcc", "", "") == 1
    assert _cell(report, "refused", "melt_activity", "openimcc", "identity_unknown", "quantity_unknown") == 1
    assert _cell(report, "refused", "vapour", "antoine_sidecar", "invalid_source", "") == 1
    assert _cell(report, "refused", "vapour", "magemin", "unsupported", "quantity_not_predicted") == 1


def test_funnel_temperature_band_is_half_open_and_engine_filter() -> None:
    report = funnel_report(
        SAMPLE,
        engine="openimcc",
        t_min=Decimal("1000"),
        t_max=Decimal("1500"),
    )

    # T=1000 and T=1200 match, T=1100 refused. T=1500, T=1800, T=900, and the
    # row with no identity temperature are outside [1000, 1500).
    assert report.rows == 3
    assert _cell(report, "match", "melt_activity", "openimcc", "", "") == 2
    assert _cell(report, "refused", "melt_activity", "openimcc", "identity_unknown", "quantity_unknown") == 1


def test_rowdiff_reports_status_value_and_score_refusal_changes() -> None:
    report = rowdiff_report(BASE, TIP, retain_details=True)

    assert report.base_rows == 4
    assert report.tip_rows == 4
    assert report.base_duplicate_keys == 0
    assert report.tip_duplicate_keys == 0
    assert [(item.engine, item.base_status, item.tip_status, item.n) for item in report.transitions] == [
        ("magemin", "match", "refused", 1),
        ("openimcc", "refused", "no_band", 1),
    ]
    assert report.value_move_count == 1
    assert report.value_moves_above == (
        (Decimal("1e-9"), 1),
        (Decimal("1e-6"), 1),
        (Decimal("1e-3"), 1),
    )
    move = report.value_moves[0]
    assert move.key == "k1:T=1000::activity::melt_activity::openimcc"
    assert move.base_value == Decimal("0.2")
    assert move.tip_value == Decimal("0.5")
    assert move.relative == Decimal("1.5")
    assert report.value_appeared == 1
    assert report.value_cleared == 1
    assert [item.key for item in report.newly_scored] == [
        "k2:T=1100::activity::melt_activity::openimcc"
    ]
    assert report.newly_scored[0].base_status == "refused"
    assert report.newly_scored[0].tip_status == "no_band"
    assert report.newly_scored[0].base_reason == "identity_unknown"
    assert [item.key for item in report.newly_refused] == [
        "k3:T=1200::p_sat::vapour::magemin"
    ]
    assert report.newly_refused[0].tip_reason == "unsupported"
    assert report.newly_refused[0].tip_detail == "quantity_not_predicted"
    assert [item.key for item in report.added] == [
        "k5:T=1400::activity::melt_activity::openimcc"
    ]
    assert [item.key for item in report.removed] == [
        "k4:T=1300::activity::melt_activity::openimcc"
    ]


def test_rowdiff_filter_ignores_rows_outside_the_band_and_engine() -> None:
    by_engine = rowdiff_report(BASE, TIP, engine="openimcc", retain_details=True)

    assert by_engine.base_rows == 3
    assert by_engine.tip_rows == 3
    assert [(item.engine, item.base_status, item.tip_status) for item in by_engine.transitions] == [
        ("openimcc", "refused", "no_band")
    ]
    assert by_engine.newly_refused == ()
    assert [item.key for item in by_engine.removed] == [
        "k4:T=1300::activity::melt_activity::openimcc"
    ]
    assert [item.key for item in by_engine.added] == [
        "k5:T=1400::activity::melt_activity::openimcc"
    ]

    # T=1200 is the exclusive upper bound, so the magemin row is not a removal.
    in_band = rowdiff_report(
        BASE, TIP, t_min=Decimal("1000"), t_max=Decimal("1200"), retain_details=True
    )

    assert in_band.base_rows == 2
    assert in_band.tip_rows == 2
    assert [item.key for item in in_band.newly_scored] == [
        "k2:T=1100::activity::melt_activity::openimcc"
    ]
    assert in_band.newly_refused == ()
    assert in_band.added == ()
    assert in_band.removed == ()
    assert in_band.value_move_count == 1


def test_band_stats_use_only_rows_inside_the_temperature_window() -> None:
    report = band_report(SAMPLE, t_min=Decimal("1000"), t_max=Decimal("1500"))

    assert report.rows == 6
    assert [(item.engine, item.rail, item.status, item.n) for item in report.statuses] == [
        ("antoine_sidecar", "vapour", "refused", 1),
        ("ellingham", "thermochemistry", "match", 1),
        ("magemin", "vapour", "refused", 1),
        ("openimcc", "melt_activity", "match", 2),
        ("openimcc", "melt_activity", "refused", 1),
    ]
    assert [(item.engine, item.operation, item.unit, item.n) for item in report.stats] == [
        ("ellingham", "absolute", "kJ/mol", 1),
        ("openimcc", "dex", "1", 2),
    ]
    ellingham, openimcc = report.stats
    assert ellingham.median == Decimal("2")
    assert ellingham.rms == Decimal("2")
    # 0.2 and 0.4: even-count median is 0.3; rms is the square root of 0.1.
    assert openimcc.median == Decimal("0.3")
    assert format(openimcc.rms, ".12f") == "0.316227766017"


def test_malformed_residual_line_names_the_line(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"kind":"battery_store_stamp","revision":"x"}\n{not json}\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"bad\.jsonl:2:"):
        funnel_report(path)


def test_cli_funnel_rowdiff_and_band_print_hand_written_lines(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["funnel", str(SAMPLE)]) == 0
    funnel_out = capsys.readouterr().out
    assert "rows\t10" in funnel_out
    assert "refused\tmelt_activity\topenimcc\tidentity_unknown\tquantity_unknown\t1" in funnel_out

    assert main(["rowdiff", str(BASE), str(TIP)]) == 0
    rowdiff_out = capsys.readouterr().out
    assert "newly_scored\t1" in rowdiff_out
    assert "newly_refused\t1" in rowdiff_out
    assert (
        "newly_scored_row\topenimcc\tk2:T=1100::activity::melt_activity::openimcc\t"
        "refused\tno_band\tidentity_unknown\tquantity_unknown"
    ) in rowdiff_out
    assert "value_move\topenimcc\tk1:T=1000::activity::melt_activity::openimcc\t0.2\t0.5\t1.5" in rowdiff_out

    assert main(["band", str(SAMPLE), "--t-min", "1000", "--t-max", "1500"]) == 0
    band_out = capsys.readouterr().out
    assert "band\t[1000,1500)" in band_out
    assert "rows\t6" in band_out
    assert "openimcc\tmelt_activity\tmatch\t2" in band_out
    assert "openimcc\tmelt_activity\tdex\t1\t2\t0.3\t" in band_out


def test_cli_rejects_unknown_engine(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["funnel", str(SAMPLE), "--engine", "not-an-engine"]) == 1
    error = capsys.readouterr().err
    assert "not-an-engine" in error
    assert "openimcc" in error


def _write_pair(path: Path, values: tuple[str, ...], *, status: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write('{"kind":"battery_store_stamp","revision":"dup"}\n')
        for value in values:
            handle.write(
                '{"key":"k:T=1000::activity::melt_activity::openimcc",'
                f'"status":"{status}","rail":"melt_activity",'
                '"candidate_request":{"engine":"openimcc"},'
                f'"numeric":{{"operation":"dex","unit":"1","value":"{value}"}}}}\n'
            )


def _write_values(path: Path, rows: tuple[tuple[str, str], ...]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write('{"kind":"battery_store_stamp","revision":"show"}\n')
        for key, value in rows:
            handle.write(
                '{"key":"' + key + '","status":"match","rail":"melt_activity",'
                '"candidate_request":{"engine":"openimcc"},'
                f'"numeric":{{"operation":"dex","unit":"1","value":"{value}"}}}}\n'
            )


def test_rowdiff_disjoint_keys_are_added_or_only_in_base(tmp_path: Path) -> None:
    base = tmp_path / "base.jsonl"
    tip = tmp_path / "tip.jsonl"
    _write_values(base, (("a", "1"), ("b", "1")))
    _write_values(tip, (("c", "4"), ("d", "5")))

    report = rowdiff_report(base, tip, retain_details=True)

    assert [item.key for item in report.added] == ["c", "d"]
    assert [item.key for item in report.removed] == ["a", "b"]
    assert report.value_move_count == 0
    assert report.newly_scored == ()
    assert report.newly_refused == ()


def test_rowdiff_writes_tip_changes_before_the_tip_ledger_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An added tip key is written while tip rows remain; base-only keys wait."""

    base = tmp_path / "base.jsonl"
    tip = tmp_path / "tip.jsonl"
    _write_values(
        base,
        (
            ("a::activity::melt_activity::openimcc", "1"),
            ("r::activity::melt_activity::openimcc", "1"),
        ),
    )
    _write_values(
        tip,
        (
            ("b::activity::melt_activity::openimcc", "1"),
            ("a::activity::melt_activity::openimcc", "2"),
            ("c::activity::melt_activity::openimcc", "1"),
        ),
    )
    state = {"tip_done": False, "wrote_during": False, "removed_early": False}
    real_iter = residuals_report.iter_residual_rows

    def wrapped(path: Path):
        if path == tip:
            yield from real_iter(path)
            state["tip_done"] = True
        else:
            yield from real_iter(path)

    monkeypatch.setattr(residuals_report, "iter_residual_rows", wrapped)

    class Sink(io.StringIO):
        def write(self, text: str) -> int:
            if text.startswith("added_row") and not state["tip_done"]:
                state["wrote_during"] = True
            if text.startswith("removed_row") and not state["tip_done"]:
                state["removed_early"] = True
            return super().write(text)

    sink = Sink()
    report = rowdiff_report(base, tip, show=5, out=sink)

    assert state["wrote_during"]
    assert not state["removed_early"]
    assert [item.key for item in report.value_moves] == ["a::activity::melt_activity::openimcc"]
    assert "added_row\topenimcc\tb::activity::melt_activity::openimcc\t" in sink.getvalue()
    assert "removed_row\topenimcc\tr::activity::melt_activity::openimcc\t" in sink.getvalue()


def test_rowdiff_show_keeps_the_largest_relative_moves(tmp_path: Path) -> None:
    """show=2 keeps the two largest |relative| moves, b=3 and c=2.5, not a=2."""

    base = tmp_path / "base.jsonl"
    tip = tmp_path / "tip.jsonl"
    _write_values(base, (("a", "1"), ("b", "1"), ("c", "1")))
    _write_values(tip, (("a", "2"), ("b", "3"), ("c", "2.5")))

    report = rowdiff_report(base, tip, show=2)

    assert report.value_move_count == 3
    assert [(item.key, item.relative) for item in report.value_moves] == [
        ("b", Decimal("2")),
        ("c", Decimal("1.5")),
    ]


def test_duplicate_keys_keep_the_last_row(tmp_path: Path) -> None:
    base = tmp_path / "base.jsonl"
    tip = tmp_path / "tip.jsonl"
    # The second base row is the one that counts, and it is a mismatch.
    base.write_text(
        '{"kind":"battery_store_stamp","revision":"dup"}\n'
        '{"key":"k:T=1000::activity::melt_activity::openimcc","status":"match",'
        '"rail":"melt_activity","candidate_request":{"engine":"openimcc"},'
        '"numeric":{"operation":"dex","unit":"1","value":"0.2"}}\n'
        '{"key":"k:T=1000::activity::melt_activity::openimcc","status":"mismatch",'
        '"rail":"melt_activity","candidate_request":{"engine":"openimcc"},'
        '"numeric":{"operation":"dex","unit":"1","value":"0.4"}}\n',
        encoding="utf-8",
    )
    _write_pair(tip, ("0.1", "0.4"), status="match")

    report = rowdiff_report(base, tip, retain_details=True)

    assert report.base_rows == 2
    assert report.tip_rows == 2
    assert report.base_duplicate_keys == 1
    assert report.tip_duplicate_keys == 1
    assert report.value_move_count == 0
    assert report.value_appeared == 0
    assert report.value_cleared == 0
    assert [(item.base_status, item.tip_status, item.n) for item in report.transitions] == [
        ("mismatch", "match", 1)
    ]
    assert report.newly_scored == ()
    assert report.newly_refused == ()
    assert report.added == ()
    assert report.removed == ()


def _write_ledger(path: Path, n: int, value: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write('{"kind":"battery_store_stamp","revision":"mem"}\n')
        for index in range(n):
            handle.write(
                '{"key":"k'
                f'{index:06d}'
                ':T=1000::activity::melt_activity::openimcc","status":"match",'
                '"rail":"melt_activity","candidate_request":{"engine":"openimcc"},'
                f'"numeric":{{"operation":"dex","unit":"1","value":"{value}"}}}}\n'
            )


@pytest.mark.nightly
def test_rowdiff_and_band_peak_memory_stay_compact(tmp_path: Path) -> None:
    """100_000 overlapping keys stay within the one-side base index.

    That index is key to (status code, float). On this fixture it traces
    about 21 MB. The previous nine-field union of both sides traced
    28_871_392 bytes here and is over this cap, as is a full list of the
    100_000 moves or a pair of RowView indexes. The band bound is unchanged.
    """

    n = 100_000
    base = tmp_path / "base.jsonl"
    tip = tmp_path / "tip.jsonl"
    _write_ledger(base, n, "0.2")
    _write_ledger(tip, n, "0.4")

    tracemalloc.start()
    report = rowdiff_report(base, tip, show=5)
    _current, rowdiff_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert report.base_rows == n
    assert report.tip_rows == n
    assert report.value_move_count == n
    assert len(report.value_moves) == 5
    assert report.newly_scored == ()
    assert report.added == ()
    assert report.removed == ()
    assert rowdiff_peak < 24_000_000

    tracemalloc.start()
    band = band_report(base, t_min=Decimal("0"), t_max=Decimal("2000"))
    _current, band_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert band.rows == n
    assert len(band.stats) == 1
    assert band.stats[0].n == n
    assert band.stats[0].median == Decimal("0.2")
    assert band_peak < 20_000_000
