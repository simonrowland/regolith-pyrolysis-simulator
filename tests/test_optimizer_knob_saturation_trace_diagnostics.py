from __future__ import annotations

import pytest

from simulator.optimize.knob_saturation import compute_knob_saturation
from simulator.optimize.recipe import (
    C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR,
    C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH,
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


def test_c2a_depletion_trace_labels_requested_and_applied_values() -> None:
    requested = C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR / 2.0
    patch = RecipePatch(
        {C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: requested}
    ).validated()

    report = compute_knob_saturation(
        patch,
        RecipeSchema(),
        active_objective_metrics=("oxygen_kg",),
    )

    row = _row(report, "campaigns.C2A_staged.depletion_flux_decay_fraction")
    assert row["source"] == "patched"
    assert row["requested_value"] == pytest.approx(requested)
    assert row["applied_value"] == pytest.approx(0.0)
    assert row["value"] == pytest.approx(0.0)
    assert row["pinned"] == "low"
