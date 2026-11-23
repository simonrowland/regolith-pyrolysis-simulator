"""Golden pin of the predict-time capability ladder and the thermo table.

The expected sets are the membership at the pin, not a second copy of the
ladder. A later move of the thermo table has to keep both answers.
"""

from __future__ import annotations

from simulator.battery.compilation_tier import _engine_thermo_quantities
from simulator.battery.enums import Engine, EvidenceClass, Quantity
from simulator.battery.score import _engine_lacks_quantity_capability

# Thermochemistry slice each engine declared before the table moved.
_THERMO_PIN: dict[Engine, frozenset[Quantity]] = {
    Engine.INTERNAL_ANALYTICAL: frozenset(
        {
            Quantity.DELTA_FG,
            Quantity.LOG10_KF,
            Quantity.ENTHALPY_OF_FUSION,
            Quantity.ENTROPY_OF_FUSION,
        }
    ),
    Engine.VAPOROCK: frozenset(
        {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}
    ),
    Engine.THERMOENGINE: frozenset(
        {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}
    ),
    Engine.MAGEMIN: frozenset(
        {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}
    ),
    Engine.NASA_CEA_9: frozenset(
        {
            Quantity.CP,
            Quantity.S,
            Quantity.H_MINUS_H298,
            Quantity.DELTA_FH,
            Quantity.DELTA_FG,
        }
    ),
}

# Quantities the predict-time ladder treats as callable on every engine,
# including engines that later refuse inside predict with no numeric value.
_LACKS_FALSE_EVERY_ENGINE = frozenset(
    {
        Quantity.MASS_LOSS_FRACTION,
        Quantity.FE3_FE2_RATIO,
        Quantity.VALENCE_RATIO,
        Quantity.WALL_DEPOSIT_AREAL_RATE,
        Quantity.TRANSITION_TEMPERATURE,
        Quantity.SULFIDE_CAPACITY,
        Quantity.CHLORIDE_CAPACITY,
        Quantity.DISTRIBUTION_RATIO,
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
        Quantity.P_REFERENCE,
        Quantity.ACTIVITY,
        Quantity.ACTIVITY_COEFFICIENT,
        Quantity.ENTHALPY_OF_FUSION,
        Quantity.ENTROPY_OF_FUSION,
    }
)


def test_engine_thermo_quantities_match_the_pin() -> None:
    from simulator.battery.engine_capability import engine_thermo_quantities

    for engine in Engine:
        pinned = _THERMO_PIN.get(engine, frozenset())
        assert _engine_thermo_quantities(engine) == pinned
        assert engine_thermo_quantities(engine) == pinned


def test_capability_table_names_the_score_engines() -> None:
    from simulator.battery.engine_capability import ENGINE_QUANTITIES
    from simulator.battery.score import SCORE_ENGINE_SET

    assert set(ENGINE_QUANTITIES) == set(SCORE_ENGINE_SET)


def test_capability_census_uses_the_dispatched_phase_route() -> None:
    from simulator.battery.engine_capability import engine_covers_scoring_route

    assert engine_covers_scoring_route(
        Engine.ALPHAMELTS, Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    )
    assert engine_covers_scoring_route(
        Engine.MAGEMIN, Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    )
    assert not engine_covers_scoring_route(
        Engine.VAPOROCK, Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    )
    assert not engine_covers_scoring_route(
        Engine.INTERNAL_ANALYTICAL, Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    )
    assert not engine_covers_scoring_route(
        Engine.ALPHAMELTS, Quantity.SULFIDE_CAPACITY
    )


def test_predict_time_capability_ladder_matches_the_pin() -> None:
    """Phase basis changes only equilibrium phase composition.

    ``wt_percent`` is a closed phase basis. Free text is not. MAGEMIN vapour
    and fusion on every engine stay False here: this is the predict-time
    ladder, which still enters predict and returns a non-numeric refusal.
    """

    for engine in Engine:
        thermo = _THERMO_PIN.get(engine, frozenset())
        for quantity in Quantity:
            for basis, phase_composition_lacks in (
                (None, True),
                ("wt_percent", False),
                ("free text basis", True),
            ):
                got = _engine_lacks_quantity_capability(engine, quantity, basis)
                if quantity is Quantity.LOG10_K_REACTION:
                    expected = True
                elif quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION:
                    expected = phase_composition_lacks
                elif quantity in _LACKS_FALSE_EVERY_ENGINE:
                    expected = False
                else:
                    expected = quantity not in thermo
                assert got is expected, (engine, quantity, basis, got, expected)


