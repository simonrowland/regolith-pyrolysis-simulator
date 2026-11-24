"""Contract tests for eligible relative-channel QMS point residuals."""

import ast
from dataclasses import replace
from decimal import Decimal, DecimalException
import math
from pathlib import Path

import pytest

from simulator.battery.enums import (
    Quantity,
    RelativeSeriesUnavailableReason,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.records import (
    RelativeSeriesAnchor,
    RelativeSeriesClock,
    RelativeSeriesEvidence,
    RelativeSeriesPoint,
    Uncertainty,
    Value,
)
from simulator.battery.qms_policy import QMS_POLICY_REGISTRY
from simulator.battery import score_metrics
from simulator.battery.score_metrics import (
    relative_channel_point_residuals,
)
from tests.battery import factories as F


def _point(
    coordinate: str,
    value: str,
    *,
    observable: str,
    source: bool,
    boundary: str = "cell-exit",
    coordinate_unit: str = "s",
    uncertainty: Uncertainty | None = None,
) -> RelativeSeriesPoint:
    return RelativeSeriesPoint(
        run_id="physical-run",
        coordinate=Decimal(coordinate),
        value=Value(ValueKind.POINT, point=Decimal(value)),
        observable=observable,
        boundary=boundary,
        coordinate_unit=coordinate_unit,
        locator=F.loc(page=3) if source else None,
        uncertainty=uncertainty if source else None,
    )


def _inputs():
    observed = (
        _point(
            "0", "2", observable="O2+", source=True,
            uncertainty=Uncertainty(
                UncertaintyKind.PRINTED,
                verbatim="± 1%",
                locator=F.loc(page=3),
            ),
        ),
        _point("1", "4", observable="O2+", source=True),
        _point("2", "8", observable="O2+", source=True),
    )
    model = (
        _point("10", "5", observable="O2", source=False),
        _point("11", "10", observable="O2", source=False),
        _point("12", "20", observable="O2", source=False),
    )
    anchor = RelativeSeriesAnchor(
        source_coordinate=Decimal("0"),
        model_coordinate=Decimal("10"),
        source_event="acquisition-start",
        model_event="trajectory-start",
        physical_event="heater-enable",
    )
    clock = RelativeSeriesClock(
        source_event="acquisition-start",
        model_event="trajectory-start",
        physical_event="heater-enable",
        mapping_supported=True,
        locator=F.loc(page=2),
    )
    evidence = RelativeSeriesEvidence(
        quantity=Quantity.ION_INTENSITY,
        model_quantity=Quantity.P_PARTIAL,
        channel="O2+",
        target_species="O2",
        boundary="cell-exit",
        target_isolated_or_interferences_corrected=True,
        background_treatment_supported=True,
        response_proportional_over_interval=True,
        locator=F.loc(page=1),
        oxygen_independent=True,
        model_run_id="physical-run",
        reference_run_id="physical-run",
        model_oxygen_input_observation_ids=(),
    )
    return observed, model, anchor, clock, evidence


def _assumption(result, policy_id):
    return next(item for item in result.assumptions if item.policy_id == policy_id)


def test_constant_current_multiplier_cancels_after_eligible_anchor_normalization():
    observed, model, anchor, clock, evidence = _inputs()
    model = (
        model[0],
        replace(model[1], value=Value(ValueKind.POINT, point=Decimal("15"))),
        replace(model[2], value=Value(ValueKind.POINT, point=Decimal("45"))),
    )
    original = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )
    scaled = relative_channel_point_residuals(
        tuple(
            replace(
                point,
                value=Value(
                    ValueKind.POINT,
                    point=point.value.point * Decimal("7"),
                ),
            )
            for point in observed
        ),
        model,
        anchor,
        clock,
        evidence,
    )
    assert original.unavailable_reason is None
    assert scaled.unavailable_reason is None
    assert tuple(row.residual_dex for row in original.points) == tuple(
        row.residual_dex for row in scaled.points
    )
    assert tuple(float(row.residual_dex) for row in original.points) == pytest.approx(
        (0.0, math.log10(1.5), math.log10(2.25))
    )
    assert original.points[0].uncertainty == observed[0].uncertainty


