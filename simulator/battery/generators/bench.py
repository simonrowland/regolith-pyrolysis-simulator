"""Consumer inputs generated exclusively from collected waypoints."""

from dataclasses import dataclass, replace
from decimal import Decimal
from collections.abc import Mapping

from simulator.battery.consumer_inputs import ConsumerInputs, REQUIREMENTS
from simulator.battery.enums import ValueKind
from simulator.battery.records import Value
from simulator.battery.migrate import to_plain
from simulator.battery.waypoints import (
    ConsumerReadiness, ReadinessStatus, ReadinessGap, GapReason,
    ENGINE_POINT_CONSUMERS, WaypointFlag,
)


@dataclass(frozen=True)
class GeneratedInput:
    readiness: ConsumerReadiness
    payload: Mapping | None
    provenance: Mapping


def _provenance(inputs):
    return to_plain({"experiment_id": inputs.experiment_id, "observation_id": inputs.observation_id,
        "source_id": inputs.source_id, "bench_identity": inputs.bench_identity,
        "waypoints": inputs.waypoints, "charge_moles_by_species": inputs.charges,
        "run_charge_moles_by_species": inputs.run_charges,
        "evidence": inputs.evidence})


def _requirements(inputs, consumer, engine=None):
    constraints = next(item for item in inputs.constraints if item.consumer == consumer and item.engine == engine)
    gaps = list(constraints.gaps)
    if constraints.status is ReadinessStatus.NOT_APPLICABLE:
        return constraints
    for name in REQUIREMENTS[consumer]:
        if name in {"charge_moles_by_species", "run_charge_moles_by_species"}:
            charges = inputs.run_charges if name == "run_charge_moles_by_species" else inputs.charges
            absent = not charges
            missing = charges.absence.missing if charges.absence else ()
        else:
            waypoint = inputs.waypoints[name]
            absent = waypoint.selected is None
            missing = waypoint.absence.missing if waypoint.absence else ()
            if not absent and consumer == "engine_point":
                if not isinstance(waypoint.selected.value, Value) or waypoint.selected.value.kind is not ValueKind.POINT:
                    gaps.append(ReadinessGap(name, GapReason.UNSUPPORTED_PRINT_FORM, (name,)))
        if absent:
            gaps.append(ReadinessGap(name, GapReason.MISSING_EVIDENCE, missing))
    return replace(constraints, status=ReadinessStatus.GAP if gaps else ReadinessStatus.READY,
                   gaps=tuple(dict.fromkeys(gaps)))


class UnsupportedValue(ValueError):
    pass


def _point(inputs, name):
    value = inputs.waypoints[name].selected.value
    if not isinstance(value, Value) or value.kind is not ValueKind.POINT:
        raise UnsupportedValue(name)
    return value.point


def _document(inputs, name):
    value = inputs.waypoints[name].selected.value
    if not isinstance(value, Mapping):
        raise UnsupportedValue(name)
    return to_plain(value)


def _refused(readiness, provenance, name, reason=GapReason.UNSUPPORTED_PRINT_FORM):
    return GeneratedInput(replace(readiness, status=ReadinessStatus.GAP,
        gaps=(*readiness.gaps, ReadinessGap(name, reason, (name,)))), None, provenance)


