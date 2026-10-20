from __future__ import annotations

import pytest

from simulator.thermal_window import preheat_interval_hours


@pytest.mark.parametrize(
    ("temperature_range", "ramp_rate", "expected_hours"),
    [
        ([1500.0, 1600.0], 600.0, 3),
        ([1600.0, 1800.0, 25.0], 600.0, 3),
        ([1500.0, 1600.0], None, 3),
    ],
)
def test_preheat_interval_hours_uses_first_two_bounds_and_common_defaults(
    temperature_range: list[float],
    ramp_rate: float | None,
    expected_hours: int,
) -> None:
    assert preheat_interval_hours(temperature_range, ramp_rate) == expected_hours


def test_preheat_interval_hours_rejects_descending_ranges() -> None:
    with pytest.raises(ValueError, match="ascending"):
        preheat_interval_hours([1800.0, 1600.0], 600.0)


def test_preheat_interval_hours_rejects_invalid_ramps() -> None:
    for ramp_rate in ("slow", 0.0, float("nan")):
        with pytest.raises(ValueError, match="ramp"):
            preheat_interval_hours([1600.0, 1800.0], ramp_rate)