def test_matching_normalized_traces_have_zero_residuals():
    observed, model, anchor, clock, evidence = _inputs()
    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )
    assert result.unavailable_reason is None
    assert tuple(row.residual_dex for row in result.points) == (
        Decimal(0), Decimal(0), Decimal(0)
    )


def test_model_input_naming_the_source_trace_is_self_agreement():
    observed, model, anchor, clock, evidence = _inputs()
    observed = tuple(
        replace(point, observation_id="source-o2-trace") for point in observed
    )
    evidence = replace(
        evidence,
        model_oxygen_input_observation_ids=("source-o2-trace",),
    )

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.OXYGEN_SELF_AGREEMENT
    )
    assert result.points == ()


def test_model_series_from_mixed_runs_has_typed_unavailability():
    observed, model, anchor, clock, evidence = _inputs()
    model = (*model[:-1], replace(model[-1], run_id="other-model-run"))

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.MIXED_MODEL_RUNS
    )
    assert result.points == ()


def test_single_sio_model_run_keeps_zero_anchor_residuals():
    observed, model, anchor, clock, evidence = _inputs()
    observed = tuple(replace(point, observable="SiO+") for point in observed)
    model = tuple(replace(point, observable="SiO") for point in model)
    evidence = replace(evidence, target_species="SiO", channel="SiO+")

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is None
    assert tuple(point.residual_dex for point in result.points) == (0, 0, 0)


def test_tied_physical_run_scores_and_each_broken_tie_refuses():
    observed, model, anchor, clock, evidence = _inputs()
    scored = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )
    assert scored.unavailable_reason is None
    assert tuple(point.residual_dex for point in scored.points) == (
        Decimal(0),
        Decimal(0),
        Decimal(0),
    )

    cases = (
        relative_channel_point_residuals(
            tuple(replace(point, run_id="other-observed") for point in observed),
            model,
            anchor,
            clock,
            evidence,
        ),
        relative_channel_point_residuals(
            observed,
            model,
            anchor,
            clock,
            replace(evidence, reference_run_id="other-reference"),
        ),
        relative_channel_point_residuals(
            observed,
            tuple(replace(point, run_id="other-model") for point in model),
            anchor,
            clock,
            replace(evidence, model_run_id="other-model"),
        ),
        relative_channel_point_residuals(
            observed,
            model,
            anchor,
            clock,
            replace(evidence, reference_run_id=None),
        ),
        relative_channel_point_residuals(
            observed,
            model,
            anchor,
            clock,
            replace(evidence, model_run_id=None),
        ),
    )
    for result in cases:
        assert result.unavailable_reason is (
            RelativeSeriesUnavailableReason.AMBIGUOUS_RUN_IDENTITY
        )
        assert result.points == ()


def test_tied_ids_with_one_unrelated_model_trajectory_refuse():
    observed, model, anchor, clock, evidence = _inputs()
    model = tuple(replace(point, run_id="unrelated-trajectory") for point in model)
    assert evidence.reference_run_id == evidence.model_run_id
    assert {point.run_id for point in observed} == {evidence.model_run_id}
    assert {point.run_id for point in model} == {"unrelated-trajectory"}

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.AMBIGUOUS_RUN_IDENTITY
    )
    assert result.points == ()


def test_mixed_sio_model_runs_are_typed_unavailable():
    observed, model, anchor, clock, evidence = _inputs()
    observed = tuple(replace(point, observable="SiO+") for point in observed)
    model = tuple(replace(point, observable="SiO") for point in model)
    model = (*model[:-1], replace(model[-1], run_id="other-model-run"))
    evidence = replace(evidence, target_species="SiO", channel="SiO+")

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.MIXED_MODEL_RUNS
    )
    assert result.points == ()


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    found = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(found) == 1
    return found[0]


