"""Gas-phase O2 trajectory: one owner for molar flow, pO2, and ppm.

The Robinot Fig. 4a score uses the booked melt-offgas bleed. It does
not call the pyrolysis engine. A temperature walk calls only
``solve_power_driven_melt_temperature_C``.
"""

from __future__ import annotations

import math
import sys
import warnings
from collections import Counter
from pathlib import Path

import pytest
import yaml

from simulator.account_ids import (
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
)
from simulator.condensation import gram_lab_exposed_melt_area_bridge
from simulator.diagnostic_helpers.reproduction_compare import (
    STANDARD_MOLAR_VOLUME_L_PER_MOL,
    TimeSeriesComparisonRefusal,
    compare_o2_release_time_series,
    compare_time_series,
)
from simulator.diagnostic_helpers.timeseries_concordance import trapezoid_integral
from simulator.gas_phase_o2 import (
    CONDENSATION_TRAIN_ACCOUNT,
    OWNER_NAME,
    analyzer_visible_ppm,
    gas_phase_o2_trajectory,
    o2_mole_fraction_in_carrier,
    step_records_from_snapshots,
)
from simulator.physical_constants import PA_PER_MBAR
from simulator.process_timestep import exponential_approach_fraction
from simulator.runner import (
    _gas_phase_o2_trajectory_for_run,
    _load_preset_run_spec,
)
from simulator.thermal_budget import (
    FACE_FULL_SIDE,
    FACE_HOT_UPPER,
    FURNACE_POWER_DRIVER_KEY,
    GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_HIGH,
    GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_LOW,
    SOLAR_ABSORPTANCE_DEFAULT,
    PowerTemperatureRefusal,
    furnace_power_trace_last_sample_s,
    pellet_face_areas_m2,
    sample_furnace_power_W,
    solve_power_driven_melt_temperature_C,
)


_ROOT = Path(__file__).resolve().parents[1]
_PRESET = _ROOT / "data" / "presets" / "vacuum_pyrolysis" / "robinot_2026.yaml"
_CARRIER_NL_MIN = 0.3
_CHAMBER_MBAR = 13.0
_DT_S = 30.0


def _emit(text: str) -> None:
    """The gate profile refuses pytest ``-s``, and xdist does not forward
    a worker's original stdout. The warning summary does travel.
    """

    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()
    warnings.warn(text, UserWarning, stacklevel=2)


