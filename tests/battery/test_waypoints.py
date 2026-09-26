from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.migrate import wt_pct_to_mole_fraction

from simulator.battery.enums import AmountBasis, BenchIdentityBasis, FO2Channel, MethodToken, ValueKind
from simulator.battery.records import (
    ApparatusGeometry,
    Bench,
    BenchFact,
    BenchIdentity,
    Composition,
    Derivation,
    FO2Control,
    Located,
    Sample,
    SweepGas,
    SweepGasComponent,
    ThermalSchedule,
    ThermalRamp,
    ThermalSetpoint,
    ThermalPoint,
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
    normalized_composition,
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


def test_bench_knudsen_method_selects_cell_when_experiment_method_unknown() -> None:
    geometry = ApparatusGeometry(
        cell_internal_dimensions={
            key: factories.located(Value.point_of("0.1"))
            for key in ("length_m", "width_m", "height_m")
        },
        chamber_volume_m3=factories.located(Value.point_of("9")),
    )
    experiment = replace(
        factories.kems_experiment(), method=factories.State.unknown("unknown")
    )
    bench = _bench(
        geometry=geometry,
        method=factories.located("knudsen_effusion"),
    )
    result = relevant_volume(experiment, bench)
    assert result.selected is not None
    assert result.selected.route == "cell_internal_dimensions"
    assert result.selected.value.point == Decimal("0.001")


def test_steady_pressure_does_not_require_volume() -> None:
    """P = Q/S at steady state. Chamber volume cancels, so a missing volume
    is not a gap and does not block the derived pressure."""
    base = factories.tabulation_experiment()
    experiment = replace(
        base,
        method=factories.State.of(MethodToken.VACUUM_CHAMBER_PYROLYSIS),
        pressure_environment=replace(
            base.pressure_environment,
            total_pressure_Pa=factories.Located(factories.State.unknown("not printed")),
        ),
    )
    bench = _bench(
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
    assert result.selected.route == "steady_gas_load_over_pump_speed"
    assert result.selected.value.point == Decimal("1e-3")
    assert "relevant_volume" not in result.selected.inputs
    assert result.absence is None


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


def test_inferred_run_pressure_never_carries_printed_authority() -> None:
    """A unit-converted or otherwise inferred run pressure is a derivation;
    it still wins its scope but must not be stamped printed."""
    experiment = factories.tabulation_experiment(total_P=Decimal("2e-3"))
    located_pressure = experiment.pressure_environment.total_pressure_Pa
    inferred = Located(
        located_pressure.state,
        located_pressure.locator,
        Derivation("unit_conversion", ("printed_mTorr",), (), "Pa"),
    )
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment, total_pressure_Pa=inferred
        ),
    )
    result = pressure_boundary(experiment, _bench())
    assert result.selected is not None
    assert result.selected.route == "printed_run_pressure"
    assert result.selected.authority is WaypointAuthority.DERIVED


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


def test_corrected_printed_escape_area_wins_while_raw_route_remains_visible() -> None:
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
    assert result.selected.route == "printed_area_times_clausing"
    assert result.selected.value.point == Decimal("5e-7")
    assert WaypointFlag.GEOMETRIC_ONLY in result.routes[0].flags
    assert {route.route for route in result.routes} == {
        "printed_area",
        "printed_area_times_clausing",
    }
    assert result.routes[1].value.point == Decimal("5e-7")


def test_clausing_corrected_diameter_route_wins_and_raw_route_stays_flagged() -> None:
    result = effective_escape_area(
        factories.kems_experiment(),
        _bench(
            geometry=ApparatusGeometry(
                orifice_diameter_m=factories.located(Value.point_of("0.001")),
                clausing_factor=factories.located(Value.point_of("0.5")),
            )
        ),
    )
    assert result.selected is not None
    assert result.selected.route == "diameter_times_clausing"
    assert result.selected.value.point == Decimal(
        "3.926990816987241548078304229E-7"
    )
    geometric = next(route for route in result.routes if "geometric" in route.route)
    assert WaypointFlag.GEOMETRIC_ONLY in geometric.flags


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
    assert oxygen.selected is None
    assert "fO2_log" in oxygen.absence.missing


def _sweep_gas_experiment(gas: SweepGas):
    experiment = factories.kems_experiment()
    return replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment, sweep_gas=factories.located(gas)
        ),
    )


def _o2_ar_mixture(o2_partial_pressure=None) -> SweepGas:
    unknown = factories.State.unknown("not_published")
    return SweepGas(
        species=None,
        flow_sccm=unknown,
        partial_pressure_Pa=unknown,
        components=(
            SweepGasComponent(
                species="O2",
                mole_fraction=factories.State.of(Decimal("0.2")),
                flow_sccm=unknown,
                partial_pressure_Pa=(
                    factories.State.of(Decimal("20000"))
                    if o2_partial_pressure is None
                    else o2_partial_pressure
                ),
            ),
            SweepGasComponent(
                species="Ar",
                mole_fraction=factories.State.of(Decimal("0.8")),
                flow_sccm=unknown,
                partial_pressure_Pa=factories.State.of(Decimal("80000")),
            ),
        ),
    )


def test_oxygen_condition_uses_mixture_component_printed_partial_pressure() -> None:
    result = oxygen_condition(_sweep_gas_experiment(_o2_ar_mixture()), _bench())
    assert result.selected is not None
    assert result.selected.route == "oxygen_sweep_component_partial_pressure"
    assert result.selected.authority is WaypointAuthority.DERIVED
    assert float(result.selected.value.point) == pytest.approx(-0.6989700043360188, rel=1e-12)
    single = SweepGas(
        species="O2",
        flow_sccm=factories.State.unknown("not_published"),
        partial_pressure_Pa=factories.State.of(Decimal("20000")),
    )
    single_result = oxygen_condition(_sweep_gas_experiment(single), _bench())
    assert single_result.selected is not None
    assert result.selected.value == single_result.selected.value


