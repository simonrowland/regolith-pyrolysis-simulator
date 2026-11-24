"""One fO2 per row, shared by every engine (d-180).

The general ladder is the seeding order from d-160/d-163:

1. printed fO2;
2. printed Fe3+/ΣFe through the Kress91 inverse;
3. the experiment's controlled atmosphere or buffer, including the
   effusion oxygen-mass-balance closure; unsupported FeO(l) metal-saturation
   activity targets are refused under d-305;
4. the melt's intrinsic fO2, flagged borrowed.

Knudsen vapour rows keep printed fO2 and explicit atmosphere/buffer routes
first, then use a measured same-cell oxygen condition, then congruent effusion
before composition-only redox estimates. If its melt composition or
temperature is missing, the row does not resolve.

Tiers 1, 2 and 4 share one numeric fO2. Tier 3 can be a numeric measured
same-cell pressure, or an effusion closure whose shared input is not a number.

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
from dataclasses import dataclass, replace
from decimal import Decimal

from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Fo2Mode,
    MethodToken,
    Phase,
    ReferenceStateConvention,
    NoticeKind,
    Quantity,
    ValueKind,
)
from simulator.battery.composition import composition_wt_pct
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.redox_fields import (
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
    nominal_pressure_pa,
)
from simulator.source_ids import RYS_SOURCE_ID
from simulator.battery.waypoints import (
    EXPERIMENT_RUN_OXYGEN_ROUTES,
    TYPED_RUN_OXYGEN_ROUTES,
    Waypoint,
    WaypointAuthority,
    WaypointResult,
    oxygen_condition,
)
from simulator.oxygen_buffers import log10_fo2_c_co_bar
from simulator.physical_constants import PA_PER_BAR

_TIER1_DIRECT_ROUTES = TYPED_RUN_OXYGEN_ROUTES | EXPERIMENT_RUN_OXYGEN_ROUTES
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
    # Physics-95 rung is separate from the existing generic ``tier`` taxonomy.
    physics_rung: int | None = None


_PHYSICS_RUNG_LABELS = {
    1: "printed_fO2_or_buffer",
    2: "measured_same_cell_oxygen",
    3: "congruent_effusion_balance",
    4: "refusal",
}


def fo2_tier_notice(quantity: Quantity, resolved: ResolvedFo2) -> Notice:
    reason = f"fo2_tier={resolved.tier}"
    if resolved.physics_rung is not None:
        reason += (
            f"; physics_rung={resolved.physics_rung}:"
            f"{_PHYSICS_RUNG_LABELS[resolved.physics_rung]}"
        )
    reason += f"; route={resolved.route}"
    if resolved.borrowed:
        reason += "; borrowed"
    if resolved.route.endswith("rys_pO2_calculated_from_pNa_not_independent"):
        reason += "; source-calculated pO2 from observed pNa omitted as a circular condition"
        kind = NoticeKind.INPUT_OMITTED
    elif resolved.route.endswith("rys_pO2_provenance_unknown"):
        reason += "; row-level pO2 provenance unknown; treated as non-independent"
        kind = NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
    else:
        kind = (
            NoticeKind.OUT_OF_CERTIFIED_BAND
            if resolved.borrowed
            else NoticeKind.SOURCE_DISAGREEMENT
        )
    return Notice(
        kind=kind,
        affected_quantities=(quantity,),
        reason=reason,
        origin="fo2_ladder",
        band=f"borrowed fO2: {resolved.route}" if resolved.borrowed else None,
        authority=Authority.BRIDGE if resolved.borrowed else None,
        fo2_mode=Fo2Mode.SHARED_LADDER,
    )


def fo2_refusal_notice(quantity: Quantity) -> Notice:
    return Notice(
        kind=NoticeKind.INPUT_OMITTED,
        affected_quantities=(quantity,),
        reason=f"physics_rung=4:{_PHYSICS_RUNG_LABELS[4]}",
        origin="fo2_ladder",
    )


def engine_intrinsic_fo2_notice(quantity: Quantity) -> Notice:
    """Record an engine's own redox calculation with no supplied fO2."""

    return Notice(
        kind=NoticeKind.INPUT_OMITTED,
        affected_quantities=(quantity,),
        reason="engine intrinsic redox; no fO2 supplied",
        origin="fo2_ladder",
        fo2_mode=Fo2Mode.ENGINE_INTRINSIC,
    )


