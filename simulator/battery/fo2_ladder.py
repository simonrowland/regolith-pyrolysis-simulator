"""One fO2 per row, shared by every engine (d-180).

The general ladder is the seeding order from d-160/d-163:

1. printed fO2;
2. printed Fe3+/ΣFe through the Kress91 inverse;
3. the experiment's controlled atmosphere or buffer, including the
   effusion oxygen-mass-balance closure; unsupported FeO(l) metal-saturation
   activity targets are refused under d-305;
4. DP-019's finite pressure boundary for vacuum or Knudsen methods, followed
   by DP-010 v2's proposed ambient-air default for other unprinted atmospheres.

Knudsen vapour rows keep printed fO2 and explicit atmosphere/buffer routes
first, then use a measured same-cell oxygen condition, then congruent effusion
before composition-only redox estimates. If its melt composition or
temperature is missing, the row does not resolve.

Tiers 1, 2 and 4 share one numeric fO2. Tier 3 can be a numeric measured
same-cell pressure, or an effusion closure whose shared input is not a number.

Tiers 1 and the atmosphere part of tier 3 reuse ``oxygen_condition`` routes.
The Kress inverse lives in ``simulator.fe_redox``. The effusion closure is
the existing oxygen-balance solver; this module only names the route.
The intrinsic-melt clamp remains the furnace-headspace diagnostic; lab rows
use it only for the flagged vacuum proxy. A row that resolves nothing returns
None; callers refuse every engine the same way.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal

from simulator.battery.default_policy import resolve
from simulator.battery.enums import (
    AmountBasis,
    Authority,
    AmbientPressureEvidenceClass,
    Fo2Mode,
    MethodToken,
    Phase,
    PHASE_COMPOSITION_QUANTITIES,
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
    KRESS_FE2O3_WT_PCT_FIELD,
    KRESS_FEO_WT_PCT_FIELD,
)
from simulator.battery.oxygen_balance import _cell_oxygen_class
from simulator.battery.records import (
    Composition,
    Experiment,
    Notice,
    NoticePolicy,
    Observation,
    Value,
    as_decimal,
    nominal_pressure_pa,
)
from simulator.source_ids import RYS_SOURCE_ID
from simulator.battery.waypoints import (
    GapReason,
    EXPERIMENT_RUN_OXYGEN_ROUTES,
    PredictionPressure,
    TYPED_RUN_OXYGEN_ROUTES,
    VACUUM_PRESSURE_UPPER_BOUND_ROUTE,
    Waypoint,
    WaypointAuthority,
    WaypointResult,
    _is_vacuum_pressure_method,
    oxygen_condition,
    route_source_pressure_pa,
)
from simulator.oxygen_buffers import log10_fo2_c_co_bar
from simulator.physical_constants import ATM_PA_DECIMAL, BAR_PA_DECIMAL, PA_PER_BAR

# DP-010 v2's declared dry-air oxygen fraction.
_DP010_AMBIENT_OXYGEN_ATM = Decimal("0.21")

_TIER1_DIRECT_ROUTES = TYPED_RUN_OXYGEN_ROUTES | EXPERIMENT_RUN_OXYGEN_ROUTES
_BUFFER_ROUTES = frozenset({
    "graphite_c_co_buffer",
    "buffer_relation",
    "container_graphite_c_co_buffer",
    "container_buffer_relation",
})
_TIER3_ATMOSPHERE_ROUTES = (
    "observation_gas_composition",
    "observation_gas_composition_method_implied_pressure",
    "observation_gas_couple_equilibrium",
    *_BUFFER_ROUTES,
)
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
    # Exact pascals from the oxygen owner. None when the route is a log by
    # definition and no located pressure was read. Callers command
    # ``pressure_pa`` and do not rebuild it from ``log10_fO2_bar``.
    pressure_pa: Decimal | None = None
    notices: tuple[Notice, ...] = ()
    policy: NoticePolicy | None = None
    temperature_band_status: str | None = None
    temperature_band_source: str | None = None
    original_reason: str | None = None


_PHYSICS_RUNG_LABELS = {
    1: "printed_fO2_or_buffer",
    2: "measured_same_cell_oxygen",
    3: "congruent_effusion_balance",
    4: "refusal",
}


def fo2_tier_notice(
    quantity: Quantity,
    resolved: ResolvedFo2,
    *,
    original_reason: str | None = None,
) -> Notice:
    reason = f"fo2_tier={resolved.tier}"
    policy = None
    policy_flag = None
    kind: NoticeKind | None = None
    ferric_ratio = resolved.route.startswith("kress_inverse:")
    vacuum_proxy = resolved.route == "fo2_vacuum_cell_proxy"
    container_buffer = resolved.route.startswith("container_")
    method_implied_gas_mix = (
        resolved.route == "observation_gas_composition_method_implied_pressure"
    )
    if resolved.physics_rung is not None:
        reason += (
            f"; physics_rung={resolved.physics_rung}:"
            f"{_PHYSICS_RUNG_LABELS[resolved.physics_rung]}"
        )
    reason += f"; route={resolved.route}"
    if ferric_ratio:
        reason = (
            "fo2_from_printed_ferric_ratio; Kress-Carmichael inverse at row T and P; "
            f"log10(fO2/bar)={resolved.log10_fO2_bar:.12g}; {reason}"
        )
        if resolved.temperature_band_status is not None:
            reason += (
                "; kress91_temperature_band_status="
                f"{resolved.temperature_band_status}; "
                f"kress91_authority={resolved.temperature_band_source}"
            )
        kind = NoticeKind.FO2_FROM_PRINTED_FERRIC_RATIO
    elif container_buffer:
        reason = (
            "fo2_buffer_from_container; buffer fixed by the printed sample container; "
            f"log10(fO2/bar)={resolved.log10_fO2_bar:.12g}; {reason}"
        )
        kind = NoticeKind.FO2_BUFFER_FROM_CONTAINER
    elif method_implied_gas_mix:
        po2_bar = resolved.po2_bar
        if po2_bar is None:
            po2_bar = 10.0**resolved.log10_fO2_bar
        reason = (
            "DP-002 method_implied total pressure for printed gas composition; "
            "pO2=xO2*P_total; "
            f"pO2/bar={po2_bar:.12g}; "
            f"log10(fO2/bar)={resolved.log10_fO2_bar:.12g}; {reason}"
        )
        policy = resolved.policy
        if policy is None:
            raise ValueError("gas-mixture fO2 is missing its DP-002 policy")
        policy_flag = NoticeKind.METHOD_IMPLIED
    elif resolved.route == "dp019_vacuum_boundary":
        reason = (
            "DP-019 method-implied high-vacuum boundary; "
            f"pO2/bar={resolved.po2_bar!r}; "
            f"log10(fO2/bar)={resolved.log10_fO2_bar:.12g}; {reason}"
        )
        policy = resolved.policy
        if policy is None:
            raise ValueError("vacuum fO2 is missing its DP-019 policy")
        policy_flag = NoticeKind.METHOD_IMPLIED
    elif vacuum_proxy:
        reason = (
            "fo2_vacuum_cell_proxy; legacy intrinsic-melt clamp used only as a "
            "vacuum-cell proxy; "
            f"log10(fO2/bar)={resolved.log10_fO2_bar:.12g}; {reason}"
        )
        kind = NoticeKind.FO2_VACUUM_CELL_PROXY
    elif resolved.route == "dp010_ambient_air_unprinted_atmosphere":
        reason = (
            "DP-010 v2; "
            f"pO2={_DP010_AMBIENT_OXYGEN_ATM} atm; fO2/bar=pO2/(1 bar); "
            f"log10(fO2/bar)={resolved.log10_fO2_bar:.12g}; "
            "original_reason="
            f"{resolved.original_reason or 'fO2 unprinted'}; {reason}"
        )
        policy = resolved.policy
        if policy is None:
            raise ValueError("DP-010 ambient-air fO2 is missing its policy")
        policy_flag = NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE
    assumed_air = resolved.route == "dp003_ambient_air"
    if assumed_air:
        reason = (
            "DP-003 default_fO2_from_air; original_reason=no printed fO2; "
            f"{reason}"
        )
        if resolved.po2_bar is not None:
            policy = resolved.policy
            if policy is None:
                raise ValueError("ambient-air fO2 is missing its DP-003 policy")
            policy_flag = NoticeKind.METHOD_IMPLIED
    if policy_flag is not None:
        kind = policy_flag
    elif kind is None:
        if resolved.route.endswith("rys_pO2_calculated_from_pNa_not_independent"):
            reason += "; source-calculated pO2 from observed pNa omitted as a circular condition"
            kind = NoticeKind.INPUT_OMITTED
        elif resolved.route.endswith("rys_pO2_provenance_unknown"):
            reason += "; row-level pO2 provenance unknown; treated as non-independent"
            kind = NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
        elif resolved.borrowed:
            kind = NoticeKind.OUT_OF_CERTIFIED_BAND
        else:
            kind = NoticeKind.SOURCE_DISAGREEMENT
    out_of_band_kress = (
        ferric_ratio
        and resolved.temperature_band_status is not None
        and resolved.temperature_band_status != "authoritative"
    )
    if out_of_band_kress:
        kind = NoticeKind.OUT_OF_CERTIFIED_BAND
    selected_bar = (
        resolved.po2_bar
        if resolved.po2_bar is not None
        else 10.0**resolved.log10_fO2_bar
    )
    selected_pa = (
        None
        if resolved.closure or not math.isfinite(selected_bar)
        else Decimal(str(selected_bar * PA_PER_BAR))
    )
    if original_reason is not None:
        reason = f"{reason}; original_reason={original_reason}"
    notice = Notice(
        kind=kind,
        affected_quantities=(quantity,),
        reason=reason,
        origin="fo2_ladder",
        band=(
            f"Kress91 temperature band: {resolved.temperature_band_status}"
            if out_of_band_kress
            else f"borrowed fO2: {resolved.route}"
            if resolved.borrowed
            else None
        ),
        authority=(
            Authority.EXTRAPOLATED
            if out_of_band_kress
            else Authority.BRIDGE
            if resolved.borrowed
            else None
        ),
        fo2_mode=Fo2Mode.SHARED_LADDER,
        policy=policy,
    )
    if resolved.route == VACUUM_PRESSURE_UPPER_BOUND_ROUTE:
        notice = replace(
            notice,
            kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
            band="vacuum pO2 upper bound substituted at Ptotal",
            authority=Authority.EXTRAPOLATED,
            reason=(
                f"{reason}; selected_fO2_Pa={selected_pa}; "
                "Dalton pO2 <= Ptotal; the upper bound was substituted as a point"
            ),
        )
    return notice


def _with_policy_notice(quantity: Quantity, resolved: ResolvedFo2) -> ResolvedFo2:
    """Keep a typed policy decision attached to its selected oxygen value."""

    if resolved.policy is None:
        raise ValueError("policy fO2 resolution is missing its policy")
    notice = fo2_tier_notice(quantity, resolved)
    return replace(resolved, notices=(*resolved.notices, notice))


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
    prediction_pressure: PredictionPressure | None = None,
) -> ResolvedFo2 | None:
    """Resolve one fO2 for this row. None when every evidence path fails.

    An explicit ``temperature_K=None`` means the caller already knows the
    row has no positive point temperature. The identity temperature is used
    only when the caller does not pass one.

    ``quantity`` selects the effusion closure and the air / DP-010 receipts.
    A melt-activity row never takes the vapour closure.

    The ladder never derives oxygen from a scored ferric target or phase
    composition. A different valence target can use independent printed Fe
    evidence. Activity and vapour keep the inverse.
    """

    identity = _identity(observation)
    if temperature_K is _TEMPERATURE_UNSET:
        temperature = _temperature_K(identity, None)
    elif isinstance(temperature_K, (int, float)):
        temperature = _temperature_K(None, float(temperature_K))
    else:
        temperature = None
    oxygen_result = _oxygen_result(experiment, bench, observation, oxygen)
    routes = () if oxygen_result is None else oxygen_result.routes
    knudsen_vapour = _vapour_quantity(quantity) and _is_knudsen(experiment)
    physics_rung = 1 if knudsen_vapour else None
    rys_row_oxygen_unverified = (
        _rys_oxygen_measurement_status(observation, quantity)
        not in {None, "measured_independent"}
    )
    # A DP-112 receipt already stored the absolute pressure. It precedes a
    # buffer waypoint and any log reconstructed from one.
    if _receipted_buffer(observation) and not rys_row_oxygen_unverified:
        admitted = absolute_frame_admission(
            observation, identity, physics_rung=physics_rung
        )
        if admitted is not None:
            return resolved_from_admitted(admitted)
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
            admitted = admit_waypoint_oxygen(
                route,
                experiment,
                observation,
                bench=bench,
                tier=1,
                physics_rung=physics_rung,
            )
            if admitted is not None:
                return resolved_from_admitted(admitted)
    if not rys_row_oxygen_unverified and (
        observation is not None
        and identity is not None
        and has_printed_fo2(observation, identity)
    ):
        admitted = absolute_frame_admission(
            observation, identity, physics_rung=physics_rung
        )
        if admitted is not None:
            return resolved_from_admitted(admitted)
    weights = _weight_percent(composition, composition_moles, composition_wt, identity)
    # On a Knudsen vapour row, an unprinted ferric ratio or melt redox estimate
    # does not replace the cell's explicit oxygen control or effusion balance.
    # A ferric target cannot command its own oxygen through the inverse. A
    # different named valence target can still use independent Fe evidence.
    scored_ferric_target = (
        quantity is Quantity.FE3_FE2_RATIO
        or (
            quantity is Quantity.VALENCE_RATIO
            and identity is not None
            and identity.species is not None
            and identity.species.formula == "Fe"
        )
    )
    if (
        not knudsen_vapour
        and not scored_ferric_target
        and quantity not in PHASE_COMPOSITION_QUANTITIES
    ):
        tier2 = _tier2(
            observation,
            weights,
            temperature,
            experiment,
            bench,
            identity,
            prediction_pressure,
        )
        if tier2 is not None:
            return _with_exact_pascals(tier2)
    for route in routes:
        if bench is not None and route.route in _TIER3_ATMOSPHERE_ROUTES:
            if route.route == "buffer_relation" and (
                uncalibrated_buffer_awaits_receipt(observation)
                or _receipted_buffer(observation)
            ):
                continue
            admitted = admit_waypoint_oxygen(
                route,
                experiment,
                observation,
                bench=bench,
                tier=3,
                physics_rung=physics_rung,
            )
            if admitted is not None:
                return resolved_from_admitted(admitted)
    unresolved = (
        admit_unresolved_buffer(
            observation, experiment, tier=3, physics_rung=physics_rung
        )
        if bench is not None
        else None
    )
    if unresolved is not None:
        return resolved_from_admitted(unresolved[1])
    gas_control = (
        None
        if experiment is None or experiment.fO2_control is None
        else experiment.fO2_control.gas_ratio
    )
    gas_ratio_printed = gas_control is not None and gas_control.value.state.is_value
    gas_without_bench = gas_ratio_printed and bench is None
    atmosphere = None if gas_without_bench else _gas_ratio_fo2(experiment, temperature)
    if atmosphere is not None:
        sealed = _with_exact_pascals(atmosphere)
        if knudsen_vapour:
            return replace(sealed, physics_rung=1)
        return sealed
    if knudsen_vapour and source_oxygen_bar is not None:
        try:
            bar = float(source_oxygen_bar)
        except (TypeError, ValueError, ArithmeticError):
            bar = math.nan
        if math.isfinite(bar) and bar > 0.0:
            return _with_exact_pascals(
                ResolvedFo2(
                    math.log10(bar),
                    3,
                    "source_linked_oxygen_condition",
                    False,
                    po2_bar=bar,
                    physics_rung=2,
                )
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
                return _with_exact_pascals(
                    ResolvedFo2(
                        log10_fo2_c_co_bar(temperature, p_co_bar),
                        3,
                        "rego_c_co_two_species_approximation",
                        False,
                        physics_rung=1,
                    )
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
    resolved_quantity = quantity or (
        None if identity is None else quantity_token(identity)
    )
    if resolved_quantity is not None:
        air = _air_oxygen_decision(
            experiment, observation, identity, resolved_quantity
        )
        if air is not None:
            return air
    # An unsupported printed apparatus is a gap. A vacuum route or vacuum
    # policy must not hide it; DP-010 remains the flagged prediction.
    unsupported_print = (
        oxygen_result is not None
        and oxygen_result.absence is not None
        and oxygen_result.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    )
    if not unsupported_print:
        if bench is not None:
            for route in routes:
                if route.route == VACUUM_PRESSURE_UPPER_BOUND_ROUTE:
                    admitted = admit_waypoint_oxygen(
                        route, experiment, observation, bench=bench, tier=3
                    )
                    if admitted is not None:
                        return resolved_from_admitted(admitted)
        if _is_vacuum_pressure_method(experiment) or _is_knudsen(experiment):
            vacuum = resolve("DP-019")
            vacuum_pressure_pa = as_decimal(vacuum.value)
            if vacuum_pressure_pa.is_finite() and vacuum_pressure_pa > 0:
                vacuum_bar = vacuum_pressure_pa / BAR_PA_DECIMAL
                vacuum_log = math.log10(float(vacuum_bar))
                return _with_policy_notice(
                    resolved_quantity,
                    ResolvedFo2(
                        vacuum_log,
                        3,
                        "dp019_vacuum_boundary",
                        False,
                        po2_bar=float(vacuum_bar),
                        pressure_pa=vacuum_pressure_pa,
                        policy=NoticePolicy(**vacuum.payload["policy"]),
                    ),
                )
    if resolved_quantity is None:
        return None
    oxygen_pa = _DP010_AMBIENT_OXYGEN_ATM * ATM_PA_DECIMAL
    oxygen_bar = oxygen_pa / BAR_PA_DECIMAL
    log_fo2_bar = math.log10(float(oxygen_bar))
    policy_result = resolve("DP-010", formula_result=Decimal(str(log_fo2_bar)))
    original_reason = "fO2 unprinted"
    if gas_without_bench:
        original_reason = "gas_ratio_requires_bench_but_none_was_supplied"
    elif (
        bench is None
        and experiment is not None
        and experiment.fO2_control is not None
        and experiment.fO2_control.buffer is not None
        and experiment.fO2_control.buffer.state.is_value
    ):
        original_reason = "buffer_requires_bench_but_none_was_supplied"
    else:
        frame_reason = scale_unknown_evidence_clause(
            identity, _printed_log_text(observation)
        )
        if frame_reason is not None:
            original_reason = f"{original_reason}; {frame_reason}"
    return _with_policy_notice(
        resolved_quantity,
        ResolvedFo2(
            log_fo2_bar,
            4,
            "dp010_ambient_air_unprinted_atmosphere",
            False,
            po2_bar=float(oxygen_bar),
            pressure_pa=oxygen_pa,
            policy=NoticePolicy(**policy_result.payload["policy"]),
            original_reason=original_reason,
        ),
    )


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


def _oxygen_result(
    experiment: Experiment | None,
    bench: object | None,
    observation: Observation | None,
    oxygen: WaypointResult | None,
) -> WaypointResult | None:
    if oxygen is None and experiment is not None and bench is not None:
        oxygen = oxygen_condition(experiment, bench, observation)
    return oxygen


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


@dataclass(frozen=True)
class AdmittedOxygen:
    """One oxygen pressure admitted with the frame that resolved it.

    ``pressure_pa`` is the stored absolute pressure. ``log10_bar`` is that
    pressure over 1 bar. Callers command ``command_bar()`` and keep
    ``pressure_pa``; they do not rebuild pascals from the printed log.
    """

    pressure_pa: Decimal
    log10_bar: Decimal
    tier: int
    route: str
    borrowed: bool
    notices: tuple[Notice, ...] = ()
    physics_rung: int | None = None

    def command_bar(self) -> float:
        return float(self.pressure_pa / BAR_PA_DECIMAL)


def decimal_log10_bar(pressure_pa: Decimal) -> Decimal:
    """log10(pressure_pa / 1 bar). 100000 Pa is exactly zero."""

    return pressure_pa.log10() - BAR_PA_DECIMAL.log10()


def stored_absolute_pressure_pa(identity: Identity | None) -> Decimal | None:
    """The identity's finite positive fO2 in pascals, or None."""

    if identity is None or identity.fO2_Pa is None or not identity.fO2_Pa.is_value:
        return None
    value = identity.fO2_Pa.value
    if not isinstance(value, Decimal):
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value


