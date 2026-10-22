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
or removed. Every non-empty key may appear once in a ledger, including a
row the engine or temperature filter does not report. A second appearance
is an input error naming that key and its line. The filter selects only
the rows that are counted and compared. The base ledger is indexed once.
An accepted row is key to a status code and a float64 value or none. A
filtered base key is remembered only until that index is finished, so a
repeat can be named, and that set is empty when every row is accepted.
A repeated base key is found while the index is built. The tip ledger is
read once. A base key the tip has already shown, and a tip key that is
absent from the base index and has already been shown, are the same error.
Tip keys absent from the base index, including filtered ones, stay in one
set. That memory follows how many tip keys are not in the base index.
Each tip change is written when that row is read. Detail lines already
written stay; the output then gains an invalid trailer and the command
exits nonzero. After a complete stream, accepted base keys the tip did not
report are only in the base ledger and are written then. The base index of
the 100_000-key overlapping fixture traces about 21 MB, about 210 MB per
million base keys, dominated by the key text. That fixture has no tip keys
absent from the base index. A nine-field record holding both sides traced
28_871_392 bytes on it. At most ``show`` value moves are kept, in a bounded
heap of the largest relative changes; the counts include every change. A
float64 value that does not round-trip through decimal text can hide a
difference smaller than float64.

``band`` reports, inside the temperature window, status counts and the
median and root-mean-square of stored numeric values grouped by engine,
rail, metric operation, and unit. Numeric values reside in ``array('d')``,
8 MB per million values. Computing the median of a group builds one
temporary Decimal per value in that group (about 130 MB per million values
in the largest group) and then drops it.

Exit status is 0 for a report, 1 for an unreadable file, a bad row, or a
repeated key.
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
from typing import Iterator, Mapping, NoReturn, Sequence, TextIO

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
_UNKNOWN = -2
# Offsets on a base status code. Seen means the tip has shown that key on
# any row. Reported means an accepted tip row was compared. A seen key that
# was not reported is still only in the base. The marked codes are created
# once. A per-key sum would retain an int per key.
_SEEN = 1000
_REPORTED = 2000
_STATUS_CODES = (*_STATUS_CODE.values(), _UNKNOWN)
_SEEN_CODE = {
    code: (code + _SEEN if code >= 0 else code - _SEEN) for code in _STATUS_CODES
}
_REPORTED_CODE = {
    code: (code + _REPORTED if code >= 0 else code - _REPORTED) for code in _STATUS_CODES
}
_BaseSlot = tuple[int, float | None]


class DuplicateResidualKey(ValueError):
    """A residual ledger repeats a key. One ledger has unique keys."""

    def __init__(self, path: Path, line: int, key: str) -> None:
        self.path = path
        self.line = line
        self.key = key
        super().__init__(f"{path}:{line}: duplicate residual key {key}")


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