def test_oxygen_condition_mixture_typed_absence_component_pressure_stays_refusal() -> None:
    mixture = _o2_ar_mixture(o2_partial_pressure=factories.State.unknown("not_published"))
    result = oxygen_condition(_sweep_gas_experiment(mixture), _bench())
    assert result.selected is None
    assert result.absence is not None
    # No route may be invented from the printed mole fraction alone.
    assert not any("sweep" in route.route for route in result.routes)


def test_oxygen_condition_mixture_without_o2_component_stays_refusal() -> None:
    unknown = factories.State.unknown("not_published")
    mixture = SweepGas(
        species=None,
        flow_sccm=unknown,
        partial_pressure_Pa=unknown,
        components=(
            SweepGasComponent(
                species="CO",
                mole_fraction=factories.State.of(Decimal("0.25")),
                flow_sccm=unknown,
                partial_pressure_Pa=factories.State.of(Decimal("25000")),
            ),
            SweepGasComponent(
                species="Ar",
                mole_fraction=factories.State.of(Decimal("0.75")),
                flow_sccm=unknown,
                partial_pressure_Pa=factories.State.of(Decimal("75000")),
            ),
        ),
    )
    result = oxygen_condition(_sweep_gas_experiment(mixture), _bench())
    assert result.selected is None
    assert not any("sweep" in route.route for route in result.routes)


def test_oxygen_condition_never_selects_among_alternatives() -> None:
    unknown = factories.State.unknown("not_published")
    single = SweepGas(
        species="O2",
        flow_sccm=unknown,
        partial_pressure_Pa=factories.State.of(Decimal("20000")),
    )
    gas = SweepGas(
        species=None,
        flow_sccm=unknown,
        partial_pressure_Pa=unknown,
        alternatives=(single, _o2_ar_mixture()),
    )
    result = oxygen_condition(_sweep_gas_experiment(gas), _bench())
    assert result.selected is None
    assert not any("sweep" in route.route for route in result.routes)


def test_sweep_species_route_refuses_when_alternatives_present() -> None:
    """A species="O2" record that also sets alternatives is invalid (validation
    rejects it); the waypoint must refuse it too, never score the partial."""
    unknown = factories.State.unknown("not_published")
    gas = SweepGas(
        species="O2",
        flow_sccm=unknown,
        partial_pressure_Pa=factories.State.of(Decimal("20000")),
        alternatives=(
            SweepGas(species="Ar", flow_sccm=unknown, partial_pressure_Pa=unknown),
            SweepGas(species="N2", flow_sccm=unknown, partial_pressure_Pa=unknown),
        ),
    )
    result = oxygen_condition(_sweep_gas_experiment(gas), _bench())
    assert result.selected is None
    assert not any("sweep" in route.route for route in result.routes)


def test_printed_mixture_component_outranks_observation_gas_derivation() -> None:
    """A printed mixture-component pO2 is direct evidence; an observation
    gas_composition x_O2 * P_total derivation must not shadow it."""
    experiment = _sweep_gas_experiment(_o2_ar_mixture())
    gas = Composition("mix", (("O2", Decimal("0.21")), ("N2", Decimal("0.79"))), AmountBasis.MOLE_FRACTION)
    observation = replace(
        factories.observation("obs", experiment.experiment_id, factories.o2_identity(), 1),
        point_conditions={"gas_composition": factories.located(gas)},
    )
    result = oxygen_condition(experiment, _bench(), observation)
    assert result.selected is not None
    assert result.selected.route == "oxygen_sweep_component_partial_pressure"
    assert float(result.selected.value.point) == pytest.approx(-0.6989700043360188, rel=1e-12)
    assert "observation_gas_composition" in {route.route for route in result.routes}


def test_printed_sweep_partial_pressure_outranks_buffer_derivation() -> None:
    """A printed sweep pO2 outranks the buffer relation, which needs a
    published table plus thermal and pressure waypoints."""
    experiment = replace(
        factories.kems_experiment(),
        conditions={"temperature_K": factories.located(Decimal("1200"))},
        fO2_control=FO2Control(
            channel=factories.State.of(FO2Channel.BUFFER),
            buffer=factories.located("IW"),
        ),
    )
    single = SweepGas(
        species="O2",
        flow_sccm=factories.State.unknown("not_published"),
        partial_pressure_Pa=factories.State.of(Decimal("20000")),
    )
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment, sweep_gas=factories.located(single)
        ),
    )
    result = oxygen_condition(experiment, _bench())
    assert result.selected is not None
    assert result.selected.route == "oxygen_sweep_partial_pressure"
    assert float(result.selected.value.point) == pytest.approx(-0.6989700043360188, rel=1e-12)
    assert "buffer_relation" in {route.route for route in result.routes}


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
    experiment = factories.kems_experiment(total_P=Decimal("0.1"))
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
    for item in readiness:
        name = {"kems": "temperature_program", "rps": "thermal_path", "engine_point": "temperature_K"}[item.consumer]
        assert any(gap.waypoint == name for gap in item.gaps)


def test_thermal_ramp_and_hold_are_composed() -> None:
    ramp = ThermalRamp(
        rate_K_s=factories.located(Value.point_of("2")),
        start_temperature_K=factories.located(Value.point_of("300")),
        end_temperature_K=factories.located(Value.point_of("1500")),
    )
    hold = ThermalSetpoint(
        factories.located(Value.point_of("1500")),
        factories.located(Value.point_of("600")),
    )
    result = thermal_path(
        replace(
            factories.kems_experiment(),
            thermal_schedule=ThermalSchedule(
                ramps=(ramp,), setpoints_and_holds=(hold,)
            ),
        ),
        _bench(),
    )
    assert result.selected is not None
    assert result.selected.route == "printed_ramps_and_holds"
    assert result.selected.value.series == (
        (Decimal("0"), Decimal("300")),
        (Decimal("600"), Decimal("1500")),
        (Decimal("1200"), Decimal("1500")),
    )


