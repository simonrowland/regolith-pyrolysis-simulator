"""Shared printed-token parsing helpers for the USGS generator family."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence


LETTER_TAIL_RE = re.compile(r".*\d[A-Za-z]$")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
MERGED_SPLIT_METHOD = "split_merged_value_uncertainty_by_column_grain"


def _as_published_cell(cell: object) -> tuple[str, bool, tuple[str, ...]] | None:
    if not isinstance(cell, Mapping) or "as_published" not in cell:
        return None
    token = cell.get("as_published")
    if token is None:
        return None
    markers = tuple(str(marker) for marker in (cell.get("footnote_markers") or ()))
    return str(token), bool(cell.get("ocr_suspect")), markers


def _published_decimal(token: str) -> Decimal | None:
    text = token.strip()
    if text.startswith("(") and text.endswith(")") and text != "()":
        text = text[1:-1].strip()
    if not text or not _NUMBER_RE.fullmatch(text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _decimal_grain(token: str) -> Decimal:
    """10^exponent of a printed numeric token."""

    value = Decimal(token.strip())
    return Decimal(1).scaleb(value.as_tuple().exponent)


def _value_grain(value: Decimal) -> Decimal:
    """10^exponent of a Decimal value."""

    return Decimal(1).scaleb(value.as_tuple().exponent)


def _formula_weight_decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    return _published_decimal(re.sub(r"\s+", "", text.strip()))


def _grain_valid_splits(
    text: str, value_grain: Decimal, unc_grain: Decimal
) -> list[tuple[Decimal, Decimal]]:
    payload = text.strip()
    if not payload:
        return []
    matches: list[tuple[Decimal, Decimal]] = []
    for index in range(1, len(payload)):
        left_text = payload[:index]
        right_text = payload[index:]
        left = _published_decimal(left_text)
        right = _published_decimal(right_text)
        if left is None or right is None:
            continue
        if right <= 0:
            continue
        if _value_grain(left) != value_grain:
            continue
        if _value_grain(right) != unc_grain:
            continue
        matches.append((left, right))
    return matches


def _unique_grain_split(
    text: str, value_grain: Decimal, unc_grain: Decimal
) -> tuple[Decimal, Decimal] | None:
    """Split a glued value+uncertainty only when both halves match printed grain.

    `42.550.21` with entropy grain 0.01 is uniquely 42.55 and 0.21.
    `10575085` with integer grain 1 has many integer/integer cuts — refuse.
    """

    unique = set(_grain_valid_splits(text, value_grain, unc_grain))
    if len(unique) != 1:
        return None
    return next(iter(unique))


def _merged_split(
    token: Any,
    grains: Mapping[tuple[str, str | None], Decimal],
    payload: str,
) -> dict[str, Any] | None:
    if not payload or _published_decimal(payload) is not None:
        return None
    grain = grains.get((token.column, token.formation_basis))
    if grain is None:
        return None
    if payload.count(".") < 2 and grain == 1:
        return None
    split = _unique_grain_split(payload, grain, grain)
    if split is None:
        return None
    value, uncertainty = split
    return {
        "value": value,
        "uncertainty": uncertainty,
        "method_class": MERGED_SPLIT_METHOD,
        "evidence": (
            f"merged value+uncertainty {token.as_published!r} splits uniquely "
            f"at column printed grain {grain} into {value} ± {uncertainty}"
        ),
    }


def _letter_tail_grain_reason(
    token: Any,
    grains: Mapping[tuple[str, str | None], Decimal],
    text: str,
) -> str | None:
    if not LETTER_TAIL_RE.fullmatch(text):
        return None
    remaining = text[:-1]
    if _published_decimal(remaining) is None:
        return None
    grain = grains.get((token.column, token.formation_basis))
    if grain is None:
        return None
    remaining_grain = _decimal_grain(remaining)
    if remaining_grain == grain:
        return None
    return (
        f"OCR letter tail {token.as_published!r}: remaining digits grain "
        f"{remaining_grain} does not match column printed grain {grain}; "
        "tail is a digit, not dropped"
    )


def _reconstruction_coarser_than_column(
    value: Decimal,
    token: Any,
    grains: Mapping[tuple[str, str | None], Decimal],
) -> str | None:
    grain = grains.get((token.column, token.formation_basis))
    if grain is None:
        return None
    value_grain = _value_grain(value)
    if value_grain <= grain:
        return None
    return (
        f"reconstruction grain {value_grain} is coarser than column printed "
        f"grain {grain} for {token.column}; as_published={token.as_published!r} "
        f"reconstructed={value}"
    )


def _merged_split_record(
    value: Decimal,
    uncertainty: Decimal,
    *,
    evidence: str,
) -> dict[str, Any]:
    return {
        "value": value,
        "uncertainty": uncertainty,
        "method_class": MERGED_SPLIT_METHOD,
        "evidence": evidence,
    }


def _extract_signed_number(token: str) -> Decimal | None:
    return _published_decimal(token)


def _neighbours_of(
    token: Any,
    series: Mapping[
        tuple[str, str | None], list[tuple[int, Any, Decimal]]
    ],
) -> tuple[Decimal | None, Decimal | None]:
    points = series.get((token.column, token.formation_basis), [])
    ordered = sorted(points, key=lambda item: item[0])
    index = next(
        (
            i
            for i, item in enumerate(ordered)
            if item[1] is token or item[0] == token.row_index
        ),
        None,
    )
    if index is None:
        return None, None
    left = None
    right = None
    for previous in reversed(ordered[:index]):
        if previous[2] != 0:
            left = previous[2]
            break
    for following in ordered[index + 1 :]:
        if following[2] != 0:
            right = following[2]
            break
    return left, right


def _opposite_to_both_neighbours(
    value: Decimal, left: Decimal | None, right: Decimal | None
) -> bool:
    """True when value is the dropped-minus (or dropped-plus) class.

    Example: +111.855 sitting between −113.073 and −110.445 on a page headed
    FORMATION FROM THE OXIDES. The scan refuses that candidate; it is not
    advisory-only.
    """

    if left is None or right is None or value == 0:
        return False
    return (value > 0 > left and right < 0) or (value < 0 < left and right > 0)


def _neighbour_sign_hits(
    tokens: Sequence[Any],
    series: Mapping[tuple[str, str | None], list[tuple[int, Any, Decimal]]],
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for _key, points in series.items():
        ordered = sorted(points, key=lambda item: item[0])
        for row_index, token, value in ordered:
            left, right = _neighbours_of(token, series)
            if not _opposite_to_both_neighbours(value, left, right):
                continue
            hits.append(
                {
                    "record_id": token.record_id,
                    "column": token.column,
                    "formation_basis": token.formation_basis,
                    "row_index": row_index,
                    "as_published": token.as_published,
                    "extracted": str(value),
                    "left_neighbour": str(left),
                    "right_neighbour": str(right),
                }
            )
    return hits
