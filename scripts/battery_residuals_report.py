#!/usr/bin/env python3
"""Streaming diagnostics for a battery residuals ledger.

Reads ``residuals.jsonl`` one line at a time. The store-stamp line is skipped.
Rail, engine, status, refusal reason, and metric operation tokens come from
``simulator.battery.enums``. Identity temperature comes from
``residual_key_slots``. Whether a status stored a numeric comparison comes
from ``numeric_comparison_transition``. Median and root-mean-square are the
score report's ``_median`` and ``_rms``, applied to one float64 group by
``_median_rms_from_float64``.

A temperature band is half-open: ``t_min <= T < t_max``, in kelvin, using the
``:T=`` suffix of the residual key. A row with no identity temperature is
outside every band. ``--engine`` keeps one engine token.

Subcommands::

    python scripts/battery_residuals_report.py funnel LEDGER
        [--engine ENGINE] [--t-min K] [--t-max K]

    python scripts/battery_residuals_report.py rowdiff BASE TIP
        [--engine ENGINE] [--t-min K] [--t-max K] [--show N]

    python scripts/battery_residuals_report.py band LEDGER
        --t-min K --t-max K [--engine ENGINE]

``funnel`` counts rows by status, rail, engine, refusal reason, and the
stored refusal-detail reason. Its memory follows the number of distinct
count cells.

``rowdiff`` compares two ledgers by residual key. It reports status
transitions per engine, numeric value changes, keys that were refused and
are now scored, keys that were scored and are now refused, and keys added
or removed inside the same filter. Newly scored and newly refused require
the key in both files. A filter that drops a key does not count it as added
or removed. One map holds the union: each distinct key stores a status code
and a float64 value for each side that contained it. The tip file is folded
into that map and is not a second index. Duplicate keys in one file keep
the last row. A hundred thousand distinct keys of the fixture shape use
about 29 MB; about 290 MB per million distinct keys, dominated by the key
text. Detail rows are written from that map. At most ``show``
value moves are kept, the largest relative changes; the counts include
every change. A float64 value that does not round-trip through decimal text
can hide a difference smaller than float64.

``band`` reports, inside the temperature window, status counts and the
median and root-mean-square of stored numeric values grouped by engine,
rail, metric operation, and unit. Numeric values reside in ``array('d')``,
8 MB per million values. Computing the median of a group builds one
temporary Decimal per value in that group (about 130 MB per million values
in the largest group) and then drops it.

Exit status is 0 for a report, 1 for an unreadable file or a bad row.
"""

from __future__ import annotations

import argparse
import array
import heapq
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Mapping, Sequence, TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.enums import (  # noqa: E402
    Engine,
    MetricOperation,
    Rail,
    RefusalReason,
    ResidualStatus,
    residual_status_token,
)
from simulator.battery.score import (  # noqa: E402
    NO_HEADLINE_RAIL_TOKEN,
    _engine_from_payload,
    _median_rms_from_float64,
    iter_residual_payloads,
    numeric_comparison_transition,
    residual_key_slots,
)

# Display buckets for a relative numeric move. Not acceptance thresholds.
_RELATIVE_BUCKETS: tuple[Decimal, ...] = (
    Decimal("1e-9"),
    Decimal("1e-6"),
    Decimal("1e-3"),
)
_STATUS_NAMES = tuple(status.value for status in ResidualStatus)
_STATUS_CODE = {name: index for index, name in enumerate(_STATUS_NAMES)}
_ABSENT = -1
_UNKNOWN = -2
# base code, base value, base reason, base detail,
# tip code, tip value, tip reason, tip detail, engine
_Record = tuple[int, float | None, str, str, int, float | None, str, str, str]


@dataclass(frozen=True)
class RowView:
    key: str
    status: str
    rail: str
    engine: str
    reason: str
    detail: str
    temperature_K: Decimal | None
    operation: str
    unit: str
    value: Decimal | None


@dataclass(frozen=True)
class FunnelCell:
    status: str
    rail: str
    engine: str
    reason: str
    detail: str
    n: int


@dataclass(frozen=True)
class FunnelReport:
    rows: int
    cells: tuple[FunnelCell, ...]


@dataclass(frozen=True)
class StatusTransition:
    engine: str
    base_status: str
    tip_status: str
    n: int


@dataclass(frozen=True)
class ValueMove:
    engine: str
    key: str
    base_value: Decimal
    tip_value: Decimal
    relative: Decimal


