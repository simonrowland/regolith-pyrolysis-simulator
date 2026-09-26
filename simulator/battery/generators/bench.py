"""Consumer inputs generated exclusively from collected waypoints."""

from dataclasses import dataclass, replace
from decimal import Decimal
from collections.abc import Mapping

from simulator.battery.consumer_inputs import ConsumerInputs, REQUIREMENTS
from simulator.battery.enums import Phase, ReferenceStateConvention, ValueKind
from simulator.battery.records import StandardState, Value, phase_token
from simulator.battery.migrate import to_plain
from simulator.battery.waypoints import (
    ConsumerReadiness, ReadinessStatus, ReadinessGap, GapReason,
    ENGINE_POINT_CONSUMERS, MELT_ACTIVITY_ENGINES, Waypoint, WaypointFlag,
    WaypointResult, pure_substance_engine_point_gap,
)


_FLAGGED_AUTHORITIES = {"assumed", "bound", "extrapolated"}


def _readiness_flags(provenance: Mapping, payload) -> tuple[Mapping[str, object], ...]:
    if payload is None:
        return ()
    flags = []
    for output, route in (provenance.get("output_routes") or {}).items():
        if not isinstance(route, Mapping):
            continue
        authority = str(route.get("authority") or "")
        if authority not in _FLAGGED_AUTHORITIES:
            continue
        flag = {
            "waypoint": str(route.get("waypoint") or output),
            "authority": authority,
            "flag_id": str(route.get("flag_id") or output),
        }
        if route.get("notice") is not None:
            flag["notice"] = route["notice"]
        flags.append(flag)
    return tuple(flags)


@dataclass(frozen=True)
class GeneratedInput:
    readiness: ConsumerReadiness
    payload: Mapping | None
    provenance: Mapping

    def __post_init__(self) -> None:
        flags = _readiness_flags(self.provenance, self.payload)
        if flags != self.readiness.flags:
            object.__setattr__(self, "readiness", replace(self.readiness, flags=flags))


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
            if not absent and consumer == "engine_point" and name != "normalized_composition":
                selected = waypoint.selected.value
                if (name == "pressure_boundary"
                        and isinstance(selected, Value)
                        and selected.kind is ValueKind.BOUND
                        and selected.bound_operator in {"<", "<=", "≤"}
                        and inputs.waypoints["oxygen_condition"].selected is not None
                        and inputs.waypoints["oxygen_condition"].selected.route == "vacuum_total_pressure_upper_bound"):
                    continue
                if not isinstance(selected, Value) or selected.kind is not ValueKind.POINT:
                    reason = (GapReason.INTERVAL_NEEDS_POINT
                              if isinstance(selected, Value) and selected.kind is ValueKind.INTERVAL
                              else GapReason.UNSUPPORTED_PRINT_FORM)
                    gaps.append(ReadinessGap(name, reason, (name,)))
        if absent:
            reason = (waypoint.absence.reason if name == "normalized_composition" and waypoint.absence
                      else GapReason.MISSING_EVIDENCE)
            gaps.append(ReadinessGap(name, reason, missing))
    return replace(constraints, status=ReadinessStatus.GAP if gaps else ReadinessStatus.READY,
                   gaps=tuple(dict.fromkeys(gaps)))


class UnsupportedValue(ValueError):
    pass


# Each element has more than one oxidation state in silicate melts over the
# fO2 range these engines are asked to run. Omitting fO2 lets the engine
# invent that redox state. Composition names and the measured species are
# mapped onto this table; the element symbols are not repeated elsewhere.
# Fe: Fe2+ and Fe3+, and Fe0 at very low fO2. Every MELTS iron alias, including
#     the total-iron prints, maps here.
# Ti: Ti4+ and Ti3+.
# Cr: Cr3+ and Cr2+.
# Mn: Mn2+ and Mn3+.
# V: V3+, V4+, and V5+.
# Eu: Eu3+ and Eu2+.
# Ce: Ce4+ and Ce3+.
# S: sulfide S2- and sulfate S6+.
# Ga: Ga3+ and Ga+. Bischof measures Ga dissolved in CMAS, so Ga is not in the bulk map.
MULTIVALENT_MELT_ELEMENTS: dict[str, str] = {
    "Fe": "Fe2+/Fe3+",
    "Ti": "Ti4+/Ti3+",
    "Cr": "Cr3+/Cr2+",
    "Mn": "Mn2+/Mn3+",
    "V": "V3+/V4+/V5+",
    "Eu": "Eu3+/Eu2+",
    "Ce": "Ce4+/Ce3+",
    "S": "S2-/S6+",
    "Ga": "Ga3+/Ga+",
}

