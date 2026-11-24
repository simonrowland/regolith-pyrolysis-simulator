from __future__ import annotations

from simulator.diagnostic_helpers.reproduction_compare import compare_time_series


def test_rate_pointwise_and_integral_outputs_are_pinned_before_shared_move() -> None:
    observation_points = (
        {
            "time_min": 0.0,
            "expected_value": 1.0,
            "expected_uncertainty": {"kind": "absolute", "value": 0.0},
            "source_locator": {"point_index": 0},
        },
        {
            "time_min": 1.0,
            "expected_value": 3.0,
            "expected_uncertainty": {"kind": "absolute", "value": 0.0},
            "source_locator": {"point_index": 1},
        },
    )
    records = compare_time_series(
        case_id="pointwise_refactor_pin",
        source_id="synthetic",
        observable_id="synthetic_rate",
        observation_points=observation_points,
        runtime_samples=(
            {"time_min": 0.0, "value": 2.0},
            {"time_min": 1.0, "value": 4.0},
        ),
        series_semantics="rate",
        units="ppm",
        evidence_scope="synthetic rate comparison pin",
        source_locator={"figure": "synthetic"},
        recipe={"preset": "synthetic"},
        observation={"points": observation_points},
        runtime={"samples": ((0.0, 2.0), (1.0, 4.0))},
    )

    assert [
        (
            record.expected_value.hex(),
            record.actual_value.hex(),
            record.residual.hex(),
            record.units,
            record.status,
            record.source_locator,
        )
        for record in records
    ] == [
        (
            "0x1.0000000000000p+0",
            "0x1.0000000000000p+1",
            "0x1.0000000000000p+0",
            "ppm",
            "mismatch",
            {"figure": "synthetic", "point_index": 0},
        ),
        (
            "0x1.8000000000000p+1",
            "0x1.0000000000000p+2",
            "0x1.0000000000000p+0",
            "ppm",
            "mismatch",
            {"figure": "synthetic", "point_index": 1},
        ),
        (
            "0x1.0000000000000p+1",
            "0x1.8000000000000p+1",
            "0x1.0000000000000p+0",
            "ppm min",
            "mismatch",
            {
                "figure": "synthetic",
                "integral_of": "synthetic_rate",
                "method": "trapezoid",
            },
        ),
    ]
