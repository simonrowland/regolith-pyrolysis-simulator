from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from simulator.battery.enums import AmountBasis, BenchIdentityBasis, MethodToken, ValueKind
from simulator.battery.records import (
    ApparatusGeometry,
    Bench,
    BenchFact,
    BenchIdentity,
    Composition,
    Sample,
    ThermalSchedule,
    ThermalRamp,
    ThermalSetpoint,
    Value,
)
from simulator.battery.waypoints import (
    GapReason,
    ReadinessStatus,
    WaypointAuthority,
    WaypointFlag,
    charge_moles_by_species,
    consumer_readiness,
    effective_escape_area,
    g1,
    g2,
    g3,
    oxygen_condition,
    pressure_boundary,
    relevant_volume,
    thermal_path,
)
from tests.battery import factories


def _bench(**kwargs: object) -> Bench:
    return Bench(
        id="work-1::bench::one",
        work_id="work-1",
        identity=BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
        **kwargs,
    )


def _schedule() -> ThermalSchedule:
    return ThermalSchedule(
        setpoints_and_holds=(
            ThermalSetpoint(
                factories.located(Value.point_of("1500")),
                factories.located(Value.point_of("600")),
            ),
        )
    )


def _charge(single: bool = True) -> Sample:
    components = (("Mg2SiO4", Decimal("1")),)
    if not single:
        components = (("Mg2SiO4", Decimal("0.5")), ("SiO2", Decimal("0.5")))
    return Sample(
        mass_kg=factories.located(Value.point_of("0.0001")),
        initial_composition=factories.located(
            Composition("formula", components, AmountBasis.MOLE_FRACTION)
        ),
        surface_area_m2=factories.located(Value.point_of("1e-4")),
    )


def test_charge_moles_sanity_and_typed_absence() -> None:
    experiment = replace(factories.kems_experiment(), sample=_charge())
    result = charge_moles_by_species(experiment, _bench())
    assert result.absence is None
    assert result["Mg2SiO4"].selected is not None
    assert result["Mg2SiO4"].selected.value.point == Decimal(
        "0.0007107775195286123490486242901"
    )
    missing = charge_moles_by_species(
        replace(experiment, sample=Sample()), _bench()
    )
    assert missing.absence is not None
    assert missing.absence.reason is GapReason.MISSING_EVIDENCE


def test_knudsen_uses_cell_volume_and_preserves_interval() -> None:
    geometry = ApparatusGeometry(
        cell_internal_dimensions={
            "length_m": factories.located(
                Value(ValueKind.INTERVAL, interval_low=Decimal("0.01"), interval_high=Decimal("0.02"))
            ),
            "width_m": factories.located(Value.point_of("0.01")),
            "height_m": factories.located(Value.point_of("0.01")),
        },
        chamber_volume_m3=factories.located(Value.point_of("9")),
    )
    result = relevant_volume(factories.kems_experiment(), _bench(geometry=geometry))
    assert result.selected is not None
    assert result.selected.route == "cell_internal_dimensions"
    assert result.selected.value.kind is ValueKind.INTERVAL


def test_pressure_keeps_printed_and_derived_routes() -> None:
    experiment = replace(
        factories.tabulation_experiment(total_P=Decimal("2e-3")),
        method=factories.State.of(MethodToken.VACUUM_CHAMBER_PYROLYSIS),
    )
    bench = _bench(
        geometry=ApparatusGeometry(
            chamber_volume_m3=factories.located(Value.point_of("1e-3"))
        ),
        pumping_speed_m3_s=factories.located(Value.point_of("1e-2")),
        other_facts=(
            BenchFact(
                "gas_load_Pa_m3_s",
                factories.located(Value.point_of("1e-5")),
                "Pa m3/s",
            ),
        ),
    )
    result = pressure_boundary(experiment, bench)
    assert result.selected is not None
    assert result.selected.authority is WaypointAuthority.PRINTED
    assert {route.route for route in result.routes} == {
        "printed_run_pressure",
        "steady_gas_load_over_pump_speed",
    }


def test_diameter_only_escape_area_is_flagged_and_not_midpointed() -> None:
    diameter = Value(
        ValueKind.BOUND,
        bound_operator="<=",
        bound_value=Decimal("0.001"),
    )
    result = effective_escape_area(
        factories.kems_experiment(),
        _bench(
            geometry=ApparatusGeometry(
                orifice_diameter_m=factories.located(diameter)
            )
        ),
    )
    assert result.selected is not None
    assert WaypointFlag.GEOMETRIC_ONLY in result.selected.flags
    assert result.selected.value.kind in {ValueKind.BOUND, ValueKind.INTERVAL}
    assert "bench.geometry.orifice_count" not in result.selected.inputs