@pytest.mark.parametrize("unusable_points", [False, True])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_incomplete_or_unordered_ramp_hold_schedule_cannot_earn_readiness(ambiguous, unusable_points) -> None:
    ramp = ThermalRamp(
        factories.located(Value.point_of("2")),
        factories.located(Value.point_of("300")),
        factories.located(Value.point_of("1500")),
    )
    hold = ThermalSetpoint(
        factories.located(Value.point_of("1500")),
        factories.located(Value(ValueKind.INTERVAL, interval_low=Decimal("500"), interval_high=Decimal("600"))),
    )
    ramps, holds = (ramp,), (hold,)
    if ambiguous:
        ramps = (
            replace(ramp, end_temperature_K=factories.located(Value.point_of("1000"))),
            replace(ramp, start_temperature_K=factories.located(Value.point_of("1000"))),
        )
        holds = (
            ThermalSetpoint(factories.located(Value.point_of("1000")), factories.located(Value.point_of("100"))),
            replace(hold, hold_duration_s=factories.located(Value.point_of("200"))),
        )
    experiment = replace(
        factories.kems_experiment(total_P=Decimal("0.1")),
        sample=_charge(single=False),
        thermal_schedule=ThermalSchedule(ramps=ramps, setpoints_and_holds=holds,
            points=(ThermalPoint(factories.Located(factories.State.unknown("not_published")),
                                factories.Located(factories.State.unknown("not_published"))),) if unusable_points else None),
    )
    bench = _bench(geometry=experiment.apparatus.geometry)
    result = thermal_path(experiment, bench)
    assert result.selected is None
    assert result.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    assert result.routes  # Evidence remains inspectable, never selected as a complete path.
    for item in consumer_readiness(experiment, bench):
        assert item.status is ReadinessStatus.GAP
        name = {"kems": "temperature_program", "rps": "thermal_path", "engine_point": "temperature_K"}[item.consumer]
        assert any(gap.waypoint == name for gap in item.gaps)


def test_raw_printed_area_cannot_satisfy_effective_requirement() -> None:
    experiment = replace(factories.kems_experiment(), sample=_charge(single=False), thermal_schedule=_schedule())
    bench = _bench(geometry=ApparatusGeometry(orifice_area_m2=factories.located(Value.point_of("1e-6"))))
    area = effective_escape_area(experiment, bench)
    assert WaypointFlag.GEOMETRIC_ONLY in area.selected.flags
    kems = consumer_readiness(experiment, bench)[0]
    assert kems.status is ReadinessStatus.GAP
    escape = [gap for gap in kems.gaps if gap.waypoint == "effective_escape_area"]
    assert len(escape) == 1
    assert escape[0].reason is GapReason.MISSING_EVIDENCE
    assert escape[0].missing == ("bench.geometry.clausing_factor",)


def test_diameter_only_escape_gap_names_clausing_not_area() -> None:
    experiment = replace(factories.kems_experiment(), sample=_charge(single=False), thermal_schedule=_schedule())
    bench = _bench(
        geometry=ApparatusGeometry(
            orifice_diameter_m=factories.located(Value.point_of("0.001"))
        )
    )
    area = effective_escape_area(experiment, bench)
    assert WaypointFlag.GEOMETRIC_ONLY in area.selected.flags
    assert "bench.geometry.orifice_area_m2" not in area.selected.inputs
    kems = consumer_readiness(experiment, bench)[0]
    assert kems.status is ReadinessStatus.GAP
    escape = next(gap for gap in kems.gaps if gap.waypoint == "effective_escape_area")
    assert escape.missing == ("bench.geometry.clausing_factor",)


def test_readiness_pressure_floor_and_single_species_routing() -> None:
    experiment = replace(
        factories.kems_experiment(total_P=Decimal("0.1")),
        sample=_charge(),
        thermal_schedule=_schedule(),
    )
    bench = _bench(geometry=experiment.apparatus.geometry)
    readiness = {item.consumer: item for item in consumer_readiness(experiment, bench)}
    assert readiness["rps"].status is ReadinessStatus.GAP
    assert any(gap.waypoint == "operator.surfaces" for gap in readiness["rps"].gaps)
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


@pytest.mark.parametrize("pressure,status", [
    (Value.point_of("0.099"), ReadinessStatus.NOT_APPLICABLE),
    (Value(ValueKind.INTERVAL, interval_low=Decimal("0.01"), interval_high=Decimal("0.09")), ReadinessStatus.NOT_APPLICABLE),
    (Value(ValueKind.INTERVAL, interval_low=Decimal("0.09"), interval_high=Decimal("0.11")), ReadinessStatus.GAP),
    (Value(ValueKind.BOUND, bound_operator="<", bound_value=Decimal("0.1")), ReadinessStatus.NOT_APPLICABLE),
    (Value(ValueKind.BOUND, bound_operator="<=", bound_value=Decimal("0.1")), ReadinessStatus.GAP),
    (Value(ValueKind.BOUND, bound_operator=">", bound_value=Decimal("0.09")), ReadinessStatus.GAP),
])
def test_rps_pressure_floor_rejects_only_wholly_excluded_values(pressure, status) -> None:
    base = replace(
        factories.kems_experiment(),
        sample=_charge(single=False),
        thermal_schedule=_schedule(),
    )
    bench = _bench(geometry=base.apparatus.geometry)
    for pressure in (pressure,):
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
        assert readiness["rps"].status is status
        assert any(
            gap.reason is GapReason.BELOW_PRESSURE_FLOOR
            for gap in readiness["rps"].gaps
        )


