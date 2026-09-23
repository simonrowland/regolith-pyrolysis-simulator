from dataclasses import replace
from decimal import Decimal
import math

import pytest

from simulator.battery.consumer_inputs import collect_consumer_inputs, REQUIREMENTS
from simulator.battery.generators import engine_point_requests, kems_case, vacuum_pyrolysis_preset
from simulator.battery.enums import AmountBasis, BenchIdentityBasis, ValueKind
from simulator.battery.records import (
    Bench, BenchIdentity, BenchReference, Composition, Sample, State, Value,
    FO2Control, ThermalSchedule, ThermalPoint, ApparatusGeometry, Located,
)
from simulator.battery.waypoints import (
    charge_moles_by_species, oxygen_condition, consumer_readiness,
    WaypointAuthority, ReadinessStatus, GapReason,
)
from tests.battery import factories as f


def case(*, single=False, pressure="1", oxygen=True):
    experiment = replace(f.kems_experiment(total_P=Decimal(pressure)),
        sample=Sample(mass_kg=f.located(Value.point_of("0.0001")),
            printed_composition=f.located({"MgO": Decimal(100)} if single else {"MgO": Decimal(50), "SiO2": Decimal(50)})),
        thermal_schedule=ThermalSchedule(points=(
            ThermalPoint(f.located(Value.point_of(0)), f.located(Value.point_of(1400))),
            ThermalPoint(f.located(Value.point_of(3600)), f.located(Value.point_of(1400))),
        )))
    geometry = ApparatusGeometry(orifice_diameter_m=f.located(Value.point_of("0.001")),
        orifice_area_m2=f.located(Value.point_of(str(math.pi * .001**2 / 4))),
        clausing_factor=f.located(Value.point_of("0.5")))
    bench = Bench("bench", "work-1", BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
                  geometry=geometry, cell_material_and_liner=f.located("Pt"))
    point = {"temperature_K": f.located(Decimal(1400))}
    if oxygen:
        point["fO2_log"] = f.located(Decimal(-9))
    observation = replace(f.observation("obs", experiment.experiment_id, f.o2_identity(), 1), point_conditions=point)
    return experiment, bench, observation


def complete_kems():
    experiment, bench, observation = case(single=True)
    conditions = dict(observation.point_conditions)
    conditions.update({name: f.located(value) for name, value in {
        "post_mass_kg": Decimal("0.00009"), "purity_fraction": Decimal("0.999"),
        "temperature_uncertainty_K": Decimal(2), "repeat_count": Decimal(3), "hold_duration_s": Decimal(3600),
        "temperature_program": {"mode": "isothermal", "start_K": 1400, "end_K": 1400,
            "step_K": 1, "range_status": "reported", "isothermal_hold": {
                "temperature_K": 1400, "temperature_uncertainty_K": 2, "duration_h": 1}},
        "calibration": {"standard": "Ag", "method": "reference", "sensitivity_factor": 1,
                        "sensitivity_factor_units": "A/Pa", "source_locator": "p. 2"},
        "measurement_selectors": ({"observable_id": "obs", "observable": "partial_pressure_pa",
                                  "species": "Mg", "evidence_scope": "species"},),
    }.items()})
    experiment = replace(experiment, apparatus=replace(experiment.apparatus, calibration=None))
    return experiment, bench, replace(observation, point_conditions=conditions)


def complete_rps():
    experiment, bench, observation = case()
    observation = replace(observation, point_conditions={**observation.point_conditions, "surfaces": f.located(({
        "id": "wall", "area_m2": .001, "temperature_C": 25,
    },))})
    boundary = {key: {**value, "source_class": "literature_sidecar", "citation_id": "paper", "extraction_note": "p. 1"}
        for key, value in {"background_gas": {"species": "Ar"}, "imposed_flow": {"value": 10, "unit": "sccm"},
                           "pressure_control": {"mode": "regulated"}}.items()}
    observation = replace(observation, point_conditions={**observation.point_conditions, "gas_boundary": f.located(boundary)})
    model = {"surfaces": {"wall": {"role": "chamber_wall", "view_factor_from_melt": .2,
                                  "line_of_sight_to_melt": True}},
        "feedstock_id": "test-feed", "furnace_ceiling_C": 1200}
    return experiment, bench, observation, model


