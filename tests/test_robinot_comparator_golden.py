from __future__ import annotations

from scripts.benchtop import reproduce_robinot_2026 as reproduction


def test_robinot_pyrometer_comparator_output_before_planck_change() -> None:
    record = reproduction._pyrometer_record(
        {
            "power_temperature_records": [
                {"temperature_C": 2026.0, "face_set": "hot_upper", "time_min": 59.0}
            ]
        },
        observation={"value": 1800.0, "qualifier": "approximate_peak"},
        unavailable_reason=None,
        mode="imposed",
    )

    assert record["simulated"] == 2026.0
    assert record["residual"] == 226.0
    assert record["flags"] == [
        "approximate_peak_observation",
        "prediction_not_scored_as_observed_temperature",
    ]