def test_multicomponent_engine_charge_is_not_structural_failure() -> None:
    from simulator.battery.records import FO2Control

    experiment = replace(
        factories.kems_experiment(total_P=Decimal("0.1")),
        sample=_charge(single=False),
        thermal_schedule=_schedule(),
        fO2_control=FO2Control(factories.State.unknown("channel not printed"), oxygen_partial_pressure_Pa=factories.located(Value.point_of("0.0001"))),
    )
    bench = _bench(geometry=experiment.apparatus.geometry)
    readiness = {item.consumer: item for item in consumer_readiness(experiment, bench)}
    assert readiness["engine_point"].status is ReadinessStatus.READY
    engines = [item for item in consumer_readiness(experiment, bench) if item.engine]
    assert [item.engine for item in engines] == [
        "internal-analytical",
        "alphamelts",
        "thermoengine",
        "vaporock",
        "magemin",
        "cached-real",
        "imcc_sf04",
        "imcc_sf04_ext",
    ]
    assert all(item.status is ReadinessStatus.READY for item in engines)


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
    assert group2.selected.value.point == Decimal("0.005")
    assert WaypointFlag.ASSUMPTION in group2.selected.flags
    group3 = g3(experiment, bench)
    assert group3.selected is not None
    assert group3.selected.value.point == Decimal("2e3")


def test_g2_uses_explicit_evaporation_alpha() -> None:
    experiment = replace(
        factories.kems_experiment(),
        sample=_charge(),
        conditions={
            **factories.kems_experiment().conditions,
            "evaporation_alpha": factories.located(Decimal("0.1")),
        },
    )
    bench = _bench(
        geometry=ApparatusGeometry(
            orifice_area_m2=factories.located(Value.point_of("1e-6"))
        )
    )
    result = g2(experiment, bench)
    assert result.selected is not None
    assert result.selected.value.point == Decimal("0.1")
    assert WaypointFlag.ASSUMPTION not in result.selected.flags


@pytest.mark.parametrize("silica,expected_mass", [("50", "0.0009"), ("60", "0.001")])
def test_partial_printed_oxide_inventory_preserves_printed_mass_basis(silica, expected_mass) -> None:
    printed = {"MgO": Decimal("40"), "SiO2": Decimal(silica)}
    sample = replace(
        _charge(single=False),
        mass_kg=factories.located(Value.point_of("0.001")),
        initial_composition=factories.located(wt_pct_to_mole_fraction(printed)),
        printed_composition=factories.located(printed),
    )
    result = charge_moles_by_species(
        replace(factories.kems_experiment(), sample=sample), _bench()
    )
    for species in printed:
        assert result[species].selected.route == "mass_times_printed_wt_percent"
        assert {route.route for route in result[species].routes} == {
            "mass_times_printed_wt_percent", "mass_times_mole_fraction",
        }
    # CIAAW atomic masses: MgO=40.304 g/mol, SiO2=60.083 g/mol.
    recovered = (result["MgO"].selected.value.point * Decimal("0.040304")
                 + result["SiO2"].selected.value.point * Decimal("0.060083"))
    assert float(recovered) == pytest.approx(float(expected_mass), rel=1e-12)


@pytest.mark.parametrize("mass", [
    factories.Located(factories.State.unknown("not_numeric")),
    factories.located(Value(ValueKind.UNAVAILABLE, unavailable_reason="not_numeric")),
    factories.located(Value(ValueKind.CATEGORICAL, categorical="not measured")),
])
def test_non_numeric_mass_does_not_fabricate_zero_charge(mass) -> None:
    sample = Sample(
        mass_kg=mass,
        printed_composition=factories.located({"SiO2": Decimal("100")}),
    )
    result = charge_moles_by_species(
        replace(factories.kems_experiment(), sample=sample), _bench()
    )
    assert result.absence is not None
    assert not result


def test_categorical_printed_composition_routes_no_wt_percent() -> None:
    """A Value-payload-shaped printed_composition ({kind: categorical, ...}) is
    not a wt% map: no leaf parses as Decimal, so no charge route and no crash.
    """
    sample = Sample(
        mass_kg=factories.located(Value.point_of("0.001")),
        printed_composition=factories.located(
            {"kind": "categorical", "categorical": "Fe-Mn, Fe-C-Mn, Fe-Cu, or Fe-Sn master alloy"}
        ),
    )
    result = charge_moles_by_species(
        replace(factories.kems_experiment(), sample=sample), _bench()
    )
    assert result.absence is not None
    assert not result


def test_printed_wt_percent_outranks_inferred_observation_composition() -> None:
    """An inferred point composition must never outrank a printed wt%: the
    observation scope bit is not evidence authority."""
    printed = {"MgO": Decimal("40.304"), "SiO2": Decimal("60.083")}
    sample = Sample(
        mass_kg=Located(factories.State.unknown("intensive point only")),
        printed_composition=factories.located(printed),
    )
    experiment = replace(factories.kems_experiment(), sample=sample)
    inferred = Located(
        factories.State.of(
            Composition(
                "mix",
                (("MgO", Decimal("0.1")), ("SiO2", Decimal("0.9"))),
                AmountBasis.MOLE_FRACTION,
            )
        ),
        factories.loc(),
        Derivation(
            "calculated_from_printed_recipe_or_aimed_target",
            ("assumed",), (), "mol_fraction",
        ),
    )
    observation = replace(
        factories.observation("obs", experiment.experiment_id, factories.o2_identity(), 1),
        point_conditions={"composition": inferred},
    )
    result = normalized_composition(experiment, _bench(), observation)
    assert result.selected is not None
    assert result.selected.route == "normalized_printed_composition"
    # CIAAW molar masses: 40.304/40.304 and 60.083/60.083 give equal moles.
    assert result.selected.value == {"MgO": Decimal("0.5"), "SiO2": Decimal("0.5")}
    # The inferred route stays visible as evidence; it is just never selected.
    assert "observation_normalized_initial_composition" in {
        route.route for route in result.routes
    }