def test_fifty_observation_masses_never_collapse_and_are_derived():
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=Sample())
    points = []
    for index in range(50):
        mass = Decimal(index + 1) / Decimal(1000000)
        point = replace(observation, observation_id=f"obs-{index}", point_conditions={
            "mass_kg": f.located(mass), "printed_composition": f.located({"MgO": 100})})
        result = charge_moles_by_species(experiment, bench, point)["MgO"].selected
        assert result.authority is WaypointAuthority.DERIVED
        assert result.inputs == (f"observation[obs-{index}].point_conditions.mass_kg",
                                 f"observation[obs-{index}].point_conditions.printed_composition")
        points.append(result.value.point)
    assert len(set(points)) == 50
    assert not charge_moles_by_species(experiment, bench)
    assert float(points[0]) == pytest.approx(1e-6 / .040304, rel=1e-4)


def test_uncontrolled_oxygen_refuses_every_engine():
    experiment, bench, observation = case(oxygen=False)
    assert oxygen_condition(experiment, bench, observation).selected is None
    results = engine_point_requests(collect_consumer_inputs(experiment, bench, observation))
    assert len(results) == 8
    assert all(result.payload is None for result in results)
    assert all(any(gap.waypoint == "oxygen_condition" for gap in result.readiness.gaps) for result in results)


def test_vacuum_total_pressure_supplies_flagged_oxygen_bound_to_engine():
    experiment, bench, observation = case(oxygen=False, pressure="1e-4")
    experiment = replace(experiment, pressure_environment=replace(
        experiment.pressure_environment,
        total_pressure_Pa=f.located(
            Value(ValueKind.BOUND, bound_operator="<", bound_value=Decimal("1e-4")),
            note="printed vacuum during run",
        ),
    ))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    oxygen = inputs.waypoints["oxygen_condition"].selected
    assert oxygen is not None
    assert oxygen.route == "vacuum_total_pressure_upper_bound"
    assert oxygen.authority is WaypointAuthority.EXTRAPOLATED
    assert oxygen.value.point == Decimal("-9")
    assert oxygen.notice is not None
    assert "upper bound from printed vacuum 0.0001 Pa" in oxygen.notice
    assert "bound, not a measurement" in oxygen.notice
    results = engine_point_requests(inputs)
    assert all(result.payload is not None for result in results)
    for result in results:
        output = result.provenance["output_routes"]["fO2_log"]
        assert output["authority"] == "extrapolated"
        assert output["method_class"] == "calculated"
        assert output["notice"] == oxygen.notice


def test_oxygen_precedence_keeps_printed_and_derived_routes_above_vacuum_bound():
    experiment, bench, observation = case(pressure="1e-4")
    printed = oxygen_condition(experiment, bench, observation).selected
    assert printed is not None
    assert printed.authority is WaypointAuthority.PRINTED
    assert printed.route == "observation_fO2_log"

    derived_observation = replace(observation, point_conditions={
        "temperature_K": f.located(Decimal(1400)),
        "fO2_Pa": f.located(Value.point_of("1e-5")),
    })
    derived = oxygen_condition(experiment, bench, derived_observation).selected
    assert derived is not None
    assert derived.authority is WaypointAuthority.DERIVED
    assert derived.route == "observation_fO2_Pa_to_log_fO2"