def derived_fo2_condition(observation: Observation) -> bool:
    """True when the row's oxygen is derived rather than printed.

    A pure pressure-unit conversion stays printed (d-216, source_oxygen).
    """

    from simulator.battery.source_oxygen import (
        oxygen_pressure_point,
        pressure_has_only_unit_conversion,
    )

    point = oxygen_pressure_point(observation)
    if point is not None:
        if point.inference is not None and not pressure_has_only_unit_conversion(point):
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
    from simulator.battery.source_oxygen import printed_source_pressure

    if _rys_oxygen_measurement_status(observation, None) not in {
        None,
        "measured_independent",
    }:
        return False
    if derived_fo2_condition(observation):
        return False
    fo2 = identity.fO2_Pa
    if fo2 is not None and fo2.is_value and fo2.value is not None:
        return True
    return printed_source_pressure(observation) is not None


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
    source_oxygen_bar: float | None = None,
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
    knudsen_vapour = _vapour_quantity(quantity) and _is_knudsen(experiment)
    rys_row_oxygen_unverified = (
        _rys_oxygen_measurement_status(observation, quantity)
        not in {None, "measured_independent"}
    )
    for route in routes:
        if (
            rys_row_oxygen_unverified
            and route.route in {
                "observation_fO2_log",
                "observation_fO2_Pa_to_log_fO2",
            }
        ):
            continue
        if _is_tier1(route, observation):
            log = _log_point(route.value)
            if log is not None:
                return ResolvedFo2(
                    log, 1, route.route, False,
                    physics_rung=1 if knudsen_vapour else None,
                )
    printed = (
        None
        if rys_row_oxygen_unverified
        else _printed_identity_bar(observation, identity)
    )
    if printed is not None:
        bar, log = printed
        return ResolvedFo2(
            log, 1, "identity_fO2_Pa", False, po2_bar=bar,
            physics_rung=1 if knudsen_vapour else None,
        )
    weights = _weight_percent(composition, composition_moles, composition_wt, identity)
    # On a Knudsen vapour row, an unprinted ferric ratio or melt redox estimate
    # does not replace the cell's explicit oxygen control or effusion balance.
    if not knudsen_vapour:
        tier2 = _tier2(observation, weights, temperature, experiment, bench, identity)
        if tier2 is not None:
            return tier2
    for route in routes:
        if route.route in _TIER3_ATMOSPHERE_ROUTES:
            if (
                route.route == "buffer_relation"
                and uncalibrated_buffer_awaits_receipt(observation)
            ):
                continue
            log = _log_point(route.value)
            if log is not None:
                return ResolvedFo2(
                    log, 3, route.route, False,
                    physics_rung=1 if knudsen_vapour else None,
                )
    atmosphere = _gas_ratio_fo2(experiment, temperature)
    if atmosphere is not None:
        if knudsen_vapour:
            return ResolvedFo2(
                atmosphere.log10_fO2_bar,
                atmosphere.tier,
                atmosphere.route,
                atmosphere.borrowed,
                po2_bar=atmosphere.po2_bar,
                closure=atmosphere.closure,
                cell_material=atmosphere.cell_material,
                physics_rung=1,
            )
        return atmosphere
    if knudsen_vapour and source_oxygen_bar is not None:
        try:
            bar = float(source_oxygen_bar)
        except (TypeError, ValueError, ArithmeticError):
            bar = math.nan
        if math.isfinite(bar) and bar > 0.0:
            return ResolvedFo2(
                math.log10(bar),
                3,
                "source_linked_oxygen_condition",
                False,
                po2_bar=bar,
                physics_rung=2,
            )
    if (
        observation is not None
        and observation.source_id == "rego-1985-na2o-sio2-transpiration"
        and identity is not None
        and getattr(identity.species, "formula", None) == "Na"
        and quantity is Quantity.P_PARTIAL
        and temperature is not None
        and math.isfinite(temperature)
        and temperature > 0.0
    ):
        total_pressure = identity.total_pressure_Pa
        if (
            total_pressure is not None
            and total_pressure.is_value
            and total_pressure.value is not None
        ):
            pressure_pa = nominal_pressure_pa(total_pressure)
            p_co_bar = (
                math.nan if pressure_pa is None else float(pressure_pa) / PA_PER_BAR
            )
            if math.isfinite(p_co_bar) and p_co_bar > 0.0:
                # Rego reports graphite under a CO-only cover, so the stated
                # CO pressure is the row's total pressure. Its observed pNa is
                # the target and does not enter this source condition.
                return ResolvedFo2(
                    log10_fo2_c_co_bar(temperature, p_co_bar),
                    3,
                    "rego_c_co_two_species_approximation",
                    False,
                    physics_rung=1,
                )
    if knudsen_vapour and (temperature is None or not weights):
        return None
    closure = _effusion_closure(
        experiment,
        bench,
        observation,
        temperature_K=temperature,
        quantity=quantity,
        weights=weights,
    )
    if closure is not None:
        provenance = _rys_pO2_provenance(observation, quantity)
        if provenance is not None:
            return replace(closure, route=f"{closure.route}; {provenance}")
        return closure
    if metal_saturation_activity_refusal(experiment, observation, quantity) is not None:
        return None
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