def test_inferred_molar_inventory_never_printed_and_loses_to_printed_wt() -> None:
    """A molar inventory carrying an inference marker is a derivation: it must
    not acquire printed authority, and a printed wt% derivation outranks it."""
    sample = Sample(
        mass_kg=factories.located(Value.point_of("0.001")),
        initial_composition=Located(
            factories.State.of(
                Composition(
                    "mix",
                    (("MgO", Decimal("0.5")), ("SiO2", Decimal("0.5"))),
                    AmountBasis.MOL_INVENTORY,
                )
            ),
            factories.loc(),
            Derivation("derived_inventory", ("wt_pct",), (), "mol"),
        ),
        printed_composition=factories.located({"MgO": Decimal("40"), "SiO2": Decimal("60")}),
    )
    result = charge_moles_by_species(replace(factories.kems_experiment(), sample=sample), _bench())
    selected = result["MgO"].selected
    assert selected is not None
    assert selected.route == "mass_times_printed_wt_percent"
    assert selected.authority is WaypointAuthority.DERIVED
    inventory = next(
        route for route in result["MgO"].routes if route.route == "printed_molar_inventory"
    )
    assert inventory.authority is WaypointAuthority.DERIVED
    # A genuinely printed inventory keeps printed authority and wins outright.
    printed_sample = replace(
        sample,
        initial_composition=Located(
            factories.State.of(sample.initial_composition.state.value), factories.loc()
        ),
    )
    printed_result = charge_moles_by_species(
        replace(factories.kems_experiment(), sample=printed_sample), _bench()
    )
    printed_selected = printed_result["MgO"].selected
    assert printed_selected is not None
    assert printed_selected.route == "printed_molar_inventory"
    assert printed_selected.authority is WaypointAuthority.PRINTED
    assert printed_selected.value.point == Decimal("0.5")


def test_geometric_only_escape_does_not_satisfy_kems_readiness() -> None:
    experiment = replace(
        factories.kems_experiment(total_P=Decimal("0.1")),
        sample=_charge(single=False),
        thermal_schedule=_schedule(),
    )
    bench = _bench(
        geometry=ApparatusGeometry(
            orifice_diameter_m=factories.located(Value.point_of("0.001"))
        )
    )
    readiness = {item.consumer: item for item in consumer_readiness(experiment, bench)}
    assert readiness["kems"].status is ReadinessStatus.GAP
    assert any(gap.waypoint == "effective_escape_area" for gap in readiness["kems"].gaps)


def _knudsen_case(pressure, method=MethodToken.KNUDSEN_EFFUSION):
    experiment = replace(
        factories.kems_experiment(),
        sample=_charge(single=False),
        thermal_schedule=_schedule(),
        method=factories.State.of(method) if method else factories.State.unknown("not_published"),
    )
    experiment = replace(experiment, pressure_environment=replace(
        experiment.pressure_environment,
        total_pressure_Pa=factories.located(pressure, page=7),
        sweep_gas=factories.located(replace(experiment.pressure_environment.sweep_gas.state.value, species="Ar")),
    ))
    bench = _bench(geometry=ApparatusGeometry(
        orifice_diameter_m=factories.located(Value.point_of("0.0005"), page=9),
        clausing_factor=factories.located(Value.point_of("0.5")),
    ))
    return experiment, bench


def test_atmospheric_knudsen_method_gets_notice_without_refusal() -> None:
    experiment, bench = _knudsen_case(Value.point_of("101325"))
    result = consumer_readiness(experiment, bench)[0]
    assert result.status is ReadinessStatus.GAP
    assert not any(gap.reason is GapReason.OUTSIDE_PRESSURE_REGIME for gap in result.gaps)
    assert result.notices
    notice, = result.notices
    assert notice.threshold == Decimal("10")
    assert float(notice.knudsen_number.point) == pytest.approx(74.30919441729452 / 101325, rel=1e-12)
    assert notice.inputs["d_orifice"].value.point == Decimal("0.0005")
    assert notice.inputs["d_orifice"].locators == (factories.loc(page=9),)
    assert notice.inputs["P"].locators == (factories.loc(page=7),)
    assert notice.inputs["T"].value.point == Decimal("1500")
    assert notice.inputs["T"].locators == (factories.loc(),)


def test_knudsen_notice_detects_failing_actual_ramp_segment() -> None:
    experiment, bench = _knudsen_case(Value.point_of("5"))
    experiment = replace(experiment, thermal_schedule=ThermalSchedule(ramps=(ThermalRamp(
        factories.located(Value.point_of("2")), factories.located(Value.point_of("300")),
        factories.located(Value.point_of("1500")),
    ),)))
    result = consumer_readiness(experiment, bench)[0]
    assert result.status is ReadinessStatus.GAP
    assert not any(gap.reason is GapReason.OUTSIDE_PRESSURE_REGIME for gap in result.gaps)
    assert result.notices
    notice, = result.notices
    assert float(notice.knudsen_number.interval_low) == pytest.approx(2.97236777669)
    assert float(notice.knudsen_number.interval_high) == pytest.approx(14.8618388835)


@pytest.mark.parametrize("pressure,status", [
    (Value.point_of("5"), ReadinessStatus.GAP),
    (Value.point_of("20"), ReadinessStatus.NOT_APPLICABLE),
    (Value(ValueKind.BOUND, bound_operator="<", bound_value=Decimal("5")), ReadinessStatus.GAP),
    (Value(ValueKind.BOUND, bound_operator="<", bound_value=Decimal("20")), ReadinessStatus.GAP),
    (Value(ValueKind.BOUND, bound_operator=">", bound_value=Decimal("20")), ReadinessStatus.NOT_APPLICABLE),
])
def test_unknown_method_knudsen_predicate_and_directional_pressure_bounds(pressure, status) -> None:
    experiment, bench = _knudsen_case(pressure, method=None)
    result = consumer_readiness(experiment, bench)[0]
    assert result.status is status


def test_explicit_non_effusion_method_is_not_applicable_even_at_low_pressure() -> None:
    experiment, bench = _knudsen_case(Value.point_of("1e-6"), MethodToken.TRANSPIRATION)
    result = consumer_readiness(experiment, bench)[0]
    assert result.status is ReadinessStatus.NOT_APPLICABLE
    assert result.gaps[0].waypoint == "method"
    assert result.gaps[0].missing == ("transpiration",)


