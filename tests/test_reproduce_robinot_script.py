from __future__ import annotations

import json

import pytest

from scripts.benchtop import reproduce_robinot_2026 as reproduction
from scripts.benchtop.reproduce_robinot_2026 import (
    _required_corpus_paths,
    run_reproduction,
)
from simulator.corpus_paths import regolith_corpus_root


RECORD_FIELDS = {
    "t",
    "observable",
    "published",
    "simulated",
    "residual",
    "flags",
}


def test_robinot_pyrometer_observation_uses_selected_preset_point() -> None:
    observation, reason = reproduction._reported_pyrometer_observation(
        reproduction._preset_spec()
    )

    assert reason is None
    assert observation is not None
    assert observation["value"] == 1800
    assert observation["qualifier"] == "approximate_peak"
    assert observation["source_ref"] == "robinot_2026_methods"


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
        include_sensitivity=False,
    )

    assert result["runs"]["imposed"]["runtime"]["steps_completed"] == 3
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
        assert len(temperature_times) == 3
        assert len(temperature_times) == len(set(temperature_times))

    json_path, markdown_path = reproduction._write_outputs(result, tmp_path)
    written = json.loads(json_path.read_text(encoding="utf-8"))
    report = markdown_path.read_text(encoding="utf-8")
    assert written["records"] == records
    assert "typed_unavailable" in report
    assert "missing_reported_pyrometer_observation" in report
    progress_lines = [
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith("Robinot reproduction: starting ")
    ]
    assert progress_lines == [
        "Robinot reproduction: starting imposed",
        "Robinot reproduction: starting limiting_case_unrestricted_conductance",
    ]
