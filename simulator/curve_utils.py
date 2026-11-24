"""Pure curve validation and sampling helpers shared by diagnostics."""

from __future__ import annotations

import math
from collections.abc import Sequence


Curve = Sequence[tuple[float, float]]


def _normalise_curve(
    points: Curve,
    *,
    field_name: str,
    allow_negative_values: bool = False,
) -> tuple[tuple[float, float], ...]:
    validate_value = _finite_number if allow_negative_values else _finite_nonnegative
    normalised = tuple(
        sorted(
            (
                (
                    _finite_number(float(time_s), f"{field_name} time"),
                    validate_value(float(value), f"{field_name} value"),
                )
                for time_s, value in points
            ),
            key=lambda item: item[0],
        )
    )
    if not normalised:
        raise ValueError(f"{field_name} requires at least one point")
    previous_time: float | None = None
    for time_s, _value in normalised:
        if previous_time is not None and time_s <= previous_time:
            raise ValueError(f"{field_name} times must be unique")
        previous_time = time_s
    return normalised


def _interpolate(
    points: tuple[tuple[float, float], ...],
    time_s: float,
    *,
    field_name: str,
) -> float:
    if time_s < points[0][0] or time_s > points[-1][0]:
        raise ValueError(f"{field_name} does not bracket t={time_s}")
    for existing_time, value in points:
        if existing_time == time_s:
            return value
    for (t0, y0), (t1, y1) in zip(points, points[1:]):
        if t0 <= time_s <= t1:
            fraction = (time_s - t0) / (t1 - t0)
            return y0 + fraction * (y1 - y0)
    raise ValueError(f"{field_name} does not bracket t={time_s}")


def _finite_nonnegative(value: float, label: str) -> float:
    value = _finite_number(value, label)
    if value < 0.0:
        raise ValueError(f"{label} must be nonnegative")
    return value


def _finite_number(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value
