import json
import math

import numpy as np
import pytest

from simulator.composition_projection import (
    ProjectedBulkClassification,
    classify_projected_bulk,
    format_projected_component_mol,
    projected_component_moles_per_kg,
)


def test_one_wt_percent_boundary_is_within_threshold():
    classification = classify_projected_bulk(
        {"NaCl": 1.0},
        source_sum_wt_pct=100.0,
    )

    assert classification.verdict == "within_threshold"
    assert classification.within_threshold is True
    assert classification.dropped_total_wt_pct == pytest.approx(1.0)


def test_more_than_one_wt_percent_is_over_threshold():
    classification = classify_projected_bulk(
        {"NaCl": 1.0 + 1e-9},
        source_sum_wt_pct=100.0,
    )

    assert classification.verdict == "over_threshold"
    assert classification.within_threshold is False
    assert classification.dropped_total_wt_pct > 1.0


def test_projection_normalizes_against_non_100_source_sum_and_sorts_components():
    classification = classify_projected_bulk(
        {"SiO2": 0.49, "Al2O3": 0.50},
        source_sum_wt_pct=98.0,
    )

    assert classification.verdict == "over_threshold"
    assert classification.dropped_total_wt_pct == pytest.approx(100.0 * 0.99 / 98.0)
    assert classification.dropped_component_wt_pct == (
        ("Al2O3", pytest.approx(100.0 * 0.50 / 98.0)),
        ("SiO2", pytest.approx(100.0 * 0.49 / 98.0)),
    )


@pytest.mark.parametrize(
    ("source_sum", "reason"),
    [
        (None, "source_sum_not_positive_finite"),
        ("x", "source_sum_not_positive_finite"),
        (True, "source_sum_not_positive_finite"),
        ("1.0", "source_sum_not_positive_finite"),
        (0, "source_sum_not_positive_finite"),
        (-1, "source_sum_not_positive_finite"),
        (math.nan, "source_sum_not_positive_finite"),
        (math.inf, "source_sum_not_positive_finite"),
        pytest.param(object(), "source_sum_not_positive_finite", id="object"),
    ],
)
def test_invalid_source_sum_refuses_with_typed_reason(source_sum, reason):
    classification = classify_projected_bulk({}, source_sum_wt_pct=source_sum)

    assert classification.verdict == "invalid_input"
    assert classification.invalid_reason == reason
    assert classification.within_threshold is False


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("x", "component_not_numeric:NaCl"),
        (True, "component_not_numeric:NaCl"),
        ("1.0", "component_not_numeric:NaCl"),
        (None, "component_not_numeric:NaCl"),
        (math.nan, "component_not_finite:NaCl"),
        (math.inf, "component_not_finite:NaCl"),
        (-0.1, "component_negative:NaCl"),
        pytest.param(object(), "component_not_numeric:NaCl", id="object"),
    ],
)
def test_invalid_component_refuses_with_typed_reason(value, reason):
    classification = classify_projected_bulk(
        {"NaCl": value},
        source_sum_wt_pct=100.0,
    )

    assert classification.verdict == "invalid_input"
    assert classification.invalid_reason == reason
    assert classification.within_threshold is False


def test_zero_component_is_absent_and_does_not_refuse():
    classification = classify_projected_bulk(
        {"NaCl": 0.0},
        source_sum_wt_pct=100.0,
    )

    assert classification.verdict == "within_threshold"
    assert classification.invalid_reason is None
    assert classification.components == ()


@pytest.mark.parametrize(
    "component_value",
    [np.float32(1.0), np.int64(1)],
)
def test_numpy_real_values_are_accepted(component_value):
    classification = classify_projected_bulk(
        {"NaCl": component_value},
        source_sum_wt_pct=np.float64(100.0),
    )

    assert classification.verdict == "within_threshold"
    assert classification.dropped_total_wt_pct == pytest.approx(1.0)


def test_normalization_overflow_refuses_and_diagnostics_stay_strict_json():
    classification = classify_projected_bulk(
        {"NaCl": 1e308},
        source_sum_wt_pct=1.0,
    )

    assert classification.verdict == "invalid_input"
    assert classification.invalid_reason == "normalized_value_not_finite"
    assert classification.within_threshold is False
    assert classification.components == ()
    assert classification.dropped_total_wt_pct == 0.0
    json.dumps(classification.as_diagnostics(), allow_nan=False)


@pytest.mark.parametrize(
    "components",
    [pytest.param([], id="list"), pytest.param(None, id="none")],
)
def test_non_mapping_components_refuse_with_typed_reason(components):
    classification = classify_projected_bulk(
        components,
        source_sum_wt_pct=100.0,
    )

    assert classification.verdict == "invalid_input"
    assert classification.invalid_reason == "dropped_components_not_mapping"
    assert classification.within_threshold is False


def test_projected_bulk_classification_constructor_defaults_invalid_reason():
    classification = ProjectedBulkClassification(
        (),
        0.0,
        1.0,
        100.0,
        "within_threshold",
    )

    assert classification.invalid_reason is None


def test_projected_component_moles_per_kg_has_basalt_mno_sanity_value():
    moles, reasons = projected_component_moles_per_kg(
        {"MnO": 0.2009},
        source_sum_wt_pct=100.0,
    )

    assert moles["MnO"] == pytest.approx(0.0283, abs=0.00005)
    assert f"{moles['MnO']:.4f}" == "0.0283"
    assert reasons == {}


def test_projected_component_moles_keep_unresolvable_formula_reason():
    moles, reasons = projected_component_moles_per_kg(
        {"not-a-formula": 0.2},
        source_sum_wt_pct=100.0,
    )

    assert moles == {"not-a-formula": None}
    assert reasons["not-a-formula"]


def test_projected_component_mol_formats_available_and_unavailable_values():
    available_moles, available_reasons = projected_component_moles_per_kg(
        {"MnO": 0.2009},
        source_sum_wt_pct=100.0,
    )
    available = classify_projected_bulk(
        {"MnO": 0.2009},
        source_sum_wt_pct=100.0,
        dropped_component_mol_per_kg=available_moles,
        dropped_component_mol_unavailable_reasons=available_reasons,
    )
    unavailable_moles, unavailable_reasons = projected_component_moles_per_kg(
        {"not-a-formula": 0.2},
        source_sum_wt_pct=100.0,
    )
    unavailable = classify_projected_bulk(
        {"not-a-formula": 0.2},
        source_sum_wt_pct=100.0,
        dropped_component_mol_per_kg=unavailable_moles,
        dropped_component_mol_unavailable_reasons=unavailable_reasons,
    )

    assert format_projected_component_mol(available, "MnO") == (
        "0.028320886898 mol/kg"
    )
    assert format_projected_component_mol(unavailable, "not-a-formula") == (
        "mol/kg unavailable (unknown species 'not-a-formula')"
    )


def test_diagnostics_round_trip_as_strict_json_and_expose_invalid_reason():
    classification = classify_projected_bulk(
        {"NaCl": 1.0},
        source_sum_wt_pct=100.0,
    )
    invalid = classify_projected_bulk({}, source_sum_wt_pct=None)

    json.dumps(classification.as_diagnostics(), allow_nan=False)
    json.dumps(invalid.as_diagnostics(), allow_nan=False)
    assert classification.as_diagnostics()["invalid_reason"] is None
    assert invalid.as_diagnostics()["invalid_reason"] == (
        "source_sum_not_positive_finite"
    )