def _called_names(function: ast.FunctionDef) -> list[str]:
    names: list[str] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.append(func.id)
        elif isinstance(func, ast.Attribute):
            names.append(func.attr)
    return names


def _definitions(*trees: ast.AST) -> dict[str, ast.FunctionDef]:
    found: dict[str, ast.FunctionDef] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                found[node.name] = node
    return found


def _lookup_count(name: str, definitions: dict[str, ast.FunctionDef], seen: set[str]) -> int:
    if name in seen or name not in definitions:
        return 0
    seen.add(name)
    called = _called_names(definitions[name])
    return called.count("oxygen_pressure_point") + sum(
        _lookup_count(callee, definitions, seen) for callee in called
    )


def test_qms_channel_rules_have_one_owner():
    """One model-run refusal, and one read of the printed control point.

    A second MIXED_MODEL_RUNS return, a second direct oxygen_pressure_point
    read, a lookup hidden inside the printed-control predicate, or a call
    from the adapter into the pure comparator splits the rule the reviews
    closed.
    """

    root = Path(relative_channel_point_residuals.__code__.co_filename).parent
    metrics_tree = ast.parse((root / "score_metrics.py").read_text(encoding="utf-8"))
    score_tree = ast.parse((root / "score.py").read_text(encoding="utf-8"))
    ladder_tree = ast.parse((root / "fo2_ladder.py").read_text(encoding="utf-8"))
    oxygen_tree = ast.parse((root / "source_oxygen.py").read_text(encoding="utf-8"))
    mixed_owners = [
        node.name
        for node in ast.walk(metrics_tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(child, ast.Attribute) and child.attr == "MIXED_MODEL_RUNS"
            for child in ast.walk(node)
        )
    ]
    score_mixed = [
        node.name
        for node in ast.walk(score_tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(child, ast.Attribute) and child.attr == "MIXED_MODEL_RUNS"
            for child in ast.walk(node)
        )
    ]
    assert mixed_owners == ["_relative_series_model_oxygen_unavailable_reason"]
    assert score_mixed == ["_score_relative_qms_series"]

    helper = _function(
        metrics_tree, "_relative_series_model_oxygen_unavailable_reason"
    )
    mixed_line = next(
        node.lineno
        for node in ast.walk(helper)
        if isinstance(node, ast.Attribute) and node.attr == "MIXED_MODEL_RUNS"
    )
    species_line = next(
        node.lineno
        for node in ast.walk(helper)
        if isinstance(node, ast.Attribute) and node.attr == "target_species"
    )
    assert mixed_line < species_line

    walker = _function(score_tree, "_model_oxygen_control_lineage")
    scorer = _function(score_tree, "_score_relative_qms_series")
    scorer_calls = _called_names(scorer)
    assert _called_names(walker).count("oxygen_pressure_point") == 0
    assert scorer_calls.count("oxygen_pressure_point") == 1
    assert "printed_fo2_from_point" in scorer_calls
    assert "relative_channel_point_residuals" not in scorer_calls
    assert "_qms_store_producer_unavailable" in scorer_calls
    assert "_has_printed_fo2" not in scorer_calls
    assert "has_printed_fo2" not in scorer_calls
    definitions = _definitions(score_tree, ladder_tree, oxygen_tree)
    assert _lookup_count("printed_fo2_from_point", definitions, set()) == 0


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            lambda observed, model, anchor, clock, evidence: (
                observed,
                model,
                replace(anchor, source_coordinate=Decimal("99")),
                clock,
                evidence,
            ),
            RelativeSeriesUnavailableReason.SOURCE_ANCHOR_MISSING,
        ),
        (
            lambda observed, model, anchor, clock, evidence: (
                observed,
                model,
                replace(anchor, model_coordinate=Decimal("99")),
                clock,
                evidence,
            ),
            RelativeSeriesUnavailableReason.MODEL_ANCHOR_MISSING,
        ),
        (
            lambda observed, model, anchor, clock, evidence: (
                (observed[0], replace(observed[1], observable="O+"), *observed[2:]),
                model,
                anchor,
                clock,
                evidence,
            ),
            RelativeSeriesUnavailableReason.MIXED_CHANNELS,
        ),
        (
            lambda observed, model, anchor, clock, evidence: (
                observed,
                model,
                anchor,
                replace(clock, source_event="sample-insertion"),
                evidence,
            ),
            RelativeSeriesUnavailableReason.CLOCK_EVENT_MISMATCH,
        ),
        (
            lambda observed, model, anchor, clock, evidence: (
                observed,
                model,
                anchor,
                clock,
                replace(evidence, background_treatment_supported=False),
            ),
            RelativeSeriesUnavailableReason.BACKGROUND_UNRESOLVED,
        ),
        (
            lambda observed, model, anchor, clock, evidence: (
                observed,
                model,
                anchor,
                clock,
                replace(evidence, target_isolated_or_interferences_corrected=False),
            ),
            RelativeSeriesUnavailableReason.INTERFERENCE_UNRESOLVED,
        ),
        (
            lambda observed, model, anchor, clock, evidence: (
                observed,
                model,
                anchor,
                clock,
                replace(evidence, response_proportional_over_interval=False),
            ),
            RelativeSeriesUnavailableReason.PROPORTIONAL_TRACKING_UNESTABLISHED,
        ),
    ],
    ids=(
        "missing-source-anchor",
        "missing-model-anchor",
        "mixed-channels",
        "changed-clock-zero",
        "background",
        "interference",
        "response-drift",
    ),
)
def test_unproven_relative_series_is_typed_unavailable(change, reason):
    observed, model, anchor, clock, evidence = change(*_inputs())
    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )
    assert result.unavailable_reason is reason
    assert result.points == ()