def test_apparatus_ultimate_vacuum_without_run_pressure_refuses_oxygen_bound():
    experiment, bench, observation = case(oxygen=False)
    pressure = replace(
        experiment.pressure_environment,
        total_pressure_Pa=Located(State.unknown("apparatus-only ultimate vacuum")),
        pumping={"base_pressure_Pa": f.located(Value.point_of("1e-4"))},
    )
    experiment = replace(experiment, pressure_environment=pressure)
    result = oxygen_condition(experiment, bench, observation)
    assert result.selected is None
    assert not any(route.route == "vacuum_total_pressure_upper_bound" for route in result.routes)


def test_buffer_derivation_and_domain():
    experiment, bench, observation = case(oxygen=False, pressure="100000")
    experiment = replace(experiment, fO2_control=FO2Control(State.unknown("buffer"), buffer=f.located("IW")))
    observation = replace(observation, point_conditions={"temperature_K": f.located(Decimal(1000))})
    result = oxygen_condition(experiment, bench, observation).selected
    assert result.authority is WaypointAuthority.DERIVED
    assert result.value.point == Decimal("-20.787")
    assert "experiment.fO2_control.buffer" in result.inputs
    assert any("temperature_K" in name for name in result.inputs)
    outside = replace(observation, point_conditions={"temperature_K": f.located(Decimal(2000))})
    assert oxygen_condition(experiment, bench, outside).selected is None


def test_printed_gas_composition_derives_oxygen():
    experiment, bench, observation = case(oxygen=False, pressure="100000")
    observation = replace(observation, point_conditions={**observation.point_conditions,
        "gas_composition": f.located(Composition("gas", (("O2", Decimal(".2")), ("Ar", Decimal(".8"))), AmountBasis.MOLE_FRACTION))})
    result = oxygen_condition(experiment, bench, observation).selected
    assert result.authority is WaypointAuthority.DERIVED
    assert float(result.value.point) == pytest.approx(math.log10(.2))


def test_printed_control_pressure_outranks_gas_composition_derivation():
    """A printed fO2-control pO2 is direct evidence; the observation
    gas_composition x_O2 * P_total derivation must not shadow it."""
    experiment, bench, observation = case(oxygen=False, pressure="100000")
    experiment = replace(experiment, fO2_control=FO2Control(State.unknown("channel"),
        oxygen_partial_pressure_Pa=f.located(Value.point_of(1))))
    observation = replace(observation, point_conditions={**observation.point_conditions,
        "gas_composition": f.located(Composition("gas", (("O2", Decimal(".2")), ("Ar", Decimal(".8"))), AmountBasis.MOLE_FRACTION))})
    result = oxygen_condition(experiment, bench, observation)
    assert result.selected.authority is WaypointAuthority.DERIVED
    assert result.selected.route == "oxygen_partial_pressure_to_log_fO2"
    assert result.selected.value.point == -5
    assert "observation_gas_composition" in {route.route for route in result.routes}


def test_point_oxygen_pressure_derives_log_units():
    experiment, bench, observation = case(oxygen=False)
    observation = replace(observation, point_conditions={"fO2_Pa": f.located(Value.point_of("0.001"))})
    result = oxygen_condition(experiment, bench, observation).selected
    assert result.authority is WaypointAuthority.DERIVED
    assert result.value.point == -8
    assert result.inputs == ("observation[obs].point_conditions.fO2_Pa",)


@pytest.mark.parametrize("reduced,oxidized", [("CO", "CO2"), ("H2", "H2O")])
def test_gas_couple_route_obeys_mass_action(reduced, oxidized):
    experiment, bench, observation = case(oxygen=False)
    values = []
    for ratio in (Decimal(1), Decimal(100)):
        composition = Composition("gas", ((reduced, 1 / (1 + ratio)),
            (oxidized, ratio / (1 + ratio))), AmountBasis.MOLE_FRACTION)
        point = replace(observation, point_conditions={**observation.point_conditions,
            "gas_composition": f.located(composition)})
        selected = oxygen_condition(experiment, bench, point).selected
        assert selected.authority is WaypointAuthority.DERIVED
        assert selected.route == "observation_gas_couple_equilibrium"
        assert "observation[obs].point_conditions.temperature_K" in selected.inputs
        values.append(float(selected.value.point))
    # M + 1/2 O2 = MO: fO2=(pMO/(K*pM))**2 at fixed T.
    assert values[1] - values[0] == pytest.approx(4)


