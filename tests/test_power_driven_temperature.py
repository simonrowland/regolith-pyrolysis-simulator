"""Power-driven melt temperature for a measured furnace watt trace.

Robinot 2026 carries the watt series. Sesko 2024 stays a reported
range and has no power driver. With the driver absent, the lab
schedule's melt temperature series is still the target.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

from simulator.campaigns import CampaignManager
from simulator.equipment import EquipmentDesigner
from simulator.lab_schedule import lab_schedule_digests
from simulator.runner import _load_preset_run_spec
from simulator.state import CampaignPhase, MeltState
from simulator.thermal_budget import (
    FURNACE_POWER_DRIVER_KEY,
    SOLAR_ABSORPTANCE_DEFAULT,
    WINDOW_TRANSMITTANCE_UPPER_BOUND,
    PowerTemperatureRefusal,
    furnace_power_driver_payload,
    measured_furnace_power_trace,
    melt_temperature_from_furnace_power,
    reported_temperature_observations,
    sample_furnace_power_W,
    solve_power_driven_melt_temperature_C,
)


_REPO = Path(__file__).resolve().parents[1]
_ROBINOT = _REPO / "data" / "presets" / "vacuum_pyrolysis" / "robinot_2026.yaml"
_SESKO = _REPO / "data" / "presets" / "vacuum_pyrolysis" / "sesko_2024.yaml"
_DT_S = 60.0
_DT_H = _DT_S / 3600.0


def _robinot_schedule() -> dict:
    spec = _load_preset_run_spec(_ROBINOT, "faithful")
    schedule = copy.deepcopy(spec.lab_schedule)
    assert FURNACE_POWER_DRIVER_KEY in schedule
    return schedule


def _manager(schedule, *, furnace_max_T_C: float = 1800.0, dt_h: float = _DT_H):
    manager = CampaignManager({"furnace_max_T_C": furnace_max_T_C})
    manager.overrides["C2A"] = {"lab_schedule": schedule}
    manager._process_dt_h = dt_h
    return manager


def _melt(temperature_C: float = 25.0) -> MeltState:
    return MeltState(campaign=CampaignPhase.C2A, temperature_C=temperature_C)


def _step(manager, melt, step_index: int):
    elapsed_h = step_index * manager._process_dt_h
    previous = float(melt.temperature_C)
    target, ramp = manager.get_temp_target(
        CampaignPhase.C2A,
        elapsed_h,
        melt,
    )
    record = manager._power_temperature_for_step
    melt.temperature_C = float(target)
    return float(target), float(ramp), record, previous


def _series(schedule, *, furnace_max_T_C: float = 1800.0):
    manager = _manager(schedule, furnace_max_T_C=furnace_max_T_C)
    melt = _melt()
    return [_step(manager, melt, index) for index in range(59)]


def _double_power(schedule) -> dict:
    doubled = copy.deepcopy(schedule)
    trace = doubled[FURNACE_POWER_DRIVER_KEY]["trace"]
    value_index = list(trace["point_fields"]).index("expected_value")
    points = []
    for point in trace["points"]:
        row = list(point)
        row[value_index] = float(row[value_index]) * 2.0
        points.append(row)
    trace["points"] = points
    return doubled


def _trace_watts_by_minute(trace) -> dict[float, float]:
    fields = list(trace["point_fields"])
    time_index = fields.index("time_min")
    value_index = fields.index("expected_value")
    return {
        float(point[time_index]): float(point[value_index])
        for point in trace["points"]
    }


def test_robinot_power_trace_steps_at_one_minute_and_flags_the_prediction():
    schedule = _robinot_schedule()
    driver = schedule[FURNACE_POWER_DRIVER_KEY]
    assert driver["mass_kg"] == pytest.approx(0.00338)
    assert driver["exposed_area_m2"] is None
    assert driver["emissivity"] is None
    assert driver["pellet_diameter_m"] == pytest.approx(0.013)
    assert driver["focal_spot_diameter_m"] == pytest.approx(0.020)
    assert driver["solar_absorptance"] is None
    assert driver["window_transmittance"] is None
    watts = _trace_watts_by_minute(driver["trace"])

    rows = _series(schedule)
    temperatures = [row[0] for row in rows]
    assert all(math.isfinite(temperature) for temperature in temperatures)
    assert all(25.0 < temperature < 10000.0 for temperature in temperatures)
    # Minute 1 is the end of the first 60 s step. It is not the
    # schedule line (about 55 C) and not the 1800 C printed peak.
    assert 400.0 < temperatures[0] < 2000.0
    assert temperatures[29] > temperatures[4] > temperatures[0]
    assert temperatures[-1] > temperatures[0]
    assert temperatures[29] > 2500.0

    target, ramp, record, previous = rows[0]
    # Eq. (6) watts are the focal spot. The pellet takes the uniform-disk
    # intercept 0.4225, the Gaier midpoint, and the unprinted window at 1.
    assert record["focal_power_W"] == pytest.approx(watts[1.0])
    assert record["intercept"] == pytest.approx(0.4225)
    assert record["solar_absorptance"] == pytest.approx(SOLAR_ABSORPTANCE_DEFAULT)
    assert record["window_transmittance"] == pytest.approx(
        WINDOW_TRANSMITTANCE_UPPER_BOUND
    )
    assert record["power_W"] == pytest.approx(
        watts[1.0] * 0.4225 * SOLAR_ABSORPTANCE_DEFAULT
    )
    assert record["time_s"] == pytest.approx(60.0)
    assert record["role"] == "prediction"
    assert record["source_class"] == "engine_prediction"
    assert record["scored_as_observed_temperature"] is False
    assert record["mass_kg"] == pytest.approx(0.00338)
    assert record["area_m2"] == pytest.approx(
        EquipmentDesigner.radiative_loss_area_m2(0.00338)
    )
    assert record["emissivity"] == pytest.approx(EquipmentDesigner.MELT_EMISSIVITY)
    assert ramp == pytest.approx(abs(target - previous))
    knobs = {notice["knob"]: notice for notice in record["notices"]}
    assert set(knobs) == {
        "melt_radiative_area_m2",
        "melt_emissivity",
        "solar_absorptance",
        "window_transmittance",
    }
    assert knobs["melt_radiative_area_m2"]["kind"] == "knob_defaulted"
    assert knobs["melt_emissivity"]["kind"] == "knob_defaulted"
    assert knobs["solar_absorptance"]["kind"] == "knob_defaulted"
    assert knobs["window_transmittance"]["kind"] == "typed_unknown"
    assert knobs["window_transmittance"]["bound"] == "upper"
    assert knobs["melt_radiative_area_m2"]["unit"] == "m2"
    assert knobs["melt_emissivity"]["unit"] == "1"
    assert knobs["melt_emissivity"]["value"] == pytest.approx(
        EquipmentDesigner.MELT_EMISSIVITY
    )
    assert knobs["solar_absorptance"]["value"] == pytest.approx(
        SOLAR_ABSORPTANCE_DEFAULT
    )
    assert knobs["solar_absorptance"]["band_low"] == pytest.approx(0.669)
    assert knobs["solar_absorptance"]["band_high"] == pytest.approx(0.906)
    for _target, _ramp, step_record, _previous in rows:
        assert {(notice["kind"], notice["knob"]) for notice in step_record["notices"]} == {
            ("knob_defaulted", "melt_radiative_area_m2"),
            ("knob_defaulted", "melt_emissivity"),
            ("knob_defaulted", "solar_absorptance"),
            ("typed_unknown", "window_transmittance"),
        }
        assert step_record["scored_as_observed_temperature"] is False

    late = rows[29][2]
    assert late["exceeds_furnace_ceiling"] is True
    assert late["exceeds_furnace_max"] is True
    assert temperatures[29] > 1800.0

    midpoint = sample_furnace_power_W(driver["trace"], 0.25 * 60.0)
    assert midpoint == pytest.approx((watts[0.0] + watts[0.5]) / 2.0)

    manager = _manager(schedule)
    with pytest.raises(PowerTemperatureRefusal) as refused:
        manager.get_temp_target(CampaignPhase.C2A, 59 * _DT_H, _melt())
    assert refused.value.reason == "power_trace_does_not_bracket"

    observed = reported_temperature_observations(schedule["melt_temperature_C"])
    assert len(observed) == 1
    assert observed[0]["value"] == 1800
    assert observed[0]["source_class"] == "literature_sidecar"
    assert observed[0]["qualifier"] == "approximate_peak"
    assert reported_temperature_observations((record,)) == ()


def test_doubled_robinot_power_raises_the_predicted_temperature():
    schedule = _robinot_schedule()
    base = _series(schedule)
    doubled = _series(_double_power(schedule))
    for minute in (1, 5, 30, 59):
        assert doubled[minute - 1][0] > base[minute - 1][0]


def test_sesko_has_no_power_driver_and_its_range_is_not_a_temperature_series():
    spec = _load_preset_run_spec(_SESKO, "faithful")
    assert FURNACE_POWER_DRIVER_KEY not in spec.lab_schedule
    assert (
        reported_temperature_observations(
            spec.lab_schedule["melt_temperature_C"]
        )
        == ()
    )


def test_schedule_without_a_power_trace_keeps_interpolation_and_the_furnace_clamp():
    schedule = _robinot_schedule()
    del schedule[FURNACE_POWER_DRIVER_KEY]
    bare = copy.deepcopy(schedule)
    loaded = _robinot_schedule()
    assert lab_schedule_digests(bare) == lab_schedule_digests(loaded)

    manager = _manager(bare, furnace_max_T_C=1800.0, dt_h=1.0)
    target, ramp = manager.get_temp_target(CampaignPhase.C2A, 0, _melt())
    assert target == pytest.approx(1800.0)
    assert ramp == pytest.approx(1775.0)
    assert "_power_temperature_for_step" not in manager.__dict__

    clamped = _manager(bare, furnace_max_T_C=1500.0, dt_h=1.0)
    clamped_target, _ramp = clamped.get_temp_target(
        CampaignPhase.C2A,
        0,
        _melt(),
    )
    assert clamped_target == pytest.approx(1500.0)
    assert "_power_temperature_for_step" not in clamped.__dict__


def test_power_driven_target_skips_the_furnace_clamp_and_the_c0_forecast_cap():
    schedule = _robinot_schedule()
    manager = CampaignManager({"furnace_max_T_C": 1200.0})
    manager.overrides["C0"] = {"lab_schedule": schedule}
    manager._process_dt_h = _DT_H
    manager.c0_exit_prediction = {"T_exit_C": 100.0}
    manager.c0_remaining_budget_exit = object()
    melt = MeltState(campaign=CampaignPhase.C0, temperature_C=25.0)

    target, _ramp = manager.get_temp_target(CampaignPhase.C0, 0.0, melt)

    assert target > 1200.0
    assert target != pytest.approx(25.0)
    assert target != pytest.approx(100.0)
    record = manager._power_temperature_for_step
    assert record["exceeds_furnace_max"] is True
    assert target == pytest.approx(record["temperature_C"])


def test_a_printed_area_and_emissivity_emit_no_knob_defaulted_notice():
    schedule = _robinot_schedule()
    schedule[FURNACE_POWER_DRIVER_KEY] = furnace_power_driver_payload(
        {
            "role": "measured_driver",
            "units": "W",
            "point_fields": ["time_min", "expected_value"],
            "points": [[0.0, 100.0], [1.0, 100.0]],
        },
        mass_kg=0.00338,
        exposed_area_m2=1.0e-3,
    )
    schedule[FURNACE_POWER_DRIVER_KEY]["emissivity"] = 0.9
    manager = _manager(schedule)
    _target, _ramp, record, _previous = _step(manager, _melt(), 0)

    assert record["notices"] == ()
    assert record["area_m2"] == pytest.approx(1.0e-3)
    assert record["emissivity"] == pytest.approx(0.9)
    assert record["intercept"] is None
    assert record["focal_power_W"] is None
    assert record["power_W"] == pytest.approx(100.0)


def test_ambiguous_or_negative_power_and_a_missing_charge_refuse():
    with pytest.raises(PowerTemperatureRefusal) as ambiguous:
        measured_furnace_power_trace({
            "trajectory_comparison": {
                "recorded_input_traces": [
                    {"role": "measured_driver", "units": "W"},
                    {"role": "measured_driver", "units": "W"},
                ],
            },
        })
    assert ambiguous.value.reason == "ambiguous_furnace_power_trace"

    with pytest.raises(PowerTemperatureRefusal) as negative:
        sample_furnace_power_W(
            {
                "role": "measured_driver",
                "units": "W",
                "point_fields": ["time_min", "expected_value"],
                "points": [[0.0, -1.0], [1.0, 10.0]],
            },
            30.0,
        )
    assert negative.value.reason == "furnace_power_not_nonnegative"

    payload = furnace_power_driver_payload(
        {
            "role": "measured_driver",
            "units": "W",
            "point_fields": ["time_min", "expected_value"],
            "points": [[0.0, 10.0], [1.0, 10.0]],
        },
        mass_kg=None,
        exposed_area_m2=None,
    )
    with pytest.raises(PowerTemperatureRefusal) as missing:
        melt_temperature_from_furnace_power(
            payload,
            time_s=60.0,
            previous_temperature_C=25.0,
            dt_s=60.0,
            live_mass_kg=0.0,
        )
    assert missing.value.reason == "charge_mass_missing"


def test_uniform_disk_intercept_books_the_printed_13_mm_pellet():
    # Investigation anchor: (13 mm / 20 mm)^2 = 0.4225, and 1468.82 W of
    # focal power puts 620.6 W on the pellet when alpha and tau are 1.
    record = solve_power_driven_melt_temperature_C(
        power_W=1468.819694,
        previous_temperature_C=1800.0,
        dt_s=60.0,
        mass_kg=1.0,
        area_m2=1.0e-3,
        emissivity=0.85,
        pellet_diameter_m=0.013,
        focal_spot_diameter_m=0.020,
        solar_absorptance=1.0,
        window_transmittance=1.0,
    )

    assert record["intercept"] == pytest.approx(0.4225)
    assert record["focal_power_W"] == pytest.approx(1468.819694)
    # Investigation: 0.4225 of 1468.82 W is 620.6 W on the pellet.
    assert record["power_W"] == pytest.approx(620.6, abs=0.05)
    assert record["notices"] == ()


def test_one_focal_diameter_without_the_other_refuses():
    with pytest.raises(PowerTemperatureRefusal) as missing_spot:
        solve_power_driven_melt_temperature_C(
            power_W=100.0,
            previous_temperature_C=25.0,
            dt_s=60.0,
            mass_kg=1.0,
            area_m2=1.0e-3,
            emissivity=0.85,
            pellet_diameter_m=0.013,
        )
    assert missing_spot.value.reason == "focal_diameter_missing"

    with pytest.raises(PowerTemperatureRefusal) as missing_pellet:
        solve_power_driven_melt_temperature_C(
            power_W=100.0,
            previous_temperature_C=25.0,
            dt_s=60.0,
            mass_kg=1.0,
            area_m2=1.0e-3,
            emissivity=0.85,
            focal_spot_diameter_m=0.020,
            solar_absorptance=0.8,
        )
    assert missing_pellet.value.reason == "focal_diameter_missing"


def test_one_kilogram_without_a_focal_spot_keeps_the_sizer_balance():
    # Docstring sanity from before the focal booking: 1 kg, emissivity
    # 0.85, sizer area, 1740.1373551492054 W holds 1800 °C. No pellet
    # diameters, so the watts are not scaled.
    record = solve_power_driven_melt_temperature_C(
        power_W=1740.1373551492054,
        previous_temperature_C=1800.0,
        dt_s=60.0,
        mass_kg=1.0,
        area_m2=None,
        emissivity=None,
    )

    assert record["temperature_C"] == pytest.approx(1800.0, abs=1.0e-4)
    assert record["area_m2"] == pytest.approx(
        EquipmentDesigner.radiative_loss_area_m2(1.0)
    )
    assert record["intercept"] is None
    assert record["power_W"] == pytest.approx(1740.1373551492054)
    assert {notice["knob"] for notice in record["notices"]} == {
        "melt_radiative_area_m2",
        "melt_emissivity",
    }