def test_printed_escape_area_wins_while_clausing_route_remains_visible() -> None:
    result = effective_escape_area(
        factories.kems_experiment(),
        _bench(
            geometry=ApparatusGeometry(
                orifice_area_m2=factories.located(Value.point_of("1e-6")),
                clausing_factor=factories.located(Value.point_of("0.5")),
            )
        ),
    )
    assert result.selected is not None
    assert result.selected.route == "printed_area"
    assert {route.route for route in result.routes} == {
        "printed_area",
        "printed_area_times_clausing",
    }
    assert result.routes[1].value.point == Decimal("5e-7")


def test_thermal_setpoint_route_and_uncontrolled_oxygen_flag() -> None:
    experiment = replace(
        factories.kems_experiment(),
        thermal_schedule=_schedule(),
        fO2_control=None,
    )
    thermal = thermal_path(experiment, _bench())
    assert thermal.selected is not None
    assert thermal.selected.value.series == (
        (Decimal("0"), Decimal("1500")),
        (Decimal("600"), Decimal("1500")),
    )
    oxygen = oxygen_condition(experiment, _bench())
    assert oxygen.selected is not None
    assert oxygen.selected.authority is WaypointAuthority.ASSUMED
    assert WaypointFlag.ASSUMPTION in oxygen.selected.flags


