from __future__ import annotations

import json

import pytest

from scripts.benchtop import reproduce_robinot_2026 as reproduction
from scripts.benchtop.reproduce_robinot_2026 import (
    _required_corpus_paths,
    run_reproduction,
)
from simulator.corpus_paths import regolith_corpus_root
from simulator.thermal_budget import (
    PowerTemperatureRefusal,
    sample_furnace_power_W,
)


RECORD_FIELDS = {
    "t",
    "observable",
    "published",
    "simulated",
    "residual",
    "flags",
}


def test_booked_o2_molar_flow_is_the_cumulative_stock_increment() -> None:
    step_min = 1.0
    stock_kg = 38.91358227877225e-6
    cumulative_mg, rate = reproduction._booked_o2_step_observables(
        stock_kg,
        previous_kg=0.0,
        step_min=step_min,
    )

    assert cumulative_mg == pytest.approx(stock_kg * 1_000_000.0)
    recovered_mg = (
        rate
        * step_min
        * reproduction.OXYGEN_MOLAR_MASS_KG_PER_MOL
        * 1_000_000.0
    )
    assert recovered_mg == pytest.approx(cumulative_mg)
    hour_scaled = (
        stock_kg / reproduction.OXYGEN_MOLAR_MASS_KG_PER_MOL
    ) / 60.0
    assert rate == pytest.approx(hour_scaled * 60.0)
    with pytest.raises(reproduction.RobinotReproductionError):
        reproduction._booked_o2_step_observables(
            0.0,
            previous_kg=1.0e-6,
            step_min=step_min,
        )


def test_robinot_step_budget_is_one_hour_of_process_steps() -> None:
    spec = reproduction._preset_spec()

    assert spec.process_dt_s == 60
    assert spec.provenance["duration_h"] == 1.0
    assert spec.step_count == 60
    assert spec.hours == 60
    budget = reproduction._reproduction_step_budget(spec)
    assert budget < spec.hours
    trace = spec.lab_schedule[reproduction.FURNACE_POWER_DRIVER_KEY]["trace"]
    dt_s = float(spec.process_dt_s)
    sample_furnace_power_W(trace, budget * dt_s)
    with pytest.raises(PowerTemperatureRefusal) as refused:
        sample_furnace_power_W(trace, (budget + 1) * dt_s)
    assert refused.value.reason == "power_trace_does_not_bracket"
    assert reproduction._reproduction_end_reason(
        spec,
        steps_requested=budget,
        steps_completed=budget,
        execution_status="ok",
        execution_reason="",
    ) == "driver_trace_end"
    assert reproduction._reproduction_end_reason(
        spec,
        steps_requested=3,
        steps_completed=3,
        execution_status="ok",
        execution_reason="",
    ) == ""
    assert reproduction._reproduction_end_reason(
        spec,
        steps_requested=budget,
        steps_completed=budget,
        execution_status="failed",
        execution_reason="power_trace_does_not_bracket",
    ) == "power_trace_does_not_bracket"


def test_robinot_pyrometer_observation_uses_selected_preset_point() -> None:
    observation, reason = reproduction._reported_pyrometer_observation(
        reproduction._preset_spec()
    )

    assert reason is None
    assert observation is not None
    assert observation["value"] == 1800
    assert observation["qualifier"] == "approximate_peak"
    assert observation["source_ref"] == "robinot_2026_methods"
    assert observation["citation_id"] == "robinot_2026"


