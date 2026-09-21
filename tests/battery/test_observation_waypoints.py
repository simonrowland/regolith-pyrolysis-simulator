from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import AmountBasis, BenchIdentityBasis, ValueKind
from simulator.battery.records import Bench, BenchIdentity, Composition, Located, Sample, State, Value
from simulator.battery.waypoints import ReadinessStatus, WaypointAuthority, consumer_readiness, pressure_boundary, thermal_path
from tests.battery import factories


@pytest.mark.parametrize("key,route", [("temperature_K", thermal_path), ("total_pressure_Pa", pressure_boundary)])
@pytest.mark.parametrize("value", [
    Value.point_of("1500"),
    Value(ValueKind.INTERVAL, interval_low=Decimal("1400"), interval_high=Decimal("1600")),
    Value(ValueKind.BOUND, bound_operator="<", bound_value=Decimal("1600")),
])
def test_observation_route_preserves_print_form_and_provenance(key, route, value):
    experiment = factories.kems_experiment()
    bench = Bench("bench", "work", BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK))
    observation = replace(
        factories.observation("point-1", experiment.experiment_id, factories.o2_identity(), 1),
        point_conditions={key: factories.located(value)},
    )
    selected = route(experiment, bench, observation).selected
    assert selected.value == value
    assert selected.authority is WaypointAuthority.PRINTED
    assert selected.route == f"observation_{key}"
    assert selected.inputs == (f"observation[point-1].point_conditions.{key}",)
    unknown = replace(observation, point_conditions={key: Located(State.unknown("not printed"))})
    assert all(item.route != f"observation_{key}" for item in route(experiment, bench, unknown).routes)
    with pytest.raises(ValueError, match="different experiment"):
        route(experiment, bench, replace(observation, experiment_id="different"))


def test_engine_point_uses_observation_temperature_without_schedule():
    base = factories.kems_experiment()
    experiment = replace(
        base,
        conditions={"temperature_K": Located(State.unknown("per observation"))}, thermal_schedule=None,
        pressure_environment=replace(base.pressure_environment, total_pressure_Pa=Located(State.unknown("per observation"))),
        sample=Sample(initial_composition=factories.located(Composition(
            "formula", (("MgO", Decimal(1)), ("SiO2", Decimal(1))), AmountBasis.MOL_INVENTORY,
        ))),
    )
    bench = Bench("bench", "work", BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK))
    observation = replace(
        factories.observation("point-1", experiment.experiment_id, factories.o2_identity(), 1),
        point_conditions={
            "temperature_K": factories.located(Decimal("1500")),
            "total_pressure_Pa": factories.located(Decimal("0.01")),
            "fO2_log": factories.located(Decimal("-9")),
        },
    )
    rows = consumer_readiness(experiment, bench, observation)
    engines = [row for row in rows if row.consumer == "engine_point"]
    assert len(engines) == 8
    assert all(row.status is ReadinessStatus.READY for row in engines)
    assert all(row.status is not ReadinessStatus.READY for row in rows if row.consumer != "engine_point")
    assert all(row.status is ReadinessStatus.GAP for row in consumer_readiness(experiment, bench) if row.consumer == "engine_point")