def test_thermal_ramp_route_and_nonpoint_propagation() -> None:
    point_ramp = ThermalRamp(
        rate_K_s=factories.located(Value.point_of("2")),
        start_temperature_K=factories.located(Value.point_of("300")),
        end_temperature_K=factories.located(Value.point_of("1500")),
    )
    interval_ramp = ThermalRamp(
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
    experiment = factories.kems_experiment()
    point = thermal_path(
        replace(
            experiment,
            thermal_schedule=ThermalSchedule(ramps=(point_ramp,)),
        ),
        _bench(),
    )
    assert point.selected is not None
    assert point.selected.route == "printed_ramps"
    assert point.selected.value.series[-1][0] == Decimal("600")
    nonpoint = thermal_path(
        replace(
            experiment,
            thermal_schedule=ThermalSchedule(ramps=(interval_ramp,)),
        ),
        _bench(),
    )
    assert nonpoint.selected is not None
    assert nonpoint.selected.value.kind is ValueKind.INTERVAL
    readiness = consumer_readiness(
        replace(
            experiment,
            sample=_charge(single=False),
            thermal_schedule=ThermalSchedule(ramps=(interval_ramp,)),
        ),
        _bench(geometry=experiment.apparatus.geometry),
    )
    assert all(
        any(
            gap.waypoint == "thermal_path"
            and gap.reason is GapReason.UNSUPPORTED_PRINT_FORM
            for gap in item.gaps
        )
        for item in readiness
    )


def test_readiness_pressure_floor_and_single_species_routing() -> None:
    experiment = replace(
        factories.kems_experiment(total_P=Decimal("0.1")),
        sample=_charge(),
        thermal_schedule=_schedule(),
    )
    bench = _bench(geometry=experiment.apparatus.geometry)
    readiness = {item.consumer: item for item in consumer_readiness(experiment, bench)}
    assert readiness["rps"].status is ReadinessStatus.READY
    assert readiness["engine_point"].status is ReadinessStatus.NOT_APPLICABLE
    assert readiness["engine_point"].gaps[0].reason is GapReason.SINGLE_SPECIES_CHARGE


def test_rps_refuses_temperature_point_without_piecewise_schedule() -> None:
    experiment = replace(
        factories.kems_experiment(total_P=Decimal("0.1")),
        sample=_charge(single=False),
        thermal_schedule=None,
    )
    bench = _bench(geometry=experiment.apparatus.geometry)
    readiness = {item.consumer: item for item in consumer_readiness(experiment, bench)}
    assert readiness["rps"].status is ReadinessStatus.GAP
    assert any(gap.waypoint == "thermal_path" for gap in readiness["rps"].gaps)


def test_rps_pressure_floor_rejects_below_and_crossing_interval() -> None:
    base = replace(
        factories.kems_experiment(),
        sample=_charge(single=False),
        thermal_schedule=_schedule(),
    )
    bench = _bench(geometry=base.apparatus.geometry)
    for pressure in (
        Value.point_of("0.099"),
        Value(
            ValueKind.INTERVAL,
            interval_low=Decimal("0.09"),
            interval_high=Decimal("0.11"),
        ),
    ):
        experiment = replace(
            base,
            pressure_environment=replace(
                base.pressure_environment,
                total_pressure_Pa=factories.located(pressure),
            ),
        )
        readiness = {
            item.consumer: item for item in consumer_readiness(experiment, bench)
        }
        assert readiness["rps"].status is ReadinessStatus.GAP
        assert any(
            gap.reason is GapReason.BELOW_PRESSURE_FLOOR
            for gap in readiness["rps"].gaps
        )


def test_multicomponent_engine_charge_is_not_structural_failure() -> None:
    experiment = replace(
        factories.kems_experiment(total_P=Decimal("0.1")),
        sample=_charge(single=False),
        thermal_schedule=_schedule(),
    )
    bench = _bench(geometry=experiment.apparatus.geometry)
    readiness = {item.consumer: item for item in consumer_readiness(experiment, bench)}
    assert readiness["engine_point"].status is ReadinessStatus.READY


def test_derived_groups_compute_and_preserve_nonpoint_inputs() -> None:
    experiment = replace(
        factories.kems_experiment(total_P=Decimal("0.1")),
        sample=_charge(),
        thermal_schedule=_schedule(),
    )
    bench = _bench(
        geometry=ApparatusGeometry(
            orifice_area_m2=factories.located(Value.point_of("1e-6")),
            clausing_factor=factories.located(Value.point_of("0.5")),
            cell_internal_dimensions={
                "length_m": factories.located(Value.point_of("0.1")),
                "width_m": factories.located(Value.point_of("0.1")),
                "height_m": factories.located(Value.point_of("0.1")),
            },
        )
    )
    group1 = g1(
        experiment,
        bench,
        {
            "Mg2SiO4": Value(
                ValueKind.INTERVAL,
                interval_low=Decimal("90"),
                interval_high=Decimal("110"),
            )
        },
    )
    assert group1["Mg2SiO4"].selected is not None
    assert group1["Mg2SiO4"].selected.value.kind is ValueKind.INTERVAL
    group2 = g2(experiment, bench)
    assert group2.selected is not None
    assert group2.selected.value.point == Decimal("0.01")
    group3 = g3(experiment, bench)
    assert group3.selected is not None
    assert group3.selected.value.point == Decimal("1e3")


def test_g1_requires_species_keyed_saturation_pressure_for_multicomponent_charge() -> None:
    experiment = replace(
        factories.kems_experiment(),
        sample=_charge(single=False),
        thermal_schedule=_schedule(),
    )
    bench = _bench(
        geometry=ApparatusGeometry(
            cell_internal_dimensions={
                "length_m": factories.located(Value.point_of("0.1")),
                "width_m": factories.located(Value.point_of("0.1")),
                "height_m": factories.located(Value.point_of("0.1")),
            }
        )
    )
    scalar = g1(experiment, bench, Value.point_of("100"))
    assert all(result.absence is not None for result in scalar.values())
    keyed = g1(
        experiment,
        bench,
        {"Mg2SiO4": Value.point_of("100"), "SiO2": Value.point_of("10")},
    )
    assert set(keyed) == {"Mg2SiO4", "SiO2"}
    assert keyed["Mg2SiO4"].selected is not None
    assert keyed["SiO2"].selected is not None
    assert keyed["Mg2SiO4"].selected.value != keyed["SiO2"].selected.value


def test_each_unreachable_scalar_waypoint_has_typed_absence() -> None:
    experiment = replace(
        factories.tabulation_experiment(),
        pressure_environment=replace(
            factories.tabulation_experiment().pressure_environment,
            total_pressure_Pa=factories.Located(
                factories.State.unknown("not published")
            ),
        ),
        conditions={
            "temperature_K": factories.Located(
                factories.State.unknown("not published")
            )
        },
    )
    bench = _bench()
    for result in (
        relevant_volume(experiment, bench),
        pressure_boundary(experiment, bench),
        effective_escape_area(experiment, bench),
        thermal_path(experiment, bench),
    ):
        assert result.selected is None
        assert result.absence is not None
        assert result.absence.reason is GapReason.MISSING_EVIDENCE