@pytest.mark.parametrize("missing", ["diameter", "gas", "pressure", "thermal"])
def test_unknown_method_uncomputable_knudsen_names_missing_input(missing) -> None:
    experiment, bench = _knudsen_case(Value.point_of("1"), method=None)
    if missing == "diameter":
        bench = replace(bench, geometry=replace(bench.geometry, orifice_diameter_m=None))
    elif missing == "gas":
        experiment = replace(experiment, pressure_environment=replace(experiment.pressure_environment,
            sweep_gas=factories.Located(factories.State.unknown("not_published"))))
    elif missing == "pressure":
        experiment = replace(experiment, pressure_environment=replace(experiment.pressure_environment,
            total_pressure_Pa=factories.Located(factories.State.unknown("not_published"))))
    else:
        experiment = replace(experiment, thermal_schedule=None,
            conditions={"temperature_K": factories.Located(factories.State.unknown("not_published"))})
    result = consumer_readiness(experiment, bench)[0]
    assert result.status is ReadinessStatus.GAP
    gap = next(gap for gap in result.gaps if gap.waypoint == "knudsen_number_orifice")
    assert gap.reason is GapReason.MISSING_EVIDENCE
    assert gap.missing
    assert not result.notices


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


def test_multi_cited_bench_identity_gap_is_unattributable_by_construction() -> None:
    from scripts.bench_readiness import _missing_bench_readiness

    readiness = _missing_bench_readiness(implicit=True)
    for consumer in readiness:
        assert consumer.status is ReadinessStatus.GAP
        (gap,) = consumer.gaps
        assert gap.waypoint == "bench_identity"
        assert gap.reason is GapReason.UNATTRIBUTABLE_BY_CONSTRUCTION
        assert gap.missing == ("bench.identity.ref: multiple cited apparatuses",)


def test_missing_linked_bench_keeps_missing_evidence_reason() -> None:
    from scripts.bench_readiness import _missing_bench_readiness

    readiness = _missing_bench_readiness(implicit=False)
    for consumer in readiness:
        (gap,) = consumer.gaps
        assert gap.waypoint == "bench"
        assert gap.reason is GapReason.MISSING_EVIDENCE


def _lead_corpus(tmp_path, leads) -> "object":
    import yaml

    corpus = tmp_path / "corpus"
    (corpus / "ledger").mkdir(parents=True)
    (corpus / "ledger" / "apparatus-reference-leads.yaml").write_text(
        yaml.safe_dump({"leads": leads})
    )
    work = factories.work()
    return replace(
        work,
        source_ids=("fixture-source",),
        source_files=replace(work.source_files, corpus_repo=str(corpus)),
    )


def _report_with_work(tmp_path, monkeypatch, work, experiment) -> "object":
    import scripts.bench_readiness as module

    monkeypatch.setattr(
        module,
        "load_migrated_store",
        lambda root: (
            {work.work_id: work},
            {experiment.experiment_id: experiment},
            {},
        ),
    )
    monkeypatch.setattr(module, "load_migrated_benches", lambda root: {})
    return module.report(tmp_path)


def test_readiness_report_passes_modelling_inputs_to_consumer_path(
    tmp_path, monkeypatch
) -> None:
    from tests.battery.test_bench_generators import complete_rps
    import scripts.bench_readiness as module

    experiment, bench, observation, modelling_inputs = complete_rps()
    printed_experiment = replace(experiment, bench_id=bench.id)
    printed_observation = observation
    observation = replace(
        observation,
        point_conditions={
            key: value
            for key, value in observation.point_conditions.items()
            if key != "fO2_log"
        },
    )
    pressure = factories.located(
        Value.point_of(Decimal("1")),
        note="printed vacuum during run",
    )
    experiment = replace(
        experiment,
        bench_id=bench.id,
        conditions={
            **experiment.conditions,
            "surfaces": observation.point_conditions["surfaces"],
            "gas_boundary": observation.point_conditions["gas_boundary"],
        },
        pressure_environment=replace(
            experiment.pressure_environment, total_pressure_Pa=pressure
        ),
    )
    work = factories.work()
    monkeypatch.setattr(
        module,
        "load_migrated_store",
        lambda root: (
            {work.work_id: work},
            {experiment.experiment_id: experiment},
            {observation.observation_id: observation},
        ),
    )
    monkeypatch.setattr(module, "load_migrated_benches", lambda root: {bench.id: bench})

    direct = {
        item.consumer: item
        for item in consumer_readiness(
            experiment, bench, observation, modelling_inputs=modelling_inputs
        )
    }
    assert oxygen_condition(experiment, bench, observation).selected.route == (
        "vacuum_total_pressure_upper_bound"
    )
    without = module.report(tmp_path)
    with_model = module.report(tmp_path, modelling_inputs=modelling_inputs)

    def consumer_rows(result):
        return {
            item["consumer"]: item
            for item in result["sources"][0]["consumers"]
        }

    without_rps = consumer_rows(without)["rps"]
    with_rps = consumer_rows(with_model)["rps"]
    assert without_rps["status"] == ReadinessStatus.GAP.value
    assert with_rps["status"] == direct["rps"].status.value == ReadinessStatus.READY.value
    assert not any(
        gap["waypoint"].startswith("operator.") for gap in with_rps["gaps"]
    )
    assert with_rps["flags"] == [
        {"waypoint": "surfaces", "authority": "assumed", "flag_id": "operator.surfaces"},
        {"waypoint": "furnace_ceiling_C", "authority": "assumed", "flag_id": "operator.furnace_ceiling_C"},
        {"waypoint": "feedstock_id", "authority": "assumed", "flag_id": "operator.feedstock_id"},
    ]
    assert consumer_rows(without)["engine_point"]["flags"]
    assert without["summary"]["by_consumer"]["rps"]["ready"] == 0
    assert with_model["summary"]["by_consumer"]["rps"]["ready"] == 1
    for consumer in ("kems", "engine_point"):
        assert consumer_rows(with_model)[consumer]["status"] == direct[consumer].status.value

    monkeypatch.setattr(
        module,
        "load_migrated_store",
        lambda root: (
            {work.work_id: work},
            {printed_experiment.experiment_id: printed_experiment},
            {printed_observation.observation_id: printed_observation},
        ),
    )
    printed = module.report(tmp_path)
    assert consumer_rows(printed)["engine_point"]["status"] == ReadinessStatus.READY.value
    assert consumer_rows(printed)["engine_point"]["flags"] == []
    assert all(item["flags"] == [] for item in printed["sources"][0]["engines"])