def test_engine_inputs_all_eight_and_provenance():
    experiment, bench, observation = case()
    bench = replace(bench, identity=BenchIdentity(BenchIdentityBasis.CITED_BY_AUTHOR,
        ref=BenchReference("cited", "Earlier apparatus paper", ("apparatus",), f.loc(source_path="cited.pdf", page=7))))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    results = engine_point_requests(inputs)
    assert {item.payload["engine"] for item in results} == {
        "internal-analytical", "alphamelts", "thermoengine", "vaporock", "magemin", "cached-real", "imcc_sf04", "imcc_sf04_ext"}
    for result in results:
        assert result.payload["temperature_C"] == 1126.85
        assert result.payload["pressure_bar"] == 1e-5
        assert result.payload["fO2_log"] == -9
        assert result.provenance["bench_identity"]["ref"]["locator"]["source_path"] == "cited.pdf"
        assert "experiment.thermal_schedule.points[0].temperature_K" in result.provenance["evidence"]
    inferred = replace(bench, identity=BenchIdentity(BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE, reason="embedded"))
    result = engine_point_requests(collect_consumer_inputs(experiment, inferred, observation))[0]
    assert result.provenance["bench_identity"]["basis"] == "inferred_from_embedded_evidence"


@pytest.mark.parametrize("name", ["temperature_K", "total_pressure_Pa", "fO2_log"])
def test_engine_bounds_are_preserved_and_refused_without_midpoint(name):
    experiment, bench, observation = case()
    interval = Value(ValueKind.INTERVAL, interval_low=Decimal(1), interval_high=Decimal(2))
    observation = replace(observation, point_conditions={**observation.point_conditions, name: f.located(interval)})
    results = engine_point_requests(collect_consumer_inputs(experiment, bench, observation))
    assert all(result.payload is None for result in results)
    waypoint = {"total_pressure_Pa": "pressure_boundary", "fO2_log": "oxygen_condition"}.get(name, name)
    assert results[0].provenance["waypoints"][waypoint]["selected"]["value"]["kind"] == "interval"


@pytest.mark.parametrize("name", ["temperature_K", "total_pressure_Pa", "fO2_log"])
def test_engine_interval_refusal_is_typed_interval_needs_point(name):
    """An interval is a real printed fact; the engine needs a point. The typed
    refusal must say so — 'interval printed, engine needs point' — rather than
    the generic unsupported-print-form kind (t-953)."""
    experiment, bench, observation = case()
    interval = Value(ValueKind.INTERVAL, interval_low=Decimal(1), interval_high=Decimal(2))
    observation = replace(observation, point_conditions={**observation.point_conditions, name: f.located(interval)})
    results = engine_point_requests(collect_consumer_inputs(experiment, bench, observation))
    waypoint = {"total_pressure_Pa": "pressure_boundary", "fO2_log": "oxygen_condition"}.get(name, name)
    assert all(result.payload is None for result in results)
    for result in results:
        assert result.readiness.status is ReadinessStatus.GAP
        assert any(gap.waypoint == waypoint and gap.reason is GapReason.INTERVAL_NEEDS_POINT
                   for gap in result.readiness.gaps), result.readiness.gaps


