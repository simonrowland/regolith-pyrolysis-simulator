"""Shared admission check for caller-declared numeric scalars."""

from __future__ import annotations

import math
import numbers
from decimal import Decimal
from typing import Any


def is_declared_real_scalar(
    value: Any,
    *,
    allow_numeric_str: bool = False,
) -> bool:
    """Return whether ``value`` declares a real number, never a boolean."""

    if isinstance(value, bool):
        return False
    if isinstance(value, (numbers.Real, Decimal)):
        return True
    if not allow_numeric_str or not isinstance(value, str):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def require_finite_float(value: Any, label: str) -> float:
    """Require a declared finite scalar, preserving session numeric errors."""
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def finite_float(value: Any) -> float | None:
    """Return the float value when it is finite, otherwise ``None``."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def is_finite(value: Any) -> bool:
    """Return whether ``value`` converts to a finite float."""

    return finite_float(value) is not None


__all__ = ("finite_float", "is_declared_real_scalar", "is_finite")
