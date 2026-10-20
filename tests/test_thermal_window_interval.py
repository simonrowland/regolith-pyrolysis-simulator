from __future__ import annotations

from pathlib import Path

import pytest

import scripts.make_recipe_db_profile as recipe_db_profile
import simulator.optimize.evaluate as evaluate
import simulator.optimize.profiles as profiles
from simulator.thermal_window import resolve_thermal_preheat_interval


@pytest.mark.parametrize(
    ("temperature_range", "ramp_rate", "expected_hours"),
    [
        ([1500.0, 1600.0], 600.0, 3),
        ([1500.0, 1600.0], None, 3),
        ([1800.0, 1800.0], 600.0, 3),
        ([1050.0, 1843.0], 600.0, 2),
    ],
)
def test_preheat_interval_hours_uses_two_bounds_and_common_defaults(
    temperature_range: list[float],
    ramp_rate: float | None,
    expected_hours: int,
) -> None:
    assert resolve_thermal_preheat_interval(
        temperature_range, ramp_rate
    ).hours == expected_hours


def test_preheat_interval_hours_rejects_descending_ranges() -> None:
    with pytest.raises(ValueError, match="ascending"):
        resolve_thermal_preheat_interval([1800.0, 1600.0], 600.0)


@pytest.mark.parametrize(
    ("temperature_range", "ramp_rate"),
    [
        ([1600.0, 1800.0, 25.0], 600.0),
        ([1600.0, 1800.0, float("nan")], 600.0),
        ([1600.0], 600.0),
        (["warm", 1800.0], 600.0),
        ([1600.0, 1800.0], "slow"),
    ],
)
def test_three_preheat_readers_refuse_the_same_invalid_inputs(
    monkeypatch: pytest.MonkeyPatch,
    temperature_range: list[object],
    ramp_rate: object,
) -> None:
    with pytest.raises(ValueError):
        resolve_thermal_preheat_interval(temperature_range, ramp_rate)

    profile = {
        "run": {
            "campaign": "C0",
            "hours": 1,
            "duration_h": 1,
            "temp_range_C": temperature_range,
            "preheat_ramp_C_per_hr": ramp_rate,
        }
    }
    monkeypatch.setattr(
        evaluate,
        "_furnace_ceiling_C",
        lambda *args, **kwargs: (2000.0, "test ceiling"),
    )
    with pytest.raises(evaluate.EvaluationInputError):
        evaluate._profile_thermal_window_schedule(
            profile["run"],
            profile=profile,
            recipe_patch=None,
            constraints=None,
            setpoints={},
        )

    with pytest.raises(profiles.ProfileValidationError):
        profiles._validate_run_thermal_window_caps(
            profile,
            profile["run"],
            source=Path("test-profile.yaml"),
            where="test",
        )

    monkeypatch.setattr(
        recipe_db_profile,
        "_setpoint_campaign_config",
        lambda _campaign: {"preheat_ramp_C_per_hr": ramp_rate},
    )
    with pytest.raises(ValueError):
        recipe_db_profile._thermal_window_preheat_hours(
            "C0", temperature_range
        )


def test_preheat_interval_hours_rejects_invalid_ramps() -> None:
    for ramp_rate in ("slow", 0.0, float("nan")):
        with pytest.raises(ValueError, match="ramp"):
            resolve_thermal_preheat_interval([1600.0, 1800.0], ramp_rate)


@pytest.mark.parametrize(
    ("temperature_range", "expected_hours"),
    [([1600.0, 1800.0], 3), ([1050.0, 1843.0], 2)],
)
def test_three_preheat_readers_agree_on_valid_intervals(
    monkeypatch: pytest.MonkeyPatch,
    temperature_range: list[float],
    expected_hours: int,
) -> None:
    profile = {
        "run": {
            "campaign": "C0",
            "hours": 1,
            "duration_h": 1,
            "temp_range_C": temperature_range,
            "preheat_ramp_C_per_hr": 600.0,
        }
    }
    monkeypatch.setattr(
        evaluate,
        "_furnace_ceiling_C",
        lambda *args, **kwargs: (2000.0, "test ceiling"),
    )
    schedule = evaluate._profile_thermal_window_schedule(
        profile["run"],
        profile=profile,
        recipe_patch=None,
        constraints=None,
        setpoints={},
    )
    assert schedule is not None
    evaluate_hours = schedule["overrides"]["thermal_window_preheat_hours"]

    monkeypatch.setattr(
        profiles,
        "_campaign_max_hold_hr_for_profile",
        lambda *_args: expected_hours + 0.5,
    )
    with pytest.raises(
        profiles.ProfileValidationError,
        match=f"preheat {expected_hours} h",
    ):
        profiles._validate_run_thermal_window_caps(
            profile,
            profile["run"],
            source=Path("test-profile.yaml"),
            where="test",
        )
    monkeypatch.setattr(
        recipe_db_profile,
        "_setpoint_campaign_config",
        lambda _campaign: {"preheat_ramp_C_per_hr": 600.0},
    )
    script_hours = recipe_db_profile._thermal_window_preheat_hours(
        "C0", profile["run"]["temp_range_C"]
    )
    assert evaluate_hours == expected_hours
    assert script_hours == expected_hours