def _admission_notices(observation: Observation | None) -> tuple[Notice, ...]:
    """DP-112 receipts, their pressure warnings, and frame receipts, once each.

    ``pressure_provenance_unknown`` from the DP-112 resolver has no policy id.
    Dropping it leaves the oxygen command without the omitted-pressure warning.
    A derived-condition notice of the same kind is not a resolver warning.
    """

    if observation is None:
        return ()
    from simulator.battery.oxygen_identity import _dp112_receipts

    pressure_warnings = tuple(
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
        and notice.origin == "score:DP-112"
    )
    notices: list[Notice] = []
    for notice in (
        *_dp112_receipts(observation),
        *pressure_warnings,
        *oxygen_frame_receipts(observation),
    ):
        if notice not in notices:
            notices.append(notice)
    return tuple(notices)


def absolute_frame_admission(
    observation: Observation | None,
    identity: Identity | None,
    *,
    route: str = "identity_fO2_Pa",
    tier: int = 1,
    physics_rung: int | None = None,
) -> AdmittedOxygen | None:
    """Admit the stored absolute pressure when the frame is already resolved.

    A monkeypatched frame check with no stored pressure returns None.
    Callers do not reconstruct a bar command from the printed log.
    """

    if not absolute_oxygen_frame(identity):
        return None
    pressure_pa = stored_absolute_pressure_pa(identity)
    if pressure_pa is None:
        return None
    return AdmittedOxygen(
        pressure_pa=pressure_pa,
        log10_bar=decimal_log10_bar(pressure_pa),
        tier=tier,
        route=route,
        borrowed=False,
        notices=_admission_notices(observation),
        physics_rung=physics_rung,
    )