@pytest.mark.parametrize(
    ("anchor_change", "reason"),
    (
        (
            lambda anchor: replace(anchor, source_coordinate=Decimal("99")),
            RelativeSeriesUnavailableReason.SOURCE_ANCHOR_MISSING,
        ),
        (
            lambda anchor: replace(anchor, model_coordinate=Decimal("99")),
            RelativeSeriesUnavailableReason.MODEL_ANCHOR_MISSING,
        ),
    ),
)
def test_probe_explicit_clock_anchor_mismatch_stays_category_a(
    anchor_change, reason, monkeypatch
):
    observed, model, anchor, clock, evidence = _inputs()
    anchor = anchor_change(anchor)

    def anchor_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is reason

    with monkeypatch.context() as patch:
        patch.setattr(
            score_metrics,
            "_relative_series_explicit_anchor_missing_reason",
            lambda *_args: None,
        )
        with pytest.raises((AssertionError, AttributeError)):
            anchor_contract()


def test_unmatched_observed_point_is_unavailable_without_interpolation():
    observed, model, anchor, clock, evidence = _inputs()
    observed = (*observed, _point("3", "16", observable="O2+", source=True))
    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )
    assert result.unavailable_reason is None
    assert result.points[-1].residual_dex is None
    assert result.points[-1].unavailable_reason is (
        RelativeSeriesUnavailableReason.MODEL_POINT_MISSING
    )


def test_non_point_source_signal_and_zero_stay_invalid():
    observed, model, anchor, clock, evidence = _inputs()
    observed = (
        observed[0],
        replace(
            observed[1],
            value=Value(
                ValueKind.BOUND,
                bound_operator="<",
                bound_value=Decimal("5"),
            ),
        ),
        replace(
            observed[2], value=Value(ValueKind.POINT, point=Decimal("0"))
        ),
    )
    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )
    assert result.unavailable_reason is None
    assert result.points[1].residual_dex is None
    assert result.points[1].unavailable_reason is (
        RelativeSeriesUnavailableReason.SOURCE_VALUE_NOT_POSITIVE_POINT
    )
    assert result.points[2].unavailable_reason is (
        RelativeSeriesUnavailableReason.SOURCE_VALUE_NOT_POSITIVE_POINT
    )


