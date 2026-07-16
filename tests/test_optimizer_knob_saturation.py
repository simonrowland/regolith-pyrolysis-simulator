from __future__ import annotations

import pytest

from simulator.optimize.evalspec import cache_key
from simulator.optimize.evaluate import _build_eval_inputs
from simulator.optimize.knob_saturation import compute_knob_saturation
from simulator.optimize.objective import (
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    LEGACY_ENERGY_KWH_METRIC,
)
from simulator.optimize.recipe import KnobSpec, RecipePatch, RecipeSchema


FLOAT_LOW = ("test", "float_low")
FLOAT_HIGH = ("test", "float_high")
FLOAT_MID = ("test", "float_mid")
PAIR = ("test", "pair")
INT_KNOB = ("test", "integer")
DEGENERATE = ("test", "degenerate")
MISSING_BOUNDS = ("test", "missing_bounds")
NONFINITE = ("test", "nonfinite")
CATEGORICAL = ("test", "mode")
DURATION = ("campaigns", "C0b_p_cleanup", "duration_hr")
C3_DURATION_AFTER_PATHA = ("campaigns", "C3", "duration_after_pathA_hr")
VOLTAGE = ("campaigns", "C5", "allow_mre_voltage_cap_V")
TEMP_RANGE = ("campaigns", "C0", "temp_range_C")
C2A_STAGED_DURATION = (
    "campaigns",
    "C2A_staged",
    "stages",
    "alkali_early_fe",
    "duration_hr",
)

PROFILE = {
    "profile_id": "knob-saturation-cache-key-test",
    "profile_schema_version": "profile-schema-v1",
    "feedstock": "lunar_mare_low_ti",
    "objectives": [
        {
            "metric": "oxygen_kg",
            "sense": "maximize",
            "units": "kg",
            "weight": 1.0,
            "rationale": "test oxygen objective evidence",
        }
    ],
    "constraints": {"gates": ["delivered_stream_purity"]},
    "run": {"campaign": "C0", "hours": 1, "mass_kg": 1000.0, "backend_name": "stub"},
    "fidelities": {"stub": {"backend_name": "stub", "hours": 1}},
    "seed_recipes": [
        {
            "id": "knob-saturation-seed",
            "source_campaign": "C0",
            "patch": {"campaigns": {"C0": {"temp_range_C": [20.0, 950.0]}}},
        }
    ],
}


def _schema() -> RecipeSchema:
    return RecipeSchema(
        allowlist=(
            KnobSpec(FLOAT_LOW, "float", low=0.0, high=100.0, units="C"),
            KnobSpec(FLOAT_HIGH, "float", low=0.0, high=100.0, units="C"),
            KnobSpec(FLOAT_MID, "float", low=0.0, high=100.0, units="C"),
            KnobSpec(PAIR, "float", low=0.0, high=100.0, units="C"),
            KnobSpec(INT_KNOB, "int", low=1, high=3, units="count"),
            KnobSpec(DEGENERATE, "float", low=5.0, high=5.0, units="unit"),
            KnobSpec(MISSING_BOUNDS, "float", units="unit"),
            KnobSpec(NONFINITE, "float", low=0.0, high=1.0, units="unit"),
            KnobSpec(CATEGORICAL, "categorical", choices=("off", "on")),
        )
    )


def _row(report: dict, key: str) -> dict:
    matches = [row for row in report["knobs"] if row["key"] == key]
    assert len(matches) == 1
    return matches[0]


def test_pinned_low_high_interior_and_categorical_skip() -> None:
    report = compute_knob_saturation(
        RecipePatch(
            {
                FLOAT_LOW: 0.5,
                FLOAT_HIGH: 99.5,
                FLOAT_MID: 50.0,
                CATEGORICAL: "on",
            }
        ),
        _schema(),
        active_objective_metrics=("oxygen_kg",),
    )

    assert _row(report, "test.float_low")["pinned"] == "low"
    assert _row(report, "test.float_high")["pinned"] == "high"
    assert _row(report, "test.float_mid")["pinned"] == "none"
    assert "test.mode" not in {row["key"] for row in report["knobs"]}
    assert report["pinned_count"] == 2
    assert report["no_opposing_cost_pinned_count"] == 2
    assert report["red_flag"] is True


def test_numeric_pair_splits_and_integer_bounds() -> None:
    report = compute_knob_saturation(
        RecipePatch({PAIR: [0.0, 100.0], INT_KNOB: 3}),
        _schema(),
        active_objective_metrics=("oxygen_kg",),
    )

    assert _row(report, "test.pair[0]")["pinned"] == "low"
    assert _row(report, "test.pair[1]")["pinned"] == "high"
    int_row = _row(report, "test.integer")
    assert int_row["kind"] == "int"
    assert int_row["pinned"] == "high"
    assert int_row["frac_of_range"] == pytest.approx(1.0)


def test_degenerate_range_reports_reason_without_pin() -> None:
    report = compute_knob_saturation(
        RecipePatch({DEGENERATE: 5.0}),
        _schema(),
        active_objective_metrics=("oxygen_kg",),
    )

    row = _row(report, "test.degenerate")
    assert row["pinned"] == "none"
    assert row["frac_of_range"] is None
    assert row["reason"] == "degenerate_range"
    assert report["red_flag"] is False