def _preset() -> dict:
    loaded = yaml.safe_load(_PRESET.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _observation_points(preset: dict) -> list[dict]:
    observable = next(
        item
        for item in preset["trajectory_comparison"]["observables"]
        if item["observable_id"] == "robinot_o2_ppm_time_series"
    )
    fields = observable["point_fields"]
    return [
        {
            "time_min": point[fields.index("time_min")],
            "expected_value": point[fields.index("expected_value")],
            "expected_uncertainty": {
                "kind": "absolute",
                "value": point[fields.index("combined_uncertainty")],
            },
        }
        for point in observable["points"]
    ]


def _compare_release(points, samples):
    return compare_o2_release_time_series(
        case_id="robinot_2026",
        source_id="kems-044-robinot-2026",
        observable_id="robinot_o2_ppm_time_series",
        observation_points=points,
        runtime_rate_samples=samples,
        argon_flow_nl_min=_CARRIER_NL_MIN,
        evidence_scope="analyzer_visible_o2_in_argon_carrier",
        source_locator={"figure": "4a"},
        recipe={"preset": "robinot_2026"},
        observation={"points": points},
        runtime={"rates": samples},
    )


def _one_percent_rate(carrier_nl_min: float) -> float:
    n_carrier = carrier_nl_min / STANDARD_MOLAR_VOLUME_L_PER_MOL
    return n_carrier / 99.0


def test_eq1_mole_fraction_is_one_percent_at_10000_ppm() -> None:
    rate = _one_percent_rate(_CARRIER_NL_MIN)
    fraction = o2_mole_fraction_in_carrier(
        rate,
        carrier_flow_nl_min=_CARRIER_NL_MIN,
    )
    assert fraction == pytest.approx(0.01)
    assert analyzer_visible_ppm(
        rate,
        carrier_flow_nl_min=_CARRIER_NL_MIN,
    ) == pytest.approx(10_000.0)
    # The dilute ratio n_O2 / n_carrier is not Eq. (1).
    n_carrier = _CARRIER_NL_MIN / STANDARD_MOLAR_VOLUME_L_PER_MOL
    assert abs((rate / n_carrier) - fraction) > 1.0e-4


def test_comparator_is_a_caller_of_the_mole_fraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate = _one_percent_rate(_CARRIER_NL_MIN)
    points = [
        {
            "time_min": 0.0,
            "expected_value": 10_000.0,
            "expected_uncertainty": {"kind": "absolute", "value": 1.0},
        },
        {
            "time_min": 1.0,
            "expected_value": 10_000.0,
            "expected_uncertainty": {"kind": "absolute", "value": 1.0},
        },
    ]
    samples = [
        {"time_min": 0.0, "o2_release_mol_min": rate},
        {"time_min": 1.0, "o2_release_mol_min": rate},
    ]
    clean = _compare_release(points, samples)
    assert clean[0].residual == pytest.approx(0.0, abs=1e-9)

    def dilute(o2_release_mol_min, *, carrier_flow_nl_min):
        n_dot_carrier = (
            float(carrier_flow_nl_min) / STANDARD_MOLAR_VOLUME_L_PER_MOL
        )
        return float(o2_release_mol_min) / n_dot_carrier

    monkeypatch.setattr(
        "simulator.gas_phase_o2.o2_mole_fraction_in_carrier",
        dilute,
    )
    mutated = _compare_release(points, samples)
    assert mutated[0].residual != pytest.approx(0.0)
    assert mutated[0].actual_value == pytest.approx(1_000_000.0 / 99.0)


def test_condensation_train_is_recorded_and_not_added_to_the_flow() -> None:
    class Snap:
        t_s = 30.0
        duration_h = 30.0 / 3600.0
        melt_offgas_O2_mol_hr = 0.0015
        overhead = {"composition": {"O2": 1.5}}
        condensation_totals = {"O2": 0.004}
        oxygen_produced_kg = 3.5e-5

    records = step_records_from_snapshots([Snap()])
    buried = dict(records[0])
    buried["condensation_train_o2_kg"] = 1.0
    bare = dict(records[0])
    bare["condensation_train_o2_kg"] = 0.0
    common = {
        "carrier_flow_nl_min": _CARRIER_NL_MIN,
        "chamber_pressure_mbar": _CHAMBER_MBAR,
        "analyzer_tau_s": None,
    }
    with_train = gas_phase_o2_trajectory([buried], **common)
    without = gas_phase_o2_trajectory([bare], **common)
    assert with_train["points"][0]["o2_release_mol_min"] == pytest.approx(
        without["points"][0]["o2_release_mol_min"]
    )
    assert with_train["points"][0]["o2_release_mol_min"] == pytest.approx(
        0.0015 * 60.0 / with_train["points"][0]["dt_s"]
    )
    assert with_train["points"][0]["condensation_train_o2_kg"] == pytest.approx(
        1.0
    )
    assert with_train["points"][0]["furnace_p_O2_Pa"] == pytest.approx(
        1.5 * PA_PER_MBAR
    )
    assert with_train["accounts"]["not_the_analyzer"] == (
        CONDENSATION_TRAIN_ACCOUNT
    )
    assert CONDENSATION_TRAIN_ACCOUNT not in with_train["accounts"][
        "analyzer_visible_terminal"
    ]
    assert with_train["accounts"]["analyzer_visible_terminal"] == [
        OXYGEN_MELT_OFFGAS_ACCOUNT,
        OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
        OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT,
    ]
    assert with_train["owner"] == OWNER_NAME

    explicit = dict(records[0])
    explicit["o2_release_mol_min"] = 0.0
    zero = gas_phase_o2_trajectory([explicit], **common)
    assert zero["points"][0]["o2_release_mol_min"] == 0.0
    assert zero["points"][0]["ppm_instant"] == 0.0

    missing = {
        "time_s": 30.0,
        "dt_s": 30.0,
        "condensation_train_o2_kg": 0.004,
    }
    withheld = gas_phase_o2_trajectory([missing], **common)
    assert withheld["points"][0]["o2_release_mol_min"] is None
    assert withheld["points"][0]["ppm_instant"] is None
    with pytest.raises(TimeSeriesComparisonRefusal) as refused:
        compare_time_series(
            case_id="synthetic",
            source_id="source",
            observable_id="o2_ppm",
            observation_points=[
                {
                    "time_min": 0.0,
                    "expected_value": 1.0,
                    "expected_uncertainty": {"kind": "absolute", "value": 1.0},
                },
                {
                    "time_min": 0.5,
                    "expected_value": 1.0,
                    "expected_uncertainty": {"kind": "absolute", "value": 1.0},
                },
            ],
            runtime_samples=[
                {"time_min": 0.0, "value": 0.0},
                {"time_min": 0.5, "value": None},
            ],
            series_semantics="rate",
            units="ppm",
            evidence_scope="synthetic",
            source_locator="synthetic",
            recipe={},
            observation={},
            runtime={},
        )
    assert refused.value.reason_code == "runtime_sample_missing"


def test_furnace_partial_pressure_does_not_need_a_carrier() -> None:
    trajectory = gas_phase_o2_trajectory(
        [
            {
                "time_s": 10.0,
                "dt_s": 10.0,
                "overhead_o2_partial_mbar": 2.5,
            }
        ],
        carrier_flow_nl_min=None,
        chamber_pressure_mbar=None,
        analyzer_tau_s=30.0,
    )
    point = trajectory["points"][0]
    assert point["ppm_analyzer"] is None
    assert point["p_O2_flow_Pa"] is None
    assert point["furnace_p_O2_Pa"] == pytest.approx(2.5 * PA_PER_MBAR)
    assert any(
        notice["reason"] == "carrier_flow_missing"
        for notice in trajectory["notices"]
    )


def test_lag_uses_the_process_timestep_fraction_and_moves_the_peak() -> None:
    tau_s = 30.0
    rate = _one_percent_rate(_CARRIER_NL_MIN)
    fraction = exponential_approach_fraction(tau_s, _DT_S)
    stepped = gas_phase_o2_trajectory(
        [
            {
                "time_s": 0.0,
                "dt_s": _DT_S,
                "o2_release_mol_min": 0.0,
                "anchor": "pre_step_baseline",
            },
            {
                "time_s": _DT_S,
                "dt_s": _DT_S,
                "o2_release_mol_min": rate,
            },
        ],
        carrier_flow_nl_min=_CARRIER_NL_MIN,
        chamber_pressure_mbar=_CHAMBER_MBAR,
        analyzer_tau_s=tau_s,
    )
    instant = stepped["points"][1]["ppm_instant"]
    assert instant == pytest.approx(10_000.0)
    assert stepped["points"][1]["ppm_analyzer"] == pytest.approx(
        fraction * instant
    )
    assert stepped["points"][1]["p_O2_flow_Pa"] == pytest.approx(
        0.01 * _CHAMBER_MBAR * PA_PER_MBAR
    )

    def _triangle(tau: float) -> dict:
        steps = []
        for index, time_s in enumerate(range(0, 2481, 10)):
            if time_s <= 40:
                value = time_s / 40.0
            elif time_s <= 80:
                value = (80.0 - time_s) / 40.0
            else:
                value = 0.0
            step = {
                "time_s": float(time_s),
                "dt_s": 10.0,
                "o2_release_mol_min": value * 1.0e-4,
            }
            if index == 0:
                step["anchor"] = "pre_step_baseline"
            steps.append(step)
        return gas_phase_o2_trajectory(
            steps,
            carrier_flow_nl_min=_CARRIER_NL_MIN,
            chamber_pressure_mbar=_CHAMBER_MBAR,
            analyzer_tau_s=tau,
        )

    peaks = {}
    molar = {}
    ppm_integrals = {}
    for tau in (5.0, 30.0, 120.0):
        trajectory = _triangle(tau)
        points = trajectory["points"]
        peak = max(points, key=lambda point: point["ppm_analyzer"])
        peaks[tau] = peak["time_s"]
        molar[tau] = trapezoid_integral(
            [
                (point["time_min"], point["o2_release_mol_min"])
                for point in points
            ],
            allow_negative_values=True,
        )
        ppm_integrals[tau] = trapezoid_integral(
            [(point["time_min"], point["ppm_analyzer"]) for point in points],
            allow_negative_values=True,
        )
    assert peaks[120.0] > peaks[5.0]
    assert peaks[120.0] >= peaks[30.0] >= peaks[5.0]
    assert molar[5.0] == pytest.approx(molar[30.0])
    assert molar[30.0] == pytest.approx(molar[120.0])
    lines = ["tau_s peak_time_s molar_integral_mol ppm_integral_ppm_min"]
    for tau in (5.0, 30.0, 120.0):
        lines.append(
            f"{tau:.0f} {peaks[tau]:.1f} {molar[tau]:.6e} {ppm_integrals[tau]:.6e}"
        )
    _emit("\n".join(lines))


def test_carrier_flow_scales_ppm_and_not_the_molar_integral() -> None:
    """0.15 and 0.6 NL/min are a bound, not adopted values.

    The printed carrier is 0.3 NL/min. A zero bleed does not move with
    it. This pulse shows the inverse scale for a nonzero release.
    """

    def _pulse(carrier: float) -> dict:
        return gas_phase_o2_trajectory(
            [
                {
                    "time_s": 0.0,
                    "dt_s": _DT_S,
                    "o2_release_mol_min": 0.0,
                    "anchor": "pre_step_baseline",
                },
                {
                    "time_s": _DT_S,
                    "dt_s": _DT_S,
                    "o2_release_mol_min": 1.0e-6,
                },
                {
                    "time_s": 2.0 * _DT_S,
                    "dt_s": _DT_S,
                    "o2_release_mol_min": 0.0,
                },
            ],
            carrier_flow_nl_min=carrier,
            chamber_pressure_mbar=_CHAMBER_MBAR,
            analyzer_tau_s=None,
        )

    series = {carrier: _pulse(carrier) for carrier in (0.15, 0.3, 0.6)}

    def _molar(trajectory: dict) -> float:
        return trapezoid_integral(
            [
                (point["time_min"], point["o2_release_mol_min"])
                for point in trajectory["points"]
            ],
            allow_negative_values=True,
        )

    def _ppm(trajectory: dict) -> float:
        return trapezoid_integral(
            [
                (point["time_min"], point["ppm_instant"])
                for point in trajectory["points"]
            ],
            allow_negative_values=True,
        )

    assert _molar(series[0.15]) == pytest.approx(_molar(series[0.3]))
    assert _molar(series[0.3]) == pytest.approx(_molar(series[0.6]))
    ratio = _ppm(series[0.15]) / _ppm(series[0.3])
    assert 1.9 < ratio < 2.1
    _emit(
        "carrier_nl_min ppm_integral_ppm_min "
        f"0.15={_ppm(series[0.15]):.6e} "
        f"0.30={_ppm(series[0.3]):.6e} "
        f"0.60={_ppm(series[0.6]):.6e} "
        f"ratio_0.15_over_0.30={ratio:.4f} "
        "adopted_carrier_nl_min=0.3"
    )


def test_booked_zero_bleed_flags_every_fig4a_point() -> None:
    preset = _preset()
    observations = _observation_points(preset)
    assert len(observations) == 120
    assert observations[0]["time_min"] == pytest.approx(0.0)
    assert observations[-1]["time_min"] == pytest.approx(59.5)
    steps = [
        {
            "time_s": 0.0,
            "dt_s": _DT_S,
            "o2_release_mol_min": 0.0,
            "melt_offgas_o2_step_mol": 0.0,
            "anchor": "pre_step_baseline",
        }
    ]
    for index in range(1, 120):
        steps.append(
            {
                "time_s": index * _DT_S,
                "dt_s": _DT_S,
                "o2_release_mol_min": 0.0,
                "melt_offgas_o2_step_mol": 0.0,
            }
        )
    notice = preset["lab_schedule"]["analyzer_response"]["notice"]
    trajectory = gas_phase_o2_trajectory(
        steps,
        carrier_flow_nl_min=_CARRIER_NL_MIN,
        chamber_pressure_mbar=_CHAMBER_MBAR,
        analyzer_tau_s=preset["lab_schedule"]["analyzer_response"]["tau_s"],
        analyzer_notice=notice,
    )
    assert trajectory["owner"] == OWNER_NAME
    assert len(trajectory["points"]) == 120
    assert all(point["ppm_analyzer"] == 0.0 for point in trajectory["points"])
    assert any(
        item["reason"] == "analyzer_response_time_not_printed"
        for item in trajectory["notices"]
    )
    samples = [
        {"time_min": point["time_min"], "value": point["ppm_analyzer"]}
        for point in trajectory["points"]
    ]
    records = compare_time_series(
        case_id="robinot_2026",
        source_id="kems-044-robinot-2026",
        observable_id="robinot_o2_ppm_time_series",
        observation_points=observations,
        runtime_samples=samples,
        series_semantics="rate",
        units="ppm",
        evidence_scope="analyzer_visible_o2_in_argon_carrier",
        source_locator={"figure": "4a"},
        recipe={"preset": "robinot_2026"},
        observation={"points": observations},
        runtime={"samples": samples},
        species="O2",
        assumed_input=True,
    )
    points = [
        record
        for record in records
        if record.observable_id == "robinot_o2_ppm_time_series"
    ]
    integrals = [
        record
        for record in records
        if record.observable_id.endswith(":integral")
    ]
    assert len(points) == 120
    assert len(integrals) == 1
    counts = Counter(record.status for record in points)
    assert counts["assumed-input"] == 120
    assert counts["match"] + counts["mismatch"] == 0
    for record in points:
        assert record.residual == pytest.approx(-record.expected_value)
        assert record.actual_value == 0.0
    residuals = [float(record.residual) for record in points]
    rms = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    max_abs = max(abs(value) for value in residuals)
    inside = sum(
        1
        for record in points
        if abs(float(record.residual))
        <= float(record.expected_uncertainty["value"])
    )
    assert integrals[0].actual_value == pytest.approx(0.0)
    assert integrals[0].status == "assumed-input"
    _emit(
        "BENCHM1 robinot_o2_ppm_time_series "
        f"points=120 scored=0 flagged=120 refused=0 "
        f"rms={rms:.6f} max_abs={max_abs:.6f} inside={inside} "
        f"integral_expected={integrals[0].expected_value:.6f} "
        f"integral_actual=0 integral_residual={integrals[0].residual:.6f} "
        "model_ppm=0 status=assumed-input"
    )


def test_robinot_deck_brackets_fig4a_without_using_area_as_radiation() -> None:
    spec = _load_preset_run_spec(_PRESET, "faithful")
    assert spec.process_dt_s == pytest.approx(30.0)
    assert spec.step_count == 119
    assert spec.hours == 119
    assert spec.provenance["duration_h"] == pytest.approx(1.0)
    window = spec.provenance["power_window_notice"]
    assert window["reason"] == "measured_power_trace_ends_before_declared_duration"
    assert window["executed_step_count"] == 119
    assert window["trace_last_sample_s"] == pytest.approx(3570.0)
    assert window["kind"] == "typed_unknown"
    driver = spec.lab_schedule[FURNACE_POWER_DRIVER_KEY]
    assert driver["exposed_area_m2"] is None
    assert driver["radiating_face_set"] == FACE_HOT_UPPER
    areas = pellet_face_areas_m2(0.013, 0.010)
    sample = spec.document["lab_geometry"]["sample"]
    assert sample["exposed_melt_area_m2"] == pytest.approx(areas["top"])
    bridge = gram_lab_exposed_melt_area_bridge(spec.lab_geometry)
    assert bridge["effective_exposed_area_m2"] == pytest.approx(areas["top"])
    assert bridge["notice"]["reason"] == "exposed_melt_area_not_printed"
    assert bridge["notice"]["value"] == pytest.approx(areas["top"])
    response = spec.lab_schedule["analyzer_response"]
    assert response["kind"] == "first_order_lag"
    assert response["tau_s"] == 30
    assert response["notice"]["reason"] == "analyzer_response_time_not_printed"

    with pytest.raises(PowerTemperatureRefusal) as refused:
        solve_power_driven_melt_temperature_C(
            power_W=100.0,
            previous_temperature_C=25.0,
            dt_s=_DT_S,
            mass_kg=0.00338,
            area_m2=areas["top"],
            emissivity=None,
            pellet_diameter_m=0.013,
            pellet_height_m=0.010,
            focal_spot_diameter_m=0.020,
            radiating_face_set=FACE_HOT_UPPER,
            solar_absorptance=None,
            window_transmittance=None,
            conductivity_W_m_K=None,
        )
    assert refused.value.reason == "radiating_area_ambiguous"


def test_power_root_sweep_reports_peak_temperature_and_the_1400_c_line() -> None:
    spec = _load_preset_run_spec(_PRESET, "faithful")
    trace = spec.lab_schedule[FURNACE_POWER_DRIVER_KEY]["trace"]
    assert furnace_power_trace_last_sample_s(trace) == pytest.approx(3570.0)
    areas = pellet_face_areas_m2(0.013, 0.010)
    faces = (
        ("top", areas["top"], None),
        (FACE_HOT_UPPER, None, FACE_HOT_UPPER),
        (FACE_FULL_SIDE, None, FACE_FULL_SIDE),
    )
    absorptances = (
        GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_LOW,
        SOLAR_ABSORPTANCE_DEFAULT,
        GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_HIGH,
    )
    lines = [
        "face absorptance peak_C peak_min minute1_C minute30_C crosses_1400"
    ]
    nominal_minute_30 = None
    nominal_peak = None
    for face_name, area_m2, face_set in faces:
        for absorptance in absorptances:
            previous = 25.0
            peak = previous
            peak_s = 0.0
            minute_1 = None
            minute_30 = None
            crosses = False
            for step in range(1, 120):
                time_s = step * _DT_S
                record = solve_power_driven_melt_temperature_C(
                    power_W=sample_furnace_power_W(trace, time_s),
                    previous_temperature_C=previous,
                    dt_s=_DT_S,
                    mass_kg=0.00338,
                    area_m2=area_m2,
                    emissivity=None,
                    pellet_diameter_m=0.013,
                    pellet_height_m=0.010,
                    focal_spot_diameter_m=0.020,
                    radiating_face_set=face_set,
                    solar_absorptance=absorptance,
                    window_transmittance=None,
                    conductivity_W_m_K=None,
                )
                previous = float(record["temperature_C"])
                assert math.isfinite(previous)
                if previous > peak:
                    peak = previous
                    peak_s = time_s
                if time_s == 60.0:
                    minute_1 = previous
                if time_s == 1800.0:
                    minute_30 = previous
                if previous >= 1400.0:
                    crosses = True
            if (
                face_name == FACE_HOT_UPPER
                and absorptance == SOLAR_ABSORPTANCE_DEFAULT
            ):
                nominal_minute_30 = minute_30
                nominal_peak = peak
            assert minute_1 is not None and minute_30 is not None
            lines.append(
                f"{face_name} {absorptance:.4f} {peak:.2f} "
                f"{peak_s / 60.0:.2f} {minute_1:.2f} {minute_30:.2f} "
                f"{int(crosses)}"
            )
    _emit("\n".join(lines))
    assert nominal_minute_30 is not None and nominal_peak is not None
    assert 1500.0 < nominal_minute_30 < 2500.0
    assert nominal_peak > 1400.0
    _emit(
        "o2_molar_integral_mol=0 for every face and absorptance in this "
        "probe: the bleed writer is not called. A peak at or above 1400 C "
        "can open peak_SiO_window_1400_1600C; that integral is not invented."
    )


def test_runner_inserts_the_pre_step_baseline() -> None:
    class Snap:
        t_s = 30.0
        duration_h = 30.0 / 3600.0
        melt_offgas_O2_mol_hr = 0.0
        condensation_totals = {"O2": 0.002}

    schedule = {
        "gas_boundary": {"imposed_flow": {"value": 0.3}},
        "chamber_pressure_mbar": ({"value": 13.0},),
        "analyzer_response": {
            "tau_s": 30,
            "notice": {
                "kind": "typed_unknown",
                "reason": "analyzer_response_time_not_printed",
            },
        },
    }
    trajectory = _gas_phase_o2_trajectory_for_run(schedule, [Snap()])
    assert trajectory["owner"] == OWNER_NAME
    assert trajectory["points"][0]["anchor"] == "pre_step_baseline"
    assert trajectory["points"][0]["time_s"] == 0.0
    assert trajectory["points"][0]["o2_release_mol_min"] == 0.0
    assert trajectory["points"][1]["o2_release_mol_min"] == 0.0
    assert trajectory["points"][1]["condensation_train_o2_kg"] == pytest.approx(
        0.002
    )
    assert trajectory["points"][1]["ppm_analyzer"] == 0.0