def test_relative_channel_contract_rejects_flux_or_mixed_source_runs():
    observed, model, anchor, clock, evidence = _inputs()
    wrong_model_quantity = relative_channel_point_residuals(
        observed,
        model,
        anchor,
        clock,
        replace(evidence, model_quantity=Quantity.EVAPORATION_RATE),
    )
    assert wrong_model_quantity.unavailable_reason is (
        RelativeSeriesUnavailableReason.QUANTITY_UNSUPPORTED
    )

    mixed_runs = relative_channel_point_residuals(
        (observed[0], replace(observed[1], run_id="other-source-run"), observed[2]),
        model,
        anchor,
        clock,
        evidence,
    )
    assert mixed_runs.unavailable_reason is (
        RelativeSeriesUnavailableReason.MIXED_SOURCE_RUNS
    )


def test_dp013_converts_clock_units_and_anchor_coordinates_exactly():
    observed, model, anchor, clock, evidence = _inputs()
    observed = tuple(replace(point, coordinate_unit="min") for point in observed)

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is None
    assert result.points[0].model_coordinate == Decimal("10")
    assert result.points[1].model_coordinate == Decimal("70")
    assert result.points[1].unavailable_reason is (
        RelativeSeriesUnavailableReason.MODEL_POINT_MISSING
    )


def test_unmapped_clock_uses_first_shared_positive_sample_without_fitting():
    observed, model, anchor, clock, evidence = _inputs()
    observed = tuple(replace(point, coordinate_unit="min") for point in observed)
    model = tuple(
        replace(
            point,
            coordinate=observed[index].coordinate * Decimal("60"),
            value=(
                Value(ValueKind.POINT, point=Decimal("0"))
                if index == 0
                else point.value
            ),
        )
        for index, point in enumerate(model)
    )
    clock = replace(
        clock,
        mapping_supported=False,
        source_event="clock-mapping-not-published",
        locator=None,
    )
    evidence = replace(
        evidence,
        target_isolated_or_interferences_corrected=None,
        background_treatment_supported=None,
        response_proportional_over_interval=None,
    )

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is None
    assert tuple(point.model_coordinate for point in result.points) == (
        Decimal("0"), Decimal("60"), Decimal("120")
    )
    assert result.points[0].unavailable_reason is (
        RelativeSeriesUnavailableReason.MODEL_VALUE_NOT_POSITIVE_POINT
    )
    assert result.points[1].residual_dex == Decimal("0")
    clock_flag = _assumption(result, "qms_clock_anchor_assumed")
    assert clock_flag.value["index_base"] == 0
    assert clock_flag.value["source_sample"] == {
        "index": 1,
        "time": "1",
        "unit": "min",
        "observation_id": None,
    }
    assert clock_flag.value["model_sample"] == {
        "index": 1,
        "time": "60",
        "unit": "s",
        "observation_id": None,
    }
    assert clock_flag.value["noise_floor"] == (
        "not supplied; raw values > 0 select the anchor"
    )
    assert "no fitted shift or scale" in clock_flag.reason
    signal_flag = _assumption(result, "qms_signal_response_assumed")
    assert set(signal_flag.value["unverified"]) == {
        "isolation",
        "background",
        "proportionality",
    }
    assert set(QMS_POLICY_REGISTRY) == {
        "qms_boundary_assumed",
        "qms_clock_anchor_assumed",
        "qms_signal_response_assumed",
        "qms_provenance_assumed",
    }
    assert QMS_POLICY_REGISTRY["qms_clock_anchor_assumed"]["status"] == "ratified"
    assert QMS_POLICY_REGISTRY["qms_signal_response_assumed"]["status"] == "ratified"
    assert QMS_POLICY_REGISTRY["qms_provenance_assumed"]["status"] == "ratified"
    assert QMS_POLICY_REGISTRY["qms_boundary_assumed"]["status"] == "proposed"


