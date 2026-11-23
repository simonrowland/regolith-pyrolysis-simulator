"""Shared printed-cell address and unit identity helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from simulator.battery.records import Located, Locator, Value
from simulator.battery.enums import ValueKind


@dataclass(frozen=True)
class PrintedCell:
    address: tuple[str | None, ...]
    value: str | None
    unit: str | None = None


PRINTED_CELL_NOTE = "printed_cell="


def normalize_printed_unit(unit: str) -> str:
    token = unit.strip().lower().replace("³", "3")
    token = re.sub(r"\s+", "", token)
    return {
        "c": "C", "°c": "C", "degc": "C", "c/min": "C/min",
        "torr": "Torr", "mmhg": "Torr", "atm": "atm",
        "atmosphere": "atm", "atmospheres": "atm", "bar": "bar",
        "bars": "bar", "mbar": "mbar", "millibar": "mbar",
        "mpa": "MPa", "mm": "mm", "cm": "cm", "cm3": "cm3",
        "mg": "mg", "g": "g", "gram": "g", "grams": "g",
        "kg": "kg", "s": "s", "sec": "s", "second": "s",
        "seconds": "s", "min": "min", "minute": "min",
        "minutes": "min", "h": "h", "hr": "h", "hour": "h",
        "hours": "h", "ks": "ks", "l/s": "L/s", "liters-1": "L/s",
    }.get(token, unit)


def address_note_segments(locator: Locator) -> Iterator[tuple[str, str | None, str | None]]:
    for segment in (locator.note or "").split(";"):
        key, separator, value = segment.strip().partition("=")
        yield segment, key.strip() if separator else None, value.strip() or None


def inherited_row_locator(locator: Locator | None) -> Locator | None:
    if locator is None or not locator.note:
        return locator
    parts = [segment for segment, key, _ in address_note_segments(locator) if key != "column"]
    note = ";".join(parts)
    if note == locator.note:
        return locator
    try:
        return replace(locator, note=note.strip() or None)
    except ValueError:
        return None


def printed_cell_address(locator: Locator) -> tuple[str | None, ...]:
    coordinates = {}
    for _, key, value in address_note_segments(locator):
        if key in {"row", "column"}:
            coordinates[key] = value
    page = locator.pdf_page_index + 1 if locator.pdf_page_index is not None else locator.page
    return (locator.source_path, str(page) if page is not None else None,
            locator.table, locator.figure,
            coordinates.get("row") or locator.record, coordinates.get("column"))


def located_printed_cell(locator: Locator | None, value: str | None, unit: str | None) -> Locator | None:
    if locator is None:
        return None
    encoded = json.dumps([value, unit], ensure_ascii=False).replace(";", r"\u003b")
    note = f"{locator.note}; " if locator.note else ""
    return replace(locator, note=f"{note}{PRINTED_CELL_NOTE}{encoded}")


def has_printed_cell(locator: Locator | None) -> bool:
    return locator is not None and any(
        segment.strip().startswith(PRINTED_CELL_NOTE)
        for segment in (locator.note or "").split(";")
    )


def printed_cell(value: Located, *, address_only: bool = False) -> PrintedCell | None:
    if value.locator is None or not value.state.is_value:
        return None
    address = printed_cell_address(value.locator)
    for segment, _, _ in reversed(list(address_note_segments(value.locator))):
        if segment.strip().startswith(PRINTED_CELL_NOTE):
            try:
                raw, unit = json.loads(segment.strip().removeprefix(PRINTED_CELL_NOTE))
            except (ValueError, TypeError):
                continue
            if (raw is None or isinstance(raw, str)) and (unit is None or isinstance(unit, str)):
                return PrintedCell(address, raw, unit)
    if address_only:
        return PrintedCell(address, None)
    scalar = value.state.value
    if isinstance(scalar, Value):
        if scalar.kind is ValueKind.INTERVAL:
            return PrintedCell(address, None)
        scalar = scalar.point
    if not isinstance(scalar, (str, Decimal, int)) or isinstance(scalar, bool):
        return None
    return PrintedCell(address, str(scalar))


def same_printed_cell(a: PrintedCell | None, b: PrintedCell | None) -> str | None:
    if a is None or b is None:
        return None
    left, right = a.address, b.address
    if left[4] is None or left[4] != right[4]:
        return None
    if not any(locus is not None and locus == other for locus, other in zip(left[1:4], right[1:4])):
        return None
    if any(locus is not None and other is not None and locus != other for locus, other in zip(left, right)):
        return None
    if left[5] is not None and right[5] is not None:
        return "same"
    if a.value is None or b.value is None:
        return None
    try:
        first, second = Decimal(a.value), Decimal(b.value)
    except InvalidOperation:
        units_agree = (a.unit is None or b.unit is None
                       or normalize_printed_unit(a.unit) == normalize_printed_unit(b.unit))
        return "ambiguous" if a.value == b.value and units_agree else None
    units_agree = (a.unit is None or b.unit is None
                   or normalize_printed_unit(a.unit) == normalize_printed_unit(b.unit))
    return "ambiguous" if first.is_finite() and second.is_finite() and first == second and units_agree else None
