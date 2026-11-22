"""Contract tests for eligible relative-channel QMS point residuals."""

import ast
from dataclasses import replace
from decimal import Decimal
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
        run_id="source-run" if source else "model-run",
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
        model_run_id="model-run",
        model_oxygen_input_observation_ids=(),
    )
    return observed, model, anchor, clock, evidence


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
    read, or a lookup hidden inside the printed-control predicate splits
    the rule the reviews closed.
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
    assert score_mixed == []

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
                replace(evidence, target_isolated_or_interferences_corrected=None),
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


def test_nonpositive_or_censored_datum_is_typed_unavailable_per_point():
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
