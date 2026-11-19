from __future__ import annotations

import pytest

from simulator.optimize.knob_saturation import compute_knob_saturation
from simulator.optimize.recipe import (
    C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_FLOOR_PER_HR,
    C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_PATHS_BY_STAGE,
    C5_ALLOW_MRE_VOLTAGE_CAP_PATH,
    RecipePatch,
    RecipeSchema,
)


def _row(report: dict, key: str) -> dict:
    matches = [row for row in report["knobs"] if row["key"] == key]
    assert len(matches) == 1
    return matches[0]


def test_empty_patch_reports_default_at_bound_search_knobs() -> None:
    report = compute_knob_saturation(
        RecipePatch({}),
        RecipeSchema(),
        active_objective_metrics=("oxygen_kg",),
    )

    cap = _row(report, "campaigns.C5.allow_mre_voltage_cap_V")
    assert cap["source"] == "default"
    assert cap["value"] == pytest.approx(0.0)
    assert cap["pinned"] == "low"
    assert cap["has_opposing_cost"] is False
    assert report["red_flag"] is True
    assert report["pinned_count"] >= 1


def test_c2a_depletion_log_slope_trace_labels_requested_and_applied_values() -> None:
    requested = C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_FLOOR_PER_HR / 2.0
    path = C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_PATHS_BY_STAGE["alkali_early_fe"]
    patch = RecipePatch({path: requested}).validated()

    report = compute_knob_saturation(
        patch,
        RecipeSchema(),
        active_objective_metrics=("oxygen_kg",),
    )

    row = _row(
        report,
        "campaigns.C2A_staged.stages.alkali_early_fe."
        "depletion_log_slope_epsilon_per_hr",
    )
    assert row["source"] == "patched"
    assert row["requested_value"] == pytest.approx(requested)
    assert row["applied_value"] == pytest.approx(
        C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_FLOOR_PER_HR
    )
    assert row["value"] == pytest.approx(
        C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_FLOOR_PER_HR
    )
    assert row["pinned"] == "none"


def test_d452_bound_saturation_reports_max_throat_hardware() -> None:
    schema = RecipeSchema()
    path = ("condenser_geometry", "initial_throat_area_m2")
    spec = schema.spec_for(path)
    patch = RecipePatch({path: spec.high}).validated(schema)

    report = compute_knob_saturation(
        patch,
        schema,
        active_objective_metrics=("duration_h", "throughput_cost_owner_ratify_usd"),
    )
    row = _row(report, "condenser_geometry.initial_throat_area_m2")

    assert row["source"] == "patched"
    assert row["value"] == pytest.approx(spec.high)
    assert row["pinned"] == "high"


def test_d452_bound_saturation_reports_fully_open_stage_vane() -> None:
    schema = RecipeSchema()
    path = (
        "campaigns", "C2A_staged", "stages", "sio_window",
        "slider_vane_open_fraction",
    )
    spec = schema.spec_for(path)
    patch = RecipePatch({path: spec.high}).validated(schema)

    report = compute_knob_saturation(
        patch,
        schema,
        active_objective_metrics=("duration_h",),
    )
    row = _row(
        report,
        "campaigns.C2A_staged.stages.sio_window.slider_vane_open_fraction",
    )

    assert row["source"] == "patched"
    assert row["value"] == pytest.approx(spec.high)
    assert row["pinned"] == "high"