# Parent-oxide basis tokens already printed on rows. single_cation is a
# different activity and is not converted into this set.
_PARENT_OXIDE_COMPONENT_BASES = frozenset({"oxide", "parent", "parent_oxide"})


@dataclass(frozen=True)
class EngineReportedActivity:
    """The activity number this engine reports. A row must match it.

    alphaMELTS and ThermoEngine report pure-liquid-endmember activity. That
    equals a parent-oxide activity only for the MELTS oxide endmembers.
    IMCC ``parent_activity`` is x* of a parent oxide, relative to the pure
    liquid oxide. The basis token is not the formula. Nothing here converts
    one convention into the other.
    """

    reported: str
    convention: ReferenceStateConvention
    phase: Phase
    component_bases: frozenset[str]


_ENGINE_REPORTED_ACTIVITY = {
    engine: EngineReportedActivity(
        "raoultian_pure_liquid_endmember",
        ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
        Phase.L,
        _PARENT_OXIDE_COMPONENT_BASES,
    )
    for engine in ("alphamelts", "thermoengine")
}
_ENGINE_REPORTED_ACTIVITY.update({
    engine: EngineReportedActivity(
        "raoultian_pure_liquid_oxide_parent",
        ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
        Phase.L,
        _PARENT_OXIDE_COMPONENT_BASES,
    )
    for engine in ("imcc_sf04", "imcc_sf04_ext")
})


def _oxide_lookup_key(name: str) -> str:
    key = str(name).strip()
    if key.endswith("_Liq"):
        key = key[:-4]
    # FeO* is total iron written as FeO. The MELTS alias map keys FeO, not the star.
    if key.endswith("*"):
        key = key[:-1]
    return key


def _elements_of_component(name: str) -> frozenset[str]:
    """Elements in one composition name or species formula.

    Iron aliases come from the MELTS basis map (FeO_tot, FeOT, FeO_total,
    and the same keys in any case). A name the map does not know is parsed
    as a formula, which is how TiO2, V2O3, and Ga reach the element table.
    """

    from simulator.melt_backend.alphamelts import MELTS_OXIDE_ALIASES
    from simulator.reference_data.janaf import formula_composition

    key = _oxide_lookup_key(name)
    canonical = MELTS_OXIDE_ALIASES.get(key.lower())
    # FeO_total does not parse: the underscore is not a formula token.
    # Every MELTS total-iron alias is total Fe expressed as FeO.
    formula = "FeO" if canonical == "FeO_total" else (canonical or key)
    parsed = formula_composition(formula)
    if not parsed:
        return frozenset()
    return frozenset(element for element, _count in parsed)


def _is_multivalent(name: str) -> bool:
    return any(element in MULTIVALENT_MELT_ELEMENTS for element in _elements_of_component(name))


def _redox_components(composition: Mapping, species: str | None = None) -> tuple[str, ...]:
    """Names that hold a multivalent element at a positive amount, plus the species.

    A printed zero does not count, so FeO = 0 cannot hide TiO2.
    """

    present: list[str] = []
    for name, amount in composition.items():
        try:
            number = Decimal(str(amount))
        except (ArithmeticError, ValueError):
            continue
        if number <= 0:
            continue
        if _is_multivalent(str(name)):
            present.append(str(name))
    if species and _is_multivalent(species) and species not in present:
        present.append(species)
    return tuple(sorted(present))