_LOG_DEFINED_OXYGEN_ROUTES = frozenset({
    "observation_gas_couple_equilibrium",
    "observation_gas_composition_method_implied_pressure",
    *_BUFFER_ROUTES,
})
_PA_BACKED_OXYGEN_ROUTES = (
    TYPED_RUN_OXYGEN_ROUTES
    | EXPERIMENT_RUN_OXYGEN_ROUTES
    | frozenset({
        "observation_fO2_Pa_to_log_fO2",
        VACUUM_PRESSURE_UPPER_BOUND_ROUTE,
        "observation_gas_composition",
    })
)


def resolved_from_admitted(admitted: AdmittedOxygen) -> ResolvedFo2:
    """The ladder's float command plus the owner's exact pascals and notices."""

    policy = None
    if admitted.route == "observation_gas_composition_method_implied_pressure":
        policy = NoticePolicy(**resolve("DP-002").payload["policy"])
    return ResolvedFo2(
        float(admitted.log10_bar),
        admitted.tier,
        admitted.route,
        admitted.borrowed,
        po2_bar=admitted.command_bar(),
        physics_rung=admitted.physics_rung,
        pressure_pa=admitted.pressure_pa,
        notices=admitted.notices,
        policy=policy,
    )


def _admit_log_defined(
    route: Waypoint,
    *,
    tier: int,
    observation: Observation | None,
    physics_rung: int | None,
) -> AdmittedOxygen | None:
    """One conversion of an evaluated log. The log is the quantity."""

    from simulator.battery.oxygen_identity import log10_fo2_to_pa

    if not isinstance(route.value, Value) or route.value.kind is not ValueKind.POINT:
        return None
    point = route.value.point
    if not isinstance(point, Decimal) or not point.is_finite():
        return None
    pressure_pa = log10_fo2_to_pa(point)
    if not pressure_pa.is_finite() or pressure_pa <= 0:
        return None
    return AdmittedOxygen(
        pressure_pa=pressure_pa,
        log10_bar=point,
        tier=tier,
        route=route.route,
        borrowed=False,
        notices=_admission_notices(observation),
        physics_rung=physics_rung,
    )


