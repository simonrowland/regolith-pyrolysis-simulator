"""A gap's missing list names only inputs that are absent for that context.

The fallback OR-set used to be stamped verbatim whenever no route fired.
Route selection is unchanged: a gap still exists, it just stops naming
inputs that already resolved.
"""

from dataclasses import replace
from decimal import Decimal

from simulator.battery.records import ApparatusGeometry, Sample, Value
from simulator.battery.waypoints import (
    GapReason,
    ReadinessStatus,
    consumer_readiness,
    g2,
    g3,
    oxygen_condition,
    pressure_boundary,
)
from tests.battery import factories
from tests.battery.test_waypoints import _bench


_OXYGEN_OR = (
    "fO2_log",
    "experiment.fO2_control",
    "temperature_K",
    "total_pressure_Pa",
)


def _unknown():
    return factories.Located(factories.State.unknown("not_published"))


def test_oxygen_gap_omits_temperature_and_pressure_that_resolved() -> None:
    experiment = replace(factories.kems_experiment(), fO2_control=None)
    result = oxygen_condition(experiment, _bench())
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.reason is GapReason.MISSING_EVIDENCE
    assert result.absence.missing == ("fO2_log", "experiment.fO2_control")
    assert "temperature_K" not in result.absence.missing
    assert "total_pressure_Pa" not in result.absence.missing


def test_oxygen_gap_keeps_the_full_set_when_nothing_resolved() -> None:
    base = factories.kems_experiment()
    experiment = replace(
        base,
        fO2_control=None,
        thermal_schedule=None,
        conditions={"temperature_K": _unknown()},
        pressure_environment=replace(
            base.pressure_environment, total_pressure_Pa=_unknown()
        ),
    )
    result = oxygen_condition(experiment, _bench())
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.missing == _OXYGEN_OR


def test_oxygen_route_still_wins_over_the_gap_text() -> None:
    experiment = factories.kems_experiment()
    observation = replace(
        factories.observation(
            "obs", experiment.experiment_id, factories.o2_identity(), 1
        ),
        point_conditions={"fO2_log": factories.located(Decimal("-9"))},
    )
    result = oxygen_condition(experiment, _bench(), observation)
    assert result.selected is not None
    assert result.selected.route == "observation_fO2_log"
    assert result.absence is None


def test_reporting_a_shorter_gap_does_not_make_the_consumer_ready() -> None:
    experiment = replace(factories.kems_experiment(), fO2_control=None)
    readiness = consumer_readiness(experiment, _bench())
    engine = next(
        item
        for item in readiness
        if item.consumer == "engine_point" and item.engine == "internal-analytical"
    )
    assert engine.status is ReadinessStatus.GAP
    oxygen = next(gap for gap in engine.gaps if gap.waypoint == "oxygen_condition")
    assert "temperature_K" not in oxygen.missing
    assert "total_pressure_Pa" not in oxygen.missing


def test_pressure_boundary_omits_a_present_pump_speed() -> None:
    base = factories.kems_experiment()
    experiment = replace(
        base,
        pressure_environment=replace(
            base.pressure_environment, total_pressure_Pa=_unknown()
        ),
    )
    result = pressure_boundary(
        experiment,
        _bench(pumping_speed_m3_s=factories.located(Value.point_of("1e-2"))),
    )
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.missing == (
        "experiment.pressure_environment.total_pressure_Pa",
        "bench.other_facts.gas_load_Pa_m3_s",
    )


def test_g3_names_only_the_absent_factor() -> None:
    geometry = ApparatusGeometry(
        cell_internal_dimensions={
            key: factories.located(Value.point_of("0.1"))
            for key in ("length_m", "width_m", "height_m")
        }
    )
    result = g3(factories.kems_experiment(), _bench(geometry=geometry))
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.missing == ("effective_escape_area",)


def test_g3_full_set_when_both_factors_are_absent() -> None:
    result = g3(factories.kems_experiment(), _bench())
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.missing == ("relevant_volume", "effective_escape_area")


def test_g2_omits_a_resolved_escape_area() -> None:
    geometry = ApparatusGeometry(
        orifice_area_m2=factories.located(Value.point_of("1e-6"))
    )
    result = g2(
        replace(factories.kems_experiment(), sample=Sample()),
        _bench(geometry=geometry),
    )
    assert result.selected is None
    assert result.absence is not None
    assert "effective_escape_area" not in result.absence.missing
    assert result.absence.missing == (
        "experiment.sample.surface_area_m2",
        "experiment.conditions.evaporation_alpha",
    )