def engine_point_requests(inputs: ConsumerInputs) -> tuple[GeneratedInput, ...]:
    results = []
    provenance = _provenance(inputs)
    for engine in ENGINE_POINT_CONSUMERS:
        readiness = _requirements(inputs, "engine_point", engine)
        if readiness.status is not ReadinessStatus.READY:
            results.append(GeneratedInput(readiness, None, provenance))
            continue
        try:
            composition = {}
            for species, waypoint in inputs.charges.items():
                value = waypoint.selected.value
                if value.kind is not ValueKind.POINT or value.point < 0:
                    raise UnsupportedValue("charge_moles_by_species." + species)
                composition[species] = float(value.point)
            temperature = _point(inputs, "temperature_K")
            pressure = _point(inputs, "pressure_boundary")
            oxygen = _point(inputs, "oxygen_condition")
            if temperature <= 0 or pressure < 0 or not any(composition.values()):
                raise UnsupportedValue("physical_inputs")
            # Definitions: Celsius = kelvin - 273.15; 1 bar = 100000 Pa.
            # Units: K -> degC, Pa/(Pa/bar) -> bar. 1500 K -> 1226.85 C;
            # 1 Pa -> 1e-5 bar. These projections are DERIVED, never PRINTED.
            payload = {"engine": engine, "temperature_C": float(temperature - Decimal("273.15")),
                       "pressure_bar": float(pressure / Decimal(100000)), "fO2_log": float(oxygen),
                       "composition_mol": composition}
            results.append(GeneratedInput(readiness, payload, {**provenance, "output_routes": {
                "temperature_C": {"authority": "derived", "waypoint": "temperature_K", "formula": "K - 273.15"},
                "pressure_bar": {"authority": "derived", "waypoint": "pressure_boundary", "formula": "Pa / 100000"},
                "fO2_log": {"waypoint": "oxygen_condition"},
                "composition_mol": {"waypoint": "charge_moles_by_species"},
            }}))
        except UnsupportedValue as exc:
            results.append(_refused(readiness, provenance, str(exc)))
    return tuple(results)


def kems_case(inputs: ConsumerInputs) -> GeneratedInput:
    readiness = _requirements(inputs, "kems")
    provenance = _provenance(inputs)
    provenance["output_routes"] = {
        "oxide.formula": {"waypoint": "charge_moles_by_species", "authority": "derived", "formula": "single species key"},
        "oxide.purity_fraction": {"waypoint": "purity_fraction"},
        "samples[].initial_mass_mg": {"waypoint": "mass_kg", "authority": "derived", "formula": "kg * 1000000"},
        "samples[].post_mass_mg": {"waypoint": "post_mass_kg", "authority": "derived", "formula": "kg * 1000000"},
        "cell.material": {"waypoint": "cell_material"},
        "cell.orifice_diameter_m": {"waypoint": "orifice_diameter_m"},
        "cell.orifice_area_m2": {"waypoint": "orifice_area_m2"},
        "cell.transmission_factor": {"waypoint": "clausing_factor"},
        "temperature_program": {"waypoint": "temperature_program"},
        "temperature_program.temperature_uncertainty_K": {"waypoint": "temperature_uncertainty_K"},
        "temperature_program.repeat_count": {"waypoint": "repeat_count"},
        "temperature_program.hold_s": {"waypoint": "hold_duration_s"},
        "exterior_chamber_pressure": {"waypoint": "pressure_boundary"},
        "provider_inputs.pO2_bar": {"waypoint": "oxygen_condition", "authority": "derived", "formula": "10 ** log_fO2"},
        "calibration": {"waypoint": "calibration"},
        "measurement_selectors": {"waypoint": "measurement_selectors"},
    }
    if readiness.status is not ReadinessStatus.READY:
        return GeneratedInput(readiness, None, provenance)
    from simulator.diagnostic_helpers.kems import validate_kems_case, KEMSSchemaError
    try:
        if len(inputs.charges) != 1:
            raise UnsupportedValue("single_oxide_formula")
        escape = inputs.waypoints["effective_escape_area"].selected
        if WaypointFlag.GEOMETRIC_ONLY in escape.flags:
            raise UnsupportedValue("effective_escape_area")
        formula = next(iter(inputs.charges))
        material = inputs.waypoints["cell_material"].selected.value
        if not isinstance(material, Value) or material.kind is not ValueKind.CATEGORICAL:
            raise UnsupportedValue("cell_material")
        program = _document(inputs, "temperature_program")
        for field, name in (("temperature_uncertainty_K", "temperature_uncertainty_K"),
                            ("repeat_count", "repeat_count"), ("hold_s", "hold_duration_s")):
            program[field] = float(_point(inputs, name))
        pressure = inputs.waypoints["pressure_boundary"].selected.value
        if pressure.kind is ValueKind.POINT:
            operator, pressure_value = "=", pressure.point
        elif pressure.kind is ValueKind.BOUND:
            operator, pressure_value = pressure.bound_operator, pressure.bound_value
        else:
            raise UnsupportedValue("pressure_boundary")
        selectors = inputs.waypoints["measurement_selectors"].selected.value
        if not isinstance(selectors, (tuple, list)):
            raise UnsupportedValue("measurement_selectors")
        # Definitions: 1 kg = 1e6 mg; log_fO2 = log10(pO2/bar).
        # Algebra: m_mg=m_kg*1e6; pO2_bar=10**log_fO2. Unit check mg and bar.
        # Sanity: .0001 kg -> 100 mg; log_fO2=-5 -> 1e-5 bar.
        case = {"schema_version": 1, "case_id": inputs.observation_id or inputs.experiment_id,
            "source_id": inputs.source_id,
            "oxide": {"formula": formula, "identity": formula,
                      "purity_fraction": float(_point(inputs, "purity_fraction")), "purity_source": provenance},
            "samples": [{"measurement_id": inputs.observation_id or inputs.experiment_id,
                         "initial_mass_mg": float(_point(inputs, "mass_kg") * Decimal(1000000)),
                         "post_mass_mg": float(_point(inputs, "post_mass_kg") * Decimal(1000000))}],
            "cell": {"material": material.categorical,
                     "orifice_diameter_m": float(_point(inputs, "orifice_diameter_m")),
                     "orifice_area_m2": float(_point(inputs, "orifice_area_m2")),
                     "transmission_factor": {"status": "reported" if inputs.waypoints["clausing_factor"].selected.authority.value == "printed" else "derived",
                         "value": float(_point(inputs, "clausing_factor")), "derivation": provenance,
                         "source_locator": provenance}},
            "temperature_program": program,
            "exterior_chamber_pressure": {"operator": operator, "value_pa": float(pressure_value), "source_locator": provenance},
            "provider_inputs": {"pO2_bar": float(Decimal(10) ** _point(inputs, "oxygen_condition")),
                                "status": "derived", "rationale": provenance},
            "calibration": _document(inputs, "calibration"), "measurement_selectors": to_plain(selectors),
            "citations": [{"source_id": inputs.source_id, "citation": inputs.source_id, "doi": None,
                           "url": None, "locators": provenance}], "assumptions": []}
        validate_kems_case(case)
        return GeneratedInput(readiness, case, provenance)
    except (UnsupportedValue, KEMSSchemaError) as exc:
        return _refused(readiness, provenance, str(exc))