@dataclass(frozen=True)
class KeyChange:
    key: str
    engine: str
    base_status: str
    tip_status: str
    base_reason: str
    tip_reason: str
    base_detail: str
    tip_detail: str


@dataclass(frozen=True)
class RowDiffReport:
    base_rows: int
    tip_rows: int
    base_duplicate_keys: int
    tip_duplicate_keys: int
    transitions: tuple[StatusTransition, ...]
    value_move_count: int
    value_moves_above: tuple[tuple[Decimal, int], ...]
    value_moves: tuple[ValueMove, ...]
    value_appeared: int
    value_cleared: int
    newly_scored: tuple[KeyChange, ...]
    newly_refused: tuple[KeyChange, ...]
    added: tuple[KeyChange, ...]
    removed: tuple[KeyChange, ...]


@dataclass(frozen=True)
class BandStatus:
    engine: str
    rail: str
    status: str
    n: int


@dataclass(frozen=True)
class BandStat:
    engine: str
    rail: str
    operation: str
    unit: str
    n: int
    median: Decimal
    rms: Decimal


@dataclass(frozen=True)
class BandReport:
    t_min: Decimal
    t_max: Decimal
    rows: int
    statuses: tuple[BandStatus, ...]
    stats: tuple[BandStat, ...]


def _stored_token(enum_cls: type[Engine] | type[Rail] | type[RefusalReason] | type[MetricOperation], raw: object) -> str:
    text = str(raw)
    try:
        return enum_cls(text).value
    except ValueError:
        return text


def _decimal_value(raw: object) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, Decimal):
        value = raw
    else:
        try:
            value = Decimal(str(raw).strip())
        except InvalidOperation:
            return None
    if not value.is_finite():
        return None
    return value


def _rail_of(row: Mapping[str, object], key_rail: str) -> str:
    raw = row.get("rail")
    if raw:
        return _stored_token(Rail, raw)
    if key_rail == NO_HEADLINE_RAIL_TOKEN:
        return NO_HEADLINE_RAIL_TOKEN
    if key_rail:
        return _stored_token(Rail, key_rail)
    return ""


def _engine_of(row: Mapping[str, object]) -> str:
    return _stored_token(Engine, _engine_from_payload(row))


def _status_of(row: Mapping[str, object]) -> str:
    token = residual_status_token(row.get("status"))
    if token is not None:
        return token.value
    raw = row.get("status")
    return "" if raw is None else str(raw)


def _refusal_of(row: Mapping[str, object]) -> tuple[str, str]:
    refusal = row.get("refusal")
    if not isinstance(refusal, Mapping):
        return ("", "")
    reason_raw = refusal.get("reason")
    reason = "" if reason_raw is None else _stored_token(RefusalReason, reason_raw)
    detail = refusal.get("detail")
    detail_reason = ""
    if isinstance(detail, Mapping) and detail.get("reason") is not None:
        detail_reason = str(detail.get("reason"))
    return (reason, detail_reason)


def _view(row: Mapping[str, object]) -> RowView:
    _quantity, key_rail, _key_engine, temperature = residual_key_slots(row.get("key"))
    numeric = row.get("numeric")
    operation = ""
    unit = ""
    value = None
    if isinstance(numeric, Mapping):
        operation_raw = numeric.get("operation")
        if operation_raw:
            operation = _stored_token(MetricOperation, operation_raw)
        unit_raw = numeric.get("unit")
        unit = "" if unit_raw is None else str(unit_raw)
        value = _decimal_value(numeric.get("value"))
    reason, detail = _refusal_of(row)
    key = row.get("key")
    return RowView(
        key="" if key is None else str(key),
        status=_status_of(row),
        rail=_rail_of(row, key_rail),
        engine=_engine_of(row),
        reason=reason,
        detail=detail,
        temperature_K=temperature,
        operation=operation,
        unit=unit,
        value=value,
    )


def iter_residual_rows(path: Path) -> Iterator[tuple[int, dict[str, object]]]:
    """Yield ``(line_number, object)`` for residual rows, skipping the store stamp."""

    if not path.is_file():
        return
    yield from iter_residual_payloads(path, strict=True)


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"not a file: {path}")


def _engine_value(engine: str | None) -> str | None:
    if engine is None:
        return None
    try:
        return Engine(engine).value
    except ValueError:
        known = ", ".join(item.value for item in Engine)
        raise ValueError(f"unknown engine {engine!r}; known: {known}") from None