def _row_key(row: Mapping[str, object]) -> str:
    key = row.get("key")
    return "" if key is None else str(key)


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
    return RowView(
        key=_row_key(row),
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


def _duplicate_key(path: Path, line: int, key: str, out: TextIO | None) -> NoReturn:
    """Stop on a repeated key and mark report lines already written."""

    if out is not None:
        print(f"rowdiff_invalid\t{line}\t{key}", file=out)
    raise DuplicateResidualKey(path, line, key)


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
    if code == _UNKNOWN:
        return raw
    if code < 0 or code >= len(_STATUS_NAMES):
        return ""
    return _STATUS_NAMES[code]


def _signed_offset(code: int, magnitude: int) -> int:
    """Move ``code`` away from zero by ``magnitude``. A negative magnitude strips a mark."""

    return code + magnitude if code >= 0 else code - magnitude


def _mark_seen(code: int) -> int:
    marked = _SEEN_CODE.get(code)
    if marked is None:
        marked = _signed_offset(code, _SEEN)
        _SEEN_CODE[code] = marked
    return marked


def _mark_reported(code: int) -> int:
    marked = _REPORTED_CODE.get(code)
    if marked is None:
        marked = _signed_offset(code, _REPORTED)
        _REPORTED_CODE[code] = marked
    return marked


def _is_seen(code: int) -> bool:
    """True once the tip has shown this base key, including a reported row."""

    return code >= _SEEN or code <= -_SEEN


def _is_reported(code: int) -> bool:
    return code >= _REPORTED or code <= -_REPORTED


def _plain_code(code: int) -> int:
    """Status code with the seen or reported offset removed.

    Reported is checked first. It is also seen, and stripping only the seen
    offset would leave the reported mark in place.
    """

    if _is_reported(code):
        return _signed_offset(code, -_REPORTED)
    if _is_seen(code):
        return _signed_offset(code, -_SEEN)
    return code


@dataclass
class _Running:
    show: int
    transitions: Counter[tuple[str, str, str]]
    heap: list[tuple[Decimal, _ReverseKey, ValueMove]]
    above: dict[Decimal, int]
    move_count: int
    appeared: int
    cleared: int
    scored: list[KeyChange]
    refused: list[KeyChange]
    added: list[KeyChange]
    removed: list[KeyChange]
    scored_n: int
    refused_n: int
    added_n: int
    removed_n: int


def _running(show: int) -> _Running:
    return _Running(
        show=show,
        transitions=Counter(),
        heap=[],
        above={threshold: 0 for threshold in _RELATIVE_BUCKETS},
        move_count=0,
        appeared=0,
        cleared=0,
        scored=[],
        refused=[],
        added=[],
        removed=[],
        scored_n=0,
        refused_n=0,
        added_n=0,
        removed_n=0,
    )


def _index_base(
    path: Path,
    *,
    engine: str | None,
    t_min: Decimal | None,
    t_max: Decimal | None,
) -> tuple[dict[str, _BaseSlot], int, dict[str, str], dict[str, tuple[str, str]]]:
    """Index accepted base rows as key to (status code, float or none).

    Every non-empty key is checked before the engine and temperature filter.
    A filtered key is remembered only so a later repeat can be named. It is
    not a comparison row, and that set is dropped when the index is done.
    A repeated key raises before any tip output. Refusal text and unknown
    status spellings are kept only for accepted keys that have them.
    """

    index: dict[str, _BaseSlot] = {}
    excluded: set[str] = set()
    unknown: dict[str, str] = {}
    reasons: dict[str, tuple[str, str]] = {}
    rows = 0
    for number, row in iter_residual_rows(path):
        view = _view(row)
        accepted = _accept(view, engine, t_min, t_max)
        if not view.key:
            if accepted:
                rows += 1
            continue
        if view.key in index or view.key in excluded:
            raise DuplicateResidualKey(path, number, view.key)
        if not accepted:
            excluded.add(view.key)
            continue
        rows += 1
        code = _status_code(view.status)
        if code == _UNKNOWN:
            unknown[view.key] = view.status
        elif view.key in unknown:
            del unknown[view.key]
        if view.reason or view.detail:
            reasons[view.key] = (view.reason, view.detail)
        elif view.key in reasons:
            del reasons[view.key]
        value = None if view.value is None else float(view.value)
        index[view.key] = (code, value)
    return index, rows, unknown, reasons


def _note_matched(
    running: _Running,
    key: str,
    base_code: int,
    base_value: float | None,
    view: RowView,
    base_reason: str,
    base_detail: str,
    base_raw: str,
    *,
    retain: bool,
    out: TextIO | None,
) -> None:
    base_status = _status_text(base_code, base_raw)
    change = KeyChange(
        key=key,
        engine=view.engine,
        base_status=base_status,
        tip_status=view.status,
        base_reason=base_reason,
        tip_reason=view.reason,
        base_detail=base_detail,
        tip_detail=view.detail,
    )
    if base_status != view.status:
        running.transitions[(view.engine, base_status, view.status)] += 1
    tip_value = None if view.value is None else float(view.value)
    if base_value is not None and tip_value is not None and base_value != tip_value:
        base_dec = Decimal(str(base_value))
        tip_dec = Decimal(str(tip_value))
        relative = _relative(base_dec, tip_dec)
        running.move_count += 1
        _keep_move(
            running.heap,
            ValueMove(view.engine, key, base_dec, tip_dec, relative),
            running.show,
        )
        for threshold in _RELATIVE_BUCKETS:
            if relative > threshold:
                running.above[threshold] += 1
    elif base_value is None and tip_value is not None:
        running.appeared += 1
    elif base_value is not None and tip_value is None:
        running.cleared += 1
    kind = numeric_comparison_transition(base_status, view.status)
    if kind == "scored":
        running.scored_n += 1
        if retain:
            running.scored.append(change)
        if out is not None:
            _print_change("newly_scored_row", change, out)
    elif kind == "refused":
        running.refused_n += 1
        if retain:
            running.refused.append(change)
        if out is not None:
            _print_change("newly_refused_row", change, out)


def _note_added(running: _Running, view: RowView, *, retain: bool, out: TextIO | None) -> None:
    change = KeyChange(
        key=view.key,
        engine=view.engine,
        base_status="",
        tip_status=view.status,
        base_reason="",
        tip_reason=view.reason,
        base_detail="",
        tip_detail=view.detail,
    )
    running.added_n += 1
    if retain:
        running.added.append(change)
    if out is not None:
        _print_change("added_row", change, out)


def _note_removed(
    running: _Running,
    key: str,
    code: int,
    reasons: Mapping[str, tuple[str, str]],
    unknown: Mapping[str, str],
    *,
    retain: bool,
    out: TextIO | None,
) -> None:
    reason, detail = reasons.get(key, ("", ""))
    change = KeyChange(
        key=key,
        engine=_engine_of({"key": key}),
        base_status=_status_text(code, unknown.get(key, "")),
        tip_status="",
        base_reason=reason,
        tip_reason="",
        base_detail=detail,
        tip_detail="",
    )
    running.removed_n += 1
    if retain:
        running.removed.append(change)
    if out is not None:
        _print_change("removed_row", change, out)


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


def _report_from(
    running: _Running,
    *,
    base_rows: int,
    tip_rows: int,
    retain_details: bool,
) -> RowDiffReport:
    ordered = sorted((item[2] for item in running.heap), key=lambda item: (-item.relative, item.key))
    if retain_details:
        for bucket in (running.scored, running.refused, running.added, running.removed):
            bucket.sort(key=lambda item: item.key)
    return RowDiffReport(
        base_rows=base_rows,
        tip_rows=tip_rows,
        transitions=tuple(
            StatusTransition(eng, base_status, tip_status, n)
            for (eng, base_status, tip_status), n in sorted(running.transitions.items())
        ),
        value_move_count=running.move_count,
        value_moves_above=tuple((threshold, running.above[threshold]) for threshold in _RELATIVE_BUCKETS),
        value_moves=tuple(ordered),
        value_appeared=running.appeared,
        value_cleared=running.cleared,
        newly_scored=tuple(running.scored) if retain_details else (),
        newly_refused=tuple(running.refused) if retain_details else (),
        added=tuple(running.added) if retain_details else (),
        removed=tuple(running.removed) if retain_details else (),
    )


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

    The base ledger is indexed once. Accepted rows are key to (status code,
    float or none). A repeated base key, including one the filter drops,
    raises there, before the tip is read. The tip ledger is read once.
    Every non-empty tip key is checked before the filter. A second look at
    a base key uses the seen mark on that entry. Tip keys absent from the
    base index, including filtered ones, stay in one set so a repeat can be
    named. That set is not a copy of the base index. An accepted tip row is
    marked reported and written then. A filtered tip row of an accepted base
    key is not a comparison, and that base key is still only in the base.
    A tip duplicate marks the output invalid and raises. Detail lines already
    written are left in place. ``show`` keeps the largest relative value
    moves in a bounded heap. Counts include every change. ``retain_details``
    keeps every changed key on the returned report. The command path leaves
    that false.
    """

    if show < 0:
        raise ValueError("show must be >= 0")
    _require_file(base)
    _require_file(tip)
    engine_value = _engine_value(engine)
    _check_bounds(t_min, t_max, both=False)
    index, base_rows, unknown, reasons = _index_base(
        base,
        engine=engine_value,
        t_min=t_min,
        t_max=t_max,
    )
    running = _running(show)
    # Tip keys that are not in the base index, filtered or not. Not a copy of the index.
    tip_extra: set[str] = set()
    tip_rows = 0
    for number, row in iter_residual_rows(tip):
        view = _view(row)
        accepted = _accept(view, engine_value, t_min, t_max)
        if not view.key:
            if accepted:
                tip_rows += 1
            continue
        slot = index.get(view.key)
        if slot is not None:
            code, value = slot
            if _is_seen(code):
                _duplicate_key(tip, number, view.key, out)
            if not accepted:
                index[view.key] = (_mark_seen(code), value)
                continue
            index[view.key] = (_mark_reported(code), value)
            tip_rows += 1
            reason, detail = reasons.get(view.key, ("", ""))
            _note_matched(
                running,
                view.key,
                code,
                value,
                view,
                reason,
                detail,
                unknown.get(view.key, ""),
                retain=retain_details,
                out=out,
            )
            continue
        if view.key in tip_extra:
            _duplicate_key(tip, number, view.key, out)
        tip_extra.add(view.key)
        if not accepted:
            continue
        tip_rows += 1
        _note_added(running, view, retain=retain_details, out=out)
    # Accepted base keys the tip did not report are only in the base ledger.
    for key, (code, _value) in index.items():
        if _is_reported(code):
            continue
        _note_removed(
            running,
            key,
            _plain_code(code),
            reasons,
            unknown,
            retain=retain_details,
            out=out,
        )
    report = _report_from(
        running,
        base_rows=base_rows,
        tip_rows=tip_rows,
        retain_details=retain_details,
    )
    if out is not None:
        _print_rowdiff(report, out, running.scored_n, running.refused_n, running.added_n, running.removed_n)
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
    out: TextIO,
    scored_n: int,
    refused_n: int,
    added_n: int,
    removed_n: int,
) -> None:
    print(f"base_rows\t{report.base_rows}", file=out)
    print(f"tip_rows\t{report.tip_rows}", file=out)
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