def test_readiness_report_carries_vacuum_flag_for_real_source(
    tmp_path, monkeypatch
) -> None:
    from pathlib import Path

    from simulator.battery.migrate import (
        bench_from_plain,
        experiment_from_plain,
        load_yaml,
        observation_from_plain,
        work_from_plain,
    )
    import scripts.bench_readiness as module

    source_id = "mendybaev-2017-fun-cai-lab-evaporation"
    root = Path(__file__).parents[2]
    work_doc = load_yaml(
        root / "data/literature/works/10.1016_j.gca.2016.08.034.yaml"
    )
    work = work_from_plain(work_doc["work"])
    experiments = {
        experiment.experiment_id: experiment
        for experiment in (
            experiment_from_plain(item) for item in work_doc["experiments"]
        )
    }
    bench = bench_from_plain(work_doc["benches"][0])
    source_doc = load_yaml(root / f"data/literature/extracts-v2/{source_id}.yaml")
    observations = [
        observation_from_plain(item) for item in source_doc["observations"]
    ]
    for item in observations:
        experiment = experiments.get(item.experiment_id)
        if experiment is None:
            continue
        condition = oxygen_condition(experiment, bench, item)
        if (
            condition.selected is not None
            and condition.selected.route == "vacuum_total_pressure_upper_bound"
        ):
            observation = item
            break
    else:
        raise AssertionError("no Mendybaev observation selected for a matching experiment")
    monkeypatch.setattr(
        module,
        "load_migrated_store",
        lambda root: (
            {work.work_id: work},
            {experiment.experiment_id: experiment},
            {observation.observation_id: observation},
        ),
    )
    monkeypatch.setattr(module, "load_migrated_benches", lambda root: {bench.id: bench})

    result = module.report(
        tmp_path,
        modelling_inputs={"surfaces": {}, "feedstock_id": "test-feed", "furnace_ceiling_C": 1200},
    )
    source = next(row for row in result["sources"] if row["source_id"] == source_id)
    engine = next(
        row for row in source["engines"] if row["engine"] == "internal-analytical"
    )
    assert engine["status"] == ReadinessStatus.READY.value
    assert len(engine["flags"]) == 1
    flag = engine["flags"][0]
    assert flag["waypoint"] == "oxygen_condition"
    assert flag["authority"] == "extrapolated"
    assert flag["flag_id"] == "vacuum_total_pressure_upper_bound"
    assert "bound, not a measurement" in flag["notice"]


def test_verdict_no_lead_is_not_an_apparatus_reference(tmp_path, monkeypatch) -> None:
    work = _lead_corpus(
        tmp_path,
        [
            {
                "citing": "fixture-source",
                "lead_as_given": "Ross (1948); Bonamici et al. (2017)",
                "for_parameters": ["arkosic soil characterization"],
                "identity_verdict": "no",
            }
        ],
    )
    experiment = factories.kems_experiment(work_id=work.work_id)
    result = _report_with_work(tmp_path, monkeypatch, work, experiment)
    (source,) = result["sources"]
    (row,) = source["experiments"]
    assert row["bench_identity"]["basis"] == "inferred_from_embedded_evidence"
    assert all(
        gap["waypoint"] != "bench_identity"
        for consumer in row["consumers"] + row["engines"]
        for gap in consumer["gaps"]
    )
    assert result["summary"]["bench_identity_verdicts"] == {
        "unattributable_by_construction": {"source_count": 0, "experiment_count": 0},
        "reference_not_yet_resolved": {"source_count": 0, "experiment_count": 0},
    }


def test_unobtained_single_cited_apparatus_is_pending_acquisition(tmp_path, monkeypatch) -> None:
    work = _lead_corpus(
        tmp_path,
        [
            {
                "citing": "fixture-source",
                "lead_as_given": "Smith (1980)",
                "for_parameters": ["orifice diameter"],
            }
        ],
    )
    experiment = factories.kems_experiment(work_id=work.work_id)
    result = _report_with_work(tmp_path, monkeypatch, work, experiment)
    (source,) = result["sources"]
    (row,) = source["experiments"]
    assert row["bench_identity"]["basis"] == "cited_by_author"
    assert {
        "waypoint": "bench_identity",
        "reason": "reference_not_yet_resolved",
        "missing": ["bench.identity.ref: Smith (1980)"],
    } in row["informational_gaps"]
    assert {
        "waypoint": "bench_identity",
        "reason": "reference_not_yet_resolved",
        "missing": ["bench.identity.ref: Smith (1980)"],
        "count": 1,
        "experiment_ids": [experiment.experiment_id],
    } in source["informational_gaps"]
    assert result["summary"]["bench_identity_verdicts"]["reference_not_yet_resolved"] == {
        "source_count": 1,
        "experiment_count": 1,
    }
    assert result["summary"]["bench_identity_verdicts"][
        "unattributable_by_construction"
    ] == {"source_count": 0, "experiment_count": 0}


def test_obtained_single_cited_apparatus_is_not_pending(tmp_path, monkeypatch) -> None:
    work = _lead_corpus(
        tmp_path,
        [
            {
                "citing": "fixture-source",
                "lead_as_given": "Smith (1980)",
                "for_parameters": ["orifice diameter"],
                "resolution": "obtained",
                "obtained_path": "/corpus/raw/smith-1980/smith-1980.pdf",
            }
        ],
    )
    experiment = factories.kems_experiment(work_id=work.work_id)
    result = _report_with_work(tmp_path, monkeypatch, work, experiment)
    (source,) = result["sources"]
    (row,) = source["experiments"]
    assert row["bench_identity"]["basis"] == "cited_by_author"
    assert all(
        gap["reason"] != "reference_not_yet_resolved"
        for gap in row["informational_gaps"] + source["informational_gaps"]
    )
    assert result["summary"]["bench_identity_verdicts"][
        "reference_not_yet_resolved"
    ] == {"source_count": 0, "experiment_count": 0}