def test_declared_capability_is_the_adapter_set() -> None:
    """Emission gate. Not the predict-time ladder.

    MAGEMin has no vapour adapter. Fusion is internal-analytical only.
    NASA answers its thermo set, saturation pressure, and Fe3+/Fe2+.
    The catalog answers the three vapour pressures. Quantities with no
    adapter are outside every set.
    """

    from simulator.battery.engine_capability import engine_covers_quantity

    covered = (
        (Engine.VAPOROCK, Quantity.CP),
        (Engine.VAPOROCK, Quantity.P_SAT),
        (Engine.VAPOROCK, Quantity.ACTIVITY),
        (Engine.VAPOROCK, Quantity.FE3_FE2_RATIO),
        (Engine.VAPOROCK, Quantity.EQUILIBRIUM_PHASE_COMPOSITION),
        (Engine.VAPOROCK, Quantity.TRANSITION_TEMPERATURE),
        (Engine.THERMOENGINE, Quantity.S),
        (Engine.THERMOENGINE, Quantity.H_MINUS_H298),
        (Engine.MAGEMIN, Quantity.CP),
        (Engine.MAGEMIN, Quantity.ACTIVITY),
        (Engine.ALPHAMELTS, Quantity.P_PARTIAL),
        (Engine.OPENIMCC, Quantity.P_PARTIAL),
        (Engine.INTERNAL_ANALYTICAL, Quantity.DELTA_FG),
        (Engine.INTERNAL_ANALYTICAL, Quantity.LOG10_KF),
        (Engine.INTERNAL_ANALYTICAL, Quantity.ENTHALPY_OF_FUSION),
        (Engine.INTERNAL_ANALYTICAL, Quantity.P_SAT),
        (Engine.NASA_CEA_9, Quantity.CP),
        (Engine.NASA_CEA_9, Quantity.DELTA_FH),
        (Engine.NASA_CEA_9, Quantity.DELTA_FG),
        (Engine.NASA_CEA_9, Quantity.P_SAT),
        (Engine.NASA_CEA_9, Quantity.FE3_FE2_RATIO),
        (Engine.CATALOG_EVALUATOR, Quantity.P_SAT),
        (Engine.CATALOG_EVALUATOR, Quantity.P_PARTIAL),
        (Engine.CATALOG_EVALUATOR, Quantity.P_REFERENCE),
    )
    uncovered = (
        (Engine.MAGEMIN, Quantity.P_SAT),
        (Engine.MAGEMIN, Quantity.P_PARTIAL),
        (Engine.MAGEMIN, Quantity.P_REFERENCE),
        (Engine.MAGEMIN, Quantity.ENTHALPY_OF_FUSION),
        (Engine.ALPHAMELTS, Quantity.CP),
        (Engine.ALPHAMELTS, Quantity.DELTA_FG),
        (Engine.OPENIMCC, Quantity.DELTA_FG),
        (Engine.INTERNAL_ANALYTICAL, Quantity.CP),
        (Engine.INTERNAL_ANALYTICAL, Quantity.S),
        (Engine.INTERNAL_ANALYTICAL, Quantity.DELTA_FH),
        (Engine.NASA_CEA_9, Quantity.P_PARTIAL),
        (Engine.NASA_CEA_9, Quantity.ACTIVITY),
        (Engine.NASA_CEA_9, Quantity.ENTHALPY_OF_FUSION),
        (Engine.NASA_CEA_9, Quantity.LOG10_KF),
        (Engine.CATALOG_EVALUATOR, Quantity.ACTIVITY),
        (Engine.CATALOG_EVALUATOR, Quantity.CP),
        (Engine.CATALOG_EVALUATOR, Quantity.FE3_FE2_RATIO),
        (Engine.VAPOROCK, Quantity.DELTA_FG),
    )
    for engine, quantity in covered:
        assert engine_covers_quantity(engine, quantity), (engine, quantity)
    for engine, quantity in uncovered:
        assert not engine_covers_quantity(engine, quantity), (engine, quantity)
    for quantity in (
        Quantity.FUGACITY,
        Quantity.INTERACTION_PARAMETER,
        Quantity.EVOLVED_GAS_YIELD,
        Quantity.LOG10_K_REACTION,
        Quantity.LOG10_K_STAR,
        Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW,
        Quantity.RESIDUE_COMPONENT_COMPOSITION,
        Quantity.ELEMENT_RETENTION_RATIO,
    ):
        for engine in Engine:
            assert not engine_covers_quantity(engine, quantity), (engine, quantity)