def test_truncated_three_step_run_writes_outputs_when_pyrometer_is_unavailable(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    corpus_root = regolith_corpus_root()
    missing = [
        str(path)
        for path in _required_corpus_paths(corpus_root).values()
        if not path.is_file()
    ]
    if missing:
        pytest.skip(f"Robinot corpus inputs are absent: {', '.join(missing)}")

    monkeypatch.setattr(
        reproduction,
        "reported_temperature_observations",
        lambda _points: (),
    )
    result = run_reproduction(
        corpus_root=corpus_root,
        max_steps=3,
        include_sensitivity=True,
    )

    assert result["runs"]["imposed"]["runtime"]["steps_completed"] == 3, (
        result["runs"]["imposed"]["runtime"]
    )
    assert len(result["runs"]["imposed"]["teststand_pressure_records"]) == 3
    assert (
        result["runs"]["limiting_case_unrestricted_conductance"]["runtime"][
            "steps_completed"
        ]
        == 3
    )
    assert len(
        result["runs"]["limiting_case_unrestricted_conductance"][
            "teststand_pressure_records"
        ]
    ) == 3
    assert result["limiting_case"]["conductance_reconciliation_diagnostic"][
        "used_in_scored_run"
    ] is False
    assert result["deck"]["exposed_melt_area"]["area_m2"] == pytest.approx(
        reproduction._preset_spec().lab_geometry["sample"][
            "exposed_melt_area_m2"
        ]
    )
    assert result["deck"]["exposed_melt_area"]["source_locator"][
        "physical_pdf_page"
    ] == 5
    sensitivity_cases = result["sensitivity"]["cases"]
    assert {
        "imposed_full_side_default",
        "imposed_hot_upper_alpha_0.669",
        "imposed_hot_upper_alpha_0.906",
        "imposed_molten_bead_equal_volume_sphere",
    } <= sensitivity_cases.keys()
    bead_case = sensitivity_cases["imposed_molten_bead_equal_volume_sphere"]
    assert bead_case["exposed_melt_area_m2"] != pytest.approx(
        result["deck"]["exposed_melt_area"]["area_m2"]
    )
    assert bead_case["exposed_melt_area_basis"] == (
        "assumed_equal_volume_molten_bead_sphere"
    )
    area_notices = [
        notice
        for notice in result["notices_and_flags"]
        if notice["kind"] == "assumption/knob_defaulted"
    ]
    assert any(
        notice["value"]["source_locator"]["physical_pdf_page"] == 5
        for notice in area_notices
    )
    records = result["records"]
    assert records
    assert all(RECORD_FIELDS <= record.keys() for record in records)
    assert {record["mode"] for record in records} == {
        "imposed",
        "limiting_case_unrestricted_conductance",
    }
    assert {record["observable"] for record in records} >= {
        "robinot_o2_ppm_time_series",
        "robinot_cumulative_o2_mass_time_series",
        "predicted_melt_temperature_C",
        "robinot_pyrometer_peak_temperature_C",
    }
    assert all(isinstance(record["flags"], list) for record in records)
    assert all(
        "assumed_input" in record["flags"]
        for record in records
        if record["observable"] == "robinot_o2_ppm_time_series"
    )
    pyrometer_records = [
        record
        for record in records
        if record["observable"] == "robinot_pyrometer_peak_temperature_C"
    ]
    assert len(pyrometer_records) == 2
    assert all(record["comparison_status"] == "typed_unavailable" for record in pyrometer_records)
    assert all(
        record["unavailable_reason"] == "missing_reported_pyrometer_observation"
        for record in pyrometer_records
    )
    assert all(
        "chamber_pressure_mbar" in record
        for record in records
        if record["observable"]
        in {
            "robinot_o2_ppm_time_series",
            "robinot_cumulative_o2_mass_time_series",
        }
    )
    for mode in {record["mode"] for record in records}:
        temperature_times = [
            record["t"]
            for record in records
            if record["mode"] == mode
            and record["observable"] == "predicted_melt_temperature_C"
        ]
        run_data = result["runs"][mode]
        expected_temperature_times = {
            float(record["time_min"])
            for record in run_data["power_temperature_records"]
            if record.get("temperature_C") is not None
        }
        assert set(temperature_times) == expected_temperature_times
        assert temperature_times
        assert len(temperature_times) == len(set(temperature_times))

    json_path, markdown_path = reproduction._write_outputs(result, tmp_path)
    written = json.loads(json_path.read_text(encoding="utf-8"))
    report = markdown_path.read_text(encoding="utf-8")
    assert written["records"] == records
    assert "typed_unavailable" in report
    assert "missing_reported_pyrometer_observation" in report
    assert "True upper-surface peak (C)" in report
    assert "Residual range (C)" in report
    assert "DP-110" in report and "DP-111" in report
    assert "sapphire at 5 um can be well below 1" in report
    assert "A_bead = 4π(3V/(4π))^(2/3)" in report
    progress_lines = [
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith("Robinot reproduction: starting ")
    ]
    assert progress_lines == [
        "Robinot reproduction: starting imposed",
        "Robinot reproduction: starting limiting_case_unrestricted_conductance",
        "Robinot reproduction: starting imposed_full_side_default",
        "Robinot reproduction: starting imposed_hot_upper_alpha_0.669",
        "Robinot reproduction: starting imposed_hot_upper_alpha_0.906",
        "Robinot reproduction: starting imposed_molten_bead_equal_volume_sphere",
    ]