def admit_waypoint_oxygen(
    route: Waypoint,
    experiment: Experiment | None,
    observation: Observation | None,
    *,
    bench: object | None = None,
    tier: int,
    physics_rung: int | None = None,
) -> AdmittedOxygen | None:
    """One typed admission for a score-time oxygen waypoint.

    A route that started as pascals keeps those pascals. A route whose
    quantity is an evaluated log converts once. A missing located pressure
    is not recovered by inverting the waypoint log.
    """

    if route.route == "observation_fO2_log":
        return absolute_frame_admission(
            observation,
            _identity(observation),
            route=route.route,
            tier=tier,
            physics_rung=physics_rung,
        )
    if route.route in _PA_BACKED_OXYGEN_ROUTES:
        if experiment is None:
            return None
        pressure_pa = route_source_pressure_pa(
            experiment, bench, observation, route.route
        )
        if pressure_pa is None:
            # An ideal-mixture interval has no single point. Keep its log.
            if route.route == "observation_gas_composition":
                return _admit_log_defined(
                    route,
                    tier=tier,
                    observation=observation,
                    physics_rung=physics_rung,
                )
            return None
        return AdmittedOxygen(
            pressure_pa=pressure_pa,
            log10_bar=decimal_log10_bar(pressure_pa),
            tier=tier,
            route=route.route,
            borrowed=False,
            notices=_admission_notices(observation),
            physics_rung=physics_rung,
        )
    if route.route in _LOG_DEFINED_OXYGEN_ROUTES:
        return _admit_log_defined(
            route,
            tier=tier,
            observation=observation,
            physics_rung=physics_rung,
        )
    return None


