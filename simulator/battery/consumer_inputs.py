"""Collect consumer evidence once; modelling choices are never literature facts."""

from dataclasses import dataclass, fields, is_dataclass
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256

from simulator.battery.records import (
    Located,
    StandardState,
    Value,
    nominal_pressure_pa,
)
from simulator.battery.enums import ValueKind
from simulator.source_ids import SOSSI_SOURCE_ID
from simulator.battery.waypoints import (
    Waypoint, WaypointResult, WaypointAuthority, SpeciesWaypoints,
    ConsumerReadiness, _AUTHORITY_RANK, _result, charge_moles_by_species, thermal_path,
    pressure_boundary, oxygen_condition, effective_escape_area, normalized_composition,
    identity_composition_waypoint, is_melt_activity_observation, is_pure_substance_engine_reference,
    reference_state_is_known, reference_state_value,
)


REQUIREMENTS = {
    "engine_point": ("normalized_composition", "temperature_K", "pressure_boundary", "oxygen_condition"),
    # Condensed-phase activity at fixed T and composition. Pressure is not an
    # input (the PV term is negligible here). Oxygen is required when the
    # composition or the measured species contains a multivalent element.
    # That conditional check is not a static tuple entry.
    "melt_activity": ("normalized_composition", "temperature_K"),
    "kems": ("charge_moles_by_species", "mass_kg", "post_mass_kg", "purity_fraction",
             "orifice_diameter_m", "orifice_area_m2", "clausing_factor", "effective_escape_area",
             "cell_material", "temperature_program", "temperature_uncertainty_K", "repeat_count",
             "hold_duration_s", "pressure_boundary", "oxygen_condition", "calibration",
             "measurement_selectors"),
    "rps": ("run_charge_moles_by_species", "run_mass_kg", "thermal_path", "run_pressure_boundary", "surfaces", "gas_boundary"),
}


def _sossi_table2_run_id(experiment):
    from simulator.battery.oxygen_identity import _sossi_experiment_run_id

    locator = experiment.locator
    return _sossi_experiment_run_id(
        {
            "locator": (
                {
                    "note": locator.note,
                    "table": locator.table,
                    "page": locator.page,
                }
                if locator is not None
                else None
            )
        }
    )


