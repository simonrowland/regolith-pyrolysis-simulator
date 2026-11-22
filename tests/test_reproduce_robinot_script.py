from __future__ import annotations

import pytest

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


def test_truncated_three_step_run_emits_comparison_record_schema() -> None:
    corpus_root = regolith_corpus_root()
    missing = [
        str(path)
        for path in _required_corpus_paths(corpus_root).values()
        if not path.is_file()
    ]
    if missing:
        pytest.skip(f"Robinot corpus inputs are absent: {', '.join(missing)}")

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