def admit_unresolved_buffer(
    observation: Observation | None,
    experiment: Experiment | None,
    *,
    tier: int = 3,
    physics_rung: int | None = None,
) -> tuple[Observation, AdmittedOxygen] | None:
    """Resolve a calibration marker that DP-112 has not yet receipted.

    The pressure is the resolver's stored pascals. Notices are only the
    ones the resolver added. A marker the resolver cannot evaluate returns
    None so later named routes still run.
    """

    if observation is None or not uncalibrated_buffer_awaits_receipt(observation):
        return None
    from simulator.battery.oxygen_identity import _resolve_score_time_buffer_identity

    resolved = _resolve_score_time_buffer_identity(
        observation,
        ambient_pressure_evidence=(
            None if experiment is None else experiment.ambient_pressure_evidence
        ),
    )
    pressure_pa = stored_absolute_pressure_pa(
        resolved.identity if isinstance(resolved.identity, Identity) else None
    )
    new_notices = tuple(
        notice
        for notice in resolved.notices
        if notice not in observation.notices
    )
    if pressure_pa is None or not any(
        notice.policy is not None and notice.policy.id == "DP-112"
        for notice in new_notices
    ):
        return None
    return resolved, AdmittedOxygen(
        pressure_pa=pressure_pa,
        log10_bar=decimal_log10_bar(pressure_pa),
        tier=tier,
        route="score:DP-112",
        borrowed=False,
        notices=new_notices,
        physics_rung=physics_rung,
    )


