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
from scripts.battery_residuals_report import (
    DuplicateResidualKey,
    band_report,
    funnel_report,
    main,
    rowdiff_report,
)

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


def test_rowdiff_duplicate_base_key_is_an_input_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A repeated base key names that key and line and writes nothing."""

    base = tmp_path / "base.jsonl"
    tip = tmp_path / "tip.jsonl"
    _write_values(base, (("k", "1"), ("k", "2")))
    _write_values(tip, (("other", "1"),))
    sink = io.StringIO()

    with pytest.raises(DuplicateResidualKey, match=r":3: duplicate residual key k$") as raised:
        rowdiff_report(base, tip, out=sink)

    assert raised.value.line == 3
    assert raised.value.key == "k"
    assert sink.getvalue() == ""
    assert main(["rowdiff", str(base), str(tip)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "duplicate residual key k" in captured.err


def test_rowdiff_duplicate_tip_key_is_an_input_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A repeated tip key names that key and line and marks earlier lines invalid."""

    base = tmp_path / "base.jsonl"
    tip = tmp_path / "tip.jsonl"
    _write_values(base, (("base-only", "1"),))
    _write_values(tip, (("ok", "1"), ("dup", "1"), ("dup", "2")))
    sink = io.StringIO()

    with pytest.raises(DuplicateResidualKey, match=r":4: duplicate residual key dup$") as raised:
        rowdiff_report(base, tip, out=sink)

    assert raised.value.line == 4
    assert raised.value.key == "dup"
    text = sink.getvalue()
    assert "added_row\topenimcc\tok\t" in text
    assert "rowdiff_invalid\t4\tdup" in text
    assert "base_rows" not in text
    assert main(["rowdiff", str(base), str(tip)]) == 1
    captured = capsys.readouterr()
    assert "rowdiff_invalid\t4\tdup" in captured.out
    assert "duplicate residual key dup" in captured.err

    _write_values(base, (("m", "1"),))
    _write_values(tip, (("m", "1"), ("m", "2")))
    seen = io.StringIO()
    with pytest.raises(DuplicateResidualKey, match=r":3: duplicate residual key m$") as repeated:
        rowdiff_report(base, tip, out=seen)
    assert repeated.value.line == 3
    assert repeated.value.key == "m"
    assert seen.getvalue() == "rowdiff_invalid\t3\tm\n"


def test_rowdiff_duplicate_key_outside_the_filter_is_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A repeated key the report filter would drop is still an input error."""

    key = "outside:T=1000::activity::melt_activity::openimcc"
    bad = tmp_path / "bad.jsonl"
    empty = tmp_path / "empty.jsonl"
    _write_values(bad, ((key, "1"), (key, "2")))
    empty.write_text('{"kind":"battery_store_stamp","revision":"show"}\n', encoding="utf-8")

    base_sink = io.StringIO()
    with pytest.raises(DuplicateResidualKey, match=r":3: duplicate residual key ") as raised:
        rowdiff_report(bad, empty, t_min=Decimal("1500"), t_max=Decimal("2000"), out=base_sink)

    assert raised.value.line == 3
    assert raised.value.key == key
    assert base_sink.getvalue() == ""
    assert main(["rowdiff", str(bad), str(empty), "--t-min", "1500", "--t-max", "2000"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert key in captured.err

    tip_sink = io.StringIO()
    with pytest.raises(DuplicateResidualKey, match=r":3: duplicate residual key ") as tip_raised:
        rowdiff_report(empty, bad, t_min=Decimal("1500"), t_max=Decimal("2000"), out=tip_sink)

    assert tip_raised.value.line == 3
    assert tip_raised.value.key == key
    assert tip_sink.getvalue() == f"rowdiff_invalid\t3\t{key}\n"
    assert main(["rowdiff", str(empty), str(bad), "--t-min", "1500", "--t-max", "2000"]) == 1
    captured = capsys.readouterr()
    assert "base_rows" not in captured.out
    assert f"rowdiff_invalid\t3\t{key}" in captured.out
    assert key in captured.err

    engine_base = io.StringIO()
    with pytest.raises(DuplicateResidualKey, match=r":3: duplicate residual key ") as engine_raised:
        rowdiff_report(bad, empty, engine="magemin", out=engine_base)
    assert engine_raised.value.key == key
    assert engine_base.getvalue() == ""

    engine_tip = io.StringIO()
    with pytest.raises(DuplicateResidualKey, match=r":3: duplicate residual key ") as engine_tip_raised:
        rowdiff_report(empty, bad, engine="magemin", out=engine_tip)
    assert engine_tip_raised.value.key == key
    assert engine_tip.getvalue() == f"rowdiff_invalid\t3\t{key}\n"


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
    100_000 moves or a pair of RowView indexes. Tip keys absent from the base
    index are a separate set; this fixture has none, and no filter retains
    a filtered base key either. The band bound is unchanged.
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


class _AddedCount:
    """Count added-row lines without retaining them."""

    def __init__(self) -> None:
        self.added = 0

    def write(self, text: str) -> int:
        if text.startswith("added_row"):
            self.added += 1
        return len(text)


@pytest.mark.nightly
def test_rowdiff_empty_base_and_unique_tip_stay_compact(tmp_path: Path) -> None:
    """An empty base retains each tip key string, not a RowView.

    A set of these 100_000 keys traced 13_384_428 bytes, 134 bytes per key.
    This call traced 13_390_297. The cap is 160 bytes per key, restated from
    that measured cost. A RowView per row of the same ledger traced
    45_816_746 bytes, 458 bytes per key, and is over the cap.
    """

    n = 100_000
    bytes_per_tip_key = 160
    base = tmp_path / "base.jsonl"
    tip = tmp_path / "tip.jsonl"
    base.write_text('{"kind":"battery_store_stamp","revision":"mem"}\n', encoding="utf-8")
    _write_ledger(tip, n, "0.4")
    sink = _AddedCount()

    tracemalloc.start()
    report = rowdiff_report(base, tip, show=5, out=sink)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert report.base_rows == 0
    assert report.tip_rows == n
    assert report.value_move_count == 0
    assert report.added == ()
    assert sink.added == n
    assert peak < n * bytes_per_tip_key