def test_multi_cited_source_counts_as_unattributable_not_pending(tmp_path, monkeypatch) -> None:
    work = _lead_corpus(
        tmp_path,
        [
            {
                "citing": "fixture-source",
                "lead_as_given": "Smith (1980)",
                "for_parameters": ["detailed apparatus construction"],
            },
            {
                "citing": "fixture-source",
                "lead_as_given": "Jones (1981)",
                "for_parameters": ["detailed apparatus construction"],
            },
        ],
    )
    experiment = factories.kems_experiment(work_id=work.work_id)
    result = _report_with_work(tmp_path, monkeypatch, work, experiment)
    (source,) = result["sources"]
    (row,) = source["experiments"]
    assert row["bench_identity"] is None
    assert result["summary"]["bench_identity_verdicts"] == {
        "unattributable_by_construction": {"source_count": 1, "experiment_count": 1},
        "reference_not_yet_resolved": {"source_count": 0, "experiment_count": 0},
    }


def test_oxygen_condition_graphite_c_co_is_derived_never_printed() -> None:
    """Graphite / C–CO buffer derives log fO2; authority stays DERIVED."""
    experiment = replace(
        factories.kems_experiment(total_P=Decimal("101325")),
        conditions={"temperature_K": factories.located(Value.point_of("1473.15"))},
        fO2_control=FO2Control(
            channel=factories.State.of(FO2Channel.BUFFER),
            buffer=factories.located("C-CO"),
        ),
        pressure_environment=replace(
            factories.kems_experiment(total_P=Decimal("101325")).pressure_environment,
            sweep_gas=factories.located(
                SweepGas(
                    species="CO",
                    flow_sccm=factories.State.unknown("not_published"),
                    partial_pressure_Pa=factories.State.of(Decimal("101325")),
                )
            ),
        ),
    )
    result = oxygen_condition(experiment, _bench())
    assert result.selected is not None
    assert result.selected.route == "graphite_c_co_buffer"
    assert result.selected.authority is WaypointAuthority.DERIVED
    # Sanity vs published CCO at 1473.15 K, 1.01325 bar (Jakobsson & Oskarsson).
    assert float(result.selected.value.point) == pytest.approx(-10.475256412619215, abs=1e-9)


def test_oxygen_condition_c_co_prose_buffer_without_token_stays_refusal() -> None:
    """Free-text CO/Ar prose is not a C–CO token and must not invent fO2."""
    experiment = replace(
        factories.kems_experiment(total_P=Decimal("101325")),
        conditions={"temperature_K": factories.located(Value.point_of("1473.15"))},
        fO2_control=FO2Control(
            channel=factories.State.of(FO2Channel.COMMANDED),
            buffer=factories.located("CO partial pressure controlled by CO/Ar mixing"),
        ),
    )
    result = oxygen_condition(experiment, _bench())
    assert result.selected is None
    assert not any(route.route == "graphite_c_co_buffer" for route in result.routes)


def test_oxygen_condition_c_co_uses_total_p_when_co_is_the_stated_gas() -> None:
    """When sweep CO PP is absent, printed total P is P_CO for a C–CO token."""
    experiment = replace(
        factories.kems_experiment(total_P=Decimal("101325")),
        conditions={"temperature_K": factories.located(Value.point_of("1373.15"))},
        fO2_control=FO2Control(
            channel=factories.State.of(FO2Channel.BUFFER),
            buffer=factories.located("graphite-CO"),
        ),
        pressure_environment=replace(
            factories.kems_experiment(total_P=Decimal("101325")).pressure_environment,
            sweep_gas=factories.located(
                SweepGas(
                    species="CO",
                    flow_sccm=factories.State.unknown("not_published"),
                    partial_pressure_Pa=factories.State.unknown("not_published"),
                )
            ),
        ),
    )
    result = oxygen_condition(experiment, _bench())
    assert result.selected is not None
    assert result.selected.route == "graphite_c_co_buffer"
    assert result.selected.authority is WaypointAuthority.DERIVED
    assert float(result.selected.value.point) == pytest.approx(-11.553088871754722, abs=1e-9)


def test_oxygen_condition_c_co_refuses_co_ar_alternatives_without_printed_p() -> None:
    """CO-or-CO/Ar alternatives without a printed P_CO must not pick a pressure."""
    unknown = factories.State.unknown("not_published")
    alternatives = (
        SweepGas(species="CO", flow_sccm=unknown, partial_pressure_Pa=unknown),
        SweepGas(
            species=None,
            flow_sccm=unknown,
            partial_pressure_Pa=unknown,
            components=(
                SweepGasComponent(
                    species="CO",
                    mole_fraction=unknown,
                    flow_sccm=unknown,
                    partial_pressure_Pa=unknown,
                ),
                SweepGasComponent(
                    species="Ar",
                    mole_fraction=unknown,
                    flow_sccm=unknown,
                    partial_pressure_Pa=unknown,
                ),
            ),
        ),
    )
    experiment = replace(
        factories.kems_experiment(),
        conditions={"temperature_K": factories.located(Value.point_of("1473.15"))},
        fO2_control=FO2Control(
            channel=factories.State.of(FO2Channel.BUFFER),
            buffer=factories.located("C-CO"),
        ),
        pressure_environment=replace(
            factories.kems_experiment().pressure_environment,
            total_pressure_Pa=factories.Located(factories.State.unknown("not_published")),
            sweep_gas=factories.located(
                SweepGas(
                    species=None,
                    flow_sccm=unknown,
                    partial_pressure_Pa=unknown,
                    alternatives=alternatives,
                )
            ),
        ),
    )
    result = oxygen_condition(experiment, _bench())
    assert result.selected is None
    assert not any(route.route == "graphite_c_co_buffer" for route in result.routes)