def vacuum_pyrolysis_preset(inputs: ConsumerInputs, *, modelling_inputs=None) -> GeneratedInput:
    readiness = _requirements(inputs, "rps")
    provenance = _provenance(inputs)
    provenance["output_routes"] = {
        "lab_geometry.sample.mass_g": {"waypoint": "run_mass_kg", "authority": "derived", "formula": "kg * 1000"},
        "lab_geometry.surfaces.physical_fields": {"waypoint": "surfaces"},
        "lab_geometry.surfaces.modelling_fields": {"authority": "assumed", "input": "operator.surfaces"},
        "lab_schedule.melt_temperature_C": {"waypoint": "thermal_path", "authority": "derived", "formula": "(s / 3600, K - 273.15)"},
        "lab_schedule.duration_h": {"waypoint": "thermal_path", "authority": "derived", "formula": "final s / 3600"},
        "lab_schedule.chamber_pressure_mbar": {"waypoint": "run_pressure_boundary", "authority": "derived", "formula": "Pa / 100"},
        "lab_schedule.surface_temperature_C": {"waypoint": "surface_temperature_C"},
        "lab_schedule.gas_boundary": {"waypoint": "gas_boundary"},
        "lab_schedule.furnace_ceiling_C": {"authority": "assumed", "input": "operator.furnace_ceiling_C"},
        "pair.faithful.feedstock_id": {"authority": "assumed", "input": "operator.feedstock_id"},
    }
    if readiness.status is ReadinessStatus.NOT_APPLICABLE:
        return GeneratedInput(readiness, None, provenance)
    model = modelling_inputs or {}
    required_model = ("surfaces", "feedstock_id", "furnace_ceiling_C")
    missing_model = tuple("operator." + key for key in required_model if key not in model)
    if missing_model:
        for name in missing_model:
            readiness = replace(readiness, status=ReadinessStatus.GAP,
                gaps=(*readiness.gaps, ReadinessGap(name, GapReason.MISSING_EVIDENCE, (name,))))
    if readiness.status is not ReadinessStatus.READY:
        return GeneratedInput(readiness, None, provenance)
    from simulator.lab_geometry import parse_lab_geometry, LabGeometryError
    from simulator.lab_schedule import normalize_lab_schedule, LabScheduleValidationError
    try:
        thermal = inputs.waypoints["thermal_path"].selected.value
        if thermal.kind is not ValueKind.SERIES or len(thermal.series) < 2:
            raise UnsupportedValue("thermal_path")
        # Definitions: 3600 s/h, Celsius=K-273.15, 100 Pa/mbar, 1000 g/kg.
        # Algebra: t_h=t_s/3600, T_C=T_K-273.15, P_mbar=P_Pa/100, m_g=1000*m_kg.
        # Sanity: 3600 s=1 h; 1500 K=1226.85 C; .1 Pa=.001 mbar; .001 kg=1 g.
        points = [{"t_h": float(t / Decimal(3600)), "value": float(T - Decimal("273.15")), "unit": "C"}
                  for t, T in thermal.series]
        duration = points[-1]["t_h"]
        if not duration.is_integer():
            raise UnsupportedValue("thermal_path.integer_duration_h_required_by_runner")
        pressure = float(_point(inputs, "run_pressure_boundary") / Decimal(100))
        surfaces = []
        physical_surfaces = inputs.waypoints["surfaces"].selected.value
        if not isinstance(physical_surfaces, (tuple, list)):
            raise UnsupportedValue("surfaces")
        for physical in physical_surfaces:
            surface_id = physical.get("id")
            choices = model["surfaces"].get(surface_id, {})
            for key in ("role", "view_factor_from_melt", "line_of_sight_to_melt"):
                if key not in choices:
                    raise UnsupportedValue("operator.surfaces." + key)
            surface = {key: value for key, value in physical.items() if key in (
                "id", "area_m2", "temperature_C", "temperature_profile", "distance_from_melt_m",
                "equivalent_diameter_m", "liner_material")}
            surface.update({key: choices[key] for key in ("role", "view_factor_from_melt", "line_of_sight_to_melt")})
            surfaces.append({**surface, "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "operator_geometry", "extraction_note": "Operator modelling input; not extracted literature evidence."})
        geometry = {"id": inputs.experiment_id + ".geometry", "scale": "gram_lab",
                    "equipment_sizing": "lab_fixed_geometry", "surfaces": surfaces,
                    "sample": {"mass_g": float(_point(inputs, "run_mass_kg") * Decimal(1000))}}
        schedule = {"id": inputs.experiment_id + ".schedule", "duration_h": duration,
            "interpolation": "piecewise_linear", "interpolation_source_class": "assumption_with_sensitivity_marker",
            "interpolation_citation_id": "operator_modelling",
            "interpolation_extraction_note": "Piecewise-linear interpolation between evidence waypoints is a modelling assumption.",
            "furnace_ceiling_C": model["furnace_ceiling_C"], "melt_temperature_C": points,
            "chamber_pressure_mbar": [{"t_h": 0, "value": pressure, "unit": "mbar"}, {"t_h": duration, "value": pressure, "unit": "mbar"}],
            "gas_boundary": _document(inputs, "gas_boundary"), "surface_temperature_C": (
                _document(inputs, "surface_temperature_C") if inputs.waypoints["surface_temperature_C"].selected else {})}
        geometry_parsed = parse_lab_geometry(geometry, allow_temperature_profiles=True)
        normalize_lab_schedule(schedule, required_surface_profiles=[s.temperature_profile for s in geometry_parsed.surfaces if s.temperature_profile])
        payload = {"schema_version": "vacuum_pyrolysis_preset.v1", "lab_schedule": schedule,
            "lab_geometry": geometry, "pair": {"faithful": {"feedstock_id": model["feedstock_id"],
                "schedule_id": schedule["id"], "geometry_id": geometry["id"]}},
            "modelling_assumptions": {"authority": "assumed", "inputs": model,
                "note": "Operator choices, not extracted values; source_class is always assumption_with_sensitivity_marker."}}
        return GeneratedInput(readiness, payload, provenance)
    except (UnsupportedValue, LabGeometryError, LabScheduleValidationError) as exc:
        return _refused(readiness, provenance, str(exc))