def test_engine_bound_refusal_stays_unsupported_print_form():
    """Only the interval form is re-typed; bounds keep the generic refusal."""
    experiment, bench, observation = case()
    bound = Value(ValueKind.BOUND, bound_operator="<=", bound_value=Decimal(2))
    observation = replace(observation, point_conditions={**observation.point_conditions, "fO2_log": f.located(bound)})
    results = engine_point_requests(collect_consumer_inputs(experiment, bench, observation))
    assert all(result.payload is None for result in results)
    for result in results:
        assert any(gap.waypoint == "oxygen_condition" and gap.reason is GapReason.UNSUPPORTED_PRINT_FORM
                   for gap in result.readiness.gaps), result.readiness.gaps


def test_single_species_is_not_applicable():
    results = engine_point_requests(collect_consumer_inputs(*case(single=True)))
    assert all(result.readiness.status is ReadinessStatus.NOT_APPLICABLE for result in results)


def test_kems_payload_passes_real_validator():
    from simulator.diagnostic_helpers.kems import validate_kems_case
    result = kems_case(collect_consumer_inputs(*complete_kems()))
    assert result.readiness.status is ReadinessStatus.READY, result.readiness.gaps
    validate_kems_case(result.payload)
    assert result.payload["samples"][0]["initial_mass_mg"] == 100
    assert result.payload["provider_inputs"]["pO2_bar"] == 1e-9


def test_kems_refuses_reduced_charge_when_print_names_dropped_species():
    """A printed composition naming a species no charge route can cover (no
    molar mass) must refuse the reduced charge, mirroring the engine_point
    dropped-species refusal; the reduced set is never scored as complete."""
    experiment, bench, observation = complete_kems()
    experiment = replace(experiment, sample=replace(experiment.sample,
        printed_composition=f.located({"MgO": Decimal("97"), "Unobtainium2O": Decimal("3")})))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    assert list(inputs.charges) == ["MgO"]
    assert inputs.charges.dropped == ("Unobtainium2O",)
    result = kems_case(inputs)
    assert result.payload is None
    assert result.readiness.status is ReadinessStatus.GAP
    assert any(gap.reason is GapReason.UNSUPPORTED_PRINT_FORM
               and "Unobtainium2O" in gap.waypoint for gap in result.readiness.gaps)


@pytest.mark.parametrize("name", REQUIREMENTS["kems"])
def test_every_kems_requirement_refuses_and_readiness_agrees(name):
    inputs = collect_consumer_inputs(*complete_kems())
    from simulator.battery.waypoints import SpeciesWaypoints, _result
    if name == "charge_moles_by_species":
        inputs = replace(inputs, charges=SpeciesWaypoints({}))
    else:
        inputs = replace(inputs, waypoints={**inputs.waypoints, name: _result(name, [], (name,))})
    result = kems_case(inputs)
    assert result.payload is None
    assert any(gap.waypoint == name for gap in result.readiness.gaps)


def test_geometric_area_never_satisfies_effective_area():
    experiment, bench, observation = complete_kems()
    bench = replace(bench, geometry=replace(bench.geometry, clausing_factor=None))
    result = kems_case(collect_consumer_inputs(experiment, bench, observation))
    assert result.payload is None
    assert any(gap.waypoint == "effective_escape_area" for gap in result.readiness.gaps)


def test_rps_pressure_floor_not_clamped():
    result = vacuum_pyrolysis_preset(collect_consumer_inputs(*case(pressure="0.001")))
    assert result.payload is None
    assert result.readiness.status is ReadinessStatus.NOT_APPLICABLE
    assert result.readiness.gaps[0].reason is GapReason.BELOW_PRESSURE_FLOOR