def _melts_reports_oxide_endmember(formula: str) -> tuple[bool, str]:
    """Pure-liquid-oxide activity exists only for a MELTS oxide endmember.

    An empty activity map has no same-named oxide key, so
    ``melts_endmember_to_parent_oxide_activity`` returns its typed refusal
    for every parent that is not itself an oxide endmember (Na2O, CaO, MgO,
    FeO, K2O, and any other formula). Oxide endmembers only lack a number.
    The endmember set stays in that function.
    """

    from engines.alphamelts.domain import (
        MELTS_PARENT_OXIDE_NOT_ENDMEMBER,
        MELTS_LIQUID_OXIDE_ENDMEMBERS,
        MELTS_OXIDE_BASIS,
        melts_endmember_to_parent_oxide_activity,
    )

    # H2O is a liquid endmember label, but it is not admitted by the
    # composition adapter's 14-oxide basis. Do not advertise a request the
    # adapter will reject before it can report that label.
    if formula in MELTS_LIQUID_OXIDE_ENDMEMBERS and formula not in MELTS_OXIDE_BASIS:
        return False, f"typed-refusal:melts_composition_basis:{formula}"
    _value, reason = melts_endmember_to_parent_oxide_activity({}, formula)
    if reason.startswith(MELTS_PARENT_OXIDE_NOT_ENDMEMBER):
        return False, reason
    return True, "raoultian_pure_liquid_endmember"


def _imcc_reports_parent_oxide(formula: str) -> tuple[bool, str]:
    """IMCC ``parent_activity`` is x* on ``IMCC_PARENT_OXIDES``. Pure limit is 1."""

    from simulator.melt_backend.imcc_sf04.gas import IMCC_PARENT_OXIDES

    if formula in IMCC_PARENT_OXIDES:
        return True, "raoultian_pure_liquid_oxide_parent"
    parents = ", ".join(IMCC_PARENT_OXIDES)
    return False, (
        f"typed-refusal:not_imcc_parent_oxide:{formula}: "
        f"IMCC parent_activity is x* on {parents}, pure liquid oxide limit 1"
    )


def _engine_reports_formula(engine: str, formula: str) -> tuple[bool, str]:
    if engine in ("alphamelts", "thermoengine"):
        return _melts_reports_oxide_endmember(formula)
    if engine in ("imcc_sf04", "imcc_sf04_ext"):
        return _imcc_reports_parent_oxide(formula)
    return False, "typed-refusal:engine_does_not_report_parent_oxide_activity"


def _reference_matches(
    state: StandardState, reported: EngineReportedActivity, engine: str,
) -> tuple[bool, str]:
    """Convention, phase, basis token, and the endmember formula.

    The basis token is not the formula. ``NaO0.5`` with token ``oxide`` is
    still the single-cation formula. ``Na2SiO3`` with that token is not an
    IMCC parent oxide.
    """

    structural = (
        state.convention is reported.convention
        and phase_token(state.endmember) is reported.phase
        and state.component_basis in reported.component_bases
    )
    if not structural:
        return False, reported.reported
    return _engine_reports_formula(engine, state.endmember.formula)


def _reference_labels(state: StandardState, reported: str) -> tuple[str, str]:
    phase = phase_token(state.endmember)
    phase_name = phase.value if phase is not None else "phase_unknown"
    formula = state.endmember.formula
    return (
        f"{state.convention.value}:{phase_name}:{state.component_basis}:{formula}",
        reported,
    )


def _reference_gap(state: StandardState, reported: str) -> ReadinessGap:
    row, engine = _reference_labels(state, reported)
    return ReadinessGap("reference_state", GapReason.REFERENCE_STATE_MISMATCH, (row, engine))


def _melt_composition(inputs: ConsumerInputs) -> WaypointResult:
    """Row composition: an observation map, else the identity mole map, else the sample."""
    current = inputs.waypoints["normalized_composition"]
    selected = current.selected
    if selected is not None and str(selected.route).startswith("observation_"):
        return current
    identity = inputs.identity_composition
    if identity is not None:
        return WaypointResult("normalized_composition", identity, (identity, *current.routes))
    return current


def _point_gap(name: str, selected: Waypoint) -> ReadinessGap | None:
    value = selected.value
    if isinstance(value, Value) and value.kind is ValueKind.POINT:
        return None
    reason = (GapReason.INTERVAL_NEEDS_POINT
              if isinstance(value, Value) and value.kind is ValueKind.INTERVAL
              else GapReason.UNSUPPORTED_PRINT_FORM)
    return ReadinessGap(name, reason, (name,))


