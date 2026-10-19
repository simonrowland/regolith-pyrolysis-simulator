"""One fO2 per row, shared by every engine (d-180).

The ladder is the seeding order from d-160/d-163:

1. printed fO2;
2. printed Fe3+/ΣFe through the Kress91 inverse;
3. the experiment's controlled atmosphere or buffer, including the
   effusion oxygen-mass-balance closure and metal/slag saturation;
4. the melt's intrinsic fO2, flagged borrowed.

Tiers 1, 2 and 4 share one numeric fO2. For tier 3 effusion the shared
input is the closure condition, not a number.

Tiers 1 and the atmosphere part of tier 3 reuse ``oxygen_condition`` routes.
The Kress inverse lives in ``simulator.fe_redox``. The effusion closure is
the existing oxygen-balance solver; this module only names the route.
Tier 4 is ``intrinsic_melt_fO2``, the ledger diagnostic in this tree (the
d-160 feedstock seeder is not on this commit). A row that resolves nothing
returns None; callers refuse every engine the same way.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from simulator.battery.enums import (
    AmountBasis,
    Authority,
    MethodToken,
    Phase,
    ReferenceStateConvention,
    NoticeKind,
    Quantity,
    ValueKind,
)
from simulator.battery.composition import composition_wt_pct
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.migrate import (
    FE3_FE2_RATIO_PRINTED_FIELDS,
    FERRIC_FRACTION_FIELDS,
)
from simulator.battery.oxygen_balance import _cell_oxygen_class
from simulator.battery.records import (
    Composition,
    Experiment,
    Notice,
    Observation,
    Value,
)
from simulator.battery.waypoints import (
    Waypoint,
    WaypointAuthority,
    WaypointResult,
    oxygen_condition,
)

_TIER1_DIRECT_ROUTES = frozenset({
    "oxygen_partial_pressure_to_log_fO2",
    "oxygen_sweep_partial_pressure",
    "oxygen_sweep_component_partial_pressure",
})
_TIER3_ATMOSPHERE_ROUTES = (
    "observation_gas_composition",
    "observation_gas_couple_equilibrium",
    "graphite_c_co_buffer",
    "buffer_relation",
)
_VACUUM_ROUTE = "vacuum_total_pressure_upper_bound"
_EFFUSION_ROUTE = "effusion_oxygen_balance"
_BLANK_CARRIER = frozenset({"", "none", "n/a", "na", "absent", "unknown", "null"})
_TEMPERATURE_UNSET = object()


@dataclass(frozen=True)
class ResolvedFo2:
    log10_fO2_bar: float
    tier: int
    route: str
    borrowed: bool
    # Set when the printed source is a pascal value. Callers command
    # float(Pa)/1e5 rather than 10**log so that printed row stays bit-identical.
    po2_bar: float | None = None
    # Tier-3 effusion. The shared input is the closure condition. ``log10``
    # is NaN and ``po2_bar`` is None; callers must not command 10**log.
    # ``cell_material`` is the modelled reservoir (W or Mo), or None.
    closure: bool = False
    cell_material: str | None = None


def fo2_tier_notice(quantity: Quantity, resolved: ResolvedFo2) -> Notice:
    reason = f"fo2_tier={resolved.tier}; route={resolved.route}"
    if resolved.borrowed:
        reason += "; borrowed"
    return Notice(
        kind=(
            NoticeKind.OUT_OF_CERTIFIED_BAND
            if resolved.borrowed
            else NoticeKind.SOURCE_DISAGREEMENT
        ),
        affected_quantities=(quantity,),
        reason=reason,
        origin="fo2_ladder",
        band=f"borrowed fO2: {resolved.route}" if resolved.borrowed else None,
        authority=Authority.BRIDGE if resolved.borrowed else None,
    )


def derived_fo2_condition(observation: Observation) -> bool:
    point = (observation.point_conditions or {}).get("fO2_Pa")
    if point is not None:
        if point.inference is not None:
            return True
        note = point.locator.note if point.locator is not None else None
        if note is not None and "derived condition" in note.casefold():
            return True
    return any(
        notice.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
        and isinstance(notice.reason, str)
        and notice.reason.startswith("fO2_Pa is a DERIVED condition")
        for notice in observation.notices
    )


def has_printed_fo2(observation: Observation, identity: Identity) -> bool:
    if derived_fo2_condition(observation):
        return False
    fo2 = identity.fO2_Pa
    if fo2 is not None and fo2.is_value and fo2.value is not None:
        return True
    point = (observation.point_conditions or {}).get("fO2_Pa")
    return bool(
        point is not None
        and point.inference is None
        and point.state.is_value
    )


def resolve_row_fo2(
    experiment: Experiment | None,
    bench: object | None,
    observation: Observation | None,
    *,
    composition: Composition | None = None,
    composition_moles: Mapping[str, object] | None = None,
    composition_wt: Mapping[str, float] | None = None,
    temperature_K: float | None | object = _TEMPERATURE_UNSET,
    oxygen: WaypointResult | None = None,
    quantity: Quantity | None = None,
) -> ResolvedFo2 | None:
    """Resolve one fO2 for this row. None when every tier fails.

    An explicit ``temperature_K=None`` means the caller already knows the
    row has no positive point temperature. The identity temperature is used
    only when the caller does not pass one.

    ``quantity`` selects the effusion closure. Activity callers omit it, so
    a melt-activity row never takes the vapour closure.
    """

    identity = _identity(observation)
    if temperature_K is _TEMPERATURE_UNSET:
        temperature = _temperature_K(identity, None)
    elif isinstance(temperature_K, (int, float)):
        temperature = _temperature_K(None, float(temperature_K))
    else:
        temperature = None
    routes = _oxygen_routes(experiment, bench, observation, oxygen)
    for route in routes:
        if _is_tier1(route, observation):
            log = _log_point(route.value)
            if log is not None:
                return ResolvedFo2(log, 1, route.route, False)
    printed = _printed_identity_bar(observation, identity)
    if printed is not None:
        bar, log = printed
        return ResolvedFo2(log, 1, "identity_fO2_Pa", False, po2_bar=bar)
    weights = _weight_percent(composition, composition_moles, composition_wt, identity)
    tier2 = _tier2(observation, weights, temperature, experiment, bench, identity)
    if tier2 is not None:
        return tier2
    for route in routes:
        if route.route in _TIER3_ATMOSPHERE_ROUTES:
            log = _log_point(route.value)
            if log is not None:
                return ResolvedFo2(log, 3, route.route, False)
    atmosphere = _gas_ratio_fo2(experiment, temperature)
    if atmosphere is not None:
        return atmosphere
    closure = _effusion_closure(
        experiment,
        bench,
        observation,
        quantity=quantity,
        weights=weights,
    )
    if closure is not None:
        return closure
    if _metal_saturation_declared(experiment):
        return _metal_saturation_fo2(observation, temperature, quantity)
    for route in routes:
        if route.route == _VACUUM_ROUTE:
            log = _log_point(route.value)
            if log is not None:
                return ResolvedFo2(log, 3, route.route, False)
    if _knudsen_blocks_intrinsic(experiment, observation, quantity):
        return None
    return _tier4(weights, temperature)


def _metal_saturation_declared(experiment: Experiment | None) -> bool:
    if experiment is None or experiment.sample.form is None:
        return False
    form = experiment.sample.form.state
    if not form.is_value or not isinstance(form.value, str):
        return False
    folded = form.value.casefold()
    return any(
        marker in folded
        for marker in ("liquid iron", "solid iron", "liquid fe", "solid fe")
    )


def _metal_saturation_fo2(
    observation: Observation | None,
    temperature_K: float | None,
    quantity: Quantity | None,
) -> ResolvedFo2 | None:
    identity = _identity(observation)
    resolved_quantity = quantity or (
        None if identity is None else quantity_token(identity)
    )
    if (
        observation is None
        or identity is None
        or temperature_K is None
        or resolved_quantity is not Quantity.ACTIVITY
        or identity.species.formula.strip().casefold() != "feo"
        or identity.species.phase is None
        or not identity.species.phase.is_value
        or identity.species.phase.value is not Phase.L
    ):
        return None
    reference_state = identity.reference_state
    if (
        reference_state is None
        or not reference_state.is_value
        or reference_state.value.convention
        is not ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        or reference_state.value.endmember.formula.strip().casefold() != "feo"
        or reference_state.value.endmember.phase is None
        or not reference_state.value.endmember.phase.is_value
        or reference_state.value.endmember.phase.value is not Phase.L
    ):
        return None
    value = observation.value
    if value.kind is not ValueKind.POINT or value.point is None:
        return None
    try:
        activity_feo = float(value.point)
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not math.isfinite(activity_feo) or activity_feo <= 0.0:
        return None

    # Premise: coexisting pure Fe fixes a_Fe=1 in 2 Fe + O2 = 2 FeO.
    # Algebra: 0=ΔG°+RT ln(a_FeO²/(a_Fe² fO2)), hence
    #          log10(fO2)=2 log10(a_FeO)+ΔG°/(RT ln 10).
    # Unit check: JANAF ΔG° is kJ/mol-O2; (R/1000)·T is kJ/mol-O2.
    # Sanity: at 1873 K with printed a_FeO=0.306, the owner gives -9.6861.
    from simulator.chemistry.ellingham_thermo import (
        ellingham_delta_g_kj_per_mol_o2,
    )
    from simulator.physical_constants import GAS_CONSTANT

    try:
        delta_g_kj_per_mol_o2 = ellingham_delta_g_kj_per_mol_o2(
            "Fe", temperature_K
        )
    except (TypeError, ValueError, ArithmeticError):
        return None
    log10_fo2 = (
        2.0 * math.log10(activity_feo)
        + delta_g_kj_per_mol_o2
        / ((GAS_CONSTANT / 1000.0) * temperature_K * math.log(10.0))
    )
    if not math.isfinite(log10_fo2):
        return None
    route = "metal_saturation"
    if resolved_quantity is Quantity.ACTIVITY:
        route += "; fo2 from printed a_FeO (circular for a_FeO rows)"
    return ResolvedFo2(log10_fo2, 3, route, False)


def _identity(observation: Observation | None) -> Identity | None:
    if observation is None or not isinstance(observation.identity, Identity):
        return None
    return observation.identity


def _temperature_K(identity: Identity | None, explicit: float | None) -> float | None:
    if explicit is not None:
        try:
            number = float(explicit)
        except (TypeError, ValueError):
            return None
        if math.isfinite(number) and number > 0.0:
            return number
        return None
    if identity is None or identity.temperature_K is None:
        return None
    state = identity.temperature_K
    if not state.is_value or state.value is None:
        return None
    try:
        number = float(state.value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(number) and number > 0.0:
        return number
    return None


def _oxygen_routes(
    experiment: Experiment | None,
    bench: object | None,
    observation: Observation | None,
    oxygen: WaypointResult | None,
) -> tuple[Waypoint, ...]:
    if oxygen is None and experiment is not None and bench is not None:
        oxygen = oxygen_condition(experiment, bench, observation)
    if oxygen is None:
        return ()
    return oxygen.routes


def _is_tier1(route: Waypoint, observation: Observation | None) -> bool:
    if route.route == "observation_fO2_log":
        return route.authority is WaypointAuthority.PRINTED
    if route.route == "observation_fO2_Pa_to_log_fO2":
        if observation is None or derived_fo2_condition(observation):
            return False
        point = (observation.point_conditions or {}).get("fO2_Pa")
        return point is not None and point.inference is None
    return route.route in _TIER1_DIRECT_ROUTES


def _log_point(value: object) -> float | None:
    if not isinstance(value, Value) or value.kind is not ValueKind.POINT:
        return None
    if value.point is None:
        return None
    try:
        number = float(value.point)
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _printed_identity_bar(
    observation: Observation | None, identity: Identity | None
) -> tuple[float, float] | None:
    if observation is None or identity is None:
        return None
    if not has_printed_fo2(observation, identity):
        return None
    state = identity.fO2_Pa
    if state is None or not state.is_value or state.value is None:
        return None
    try:
        bar = float(state.value) / 1.0e5
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not math.isfinite(bar) or bar <= 0.0:
        return None
    return bar, math.log10(bar)


def _weight_percent(
    composition: Composition | None,
    composition_moles: Mapping[str, object] | None,
    composition_wt: Mapping[str, float] | None,
    identity: Identity | None,
) -> dict[str, float] | None:
    if composition_wt:
        return {
            str(name): float(amount)
            for name, amount in composition_wt.items()
            if _positive_finite(amount)
        } or None
    comp = composition
    if comp is None and composition_moles:
        comp = Composition(
            "oxides",
            tuple(
                (str(name), Decimal(str(amount)))
                for name, amount in composition_moles.items()
            ),
            AmountBasis.MOLE_FRACTION,
        )
    if comp is None and identity is not None and identity.composition is not None:
        state = identity.composition
        if state.is_value and isinstance(state.value, Composition):
            comp = state.value
    if comp is None:
        return None
    from simulator.accounting.exceptions import UnknownSpeciesError

    try:
        return composition_wt_pct(comp)
    except (UnknownSpeciesError, ValueError, TypeError, ArithmeticError):
        return None


def _positive_finite(value: object) -> bool:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0


def _printed_number(observation: Observation | None, key: str) -> float | None:
    if observation is None:
        return None
    located = (observation.point_conditions or {}).get(key)
    if located is None or located.inference is not None or not located.state.is_value:
        return None
    raw = located.state.value
    if isinstance(raw, Value):
        if raw.kind is not ValueKind.POINT or raw.point is None:
            return None
        raw = raw.point
    try:
        number = float(raw)
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _tier2(
    observation: Observation | None,
    weights: dict[str, float] | None,
    temperature_K: float | None,
    experiment: Experiment | None,
    bench: object | None,
    identity: Identity | None,
) -> ResolvedFo2 | None:
    if temperature_K is None:
        return None
    fraction, source = _printed_ferric_fraction(observation, weights)
    if fraction is None:
        return None
    pressure_bar, pressure_route = _kress_pressure_bar(
        experiment, bench, observation, identity
    )
    mol_fractions: Mapping[str, float] = {}
    if weights:
        from simulator.fe_redox import (
            Kress91InvalidControls,
            melt_mol_fractions_for_kress91,
        )

        try:
            mol_fractions = melt_mol_fractions_for_kress91(weights)
        except Kress91InvalidControls:
            mol_fractions = {}
    from simulator.fe_redox import (
        Kress91InvalidControls,
        kress91_log10_fO2_from_fe3_over_sigma_fe,
    )

    try:
        log = kress91_log10_fO2_from_fe3_over_sigma_fe(
            fe3_over_sigma_fe=fraction,
            mol_fractions=mol_fractions,
            T_K=temperature_K,
            pressure_bar=pressure_bar,
        )
    except Kress91InvalidControls:
        return None
    if not math.isfinite(log):
        return None
    return ResolvedFo2(
        log,
        2,
        f"kress_inverse:{source};pressure={pressure_route}",
        False,
    )


def _printed_ferric_fraction(
    observation: Observation | None,
    weights: dict[str, float] | None,
) -> tuple[float | None, str]:
    for key in FERRIC_FRACTION_FIELDS:
        number = _printed_number(observation, key)
        if number is not None and 0.0 < number < 1.0:
            return number, key
    for key in FE3_FE2_RATIO_PRINTED_FIELDS:
        ratio = _printed_number(observation, key)
        if ratio is not None and ratio > 0.0:
            fraction = ratio / (1.0 + ratio)
            if 0.0 < fraction < 1.0:
                return fraction, key
    if not weights:
        return None, ""
    from simulator.feedstock_composition import iron_oxide_values

    try:
        feo, fe2o3 = iron_oxide_values(weights)
    except ValueError:
        return None, ""
    if feo <= 0.0 or fe2o3 <= 0.0:
        return None, ""
    from simulator.battery.migrate import fe3_fe2_ratio_from_oxide_wt_pct

    ratio = fe3_fe2_ratio_from_oxide_wt_pct(Decimal(str(fe2o3)), Decimal(str(feo)))
    if ratio is None or ratio <= 0:
        return None, ""
    fraction = float(ratio / (Decimal(1) + ratio))
    if not 0.0 < fraction < 1.0:
        return None, ""
    return fraction, "FeO+Fe2O3"


def _kress_pressure_bar(
    experiment: Experiment | None,
    bench: object | None,
    observation: Observation | None,
    identity: Identity | None,
) -> tuple[float, str]:
    if identity is not None and identity.total_pressure_Pa is not None:
        state = identity.total_pressure_Pa
        if state.is_value and state.value is not None:
            try:
                pascal = float(state.value)
            except (TypeError, ValueError):
                pascal = -1.0
            if math.isfinite(pascal) and pascal > 0.0:
                return pascal / 1.0e5, "identity.total_pressure_Pa"
    if experiment is not None and bench is not None:
        from simulator.battery.waypoints import (
            _prediction_pressure_waypoint,
            pressure_boundary,
        )

        boundary = _prediction_pressure_waypoint(
            pressure_boundary(experiment, bench, observation),
            experiment.method,
        )
        if boundary is not None:
            log_bar = _pascal_point_to_bar(boundary.value)
            if log_bar is not None:
                return log_bar, boundary.route
    return 1.0, "kress_reference_1_bar"


def _pascal_point_to_bar(value: object) -> float | None:
    if not isinstance(value, Value) or value.kind is not ValueKind.POINT:
        return None
    if value.point is None or value.point <= 0:
        return None
    try:
        bar = float(value.point) / 1.0e5
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not math.isfinite(bar) or bar <= 0.0:
        return None
    return bar


def _gas_ratio_fo2(
    experiment: Experiment | None, temperature_K: float | None
) -> ResolvedFo2 | None:
    if experiment is None or temperature_K is None or experiment.fO2_control is None:
        return None
    ratio = experiment.fO2_control.gas_ratio
    if ratio is None or not ratio.value.state.is_value:
        return None
    raw = ratio.value.state.value
    if isinstance(raw, Value):
        if raw.kind is not ValueKind.POINT or raw.point is None:
            return None
        raw = raw.point
    try:
        number = float(raw)
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not math.isfinite(number) or number <= 0.0:
        return None
    from simulator.chemistry.offgas_fo2 import OffgasFO2Unavailable, imposed_fo2

    try:
        computed = imposed_fo2(
            {ratio.numerator_species: number, ratio.denominator_species: 1.0},
            temperature_K,
        )
    except OffgasFO2Unavailable:
        return None
    if computed.log10_fO2 is None:
        return None
    log = float(computed.log10_fO2)
    if not math.isfinite(log):
        return None
    return ResolvedFo2(log, 3, "experiment_gas_ratio", False)


def _is_knudsen(experiment: Experiment | None) -> bool:
    return bool(
        experiment is not None
        and experiment.method.is_value
        and experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    )


def _vapour_quantity(quantity: Quantity | None) -> bool:
    if quantity is None:
        return False
    from simulator.battery.validate import _VAPOUR_EQUILIBRIUM

    return quantity in _VAPOUR_EQUILIBRIUM


def _named_carrier(species: str | None) -> bool:
    if species is None:
        return False
    return species.strip().casefold() not in _BLANK_CARRIER


def _has_carrier_gas(experiment: Experiment | None) -> bool:
    """True when the experiment states a real sweep or carrier gas."""

    if experiment is None:
        return False
    located = experiment.pressure_environment.sweep_gas
    state = getattr(located, "state", None)
    if state is None or not state.is_value or state.value is None:
        return False
    gas = state.value
    if _named_carrier(getattr(gas, "species", None)):
        return True
    for component in getattr(gas, "components", None) or ():
        if _named_carrier(getattr(component, "species", None)):
            return True
    for alternative in getattr(gas, "alternatives", None) or ():
        if _named_carrier(getattr(alternative, "species", None)):
            return True
    return False


def _closable_cell(bench: object | None) -> tuple[bool, str | None]:
    """(True, metal or None) when the cell is inert or a uniform W/Mo reservoir."""

    materials = None if bench is None else getattr(bench, "cell_materials", None)
    material_class, modelled = _cell_oxygen_class(materials)
    if material_class == "inert":
        return True, None
    if modelled is not None:
        return True, modelled
    return False, None


def _formula_has_oxygen(formula: str) -> bool:
    elements = []
    token = ""
    for character in formula:
        if character.isupper():
            if token:
                elements.append(token)
            token = character
        elif character.islower() and token:
            token += character
        elif token and not character.isalpha():
            elements.append(token)
            token = ""
    if token:
        elements.append(token)
    return "O" in elements and any(element != "O" for element in elements)


def _oxide_or_melt(weights: dict[str, float] | None) -> bool:
    if not weights:
        return False
    return all(_formula_has_oxygen(name) for name in weights)


def _effusion_closure(
    experiment: Experiment | None,
    bench: object | None,
    observation: Observation | None,
    *,
    quantity: Quantity | None,
    weights: dict[str, float] | None,
) -> ResolvedFo2 | None:
    """Tier-3 oxygen mass balance, after atmosphere routes and before vacuum.

    A Knudsen cell that is inert or a uniform W/Mo reservoir closes, including
    a p_sat row. Any other congruent vaporisation of a pure oxide or an oxide
    melt, with no carrier gas, closes for a partial or reference pressure.
    p_sat outside a closable Knudsen cell stays not an oxygen input.
    """

    if not _vapour_quantity(quantity) or experiment is None or observation is None:
        return None
    identity = _identity(observation)
    if identity is not None and has_printed_fo2(observation, identity):
        return None
    if _has_carrier_gas(experiment):
        return None
    if _is_knudsen(experiment):
        closes, material = _closable_cell(bench)
        if not closes:
            return None
        return ResolvedFo2(
            float("nan"),
            3,
            _EFFUSION_ROUTE,
            False,
            closure=True,
            cell_material=material,
        )
    if quantity is Quantity.P_SAT or not _oxide_or_melt(weights):
        return None
    return ResolvedFo2(
        float("nan"),
        3,
        _EFFUSION_ROUTE,
        False,
        closure=True,
        cell_material=None,
    )


def _knudsen_blocks_intrinsic(
    experiment: Experiment | None,
    observation: Observation | None,
    quantity: Quantity | None,
) -> bool:
    """A Knudsen row that did not close does not borrow the melt's intrinsic fO2."""

    if not _vapour_quantity(quantity) or not _is_knudsen(experiment):
        return False
    if observation is None:
        return True
    identity = _identity(observation)
    if identity is not None and has_printed_fo2(observation, identity):
        return False
    return True


def _tier4(
    weights: dict[str, float] | None, temperature_K: float | None
) -> ResolvedFo2 | None:
    if temperature_K is None or not weights:
        return None
    from simulator.fe_redox import intrinsic_melt_fO2

    log = float(intrinsic_melt_fO2(weights, temperature_K))
    if not math.isfinite(log):
        return None
    return ResolvedFo2(log, 4, "intrinsic_melt_fO2", True)
