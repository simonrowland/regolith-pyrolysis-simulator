from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import AmountBasis, BenchIdentityBasis, ValueKind
from simulator.battery.records import (
    Bench,
    BenchIdentity,
    Composition,
    Located,
    Sample,
    State,
    Value,
)
from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.consumer_inputs import collect_consumer_inputs
from simulator.battery.waypoints import (
    GapReason,
    ReadinessStatus,
    WaypointAuthority,
    consumer_readiness,
    oxygen_condition,
    pressure_boundary,
    thermal_path,
)
from tests.battery import factories


def test_sossi_2020_table_s4_oxygen_values_migrate_to_all_observation_waypoints():
    extract = Path(__file__).resolve().parents[2] / "data/literature/extracts/sossi-2020-magma-ocean-redox.yaml"
    migrator = factories.migrate_one_extract(REPO_ROOT, extract)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 12
    assert all(
        observation.derived_from
        and observation.derived_from == observation.derivation.inputs
        for observation in result.observations.values()
    )
    assert not result.validation.hard_issues  # b-882 supplies the measured-reduction lineage
    expected_by_observation = {
        "sossi_2020_per_f_o2_1_xanes": "0.00",
        "sossi_2020_per_f_o2_2_xanes": "-2.49",
        "sossi_2020_per_f_o2_3_xanes": "-2.53",
        "sossi_2020_per_f_o2_6_xanes": "-2.58",
        "sossi_2020_per_ts1_xanes": "-2.72",
        "sossi_2020_per_f_o2_7_xanes": "-2.72",
        "sossi_2020_per_ts2_xanes": "-2.72",
        "sossi_2020_per_f_o2_8_xanes": "-3.00",
        "sossi_2020_per_f_o2_9_xanes": "-3.92",
        "sossi_2020_per_f_o2_10_xanes": "-5.32",
        "sossi_2020_per_f_o2_11_xanes": "-6.74",
        "sossi_2020_per_f_o2_12_xanes": "-7.86",
    }
    expected_by_observation = {
        f"sossi-2020-magma-ocean-redox::{observation_id}": value
        for observation_id, value in expected_by_observation.items()
    }
    selected_by_observation = {}
    for observation in result.observations.values():
        experiment = result.experiments[observation.experiment_id]
        bench = result.benches[experiment.bench_id]
        waypoint = oxygen_condition(experiment, bench, observation)
        assert waypoint.selected is not None, observation.observation_id
        assert waypoint.selected.authority is WaypointAuthority.PRINTED
        assert observation.point_conditions["fO2_log"].locator.table == "S4"
        selected_by_observation[observation.observation_id] = str(waypoint.selected.value.point)
    assert selected_by_observation == expected_by_observation


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
    assert len(engines) == 7
    assert all(row.status is ReadinessStatus.READY for row in engines)
    assert all(row.status is not ReadinessStatus.READY for row in rows if row.consumer != "engine_point")
    assert all(row.status is ReadinessStatus.GAP for row in consumer_readiness(experiment, bench) if row.consumer == "engine_point")


@pytest.mark.parametrize(
    "source_id",
    ("cottrell-2009-iron-xanes", "jugo-2010-sulfur-xanes"),
)
def test_readiness_keeps_raw_printed_composition_out_of_sample_charge(source_id):
    extract = REPO_ROOT / "data/literature/extracts" / f"{source_id}.yaml"
    migrator = factories.migrate_one_extract(REPO_ROOT, extract)
    migrator.finalize()
    observation = next(
        obs
        for obs in migrator.result.observations.values()
        if obs.source_id == source_id
        and obs.point_conditions
        and obs.point_conditions.get("composition") is not None
    )
    composition = observation.point_conditions["composition"]
    assert composition.state.is_value and isinstance(composition.state.value, dict)
    experiment = migrator.result.experiments[observation.experiment_id]
    bench = Bench(
        "work::bench::one",
        "work",
        BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
    )

    readiness = consumer_readiness(experiment, bench, observation)
    assert readiness
    inputs = collect_consumer_inputs(experiment, bench, observation)
    composition_waypoint = inputs.waypoints["normalized_composition"]
    assert composition_waypoint.absence is not None
    assert composition_waypoint.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    assert composition_waypoint.absence.missing == (
        f"observation[{observation.observation_id}].point_conditions.composition",
    )

    sample_composition = factories.located(
        Composition("formula", (("MgO", Decimal("1")),), AmountBasis.MOL_INVENTORY)
    )
    experiment_with_sample = replace(
        experiment,
        sample=replace(experiment.sample, initial_composition=sample_composition),
    )
    fallback_inputs = collect_consumer_inputs(experiment_with_sample, bench, observation)
    assert fallback_inputs.charges["MgO"].selected is not None
    assert fallback_inputs.charges["MgO"].selected.inputs == (
        "experiment.sample.initial_composition",
    )