def test_located_parent_covers_missing_child_locators():
    observed, model, anchor, clock, evidence = _inputs()
    observed = tuple(replace(point, locator=None) for point in observed)
    clock = replace(clock, locator=None)

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is None
    provenance_flag = _assumption(result, "qms_provenance_assumed")
    assert provenance_flag.value == {
        "source_point_indices": (0, 1, 2),
        "clock": True,
        "parent_locator": evidence.locator,
    }
    assert "located parent panel" in provenance_flag.reason


def test_unknown_boundary_is_assumed_and_reported_on_comparison_residual():
    observed, model, anchor, clock, evidence = _inputs()
    observed = (replace(observed[0], boundary="unknown"), *observed[1:])
    model = (*model[:-1], replace(model[-1], boundary="unreported"))

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is None
    flag = _assumption(result, "qms_boundary_assumed")
    assert flag.value["boundary"] == "cell-exit"
    assert flag.value["comparison_boundary"] == "cell-exit"
    assert flag.value["unknown_samples"] == (
        ("source", 0, "unknown"),
        ("model", 2, "unreported"),
    )
    assert "one shared known boundary" in flag.reason


def test_unknown_comparison_boundary_uses_one_shared_known_point_boundary():
    observed, model, anchor, clock, evidence = _inputs()
    observed = (replace(observed[0], boundary="unknown"), *observed[1:])
    evidence = replace(evidence, boundary="unreported")

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is None
    flag = _assumption(result, "qms_boundary_assumed")
    assert flag.value["boundary"] == "cell-exit"
    assert flag.value["comparison_boundary"] == "unreported"
    assert flag.value["unknown_samples"] == (("source", 0, "unknown"),)


def test_unknown_comparison_boundary_inherits_shared_known_point_boundary():
    observed, model, anchor, clock, evidence = _inputs()
    evidence = replace(evidence, boundary="unknown")

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is None
    flag = _assumption(result, "qms_boundary_assumed")
    assert flag.value == {
        "boundary": "cell-exit",
        "comparison_boundary": "unknown",
        "unknown_samples": (),
    }


def test_all_unreported_boundaries_use_shared_unreported_assumption():
    observed, model, anchor, clock, evidence = _inputs()
    observed = tuple(replace(point, boundary="unknown") for point in observed)
    model = tuple(replace(point, boundary="unreported") for point in model)
    evidence = replace(evidence, boundary="unreported")

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is None
    flag = _assumption(result, "qms_boundary_assumed")
    assert flag.value["boundary"] == "shared_unreported_boundary"
    assert flag.value["comparison_boundary"] == "unreported"
    assert flag.reason == (
        "Unknown or unreported boundaries use the one shared known boundary; "
        "if none is known, use a shared-unreported boundary."
    )


def test_known_boundary_mismatch_remains_category_a():
    observed, model, anchor, clock, evidence = _inputs()
    model = tuple(replace(point, boundary="chamber") for point in model)

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.BOUNDARY_MISMATCH
    )


def test_unknown_point_cannot_hide_conflicting_known_comparison_boundary():
    observed, model, anchor, clock, evidence = _inputs()
    observed = (replace(observed[0], boundary="unknown"), *observed[1:])
    model = (*model[:-1], replace(model[-1], boundary="chamber"))

    result = relative_channel_point_residuals(
        observed, model, anchor, clock, evidence
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.BOUNDARY_MISMATCH
    )


def test_probe_dp013_unit_conversion_is_required(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    observed = tuple(replace(point, coordinate_unit="min") for point in observed)

    def conversion_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is None
        assert result.points[1].model_coordinate == Decimal("70")

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "_relative_series_time_coordinates", lambda *args: None)
        with pytest.raises(AssertionError):
            conversion_contract()