def _receipted_buffer(observation: Observation | None) -> bool:
    """True when DP-112 has already receipted this row's buffer."""

    if observation is None:
        return False
    from simulator.battery.oxygen_identity import _dp112_receipts

    return bool(_dp112_receipts(observation))


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
                and (
                    uncalibrated_buffer_awaits_receipt(observation)
                    or _receipted_buffer(observation)
                )
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
    prediction_pressure: PredictionPressure | None = None,
) -> ResolvedFo2 | None:
    if temperature_K is None:
        return None
    fraction, source = _printed_ferric_fraction(observation, weights, experiment)
    if fraction is None:
        return None
    pressure_bar, pressure_route = _kress_pressure_bar(
        experiment, bench, observation, identity, prediction_pressure
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
        _kress91_log10_fO2_from_fe3_over_sigma_fe,
        kress91_temperature_band_case,
    )

    temperature_band = kress91_temperature_band_case(
        temperature_K - 273.15
    )
    log = _kress91_log10_fO2_from_fe3_over_sigma_fe(
        fe3_over_sigma_fe=fraction,
        mol_fractions=mol_fractions,
        T_K=temperature_K,
        pressure_bar=pressure_bar,
    )
    if not math.isfinite(log):
        return None
    return ResolvedFo2(
        log,
        2,
        f"kress_inverse:{source};pressure={pressure_route}",
        False,
        temperature_band_status=str(temperature_band["status"]),
        temperature_band_source=str(temperature_band["source"]),
    )


