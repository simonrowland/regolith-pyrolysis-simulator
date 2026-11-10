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
    _catalog_score_rows,
    _catalog_source_terms,
    compile_residual,
    headline_rows,
    predict_with_engine,
)
from simulator.vapour_rail.catalog import compiled_catalog_for
from tests.battery import factories as F


_CATALOG_REACTION_TERM_GOLDEN = (
    ("Na", "Na=1, NaO0.5=-1, O2=1/4"),
    ("K", "K=1, KO0.5=-1, O2=1/4"),
    ("Mg", "Mg=1, MgO=-1, O2=1/2"),
    ("Ca", "Ca=1, CaO=-1, O2=1/2"),
    ("Al", "Al=1, AlO1.5=-1, O2=3/4"),
    ("Si", "O2=1, Si=1, SiO2=-1"),
    ("Ti", "O2=1, Ti=1, TiO2=-1"),
    ("Cr", "Cr=1, CrO1.5=-1, O2=3/4"),
    ("Mn", "Mn=1, MnO=-1, O2=1/2"),
    ("TiO", "O2=1/2, TiO=1, TiO2=-1"),
    ("AlO", "AlO=1, AlO1.5=-1, O2=1/4"),
    ("Al2O", "Al2O=1, AlO1.5=-2, O2=1"),
    ("Al2", "Al2=1, AlO1.5=-2, O2=3/2"),
    ("Al2O2", "Al2O2=1, AlO1.5=-2, O2=1/2"),
    ("Al2O3_gas", "Al2O3=1, AlO1.5=-2"),
    ("AlO2", "AlO1.5=-1, AlO2=1, O2=-1/4"),
    ("Ca2", "Ca2=1, CaO=-2, O2=1"),
    ("CrO", "CrO=1, CrO1.5=-1, O2=1/4"),
    ("SiO", "O2=1/2, SiO=1, SiO2=-1"),
    ("CrO2", "CrO1.5=-1, CrO2=1, O2=-1/4"),
    ("CrO3", "CrO1.5=-1, CrO3=1, O2=-3/4"),
    ("PO", "O2=3/2, P2O5=-1, PO=2"),
    ("PO2", "O2=1/2, P2O5=-1, PO2=2"),
    ("P4O6", "O2=2, P2O5=-2, P4O6=1"),
    ("P4O10", "P2O5=-2, P4O10=1"),
    ("P2", "O2=5/2, P2=1, P2O5=-1"),
    ("P4", "O2=5, P2O5=-2, P4=1"),
    ("K2", "K2=1, K2O=-1, O2=1/2"),
    ("Mg2", "Mg2=1, MgO=-2, O2=1"),
    ("Na2", "Na2=1, Na2O=-1, O2=1/2"),
    ("Si2", "O2=2, Si2=1, SiO2=-2"),
    ("Si3", "O2=3, Si3=1, SiO2=-3"),
)


def test_catalog_reaction_terms_match_reviewed_row_goldens() -> None:
    rows, error = _catalog_score_rows()
    assert error is None
    actual = []
    for row in rows:
        reaction = row.source_reaction
        if (
            reaction is None
            or reaction.get("activity_input") is None
            or row.species.evaluator is None
        ):
            continue
        terms = _catalog_source_terms(reaction)
        if terms is None:
            continue
        encoded = ", ".join(
            f"{formula}={coefficient}"
            for formula, coefficient in sorted(terms.items())
        )
        actual.append((row.species_id, encoded))
    assert tuple(actual) == _CATALOG_REACTION_TERM_GOLDEN


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
