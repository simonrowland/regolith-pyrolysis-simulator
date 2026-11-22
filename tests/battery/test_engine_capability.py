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


def test_injected_predictor_still_scores_an_uncovered_quantity() -> None:
    from dataclasses import replace
    from decimal import Decimal

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

    residuals, _candidates = score_store(
        context,
        engines=(Engine.MAGEMIN,),
        include_diagnostics=False,
        predict=predict,
    )
    assert calls == [Engine.MAGEMIN]
    assert len(residuals) == 1
    assert residuals[0].numeric is not None
