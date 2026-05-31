"""Canonical JSON helpers shared by optimizer identifiers."""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


FLOAT_QUANTUM = Decimal("0.000000001")


class CanonicalizationError(ValueError):
    """Raised when input cannot be rendered as stable canonical JSON."""


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def normalize_canonical_value(value: Any, *, float_quantum: Decimal = FLOAT_QUANTUM) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("canonical JSON rejects NaN and infinity")
        return _decimal_to_fixed(Decimal(str(value)), float_quantum)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalizationError("canonical JSON rejects NaN and infinity")
        return _decimal_to_fixed(value, float_quantum)
    if isinstance(value, tuple):
        return [normalize_canonical_value(item, float_quantum=float_quantum) for item in value]
    if isinstance(value, list):
        return [normalize_canonical_value(item, float_quantum=float_quantum) for item in value]
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        keys = list(value)
        if not all(isinstance(key, str) for key in keys):
            raise CanonicalizationError("canonical JSON mapping keys must be strings")
        for key in sorted(keys):
            normalized[key] = normalize_canonical_value(value[key], float_quantum=float_quantum)
        return normalized
    raise CanonicalizationError(f"canonical JSON unsupported value type: {type(value).__name__}")


def _decimal_to_fixed(value: Decimal, float_quantum: Decimal) -> str:
    try:
        return format(value.quantize(float_quantum), "f")
    except InvalidOperation as exc:
        raise CanonicalizationError("canonical JSON float normalization failed") from exc
