from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from simulator.battery.enums import (
    Authority,
    Engine,
    ExecutionState,
    MetricOperation,
    NoticeKind,
    Phase,
    Polymorph,
    Quantity,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.records import Species, State
from simulator.battery.migrate import REPO_ROOT, load_yaml
from simulator.battery.score import (
    SCORE_ENGINE_SET,
    ScoreContext,
    compile_residual,
    headline_rows,
    predict_with_engine,
)
from simulator.vapour_rail.catalog import compiled_catalog_for
from tests.battery import factories as F


def _catalog_pressure(formula: str, temperature_K: Decimal):
    payload = load_yaml(REPO_ROOT / "data" / "vapor_pressures.yaml")
    catalog = compiled_catalog_for(payload, emit_u0_request_rules=False)
    return catalog.evaluator_for(formula).evaluate(float(temperature_K))


def _h2o_identity(temperature_K: Decimal):
    # Ice Ih is form I. A crystal reservoir with no polymorph is identity_unknown
    # against itself, so the residual never becomes numeric.
    identity = F.psat_identity(
        "H2O", T_K=temperature_K, reservoir_phase=Phase.CR
    )
    return replace(
        identity,
        reservoir=State.of(
            Species("H2O", Phase.CR, polymorph=State.of(Polymorph.I))
        ),
    )


def _context(experiment, observation) -> ScoreContext:
    work = F.work(experiment.work_id)
    return ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={observation.observation_id: observation},
        extract_review={observation.source_id: "reviewed"},
        hostname="test",
    )


def test_catalog_evaluator_scores_supported_pressure_in_pa_and_dex() -> None:
    temperature_K = Decimal("180")
    evaluation = _catalog_pressure("H2O", temperature_K)
    pressure = Decimal(str(evaluation.pressure_pa))
    experiment = F.tabulation_experiment(
        experiment_id="catalog-pressure-exp",
        work_id="catalog-pressure-work",
        T_K=temperature_K,
    )
    identity = _h2o_identity(temperature_K)
    observation = F.observation(
        "catalog-pressure-reference",
        experiment.experiment_id,
        identity,
        pressure * 10,
        evidence=F.EvidenceClass.MEASURED_DIRECT,
        source_id="catalog-pressure-source",
    )

    prediction = predict_with_engine(
        Engine.CATALOG_EVALUATOR,
        observation,
        experiment=experiment,
    )
    residual, _candidate = compile_residual(
        observation,
        Engine.CATALOG_EVALUATOR,
        context=_context(experiment, observation),
        prediction=prediction,
    )

    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.value == pressure
    assert prediction.unit == "Pa"
    assert residual.numeric is not None
    assert residual.numeric.operation is MetricOperation.DEX
    assert residual.numeric.unit == "dimensionless"
    assert residual.numeric.value == Decimal("-1")


def test_catalog_evaluator_predicts_and_flags_temperature_extrapolation() -> None:
    temperature_K = Decimal("300")
    evaluation = _catalog_pressure("H2O", temperature_K)
    experiment = F.tabulation_experiment(
        experiment_id="catalog-extrapolation-exp",
        work_id="catalog-extrapolation-work",
        T_K=temperature_K,
    )
    observation = F.observation(
        "catalog-extrapolation-reference",
        experiment.experiment_id,
        _h2o_identity(temperature_K),
        Decimal(str(evaluation.pressure_pa)),
        evidence=F.EvidenceClass.MEASURED_DIRECT,
        source_id="catalog-extrapolation-source",
    )

    prediction = predict_with_engine(
        Engine.CATALOG_EVALUATOR,
        observation,
        experiment=experiment,
    )

    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.value is not None and prediction.value > 0
    assert prediction.authority is Authority.EXTRAPOLATED
    assert prediction.certified_band == {
        "temperature_K": (Decimal("100.0"), Decimal("273.16"))
    }
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        for notice in prediction.notices
    )


def test_catalog_evaluator_typed_refusal_for_unsupported_quantity() -> None:
    experiment = F.tabulation_experiment(
        experiment_id="catalog-unsupported-exp",
        work_id="catalog-unsupported-work",
    )
    observation = F.observation(
        "catalog-unsupported-reference",
        experiment.experiment_id,
        F.activity_identity(),
        Decimal("0.25"),
        evidence=F.EvidenceClass.MEASURED_DIRECT,
        source_id="catalog-unsupported-source",
    )

    prediction = predict_with_engine(
        Engine.CATALOG_EVALUATOR,
        observation,
        experiment=experiment,
    )

    assert prediction.value is None
    assert prediction.execution.state is ExecutionState.UNSUPPORTED
    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail == {
        "reason": "quantity_not_predicted",
        "quantity": Quantity.ACTIVITY.value,
    }


def test_catalog_evaluator_is_in_score_summary_engine_set() -> None:
    assert Engine.CATALOG_EVALUATOR in SCORE_ENGINE_SET
    rows = headline_rows((), engines=SCORE_ENGINE_SET)

    assert any(
        row["engine"] == Engine.CATALOG_EVALUATOR.value
        for row in rows
    )