def metal_saturation_activity_refusal(
    experiment: Experiment | None,
    observation: Observation | None,
    quantity: Quantity | None = None,
) -> Notice | None:
    """Typed d-305 refusal for an FeO(l) activity that would seed its own fO2."""

    if not _metal_saturation_declared(experiment):
        return None
    identity = _identity(observation)
    resolved_quantity = quantity or (
        None if identity is None else quantity_token(identity)
    )
    if (
        observation is None
        or identity is None
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
    return Notice(
        kind=NoticeKind.INPUT_OMITTED,
        affected_quantities=(Quantity.ACTIVITY,),
        reason=(
            "d-305: metal-saturation FeO(l) activity target cannot supply its "
            "own fO2; fO2 and a_FeO must be solved jointly from bulk composition"
        ),
        origin="fo2_ladder",
    )


def _rys_oxygen_measurement_status(
    observation: Observation | None, quantity: Quantity | None,
) -> str | None:
    identity = _identity(observation)
    if (
        observation is None
        or observation.source_id != RYS_SOURCE_ID
        or identity is None
        or getattr(identity.species, "formula", None) != "Na"
        or (quantity or quantity_token(identity)) is not Quantity.P_PARTIAL
    ):
        return None
    status = (observation.provenance or {}).get(
        "oxygen_measurement_status", "unknown"
    )
    return status if isinstance(status, str) else "unknown"


def _rys_pO2_provenance(
    observation: Observation | None, quantity: Quantity | None,
) -> str | None:
    status = _rys_oxygen_measurement_status(observation, quantity)
    if status is None:
        return None
    if (
        status == "calculated_from_pNa"
        or derived_fo2_condition(observation)
    ):
        return "rys_pO2_calculated_from_pNa_not_independent"
    return "rys_pO2_provenance_unknown"


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


def absolute_oxygen_frame(identity: Identity | None) -> bool:
    """True when the row's oxygen pressure is already an absolute value.

    Migration writes that value only after it has bound a frame: an explicit
    bar or atm unit, a printed atmosphere match, or the 1-bar convention.
    A located log with no inference is not a frame. The scale-unknown
    sentence is not a value, so it is not absolute.
    """

    if identity is None:
        return False
    state = identity.fO2_Pa
    if state is None or not state.is_value or state.value is None:
        return False
    try:
        pa = float(state.value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(pa) and pa > 0.0


def scale_unknown_oxygen(identity: Identity | None) -> bool:
    """True when the stored pressure says the log's scale was not printed."""

    if identity is None or identity.fO2_Pa is None or not identity.fO2_Pa.is_unknown:
        return False
    from simulator.battery.migrate import _BARE_LOG_SCALE_UNKNOWN

    return identity.fO2_Pa.reason == _BARE_LOG_SCALE_UNKNOWN


def scale_unknown_evidence_clause(
    identity: Identity | None, printed_fO2_log: str | None
) -> str | None:
    """Flag text for a log whose absolute-versus-buffer frame was not printed.

    The clause quotes the stored reason and the printed log, including a
    real zero, and names DP-010 as the command that replaces it. Callers
    that already hold a different registered default keep their own sentence.
    """

    if not scale_unknown_oxygen(identity):
        return None
    from simulator.battery.migrate import _BARE_LOG_SCALE_UNKNOWN

    printed = "absent" if printed_fO2_log is None else printed_fO2_log
    return (
        f"original_missing_evidence={_BARE_LOG_SCALE_UNKNOWN}; "
        f"printed_fO2_log={printed}; "
        "reference_frame_default=DP-010; "
    )


def oxygen_frame_receipts(observation: Observation | None) -> tuple[Notice, ...]:
    """Notices that record the frame a grounded log was admitted under."""

    if observation is None:
        return ()
    from simulator.battery.migrate import _BARE_LOG_BAR_NOTICE

    return tuple(
        notice
        for notice in observation.notices
        if isinstance(notice.reason, str)
        and (
            notice.reason == _BARE_LOG_BAR_NOTICE
            or notice.kind is NoticeKind.FO2_FRAME_FROM_PRINTED_ATMOSPHERE
        )
    )


def uncalibrated_buffer_awaits_receipt(observation: Observation | None) -> bool:
    """True when a buffer marker is present and DP-112 has not receipted it.

    A typed buffer name is not the calibration. The marker has to be seen
    by the DP-112 resolver before any buffer command is admitted.
    """

    if observation is None:
        return False
    marker = False
    for notice in observation.notices:
        if (
            notice.kind
            is not NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
        ):
            continue
        if notice.policy is not None and notice.policy.id == "DP-112":
            return False
        marker = True
    return marker


def _is_tier1(route: Waypoint, observation: Observation | None) -> bool:
    if route.route == "observation_fO2_log":
        return (
            route.authority is WaypointAuthority.PRINTED
            and absolute_oxygen_frame(_identity(observation))
        )
    if route.route == "observation_fO2_Pa_to_log_fO2":
        if observation is None or derived_fo2_condition(observation):
            return False
        from simulator.battery.source_oxygen import (
            oxygen_pressure_point,
            pressure_has_only_unit_conversion,
        )

        point = oxygen_pressure_point(observation)
        return point is not None and pressure_has_only_unit_conversion(point)
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


def admitted_score_oxygen(
    oxygen: WaypointResult, observation: Observation | None
) -> Waypoint | None:
    """First score-time oxygen route in the waypoint owner's order.

    Tier 1 is a grounded printed log, a provenance-valid observation
    pressure, or a registered run route. An unknown frame is not tier 1.
    Tier 3 is a gas mixture or a buffer whose calibration is not waiting
    on a DP-112 receipt. An uncalibrated buffer marker is not admitted
    here: the DP-112 resolver has to see it first.
    """

    for route in oxygen.routes:
        if _is_tier1(route, observation) and _log_point(route.value) is not None:
            return route
    for route in oxygen.routes:
        if (
            route.route in _TIER3_ATMOSPHERE_ROUTES
            and route.authority is WaypointAuthority.DERIVED
            and _log_point(route.value) is not None
            and not (
                route.route == "buffer_relation"
                and uncalibrated_buffer_awaits_receipt(observation)
            )
        ):
            return route
    return None


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
        bar = float(state.value) / PA_PER_BAR
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
    from simulator.melt_backend.alphamelts import MELTS_OXIDE_ALIASES
    from simulator.melt_backend.base import is_printed_total_iron_alias
    from simulator.accounting.exceptions import (
        UnparseableCompositionComponent,
        UnknownSpeciesError,
    )

    # Total-iron aliases are a missing-fO2 refusal, not an unparseable
    # component. Validate their non-alias siblings first so an unparseable
    # composition component still receives its typed refusal. No partial
    # weights: a mass would let tier 4 fill.
    aliases = {
        str(name)
        for name, amount in comp.components
        if _positive_finite(amount)
        and is_printed_total_iron_alias(str(name), MELTS_OXIDE_ALIASES)
    }
    if aliases:
        siblings = tuple(
            (name, amount)
            for name, amount in comp.components
            if _positive_finite(amount) and str(name) not in aliases
        )
        if siblings:
            try:
                composition_wt_pct(Composition(comp.basis, siblings, comp.amount_basis))
            except UnparseableCompositionComponent:
                raise
            except (UnknownSpeciesError, ValueError, TypeError, ArithmeticError):
                pass
        return None

    try:
        return composition_wt_pct(comp)
    except UnparseableCompositionComponent:
        raise
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
            pascal = nominal_pressure_pa(state)
            if pascal is not None and pascal.is_finite() and pascal > 0:
                return float(pascal) / PA_PER_BAR, "identity.total_pressure_Pa"
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
        bar = float(value.point) / PA_PER_BAR
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
    temperature_K: float | None,
    quantity: Quantity | None,
    weights: dict[str, float] | None,
) -> ResolvedFo2 | None:
    """Tier-3 oxygen mass balance, after atmosphere routes and before vacuum.

    Knudsen vapor rows close when melt composition and temperature are known.
    A uniform W/Mo reservoir adds modelled wall-oxide channels; an unmodelled
    wall uses melt-only closure and is flagged by the scorer. Any other
    congruent vaporisation of a pure oxide or an oxide melt, with no carrier
    gas, closes for a partial or reference pressure. p_sat outside Knudsen
    effusion stays not an oxygen input.
    """

    if not _vapour_quantity(quantity) or experiment is None or observation is None:
        return None
    identity = _identity(observation)
    if identity is not None and has_printed_fo2(observation, identity):
        return None
    if _has_carrier_gas(experiment):
        return None
    if _is_knudsen(experiment):
        if temperature_K is None or not weights:
            return None
        materials = None if bench is None else getattr(bench, "cell_materials", None)
        _material_class, material = _cell_oxygen_class(materials)
        return ResolvedFo2(
            float("nan"),
            3,
            _EFFUSION_ROUTE,
            False,
            closure=True,
            cell_material=material,
            physics_rung=3,
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
        physics_rung=3,
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