def _printed_ferric_fraction(
    observation: Observation | None,
    weights: dict[str, float] | None,
    experiment: Experiment | None = None,
) -> tuple[float | None, str]:
    """Printed ferric evidence only.

    ``weights`` is the row's bulk composition. A solver map or an FeOt
    conversion is not a reported Fe3+/sumFe, so it is not read here.
    A located FeO plus Fe2O3 pair is that evidence only with wet-chemical
    or Mössbauer provenance. Printed Fe3+/sumFe and Fe3+/Fe2+ stay
    method-independent.
    """

    del weights
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
    from simulator.battery.migrate import fe3_fe2_ratio_from_oxide_wt_pct

    feo = _printed_number(observation, KRESS_FEO_WT_PCT_FIELD)
    fe2o3 = _printed_number(observation, KRESS_FE2O3_WT_PCT_FIELD)
    if feo is None or fe2o3 is None or feo <= 0.0 or fe2o3 <= 0.0:
        return None, ""
    conditions = observation.point_conditions or {}
    locators = (
        conditions[KRESS_FEO_WT_PCT_FIELD].locator,
        conditions[KRESS_FE2O3_WT_PCT_FIELD].locator,
    )
    if any(locator is None for locator in locators):
        return None, ""
    analytical_method = (
        experiment is not None
        and experiment.method.is_value
        and experiment.method.value in {
            MethodToken.WET_CHEMICAL_ANALYSIS,
            MethodToken.MOSSBAUER_SPECTROSCOPY,
        }
    )
    column_provenance = " ".join(
        field
        for locator in locators
        for field in (locator.note, locator.table)
        if field
    ).casefold()
    if not analytical_method and not any(
        marker in column_provenance
        for marker in ("wet chemical", "wet-chemical", "mossbauer", "mössbauer")
    ):
        return None, ""
    ratio = fe3_fe2_ratio_from_oxide_wt_pct(Decimal(str(fe2o3)), Decimal(str(feo)))
    if ratio is None or ratio <= 0:
        return None, ""
    fraction = float(ratio / (Decimal(1) + ratio))
    if not 0.0 < fraction < 1.0:
        return None, ""
    return fraction, f"{KRESS_FEO_WT_PCT_FIELD}+{KRESS_FE2O3_WT_PCT_FIELD}"


def _kress_pressure_bar(
    experiment: Experiment | None,
    bench: object | None,
    observation: Observation | None,
    identity: Identity | None,
    prediction_pressure: PredictionPressure | None = None,
) -> tuple[float | None, str]:
    if prediction_pressure is not None:
        pascal = nominal_pressure_pa(prediction_pressure.value)
        if pascal is not None and pascal.is_finite() and pascal > 0:
            return float(pascal) / PA_PER_BAR, prediction_pressure.route
        return None, prediction_pressure.route
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
    if _is_knudsen(experiment) or _is_vacuum_fO2_method(experiment):
        return None, "pressure_term_omitted; pressure_bar=None"
    policy_pressure = Decimal(str(resolve("DP-001").value))
    return float(policy_pressure) / PA_PER_BAR, "DP-001_assumed_pressure"


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


def _is_vacuum_fO2_method(experiment: Experiment | None) -> bool:
    return _is_knudsen(experiment) or _is_vacuum_pressure_method(experiment)


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