def _not_applicable(provenance, engine: str, gaps: tuple[ReadinessGap, ...] = ()) -> GeneratedInput:
    return GeneratedInput(
        ConsumerReadiness("melt_activity", ReadinessStatus.NOT_APPLICABLE, gaps, engine),
        None,
        provenance,
    )


def _point(inputs, name):
    value = inputs.waypoints[name].selected.value
    if not isinstance(value, Value) or value.kind is not ValueKind.POINT:
        raise UnsupportedValue(name)
    return value.point


def _engine_pressure_point(inputs):
    value = inputs.waypoints["pressure_boundary"].selected.value
    if (value.kind is ValueKind.BOUND
            and value.bound_operator in {"<", "<=", "≤"}
            and inputs.waypoints["oxygen_condition"].selected.route == "vacuum_total_pressure_upper_bound"):
        return value.bound_value
    return _point(inputs, "pressure_boundary")


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
            for species, value in inputs.waypoints["normalized_composition"].selected.value.items():
                if not value.is_finite() or value < 0:
                    raise UnsupportedValue("normalized_composition." + species)
                composition[species] = float(value)
            temperature = _point(inputs, "temperature_K")
            pressure = _engine_pressure_point(inputs)
            oxygen = _point(inputs, "oxygen_condition")
            oxygen_route = inputs.waypoints["oxygen_condition"].selected
            assert oxygen_route is not None
            if temperature <= 0 or pressure < 0 or not any(composition.values()):
                raise UnsupportedValue("physical_inputs")
            # Definitions: Celsius = kelvin - 273.15; 1 bar = 100000 Pa.
            # Units: K -> degC, Pa/(Pa/bar) -> bar. 1500 K -> 1226.85 C;
            # 1 Pa -> 1e-5 bar. These projections are DERIVED, never PRINTED.
            composition_route = inputs.waypoints["normalized_composition"].selected
            payload = {"engine": engine, "temperature_C": float(temperature - Decimal("273.15")),
                       "pressure_bar": float(pressure / Decimal(100000)), "fO2_log": float(oxygen),
                       "composition_mol": composition}
            composition_output = {"waypoint": "normalized_composition", "authority": "derived"}
            if composition_route is not None and composition_route.notice is not None:
                payload["composition_method_class"] = "calculated"
                payload["composition_notice"] = composition_route.notice
                composition_output.update({"method_class": "calculated", "notice": composition_route.notice})
            oxygen_output = {
                "waypoint": "oxygen_condition",
                "authority": oxygen_route.authority.value,
                "flag_id": oxygen_route.route,
            }
            if oxygen_route.notice is not None:
                oxygen_output.update({"notice": oxygen_route.notice, "method_class": "calculated"})
            results.append(GeneratedInput(readiness, payload, {**provenance, "output_routes": {
                "temperature_C": {"authority": "derived", "waypoint": "temperature_K", "formula": "K - 273.15"},
                "pressure_bar": {"authority": "derived", "waypoint": "pressure_boundary", "formula": "Pa / 100000"},
                "fO2_log": oxygen_output,
                "composition_mol": {**composition_output, "formula": "x_i * 1 mol reference charge"},
            }}))
        except UnsupportedValue as exc:
            results.append(_refused(readiness, provenance, str(exc)))
    return tuple(results)


