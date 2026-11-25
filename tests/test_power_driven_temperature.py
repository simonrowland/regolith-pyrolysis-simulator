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
    absorbed_focal_power_W,
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
    assert driver["pellet_height_m"] == pytest.approx(0.010)
    assert driver["focal_spot_diameter_m"] == pytest.approx(0.020)
    assert driver["radiating_face_set"] == "hot_upper"
    assert driver["solar_absorptance"] is None
    assert driver["window_transmittance"] is None
    assert driver["conductivity_W_m_K"] is None
    watts = _trace_watts_by_minute(driver["trace"])

    rows = _series(schedule)
    temperatures = [row[0] for row in rows]
    assert all(math.isfinite(temperature) for temperature in temperatures)
    assert all(25.0 < temperature < 10000.0 for temperature in temperatures)
    # Minute 1 is the end of the first 60 s step. It is not the
    # schedule line (about 55 C). Conduction into the cold holder
    # and continuum argon put that first minute just under 400 C.
    assert 350.0 < temperatures[0] < 2000.0
    assert temperatures[29] > temperatures[4] > temperatures[0]
    assert temperatures[-1] > temperatures[0]
    # The tonne-sizer root was 4864 °C here. The printed hot-upper face
    # is in the pyrometer's neighbourhood, not above 2500 °C.
    assert 1500.0 < temperatures[29] < 2500.0

    target, ramp, record, previous = rows[0]
    # Eq. (6) watts are the focal spot. The pellet takes the uniform-disk
    # intercept 0.4225, the Gaier midpoint, and the unprinted window at 1.
    assert record["focal_power_W"] == pytest.approx(watts[1.0])
    assert record["intercept"] == pytest.approx(0.4225)
    assert record["solar_absorptance"] == pytest.approx(SOLAR_ABSORPTANCE_DEFAULT)
    assert record["solar_absorptance"] == pytest.approx(0.7875)
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
    # Investigation areas for the 13 mm by 10 mm cylinder: hot upper
    # 3.369e-4 m², full side 5.411e-4 m². Not the tonne sizer.
    assert record["face_set"] == "hot_upper"
    assert record["area_m2"] == pytest.approx(3.369e-4, abs=5.0e-8)
    assert record["sensitivity"]["face_set"] == "full_side"
    assert record["sensitivity"]["area_m2"] == pytest.approx(5.411e-4, abs=5.0e-8)
    assert record["sensitivity"]["role"] == "sensitivity"
    assert record["sensitivity"]["temperature_C"] < record["temperature_C"]
    assert record["emissivity"] == pytest.approx(EquipmentDesigner.MELT_EMISSIVITY)
    # The schedule holder ends at 1500 C. The balance does not read it.
    holder = schedule["surface_temperature_C"]["holder"]
    assert holder[-1]["value"] == 1500
    assert record["cold_boundary_K"] == pytest.approx(300.0)
    assert record["surroundings_K"] == pytest.approx(300.0)
    assert record["conductivity_W_m_K"] == pytest.approx(1.0)
    assert ramp == pytest.approx(abs(target - previous))
    knobs = {notice["knob"]: notice for notice in record["notices"]}
    assert set(knobs) == {
        "focal_spot_diameter_m",
        "melt_emissivity",
        "solar_absorptance",
        "window_transmittance",
        "pellet_conductivity_W_m_K",
        "cold_boundary_K",
    }
    assert knobs["focal_spot_diameter_m"]["qualifier"] == "approximate"
    assert knobs["focal_spot_diameter_m"]["reason"] == (
        "focal_spot_diameter_approximate"
    )
    assert knobs["melt_emissivity"]["kind"] == "knob_defaulted"
    assert knobs["solar_absorptance"]["kind"] == "knob_defaulted"
    assert knobs["window_transmittance"]["kind"] == "typed_unknown"
    assert knobs["window_transmittance"]["bound"] == "upper"
    assert knobs["pellet_conductivity_W_m_K"]["kind"] == "knob_defaulted"
    assert knobs["pellet_conductivity_W_m_K"]["value"] == pytest.approx(1.0)
    assert knobs["pellet_conductivity_W_m_K"]["band_low"] == pytest.approx(0.6)
    assert knobs["pellet_conductivity_W_m_K"]["band_high"] == pytest.approx(1.4)
    assert knobs["cold_boundary_K"]["kind"] == "typed_unknown"
    assert knobs["cold_boundary_K"]["value"] == pytest.approx(300.0)
    assert "1500" in knobs["cold_boundary_K"]["source"]
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
            ("typed_unknown", "focal_spot_diameter_m"),
            ("knob_defaulted", "melt_emissivity"),
            ("knob_defaulted", "solar_absorptance"),
            ("typed_unknown", "window_transmittance"),
            ("knob_defaulted", "pellet_conductivity_W_m_K"),
            ("typed_unknown", "cold_boundary_K"),
        }
        assert step_record["face_set"] == "hot_upper"
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

    # Minute 1 of the corrected pellet balance sits below this 1200 C
    # ceiling. The clamp-skip is the minute-30 sample, where the same
    # balance is above the ceiling and above the 100 C forecast cap.
    target, _ramp = manager.get_temp_target(CampaignPhase.C0, 29.0 * _DT_H, melt)

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
    assert record["face_set"] is None
    assert record["sensitivity"] is None
    assert record["power_W"] == pytest.approx(100.0)
    assert record["cold_boundary_K"] is None
    assert record["surroundings_K"] == pytest.approx(0.0)
    assert record["conductivity_W_m_K"] is None


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
    # No printed height, so the sky balance is unchanged.
    assert record["cold_boundary_K"] is None
    assert record["surroundings_K"] == pytest.approx(0.0)