def _check_bounds(t_min: Decimal | None, t_max: Decimal | None, *, both: bool) -> None:
    if both and (t_min is None or t_max is None):
        raise ValueError("a temperature band needs both t_min and t_max")
    if t_min is not None and t_max is not None and t_min >= t_max:
        raise ValueError(f"temperature band [{_num(t_min)},{_num(t_max)}) is empty")


def _in_band(temperature: Decimal | None, t_min: Decimal | None, t_max: Decimal | None) -> bool:
    if t_min is None and t_max is None:
        return True
    if temperature is None:
        return False
    if t_min is not None and temperature < t_min:
        return False
    if t_max is not None and temperature >= t_max:
        return False
    return True


def _accept(view: RowView, engine: str | None, t_min: Decimal | None, t_max: Decimal | None) -> bool:
    if engine is not None and view.engine != engine:
        return False
    return _in_band(view.temperature_K, t_min, t_max)


def _num(value: Decimal) -> str:
    return format(value, "f")


def funnel_report(
    path: Path,
    *,
    engine: str | None = None,
    t_min: Decimal | None = None,
    t_max: Decimal | None = None,
) -> FunnelReport:
    """Count residual rows by status, rail, engine, and refusal reason."""

    _require_file(path)
    engine_value = _engine_value(engine)
    _check_bounds(t_min, t_max, both=False)
    counts: Counter[tuple[str, str, str, str, str]] = Counter()
    rows = 0
    for _number, row in iter_residual_rows(path):
        view = _view(row)
        if not _accept(view, engine_value, t_min, t_max):
            continue
        rows += 1
        counts[(view.status, view.rail, view.engine, view.reason, view.detail)] += 1
    cells = tuple(
        FunnelCell(status, rail, eng, reason, detail, n)
        for (status, rail, eng, reason, detail), n in sorted(counts.items())
    )
    return FunnelReport(rows=rows, cells=cells)


def _relative(base: Decimal, tip: Decimal) -> Decimal:
    scale = abs(base)
    if scale == 0:
        scale = Decimal("1e-300")
    return abs(tip - base) / scale


def _status_code(status: str) -> int:
    return _STATUS_CODE.get(status, _UNKNOWN)


def _status_text(code: int, raw: str) -> str:
    if code == _ABSENT:
        return ""
    if code == _UNKNOWN:
        return raw
    return _STATUS_NAMES[code]


def _blank_record() -> _Record:
    return (_ABSENT, None, "", "", _ABSENT, None, "", "", "")


def _index_side(
    path: Path,
    *,
    engine: str | None,
    t_min: Decimal | None,
    t_max: Decimal | None,
    index: dict[str, _Record],
    raw_status: dict[tuple[str, str], str],
    side: str,
) -> tuple[int, int]:
    """Fold one ledger into ``index``. Last row for a key wins."""

    rows = 0
    duplicates = 0
    code_at = 0 if side == "base" else 4
    for _number, row in iter_residual_rows(path):
        view = _view(row)
        if not _accept(view, engine, t_min, t_max):
            continue
        rows += 1
        if not view.key:
            continue
        code = _status_code(view.status)
        if code == _UNKNOWN:
            raw_status[(view.key, side)] = view.status
        value = None if view.value is None else float(view.value)
        current = index.get(view.key)
        if current is None:
            current = _blank_record()
        elif current[code_at] != _ABSENT:
            duplicates += 1
        engine_name = view.engine or current[8]
        if side == "base":
            updated = (
                code,
                value,
                view.reason,
                view.detail,
                current[4],
                current[5],
                current[6],
                current[7],
                engine_name,
            )
        else:
            updated = (
                current[0],
                current[1],
                current[2],
                current[3],
                code,
                value,
                view.reason,
                view.detail,
                engine_name,
            )
        index[view.key] = updated
    return rows, duplicates


def _change_of(key: str, record: _Record, raw_status: Mapping[tuple[str, str], str]) -> KeyChange:
    return KeyChange(
        key=key,
        engine=record[8],
        base_status=_status_text(record[0], raw_status.get((key, "base"), "")),
        tip_status=_status_text(record[4], raw_status.get((key, "tip"), "")),
        base_reason=record[2],
        tip_reason=record[6],
        base_detail=record[3],
        tip_detail=record[7],
    )


class _ReverseKey:
    """Heap rank that treats a greater key as smaller, so it is evicted first."""

    __slots__ = ("key",)

    def __init__(self, key: str) -> None:
        self.key = key

    def __lt__(self, other: _ReverseKey) -> bool:
        return self.key > other.key

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _ReverseKey) and self.key == other.key