def test_probe_assumed_clock_anchor_is_required(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    clock = replace(clock, mapping_supported=None)
    model = tuple(
        replace(
            point,
            coordinate=observed[index].coordinate,
            value=(
                Value(ValueKind.POINT, point=Decimal("0"))
                if index == 0
                else point.value
            ),
        )
        for index, point in enumerate(model)
    )

    def anchor_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is None
        assert tuple(point.model_coordinate for point in result.points) == (
            Decimal("0"), Decimal("1"), Decimal("2")
        )
        assert result.points[0].unavailable_reason is (
            RelativeSeriesUnavailableReason.MODEL_VALUE_NOT_POSITIVE_POINT
        )
        assert result.points[1].residual_dex == Decimal("0")

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "_relative_series_default_anchor", lambda *args: (None, None))
        with pytest.raises(AssertionError):
            anchor_contract()


def test_probe_assumed_signal_response_is_required(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    evidence = replace(
        evidence,
        target_isolated_or_interferences_corrected=None,
        background_treatment_supported=None,
        response_proportional_over_interval=None,
    )

    def signal_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is None

    with monkeypatch.context() as patch:
        patch.setattr(
            score_metrics,
            "_relative_series_signal_response_reason",
            lambda _evidence: RelativeSeriesUnavailableReason.BACKGROUND_UNRESOLVED,
        )
        with pytest.raises(AssertionError):
            signal_contract()


def test_probe_located_parent_covers_missing_child_locator(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    evidence = replace(evidence, locator=F.loc(page=1))
    observed = tuple(replace(point, locator=None) for point in observed)

    def provenance_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is None

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "_relative_series_source_located", lambda _evidence: False)
        with pytest.raises(AssertionError):
            provenance_contract()


def test_probe_missing_parent_locator_remains_category_a(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    evidence = replace(evidence, locator=None)

    def source_location_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is (
            RelativeSeriesUnavailableReason.SOURCE_EVIDENCE_UNLOCATED
        )

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "_relative_series_source_located", lambda _evidence: True)
        with pytest.raises(AssertionError):
            source_location_contract()


def test_probe_positive_oxygen_self_agreement_guard(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    evidence = replace(evidence, oxygen_independent=False)

    def self_agreement_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is (
            RelativeSeriesUnavailableReason.OXYGEN_SELF_AGREEMENT
        )

    with monkeypatch.context() as patch:
        patch.setattr(
            score_metrics,
            "_relative_series_model_oxygen_unavailable_reason",
            lambda *_args, **_kwargs: None,
        )
        with pytest.raises(AssertionError):
            self_agreement_contract()


def test_probe_mixed_model_run_guard(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    model = (*model[:-1], replace(model[-1], run_id="other-model-run"))

    def mixed_run_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is (
            RelativeSeriesUnavailableReason.MIXED_MODEL_RUNS
        )

    with monkeypatch.context() as patch:
        patch.setattr(
            score_metrics,
            "_relative_series_model_oxygen_unavailable_reason",
            lambda *_args, **_kwargs: None,
        )
        with pytest.raises(AssertionError):
            mixed_run_contract()


def test_probe_mixed_source_run_guard(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    observed = (*observed[:-1], replace(observed[-1], run_id="other-source-run"))

    def mixed_run_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is (
            RelativeSeriesUnavailableReason.MIXED_SOURCE_RUNS
        )

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "_relative_series_source_run_mixed", lambda _points: False)
        with pytest.raises(AssertionError):
            mixed_run_contract()


def test_probe_conflicting_run_guard(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    evidence = replace(evidence, model_run_id="another-model-run")

    def run_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is (
            RelativeSeriesUnavailableReason.AMBIGUOUS_RUN_IDENTITY
        )

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "_untied_physical_run", lambda *_args: False)
        with pytest.raises(AssertionError):
            run_contract()


def test_probe_conflicting_boundary_guard(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    model = tuple(replace(point, boundary="chamber") for point in model)

    def boundary_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is (
            RelativeSeriesUnavailableReason.BOUNDARY_MISMATCH
        )

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "_relative_series_boundaries_match", lambda *_args: True)
        with pytest.raises(AssertionError):
            boundary_contract()


@pytest.mark.parametrize(
    ("policy_id", "change"),
    (
        (
            "qms_clock_anchor_assumed",
            lambda observed, model, anchor, clock, evidence: (
                observed,
                tuple(
                    replace(point, coordinate=observed[index].coordinate)
                    for index, point in enumerate(model)
                ),
                anchor,
                replace(clock, mapping_supported=False),
                evidence,
            ),
        ),
        (
            "qms_signal_response_assumed",
            lambda observed, model, anchor, clock, evidence: (
                observed,
                model,
                anchor,
                clock,
                replace(
                    evidence,
                    target_isolated_or_interferences_corrected=None,
                    background_treatment_supported=None,
                    response_proportional_over_interval=None,
                ),
            ),
        ),
        (
            "qms_provenance_assumed",
            lambda observed, model, anchor, clock, evidence: (
                tuple(replace(point, locator=None) for point in observed),
                model,
                anchor,
                replace(clock, locator=None),
                evidence,
            ),
        ),
        (
            "qms_boundary_assumed",
            lambda observed, model, anchor, clock, evidence: (
                (replace(observed[0], boundary="unknown"), *observed[1:]),
                model,
                anchor,
                clock,
                evidence,
            ),
        ),
    ),
)
def test_probe_assumption_flag_is_required(policy_id, change, monkeypatch):
    args = change(*_inputs())
    original = score_metrics.relative_channel_point_residuals

    def assumption_contract():
        result = score_metrics.relative_channel_point_residuals(*args)
        assert policy_id in {item.policy_id for item in result.assumptions}

    def omit_flag(*call_args):
        result = original(*call_args)
        return replace(
            result,
            assumptions=tuple(
                item for item in result.assumptions if item.policy_id != policy_id
            ),
        )

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "relative_channel_point_residuals", omit_flag)
        with pytest.raises(AssertionError):
            assumption_contract()


def test_probe_nonpositive_anchor_guard(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    observed = (
        replace(observed[0], value=Value(ValueKind.POINT, point=Decimal("0"))),
        *observed[1:],
    )

    def anchor_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.unavailable_reason is (
            RelativeSeriesUnavailableReason.SOURCE_ANCHOR_NOT_POSITIVE
        )

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "_positive_anchor_value", lambda _point: True)
        with pytest.raises((AssertionError, DecimalException)):
            anchor_contract()


def test_probe_nonpositive_model_sample_guard(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    model = (
        model[0],
        replace(model[1], value=Value(ValueKind.POINT, point=Decimal("0"))),
        model[2],
    )

    def sample_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.points[1].unavailable_reason is (
            RelativeSeriesUnavailableReason.MODEL_VALUE_NOT_POSITIVE_POINT
        )

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "_positive_anchor_value", lambda _point: True)
        with pytest.raises(AssertionError):
            sample_contract()


def test_probe_nonpositive_source_sample_guard(monkeypatch):
    observed, model, anchor, clock, evidence = _inputs()
    observed = (
        observed[0],
        replace(observed[1], value=Value(ValueKind.POINT, point=Decimal("0"))),
        observed[2],
    )

    def sample_contract():
        result = relative_channel_point_residuals(
            observed, model, anchor, clock, evidence
        )
        assert result.points[1].unavailable_reason is (
            RelativeSeriesUnavailableReason.SOURCE_VALUE_NOT_POSITIVE_POINT
        )

    with monkeypatch.context() as patch:
        patch.setattr(score_metrics, "_relative_positive_value", lambda value: value.point)
        patch.setattr(score_metrics, "compute_metric", lambda *_args: Decimal(0))
        with pytest.raises(AssertionError):
            sample_contract()
