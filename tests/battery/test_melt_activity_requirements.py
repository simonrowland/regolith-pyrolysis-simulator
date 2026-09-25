"""Melt-activity observations are not held to the engine_point contract.

An activity or activity coefficient needs a normalized composition and a
point temperature. Oxygen is required only when the composition contains
iron. Pressure is not an input, and a missing required input is refused
rather than defaulted.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.consumer_inputs import REQUIREMENTS, collect_consumer_inputs
from simulator.battery.enums import (
    AmountBasis,
    BenchIdentityBasis,
    Phase,
    Quantity,
    ReferenceStateConvention,
    ValueKind,
)
from simulator.battery.generators import melt_activity_requests
from simulator.battery.identity import Identity
from simulator.battery.records import (
    Bench,
    BenchIdentity,
    Composition,
    Sample,
    Species,
    StandardState,
    State,
    Value,
)
from simulator.battery.waypoints import (
    MELT_ACTIVITY_ENGINES,
    GapReason,
    ReadinessStatus,
    consumer_readiness,
)
from tests.battery import factories as f


def _composition(*pairs: tuple[str, str]) -> Composition:
    return Composition(
        "oxides",
        tuple((name, Decimal(amount)) for name, amount in pairs),
        AmountBasis.MOLE_FRACTION,
    )


def _reference_state() -> State:
    return State.of(StandardState(
        ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
        Species("Na2O", Phase.L),
        "oxide",
        Decimal("1"),
    ))


def _activity_identity(composition: Composition | None, *, known_reference: bool = True) -> Identity:
    composition_state = (
        State.unknown("no composition mapped from source")
        if composition is None
        else State.of(composition)
    )
    return Identity(
        quantity=Quantity.ACTIVITY,
        species=Species("Na2O", Phase.L),
        per=State.unknown("test"),
        temperature_K=State.of(Decimal("1473.15")),
        reference_state=_reference_state() if known_reference else State.unknown("qualifier does not name a reference_state"),
        composition=composition_state,
        fO2_Pa=State.unknown("test"),
        total_pressure_Pa=State.unknown("test"),
        subtype=State.not_applicable("activity does not use subtype"),
    )


def _case(
    *,
    composition: Composition | None,
    identity_composition: Composition | None = None,
    oxygen: Decimal | None = None,
    pressure: Value | None = None,
    temperature: Value | None = None,
    quantity: Quantity = Quantity.ACTIVITY,
    known_reference: bool = True,
):
    """One activity row. Pressure, when set, is an interval so engine_point cannot use it."""
    sample_composition = composition
    experiment = replace(
        f.kems_experiment(),
        sample=Sample(
            initial_composition=None if sample_composition is None else f.located(sample_composition),
        ),
        thermal_schedule=None,
        pressure_environment=replace(
            f.kems_experiment().pressure_environment,
            total_pressure_Pa=f.located(
                pressure if pressure is not None else Value(
                    ValueKind.INTERVAL, interval_low=Decimal("1"), interval_high=Decimal("10"),
                )
            ),
        ),
    )
    bench = Bench(
        "bench", "work-1", BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
    )
    point = {
        "temperature_K": f.located(
            temperature if temperature is not None else Decimal("1473.15")
        )
    }
    if oxygen is not None:
        point["fO2_log"] = f.located(oxygen)
    identity = _activity_identity(
        identity_composition if identity_composition is not None else composition,
        known_reference=known_reference,
    )
    if quantity is not Quantity.ACTIVITY:
        identity = replace(identity, quantity=State.of(quantity))
    observation = replace(
        f.observation("obs", experiment.experiment_id, identity, Decimal("1e-7")),
        point_conditions=point,
        source_id="ts1985",
    )
    return experiment, bench, observation


def _melt(experiment, bench, observation):
    return melt_activity_requests(collect_consumer_inputs(experiment, bench, observation))


def test_requirement_tuple_does_not_include_pressure_or_oxygen():
    assert REQUIREMENTS["melt_activity"] == ("normalized_composition", "temperature_K")
    assert "pressure_boundary" not in REQUIREMENTS["melt_activity"]
    assert "oxygen_condition" not in REQUIREMENTS["melt_activity"]


def test_no_iron_without_oxygen_is_ready_and_engine_point_stays_gap():
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
    )
    results = _melt(experiment, bench, observation)
    assert [item.readiness.engine for item in results] == list(MELT_ACTIVITY_ENGINES)
    assert all(item.readiness.status is ReadinessStatus.READY for item in results)
    assert all(item.readiness.gaps == () for item in results)
    payload = results[0].payload
    assert payload["engine"] == "alphamelts"
    assert payload["temperature_C"] == pytest.approx(1473.15 - 273.15)
    assert payload["composition_mol"]["Na2O"] == pytest.approx(0.4)
    assert payload["composition_mol"]["SiO2"] == pytest.approx(0.6)
    assert "fO2_log" not in payload
    assert "pressure_bar" not in payload
    assert results[0].provenance["output_routes"]["pressure_bar"]["authority"] == "not_an_input"
    engine_point = [
        item for item in consumer_readiness(experiment, bench, observation)
        if item.consumer == "engine_point"
    ]
    assert engine_point
    assert all(item.status is ReadinessStatus.GAP for item in engine_point)
    assert all(
        any(gap.waypoint == "oxygen_condition" for gap in item.gaps)
        for item in engine_point
    )


def test_iron_without_oxygen_is_a_typed_refusal():
    experiment, bench, observation = _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    assert all(item.readiness.status is ReadinessStatus.GAP for item in results)
    gaps = results[0].readiness.gaps
    oxygen = [gap for gap in gaps if gap.waypoint == "oxygen_condition"]
    assert len(oxygen) == 1
    assert oxygen[0].reason is GapReason.MISSING_EVIDENCE
    assert "FeO" in oxygen[0].missing
    assert not any(gap.waypoint == "pressure_boundary" for gap in gaps)


def test_iron_with_point_oxygen_is_ready():
    experiment, bench, observation = _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
        oxygen=Decimal("-9"),
    )
    results = _melt(experiment, bench, observation)
    assert all(item.readiness.status is ReadinessStatus.READY for item in results)
    assert results[0].payload["fO2_log"] == pytest.approx(-9)
    assert "pressure_bar" not in results[0].payload


def test_zero_iron_does_not_demand_oxygen():
    experiment, bench, observation = _case(
        composition=_composition(("FeO", "0"), ("Na2O", "0.4"), ("SiO2", "0.6")),
    )
    results = _melt(experiment, bench, observation)
    assert all(item.readiness.status is ReadinessStatus.READY for item in results)
    assert "fO2_log" not in results[0].payload


def test_missing_composition_is_refused_and_not_defaulted():
    experiment, bench, observation = _case(composition=None, identity_composition=None)
    # _case copies composition into the identity. Pass unknown on both sides.
    observation = replace(
        observation,
        identity=replace(
            observation.identity,
            composition=State.unknown("no composition mapped from source"),
        ),
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    assert all(item.readiness.status is ReadinessStatus.GAP for item in results)
    assert results[0].readiness.gaps[0].waypoint == "normalized_composition"
    assert results[0].readiness.gaps[0].reason is GapReason.MISSING_EVIDENCE
    assert not any(gap.waypoint == "oxygen_condition" for gap in results[0].readiness.gaps)


def test_identity_composition_outranks_a_different_sample_bulk():
    sample = _composition(("K2O", "0.333"), ("SiO2", "0.667"))
    row = _composition(("K2O", "0.25"), ("SiO2", "0.75"))
    experiment, bench, observation = _case(composition=sample, identity_composition=row)
    results = _melt(experiment, bench, observation)
    assert results[0].readiness.status is ReadinessStatus.READY
    assert results[0].payload["composition_mol"]["K2O"] == pytest.approx(0.25)
    assert results[0].provenance["output_routes"]["composition_mol"]["route"] == "identity_composition"
    engine_point_inputs = collect_consumer_inputs(experiment, bench, observation)
    sample_route = engine_point_inputs.waypoints["normalized_composition"].selected
    assert sample_route.route == "normalized_initial_composition"
    assert float(sample_route.value["K2O"]) == pytest.approx(0.333)


def test_activity_coefficient_uses_the_same_contract():
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        quantity=Quantity.ACTIVITY_COEFFICIENT,
    )
    results = _melt(experiment, bench, observation)
    assert all(item.readiness.status is ReadinessStatus.READY for item in results)


def test_other_quantities_are_not_applicable_and_do_not_loosen_engine_point():
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
    )
    observation = replace(observation, identity=f.o2_identity())
    results = _melt(experiment, bench, observation)
    assert all(item.readiness.status is ReadinessStatus.NOT_APPLICABLE for item in results)
    assert all(item.payload is None for item in results)
    assert all(item.readiness.gaps == () for item in results)
    engine_point = [
        item for item in consumer_readiness(experiment, bench, observation)
        if item.consumer == "engine_point" and item.engine == "imcc_sf04"
    ]
    assert engine_point[0].status is not ReadinessStatus.READY


def test_unknown_reference_state_is_not_consumed():
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        known_reference=False,
    )
    results = _melt(experiment, bench, observation)
    assert all(item.payload is None for item in results)
    assert all(item.readiness.status is ReadinessStatus.NOT_APPLICABLE for item in results)
    assert results[0].readiness.gaps[0].waypoint == "reference_state"
    assert results[0].readiness.gaps[0].reason is GapReason.MISSING_EVIDENCE


def test_temperature_interval_is_refused():
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        temperature=Value(ValueKind.INTERVAL, interval_low=Decimal("1400"), interval_high=Decimal("1500")),
    )
    results = _melt(experiment, bench, observation)
    assert results[0].readiness.status is ReadinessStatus.GAP
    assert any(
        gap.waypoint == "temperature_K" and gap.reason is GapReason.INTERVAL_NEEDS_POINT
        for gap in results[0].readiness.gaps
    )
    assert results[0].payload is None