def test_pellet_larger_than_focal_spot_intercepts_the_focal_power_once():
    absorbed, intercept = absorbed_focal_power_W(
        1000.0,
        pellet_diameter_m=0.030,
        focal_spot_diameter_m=0.020,
        solar_absorptance=1.0,
        window_transmittance=1.0,
    )

    assert intercept == pytest.approx(1.0)
    assert absorbed == pytest.approx(1000.0)


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
    assert record["face_set"] is None
    assert record["sensitivity"] is None
    assert record["power_W"] == pytest.approx(1740.1373551492054)
    assert record["cold_boundary_K"] is None
    assert record["surroundings_K"] == pytest.approx(0.0)
    assert record["conductivity_W_m_K"] is None
    assert {notice["knob"] for notice in record["notices"]} == {
        "melt_radiative_area_m2",
        "melt_emissivity",
    }


def test_sub_kilogram_charge_without_a_cylinder_refuses_the_tonne_sizer():
    with pytest.raises(PowerTemperatureRefusal) as refused:
        solve_power_driven_melt_temperature_C(
            power_W=100.0,
            previous_temperature_C=25.0,
            dt_s=60.0,
            mass_kg=0.00338,
            area_m2=None,
            emissivity=0.85,
        )
    assert refused.value.reason == "radiative_area_out_of_domain"


def test_unset_face_set_on_a_gram_pellet_defaults_to_hot_upper():
    record = solve_power_driven_melt_temperature_C(
        power_W=100.0,
        previous_temperature_C=25.0,
        dt_s=60.0,
        mass_kg=0.00338,
        area_m2=None,
        emissivity=0.85,
        pellet_diameter_m=0.013,
        pellet_height_m=0.010,
        focal_spot_diameter_m=0.020,
        solar_absorptance=0.85,
        window_transmittance=1.0,
    )
    declared = solve_power_driven_melt_temperature_C(
        power_W=100.0,
        previous_temperature_C=25.0,
        dt_s=60.0,
        mass_kg=0.00338,
        area_m2=None,
        emissivity=0.85,
        pellet_diameter_m=0.013,
        pellet_height_m=0.010,
        focal_spot_diameter_m=0.020,
        radiating_face_set="hot_upper",
        solar_absorptance=0.85,
        window_transmittance=1.0,
    )

    assert record["face_set"] == "hot_upper"
    assert record["area_m2"] == pytest.approx(declared["area_m2"])
    assert record["temperature_C"] == pytest.approx(declared["temperature_C"])
    knobs = {notice["knob"]: notice for notice in record["notices"]}
    assert knobs["radiating_face_set"]["kind"] == "knob_defaulted"
    assert knobs["radiating_face_set"]["value"] == "hot_upper"
    assert "radiating_face_set" not in {
        notice["knob"] for notice in declared["notices"]
    }


def test_an_unknown_face_set_refuses():
    with pytest.raises(PowerTemperatureRefusal) as refused:
        solve_power_driven_melt_temperature_C(
            power_W=100.0,
            previous_temperature_C=25.0,
            dt_s=60.0,
            mass_kg=0.00338,
            area_m2=None,
            emissivity=0.85,
            pellet_diameter_m=0.013,
            pellet_height_m=0.010,
            focal_spot_diameter_m=0.020,
            radiating_face_set="top_only",
            solar_absorptance=0.85,
            window_transmittance=1.0,
        )
    assert refused.value.reason == "radiating_face_set_unknown"


def _peak_pellet(**overrides):
    payload = dict(
        power_W=1468.819694,
        previous_temperature_C=25.0,
        dt_s=1.0e12,
        mass_kg=0.00338,
        area_m2=None,
        emissivity=0.85,
        pellet_diameter_m=0.013,
        pellet_height_m=0.010,
        focal_spot_diameter_m=0.020,
        radiating_face_set="hot_upper",
        solar_absorptance=0.85,
        window_transmittance=1.0,
        conductivity_W_m_K=1.0,
    )
    payload.update(overrides)
    return solve_power_driven_melt_temperature_C(**payload)


