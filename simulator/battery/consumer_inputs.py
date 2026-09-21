"""Collect consumer evidence once; modelling choices are never literature facts."""

from dataclasses import dataclass, fields, is_dataclass
from collections.abc import Mapping
from decimal import Decimal

from simulator.battery.records import Located, Value
from simulator.battery.enums import ValueKind
from simulator.battery.waypoints import (
    Waypoint, WaypointResult, WaypointAuthority, SpeciesWaypoints,
    ConsumerReadiness, _result, charge_moles_by_species, thermal_path,
    pressure_boundary, oxygen_condition, effective_escape_area,
)


REQUIREMENTS = {
    "engine_point": ("charge_moles_by_species", "temperature_K", "pressure_boundary", "oxygen_condition"),
    "kems": ("charge_moles_by_species", "mass_kg", "post_mass_kg", "purity_fraction",
             "orifice_diameter_m", "orifice_area_m2", "clausing_factor", "effective_escape_area",
             "cell_material", "temperature_program", "temperature_uncertainty_K", "repeat_count",
             "hold_duration_s", "pressure_boundary", "oxygen_condition", "calibration",
             "measurement_selectors"),
    "rps": ("run_charge_moles_by_species", "run_mass_kg", "thermal_path", "run_pressure_boundary", "surfaces", "gas_boundary"),
}


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
        result = _result(name, routes, tuple(path for path, _ in ((prefix + "." + name, None), *candidates)))
        if routes and routes[0].route.startswith("observation["):
            return WaypointResult(name, routes[0], tuple(routes))
        return result

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
        "thermal_path": thermal_path(experiment, bench),
        "temperature_K": temperature,
        "pressure_boundary": pressure_boundary(experiment, bench, observation),
        "run_pressure_boundary": pressure_boundary(experiment, bench),
        "oxygen_condition": oxygen_condition(experiment, bench, observation),
        "effective_escape_area": effective_escape_area(experiment, bench),
    }
    for name in ("mass_kg", "post_mass_kg", "purity_fraction", "temperature_program",
                 "temperature_uncertainty_K", "repeat_count", "hold_duration_s",
                 "measurement_selectors", "pressure_calibration", "temperature_calibration", "surfaces", "surface_temperature_C", "gas_boundary"):
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
        _consumer_constraints(experiment, bench, observation))
