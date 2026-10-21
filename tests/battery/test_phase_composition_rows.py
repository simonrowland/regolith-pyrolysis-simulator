"""Phase-composition rows, distribution ratios, and printed S/Cl capacities.

d-223: one scalar row per printed component. The balance is not filled, and pO2
is not taken from a model closure. Scoring is component-wise dex. An engine
that does not report a phase assemblage refuses, and the refusal is counted.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    QUANTITY_UNITS,
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    MetricOperation,
    Phase,
    PhaseRole,
    Quantity,
    Rail,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
)
from simulator.battery.identity import Identity, celsius_to_kelvin
from simulator.battery.migrate import migrate
from simulator.battery.records import (
    Composition,
    Execution,
    Residual,
    ResidualRefusal,
    Species,
    State,
)
from simulator.battery.score import (
    ScoreContext,
    _ScorePayloadAccumulator,
    _residual_engine_observable,
    compute_metric,
    metric_operation_for_identity,
    no_headline_rail_reason,
    populate_numeric,
    predict_with_engine,
    rail_for_quantity,
    refusal_census,
    residual_to_plain,
)
from simulator.melt_backend.base import EquilibriumResult
from tests.battery import factories as F
from tests.battery.test_migrate import _write_min_tree

SULFIDE_EQUATION = (
    "log10 C_S = log10(wt% S) + 0.5 log10(pO2/pS2), Fincham and Richardson"
)
CHLORIDE_EQUATION = (
    "log10 C_Cl = log10(wt% Cl) + 0.5 log10(pO2/pCl2), printed definition"
)
T_C = 1400
P_O2_PA = 10


def _located(value: object, *, table: str, page: int) -> dict:
    return {
        "state": {"tag": "value", "value": value},
        "locator": {"table": table, "page": page},
    }


def _observation(
    observation_id: str,
    *,
    quantity: str,
    phase_label: str,
    amount_key: str,
    amount: object,
    table: str,
    page: int,
    basis: str | None = None,
    counter: str | None = None,
    method: str = "measured_direct",
    equation: str | None = None,
    extra: dict | None = None,
    conditions: dict | None = None,
) -> dict:
    values: dict = {
        "quantity": quantity,
        "phase_label": phase_label,
        "T_C": T_C,
        amount_key: amount,
        "method_class": method,
        "admission_status": "admitted",
    }
    if basis is not None:
        values["basis"] = basis
    if counter is not None:
        values["counter_phase_label"] = counter
    if equation is not None:
        values["defining_equation"] = equation
    if extra:
        values.update(extra)
    if conditions is not None:
        values["point_conditions"] = conditions
    return {
        "observation_id": observation_id,
        "experiment": "sample-1",
        "locator": {"table": table, "page": page},
        "values": values,
    }


def _extract() -> dict:
    oxygen = _located(P_O2_PA, table="2", page=14)
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {
            "citation": "Fixture, A. (2026), Test Journal 1:1",
            "doi": "10.1234/FIXTURE",
        },
        "extraction": {"method": "unit_test", "date": "2026-10-21", "worker": "pytest"},
        "review_status": "draft",
        "species": {
            "SiO2": {
                "observations": [
                    _observation(
                        "sample1-sio2-melt",
                        quantity="equilibrium_phase_composition",
                        phase_label="melt",
                        basis="wt_percent",
                        amount_key="component_amount",
                        amount=52.1,
                        table="2",
                        page=14,
                        extra={"printed_sum": 98.4},
                        conditions={
                            "pO2_Pa": oxygen,
                            "gas_mix": _located("CO-CO2", table="2", page=14),
                            "printed_sum": _located(98.4, table="2", page=14),
                        },
                    ),
                    _observation(
                        "reject-balance",
                        quantity="equilibrium_phase_composition",
                        phase_label="melt",
                        basis="wt_percent",
                        amount_key="component_amount",
                        amount=52.1,
                        table="2",
                        page=14,
                        extra={"balance_filled": True, "printed_sum": 98.4},
                        conditions={"pO2_Pa": oxygen},
                    ),
                ]
            },
            "Al2O3": {
                "observations": [
                    _observation(
                        "sample1-al2o3-melt",
                        quantity="equilibrium_phase_composition",
                        phase_label="melt",
                        basis="wt_percent",
                        amount_key="component_amount",
                        amount=18,
                        table="2",
                        page=14,
                        extra={"printed_sum": 97},
                        conditions={"pO2_Pa": oxygen},
                    )
                ]
            },
            "MgO": {
                "observations": [
                    _observation(
                        "reject-renormalized",
                        quantity="equilibrium_phase_composition",
                        phase_label="melt",
                        basis="wt_percent",
                        amount_key="component_amount",
                        amount=10,
                        table="2",
                        page=14,
                        extra={"renormalized": "yes"},
                        conditions={"pO2_Pa": oxygen},
                    )
                ]
            },
            "FeO": {
                "observations": [
                    _observation(
                        "sample1-feo-slag-metal",
                        quantity="distribution_ratio",
                        phase_label="slag",
                        counter="metal",
                        basis="wt_percent",
                        amount_key="distribution_ratio",
                        amount=1.5,
                        table="3",
                        page=15,
                        conditions={
                            "pO2_Pa": _located(P_O2_PA, table="3", page=15)
                        },
                    ),
                    _observation(
                        "reject-backfill",
                        quantity="distribution_ratio",
                        phase_label="slag",
                        counter="metal",
                        basis="wt_percent",
                        amount_key="distribution_ratio",
                        amount=1.5,
                        table="3",
                        page=15,
                        extra={"pO2_from_closure": 1},
                        conditions={
                            "pO2_Pa": _located(P_O2_PA, table="3", page=15)
                        },
                    ),
                    _observation(
                        "reject-same-phase",
                        quantity="distribution_ratio",
                        phase_label="slag",
                        counter="slag",
                        basis="wt_percent",
                        amount_key="distribution_ratio",
                        amount=1.5,
                        table="3",
                        page=15,
                        conditions={
                            "pO2_Pa": _located(P_O2_PA, table="3", page=15)
                        },
                    ),
                ]
            },
            "S": {
                "observations": [
                    _observation(
                        "sample1-log-cs",
                        quantity="sulfide_capacity",
                        phase_label="melt",
                        amount_key="log10_C_S",
                        amount=-2.4,
                        table="4",
                        page=16,
                        method="author_derived",
                        equation=SULFIDE_EQUATION,
                        conditions={
                            "pO2_Pa": _located(P_O2_PA, table="4", page=16),
                            "pS2_Pa": _located(3, table="4", page=16),
                        },
                    ),
                    _observation(
                        "reject-model-po2",
                        quantity="sulfide_capacity",
                        phase_label="melt",
                        amount_key="log10_C_S",
                        amount=-2.4,
                        table="4",
                        page=16,
                        method="author_derived",
                        equation=SULFIDE_EQUATION,
                        extra={"model_pO2_Pa": 1},
                        conditions={
                            "pO2_Pa": _located(P_O2_PA, table="4", page=16)
                        },
                    ),
                ]
            },
            "Cl": {
                "observations": [
                    _observation(
                        "sample1-log-ccl",
                        quantity="chloride_capacity",
                        phase_label="melt",
                        amount_key="log10_C_Cl",
                        amount=-1.2,
                        table="5",
                        page=17,
                        method="author_derived",
                        equation=CHLORIDE_EQUATION,
                        conditions={
                            "pO2_Pa": _located(P_O2_PA, table="5", page=17),
                            "pCl2_Pa": _located(4, table="5", page=17),
                        },
                    ),
                    _observation(
                        "reject-no-equation",
                        quantity="chloride_capacity",
                        phase_label="melt",
                        amount_key="log10_C_Cl",
                        amount=-1.2,
                        table="5",
                        page=17,
                        method="author_derived",
                    ),
                ]
            },
            "CaO": {
                "observations": [
                    _observation(
                        "sample1-cao-glass-gasmix",
                        quantity="equilibrium_phase_composition",
                        phase_label="glass",
                        basis="wt_percent",
                        amount_key="component_amount",
                        amount=12.5,
                        table="6",
                        page=18,
                        conditions={
                            "gas_mix": _located("CO-CO2", table="6", page=18)
                        },
                    )
                ]
            },
        },
    }


def _row(result, raw_id: str):
    matches = [
        observation
        for observation in result.observations.values()
        if observation.observation_id.endswith(f"::{raw_id}")
    ]
    assert len(matches) == 1, (
        raw_id,
        [observation.observation_id for observation in result.observations.values()],
        [(entry.observation_id, entry.why) for entry in result.queue],
    )
    return matches[0]


def _queue_why(result, raw_id: str) -> str:
    whys = [
        entry.why
        for entry in result.queue
        if entry.observation_id and entry.observation_id.endswith(f"::{raw_id}")
    ]
    assert whys, (
        raw_id,
        [(entry.observation_id, entry.why) for entry in result.queue],
    )
    return " ".join(whys)


def _condition_amount(observation, key: str):
    located = observation.point_conditions[key]
    assert located.state.is_value
    return located.state.value


def test_phase_rows_migrate_printed_components_and_refuse_mutations(tmp_path) -> None:
    root = _write_min_tree(tmp_path / "tree", _extract())
    result = migrate(root, write=False)
    expected_t = celsius_to_kelvin(T_C)

    sio2 = _row(result, "sample1-sio2-melt")
    al2o3 = _row(result, "sample1-al2o3-melt")
    feo = _row(result, "sample1-feo-slag-metal")
    sulfide = _row(result, "sample1-log-cs")
    chloride = _row(result, "sample1-log-ccl")
    glass = _row(result, "sample1-cao-glass-gasmix")
    kept = (sio2, al2o3, feo, sulfide, chloride, glass)

    experiment_ids = {observation.experiment_id for observation in kept}
    assert len(experiment_ids) == 1
    assert next(iter(experiment_ids)).endswith("::experiment::sample-1")

    for observation in kept:
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == expected_t
        assert _condition_amount(observation, "temperature_K") == expected_t
        assert observation.identity.composition.is_unknown

    assert sio2.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    assert sio2.identity.species.formula == "SiO2"
    assert sio2.identity.species.phase.value is Phase.L
    assert sio2.identity.phase_role.value is PhaseRole.MELT
    assert sio2.identity.counter_phase_role.is_not_applicable
    assert sio2.identity.subtype.value == "wt_percent"
    assert sio2.value.point == Decimal("52.1")
    assert sio2.identity.fO2_Pa.value == Decimal(str(P_O2_PA))
    assert _condition_amount(sio2, "pO2_Pa") == Decimal(str(P_O2_PA))
    assert _condition_amount(sio2, "gas_mix") == "CO-CO2"
    assert _condition_amount(sio2, "printed_sum") == Decimal("98.4")
    assert sio2.evidence.class_.value is EvidenceClass.MEASURED_DIRECT

    assert al2o3.identity.species.formula == "Al2O3"
    assert al2o3.value.point == Decimal("18")
    assert _condition_amount(al2o3, "printed_sum") == Decimal("97")
    assert al2o3.experiment_id == sio2.experiment_id

    assert feo.identity.quantity.value is Quantity.DISTRIBUTION_RATIO
    assert feo.identity.species.formula == "FeO"
    assert feo.identity.species.phase.value is Phase.L
    assert feo.identity.phase_role.value is PhaseRole.SLAG
    assert feo.identity.counter_phase_role.value is PhaseRole.METAL
    assert feo.identity.subtype.value == "wt_percent"
    assert feo.value.point == Decimal("1.5")
    assert feo.identity.fO2_Pa.value == Decimal(str(P_O2_PA))
    assert _condition_amount(feo, "pO2_Pa") == Decimal(str(P_O2_PA))

    assert sulfide.identity.quantity.value is Quantity.SULFIDE_CAPACITY
    assert sulfide.identity.species.formula == "S"
    assert sulfide.identity.phase_role.value is PhaseRole.MELT
    assert sulfide.identity.subtype.is_not_applicable
    assert sulfide.identity.counter_phase_role.is_not_applicable
    assert sulfide.value.point == Decimal("-2.4")
    assert sulfide.evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE
    assert sulfide.evidence.model == "author_derived"
    assert sulfide.evidence.original_method_class == "author_derived"
    assert sulfide.derivation is not None
    assert sulfide.derivation.relation == SULFIDE_EQUATION
    assert sulfide.derivation.output_unit == "dimensionless"
    assert _condition_amount(sulfide, "pS2_Pa") == Decimal("3")
    assert sulfide.identity.fO2_Pa.value == Decimal(str(P_O2_PA))

    assert chloride.identity.quantity.value is Quantity.CHLORIDE_CAPACITY
    assert chloride.value.point == Decimal("-1.2")
    assert chloride.evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE
    assert chloride.evidence.model == "author_derived"
    assert chloride.derivation is not None
    assert chloride.derivation.relation == CHLORIDE_EQUATION
    assert _condition_amount(chloride, "pCl2_Pa") == Decimal("4")

    assert glass.identity.species.phase.value is Phase.GLASS
    assert glass.identity.phase_role.value is PhaseRole.GLASS
    assert glass.identity.fO2_Pa.is_unknown
    assert "pO2_Pa" not in glass.point_conditions
    assert "fO2_Pa" not in glass.point_conditions
    assert _condition_amount(glass, "gas_mix") == "CO-CO2"
    assert glass.value.point == Decimal("12.5")

    emitted = {observation.observation_id for observation in result.observations.values()}
    for raw_id in (
        "reject-balance",
        "reject-renormalized",
        "reject-backfill",
        "reject-model-po2",
        "reject-same-phase",
        "reject-no-equation",
    ):
        assert all(not observation_id.endswith(f"::{raw_id}") for observation_id in emitted)

    assert "balance_filled" in _queue_why(result, "reject-balance")
    assert "filling the balance is refused" in _queue_why(result, "reject-balance")
    assert "renormalized" in _queue_why(result, "reject-renormalized")
    assert "pO2_from_closure" in _queue_why(result, "reject-backfill")
    assert "atmosphere back-fill" in _queue_why(result, "reject-backfill")
    assert "model_pO2_Pa" in _queue_why(result, "reject-model-po2")
    assert "distribution ratio phases are the same" in _queue_why(
        result, "reject-same-phase"
    )
    assert "defining_equation" in _queue_why(result, "reject-no-equation")
    for raw_id in ("sample1-log-cs", "sample1-log-ccl"):
        whys = [
            entry.why
            for entry in result.queue
            if entry.observation_id and entry.observation_id.endswith(f"::{raw_id}")
        ]
        assert not any("lineage" in why for why in whys)


def _bulk() -> Composition:
    return Composition(
        basis="ordered_complete_mole_inventory",
        components=(("SiO2", Decimal("1")), ("MgO", Decimal("1"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )


def _phase_identity(
    formula: str,
    quantity: Quantity,
    *,
    role: PhaseRole,
    counter: PhaseRole | None = None,
    subtype: str | None = "wt_percent",
    fO2_Pa: Decimal | None = Decimal(str(P_O2_PA)),
    total_pressure_Pa: Decimal | None = Decimal("100000"),
) -> Identity:
    phase = Phase.GLASS if role is PhaseRole.GLASS else Phase.L
    kwargs = {
        "quantity": quantity,
        "species": Species(formula, phase),
        "temperature_K": State.of(celsius_to_kelvin(T_C)),
        "phase_role": State.of(role),
        "composition": State.of(_bulk()),
    }
    if subtype is not None:
        kwargs["subtype"] = State.of(subtype)
    if counter is not None:
        kwargs["counter_phase_role"] = State.of(counter)
    if fO2_Pa is not None:
        kwargs["fO2_Pa"] = State.of(fO2_Pa)
    if total_pressure_Pa is not None:
        kwargs["total_pressure_Pa"] = State.of(total_pressure_Pa)
    return Identity(**kwargs)


def _dex(candidate: Decimal, reference: Decimal) -> Decimal:
    produced = compute_metric(MetricOperation.DEX, candidate, reference)
    assert produced is not None
    assert produced == Decimal(str(math.log10(float(candidate / reference))))
    return produced


def test_phase_rows_score_component_dex_from_the_assemblage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery.score import composition_wt_pct

    calls: list[dict] = []

    def hook(engine, *, temperature_C, composition_kg, fO2_log, pressure_bar):
        calls.append(
            {
                "engine": engine,
                "temperature_C": temperature_C,
                "composition_kg": dict(composition_kg),
                "fO2_log": fO2_log,
                "pressure_bar": pressure_bar,
            }
        )
        return EquilibriumResult(
            liquid_fraction=1.0,
            phase_compositions={"liquid": {"SiO2": 10.0, "MgO": 40.0}},
        )

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", hook
    )
    identity = _phase_identity("SiO2", Quantity.EQUILIBRIUM_PHASE_COMPOSITION, role=PhaseRole.MELT)
    observation = F.observation(
        "sio2-melt",
        "sample-1",
        identity,
        Decimal("5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-phase-source",
    )
    expected_kg = {
        name: amount / 1000.0
        for name, amount in composition_wt_pct(identity.composition.value).items()
    }
    for engine in (Engine.ALPHAMELTS, Engine.MAGEMIN, Engine.THERMOENGINE):
        prediction = predict_with_engine(engine, observation)
        assert prediction.execution.state is ExecutionState.PRODUCED
        assert prediction.execution.call_evidence == "phase_assemblage"
        assert prediction.authority is Authority.BRIDGE
        assert prediction.value == Decimal("10")
        assert prediction.unit == QUANTITY_UNITS[Quantity.EQUILIBRIUM_PHASE_COMPOSITION]
        numeric, reason, _detail = populate_numeric(
            quantity=Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
            candidate=prediction.value,
            reference=Decimal("5"),
            source_relation=SourceRelation.INDEPENDENT,
            operation_override=metric_operation_for_identity(identity),
        )
        assert reason is None
        assert numeric is not None
        assert numeric.operation is MetricOperation.DEX
        assert numeric.value == _dex(Decimal("10"), Decimal("5"))

    assert [call["engine"] for call in calls] == [
        Engine.ALPHAMELTS,
        Engine.MAGEMIN,
        Engine.THERMOENGINE,
    ]
    for call in calls:
        assert call["fO2_log"] == -4.0
        assert call["fO2_log"] != -9.0
        assert call["pressure_bar"] == 1.0
        assert call["pressure_bar"] != 1e-6
        assert call["temperature_C"] == float(
            celsius_to_kelvin(T_C) - Decimal("273.15")
        )
        assert call["composition_kg"] == expected_kg
        assert set(call["composition_kg"]) == {"SiO2", "MgO"}


def test_distribution_ratio_dex_uses_both_phase_amounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hook(engine, *, temperature_C, composition_kg, fO2_log, pressure_bar):
        return EquilibriumResult(
            liquid_fraction=1.0,
            phase_compositions={
                "slag": {"FeO": 10.0},
                "metal": {"FeO": 5.0},
            },
        )

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", hook
    )
    identity = _phase_identity(
        "FeO",
        Quantity.DISTRIBUTION_RATIO,
        role=PhaseRole.SLAG,
        counter=PhaseRole.METAL,
    )
    observation = F.observation(
        "feo-l",
        "sample-1",
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-phase-source",
    )
    prediction = predict_with_engine(Engine.MAGEMIN, observation)
    assert prediction.value == Decimal("2")
    numeric, reason, _detail = populate_numeric(
        quantity=Quantity.DISTRIBUTION_RATIO,
        candidate=prediction.value,
        reference=Decimal("1"),
        source_relation=SourceRelation.INDEPENDENT,
        operation_override=metric_operation_for_identity(identity),
    )
    assert reason is None
    assert numeric is not None
    assert numeric.value == _dex(Decimal("2"), Decimal("1"))
    assert rail_for_quantity(Quantity.DISTRIBUTION_RATIO, species_formula="FeO") is Rail.REDOX
    assert (
        rail_for_quantity(Quantity.DISTRIBUTION_RATIO, species_formula="SiO2") is None
    )
    assert (
        no_headline_rail_reason(Quantity.DISTRIBUTION_RATIO, species_formula="SiO2")
        == "distribution_ratio_not_multivalent"
    )
    assert rail_for_quantity(Quantity.EQUILIBRIUM_PHASE_COMPOSITION) is Rail.MELT_ACTIVITY
    assert rail_for_quantity(Quantity.SULFIDE_CAPACITY) is Rail.MELT_ACTIVITY
    assert rail_for_quantity(Quantity.CHLORIDE_CAPACITY) is Rail.MELT_ACTIVITY


def test_non_phase_engines_refuse_and_the_refusal_is_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hook(*args, **kwargs):
        raise AssertionError("phase assemblage must not be called")

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", hook
    )
    composition = _phase_identity(
        "SiO2", Quantity.EQUILIBRIUM_PHASE_COMPOSITION, role=PhaseRole.MELT
    )
    composition_observation = F.observation(
        "sio2-melt",
        "sample-1",
        composition,
        Decimal("5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-phase-source",
    )
    mole = _phase_identity(
        "SiO2",
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        role=PhaseRole.MELT,
        subtype="mole_fraction",
    )
    mole_observation = F.observation(
        "sio2-mole",
        "sample-1",
        mole,
        Decimal("0.4"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-phase-source",
    )
    capacity = _phase_identity(
        "S",
        Quantity.SULFIDE_CAPACITY,
        role=PhaseRole.MELT,
        subtype=None,
    )
    capacity_observation = F.observation(
        "log-cs",
        "sample-1",
        capacity,
        Decimal("-2.4"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-phase-source",
    )
    missing_pressure = _phase_identity(
        "SiO2",
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        role=PhaseRole.MELT,
        total_pressure_Pa=None,
    )
    missing_pressure_observation = F.observation(
        "sio2-no-p",
        "sample-1",
        missing_pressure,
        Decimal("5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-phase-source",
    )

    for engine in (Engine.VAPOROCK, Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL):
        prediction = predict_with_engine(engine, composition_observation)
        assert prediction.execution.state is ExecutionState.UNSUPPORTED
        assert prediction.refusal_reason is RefusalReason.ENGINE_DOES_NOT_REPORT_PHASE_COMPOSITION
        assert prediction.value is None

    mole_prediction = predict_with_engine(Engine.MAGEMIN, mole_observation)
    assert mole_prediction.refusal_reason is RefusalReason.COEFFICIENT_BASIS_MISMATCH
    assert mole_prediction.value is None

    capacity_prediction = predict_with_engine(Engine.MAGEMIN, capacity_observation)
    assert capacity_prediction.execution.state is ExecutionState.UNSUPPORTED
    assert (
        capacity_prediction.refusal_reason
        is RefusalReason.ENGINE_DOES_NOT_REPORT_CAPACITY
    )

    pressure_prediction = predict_with_engine(Engine.ALPHAMELTS, missing_pressure_observation)
    assert pressure_prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert pressure_prediction.refusal_detail["input"] == "total_pressure_Pa"
    assert pressure_prediction.execution.state is ExecutionState.NOT_PROBED

    phase_prediction = predict_with_engine(Engine.VAPOROCK, composition_observation)
    phase_residual = Residual(
        key=f"{composition_observation.observation_id}::{Engine.VAPOROCK.value}",
        reference=composition_observation.observation_id,
        execution=Execution(state=phase_prediction.execution.state),
        rail=Rail.MELT_ACTIVITY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=False,
        exclusions=(),
        notices=(),
        refusal=ResidualRefusal(
            reason=phase_prediction.refusal_reason,
            detail=dict(phase_prediction.refusal_detail),
        ),
    )
    capacity_residual = Residual(
        key=f"{capacity_observation.observation_id}::{Engine.MAGEMIN.value}",
        reference=capacity_observation.observation_id,
        execution=Execution(state=capacity_prediction.execution.state),
        rail=Rail.MELT_ACTIVITY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=False,
        exclusions=(),
        notices=(),
        refusal=ResidualRefusal(
            reason=capacity_prediction.refusal_reason,
            detail=dict(capacity_prediction.refusal_detail),
        ),
    )
    census = refusal_census((phase_residual, capacity_residual))
    assert sum(census.values()) == 2
    assert any(
        RefusalReason.ENGINE_DOES_NOT_REPORT_PHASE_COMPOSITION.value in key
        for key in census
    )
    assert any(
        RefusalReason.ENGINE_DOES_NOT_REPORT_CAPACITY.value in key for key in census
    )

    context = ScoreContext(
        works={},
        experiments={},
        observations={
            composition_observation.observation_id: composition_observation,
            capacity_observation.observation_id: capacity_observation,
        },
    )
    accumulator = _ScorePayloadAccumulator(
        context, (Engine.VAPOROCK, Engine.MAGEMIN)
    )
    phase_payload = residual_to_plain(phase_residual)
    capacity_payload = residual_to_plain(capacity_residual)
    assert _residual_engine_observable(phase_payload) is True
    assert _residual_engine_observable(capacity_payload) is True
    accumulator.add(phase_payload)
    accumulator.add(capacity_payload)
    assert sum(accumulator.refusal_counts.values()) == 2
