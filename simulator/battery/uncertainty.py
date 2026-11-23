"""Shared parsing of printed uncertainty fields."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from simulator.battery.records import as_decimal


UNCERTAINTY_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
UNCERTAINTY_RANGE_RE = re.compile(
    rf"[\[(]\s*({UNCERTAINTY_NUMBER})\s*[,;–—]\s*({UNCERTAINTY_NUMBER})\s*[\])]"
)
UNCERTAINTY_PAIR_RE = re.compile(
    rf"({UNCERTAINTY_NUMBER})\s+(?:–|—|to)\s+({UNCERTAINTY_NUMBER})",
    re.IGNORECASE,
)
UNCERTAINTY_SCALAR_RE = re.compile(UNCERTAINTY_NUMBER)


def uncertainty_scalar_from_text(raw: str) -> str | None:
    match = re.search(rf"(?:±|\+/-)\s*({UNCERTAINTY_NUMBER})", raw)
    if match is not None:
        return match.group(1)
    match = re.search(
        rf"(?:standard\s+error|standard\s+deviation|\bsigma\b|\berror\b|\buncertainty\b)\s*[:=]?\s*({UNCERTAINTY_NUMBER})",
        raw,
        re.IGNORECASE,
    )
    if match is not None:
        return match.group(1)
    without_kind = re.sub(
        r"\b95\s*%\s*(?:ci|confidence interval)\b", " ", raw,
        flags=re.IGNORECASE,
    )
    without_kind = re.sub(
        r"\b95\s*(?:ci|confidence interval)\b|\b[12]\s*-?\s*(?:sigma|σ)\b",
        " ",
        without_kind,
        flags=re.IGNORECASE,
    )
    match = UNCERTAINTY_SCALAR_RE.search(without_kind)
    return None if match is None else match.group(0)


def mapping_uncertainty_value(
    mapping: Mapping[str, object],
    *,
    exact_fields: Sequence[str] = (),
) -> tuple[object | None, str | None]:
    """Select a printed width using the scorer's exact-field precedence."""

    for field_name in exact_fields:
        if field_name not in mapping:
            continue
        value = mapping[field_name]
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return value, "range"
        try:
            as_decimal(value)
        except (TypeError, ValueError, ArithmeticError):
            if isinstance(value, str):
                scalar = uncertainty_scalar_from_text(value)
                if scalar is not None:
                    unit = "%" if re.search(r"%|percent", value, re.IGNORECASE) else None
                    return scalar, unit
            return None, None
        else:
            return value, None
    for key, value in mapping.items():
        if str(key).casefold() in {
            "value",
            "amount",
            "error_value",
            "uncertainty_value",
        }:
            if isinstance(value, (tuple, list)) and len(value) == 2:
                return value, "range"
            try:
                as_decimal(value)
            except (TypeError, ValueError, ArithmeticError):
                if isinstance(value, str):
                    scalar = uncertainty_scalar_from_text(value)
                    if scalar is not None:
                        unit = "%" if re.search(r"%|percent", value, re.IGNORECASE) else None
                        return scalar, unit
            else:
                return value, None
    for key, value in mapping.items():
        name = str(key).casefold()
        if name in {"range", "bounds", "interval", "limits"} and isinstance(value, (tuple, list)) and len(value) == 2:
            return value, "range"
        if isinstance(value, Mapping):
            nested, unit = mapping_uncertainty_value(value)
            if nested is not None:
                return nested, unit or ("dex" if "dex" in name else None)
        if isinstance(value, (tuple, list)) and len(value) == 2:
            try:
                as_decimal(value[0])
                as_decimal(value[1])
            except (TypeError, ValueError, ArithmeticError):
                continue
            return value, "range"
        if any(
            token in name
            for token in ("uncert", "error", "sigma", "std", "dex", "width", "range")
        ):
            try:
                as_decimal(value)
            except (TypeError, ValueError, ArithmeticError):
                if isinstance(value, str):
                    scalar = uncertainty_scalar_from_text(value)
                    if scalar is not None:
                        unit = "%" if re.search(r"%|percent", value, re.IGNORECASE) else None
                        return scalar, "dex" if "dex" in name else unit
                continue
            return value, "dex" if "dex" in name else None
    return None, None


def mapping_uncertainty_unit(mapping: Mapping[str, object]) -> str | None:
    for key, value in mapping.items():
        if str(key).casefold() in {"unit", "units", "basis_unit"} and isinstance(value, str):
            return value
    return None