def test_nonfinite_and_missing_bounds_report_reason_without_pin() -> None:
    report = compute_knob_saturation(
        RecipePatch({NONFINITE: "nan", MISSING_BOUNDS: 4.2}),
        _schema(),
        active_objective_metrics=("oxygen_kg",),
    )

    nonfinite = _row(report, "test.nonfinite")
    assert nonfinite["pinned"] == "none"
    assert nonfinite["reason"] == "nonfinite_value"
    missing_bounds = _row(report, "test.missing_bounds")
    assert missing_bounds["pinned"] == "none"
    assert missing_bounds["reason"] == "missing_bounds"


def test_c2a_staged_path_maps_to_dotted_key_and_duration_cost() -> None:
    schema = RecipeSchema()
    spec = schema.spec_for(C2A_STAGED_DURATION)
    report = compute_knob_saturation(
        RecipePatch({C2A_STAGED_DURATION: spec.high}),
        schema,
        active_objective_metrics=("duration_h",),
    )

    row = _row(
        report,
        "campaigns.C2A_staged.stages.alkali_early_fe.duration_hr",
    )
    assert row["kind"] == "int"
    assert row["pinned"] == "high"
    assert row["has_opposing_cost"] is True
    assert row["opposing_cost_metrics"] == ["duration_h"]
    assert report["red_flag"] is False


def test_no_cost_pinned_red_flags_but_costed_duration_and_voltage_do_not() -> None:
    schema = RecipeSchema()
    no_cost = compute_knob_saturation(
        RecipePatch({TEMP_RANGE: [20.0, 950.0]}),
        schema,
        active_objective_metrics=("oxygen_kg",),
    )
    assert no_cost["red_flag"] is True

    duration_costed = compute_knob_saturation(
        RecipePatch({DURATION: schema.spec_for(DURATION).high}),
        schema,
        active_objective_metrics=("total_hours",),
    )
    duration_row = _row(duration_costed, "campaigns.C0b_p_cleanup.duration_hr")
    assert duration_row["has_opposing_cost"] is True
    assert duration_row["opposing_cost_metrics"] == ["total_hours"]
    assert duration_costed["red_flag"] is False

    c3_duration_costed = compute_knob_saturation(
        RecipePatch(
            {
                C3_DURATION_AFTER_PATHA: schema.spec_for(
                    C3_DURATION_AFTER_PATHA
                ).high
            }
        ),
        schema,
        active_objective_metrics=("total_hours",),
    )
    c3_duration_row = _row(c3_duration_costed, "campaigns.C3.duration_after_pathA_hr")
    assert c3_duration_row["has_opposing_cost"] is True
    assert c3_duration_row["opposing_cost_metrics"] == ["total_hours"]
    assert c3_duration_costed["red_flag"] is False

    voltage_no_cost = compute_knob_saturation(
        RecipePatch({VOLTAGE: schema.spec_for(VOLTAGE).high}),
        schema,
        active_objective_metrics=("oxygen_kg",),
    )
    voltage_no_cost_row = _row(voltage_no_cost, "campaigns.C5.allow_mre_voltage_cap_V")
    assert voltage_no_cost_row["pinned"] == "high"
    assert voltage_no_cost_row["has_opposing_cost"] is False
    assert voltage_no_cost["red_flag"] is True

    voltage_costed = compute_knob_saturation(
        RecipePatch({VOLTAGE: schema.spec_for(VOLTAGE).high}),
        schema,
        active_objective_metrics=(ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,),
    )
    voltage_row = _row(voltage_costed, "campaigns.C5.allow_mre_voltage_cap_V")
    assert voltage_row["has_opposing_cost"] is True
    assert voltage_row["opposing_cost_metrics"] == [ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC]
    assert voltage_costed["red_flag"] is False

    legacy_voltage_costed = compute_knob_saturation(
        RecipePatch({VOLTAGE: schema.spec_for(VOLTAGE).high}),
        schema,
        active_objective_metrics=(LEGACY_ENERGY_KWH_METRIC,),
    )
    legacy_voltage_row = _row(
        legacy_voltage_costed,
        "campaigns.C5.allow_mre_voltage_cap_V",
    )
    assert legacy_voltage_row["has_opposing_cost"] is True
    assert legacy_voltage_row["opposing_cost_metrics"] == [LEGACY_ENERGY_KWH_METRIC]
    assert legacy_voltage_costed["red_flag"] is False


def test_knob_saturation_does_not_change_evalspec_cache_key() -> None:
    schema = RecipeSchema()
    patch = RecipePatch({TEMP_RANGE: [20.0, 950.0]}).validated(schema)
    spec, _ = _build_eval_inputs(
        patch,
        "lunar_mare_low_ti",
        "stub",
        PROFILE,
        schema,
    )
    before = cache_key(spec)

    compute_knob_saturation(
        patch,
        schema,
        active_objective_metrics=("oxygen_kg",),
    )

    assert cache_key(spec) == before
    assert "knob_saturation" not in spec.__dataclass_fields__