def melt_activity_requests(inputs: ConsumerInputs) -> tuple[GeneratedInput, ...]:
    """Activity and activity-coefficient requests for engines that report activities.

    Required: a normalized composition, a point temperature, and a reference
    state whose endmember formula is the activity that engine reports. An
    oxygen point is required when the composition or the measured species
    holds a multivalent element. Pressure and the gas boundary are not inputs.
    A missing required input is a typed gap. A missing oxygen point is not
    filled: the scorer must not substitute PO2_ENGINE_DEFAULT for an activity
    comparison.
    """
    provenance = _provenance(inputs)
    if inputs.pure_substance_reference:
        gap = pure_substance_engine_point_gap()
        return tuple(_not_applicable(provenance, engine, (gap,)) for engine in MELT_ACTIVITY_ENGINES)
    if not inputs.melt_activity:
        return tuple(_not_applicable(provenance, engine) for engine in MELT_ACTIVITY_ENGINES)
    if not inputs.reference_state_known or not isinstance(inputs.reference_state, StandardState):
        # A vapour-referenced or untyped standard state is not a(oxide).
        # Excluded, not run with a guessed convention.
        gap = ReadinessGap(
            "reference_state", GapReason.MISSING_EVIDENCE, ("identity.reference_state",),
        )
        return tuple(_not_applicable(provenance, engine, (gap,)) for engine in MELT_ACTIVITY_ENGINES)
    state = inputs.reference_state
    composition = _melt_composition(inputs)
    gaps: list[ReadinessGap] = []
    if composition.selected is not None and len(composition.selected.value) == 1:
        gap = ReadinessGap("normalized_composition", GapReason.SINGLE_SPECIES_CHARGE)
        return tuple(_not_applicable(provenance, engine, (gap,)) for engine in MELT_ACTIVITY_ENGINES)
    if composition.selected is None:
        absence = composition.absence
        gaps.append(ReadinessGap(
            "normalized_composition",
            absence.reason if absence else GapReason.MISSING_EVIDENCE,
            absence.missing if absence else (),
        ))
    temperature = inputs.waypoints["temperature_K"]
    if temperature.selected is None:
        absence = temperature.absence
        gaps.append(ReadinessGap(
            "temperature_K",
            absence.reason if absence else GapReason.MISSING_EVIDENCE,
            absence.missing if absence else (),
        ))
    else:
        point_gap = _point_gap("temperature_K", temperature.selected)
        if point_gap is not None:
            gaps.append(point_gap)
    redox: tuple[str, ...] = ()
    if composition.selected is not None:
        redox = _redox_components(composition.selected.value, inputs.measured_species)
    elif inputs.measured_species and _is_multivalent(inputs.measured_species):
        redox = (inputs.measured_species,)
    if redox:
        oxygen = inputs.waypoints["oxygen_condition"]
        if oxygen.selected is None:
            absence = oxygen.absence
            gaps.append(ReadinessGap(
                "oxygen_condition",
                GapReason.MISSING_EVIDENCE,
                tuple(dict.fromkeys((*redox, *(absence.missing if absence else ())))),
            ))
        else:
            point_gap = _point_gap("oxygen_condition", oxygen.selected)
            if point_gap is not None:
                gaps.append(point_gap)
    status = ReadinessStatus.GAP if gaps else ReadinessStatus.READY
    gap_tuple = tuple(dict.fromkeys(gaps))
    results = []
    for engine in MELT_ACTIVITY_ENGINES:
        reported_activity = _ENGINE_REPORTED_ACTIVITY[engine]
        matches, engine_side = _reference_matches(state, reported_activity, engine)
        if not matches:
            # Henrian, 1 wt%, a pure solid, a gas endmember, a single-cation
            # basis, or an endmember formula this engine does not report.
            # Name both sides. Do not convert.
            results.append(_not_applicable(
                provenance,
                engine,
                (_reference_gap(state, engine_side),),
            ))
            continue
        readiness = ConsumerReadiness("melt_activity", status, gap_tuple, engine)
        if status is not ReadinessStatus.READY:
            results.append(GeneratedInput(readiness, None, provenance))
            continue
        if inputs.measured_species != state.endmember.formula:
            gap = ReadinessGap(
                "reference_state",
                GapReason.REFERENCE_STATE_MISMATCH,
                (
                    f"species:{inputs.measured_species}",
                    f"endmember:{state.endmember.formula}",
                ),
            )
            results.append(_not_applicable(provenance, engine, (gap,)))
            continue
        try:
            moles = {}
            for species, value in composition.selected.value.items():
                if not value.is_finite() or value < 0:
                    raise UnsupportedValue("normalized_composition." + species)
                moles[species] = float(value)
            temperature_K = _point(inputs, "temperature_K")
            if temperature_K <= 0 or not any(moles.values()):
                raise UnsupportedValue("physical_inputs")
            # Celsius = kelvin - 273.15. 1500 K -> 1226.85 C. Derived, never printed.
            reported = reported_activity.reported
            payload = {
                "engine": engine,
                "quantity": inputs.activity_quantity,
                "temperature_C": float(temperature_K - Decimal("273.15")),
                "composition_mol": moles,
                "reference_state": reported,
            }
            routes = {
                "temperature_C": {"authority": "derived", "waypoint": "temperature_K", "formula": "K - 273.15"},
                "composition_mol": {
                    "waypoint": "normalized_composition",
                    "authority": composition.selected.authority.value,
                    "route": composition.selected.route,
                },
                "pressure_bar": {"authority": "not_an_input", "reason": "condensed-phase activity; PV term omitted"},
                "quantity": {"waypoint": "identity.quantity"},
                "reference_state": {
                    "waypoint": "identity.reference_state",
                    "engine_reports": reported,
                },
            }
            if redox:
                oxygen_log = _point(inputs, "oxygen_condition")
                payload["fO2_log"] = float(oxygen_log)
                routes["fO2_log"] = {"waypoint": "oxygen_condition", "because": list(redox)}
            results.append(GeneratedInput(readiness, payload, {**provenance, "output_routes": routes}))
        except UnsupportedValue as exc:
            results.append(_refused(readiness, provenance, str(exc)))
    return tuple(results)


