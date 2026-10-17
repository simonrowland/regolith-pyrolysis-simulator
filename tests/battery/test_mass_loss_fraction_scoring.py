from __future__ import annotations

import pytest

from simulator.battery.enums import Engine, MetricOperation, Quantity, RefusalReason
from simulator.battery.migrate import load_migrated_store
from simulator.battery.score import (
    QUANTITY_METRIC,
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


def test_mass_loss_uses_absolute_fraction_residual() -> None:
    assert QUANTITY_METRIC[Quantity.MASS_LOSS_FRACTION] is MetricOperation.ABSOLUTE