def _sossi_table2_gas_boundary(experiment, observation, *, expected_run_id=None):
    if (
        observation is None
        or observation.source_id != SOSSI_SOURCE_ID
    ):
        return None
    temperature = (observation.point_conditions or {}).get("temperature_K")
    row_locator = temperature.locator if isinstance(temperature, Located) else None
    if (
        row_locator is None
        or str(row_locator.page) not in {"54", "55"}
        or row_locator.table != "2"
        or "quote=" not in (row_locator.note or "")
    ):
        row_locator = observation.locator
    if row_locator is None:
        row_locator = experiment.locator
    if row_locator is None:
        return None
    note = row_locator.note or ""
    marker = "quote="
    is_table2_row = str(row_locator.page) in {"54", "55"} and row_locator.table == "2"
    reason = None if is_table2_row else "sossi_table2_gas_record_not_located"
    quote = note.split(marker, 1)[1] if marker in note and is_table2_row else None
    from simulator.battery.oxygen_identity import (
        _sossi_table2_approximate_total_flow_sccm,
        _sossi_table2_gas_row_from_quote,
    )

    if expected_run_id is None:
        expected_run_id = _sossi_table2_run_id(experiment)
    run_id = expected_run_id
    co_sccm = co2_sccm = None
    if quote is not None:
        try:
            run_id, _temperature_C, co_sccm, co2_sccm, _printed_log = (
                _sossi_table2_gas_row_from_quote(quote, expected_run_id=expected_run_id)
            )
        except ValueError as exc:
            reason = str(exc)
    else:
        reason = "sossi_table2_gas_quote_missing"
    if observation.experiment_id != experiment.experiment_id:
        reason = "sossi_table2_gas_row_experiment_mismatch"

    citation_id = observation.source_id
    quote_digest = sha256((quote or "").encode("utf-8")).hexdigest()
    row_source = {
        "source_class": "literature_primary",
        "citation_id": citation_id,
        "source_ref": (
            f"PDF page {row_locator.page}, Table 2, run {run_id}"
            if is_table2_row
            else f"PDF Table 2 run {run_id}; gas quote not located"
        ),
    }
    pressure_method_implied = (
        nominal_pressure_pa(experiment.pressure_environment.total_pressure_Pa) is None
    )
    not_reported = {
        **row_source,
        "reported_status": "not_reported",
        "digest": quote_digest,
    }
    if co_sccm is None and co2_sccm is None:
        background_gas = (
            {**not_reported, "reason": reason}
            if reason
            else {**row_source, "species": "air"}
        )
        imposed_flow_reason = reason or (
            "The source leaves both CO and CO2 flow columns blank for this run."
        )
    else:
        background_gas = {
            **not_reported,
            "reason": "The source reports separate CO and CO2 component flows.",
        }
        imposed_flow_reason = "The source reports component flows separately."
    boundary = {
        "background_gas": background_gas,
        "imposed_flow": {
            **not_reported,
            "reason": imposed_flow_reason,
        },
        "pressure_control": {
            "mode": "controlled",
            "pressure_driver": "imposed",
            **(
                {"pressure_basis": "DP-002 method_implied"}
                if pressure_method_implied
                else {}
            ),
            "source_class": "literature_primary",
            "citation_id": citation_id,
            "source_ref": "PDF page 2: one-atmosphere gas-mixing furnace",
        },
    }
    component_flows = {
        species: {
            **row_source,
            "value": value,
            "unit": "sccm",
        }
        for species, value in (("CO", co_sccm), ("CO2", co2_sccm))
        if value is not None
    }
    if component_flows:
        boundary["component_flows"] = component_flows
        boundary["flow_reference_state"] = {
            **not_reported,
            "reason": "sccm_reference_state_unreported",
            "conversion_role": "diagnostic_only",
            "conversion_note": (
                "Total pressure is imposed and fO2 uses the CO/CO2 ratio, "
                "so standard-state conversion is not load-bearing."
            ),
        }
        approximate_total_flow = _sossi_table2_approximate_total_flow_sccm(
            run_id, co_sccm, co2_sccm
        )
        if approximate_total_flow is not None:
            boundary["reported_total_flow"] = {
                **row_source,
                "value": approximate_total_flow,
                "unit": "sccm",
                "qualifier": "approximate",
                "source_ref": (
                    "PDF page 6, section 2.1: approximately 200 sccm; "
                    "air and ANU runs excepted"
                ),
            }
    return boundary


def _sossi_table2_flow_observation(experiment, observations):
    try:
        run_id = _sossi_table2_run_id(experiment)
    except ValueError:
        return None
    fallback_mn = None
    fallback_any = None
    located_mn = None
    located_any = None
    for observation in observations:
        species = getattr(observation.identity, "species", None)
        if (
            observation.source_id != SOSSI_SOURCE_ID
            or observation.experiment_id != experiment.experiment_id
            or observation.admission.status.value != "admitted"
        ):
            continue
        temperature = (observation.point_conditions or {}).get("temperature_K")
        has_located_temperature = (
            isinstance(temperature, Located)
            and temperature.state.is_value
            and temperature.locator is not None
        )
        if getattr(species, "formula", None) != "Mn":
            fallback_any = fallback_any or observation
            if has_located_temperature:
                located_any = located_any or observation
            continue
        boundary = _sossi_table2_gas_boundary(
            experiment, observation, expected_run_id=run_id
        )
        if boundary is not None:
            if "component_flows" in boundary or boundary["imposed_flow"].get(
                "reason", ""
            ).startswith("The source leaves both CO and CO2 flow columns blank"):
                return observation
        fallback_mn = fallback_mn or observation
        if has_located_temperature:
            located_mn = located_mn or observation
    return located_mn or located_any or fallback_mn or fallback_any


@dataclass(frozen=True)
class ConsumerInputs:
    experiment_id: str
    observation_id: str | None
    source_id: str
    bench_identity: object
    waypoints: Mapping[str, WaypointResult]
    charges: SpeciesWaypoints
    run_charges: SpeciesWaypoints
    evidence: Mapping[str, object]
    constraints: tuple[ConsumerReadiness, ...]
    # Activity / activity-coefficient rows only. Other observables are not this consumer.
    melt_activity: bool = False
    # Per-row mole map from identity.composition. Absent when the identity has none.
    identity_composition: Waypoint | None = None
    pure_substance_reference: bool = False
    # Unknown reference state is not a melt activity the engines can run.
    reference_state_known: bool = False
    # Printed standard state when it is a value. Engines still have to match it.
    reference_state: StandardState | None = None
    # activity or activity_coefficient. Absent on every other quantity.
    activity_quantity: str | None = None
    # Measured species formula. A multivalent solute can sit outside the bulk map.
    measured_species: str | None = None
    # Experiment method token (State[MethodToken]); engine-point uses it with the
    # shared knudsen chamber-background predicate.
    method: object | None = None


