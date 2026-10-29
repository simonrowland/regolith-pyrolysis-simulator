"""Shared CPU-budget defaults for the pytest hook and its suite-shape guard."""

from __future__ import annotations

import math


DEFAULT_CPU_BUDGET_SECONDS = 300.0
WALL_GUARD_MULTIPLIER = 6.0


def cpu_budget_seconds(item) -> float:
    marker = item.get_closest_marker("cpu_budget")
    if marker is None:
        return DEFAULT_CPU_BUDGET_SECONDS
    if len(marker.args) != 1 or marker.kwargs:
        raise ValueError("cpu_budget requires exactly one positional seconds value")
    try:
        seconds = float(marker.args[0])
    except (TypeError, ValueError) as exc:
        raise ValueError("cpu_budget seconds must be a positive finite number") from exc
    if not math.isfinite(seconds) or seconds <= 0.0:
        raise ValueError("cpu_budget seconds must be a positive finite number")
    return seconds


def wall_guard_seconds(cpu_budget: float) -> int:
    return math.ceil(cpu_budget * WALL_GUARD_MULTIPLIER)