def _with_exact_pascals(resolved: ResolvedFo2) -> ResolvedFo2:
    """Attach the owner's pascals once. Closures and sealed routes stay as they are.

    A route that already names a bar keeps that bar as the pascal source.
    A log-defined route converts once through ``log10_fo2_to_pa``. Callers
    command ``pressure_pa`` and do not exponentiate the float log again.
    """

    if resolved.closure or resolved.pressure_pa is not None:
        return resolved
    if not math.isfinite(resolved.log10_fO2_bar):
        return resolved
    from simulator.battery.oxygen_identity import log10_fo2_to_pa

    if (
        resolved.po2_bar is not None
        and math.isfinite(resolved.po2_bar)
        and resolved.po2_bar > 0.0
    ):
        pressure_pa = Decimal(str(resolved.po2_bar)) * BAR_PA_DECIMAL
    else:
        pressure_pa = log10_fo2_to_pa(Decimal(str(resolved.log10_fO2_bar)))
    if not pressure_pa.is_finite() or pressure_pa <= 0:
        return resolved
    return replace(resolved, pressure_pa=pressure_pa)


def printed_total_pressure_pa(
    identity: Identity | None, observation: Observation | None
) -> Decimal | None:
    """Located total pressure in pascals, from the identity or the row."""

    if identity is not None and identity.total_pressure_Pa is not None:
        state = identity.total_pressure_Pa
        if state.is_value and state.value is not None:
            pa = nominal_pressure_pa(state)
            if pa is not None and pa.is_finite() and pa > 0:
                return pa
    if observation is None:
        return None
    located = (observation.point_conditions or {}).get("total_pressure_Pa")
    if located is None or located.inference is not None or not located.state.is_value:
        return None
    pa = nominal_pressure_pa(located)
    if pa is None or not pa.is_finite() or pa <= 0:
        return None
    return pa


def _printed_log_text(observation: Observation | None) -> str | None:
    if observation is None:
        return None
    located = (observation.point_conditions or {}).get("fO2_log")
    if located is None or not located.state.is_value:
        return None
    raw = located.state.value
    if isinstance(raw, Value):
        if raw.kind is not ValueKind.POINT or raw.point is None:
            return None
        raw = raw.point
    if isinstance(raw, Decimal) and raw.is_finite():
        return format(raw, "f")
    return None


def _air_oxygen_decision(
    experiment: Experiment | None,
    observation: Observation | None,
    identity: Identity | None,
    quantity: Quantity,
) -> ResolvedFo2 | None:
    """DP-003 when the sample was equilibrated in air and no earlier route fired.

    The notice text is the phase path's receipt. Total pressure is the printed
    value, or DP-002 when that registered default applies. The oxygen pascal
    is the policy result, not a second evaluation of the mole fraction.
    """

    if observation is None:
        return None
    from simulator.battery.default_policy import resolve as resolve_default_policy
    from simulator.battery.oxygen_identity import (
        _air_composition_quote,
        _dp002_pressure_default,
    )
    from simulator.battery.records import NoticePolicy

    atmospheric = None if experiment is None else experiment.ambient_pressure_evidence
    atmosphere_condition = (observation.point_conditions or {}).get("atmosphere")
    air_quote = _air_composition_quote(
        atmospheric, atmosphere_condition=atmosphere_condition
    )
    if air_quote is None:
        return None
    pressure_pa = printed_total_pressure_pa(identity, observation)
    pressure_notices: tuple[Notice, ...] = ()
    if pressure_pa is None:
        pressure_pa, pressure_notice = _dp002_pressure_default(
            atmospheric,
            quantity,
            atmosphere_condition=atmosphere_condition,
        )
        if pressure_notice is not None:
            pressure_notices = (pressure_notice,)
    if pressure_pa is None:
        return None
    # The phase receipt prints the bar round trip, and DP-003 consumes that
    # same total. Keep both so the notice text and the oxygen pascal stay
    # the values the phase path already produced.
    total_pressure_pa = (pressure_pa / BAR_PA_DECIMAL) * BAR_PA_DECIMAL
    policy = resolve_default_policy("DP-003", P_total=total_pressure_pa)
    oxygen_pa = as_decimal(policy.value)
    if not oxygen_pa.is_finite() or oxygen_pa <= 0:
        return None
    oxygen_notice = Notice(
        kind=policy.flag,
        affected_quantities=(quantity,),
        reason=(
            "DP-003 physics_rung=3: source says the experiment was "
            f"in air; quote={air_quote!r}; "
            f"P_total={total_pressure_pa} Pa"
        ),
        origin="score:DP-003",
        policy=NoticePolicy(**policy.payload["policy"]),
    )
    return ResolvedFo2(
        float(decimal_log10_bar(oxygen_pa)),
        3,
        "score:DP-003",
        False,
        po2_bar=float(oxygen_pa / BAR_PA_DECIMAL),
        physics_rung=3,
        pressure_pa=oxygen_pa,
        notices=(*pressure_notices, oxygen_notice),
    )