def _activity_quantity(observation) -> str | None:
    if not is_melt_activity_observation(observation):
        return None
    from simulator.battery.identity import quantity_token

    quantity = quantity_token(observation.identity)
    return None if quantity is None else quantity.value


def collect_consumer_inputs(experiment, bench, observation=None) -> ConsumerInputs:
    from simulator.battery.waypoints import _consumer_constraints

    evidence = {}

    def index(value, path):
        if isinstance(value, Located):
            evidence[path] = value
        if is_dataclass(value):
            for field in fields(value):
                index(getattr(value, field.name), path + "." + field.name)
        elif isinstance(value, Mapping):
            for key, child in value.items():
                index(child, path + "." + str(key))
        elif isinstance(value, (tuple, list)):
            for number, child in enumerate(value):
                index(child, path + f"[{number}]")

    index(experiment, "experiment")
    index(bench, "bench")
    if observation is not None:
        index(observation, f"observation[{observation.observation_id}]")
    point = observation.point_conditions or {} if observation is not None else {}
    prefix = f"observation[{observation.observation_id}].point_conditions" if observation else "observation.point_conditions"

    def fact(name, candidates=()):
        routes = []
        for path, located in ((prefix + "." + name, point.get(name)), *candidates):
            if located is None or not located.state.is_value:
                continue
            value = located.state.value
            if isinstance(value, (Decimal, int, float)):
                value = Value.point_of(value)
            elif isinstance(value, str):
                value = Value(ValueKind.CATEGORICAL, categorical=value)
            routes.append(Waypoint(name, value, path,
                WaypointAuthority.DERIVED if located.inference else WaypointAuthority.PRINTED, (path,)))
        # The observation point condition specializes the run scope, so it
        # stays first; remaining candidates rank by authority, ties keep the
        # caller's candidate order.
        head = routes[:1] if routes and routes[0].route.startswith("observation[") else []
        tail = routes[len(head):]
        tail = sorted(tail, key=lambda route: _AUTHORITY_RANK[route.authority], reverse=True)
        return _result(name, head + tail, tuple(path for path, _ in ((prefix + "." + name, None), *candidates)))

    thermal = thermal_path(experiment, bench, observation)
    temperature = thermal
    if thermal.selected is not None and thermal.selected.value.kind is ValueKind.SERIES:
        values = {value for _, value in thermal.selected.value.series}
        if len(values) == 1:
            from dataclasses import replace
            selected = replace(thermal.selected, name="temperature_K", value=Value.point_of(next(iter(values))))
            temperature = WaypointResult("temperature_K", selected, (selected,))
        else:
            temperature = _result("temperature_K", [], ("observation.point_conditions.temperature_K",))
    waypoints = {
        "normalized_composition": normalized_composition(experiment, bench, observation),
        "thermal_path": (
            thermal
            if observation is not None and observation.source_id == SOSSI_SOURCE_ID
            else thermal_path(experiment, bench)
        ),
        "temperature_K": temperature,
        "pressure_boundary": pressure_boundary(experiment, bench, observation),
        "run_pressure_boundary": pressure_boundary(experiment, bench),
        "oxygen_condition": oxygen_condition(experiment, bench, observation),
        "effective_escape_area": effective_escape_area(experiment, bench),
    }
    for name in ("mass_kg", "post_mass_kg", "purity_fraction", "temperature_program",
                 "temperature_uncertainty_K", "repeat_count", "hold_duration_s",
                 "measurement_selectors", "pressure_calibration", "temperature_calibration", "surfaces", "surface_temperature_C", "gas_boundary"):
        if name == "gas_boundary":
            source_boundary = _sossi_table2_gas_boundary(experiment, observation)
            if source_boundary is not None:
                route_path = (
                    f"observation[{observation.observation_id}]"
                    ".point_conditions.temperature_K"
                )
                route = Waypoint(
                    name,
                    source_boundary,
                    route_path,
                    (
                        WaypointAuthority.EXTRAPOLATED
                        if "reported_total_flow" in source_boundary
                        else WaypointAuthority.PRINTED
                    ),
                    (
                        route_path,
                        "experiment.pressure_environment.total_pressure_Pa",
                    ),
                )
                waypoints[name] = WaypointResult(name, route, (route,))
                continue
        candidates = []
        if name == "mass_kg":
            candidates.append(("experiment.sample.mass_kg", experiment.sample.mass_kg))
        if name == "hold_duration_s" and experiment.thermal_schedule:
            holds = experiment.thermal_schedule.setpoints_and_holds or ()
            if len(holds) == 1:
                candidates.append(("experiment.thermal_schedule.setpoints_and_holds[0].hold_duration_s", holds[0].hold_duration_s))
        if name == "temperature_uncertainty_K":
            for path, measurement in (("bench.temperature_measurement", bench.temperature_measurement),
                ("experiment.apparatus.temperature_measurement", experiment.apparatus.temperature_measurement if experiment.apparatus else None)):
                for key in ("temperature_uncertainty_K", "uncertainty_K"):
                    candidates.append((path + "." + key, (measurement or {}).get(key)))
        candidates.append(("experiment.conditions." + name, experiment.conditions.get(name)))
        if name in {"temperature_uncertainty_K", "pressure_calibration", "temperature_calibration"}:
            candidates.extend((f"bench.other_facts.{name}", item.value) for item in bench.other_facts if item.name == name)
            for path, located in candidates:
                if located is not None:
                    evidence[path] = located
        waypoints[name] = fact(name, tuple(candidates))
    run_mass = experiment.sample.mass_kg
    run_routes = []
    if run_mass is not None and run_mass.state.is_value:
        run_routes.append(Waypoint("run_mass_kg", run_mass.state.value, "experiment.sample.mass_kg",
            WaypointAuthority.DERIVED if run_mass.inference else WaypointAuthority.PRINTED, ("experiment.sample.mass_kg",)))
    waypoints["run_mass_kg"] = _result("run_mass_kg", run_routes, ("experiment.sample.mass_kg",))
    if experiment.apparatus and experiment.apparatus.wall:
        waypoints["surfaces"] = fact("surfaces", (("experiment.apparatus.wall.surfaces", experiment.apparatus.wall.get("surfaces")),))
    for name in ("orifice_diameter_m", "orifice_area_m2", "clausing_factor"):
        candidates = []
        for path, geometry in (("bench.geometry", bench.geometry),
                               ("experiment.apparatus.geometry", experiment.apparatus.geometry if experiment.apparatus else None)):
            if geometry is not None:
                candidates.append((path + "." + name, getattr(geometry, name)))
        waypoints[name] = fact(name, tuple(candidates))
    waypoints["cell_material"] = fact("cell_material", (
        ("bench.cell_material_and_liner", bench.cell_material_and_liner),
        ("experiment.apparatus.cell_material_and_liner", experiment.apparatus.cell_material_and_liner if experiment.apparatus else None),
    ))
    calibration = experiment.apparatus.calibration if experiment.apparatus else None
    waypoints["calibration"] = fact("calibration")
    for name, path, mapping in (("calibration", "experiment.apparatus.calibration", calibration),
        ("temperature_calibration", "bench.temperature_calibration", bench.temperature_calibration)):
        if mapping and all(item.state.is_value for item in mapping.values()):
            inputs = tuple(path + "." + key for key in mapping)
            item = Waypoint(name, {key: item.state.value for key, item in mapping.items()},
                            path, WaypointAuthority.DERIVED if any(item.inference for item in mapping.values()) else WaypointAuthority.PRINTED, inputs)
            routes = (*waypoints[name].routes, item)
            waypoints[name] = WaypointResult(name, waypoints[name].selected or item, routes)
    return ConsumerInputs(experiment.experiment_id, observation.observation_id if observation else None,
        observation.source_id or bench.work_id if observation else bench.work_id, bench.identity,
        waypoints, charge_moles_by_species(experiment, bench, observation), charge_moles_by_species(experiment, bench), evidence,
        _consumer_constraints(experiment, bench, observation),
        melt_activity=is_melt_activity_observation(observation),
        identity_composition=identity_composition_waypoint(observation),
        pure_substance_reference=observation is not None and is_pure_substance_engine_reference(observation),
        reference_state_known=reference_state_is_known(observation),
        reference_state=reference_state_value(observation),
        activity_quantity=_activity_quantity(observation),
        measured_species=(
            observation.identity.species.formula
            if observation is not None and observation.identity.species is not None
            else None
        ),
        method=experiment.method,
    )
