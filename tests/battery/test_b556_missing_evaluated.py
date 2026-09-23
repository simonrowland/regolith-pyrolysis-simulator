"""A gap's missing list names only inputs that are absent for that context.

The fallback OR-set used to be stamped verbatim whenever no route fired.
Route selection is unchanged: a gap still exists, it just stops naming
inputs that already resolved.
"""

from dataclasses import replace
from decimal import Decimal

from simulator.battery.enums import AmountBasis, FO2Channel, ValueKind
from simulator.battery.records import (
    ApparatusGeometry,
    Composition,
    FO2Control,
    Sample,
    ThermalRamp,
    ThermalSchedule,
    ThermalSetpoint,
    Value,
)
from simulator.battery.waypoints import (
    GapReason,
    ReadinessStatus,
    charge_moles_by_species,
    consumer_readiness,
    g1,
    g2,
    g3,
    oxygen_condition,
    pressure_boundary,
    relevant_volume,
    thermal_path,
)
from tests.battery import factories
from tests.battery.test_waypoints import _bench, _charge


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


_THERMAL_OR = (
    "experiment.thermal_schedule",
    "experiment.conditions.temperature_K",
)


def _categorical(text: str) -> Value:
    return Value(ValueKind.CATEGORICAL, categorical=text)


def _incomplete_hold() -> ThermalSchedule:
    return ThermalSchedule(
        setpoints_and_holds=(
            ThermalSetpoint(
                temperature_K=_unknown(),
                hold_duration_s=factories.located(Value.point_of("600")),
            ),
        )
    )


def test_located_but_unusable_thermal_members_are_both_named() -> None:
    """A hold without a usable temperature, or an interval conditions
    temperature with no schedule, must not drop either OR member."""
    bench = _bench()
    base = factories.kems_experiment()
    hold = thermal_path(
        replace(
            base,
            thermal_schedule=_incomplete_hold(),
            conditions={"temperature_K": factories.located(_categorical("hot"))},
        ),
        bench,
    )
    assert hold.selected is None
    assert hold.absence is not None
    assert hold.absence.reason is GapReason.MISSING_EVIDENCE
    assert hold.absence.missing == _THERMAL_OR
    absent_conditions = thermal_path(
        replace(
            base,
            thermal_schedule=_incomplete_hold(),
            conditions={"temperature_K": _unknown()},
        ),
        bench,
    )
    assert absent_conditions.absence is not None
    assert absent_conditions.absence.missing == _THERMAL_OR
    interval = thermal_path(
        replace(
            base,
            thermal_schedule=None,
            conditions={
                "temperature_K": factories.located(
                    Value(
                        ValueKind.INTERVAL,
                        interval_low=Decimal("1400"),
                        interval_high=Decimal("1500"),
                    )
                )
            },
        ),
        bench,
    )
    assert interval.selected is None
    assert interval.absence is not None
    assert interval.absence.missing == _THERMAL_OR
    ramp = thermal_path(
        replace(
            base,
            thermal_schedule=ThermalSchedule(
                ramps=(ThermalRamp(rate_K_s=factories.located(Value.point_of("2"))),)
            ),
            conditions={"temperature_K": _unknown()},
        ),
        bench,
    )
    assert ramp.selected is None
    assert ramp.absence is not None
    assert ramp.absence.missing == _THERMAL_OR


def test_empty_thermal_gap_is_not_surfaced_to_the_engine() -> None:
    experiment = replace(
        factories.kems_experiment(),
        thermal_schedule=_incomplete_hold(),
        conditions={"temperature_K": factories.located(_categorical("hot"))},
    )
    readiness = consumer_readiness(experiment, _bench())
    engine = next(
        item
        for item in readiness
        if item.consumer == "engine_point" and item.engine == "internal-analytical"
    )
    assert engine.status is ReadinessStatus.GAP
    temperature = next(gap for gap in engine.gaps if gap.waypoint == "temperature_K")
    assert temperature.reason is GapReason.MISSING_EVIDENCE
    assert temperature.missing == _THERMAL_OR