def activity_request_for_engine(experiment, observation, engine: str) -> GeneratedInput | None:
    """One engine's melt-activity contract for this observation.

    The scorer calls this before it would fill a missing fO2. A gap here is
    a typed refusal. PO2_ENGINE_DEFAULT is not an oxygen input.
    """

    from simulator.battery.consumer_inputs import collect_consumer_inputs
    from simulator.battery.enums import BenchIdentityBasis
    from simulator.battery.records import Bench, BenchIdentity

    bench = Bench(
        getattr(experiment, "bench_id", None) or observation.observation_id or "melt-activity",
        getattr(experiment, "work_id", None) or observation.source_id or "melt-activity",
        BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
    )
    for item in melt_activity_requests(collect_consumer_inputs(experiment, bench, observation)):
        if item.readiness.engine == engine:
            return item
    return None


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
        if inputs.charges.dropped:
            # The print names species no charge route covers; scoring the
            # reduced set would silently vanish printed sample mass, exactly
            # the refusal engine_point enforces on the intensive side.
            raise UnsupportedValue(
                "charge_moles_by_species.dropped:" + ",".join(inputs.charges.dropped))
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
        "lab_geometry.surfaces.modelling_fields": {"waypoint": "surfaces", "authority": "assumed", "input": "operator.surfaces", "flag_id": "operator.surfaces"},
        "lab_schedule.melt_temperature_C": {"waypoint": "thermal_path", "authority": "derived", "formula": "(s / 3600, K - 273.15)"},
        "lab_schedule.duration_h": {"waypoint": "thermal_path", "authority": "derived", "formula": "final s / 3600"},
        "lab_schedule.chamber_pressure_mbar": {"waypoint": "run_pressure_boundary", "authority": "derived", "formula": "Pa / 100"},
        "lab_schedule.surface_temperature_C": {"waypoint": "surface_temperature_C"},
        "lab_schedule.gas_boundary": {"waypoint": "gas_boundary"},
        "lab_schedule.furnace_ceiling_C": {"waypoint": "furnace_ceiling_C", "authority": "assumed", "input": "operator.furnace_ceiling_C", "flag_id": "operator.furnace_ceiling_C"},
        "pair.faithful.feedstock_id": {"waypoint": "feedstock_id", "authority": "assumed", "input": "operator.feedstock_id", "flag_id": "operator.feedstock_id"},
    }
    if readiness.status is ReadinessStatus.NOT_APPLICABLE:
        return GeneratedInput(readiness, None, provenance)
    model = modelling_inputs or {}
    required_model = ("surfaces", "feedstock_id", "furnace_ceiling_C")
    missing_model = tuple("operator." + key for key in required_model if key not in model)
    if missing_model:
        for name in missing_model:
            readiness = replace(readiness, status=ReadinessStatus.GAP,
                gaps=(*readiness.gaps, ReadinessGap(name, GapReason.CONSUMER_INPUT_NOT_SUPPLIED, (name,))))
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