def test_rps_requires_operator_choices_and_labels_them():
    experiment, bench, observation, model = complete_rps()
    inputs = collect_consumer_inputs(experiment, bench, observation)
    refusal = vacuum_pyrolysis_preset(inputs)
    assert refusal.payload is None
    assert any(gap.waypoint == "operator.surfaces" for gap in refusal.readiness.gaps)
    result = vacuum_pyrolysis_preset(inputs, modelling_inputs=model)
    assert result.readiness.status is ReadinessStatus.READY, result.readiness.gaps
    surface = result.payload["lab_geometry"]["surfaces"][0]
    assert surface["source_class"] == "assumption_with_sensitivity_marker"
    assert surface["area_m2"] == .001
    assert result.payload["modelling_assumptions"]["authority"] == "assumed"
    assert result.payload["lab_schedule"]["duration_h"] == 1
    model["surfaces"]["wall"]["area_m2"] = 999
    result = vacuum_pyrolysis_preset(inputs, modelling_inputs=model)
    assert result.payload["lab_geometry"]["surfaces"][0]["area_m2"] == .001
    readiness = consumer_readiness(experiment, bench, observation, modelling_inputs=model)
    assert next(item for item in readiness if item.consumer == "rps") == result.readiness


def test_unsupplied_operator_inputs_are_not_evidence_gaps():
    experiment, bench, observation, model = complete_rps()
    inputs = collect_consumer_inputs(experiment, bench, observation)
    refusal = vacuum_pyrolysis_preset(inputs)
    assert refusal.readiness.status is ReadinessStatus.GAP
    assert refusal.payload is None
    operator_gaps = [gap for gap in refusal.readiness.gaps if gap.waypoint.startswith("operator.")]
    assert {gap.waypoint for gap in operator_gaps} == {
        "operator.surfaces", "operator.feedstock_id", "operator.furnace_ceiling_C"}
    assert all(gap.reason is GapReason.CONSUMER_INPUT_NOT_SUPPLIED for gap in operator_gaps)
    assert not any(gap.reason is GapReason.MISSING_EVIDENCE for gap in operator_gaps)
    rps = next(item for item in consumer_readiness(experiment, bench, observation)
               if item.consumer == "rps")
    assert {gap.waypoint: gap.reason for gap in rps.gaps if gap.waypoint.startswith("operator.")} == {
        gap.waypoint: gap.reason for gap in operator_gaps}
    result = vacuum_pyrolysis_preset(inputs, modelling_inputs=model)
    assert result.readiness.status is ReadinessStatus.READY
    assert not any(gap.waypoint.startswith("operator.") for gap in result.readiness.gaps)


def test_modelling_inputs_never_touch_kems_or_engine_point_readiness():
    experiment, bench, observation, model = complete_rps()
    without = consumer_readiness(experiment, bench, observation)
    with_model = consumer_readiness(experiment, bench, observation, modelling_inputs=model)
    for consumer in ("kems", "engine_point"):
        assert [item for item in without if item.consumer == consumer] == [
            item for item in with_model if item.consumer == consumer]


def test_typed_calibration_routes_preserve_inference():
    from simulator.battery.records import Derivation
    experiment, bench, observation = case()
    inferred = Located(State.of(Decimal(2)), f.loc(page=8),
        Derivation("unit_conversion", ("printed_uncertainty",), (), "K"))
    bench = replace(bench, temperature_calibration={"standard": f.located("Au")},
                    temperature_measurement={"uncertainty_K": inferred})
    experiment = replace(experiment, apparatus=replace(experiment.apparatus,
        calibration={"sensitivity_factor": inferred}))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    assert inputs.waypoints["temperature_calibration"].selected.value == {"standard": "Au"}
    assert inputs.waypoints["temperature_uncertainty_K"].selected.authority is WaypointAuthority.DERIVED
    assert inputs.waypoints["calibration"].selected.authority is WaypointAuthority.DERIVED
    assert "experiment.apparatus.calibration.sensitivity_factor" in inputs.evidence


def test_rps_missing_physical_surface_evidence_refuses_even_with_operator_area():
    experiment, bench, observation, model = complete_rps()
    observation = replace(observation, point_conditions={key: value for key, value in observation.point_conditions.items() if key != "surfaces"})
    model["surfaces"]["wall"].update(area_m2=1, temperature_C=25)
    result = vacuum_pyrolysis_preset(collect_consumer_inputs(experiment, bench, observation), modelling_inputs=model)
    assert result.payload is None
    assert any(gap.waypoint == "surfaces" for gap in result.readiness.gaps)