def test_conditions_fo2_log_is_not_oxygen_evidence() -> None:
    base = factories.kems_experiment()
    result = oxygen_condition(
        replace(
            base,
            fO2_control=None,
            conditions={
                **base.conditions,
                "fO2_log": factories.located(Value.point_of("-10")),
            },
        ),
        _bench(),
    )
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.missing == ("fO2_log", "experiment.fO2_control")


def test_unusable_fo2_control_stays_in_the_gap() -> None:
    base = factories.kems_experiment()
    bench = _bench()
    zero = oxygen_condition(
        replace(
            base,
            fO2_control=FO2Control(
                channel=factories.State.of(FO2Channel.COMMANDED),
                oxygen_partial_pressure_Pa=factories.located(Value.point_of("0")),
            ),
        ),
        bench,
    )
    categorical = oxygen_condition(
        replace(
            base,
            fO2_control=FO2Control(
                channel=factories.State.of(FO2Channel.COMMANDED),
                oxygen_partial_pressure_Pa=factories.located(_categorical("low")),
            ),
        ),
        bench,
    )
    unknown_buffer = oxygen_condition(
        replace(
            base,
            fO2_control=FO2Control(
                channel=factories.State.of(FO2Channel.BUFFER),
                buffer=factories.located("NOT_A_REAL_BUFFER"),
            ),
        ),
        bench,
    )
    for result in (zero, categorical, unknown_buffer):
        assert result.selected is None
        assert result.absence is not None
        assert "experiment.fO2_control" in result.absence.missing
        assert "fO2_log" in result.absence.missing


def test_published_buffer_stays_present_when_temperature_is_absent() -> None:
    base = factories.kems_experiment()
    result = oxygen_condition(
        replace(
            base,
            fO2_control=FO2Control(
                channel=factories.State.of(FO2Channel.BUFFER),
                buffer=factories.located("IW"),
            ),
            thermal_schedule=None,
            conditions={"temperature_K": _unknown()},
        ),
        _bench(),
    )
    assert result.selected is None
    assert result.absence is not None
    assert "experiment.fO2_control" not in result.absence.missing
    assert result.absence.missing == ("fO2_log", "temperature_K")


def test_categorical_mass_does_not_emit_an_empty_charge_gap() -> None:
    sample = Sample(
        mass_kg=factories.located(_categorical("a few mg")),
        initial_composition=factories.located(
            Composition(
                "formula",
                (("Mg2SiO4", Decimal("1")),),
                AmountBasis.MOLE_FRACTION,
            )
        ),
    )
    result = charge_moles_by_species(
        replace(factories.kems_experiment(), sample=sample), _bench()
    )
    assert not result
    assert result.absence is not None
    assert result.absence.reason is GapReason.MISSING_EVIDENCE
    assert result.absence.missing == (
        "experiment.sample.mass_kg",
        "experiment.sample.initial_composition",
    )


def test_g1_names_every_input_when_thermal_is_not_a_series() -> None:
    ramp = ThermalRamp(
        rate_K_s=factories.located(Value.point_of("2")),
        start_temperature_K=factories.located(
            Value(
                ValueKind.INTERVAL,
                interval_low=Decimal("290"),
                interval_high=Decimal("310"),
            )
        ),
        end_temperature_K=factories.located(Value.point_of("1500")),
    )
    experiment = replace(
        factories.kems_experiment(),
        sample=_charge(),
        thermal_schedule=ThermalSchedule(ramps=(ramp,)),
    )
    bench = _bench(
        geometry=ApparatusGeometry(
            cell_internal_dimensions={
                key: factories.located(Value.point_of("0.1"))
                for key in ("length_m", "width_m", "height_m")
            }
        )
    )
    result = g1(experiment, bench, Value.point_of("100"))
    assert not result
    assert result.absence is not None
    assert result.absence.missing == (
        "charge_moles_by_species",
        "p_sat",
        "relevant_volume",
        "thermal_path",
    )


def test_categorical_cell_dimensions_do_not_emit_an_empty_volume_gap() -> None:
    categorical = factories.located(_categorical("small"))
    geometry = ApparatusGeometry(
        cell_internal_dimensions={
            key: categorical for key in ("length_m", "width_m", "height_m")
        }
    )
    result = relevant_volume(factories.kems_experiment(), _bench(geometry=geometry))
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.missing == ("bench.geometry.cell_internal_dimensions",)