def _keep_move(
    chosen: list[tuple[Decimal, _ReverseKey, ValueMove]],
    move: ValueMove,
    show: int,
) -> None:
    """Keep at most ``show`` moves with the largest absolute relative change.

    The heap root is the worst retained move: the smaller relative change,
    and, on a tie, the greater key. A smaller key outranks a greater one.
    """

    if show <= 0:
        return
    item = (move.relative, _ReverseKey(move.key), move)
    if len(chosen) < show:
        heapq.heappush(chosen, item)
        return
    if item[:2] > chosen[0][:2]:
        heapq.heapreplace(chosen, item)


def _summarize(
    index: Mapping[str, _Record],
    raw_status: Mapping[tuple[str, str], str],
    *,
    show: int,
    retain_details: bool,
) -> tuple[RowDiffReport, int, int, int, int]:
    transitions: Counter[tuple[str, str, str]] = Counter()
    moves: list[tuple[Decimal, _ReverseKey, ValueMove]] = []
    above = {threshold: 0 for threshold in _RELATIVE_BUCKETS}
    appeared = 0
    cleared = 0
    move_count = 0
    newly_scored: list[KeyChange] = []
    newly_refused: list[KeyChange] = []
    added: list[KeyChange] = []
    removed: list[KeyChange] = []
    scored_n = 0
    refused_n = 0
    added_n = 0
    removed_n = 0
    for key, record in index.items():
        change = _change_of(key, record, raw_status)
        if record[0] == _ABSENT:
            added_n += 1
            if retain_details:
                added.append(change)
            continue
        if record[4] == _ABSENT:
            removed_n += 1
            if retain_details:
                removed.append(change)
            continue
        if change.base_status != change.tip_status:
            transitions[(change.engine, change.base_status, change.tip_status)] += 1
        base_value = record[1]
        tip_value = record[5]
        if base_value is not None and tip_value is not None and base_value != tip_value:
            base_dec = Decimal(str(base_value))
            tip_dec = Decimal(str(tip_value))
            relative = _relative(base_dec, tip_dec)
            move_count += 1
            _keep_move(moves, ValueMove(change.engine, key, base_dec, tip_dec, relative), show)
            for threshold in _RELATIVE_BUCKETS:
                if relative > threshold:
                    above[threshold] += 1
        elif base_value is None and tip_value is not None:
            appeared += 1
        elif base_value is not None and tip_value is None:
            cleared += 1
        kind = numeric_comparison_transition(change.base_status, change.tip_status)
        if kind == "scored":
            scored_n += 1
            if retain_details:
                newly_scored.append(change)
        elif kind == "refused":
            refused_n += 1
            if retain_details:
                newly_refused.append(change)
    ordered = sorted((item[2] for item in moves), key=lambda item: (-item.relative, item.key))
    if retain_details:
        for bucket in (newly_scored, newly_refused, added, removed):
            bucket.sort(key=lambda item: item.key)
    return RowDiffReport(
        base_rows=0,
        tip_rows=0,
        base_duplicate_keys=0,
        tip_duplicate_keys=0,
        transitions=tuple(
            StatusTransition(eng, base_status, tip_status, n)
            for (eng, base_status, tip_status), n in sorted(transitions.items())
        ),
        value_move_count=move_count,
        value_moves_above=tuple((threshold, above[threshold]) for threshold in _RELATIVE_BUCKETS),
        value_moves=tuple(ordered),
        value_appeared=appeared,
        value_cleared=cleared,
        newly_scored=tuple(newly_scored),
        newly_refused=tuple(newly_refused),
        added=tuple(added),
        removed=tuple(removed),
    ), scored_n, refused_n, added_n, removed_n


