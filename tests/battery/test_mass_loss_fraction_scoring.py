from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    Engine,
    MethodToken,
    MetricOperation,
    NoticeKind,
    Quantity,
    RefusalReason,
    ValueKind,
)
from simulator.battery.migrate import load_migrated_store
from simulator.battery.records import Located, State, Value
from simulator.battery.score import (
    QUANTITY_METRIC,
    compute_metric,
    predict_with_engine,
    quantity_token,
)


@pytest.fixture(scope="module")
def mass_loss_rows():
    _works, experiments, observations = load_migrated_store(
        sources=("kems-010-richter-2007", "kems-026-markova-1984")
    )
    richter = next(
        row
        for row in observations.values()
        if row.source_id == "kems-010-richter-2007"
        and quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and row.observation_id.endswith("R-11::T=2073.15:row=r-11:h=0d649fba8fb2")
    )
    markova = next(
        row
        for row in observations.values()
        if row.source_id == "kems-026-markova-1984"
        and quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
    )
    return richter, experiments[richter.experiment_id], markova, experiments[markova.experiment_id]


def _richter_rep5_run(experiment):
    """Add exact R-11 mass and starting area printed in rep5's Table 1 row."""
    locator = experiment.locator
    sample = replace(
        experiment.sample,
        mass_kg=Located(
            State.of(Value(ValueKind.POINT, point=Decimal("0.0001904"))), locator
        ),
        surface_area_m2=Located(
            State.of(Value(ValueKind.POINT, point=Decimal("0.0000913"))), locator
        ),
    )
    return replace(
        experiment,
        method=State.of(MethodToken.LANGMUIR_FREE_EVAPORATION),
        sample=sample,
    )


def test_pyrolysis_yield_mass_loss_uses_bulk_dispatch(mass_loss_rows) -> None:
    _richter, _richter_experiment, markova, markova_experiment = mass_loss_rows

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        markova,
        experiment=markova_experiment,
    )

    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "duration_not_stated"
    assert prediction.refusal_detail["missing_input"] == "thermal_schedule.total_duration_s"


def test_richter_rep5_row_has_numeric_residual_and_domain_notice(mass_loss_rows) -> None:
    richter, experiment, _markova, _markova_experiment = mass_loss_rows

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        richter,
        experiment=_richter_rep5_run(experiment),
    )

    assert prediction.refusal_reason is None
    assert prediction.value is not None
    assert prediction.unit == "dimensionless"
    assert richter.value.point is not None
    residual = compute_metric(
        MetricOperation.ABSOLUTE, prediction.value, richter.value.point
    )
    assert residual is not None and residual.is_finite()
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and "paper-demonstrated-window" in notice.reason
        for notice in prediction.notices
    )


def test_richter_row_without_sample_area_refuses(mass_loss_rows) -> None:
    richter, experiment, _markova, _markova_experiment = mass_loss_rows
    missing_geometry = replace(
        _richter_rep5_run(experiment),
        sample=replace(_richter_rep5_run(experiment).sample, surface_area_m2=None),
    )

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        richter,
        experiment=missing_geometry,
    )

    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "sample_geometry_not_stated"
    assert prediction.value is None


def test_mass_loss_uses_absolute_fraction_residual() -> None:
    assert QUANTITY_METRIC[Quantity.MASS_LOSS_FRACTION] is MetricOperation.ABSOLUTE
