"""Shared thermal-window ramp interval resolution."""

from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Sequence
from typing import Any


DEFAULT_THERMAL_PREHEAT_RAMP_C_PER_HR = 600.0
DEFAULT_COLD_START_TEMPERATURE_C = 25.0


@dataclass(frozen=True)
class ThermalPreheatInterval:
    low_C: float
    high_C: float
    ramp_rate_C_per_hr: float
    hours: int


def resolve_thermal_preheat_interval(
    temperature_range: Any,
    preheat_ramp_C_per_hr: Any = None,
    ramp_rate_C_per_hr: Any = None,
) -> ThermalPreheatInterval:
    """Resolve a thermal interval and the whole-hour cold-start ramp budget."""
    if isinstance(temperature_range, (str, bytes)) or not isinstance(
        temperature_range, Sequence
    ) or len(temperature_range) < 2:
        raise ValueError("temperature range must contain at least two bounds")
    try:
        low_C, high_C = float(temperature_range[0]), float(temperature_range[1])
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature range bounds must be numeric") from exc
    if not math.isfinite(low_C) or not math.isfinite(high_C):
        raise ValueError("temperature range bounds must be finite")
    if high_C < low_C:
        raise ValueError("temperature range must be ascending")

    raw_ramp = preheat_ramp_C_per_hr
    if raw_ramp is None:
        raw_ramp = ramp_rate_C_per_hr
    if raw_ramp is None:
        ramp = DEFAULT_THERMAL_PREHEAT_RAMP_C_PER_HR
    else:
        try:
            ramp = float(raw_ramp)
        except (TypeError, ValueError) as exc:
            raise ValueError("preheat ramp must be numeric") from exc
    if not math.isfinite(ramp) or ramp <= 0.0:
        raise ValueError("preheat ramp must be positive and finite")

    # Premise: a linear ramp starts at 25 °C and reaches the interval's low end.
    # Thus hours = (T_end - T_start) / ramp rate; ceil reserves whole hours.
    # Unit check: °C / (°C h⁻¹) = h. At/below the cold start the max gives 0 h.
    hours = int(math.ceil(max(0.0, low_C - DEFAULT_COLD_START_TEMPERATURE_C) / ramp))
    return ThermalPreheatInterval(low_C, high_C, ramp, hours)
