"""Evidence-preserving bench waypoints and per-consumer readiness."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from collections.abc import Iterator, Mapping

from simulator.accounting.formulas import resolve_species_formula
from simulator.battery.enums import AmountBasis, MethodToken, ValueKind
from simulator.battery.records import (
    Bench,
    Experiment,
    Located,
    Locator,
    Observation,
    State,
    ThermalSchedule,
    Value,
    as_decimal,
)
from simulator.lab_schedule import LAB_SCHEDULE_PRESSURE_FLOOR_MBAR
from simulator.transport_constants import COLLISION_DIAMETERS_M, FREE_MOLECULAR_KNUDSEN_MIN


class WaypointAuthority(StrEnum):
    PRINTED = "printed"
    DERIVED = "derived"
    ASSUMED = "assumed"


class WaypointFlag(StrEnum):
    GEOMETRIC_ONLY = "geometric_only"
    ASSUMPTION = "assumption"


class GapReason(StrEnum):
    MISSING_EVIDENCE = "missing_evidence"
    CONSUMER_INPUT_NOT_SUPPLIED = "consumer_input_not_supplied"
    UNSUPPORTED_PRINT_FORM = "unsupported_print_form"
    # A printed interval is a real fact, but a point consumer cannot run on a
    # range: the typed refusal says "interval printed, engine needs a point"
    # instead of hiding the form behind the generic unsupported kind.
    INTERVAL_NEEDS_POINT = "interval_needs_point"
    BELOW_PRESSURE_FLOOR = "below_pressure_floor"
    OUTSIDE_PRESSURE_REGIME = "outside_pressure_regime"
    SINGLE_SPECIES_CHARGE = "single_species_charge"
    # Permanent typed absence: several apparatuses are cited and the no-select
    # ruling forbids picking one, so no acquisition can ever close this gap.
    UNATTRIBUTABLE_BY_CONSTRUCTION = "unattributable_by_construction"
    # The work is in the store but has no observation a point consumer can
    # score. Context rows are not observations, and absence is not a zero.
    NO_SCOREABLE_OBSERVATIONS = "no_scoreable_observations"


class ReadinessStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    GAP = "gap"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class Waypoint:
    name: str
    value: Value | Mapping[str, object] | tuple[object, ...]
    route: str
    authority: WaypointAuthority
    inputs: tuple[str, ...]
    flags: tuple[WaypointFlag, ...] = ()


@dataclass(frozen=True)
class WaypointAbsence:
    name: str
    reason: GapReason
    missing: tuple[str, ...]


@dataclass(frozen=True)
class WaypointResult:
    name: str
    selected: Waypoint | None
    routes: tuple[Waypoint, ...]
    absence: WaypointAbsence | None = None


@dataclass(frozen=True)
class SpeciesWaypoints(Mapping[str, WaypointResult]):
    by_species: Mapping[str, WaypointResult]
    absence: WaypointAbsence | None = None
    # Printed composition keys carrying a numeric wt% that no route could
    # charge (e.g. unknown molar mass). Consumers must refuse the reduced
    # charge set rather than score it as complete.
    dropped: tuple[str, ...] = ()

    def __getitem__(self, key: str) -> WaypointResult:
        return self.by_species[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.by_species)

    def __len__(self) -> int:
        return len(self.by_species)


@dataclass(frozen=True)
class ReadinessGap:
    waypoint: str
    reason: GapReason
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnudsenInput:
    value: Value
    locators: tuple[Locator, ...]


@dataclass(frozen=True)
class KnudsenConsistencyNotice:
    knudsen_number: Value
    threshold: Decimal
    inputs: Mapping[str, KnudsenInput]
    collision_species: str
    kind: str = "knudsen_regime_inconsistency"


@dataclass(frozen=True)
class ConsumerReadiness:
    consumer: str
    status: ReadinessStatus
    gaps: tuple[ReadinessGap, ...]
    engine: str | None = None
    notices: tuple[KnudsenConsistencyNotice, ...] = ()


_AUTHORITY_RANK = {
    WaypointAuthority.PRINTED: 3,
    WaypointAuthority.DERIVED: 2,
    WaypointAuthority.ASSUMED: 1,
}
_PI = Decimal("3.141592653589793238462643383279502884197")
_R = Decimal("8.31446261815324")
RPS_PRESSURE_FLOOR_PA = as_decimal(LAB_SCHEDULE_PRESSURE_FLOOR_MBAR) * Decimal("100")
ENGINE_POINT_CONSUMERS = (
    "internal-analytical",
    "alphamelts",
    "thermoengine",
    "vaporock",
    "magemin",
    "cached-real",
    "imcc_sf04",
    "imcc_sf04_ext",
)


def _result(
    name: str,
    routes: list[Waypoint],
    missing: tuple[str, ...] = (),
    *,
    present: Mapping[str, bool] | None = None,
) -> WaypointResult:
    # Callers pass routes best-first. The evidence ladder (was the source
    # printed or inferred, and how direct is the derivation) is known only
    # where the routes are built; selecting here by route-name prefix or a
    # second authority sort would discard that ladder.
    selected = routes[0] if routes else None
    # `present` is an OR-set evaluated for this context. A resolved member is
    # not missing. A bare tuple is already the evaluated absence (dynamic
    # refusal sites pass it through unchanged).
    if present is not None:
        evaluated = tuple(label for label, is_present in present.items() if not is_present)
        # A presence map can mark every member located while no route accepts
        # any of them. An empty missing list would claim nothing is absent;
        # name the whole set instead.
        if not evaluated and selected is None:
            evaluated = tuple(present)
        missing = evaluated
    absence = None if selected else WaypointAbsence(name, GapReason.MISSING_EVIDENCE, missing)
    return WaypointResult(name, selected, tuple(routes), absence)


def _located_present(located: Located | None) -> bool:
    return located is not None and located.state.is_value


def _value(located: Located[Value] | None) -> Value | None:
    if located is None or not located.state.is_value:
        return None
    value = located.state.value
    return value if isinstance(value, Value) else None


def _condition_value(experiment: Experiment, name: str) -> Value | None:
    located = experiment.conditions.get(name)
    if located is None or not located.state.is_value:
        return None
    raw = located.state.value
    return raw if isinstance(raw, Value) else Value.point_of(raw)


def _point_condition(
    experiment: Experiment, observation: Observation | None, key: str, name: str
) -> Waypoint | None:
    if observation is None:
        return None
    if observation.experiment_id != experiment.experiment_id:
        raise ValueError("Observation belongs to a different experiment")
    located = (observation.point_conditions or {}).get(key)
    if located is None or not located.state.is_value:
        return None
    raw = located.state.value
    if not isinstance(raw, (Value, Decimal, int, float, str)):
        return None
    try:
        value = raw if isinstance(raw, Value) else Value.point_of(raw)
    except (ValueError, TypeError, ArithmeticError):
        return None
    return Waypoint(
        name, value, f"observation_{key}", WaypointAuthority.DERIVED if located.inference else WaypointAuthority.PRINTED,
        (f"observation[{observation.observation_id}].point_conditions.{key}",),
    )


def _positive_range(value: Value) -> tuple[Decimal | None, Decimal | None]:
    if value.kind is ValueKind.POINT and value.point is not None and value.point >= 0:
        return value.point, value.point
    if value.kind is ValueKind.INTERVAL:
        if value.interval_low is not None and value.interval_low >= 0:
            return value.interval_low, value.interval_high
    if value.kind is ValueKind.BOUND and value.bound_value is not None:
        if value.bound_operator in {"<", "<=", "≤"}:
            return Decimal(0), value.bound_value
        if value.bound_operator in {">", ">=", "≥"}:
            return value.bound_value, None
    return None, None


def _range_value(low: Decimal | None, high: Decimal | None, approximate: bool = False) -> Value | None:
    if low is not None and high is not None:
        if low == high:
            return Value(ValueKind.POINT, point=low, approximate=approximate)
        return Value(ValueKind.INTERVAL, interval_low=low, interval_high=high, approximate=approximate)
    if low is not None:
        return Value(ValueKind.BOUND, bound_operator=">=", bound_value=low, approximate=approximate)
    if high is not None:
        return Value(ValueKind.BOUND, bound_operator="<=", bound_value=high, approximate=approximate)
    return None


def _multiply(*values: Value) -> Value | None:
    ranges = [_positive_range(item) for item in values]
    if any(low is None and high is None for low, high in ranges):
        return None
    low = Decimal(1)
    high: Decimal | None = Decimal(1)
    for item_low, item_high in ranges:
        if item_low is None:
            low = None  # type: ignore[assignment]
        elif low is not None:
            low *= item_low
        if item_high is None:
            high = None
        elif high is not None:
            high *= item_high
    return _range_value(low, high, any(item.approximate for item in values))


def _divide(numerator: Value, denominator: Value) -> Value | None:
    n_low, n_high = _positive_range(numerator)
    d_low, d_high = _positive_range(denominator)
    if d_low is not None and d_low <= 0:
        d_low = None
    low = None if n_low is None or d_high in (None, Decimal(0)) else n_low / d_high
    high = None if n_high is None or d_low in (None, Decimal(0)) else n_high / d_low
    return _range_value(low, high, numerator.approximate or denominator.approximate)


def _molar_mass_kg_mol(species: str) -> Decimal | None:
    try:
        return as_decimal(resolve_species_formula(species).molar_mass_kg_per_mol())
    except (KeyError, TypeError, ValueError):
        return None


def charge_moles_by_species(
    experiment: Experiment, bench: Bench, observation: Observation | None = None
) -> SpeciesWaypoints:
    if observation is not None:
        if observation.experiment_id != experiment.experiment_id:
            raise ValueError("Observation belongs to a different experiment")
        conditions = observation.point_conditions or {}
        fields = {field: conditions[key] for field, key in (
            ("mass_kg", "mass_kg"), ("initial_composition", "composition"),
            ("printed_composition", "printed_composition"),
        ) if key in conditions}
        if fields:
            scoped = replace(experiment, sample=replace(experiment.sample, **fields))
            result = charge_moles_by_species(scoped, bench)
            def scoped_waypoint(item: Waypoint) -> Waypoint:
                inputs = tuple(
                    path.replace("experiment.sample." + field,
                                 f"observation[{observation.observation_id}].point_conditions." +
                                 ("composition" if field == "initial_composition" else field))
                    if (field := path.removeprefix("experiment.sample.")) in fields else path
                    for path in item.inputs
                )
                return replace(item, route="observation_" + item.route, inputs=inputs)
            return SpeciesWaypoints({
                species: replace(value, selected=scoped_waypoint(value.selected) if value.selected else None,
                                 routes=tuple(scoped_waypoint(item) for item in value.routes))
                for species, value in result.items()
            }, result.absence, result.dropped)
    del bench
    routes: dict[str, list[Waypoint]] = {}
    # Evidence ladder per species, parallel to routes: an inferred source never
    # outranks a printed one, and a direct printed molar inventory outranks the
    # wt% and mole-fraction derivations (which need external molar masses).
    ranks: dict[str, list[tuple[bool, int]]] = {}

    def ranked(species: str, waypoint: Waypoint, inferred: bool, directness: int) -> None:
        routes.setdefault(species, []).append(waypoint)
        ranks.setdefault(species, []).append((not inferred, directness))

    dropped: list[str] = []
    mass = _value(experiment.sample.mass_kg)
    composition = experiment.sample.initial_composition
    if composition is not None and composition.state.is_value:
        comp = composition.state.value
        if comp.amount_basis is AmountBasis.MOL_INVENTORY:
            for species, moles in comp.components:
                ranked(
                    species,
                    Waypoint(
                        f"charge_moles_by_species.{species}",
                        Value.point_of(moles),
                        "printed_molar_inventory",
                        # An inferred inventory is a derivation, never a print.
                        WaypointAuthority.DERIVED if composition.inference else WaypointAuthority.PRINTED,
                        ("experiment.sample.initial_composition",),
                    ),
                    bool(composition.inference),
                    2,
                )
        elif mass is not None and comp.amount_basis is AmountBasis.MOLE_FRACTION:
            masses = [(species, _molar_mass_kg_mol(species), fraction) for species, fraction in comp.components]
            if all(item[1] is not None for item in masses):
                mean_molar_mass = sum(
                    fraction * molar_mass  # type: ignore[operator]
                    for _, molar_mass, fraction in masses
                )
                total_moles = _divide(mass, Value.point_of(mean_molar_mass))
                if total_moles is not None:
                    for species, _, fraction in masses:
                        value = _multiply(total_moles, Value.point_of(fraction))
                        if value is not None:
                            # Premise: x_i=n_i/n_tot and m=n_tot*Σ(x_i*M_i).
                            # Algebra: n_i=x_i*m/Σ(x_j*M_j). Units: kg/(kg/mol)=mol.
                            # Sanity: 100 mg Mg2SiO4 / 0.140693 kg/mol = 7.108e-4 mol.
                            ranked(
                                species,
                                Waypoint(
                                    f"charge_moles_by_species.{species}",
                                    value,
                                    "mass_times_mole_fraction",
                                    WaypointAuthority.DERIVED,
                                    ("experiment.sample.mass_kg", "experiment.sample.initial_composition"),
                                ),
                                bool(composition.inference),
                                0,
                            )
    printed = experiment.sample.printed_composition
    if mass is not None and printed is not None and printed.state.is_value:
        raw = printed.state.value
        if isinstance(raw, Mapping):
            numeric = {}
            for key, item in raw.items():
                if isinstance(item, Mapping):
                    continue
                try:
                    numeric[str(key)] = as_decimal(item)
                except (ValueError, TypeError, ArithmeticError):
                    # A printed composition may be a categorical Value payload
                    # (e.g. {kind: categorical, categorical: 'Fe-Mn or Fe-Sn'});
                    # non-numeric leaves carry no wt% and route nothing.
                    continue
            for species, wt_pct in numeric.items():
                molar_mass = _molar_mass_kg_mol(species)
                if molar_mass is None:
                    # The print names a species no route can charge; the
                    # reduced set must not be scored as the whole charge.
                    dropped.append(species)
                    continue
                species_mass = _multiply(
                    mass, Value.point_of(wt_pct / Decimal(100))
                )
                value = (
                    None
                    if species_mass is None
                    else _divide(species_mass, Value.point_of(molar_mass))
                )
                if value is not None:
                    # Premise: w_i is printed wt% and m_i=m*w_i/100.
                    # Algebra: n_i=m*w_i/(100*M_i). Units: kg/(kg/mol)=mol.
                    # Sanity: 100 mg pure Mg2SiO4 gives 7.108e-4 mol.
                    ranked(
                        species,
                        Waypoint(
                            f"charge_moles_by_species.{species}",
                            value,
                            "mass_times_printed_wt_percent",
                            WaypointAuthority.DERIVED,
                            ("experiment.sample.mass_kg", "experiment.sample.printed_composition"),
                        ),
                        bool(printed.inference),
                        1,
                    )
    results = {
        species: _result(
            f"charge_moles_by_species.{species}",
            [route for _, route in sorted(
                zip(ranks[species], candidates), key=lambda pair: pair[0], reverse=True)],
            ("experiment.sample.mass_kg", "experiment.sample.initial_composition"),
        )
        for species, candidates in routes.items()
    }
    absence = None
    if not results:
        charge_inputs = {
            "experiment.sample.mass_kg": _located_present(experiment.sample.mass_kg),
            "experiment.sample.initial_composition": _located_present(
                experiment.sample.initial_composition
            ),
        }
        absence = _result(
            "charge_moles_by_species",
            [],
            tuple(charge_inputs),
            present=charge_inputs,
        ).absence
    return SpeciesWaypoints(results, absence, tuple(sorted(set(dropped) - set(results))))


def normalized_composition(
    experiment: Experiment, bench: Bench, observation: Observation | None = None
) -> WaypointResult:
    """Intensive composition for fixed-T/P/fO2 points; never an absolute charge."""
    del bench
    if observation is not None and observation.experiment_id != experiment.experiment_id:
        raise ValueError("Observation belongs to a different experiment")
    point = (observation.point_conditions or {}) if observation else {}
    routes = []
    evidence_ranks = []
    absent = []
    missing = []
    unsupported = False
    printed_species: set[str] | None = None
    printed_path = None
    for field, key in (("printed_composition", "printed_composition"),
                       ("initial_composition", "composition")):
        located = point.get(key) if key in point else getattr(experiment.sample, field)
        path = (f"observation[{observation.observation_id}].point_conditions.{key}"
                if key in point else f"experiment.sample.{field}")
        if located is None or not located.state.is_value:
            absent.append(path)
            continue
        raw = located.state.value
        if field == "printed_composition" and isinstance(raw, Mapping):
            printed_species = {str(species) for species in raw}
            printed_path = path
        try:
            if field == "printed_composition":
                if not isinstance(raw, Mapping):
                    raise ValueError("unsupported composition")
                amounts = {}
                for species, weight in raw.items():
                    molar_mass = _molar_mass_kg_mol(species)
                    if molar_mass is None:
                        raise ValueError("unknown molar mass")
                    amounts[species] = as_decimal(weight) / molar_mass
                # Premise: printed w_i are wt%, M_i are species molar masses.
                # Algebra: n_i=m*w_i/(100*M_i); x_i=(w_i/M_i)/sum_j(w_j/M_j).
                # Units: w_i dimensionless, M_i kg/mol; (mol/kg)/(mol/kg)=1.
                # Unknown common mass m cancels; no run mass is inferred.
                # Sanity: pure Mg2SiO4 (M=0.140693 kg/mol) gives x=1;
                # weights proportional to molar masses give equal mole fractions.
            elif raw.amount_basis in (AmountBasis.MOL_INVENTORY, AmountBasis.MOLE_FRACTION):
                amounts = raw.as_map()
            else:
                raise ValueError("unsupported composition basis")
            if not amounts or any(not n.is_finite() or n < 0 for n in amounts.values()):
                raise ValueError("invalid composition")
            total = sum(amounts.values())
            if total <= 0:
                raise ValueError("empty charge")
            # n_i/sum(n_j) is dimensionless and invariant under n -> k*n, k>0.
            # A one-mole reference charge preserves ratios at specified T/P/fO2.
            routes.append(Waypoint("normalized_composition",
                {species: n / total for species, n in amounts.items()},
                ("observation_" if key in point else "") + "normalized_" + field,
                WaypointAuthority.DERIVED, (path,)))
            # Rank source evidence before normalization makes every output DERIVED.
            # Evidence directness: a printed x_i outranks a printed molar inventory,
            # which outranks a wt%-to-moles derivation (external molar-mass table).
            directness = 0
            if field == "initial_composition":
                directness = 2 if raw.amount_basis is AmountBasis.MOLE_FRACTION else 1
            evidence_ranks.append((not bool(located.inference), directness))
        except (ValueError, TypeError, ArithmeticError):
            unsupported = True
            missing.append(path)
    routes = [route for _, route in sorted(zip(evidence_ranks, routes),
                                          key=lambda pair: pair[0], reverse=True)]
    result = _result("normalized_composition", routes, tuple(absent))
    if result.selected is not None and printed_species is not None:
        dropped = tuple(sorted(
            printed_species - {str(species) for species in result.selected.value}))
        if dropped:
            # The print names species the selected route dropped; emitting the
            # reduced sibling would silently vanish printed sample mass.
            return WaypointResult(result.name, None, result.routes, WaypointAbsence(
                result.name, GapReason.UNSUPPORTED_PRINT_FORM,
                tuple(f"{printed_path}.{species}" for species in dropped)))
    if not routes and unsupported:
        return replace(result, absence=WaypointAbsence(
            result.name, GapReason.UNSUPPORTED_PRINT_FORM, tuple(missing)))
    return result


def _dimensions_present(dimensions: Mapping[str, Located[Value]] | None) -> bool:
    if not dimensions:
        return False
    return all(_value(dimensions.get(key)) is not None for key in ("length_m", "width_m", "height_m"))


def _rectangular_volume(dimensions: Mapping[str, Located[Value]] | None) -> Value | None:
    if not _dimensions_present(dimensions):
        return None
    keys = ("length_m", "width_m", "height_m")
    values = [_value(dimensions.get(key)) for key in keys]
    if any(item is None for item in values):
        return None
    # Premise: orthogonal rectangular internal dimensions. Algebra: V=L*W*H.
    # Units: m*m*m=m3. Sanity: 0.1*0.1*0.1 m = 1e-3 m3.
    return _multiply(*values)  # type: ignore[arg-type]


def relevant_volume(experiment: Experiment, bench: Bench) -> WaypointResult:
    routes: list[Waypoint] = []
    geometry = bench.geometry
    bench_method = (
        str(bench.method.state.value)
        if bench.method is not None and bench.method.state.is_value
        else None
    )
    knudsen = (
        experiment.method.is_value
        and experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    ) or bench_method == MethodToken.KNUDSEN_EFFUSION.value
    if geometry is not None:
        if knudsen:
            value = _rectangular_volume(geometry.cell_internal_dimensions)
            if value is not None:
                routes.append(Waypoint("relevant_volume", value, "cell_internal_dimensions", WaypointAuthority.DERIVED, ("bench.geometry.cell_internal_dimensions",)))
        else:
            printed = _value(geometry.chamber_volume_m3)
            if printed is not None:
                routes.append(Waypoint("relevant_volume", printed, "printed_chamber_volume", WaypointAuthority.PRINTED, ("bench.geometry.chamber_volume_m3",)))
            value = _rectangular_volume(geometry.chamber_dimensions)
            if value is not None:
                routes.append(Waypoint("relevant_volume", value, "chamber_internal_dimensions", WaypointAuthority.DERIVED, ("bench.geometry.chamber_dimensions",)))
    if knudsen:
        volume_inputs = {
            "bench.geometry.cell_internal_dimensions": geometry is not None
            and _dimensions_present(geometry.cell_internal_dimensions),
        }
    else:
        volume_inputs = {
            "bench.geometry.chamber_volume_m3": geometry is not None
            and _value(geometry.chamber_volume_m3) is not None,
            "bench.geometry.chamber_dimensions": geometry is not None
            and _dimensions_present(geometry.chamber_dimensions),
        }
    return _result("relevant_volume", routes, tuple(volume_inputs), present=volume_inputs)


def pressure_boundary(
    experiment: Experiment, bench: Bench, observation: Observation | None = None
) -> WaypointResult:
    routes: list[Waypoint] = []
    point = _point_condition(experiment, observation, "total_pressure_Pa", "pressure_boundary")
    if point is not None:
        routes.append(point)
    printed = _value(experiment.pressure_environment.total_pressure_Pa)
    if printed is not None:
        run_pressure = experiment.pressure_environment.total_pressure_Pa
        routes.append(Waypoint("pressure_boundary", printed, "printed_run_pressure",
            WaypointAuthority.DERIVED if run_pressure.inference else WaypointAuthority.PRINTED,
            ("experiment.pressure_environment.total_pressure_Pa",)))
    speed = _value(bench.pumping_speed_m3_s)
    gas_load = next((fact for fact in bench.other_facts if fact.name == "gas_load_Pa_m3_s"), None)
    volume = relevant_volume(experiment, bench).selected
    if speed is not None and gas_load is not None and volume is not None:
        load_value = _value(gas_load.value)
        if load_value is not None:
            derived = _divide(load_value, speed)
            if derived is not None:
                # Premise: well-mixed vessel d(PV)/dt=Q-S*P at steady state.
                # Algebra: 0=Q-S*P, so P=Q/S (V cancels). Units: Pa*m3/s /(m3/s)=Pa.
                # Sanity: Q=1e-5 Pa*m3/s and S=1e-2 m3/s gives 1e-3 Pa.
                routes.append(Waypoint("pressure_boundary", derived, "steady_gas_load_over_pump_speed", WaypointAuthority.DERIVED, ("bench.other_facts.gas_load_Pa_m3_s", "bench.pumping_speed_m3_s", "relevant_volume")))
    pressure_inputs = {
        "experiment.pressure_environment.total_pressure_Pa": _value(
            experiment.pressure_environment.total_pressure_Pa
        ) is not None,
        "bench.other_facts.gas_load_Pa_m3_s": gas_load is not None and _value(gas_load.value) is not None,
        "bench.pumping_speed_m3_s": speed is not None,
    }
    return _result("pressure_boundary", routes, tuple(pressure_inputs), present=pressure_inputs)


def effective_escape_area(experiment: Experiment, bench: Bench) -> WaypointResult:
    del experiment
    routes: list[Waypoint] = []
    geometry = bench.geometry
    if geometry is None:
        return _result(
            "effective_escape_area",
            routes,
            ("bench.geometry",),
            present={"bench.geometry": False},
        )
    area = _value(geometry.orifice_area_m2)
    diameter = _value(geometry.orifice_diameter_m)
    clausing = _value(geometry.clausing_factor)
    count = _value(geometry.orifice_count)
    if area is not None:
        routes.append(Waypoint("effective_escape_area", area, "printed_area", WaypointAuthority.PRINTED, ("bench.geometry.orifice_area_m2",), (WaypointFlag.GEOMETRIC_ONLY,)))
        if clausing is not None:
            # Premise: printed opening area A is geometric; C is transmission probability.
            # Algebra: A_eff=A*C. Units: m2*1=m2. Sanity: 1e-6*.51423=5.1423e-7 m2.
            derived = _multiply(area, clausing)
            if derived is not None:
                routes.append(Waypoint("effective_escape_area", derived, "printed_area_times_clausing", WaypointAuthority.DERIVED, ("bench.geometry.orifice_area_m2", "bench.geometry.clausing_factor")))
    if diameter is not None:
        # Premise: circular opening radius=d/2. Algebra: A=pi*(d/2)^2=pi*d^2/4.
        # Units: m2. Sanity: d=1 mm gives 7.854e-7 m2; short tube L/r=2 needs a separate Clausing factor (~0.51).
        geometric = _multiply(diameter, diameter, Value.point_of(_PI / Decimal(4)))
        if geometric is not None:
            inputs = ["bench.geometry.orifice_diameter_m"]
            route = "diameter_geometric_area_single_opening"
            if count is not None:
                total = _multiply(geometric, count)
                if total is not None:
                    geometric = total
                    inputs.append("bench.geometry.orifice_count")
                    route = "diameter_geometric_area_total"
            geometric_route = Waypoint(
                "effective_escape_area",
                geometric,
                route,
                WaypointAuthority.DERIVED,
                tuple(inputs),
                (WaypointFlag.GEOMETRIC_ONLY,),
            )
            if clausing is not None:
                effective = _multiply(geometric, clausing)
                if effective is not None:
                    routes.append(Waypoint("effective_escape_area", effective, "diameter_times_clausing", WaypointAuthority.DERIVED, tuple(inputs + ["bench.geometry.clausing_factor"])))
            routes.append(geometric_route)
    escape_inputs = {
        "bench.geometry.orifice_area_m2": area is not None,
        "bench.geometry.orifice_diameter_m": diameter is not None,
    }
    result = _result(
        "effective_escape_area", routes, tuple(escape_inputs), present=escape_inputs
    )
    effective = [route for route in routes if WaypointFlag.GEOMETRIC_ONLY not in route.flags]
    if effective:
        return WaypointResult(result.name, _result(result.name, effective, ()).selected, result.routes)
    return result


def thermal_path(
    experiment: Experiment, bench: Bench, observation: Observation | None = None
) -> WaypointResult:
    del bench
    routes: list[Waypoint] = []
    point = _point_condition(experiment, observation, "temperature_K", "thermal_path")
    if point is not None:
        routes.append(point)
    schedule: ThermalSchedule | None = experiment.thermal_schedule
    ramp_series: tuple[tuple[Decimal, Decimal], ...] | None = None
    hold_series: tuple[tuple[Decimal, Decimal], ...] | None = None
    if schedule is not None and schedule.points:
        pairs = []
        for point in schedule.points:
            time = _value(point.time_s)
            temperature = _value(point.temperature_K)
            if time is None or temperature is None or time.kind is not ValueKind.POINT or temperature.kind is not ValueKind.POINT:
                pairs = []
                break
            pairs.append((time.point, temperature.point))
        if pairs:
            routes.append(Waypoint("thermal_path", Value(ValueKind.SERIES, series=tuple(pairs)), "printed_time_temperature_points", WaypointAuthority.PRINTED, ("experiment.thermal_schedule.points",)))
        else:
            for point in schedule.points:
                for candidate in (_value(point.time_s), _value(point.temperature_K)):
                    if candidate is not None and candidate.kind in {
                        ValueKind.BOUND,
                        ValueKind.INTERVAL,
                    }:
                        routes.append(
                            Waypoint(
                                "thermal_path",
                                candidate,
                                "printed_time_temperature_nonpoint",
                                WaypointAuthority.PRINTED,
                                ("experiment.thermal_schedule.points",),
                            )
                        )
                        break
    if schedule is not None and schedule.ramps:
        time = Decimal(0)
        pairs = []
        nonpoint: Value | None = None
        for ramp in schedule.ramps:
            rate = _value(ramp.rate_K_s)
            start = _value(ramp.start_temperature_K)
            end = _value(ramp.end_temperature_K)
            candidates = (rate, start, end)
            if any(item is None for item in candidates):
                pairs = []
                break
            nonpoint = next(
                (
                    item
                    for item in candidates
                    if item is not None
                    and item.kind in {ValueKind.BOUND, ValueKind.INTERVAL}
                ),
                None,
            )
            if nonpoint is not None:
                pairs = []
                break
            assert rate is not None and start is not None and end is not None
            if rate.point is None or rate.point <= 0:
                pairs = []
                break
            # Premise: a printed linear ramp has constant |dT/dt|=r.
            # Algebra: dt=|T1-T0|/r. Units: K/(K/s)=s.
            # Sanity: 300→1500 K at 2 K/s takes 600 s.
            duration = abs(end.point - start.point) / rate.point
            pairs.extend(((time, start.point), (time + duration, end.point)))
            time += duration
        if pairs:
            ramp_series = tuple(pairs)
            routes.append(
                Waypoint(
                    "thermal_path",
                    Value(ValueKind.SERIES, series=ramp_series),
                    "printed_ramps",
                    WaypointAuthority.DERIVED,
                    ("experiment.thermal_schedule.ramps",),
                )
            )
        elif nonpoint is not None:
            routes.append(
                Waypoint(
                    "thermal_path",
                    nonpoint,
                    "printed_ramp_nonpoint",
                    WaypointAuthority.PRINTED,
                    ("experiment.thermal_schedule.ramps",),
                )
            )
    if schedule is not None and schedule.setpoints_and_holds:
        time = Decimal(0)
        pairs = []
        for setpoint in schedule.setpoints_and_holds:
            temperature = _value(setpoint.temperature_K)
            hold = _value(setpoint.hold_duration_s)
            if temperature is None or temperature.kind is not ValueKind.POINT or hold is None or hold.kind is not ValueKind.POINT:
                pairs = []
                break
            pairs.extend(((time, temperature.point), (time + hold.point, temperature.point)))
            time += hold.point
        if pairs:
            # Premise: each printed setpoint is held isothermally for its duration.
            # Algebra: t_end=t_start+dt, T(t)=T_set. Units: s+s=s and K.
            # Sanity: 1500 K held 600 s yields points (0,1500),(600,1500).
            hold_series = tuple(pairs)
            routes.append(Waypoint("thermal_path", Value(ValueKind.SERIES, series=hold_series), "setpoints_and_holds", WaypointAuthority.DERIVED, ("experiment.thermal_schedule.setpoints_and_holds",)))
        else:
            for setpoint in schedule.setpoints_and_holds:
                for candidate in (
                    _value(setpoint.temperature_K),
                    _value(setpoint.hold_duration_s),
                ):
                    if candidate is not None and candidate.kind in {
                        ValueKind.BOUND,
                        ValueKind.INTERVAL,
                    }:
                        routes.append(
                            Waypoint(
                                "thermal_path",
                                candidate,
                                "temperature_range_and_duration",
                                WaypointAuthority.DERIVED,
                                ("experiment.thermal_schedule.setpoints_and_holds",),
                            )
                        )
                        break
    if schedule is not None and schedule.ramps and schedule.setpoints_and_holds and not any(route.route == "printed_time_temperature_points" for route in routes):
        if ramp_series is None or hold_series is None:
            return WaypointResult(
                "thermal_path", None, tuple(routes),
                WaypointAbsence("thermal_path", GapReason.UNSUPPORTED_PRINT_FORM,
                                ("experiment.thermal_schedule.ramps", "experiment.thermal_schedule.setpoints_and_holds")),
            )
        if len(schedule.ramps) != 1 or len(schedule.setpoints_and_holds) != 1 or ramp_series[-1][1] != hold_series[0][1]:
            return WaypointResult(
                "thermal_path", None, tuple(routes),
                WaypointAbsence("thermal_path", GapReason.UNSUPPORTED_PRINT_FORM,
                                ("experiment.thermal_schedule.segment_order",)),
            )
    if ramp_series is not None and hold_series is not None:
        offset = ramp_series[-1][0]
        shifted_holds = tuple(
            (offset + time, temperature) for time, temperature in hold_series
        )
        if ramp_series[-1][1] == shifted_holds[0][1]:
            shifted_holds = shifted_holds[1:]
        # Premise: one ramp ends at the sole hold temperature; multiple arrays do not establish order.
        # Algebra: t_hold,absolute=t_ramp,end+t_hold,relative. Units: s+s=s.
        # Sanity: 300→1500 K at 2 K/s plus a 600 s hold ends at 1200 s.
        # The composed schedule ranks behind observation-scoped points and any
        # printed route, ahead of the single-block derivations it subsumes.
        insert_at = next(
            (
                index
                for index, route in enumerate(routes)
                if not route.route.startswith("observation_")
                and route.authority is not WaypointAuthority.PRINTED
            ),
            len(routes),
        )
        routes.insert(
            insert_at,
            Waypoint(
                "thermal_path",
                Value(ValueKind.SERIES, series=ramp_series + shifted_holds),
                "printed_ramps_and_holds",
                WaypointAuthority.DERIVED,
                (
                    "experiment.thermal_schedule.ramps",
                    "experiment.thermal_schedule.setpoints_and_holds",
                ),
            ),
        )
    temperature = experiment.conditions.get("temperature_K")
    raw_temperature = temperature.state.value if temperature is not None and temperature.state.is_value else None
    if raw_temperature is not None and not routes:
        temp_value = raw_temperature if isinstance(raw_temperature, Value) else Value.point_of(raw_temperature)
        if temp_value.kind is ValueKind.POINT:
            routes.append(Waypoint("thermal_path", Value(ValueKind.SERIES, series=((Decimal(0), temp_value.point),)), "temperature_points_only", WaypointAuthority.PRINTED, ("experiment.conditions.temperature_K",)))
    # Used as gap text only when no route was accepted. On that path a located
    # schedule fragment or non-point conditions temperature was not usable, so
    # name both OR members instead of a weaker presence check.
    return _result(
        "thermal_path",
        routes,
        ("experiment.thermal_schedule", "experiment.conditions.temperature_K"),
    )


def oxygen_condition(
    experiment: Experiment, bench: Bench, observation: Observation | None = None
) -> WaypointResult:
    """log10(fO2), never an uncontrolled default. A landed printed log is
    kept as the author stated it, never converted between pressure frames:
    an atm-referenced print stays the author's number (~0.0057 dex from the
    bar frame), and the frame offset is recorded in the locator note only
    when a companion atm pressure lands. Derived routes are
    log10(pO2 Pa / 1 bar)."""
    routes: list[Waypoint] = []
    printed = _point_condition(experiment, observation, "fO2_log", "oxygen_condition")
    if printed is not None:
        routes.append(printed)
    point_pressure = _point_condition(experiment, observation, "fO2_Pa", "oxygen_condition")
    if point_pressure is not None:
        value = _log_pressure(point_pressure.value)
        if value is not None:
            routes.append(Waypoint("oxygen_condition", value, "observation_fO2_Pa_to_log_fO2",
                WaypointAuthority.DERIVED, point_pressure.inputs))
    control = experiment.fO2_control
    if control is not None:
        pressure = _value(control.oxygen_partial_pressure_Pa)
        if pressure is not None:
            value = _log_pressure(pressure)
            if value is not None:
                routes.append(Waypoint("oxygen_condition", value, "oxygen_partial_pressure_to_log_fO2",
                    WaypointAuthority.DERIVED, ("experiment.fO2_control.oxygen_partial_pressure_Pa",)))
    sweep = experiment.pressure_environment.sweep_gas
    if (sweep.state.is_value and sweep.state.value.alternatives is None
            and sweep.state.value.species == "O2" and sweep.state.value.partial_pressure_Pa.is_value):
        value = _log_pressure(Value.point_of(sweep.state.value.partial_pressure_Pa.value))
        if value is not None:
            routes.append(Waypoint("oxygen_condition", value, "oxygen_sweep_partial_pressure",
                WaypointAuthority.DERIVED, ("experiment.pressure_environment.sweep_gas",)))
    if (sweep.state.is_value and sweep.state.value.alternatives is None
            and sweep.state.value.components is not None):
        # A mixture's printed O2 component partial pressure is the same evidence as a
        # single-species O2 record. Typed-absence component pressures stay refusals —
        # never inferred from mole fraction. alternatives are ambiguous by construction
        # (the paper printed "A or B"), so they are never selected from.
        printed_o2 = [c for c in sweep.state.value.components
                      if c.species == "O2" and c.partial_pressure_Pa.is_value]
        if len(printed_o2) == 1:
            value = _log_pressure(Value.point_of(printed_o2[0].partial_pressure_Pa.value))
            if value is not None:
                routes.append(Waypoint("oxygen_condition", value, "oxygen_sweep_component_partial_pressure",
                    WaypointAuthority.DERIVED, ("experiment.pressure_environment.sweep_gas",)))
    gas = (observation.point_conditions or {}).get("gas_composition") if observation else None
    if gas is not None and gas.state.is_value:
        from simulator.battery.records import Composition

        comp = gas.state.value
        if isinstance(comp, Composition) and comp.amount_basis is AmountBasis.MOLE_FRACTION:
            fractions = dict(comp.components)
            pressure = pressure_boundary(experiment, bench, observation).selected
            if "O2" in fractions and pressure is not None:
                # Ideal mixture: pO2=xO2*P. Units: dimensionless*Pa=Pa.
                # At xO2=.2 and P=1e5 Pa, log10(fO2/bar)=log10(.2)=-.69897.
                partial = _multiply(pressure.value, Value.point_of(fractions["O2"]))
                value = _log_pressure(partial) if partial is not None else None
                if value is not None:
                    routes.append(Waypoint("oxygen_condition", value, "observation_gas_composition",
                        WaypointAuthority.DERIVED,
                        (f"observation[{observation.observation_id}].point_conditions.gas_composition", *pressure.inputs)))
            elif any(set(fractions) <= set(pair) | {"Ar", "He", "N2"} and set(pair) <= set(fractions)
                     for pair in (("H2", "H2O"), ("CO", "CO2"))):
                from simulator.chemistry.offgas_fo2 import imposed_fo2, OffgasFO2Unavailable

                thermal = thermal_path(experiment, bench, observation).selected
                if thermal is not None and thermal.value.kind is ValueKind.POINT:
                    try:
                        # Premise: one equilibrated H2/H2O or CO/CO2 gas couple;
                        # NASA CEA/JANAF K(T), as documented in offgas_fo2.
                        # Algebra: log fO2=2*(log(p_oxidized/p_reduced)-log K).
                        # Units: pressure ratio and K dimensionless (1 bar reference).
                        # Sanity: ratio=K gives log fO2=0 at any admitted T.
                        computed = imposed_fo2({key: float(value) for key, value in fractions.items()}, float(thermal.value.point))
                        if computed.log10_fO2 is not None:
                            routes.append(Waypoint("oxygen_condition", Value.point_of(computed.log10_fO2),
                                "observation_gas_couple_equilibrium", WaypointAuthority.DERIVED,
                                (f"observation[{observation.observation_id}].point_conditions.gas_composition", *thermal.inputs)))
                    except OffgasFO2Unavailable:
                        pass
    # The buffer relation is a heavier derivation (published table + thermal +
    # pressure waypoints) than any printed pO2 above, so it ranks last.
    if control is not None and control.buffer is not None and control.buffer.state.is_value:
        from benchmarks.buffer_reproduction import PUBLISHED_BUFFERS

        buffer = PUBLISHED_BUFFERS.get(str(control.buffer.state.value).upper())
        thermal = thermal_path(experiment, bench, observation).selected
        pressure = pressure_boundary(experiment, bench, observation).selected
        if buffer is not None and thermal is not None and pressure is not None:
            temperature = thermal.value
            if temperature.kind is ValueKind.SERIES and len({t for _, t in temperature.series}) == 1:
                temperature = Value.point_of(temperature.series[0][1])
            if (temperature.kind is ValueKind.POINT and pressure.value.kind is ValueKind.POINT
                    and buffer.T_min_K <= temperature.point <= buffer.T_max_K):
                T = temperature.point
                P = pressure.value.point / Decimal(100000)
                # Premise: Frost (1991), doi:10.2138/rmg.1991.25.1, Table 1,
                # condensed buffer equilibrium inside its published T domain.
                # Algebra: log10(fO2/bar)=A/T+B+C*(P_bar-1)/T.
                # Units: K/K and (K/bar)*bar/K are dimensionless.
                # Sanity: IW at 1000 K, 1 bar gives -20.787.
                value = as_decimal(buffer.A_K) / T + as_decimal(buffer.B) + as_decimal(buffer.pressure_coefficient_K_per_bar) * (P - 1) / T
                routes.append(Waypoint("oxygen_condition", Value.point_of(value),
                    "buffer_relation", WaypointAuthority.DERIVED,
                    ("experiment.fO2_control.buffer", *thermal.inputs, *pressure.inputs)))
    if routes:
        # A fired route is the decision. The OR-set is consulted only for the gap text.
        return _result(
            "oxygen_condition",
            routes,
            ("fO2_log", "experiment.fO2_control", "temperature_K", "total_pressure_Pa"),
        )
    # No route fired, so observation fO2_log already failed _point_condition.
    # experiment.conditions.fO2_log is not an oxygen input. A located pO2 that
    # _log_pressure accepts would already have fired oxygen_partial_pressure_to_log_fO2,
    # so control is present here only when the buffer name is a published buffer.
    buffer_known = False
    if control is not None and control.buffer is not None and control.buffer.state.is_value:
        from benchmarks.buffer_reproduction import PUBLISHED_BUFFERS

        buffer_known = str(control.buffer.state.value).upper() in PUBLISHED_BUFFERS
    control_present = buffer_known
    oxygen_inputs = {
        "fO2_log": False,
        "experiment.fO2_control": control_present,
        "temperature_K": thermal_path(experiment, bench, observation).selected is not None,
        "total_pressure_Pa": pressure_boundary(experiment, bench, observation).selected is not None,
    }
    return _result(
        "oxygen_condition",
        routes,
        ("fO2_log", "experiment.fO2_control", "temperature_K", "total_pressure_Pa"),
        present=oxygen_inputs,
    )


def _log_pressure(pressure: Value) -> Value | None:
    # Premise: ideal O2 gas, fO2/bar = pO2_Pa / 100000 Pa/bar.
    # Algebra: log10(fO2/bar)=log10(pO2_Pa/100000); argument dimensionless.
    # Sanity: pO2=1 Pa gives -5. Monotonic transform preserves bounds.
    def convert(value: Decimal) -> Decimal:
        return (value / Decimal(100000)).log10()
    if pressure.kind is ValueKind.POINT and pressure.point > 0:
        return replace(pressure, point=convert(pressure.point))
    if pressure.kind is ValueKind.INTERVAL and pressure.interval_low > 0:
        return replace(pressure, interval_low=convert(pressure.interval_low), interval_high=convert(pressure.interval_high))
    if pressure.kind is ValueKind.BOUND and pressure.bound_value > 0:
        return replace(pressure, bound_value=convert(pressure.bound_value))
    return None


def g1(
    experiment: Experiment,
    bench: Bench,
    p_sat: Value | Mapping[str, Value],
) -> SpeciesWaypoints:
    charges = charge_moles_by_species(experiment, bench)
    volume = relevant_volume(experiment, bench).selected
    thermal = thermal_path(experiment, bench).selected
    results: dict[str, WaypointResult] = {}
    if volume is not None and thermal is not None and thermal.value.kind is ValueKind.SERIES:
        temperature = Value.point_of(thermal.value.series[-1][1])
        for species, charge_result in charges.items():
            if charge_result.selected is None:
                continue
            species_p_sat = (
                p_sat.get(species) if isinstance(p_sat, Mapping) else p_sat
            )
            if species_p_sat is None or (
                not isinstance(p_sat, Mapping) and len(charges) != 1
            ):
                results[species] = WaypointResult(
                    f"G1.{species}",
                    None,
                    (),
                    WaypointAbsence(
                        f"G1.{species}",
                        GapReason.MISSING_EVIDENCE,
                        (f"p_sat.{species}",),
                    ),
                )
                continue
            numerator = _multiply(
                charge_result.selected.value, Value.point_of(_R), temperature
            )
            denominator = _multiply(species_p_sat, volume.value)
            value = None if numerator is None or denominator is None else _divide(numerator, denominator)
            if value is not None:
                # Premise: ideal-gas saturation capacity n_sat=P_sat*V/(R*T).
                # Algebra: G1=n/n_sat=n*R*T/(P_sat*V). Units cancel to 1.
                # Sanity: 1e-4 mol, 1500 K, 100 Pa, 1e-3 m3 gives G1=12.47.
                waypoint = Waypoint(
                    f"G1.{species}",
                    value,
                    "ideal_gas_saturation_capacity",
                    WaypointAuthority.DERIVED,
                    (
                        charge_result.selected.name,
                        f"p_sat.{species}",
                        "relevant_volume",
                        "thermal_path",
                    ),
                )
                results[species] = WaypointResult(
                    f"G1.{species}", waypoint, (waypoint,)
                )
    absence = None
    if not results:
        g1_inputs = {
            "charge_moles_by_species": any(item.selected is not None for item in charges.values()),
            "p_sat": p_sat is not None and not (isinstance(p_sat, Mapping) and len(p_sat) == 0),
            "relevant_volume": volume is not None,
            "thermal_path": thermal is not None,
        }
        absence = _result("G1", [], tuple(g1_inputs), present=g1_inputs).absence
    return SpeciesWaypoints(results, absence)


def g2(experiment: Experiment, bench: Bench) -> WaypointResult:
    escape = effective_escape_area(experiment, bench).selected
    surface = _value(experiment.sample.surface_area_m2)
    alpha = _condition_value(experiment, "evaporation_alpha")
    alpha_flags: tuple[WaypointFlag, ...] = ()
    alpha_input = "experiment.conditions.evaporation_alpha"
    if alpha is None:
        alpha = Value.point_of(1)
        alpha_flags = (WaypointFlag.ASSUMPTION,)
        alpha_input = "alpha_assumed_unity"
    routes: list[Waypoint] = []
    if escape is not None and surface is not None:
        denominator = _multiply(surface, alpha)
        value = None if denominator is None else _divide(escape.value, denominator)
        if value is not None:
            # Premise: molecular escape competes with alpha-scaled surface supply.
            # Algebra: G2=S_eff/(A_evap*alpha). Units: m2/(m2*1)=1.
            # Sanity: 1e-6/(1e-4*0.1)=0.1. Missing alpha uses a visibly
            # flagged unity ceiling so G2 remains comparable without certifying it.
            routes.append(
                Waypoint(
                    "G2",
                    value,
                    "escape_over_evaporating_area_and_alpha",
                    WaypointAuthority.DERIVED,
                    (
                        "effective_escape_area",
                        "experiment.sample.surface_area_m2",
                        alpha_input,
                    ),
                    tuple(dict.fromkeys(escape.flags + alpha_flags)),
                )
            )
    g2_inputs = {
        "effective_escape_area": escape is not None,
        "experiment.sample.surface_area_m2": surface is not None,
        "experiment.conditions.evaporation_alpha": _condition_value(
            experiment, "evaporation_alpha"
        ) is not None,
    }
    return _result("G2", routes, tuple(g2_inputs), present=g2_inputs)


def g3(experiment: Experiment, bench: Bench) -> WaypointResult:
    volume = relevant_volume(experiment, bench).selected
    escape = effective_escape_area(experiment, bench).selected
    routes: list[Waypoint] = []
    if volume is not None and escape is not None:
        value = _divide(volume.value, escape.value)
        if value is not None:
            # Premise: vessel pressure response geometry scales with volume per escape area.
            # Algebra: G3=V/S_eff. Units: m3/m2=m. Sanity: 1e-3/1e-6=1000 m.
            routes.append(Waypoint("G3", value, "volume_over_escape_area", WaypointAuthority.DERIVED, ("relevant_volume", "effective_escape_area"), escape.flags))
    g3_inputs = {
        "relevant_volume": volume is not None,
        "effective_escape_area": escape is not None,
    }
    return _result("G3", routes, tuple(g3_inputs), present=g3_inputs)


def _gap(result: WaypointResult) -> ReadinessGap | None:
    if result.selected is not None:
        return None
    assert result.absence is not None
    return ReadinessGap(result.name, result.absence.reason, result.absence.missing)


def _pressure_meets_floor(value: Value) -> bool:
    low, _ = _positive_range(value)
    return low is not None and low >= RPS_PRESSURE_FLOOR_PA


def _thermal_gap(result: WaypointResult) -> ReadinessGap | None:
    missing = _gap(result)
    if missing is not None:
        return missing
    assert result.selected is not None
    if result.selected.value.kind is not ValueKind.SERIES:
        return ReadinessGap(
            "thermal_path",
            GapReason.UNSUPPORTED_PRINT_FORM,
            result.selected.inputs,
        )
    return None


def _effective_escape_gap(result: WaypointResult) -> ReadinessGap | None:
    missing = _gap(result)
    if missing is not None:
        return missing
    assert result.selected is not None
    if WaypointFlag.GEOMETRIC_ONLY in result.selected.flags:
        return ReadinessGap(
            "effective_escape_area",
            GapReason.MISSING_EVIDENCE,
            ("bench.geometry.clausing_factor", "bench.geometry.orifice_area_m2"),
        )
    return None


def _orifice_knudsen(experiment, bench, thermal, pressure):
    inputs = {}
    missing = []
    diameter = _value(bench.geometry.orifice_diameter_m) if bench.geometry else None
    if diameter is None:
        missing.append("bench.geometry.orifice_diameter_m")
    else:
        inputs["d_orifice"] = bench.geometry.orifice_diameter_m
    if thermal.selected is None or thermal.selected.value.kind is not ValueKind.SERIES:
        missing.append("thermal_path")
    else:
        temperatures = [temperature for _, temperature in thermal.selected.value.series]
        value = _range_value(min(temperatures), max(temperatures))
        schedule = experiment.thermal_schedule
        evidence = []
        if schedule:
            evidence = [p.temperature_K for p in (schedule.points or ())] + [p.temperature_K for p in (schedule.setpoints_and_holds or ())]
            evidence += [p.start_temperature_K for p in (schedule.ramps or ())] + [p.end_temperature_K for p in (schedule.ramps or ())]
        if not evidence:
            evidence = [experiment.conditions.get("temperature_K")]
        inputs["T"] = Located(State.of(value))
    if pressure.selected is None:
        missing.append("pressure_boundary")
    else:
        inputs["P"] = Located(State.of(pressure.selected.value), locator=experiment.pressure_environment.total_pressure_Pa.locator)
    gas = experiment.pressure_environment.sweep_gas
    species = gas.state.value.species if gas.state.is_value else None
    sigma = COLLISION_DIAMETERS_M.get(species)
    if sigma is None:
        missing.append("pressure_environment.sweep_gas.species.collision_diameter")
    if missing:
        return None, inputs, species, ReadinessGap("knudsen_number_orifice", GapReason.MISSING_EVIDENCE, tuple(missing))
    # Premise: hard-sphere mean free path in gas of collision diameter sigma.
    # Algebra: Kn=lambda/d=k_B*T/(sqrt(2)*pi*sigma^2*P*d).
    # Units: J/K*K/(m2*Pa*m)=1. At 1500 K, sigma=3e-10 m:
    # lambda=0.0518/P m; d=0.5 mm needs P<=10.4 Pa for Kn>=10.
    cross_section = Decimal(2).sqrt() * _PI * as_decimal(sigma) ** 2
    numerator = _multiply(inputs["T"].state.value, Value.point_of("1.380649e-23"))
    denominator = _multiply(inputs["P"].state.value, diameter, Value.point_of(cross_section))
    kn = _divide(numerator, denominator) if numerator is not None and denominator is not None else None
    gap = None if kn is not None else ReadinessGap("knudsen_number_orifice", GapReason.UNSUPPORTED_PRINT_FORM,
                                                   ("d_orifice", "T", "P"))
    located_inputs = {key: (value,) for key, value in inputs.items()}
    located_inputs["T"] = tuple(item for item in evidence if item is not None)
    if pressure.selected.route != "printed_run_pressure":
        located_inputs["P"] = tuple(
            item for item in [bench.pumping_speed_m3_s] + [fact.value for fact in bench.other_facts if fact.name == "gas_load_Pa_m3_s"]
            if item is not None
        )
    notice_inputs = {
        key: KnudsenInput(value.state.value, tuple(dict.fromkeys(
            item.locator for item in located_inputs[key] if item.locator is not None
        ))) for key, value in inputs.items()
    }
    return kn, notice_inputs, species, gap


def _consumer_constraints(
    experiment: Experiment, bench: Bench, observation: Observation | None = None
) -> tuple[ConsumerReadiness, ...]:
    thermal = thermal_path(experiment, bench)
    pressure = pressure_boundary(experiment, bench)
    escape = effective_escape_area(experiment, bench)

    def build(
        consumer: str,
        gaps: list[ReadinessGap],
        status: ReadinessStatus | None = None,
        engine: str | None = None,
    ) -> ConsumerReadiness:
        return ConsumerReadiness(
            consumer,
            status or (ReadinessStatus.GAP if gaps else ReadinessStatus.READY),
            tuple(gaps),
            engine,
        )

    thermal_gap = _thermal_gap(thermal)
    kems_gaps = [gap for gap in (_effective_escape_gap(escape),) if gap and escape.selected]
    kems_status = None
    kems_notices = ()
    method = experiment.method.value.value if experiment.method.is_value else None
    if method is None and bench.method is not None and bench.method.state.is_value:
        method = str(bench.method.state.value)
    kn, kn_inputs, species, kn_gap = _orifice_knudsen(experiment, bench, thermal, pressure)
    threshold = as_decimal(FREE_MOLECULAR_KNUDSEN_MIN)
    low, high = _positive_range(kn) if kn is not None else (None, None)
    if method == MethodToken.KNUDSEN_EFFUSION.value:
        if high is not None:
            minimum_T, maximum_T = _positive_range(kn_inputs["T"].value)
            # Premise: all temperatures in the point schedule occur, and Kn is linear in T.
            # Algebra: max(Kn at Tmin)=max(Kn)*Tmin/Tmax. Units: 1*K/K=1.
            # Sanity: Kn=[2.97,14.86] over 300..1500 K fails at 300 K.
            high_at_minimum_T = high * minimum_T / maximum_T if maximum_T else high
            if high_at_minimum_T < threshold:
                kems_notices = (KnudsenConsistencyNotice(kn, threshold, kn_inputs, species),)
    elif method in {item.value for item in MethodToken}:
        kems_gaps = [ReadinessGap("method", GapReason.OUTSIDE_PRESSURE_REGIME, (method,))]
        kems_status = ReadinessStatus.NOT_APPLICABLE
    elif low is not None and low >= threshold:
        pass
    elif high is not None and high < threshold:
        kems_gaps = [ReadinessGap("knudsen_number_orifice", GapReason.OUTSIDE_PRESSURE_REGIME)]
        kems_status = ReadinessStatus.NOT_APPLICABLE
    else:
        kems_gaps.append(kn_gap or ReadinessGap("knudsen_number_orifice", GapReason.UNSUPPORTED_PRINT_FORM, ("Kn>=10 not established",)))
    rps_gaps = [thermal_gap] if thermal_gap and thermal.selected else []
    if thermal.selected is not None and thermal.selected.route == "temperature_points_only":
        rps_gaps.append(
            ReadinessGap(
                "thermal_path",
                GapReason.MISSING_EVIDENCE,
                ("experiment.thermal_schedule.points", "experiment.thermal_schedule.setpoints_and_holds"),
            )
        )
    rps_status = None
    if pressure.selected is not None and not _pressure_meets_floor(pressure.selected.value):
        value = pressure.selected.value
        _, high = _positive_range(value)
        below = high is not None and (high < RPS_PRESSURE_FLOOR_PA or (
            high == RPS_PRESSURE_FLOOR_PA and value.kind is ValueKind.BOUND and value.bound_operator == "<"
        ))
        gap = ReadinessGap("pressure_boundary", GapReason.BELOW_PRESSURE_FLOOR)
        if below:
            rps_gaps = [gap]
            rps_status = ReadinessStatus.NOT_APPLICABLE
        else:
            rps_gaps.append(gap)
    engine_gaps = []
    engine_status = None
    intensive = normalized_composition(experiment, bench, observation)
    if intensive.selected is not None and len(intensive.selected.value) == 1:
        engine_gaps = [ReadinessGap("normalized_composition", GapReason.SINGLE_SPECIES_CHARGE)]
        engine_status = ReadinessStatus.NOT_APPLICABLE
    engine_results = tuple(
        build("engine_point", engine_gaps, engine_status, engine)
        for engine in ENGINE_POINT_CONSUMERS
    )
    return (
        ConsumerReadiness("kems", kems_status or (ReadinessStatus.GAP if kems_gaps else ReadinessStatus.READY), tuple(kems_gaps), notices=kems_notices),
        build("rps", rps_gaps, rps_status),
        *engine_results,
    )


def consumer_readiness(experiment: Experiment, bench: Bench, observation: Observation | None = None,
                       *, modelling_inputs=None) -> tuple[ConsumerReadiness, ...]:
    from simulator.battery.consumer_inputs import collect_consumer_inputs
    from simulator.battery.generators.bench import kems_case, vacuum_pyrolysis_preset, engine_point_requests

    inputs = collect_consumer_inputs(experiment, bench, observation)
    generated = (kems_case(inputs), vacuum_pyrolysis_preset(inputs, modelling_inputs=modelling_inputs),
                 *engine_point_requests(inputs))
    return tuple(item.readiness for item in generated)