def test_printed_face_areas_match_the_investigation_with_holder_and_argon():
    # Investigation table at the peak focal power, k = 1, Nu = 2,
    # losses at the solved temperature. Hot upper, then full side.
    anchors = (
        (0.906, 2112.0, 1851.0),
        (0.85, 2072.0, 1816.0),
        (0.669, 1929.0, 1689.0),
    )
    solved = {}
    for alpha, hot_C, full_C in anchors:
        record = _peak_pellet(solar_absorptance=alpha)
        solved[alpha] = record
        assert record["area_m2"] == pytest.approx(3.369e-4, abs=5.0e-8)
        assert record["temperature_C"] == pytest.approx(hot_C, abs=2.0)
        assert record["sensitivity"]["area_m2"] == pytest.approx(5.411e-4, abs=5.0e-8)
        assert record["sensitivity"]["face_set"] == "full_side"
        assert record["sensitivity"]["temperature_C"] == pytest.approx(full_C, abs=2.0)
        assert record["cold_boundary_K"] == pytest.approx(300.0)
        assert record["surroundings_K"] == pytest.approx(300.0)
        assert record["conductivity_W_m_K"] == pytest.approx(1.0)
        assert "pellet_conductivity_W_m_K" not in {
            notice["knob"] for notice in record["notices"]
        }

    # The noticed default is the investigation's k = 1, not a retune.
    default_k = _peak_pellet(conductivity_W_m_K=None)
    assert default_k["temperature_C"] == pytest.approx(
        solved[0.85]["temperature_C"]
    )
    assert default_k["sensitivity"]["temperature_C"] == pytest.approx(
        solved[0.85]["sensitivity"]["temperature_C"]
    )
    # The Gaier midpoint is 0.7875; its independently recomputed hot-upper
    # root is 2025.77 °C, rounded to the 2026 °C default.
    midpoint = _peak_pellet(solar_absorptance=SOLAR_ABSORPTANCE_DEFAULT)
    assert SOLAR_ABSORPTANCE_DEFAULT == pytest.approx(0.7875)
    assert midpoint["temperature_C"] == pytest.approx(2026.0, abs=0.5)


def test_holder_series_is_not_the_cold_boundary():
    trace = {
        "role": "measured_driver",
        "units": "W",
        "point_fields": ["time_min", "expected_value"],
        "points": [[0.0, 100.0], [1.0, 100.0]],
    }
    driver = furnace_power_driver_payload(
        trace,
        mass_kg=0.00338,
        exposed_area_m2=None,
        pellet_diameter_m=0.013,
        pellet_height_m=0.010,
        focal_spot_diameter_m=0.020,
        radiating_face_set="hot_upper",
        solar_absorptance=0.85,
        window_transmittance=1.0,
    )
    driver["holder_temperature_C"] = 1500.0
    decoy = melt_temperature_from_furnace_power(
        driver,
        time_s=60.0,
        previous_temperature_C=25.0,
        dt_s=60.0,
    )
    bare = solve_power_driven_melt_temperature_C(
        power_W=100.0,
        previous_temperature_C=25.0,
        dt_s=60.0,
        mass_kg=0.00338,
        area_m2=None,
        emissivity=None,
        pellet_diameter_m=0.013,
        pellet_height_m=0.010,
        focal_spot_diameter_m=0.020,
        radiating_face_set="hot_upper",
        solar_absorptance=0.85,
        window_transmittance=1.0,
    )

    assert decoy["temperature_C"] == pytest.approx(bare["temperature_C"])
    assert decoy["cold_boundary_K"] == pytest.approx(300.0)
    assert decoy["surroundings_K"] == pytest.approx(300.0)
    source = next(
        notice["source"]
        for notice in decoy["notices"]
        if notice["knob"] == "cold_boundary_K"
    )
    assert "1500" in source


def test_a_non_positive_conductivity_on_a_pellet_refuses():
    with pytest.raises(PowerTemperatureRefusal) as refused:
        _peak_pellet(conductivity_W_m_K=0.0)
    assert refused.value.reason == "pellet_conductivity_not_positive"


def test_conductivity_without_a_printed_cylinder_refuses():
    with pytest.raises(PowerTemperatureRefusal) as refused:
        solve_power_driven_melt_temperature_C(
            power_W=1740.1373551492054,
            previous_temperature_C=1800.0,
            dt_s=60.0,
            mass_kg=1.0,
            area_m2=None,
            emissivity=0.85,
            conductivity_W_m_K=1.0,
        )
    assert refused.value.reason == "pellet_geometry_incomplete"