def test_knudsen_residue_binds_outside_the_generic_table() -> None:
    from dataclasses import replace
    from decimal import Decimal

    from simulator.battery.score_run import _residue_adapter_binds
    from simulator.source_ids import HASHIMOTO_SOURCE_ID
    from tests.battery import factories as F

    identity = replace(
        F.psat_identity("Na"),
        quantity=Quantity.RESIDUE_COMPONENT_COMPOSITION,
    )
    point = F.observation(
        "residue-1",
        "kems-1",
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id=HASHIMOTO_SOURCE_ID,
    )
    knudsen = F.kems_experiment()
    plain = F.tabulation_experiment()
    assert _residue_adapter_binds(
        point, Engine.MAGEMIN, knudsen, Quantity.RESIDUE_COMPONENT_COMPOSITION
    )
    assert _residue_adapter_binds(
        point,
        Engine.OPENIMCC,
        plain,
        Quantity.RESIDUE_COMPONENT_COMPOSITION,
    )
    assert not _residue_adapter_binds(
        point,
        Engine.MAGEMIN,
        plain,
        Quantity.RESIDUE_COMPONENT_COMPOSITION,
    )


def test_production_score_compiles_only_covered_pairs(monkeypatch) -> None:
    from dataclasses import replace
    from decimal import Decimal

    from simulator.battery.enums import (
        ExecutionState,
        ResidualStatus,
        SourceRelation,
    )
    from simulator.battery.records import Execution, Residual
    from simulator.battery.score import ScoreContext, score_store
    from tests.battery import factories as F

    calls: list[tuple[str, Engine]] = []

    def fake(reference, engine, **_kwargs):
        calls.append((reference.observation_id, engine))
        return (
            Residual(
                key=f"k::{reference.observation_id}",
                reference=reference.observation_id,
                execution=Execution(state=ExecutionState.NOT_PROBED),
                rail=None,
                status=ResidualStatus.REFUSED,
                source_relation=SourceRelation.UNKNOWN,
                score_eligible=False,
                exclusions=(),
                notices=(),
            ),
            None,
        )

    monkeypatch.setattr(
        "simulator.battery.score._compile_residual_with_lineage", fake
    )
    experiment = F.tabulation_experiment()
    covered = F.observation(
        "covered-psat",
        experiment.experiment_id,
        F.psat_identity("Na"),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="independent-experiment",
    )
    uncovered = F.observation(
        "uncovered-fugacity",
        experiment.experiment_id,
        replace(F.psat_identity("Cl2"), quantity=Quantity.FUGACITY),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="independent-experiment",
    )
    context = ScoreContext(
        {"work-1": F.work()},
        {experiment.experiment_id: experiment},
        {
            covered.observation_id: covered,
            uncovered.observation_id: uncovered,
        },
    )
    residuals, _candidates = score_store(
        context,
        engines=(Engine.VAPOROCK, Engine.MAGEMIN),
        include_diagnostics=False,
    )
    assert calls == [("covered-psat", Engine.VAPOROCK)]
    assert [residual.reference for residual in residuals] == ["covered-psat"]


def test_injected_predictor_still_scores_an_uncovered_quantity(monkeypatch) -> None:
    from dataclasses import replace
    from decimal import Decimal

    from simulator.battery.enums import ExecutionState, MetricOperation, ResidualStatus, SourceRelation
    from simulator.battery.records import Execution, Residual, ResidualNumeric
    from simulator.battery.score import ScoreContext, score_store
    from tests.battery import factories as F
    from tests.battery.test_score import _predict

    experiment = F.tabulation_experiment()
    observation = F.observation(
        "injected-fugacity",
        experiment.experiment_id,
        replace(F.psat_identity("Cl2"), quantity=Quantity.FUGACITY),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="independent-experiment",
    )
    context = ScoreContext(
        {"work-1": F.work()},
        {experiment.experiment_id: experiment},
        {observation.observation_id: observation},
    )
    calls: list[Engine] = []

    def predict(engine, observed, **_kwargs):
        calls.append(engine)
        return _predict(Decimal("1"), observed.identity)

    def fake_compile(reference, engine, *, predict=None, **_kwargs):
        assert predict is not None
        prediction = predict(engine, reference)
        return (
            Residual(
                key=f"k::{reference.observation_id}",
                reference=reference.observation_id,
                execution=Execution(state=ExecutionState.NOT_PROBED),
                rail=None,
                status=ResidualStatus.MATCH,
                source_relation=SourceRelation.UNKNOWN,
                score_eligible=False,
                exclusions=(),
                notices=(),
                numeric=ResidualNumeric(
                    MetricOperation.DEX,
                    "dimensionless",
                    prediction.value,
                    None,
                ),
            ),
            None,
        )

    monkeypatch.setattr(
        "simulator.battery.score._compile_residual_with_lineage", fake_compile
    )
    residuals, _candidates = score_store(
        context,
        engines=(Engine.MAGEMIN,),
        include_diagnostics=False,
        predict=predict,
    )
    assert calls == [Engine.MAGEMIN]
    assert len(residuals) == 1
    assert residuals[0].numeric is not None