def rowdiff_report(
    base: Path,
    tip: Path,
    *,
    engine: str | None = None,
    t_min: Decimal | None = None,
    t_max: Decimal | None = None,
    show: int = 20,
    retain_details: bool = False,
    out: TextIO | None = None,
) -> RowDiffReport:
    """Compare two residual ledgers by key.

    ``show`` limits the listed value moves to the largest relative changes.
    Counts include every change. ``retain_details`` keeps every changed key
    on the report. The command path leaves that false and writes the rows
    to ``out`` from the index instead.
    """

    if show < 0:
        raise ValueError("show must be >= 0")
    _require_file(base)
    _require_file(tip)
    engine_value = _engine_value(engine)
    _check_bounds(t_min, t_max, both=False)
    index: dict[str, _Record] = {}
    raw_status: dict[tuple[str, str], str] = {}
    base_rows, base_duplicates = _index_side(
        base,
        engine=engine_value,
        t_min=t_min,
        t_max=t_max,
        index=index,
        raw_status=raw_status,
        side="base",
    )
    tip_rows, tip_duplicates = _index_side(
        tip,
        engine=engine_value,
        t_min=t_min,
        t_max=t_max,
        index=index,
        raw_status=raw_status,
        side="tip",
    )
    partial, scored_n, refused_n, added_n, removed_n = _summarize(
        index, raw_status, show=show, retain_details=retain_details
    )
    report = RowDiffReport(
        base_rows=base_rows,
        tip_rows=tip_rows,
        base_duplicate_keys=base_duplicates,
        tip_duplicate_keys=tip_duplicates,
        transitions=partial.transitions,
        value_move_count=partial.value_move_count,
        value_moves_above=partial.value_moves_above,
        value_moves=partial.value_moves,
        value_appeared=partial.value_appeared,
        value_cleared=partial.value_cleared,
        newly_scored=partial.newly_scored,
        newly_refused=partial.newly_refused,
        added=partial.added,
        removed=partial.removed,
    )
    if out is not None:
        _print_rowdiff(report, index, raw_status, out, scored_n, refused_n, added_n, removed_n)
    return report


def band_report(
    path: Path,
    *,
    t_min: Decimal,
    t_max: Decimal,
    engine: str | None = None,
) -> BandReport:
    """Per-engine residual counts and numeric stats inside ``[t_min, t_max)``."""

    _require_file(path)
    engine_value = _engine_value(engine)
    _check_bounds(t_min, t_max, both=True)
    status_counts: Counter[tuple[str, str, str]] = Counter()
    grouped: dict[tuple[str, str, str, str], array.array] = {}
    rows = 0
    for _number, row in iter_residual_rows(path):
        view = _view(row)
        if not _accept(view, engine_value, t_min, t_max):
            continue
        rows += 1
        status_counts[(view.engine, view.rail, view.status)] += 1
        if view.value is not None and view.operation:
            slot = (view.engine, view.rail, view.operation, view.unit)
            bucket = grouped.get(slot)
            if bucket is None:
                bucket = array.array("d")
                grouped[slot] = bucket
            bucket.append(float(view.value))
    statuses = tuple(
        BandStatus(eng, rail, status, n)
        for (eng, rail, status), n in sorted(status_counts.items())
    )
    stats: list[BandStat] = []
    for key in sorted(grouped):
        pair = _median_rms_from_float64(grouped[key])
        if pair is None:
            continue
        median, rms = pair
        eng, rail, operation, unit = key
        stats.append(
            BandStat(
                engine=eng,
                rail=rail,
                operation=operation,
                unit=unit,
                n=len(grouped[key]),
                median=median,
                rms=rms,
            )
        )
    return BandReport(
        t_min=t_min,
        t_max=t_max,
        rows=rows,
        statuses=statuses,
        stats=tuple(stats),
    )


def _print_funnel(report: FunnelReport, out: TextIO) -> None:
    print(f"rows\t{report.rows}", file=out)
    print("status\trail\tengine\treason\tdetail\tn", file=out)
    for cell in report.cells:
        print(
            f"{cell.status}\t{cell.rail}\t{cell.engine}\t{cell.reason}\t{cell.detail}\t{cell.n}",
            file=out,
        )


def _print_change(label: str, change: KeyChange, out: TextIO) -> None:
    print(
        f"{label}\t{change.engine}\t{change.key}\t{change.base_status}\t{change.tip_status}\t"
        f"{change.base_reason}\t{change.base_detail}\t{change.tip_reason}\t{change.tip_detail}",
        file=out,
    )