def test_rps_requires_literature_gas_boundary():
    experiment, bench, observation, model = complete_rps()
    model["gas_boundary"] = observation.point_conditions["gas_boundary"].state.value
    observation = replace(observation, point_conditions={key: value for key, value in observation.point_conditions.items() if key != "gas_boundary"})
    result = vacuum_pyrolysis_preset(collect_consumer_inputs(experiment, bench, observation), modelling_inputs=model)
    assert result.payload is None
    assert any(gap.waypoint == "gas_boundary" for gap in result.readiness.gaps)


@pytest.mark.parametrize("consumer", ["kems", "engine_point"])
def test_cli_generates_schema_inputs_with_numeric_json(tmp_path, monkeypatch, consumer):
    import json
    import scripts.bench_generate as cli

    experiment, bench, observation = complete_kems() if consumer == "kems" else case()
    experiment = replace(experiment, bench_id=bench.id)
    monkeypatch.setattr(cli, "load_migrated_store", lambda root: ({"work-1": f.work()},
        {experiment.experiment_id: experiment}, {observation.observation_id: observation}))
    monkeypatch.setattr(cli, "load_migrated_benches", lambda root: {bench.id: bench})
    monkeypatch.setattr(cli, "_apparatus_references", lambda root, works: {})
    assert cli.main(["--consumer", consumer, "--source", "work-1", "--output", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["counts"]["generated"] == (1 if consumer == "kems" else 8)
    payload = json.loads((tmp_path / "00000000.json").read_text())
    if consumer == "kems":
        from simulator.diagnostic_helpers.kems import validate_kems_case
        validate_kems_case(payload)
    else:
        assert isinstance(payload["temperature_C"], float)


def test_rps_never_expands_observation_mass_or_pressure_into_run():
    experiment, bench, observation, model = complete_rps()
    observation = replace(observation, point_conditions={**observation.point_conditions,
        "total_pressure_Pa": f.located(Decimal("0.001")), "mass_kg": f.located(Decimal(999))})
    result = vacuum_pyrolysis_preset(collect_consumer_inputs(experiment, bench, observation), modelling_inputs=model)
    assert result.readiness.status is ReadinessStatus.READY
    assert result.payload["lab_schedule"]["chamber_pressure_mbar"][0]["value"] == .01
    assert result.payload["lab_geometry"]["sample"]["mass_g"] == .1
    experiment = replace(experiment, sample=replace(experiment.sample, mass_kg=None),
        pressure_environment=replace(experiment.pressure_environment, total_pressure_Pa=Located(State.unknown("point only"))))
    result = vacuum_pyrolysis_preset(collect_consumer_inputs(experiment, bench, observation), modelling_inputs=model)
    assert result.payload is None
    assert {"run_mass_kg", "run_pressure_boundary"} <= {gap.waypoint for gap in result.readiness.gaps}


def test_derived_observation_scope_outranks_printed_run_scope():
    from simulator.battery.records import Derivation
    experiment, bench, observation = case()
    converted = lambda value: Located(State.of(Value.point_of(value)), f.loc(page=3),
        Derivation("unit_conversion", ("printed_operand",), (), "SI"))
    observation = replace(observation, point_conditions={**observation.point_conditions,
        "temperature_K": converted(1000), "total_pressure_Pa": converted(10), "mass_kg": converted("0.0002")})
    inputs = collect_consumer_inputs(experiment, bench, observation)
    for name, expected in (("temperature_K", 1000), ("pressure_boundary", 10), ("mass_kg", Decimal("0.0002"))):
        selected = inputs.waypoints[name].selected
        assert selected.value.point == expected
        assert selected.authority is WaypointAuthority.DERIVED