def test_bound_knudsen_residue_is_emitted_and_not_counted_uncovered(monkeypatch) -> None:
    """A bound residue residual is not an uncovered matrix cell."""

    from dataclasses import replace
    from decimal import Decimal

    from simulator.battery.engine_capability import capability_summary
    from simulator.battery.enums import ExecutionState, ResidualStatus, SourceRelation
    from simulator.battery.records import Execution, Residual
    from simulator.battery.score import EnginePrediction, ScoreContext, score_store
    from tests.battery import factories as F

    experiment = F.kems_experiment()
    observation = F.observation(
        "knudsen-residue",
        experiment.experiment_id,
        replace(
            F.psat_identity("Na"),
            quantity=Quantity.RESIDUE_COMPONENT_COMPOSITION,
        ),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="independent-experiment",
    )
    context = ScoreContext(
        {"work-1": F.work()},
        {experiment.experiment_id: experiment},
        {observation.observation_id: observation},
    )

    def fake_prediction(_context, _reference, engine, _cache):
        return EnginePrediction(
            engine=engine,
            channel=engine.value,
            execution=Execution(state=ExecutionState.NOT_PROBED),
        )

    def fake_compile(reference, _engine, **_kwargs):
        return (
            Residual(
                key=f"k::{reference.observation_id}",
                reference=reference.observation_id,
                execution=Execution(state=ExecutionState.NOT_PROBED),
                rail=None,
                status=ResidualStatus.REFUSED,
                source_relation=SourceRelation.UNKNOWN,
                score_eligible=False,
                exclusions=(),
                notices=(),
            ),
            None,
        )

    monkeypatch.setattr(
        "simulator.battery.score._knudsen_residue_prediction", fake_prediction
    )
    monkeypatch.setattr(
        "simulator.battery.score._compile_residual_with_lineage", fake_compile
    )
    residuals, _candidates = score_store(
        context,
        engines=(Engine.MAGEMIN,),
        include_diagnostics=False,
    )
    assert [residual.reference for residual in residuals] == ["knudsen-residue"]
    matrix = capability_summary(
        observations=context.observations,
        origins=context.origins,
        experiments=context.experiments,
        engines=(Engine.MAGEMIN,),
        scored_observation_ids=(),
        n_records=1,
        n_scored_records=0,
        n_refused_records=1,
    )["capability_matrix"]
    assert matrix["uncovered_pair_total"] == 0
    assert Quantity.RESIDUE_COMPONENT_COMPOSITION.value not in {
        row["quantity"] for row in matrix["quantities_no_engine_predicts"]
    }
    assert matrix["quantities_no_engine_predicts"] == []


def test_skipped_diagnostic_and_duplicate_are_counted_outside_the_census() -> None:
    """Uncovered diagnostic and duplicate refs stay visible off the candidate matrix."""

    from dataclasses import replace
    from decimal import Decimal

    from simulator.battery.engine_capability import capability_summary
    from simulator.battery.enums import QuotedTargetKind
    from simulator.battery.records import QuotedFrom
    from simulator.battery.score import ScoreContext
    from tests.battery import factories as F

    diagnostic_experiment = F.tabulation_experiment(experiment_id="diag-exp")
    diagnostic = F.observation(
        "diag-fugacity",
        diagnostic_experiment.experiment_id,
        replace(F.psat_identity("Cl2"), quantity=Quantity.FUGACITY),
        Decimal("1"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    thermo = F.observation(
        "diag-dfg",
        diagnostic_experiment.experiment_id,
        F.o2_identity(),
        Decimal("1"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    primary = F.tabulation_experiment(experiment_id="primary-exp")
    quoted = replace(
        F.tabulation_experiment(experiment_id="quoted-exp"),
        quoted_from=QuotedFrom(QuotedTargetKind.EXPERIMENT, primary.experiment_id),
    )
    duplicate = F.observation(
        "dup-fugacity",
        quoted.experiment_id,
        replace(F.psat_identity("Na"), quantity=Quantity.FUGACITY),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="independent-experiment",
    )
    context = ScoreContext(
        {"work-1": F.work()},
        {
            diagnostic_experiment.experiment_id: diagnostic_experiment,
            primary.experiment_id: primary,
            quoted.experiment_id: quoted,
        },
        {
            diagnostic.observation_id: diagnostic,
            thermo.observation_id: thermo,
            duplicate.observation_id: duplicate,
        },
    )
    matrix = capability_summary(
        observations=context.observations,
        origins={},
        experiments=context.experiments,
        engines=(Engine.VAPOROCK, Engine.INTERNAL_ANALYTICAL),
        scored_observation_ids=(),
        n_records=0,
        n_scored_records=0,
        n_refused_records=0,
    )["capability_matrix"]
    assert matrix["rows"] == []
    assert matrix["uncovered_pair_total"] == 0
    assert matrix["quantities_no_engine_predicts"] == []
    # Two fugacity refs, two engines. The compilation delta_fG row is
    # offered only to an engine that declares it, so it is not in this count.
    assert matrix["n_outside_census_not_emitted"] == 4