def _print_rowdiff(
    report: RowDiffReport,
    index: Mapping[str, _Record],
    raw_status: Mapping[tuple[str, str], str],
    out: TextIO,
    scored_n: int,
    refused_n: int,
    added_n: int,
    removed_n: int,
) -> None:
    print(f"base_rows\t{report.base_rows}", file=out)
    print(f"tip_rows\t{report.tip_rows}", file=out)
    print(f"base_duplicate_keys\t{report.base_duplicate_keys}", file=out)
    print(f"tip_duplicate_keys\t{report.tip_duplicate_keys}", file=out)
    print(f"value_moves\t{report.value_move_count}", file=out)
    for threshold, count in report.value_moves_above:
        print(f"value_moves_rel_gt_{_num(threshold)}\t{count}", file=out)
    print(f"value_appeared\t{report.value_appeared}", file=out)
    print(f"value_cleared\t{report.value_cleared}", file=out)
    print(f"newly_scored\t{scored_n}", file=out)
    print(f"newly_refused\t{refused_n}", file=out)
    print(f"added\t{added_n}", file=out)
    print(f"removed\t{removed_n}", file=out)
    for item in report.transitions:
        print(
            f"transition\t{item.engine}\t{item.base_status}\t{item.tip_status}\t{item.n}",
            file=out,
        )
    for item in report.value_moves:
        print(
            f"value_move\t{item.engine}\t{item.key}\t{_num(item.base_value)}\t"
            f"{_num(item.tip_value)}\t{_num(item.relative)}",
            file=out,
        )
    for key, record in index.items():
        if record[0] == _ABSENT or record[4] == _ABSENT:
            continue
        change = _change_of(key, record, raw_status)
        kind = numeric_comparison_transition(change.base_status, change.tip_status)
        if kind == "scored":
            _print_change("newly_scored_row", change, out)
        elif kind == "refused":
            _print_change("newly_refused_row", change, out)
    for key, record in index.items():
        if record[0] != _ABSENT:
            continue
        _print_change("added_row", _change_of(key, record, raw_status), out)
    for key, record in index.items():
        if record[4] != _ABSENT or record[0] == _ABSENT:
            continue
        _print_change("removed_row", _change_of(key, record, raw_status), out)


def _print_band(report: BandReport, out: TextIO) -> None:
    print(f"band\t[{_num(report.t_min)},{_num(report.t_max)})", file=out)
    print(f"rows\t{report.rows}", file=out)
    print("engine\trail\tstatus\tn", file=out)
    for item in report.statuses:
        print(f"{item.engine}\t{item.rail}\t{item.status}\t{item.n}", file=out)
    print("engine\trail\toperation\tunit\tn\tmedian\trms", file=out)
    for item in report.stats:
        print(
            f"{item.engine}\t{item.rail}\t{item.operation}\t{item.unit}\t{item.n}\t"
            f"{_num(item.median)}\t{_num(item.rms)}",
            file=out,
        )


def _decimal_arg(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a decimal temperature: {text}") from exc
    if not value.is_finite():
        raise argparse.ArgumentTypeError(f"not a finite temperature: {text}")
    return value


def _add_engine(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--engine",
        default=None,
        help="keep one engine token from the battery engine enum",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    funnel = sub.add_parser("funnel", help="count rows by status, rail, engine, and refusal reason")
    funnel.add_argument("ledger", type=Path)
    _add_engine(funnel)
    funnel.add_argument("--t-min", type=_decimal_arg, default=None, help="inclusive lower kelvin bound")
    funnel.add_argument("--t-max", type=_decimal_arg, default=None, help="exclusive upper kelvin bound")
    rowdiff = sub.add_parser("rowdiff", help="compare two residual ledgers by key")
    rowdiff.add_argument("base", type=Path)
    rowdiff.add_argument("tip", type=Path)
    _add_engine(rowdiff)
    rowdiff.add_argument("--t-min", type=_decimal_arg, default=None, help="inclusive lower kelvin bound")
    rowdiff.add_argument("--t-max", type=_decimal_arg, default=None, help="exclusive upper kelvin bound")
    rowdiff.add_argument("--show", type=int, default=20, help="largest relative value moves to list")
    band = sub.add_parser("band", help="per-engine residual stats inside a temperature band")
    band.add_argument("ledger", type=Path)
    band.add_argument("--t-min", type=_decimal_arg, required=True, help="inclusive lower kelvin bound")
    band.add_argument("--t-max", type=_decimal_arg, required=True, help="exclusive upper kelvin bound")
    _add_engine(band)
    return parser


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    sink = sys.stdout if out is None else out
    try:
        if args.command == "funnel":
            report = funnel_report(args.ledger, engine=args.engine, t_min=args.t_min, t_max=args.t_max)
            _print_funnel(report, sink)
        elif args.command == "rowdiff":
            rowdiff_report(
                args.base,
                args.tip,
                engine=args.engine,
                t_min=args.t_min,
                t_max=args.t_max,
                show=args.show,
                out=sink,
            )
        else:
            report = band_report(args.ledger, t_min=args.t_min, t_max=args.t_max, engine=args.engine)
            _print_band(report, sink)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
