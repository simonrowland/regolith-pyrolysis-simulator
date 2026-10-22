"""Phase-composition rows, distribution ratios, and printed S/Cl capacities.

d-223: one scalar row per printed component. The balance is not filled, and pO2
is not taken from a model closure. Scoring is component-wise dex. A printed
experiment total pressure is an input. Distribution ratios sit on redox or
melt activity. A capacity is scored only when the engine adapter declares
dissolved S or Cl, and that refusal is counted.
"""

from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal

import pytest
import yaml

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
from simulator.battery.identity import Identity, celsius_to_kelvin, profile_for
from simulator.battery.migrate import REPO_ROOT, migrate
from simulator.battery.records import (
    Composition,
    Derivation,
    Execution,
    Residual,
    ResidualRefusal,
    Species,
    State,
)
from simulator.battery.score import (
    DECISION_BAND_RATCHET_RELATIVE_PATH,
    ScoreContext,
    _ScorePayloadAccumulator,
    _alignment_axes,
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
    "C_Cl = (wt% Cl) * fO2**(1/4) / fCl2**(1/2), "
    "charge-balanced; fugacity over 1 bar"
)
# The sulfide square-root ratio written on chloride. Not a chloride definition.
SULFIDE_EXPONENT_ON_CHLORIDE = (
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


def _extract_with_printed_pressure(point: str) -> dict:
    """Same rows, plus the experiment-registry total pressure the migrator reads."""

    doc = _extract()
    doc["experiments"] = [
        {
            "experiment_id": "sample-1",
            "locator": {"page": 3, "section": "Apparatus"},
            "pressure_environment": {
                "total_pressure_Pa": {
                    "state": {
                        "tag": "value",
                        "value": {"kind": "point", "point": point},
                    },
                    "locator": {"page": 3, "section": "Apparatus"},
                },
                "sweep_gas": {
                    "state": {"tag": "unknown", "reason": "not_published"},
                    "locator": {"page": 3, "section": "Apparatus"},
                },
                "regime": {
                    "regime_class": {"tag": "unknown", "reason": "not_published"},
                },
            },
        }
    ]
    return doc


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


def _with_bulk(observation):
    """Fill composition after migrate so scoring can reach the pressure check."""

    return replace(
        observation,
        identity=replace(observation.identity, composition=State.of(_bulk())),
    )


def _defining_log10_capacity(
    weight_percent: float, p_o2_pa: Decimal, p_anion_pa: Decimal
) -> Decimal:
    """Fincham-Richardson sulfide capacity, with math.sqrt not Decimal.sqrt.

    C_S = (wt% S) * (pO2 / pS2) ** (1/2). The compared number is log10 C_S.
    """

    capacity = weight_percent * math.sqrt(float(p_o2_pa) / float(p_anion_pa))
    return Decimal(str(math.log10(capacity)))


def _charge_balanced_log10_chloride(
    weight_percent: float, p_o2_pa: Decimal, p_cl2_pa: Decimal
) -> Decimal:
    """Charge-balanced chloride capacity, with float powers not Decimal.sqrt.

    C_Cl = (wt% Cl) * fO2**(1/4) / fCl2**(1/2). Fugacity is over 1 bar.
    """

    bar = 1e5
    f_o2 = float(p_o2_pa) / bar
    f_cl2 = float(p_cl2_pa) / bar
    capacity = weight_percent * (f_o2 ** 0.25) / math.sqrt(f_cl2)
    return Decimal(str(math.log10(capacity)))


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
    for formula in ("Cr2O3", "MnO", "TiO2", "P2O5"):
        assert (
            rail_for_quantity(Quantity.DISTRIBUTION_RATIO, species_formula=formula)
            is Rail.REDOX
        )
    assert (
        rail_for_quantity(Quantity.DISTRIBUTION_RATIO, species_formula="SiO2")
        is Rail.MELT_ACTIVITY
    )
    assert no_headline_rail_reason(
        Quantity.DISTRIBUTION_RATIO, species_formula="SiO2"
    ) != "distribution_ratio_not_multivalent"
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
        is RefusalReason.ENGINE_DOES_NOT_MODEL_SULFUR
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
        RefusalReason.ENGINE_DOES_NOT_MODEL_SULFUR.value in key for key in census
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


def test_printed_experiment_pressure_reaches_the_scorer(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    def hook(engine, *, temperature_C, composition_kg, fO2_log, pressure_bar):
        calls.append(
            {
                "engine": engine,
                "pressure_bar": pressure_bar,
                "fO2_log": fO2_log,
                "temperature_C": temperature_C,
            }
        )
        return EquilibriumResult(
            liquid_fraction=1.0,
            phase_compositions={"liquid": {"SiO2": 10.0}},
        )

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", hook
    )
    printed = Decimal("100000")
    root = _write_min_tree(
        tmp_path / "with-pressure", _extract_with_printed_pressure("100000")
    )
    migrated = _with_bulk(_row(migrate(root, write=False), "sample1-sio2-melt"))
    assert migrated.identity.total_pressure_Pa.is_value
    assert migrated.identity.total_pressure_Pa.value == printed
    assert migrated.identity.total_pressure_Pa.is_not_applicable is False
    prediction = predict_with_engine(Engine.ALPHAMELTS, migrated)
    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.value == Decimal("10")
    assert len(calls) == 1
    assert calls[0]["pressure_bar"] == float(printed / Decimal("1e5"))
    assert calls[0]["pressure_bar"] != 1e-6
    assert calls[0]["fO2_log"] == -4.0
    assert calls[0]["fO2_log"] != -9.0

    calls.clear()
    bare = _write_min_tree(tmp_path / "no-pressure", _extract())
    missing = _with_bulk(_row(migrate(bare, write=False), "sample1-sio2-melt"))
    assert missing.identity.total_pressure_Pa.is_unknown
    assert missing.identity.total_pressure_Pa.is_not_applicable is False
    refused = predict_with_engine(Engine.ALPHAMELTS, missing)
    assert refused.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert refused.refusal_detail["input"] == "total_pressure_Pa"
    assert refused.execution.state is ExecutionState.NOT_PROBED
    assert refused.value is None
    assert calls == []


def test_legacy_alignment_axes_match_the_stored_ratchet() -> None:
    payload = yaml.safe_load(
        (REPO_ROOT / DECISION_BAND_RATCHET_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    groups = payload["groups"]
    assert groups
    stored = tuple(groups[0]["group_key"]["axes"])
    assert "phase_role" not in stored
    assert "counter_phase_role" not in stored
    for group in groups:
        assert tuple(group["group_key"]["axes"]) == stored
    assert _alignment_axes(profile_for(F.o2_identity())) == stored
    phase_axes = _alignment_axes(
        profile_for(
            _phase_identity(
                "SiO2",
                Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
                role=PhaseRole.MELT,
            )
        )
    )
    assert phase_axes == stored + ("phase_role", "counter_phase_role")
    ratio_axes = _alignment_axes(
        profile_for(
            _phase_identity(
                "FeO",
                Quantity.DISTRIBUTION_RATIO,
                role=PhaseRole.SLAG,
                counter=PhaseRole.METAL,
            )
        )
    )
    assert "phase_role" in ratio_axes
    assert "counter_phase_role" in ratio_axes
    assert ratio_axes != stored


def _capacity_observation(
    observation_id: str,
    quantity: Quantity,
    formula: str,
    *,
    fO2_Pa: Decimal,
    anion_key: str,
    anion_pa: Decimal,
    relation: str,
    reference: Decimal,
):
    identity = _phase_identity(
        formula,
        quantity,
        role=PhaseRole.MELT,
        subtype=None,
        fO2_Pa=fO2_Pa,
    )
    observation = F.observation(
        observation_id,
        "sample-1",
        identity,
        reference,
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-phase-source",
        derivation=Derivation(
            relation=relation,
            inputs=(),
            parameters=(),
            output_unit="dimensionless",
        ),
    )
    conditions = dict(observation.point_conditions or {})
    conditions[anion_key] = F.located(anion_pa)
    return replace(observation, point_conditions=conditions)


def test_capacity_scores_only_when_the_adapter_declares_the_solute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    from simulator.melt_backend.magemin import MAGEMinBackend
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    assert AlphaMELTSBackend.models_dissolved_sulfur is False
    assert AlphaMELTSBackend.models_dissolved_chloride is False
    assert MAGEMinBackend.models_dissolved_sulfur is False
    assert ThermoEngineBackend.models_dissolved_chloride is False
    monkeypatch.setattr(AlphaMELTSBackend, "models_dissolved_sulfur", True)
    monkeypatch.setattr(ThermoEngineBackend, "models_dissolved_chloride", True)

    calls: list[dict] = []

    def hook(
        engine,
        *,
        temperature_C,
        composition_kg,
        fO2_log,
        pressure_bar,
        solute_fugacity_bar=None,
        solute_fugacity_parameter=None,
    ):
        calls.append(
            {
                "engine": engine,
                "pressure_bar": pressure_bar,
                "fO2_log": fO2_log,
                "solute_fugacity_bar": solute_fugacity_bar,
                "solute_fugacity_parameter": solute_fugacity_parameter,
            }
        )
        element = "S" if solute_fugacity_parameter == "sulfur_fugacity_bar" else "Cl"
        return EquilibriumResult(
            liquid_fraction=1.0,
            phase_compositions={"liquid": {element: 1.0}},
        )

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", hook
    )
    sulfur_oxygen = Decimal("10000")
    sulfur_anion = Decimal("100")
    sulfur = _capacity_observation(
        "log-cs",
        Quantity.SULFIDE_CAPACITY,
        "S",
        fO2_Pa=sulfur_oxygen,
        anion_key="pS2_Pa",
        anion_pa=sulfur_anion,
        relation=SULFIDE_EQUATION,
        reference=Decimal("-2.4"),
    )
    chloride_oxygen = Decimal(str(P_O2_PA))
    chloride_anion = Decimal(str(P_O2_PA))
    chloride = _capacity_observation(
        "log-ccl",
        Quantity.CHLORIDE_CAPACITY,
        "Cl",
        fO2_Pa=chloride_oxygen,
        anion_key="pCl2_Pa",
        anion_pa=chloride_anion,
        relation=CHLORIDE_EQUATION,
        reference=Decimal("-1.2"),
    )
    sulfur_expected = _defining_log10_capacity(1.0, sulfur_oxygen, sulfur_anion)
    chloride_expected = _charge_balanced_log10_chloride(
        1.0, chloride_oxygen, chloride_anion
    )
    assert sulfur_expected == Decimal("1.0")
    assert chloride_expected == Decimal("1.0")

    scored = predict_with_engine(Engine.ALPHAMELTS, sulfur)
    assert scored.execution.state is ExecutionState.PRODUCED
    assert scored.execution.call_evidence == "phase_assemblage"
    assert scored.value == sulfur_expected
    sulfur_numeric, sulfur_reason, _detail = populate_numeric(
        quantity=Quantity.SULFIDE_CAPACITY,
        candidate=scored.value,
        reference=Decimal("-2.4"),
        source_relation=SourceRelation.INDEPENDENT,
        operation_override=metric_operation_for_identity(sulfur.identity),
    )
    assert sulfur_reason is None
    assert sulfur_numeric is not None
    assert sulfur_numeric.operation is MetricOperation.ABSOLUTE
    assert sulfur_numeric.value == sulfur_expected - Decimal("-2.4")

    refused = predict_with_engine(Engine.MAGEMIN, sulfur)
    assert refused.execution.state is ExecutionState.UNSUPPORTED
    assert refused.refusal_reason is RefusalReason.ENGINE_DOES_NOT_MODEL_SULFUR
    assert refused.value is None

    chloride_scored = predict_with_engine(Engine.THERMOENGINE, chloride)
    assert chloride_scored.execution.state is ExecutionState.PRODUCED
    assert chloride_scored.value == chloride_expected
    chloride_refused = predict_with_engine(Engine.ALPHAMELTS, chloride)
    assert (
        chloride_refused.refusal_reason
        is RefusalReason.ENGINE_DOES_NOT_MODEL_CHLORIDE
    )
    assert chloride_refused.value is None
    assert [call["engine"] for call in calls] == [
        Engine.ALPHAMELTS,
        Engine.THERMOENGINE,
    ]
    assert calls[0]["solute_fugacity_parameter"] == "sulfur_fugacity_bar"
    assert calls[0]["solute_fugacity_bar"] == float(sulfur_anion / Decimal("1e5"))
    assert calls[0]["fO2_log"] == -1.0
    assert calls[0]["pressure_bar"] == 1.0
    assert calls[1]["solute_fugacity_parameter"] == "chloride_fugacity_bar"
    assert calls[1]["solute_fugacity_bar"] == float(chloride_anion / Decimal("1e5"))
    assert calls[1]["fO2_log"] == -4.0

    sulfur_residual = Residual(
        key=f"{sulfur.observation_id}::{Engine.MAGEMIN.value}",
        reference=sulfur.observation_id,
        execution=Execution(state=refused.execution.state),
        rail=Rail.MELT_ACTIVITY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=False,
        exclusions=(),
        notices=(),
        refusal=ResidualRefusal(
            reason=refused.refusal_reason,
            detail=dict(refused.refusal_detail),
        ),
    )
    chloride_residual = Residual(
        key=f"{chloride.observation_id}::{Engine.ALPHAMELTS.value}",
        reference=chloride.observation_id,
        execution=Execution(state=chloride_refused.execution.state),
        rail=Rail.MELT_ACTIVITY,
        status=ResidualStatus.REFUSED,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=False,
        exclusions=(),
        notices=(),
        refusal=ResidualRefusal(
            reason=chloride_refused.refusal_reason,
            detail=dict(chloride_refused.refusal_detail),
        ),
    )
    assert _residual_engine_observable(residual_to_plain(sulfur_residual)) is True
    assert _residual_engine_observable(residual_to_plain(chloride_residual)) is True
    census = refusal_census((sulfur_residual, chloride_residual))
    assert any(
        RefusalReason.ENGINE_DOES_NOT_MODEL_SULFUR.value in key for key in census
    )
    assert any(
        RefusalReason.ENGINE_DOES_NOT_MODEL_CHLORIDE.value in key for key in census
    )


def test_chloride_capacity_sanity_point_is_log10_one() -> None:
    """1 wt% Cl at fO2 = fCl2 = 10 Pa is log10 C_Cl = 1. Same law, any spelling."""

    from simulator.battery.score import (
        _log10_printed_capacity,
        _recognised_capacity_law,
    )

    spellings = (
        CHLORIDE_EQUATION,
        "log10 C_Cl = log10(wt% Cl) + (1/4) log10(fO2) - (1/2) log10(fCl2)",
        "C_Cl = (wt% Cl) / sqrt(f(Cl2)) * f(O2)^(1/4), Thomas and Wood 2021 eq 2",
        "C_Cl = (wt% Cl)·fO2^(1/4)/fCl2^(1/2)",
        "C_Cl = (wt% Cl) * fO2**(0.25) / fCl2**(0.5)",
    )
    independent = _charge_balanced_log10_chloride(
        1.0, Decimal("10"), Decimal("10")
    )
    assert independent == Decimal("1.0")
    laws = []
    for relation in spellings:
        law = _recognised_capacity_law(Quantity.CHLORIDE_CAPACITY, relation)
        assert law is not None
        assert law.identity == "chloride_charge_balance"
        scored = _log10_printed_capacity(
            Decimal("1"), Decimal("10"), Decimal("10"), law
        )
        assert scored == independent
        laws.append(law)
    assert all(law is laws[0] for law in laws)
    assert (
        _recognised_capacity_law(
            Quantity.CHLORIDE_CAPACITY, SULFIDE_EXPONENT_ON_CHLORIDE
        )
        is None
    )
    assert _recognised_capacity_law(Quantity.SULFIDE_CAPACITY, CHLORIDE_EQUATION) is None
    sulfide = _recognised_capacity_law(Quantity.SULFIDE_CAPACITY, SULFIDE_EQUATION)
    assert sulfide is not None
    assert sulfide.identity == "fincham_richardson"
    assert sulfide is not laws[0]


def test_unknown_capacity_equation_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    monkeypatch.setattr(AlphaMELTSBackend, "models_dissolved_sulfur", True)
    monkeypatch.setattr(ThermoEngineBackend, "models_dissolved_chloride", True)
    calls: list[Engine] = []

    def hook(
        engine,
        *,
        temperature_C,
        composition_kg,
        fO2_log,
        pressure_bar,
        solute_fugacity_bar=None,
        solute_fugacity_parameter=None,
    ):
        calls.append(engine)
        return EquilibriumResult(
            liquid_fraction=1.0,
            phase_compositions={"liquid": {"S": 1.0, "Cl": 1.0}},
        )

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", hook
    )
    chloride_rows = (
        SULFIDE_EXPONENT_ON_CHLORIDE,
        "C_Cl = (wt% Cl) * fO2 / fCl2",
        "log CCl = 1.601 + (4470 XCa) / T",
    )
    for relation in chloride_rows:
        observation = _capacity_observation(
            "unknown-ccl",
            Quantity.CHLORIDE_CAPACITY,
            "Cl",
            fO2_Pa=Decimal("10"),
            anion_key="pCl2_Pa",
            anion_pa=Decimal("10"),
            relation=relation,
            reference=Decimal("-1.2"),
        )
        refused = predict_with_engine(Engine.THERMOENGINE, observation)
        assert refused.value is None
        assert refused.authority is Authority.REFUSED
        assert refused.execution.state is ExecutionState.UNSUPPORTED
        assert (
            refused.refusal_reason
            is RefusalReason.CAPACITY_DEFINITION_NOT_IMPLEMENTED
        )
        assert refused.refusal_detail["input"] == "defining_equation"
        assert (
            refused.refusal_detail["reason"]
            == RefusalReason.CAPACITY_DEFINITION_NOT_IMPLEMENTED.value
        )
    sulfide = _capacity_observation(
        "unknown-cs",
        Quantity.SULFIDE_CAPACITY,
        "S",
        fO2_Pa=Decimal("10000"),
        anion_key="pS2_Pa",
        anion_pa=Decimal("100"),
        relation="C_S = (wt% S) * fO2 / fS2",
        reference=Decimal("-2.4"),
    )
    sulfide_refused = predict_with_engine(Engine.ALPHAMELTS, sulfide)
    assert sulfide_refused.value is None
    assert (
        sulfide_refused.refusal_reason
        is RefusalReason.CAPACITY_DEFINITION_NOT_IMPLEMENTED
    )
    assert calls == []
