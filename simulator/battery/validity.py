"""Validity gates (v2.1 §Validity gates).

Run after referential/schema validation, before equality. Each gate returns
a typed outcome; a failure is a refused Residual reason, never a deleted
Observation. All failed checks are recorded; the first is the primary reason.

Ambiguity resolutions:
- Table self-consistency uses ``log10K_from_delta_fG_kJ_mol``
  (−ΔfG/(R T ln 10)) at matching reaction/per/p°. The finding floor is
  0.1 dex, from ``physical_constants.TABLE_SELF_CHECK_FINDING_DEX``
  (JANAF printed-precision grain). A consistent
  O2 identity (ΔfG=0, log10 Kf=0) passes. The gate does not score engines.
- Effusion Kn threshold is ``FREE_MOLECULAR_KNUDSEN_MIN`` (10) from
  transport_constants. Kn is the *orifice* (cell-local) number, not chamber
  pressure masquerading as cell pressure. When Kn is absent, grounded KEMS
  calibration and a complete same-point printed in-cell pressure sum can
  verify the regime; unstated chamber background is checked separately.
- Background ≥ 1e-2 Pa fails KEMS *equilibrium* pressure/activity only.
  Millibar bench kinetic experiments are out of this gate's scope.
- Apparatus determinants are those the actual derivation needs: calibrated
  KEMS p_partial/p_sat requires a grounded calibration but no orifice
  geometry; absolute-flux effusion and Langmuir pressure/alpha require their
  respective geometry. Determinants must be grounded VALUES (unknown
  calibration fails), physically valid (area > 0, Clausing in (0, 1]), and
  present for TGA/solar/vacuum kinetic area. A printed positive orifice
  diameter interval (locator, no inference) is usable for calibrated KEMS
  pressure (d-326 case 1: no value band; the coded p/d regime check runs
  at both ends) and for a raw-rate Knudsen reduction (case 2: geometric-mean
  diameter and a dex band). An inferred or unlocated interval is not.
  Activity, activity coefficient, log10 Kf, and reference pressure still
  require a point orifice. An area interval is not a point area.
  Missing required geometry is ``underdetermined_apparatus``. Unknown
  method is ``method_unknown`` when the quantity class is one the schema
  scopes by method (effusion pressure, Langmuir pressure/alpha,
  kinetic/yield); thermochemistry at a stated T/reference state is not
  that class and does not borrow the apparatus token. Archival
  storage of incomplete apparatus is allowed; the gate fails the
  comparison, not the record.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from simulator.battery.enums import (
    AdmissionStatus,
    BoundDirection,
    BoundInclusivity,
    EvidenceClass,
    MEASURED_EVIDENCE,
    MethodToken,
    NoticeKind,
    Quantity,
    RefusalReason,
    StateTag,
    ValueKind,
)
from simulator.battery.identity import (
    Identity,
    log10K_from_delta_fG_kJ_mol,
    quantity_token,
)
from simulator.battery.records import (
    ApparatusGeometry,
    INITIAL_CHARGE_ONLY_PROXY_FLAG,
    Composition,
    Experiment,
    Located,
    Notice,
    Observation,
    State,
    Value,
    _bound_operator_semantics,
    as_decimal,
)
from simulator.reference_data.janaf import formula_composition
from simulator.physical_constants import TABLE_SELF_CHECK_FINDING_DEX
from simulator.transport_constants import FREE_MOLECULAR_KNUDSEN_MIN

KEMS_GEOMETRY_FACTS = ("orifice_area_m2", "clausing_factor")

# KEMS equilibrium background ceiling (Pa). This remains a separate signal
# quality check; it does not determine the in-cell effusion regime.
KEMS_BACKGROUND_HIGH_PA = Decimal("1e-2")

# Drowart et al. state that p/d <= 1 Pa/mm meets the mean-free-path criterion
# in practice, and that about 10 Pa is the usual KEMS cell-pressure ceiling
# (2005, p. 689). Use only these sourced limits; do not infer a kinetic limit
# from an unsourced collision diameter. Without a located diameter, use the
# ~10 Pa ceiling alone.
KEMS_CELL_PRESSURE_MAX_PA = Decimal("10")

_CALIBRATION_STANDARD_FIELDS = frozenset(
    {
        "reference_substance",
        "reference_substances",
        "calibration_substance",
        "pressure_reference_substance",
        "pressure_standard_substance",
        "standard",
    }
)


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    detail: dict[str, Any]


@dataclass(frozen=True)
class GateOutcome:
    passed: bool
    reason: RefusalReason | None = None
    checks: tuple[GateCheck, ...] = ()
    primary_check: str | None = None

    @property
    def refusal_token(self) -> RefusalReason | None:
        return None if self.passed else self.reason


def _fail(reason: RefusalReason, checks: list[GateCheck], primary: str) -> GateOutcome:
    return GateOutcome(
        passed=False,
        reason=reason,
        checks=tuple(checks),
        primary_check=primary,
    )


def _effusion_policy_outcome(
    checks: list[GateCheck], flag: str, *, reason: str
) -> GateOutcome:
    """Keep a numeric KEMS comparison and record its regime policy flag."""
    return GateOutcome(
        passed=True,
        checks=(
            *checks,
            GateCheck("effusion_regime_policy", True, {"flag": flag, "reason": reason}),
        ),
    )


def _pass(checks: list[GateCheck]) -> GateOutcome:
    return GateOutcome(passed=True, checks=tuple(checks))


def _located_decimal(located: Located[Value | Decimal] | None) -> Decimal | None:
    if located is None or not located.state.is_value or located.state.value is None:
        return None
    value = located.state.value
    if isinstance(value, Value):
        if value.kind is not ValueKind.POINT or value.point is None:
            return None
        return value.point
    return as_decimal(value)


def _pressure_bounds(
    located: Located[Value | Decimal] | None,
) -> tuple[Decimal | None, Decimal | None, str] | None:
    """Return inclusive lower/upper bounds without collapsing a range."""

    if located is None or not located.state.is_value or located.state.value is None:
        return None
    value = located.state.value
    if not isinstance(value, Value):
        point = as_decimal(value)
        return point, point, "point"
    if value.kind is ValueKind.POINT and value.point is not None:
        return value.point, value.point, "point"
    if value.kind is ValueKind.INTERVAL:
        if value.interval_low is None or value.interval_high is None:
            return None
        low = value.interval_low
        high = value.interval_high
        if low > high:
            return None
        return low, high, "interval"
    if value.kind is ValueKind.BOUND and value.bound_value is not None:
        bound = value.bound_value
        if value.bound_operator in {"<", "<=", "≤"}:
            return None, bound, "upper_bound"
        if value.bound_operator in {">", ">=", "≥"}:
            return bound, None, "lower_bound"
    return None


def _positive_decimal(value: Decimal | None) -> bool:
    return value is not None and value.is_finite() and value > 0


def _finite_positive(located: Located[Value | Decimal] | None) -> Decimal | None:
    value = _located_decimal(located)
    return value if _positive_decimal(value) else None


_CLAUSING_FACTOR_MAX = Decimal("1")


def _clausing_value_ok(value: Decimal | None) -> bool:
    return (
        _positive_decimal(value)
        and value is not None
        and value <= _CLAUSING_FACTOR_MAX
    )


def _clausing_ok(located: Located[Value | Decimal] | None) -> bool:
    return _clausing_value_ok(_located_decimal(located))


def _positive_extent(
    located: Located[Value | Decimal] | None,
) -> tuple[Decimal, Decimal, str] | None:
    """Positive finite point or closed interval.

    A bound is not an extent: one end is open. A non-positive end, a
    non-finite end, or an inverted interval is not usable geometry.
    """

    bounds = _pressure_bounds(located)
    if bounds is None:
        return None
    low, high, kind = bounds
    if kind not in {"point", "interval"} or low is None or high is None:
        return None
    if not low.is_finite() or not high.is_finite():
        return None
    if low <= 0 or high <= 0 or low > high:
        return None
    return low, high, kind


def _printed_positive_diameter_interval(
    located: Located[Value | Decimal] | None,
) -> bool:
    """Printed positive closed orifice-diameter interval (d-326).

    Printed means a source locator and no inference. A point, a bound,
    and an inferred or unlocated interval are not this interval. The same
    locator test is inline in ``_printed_diameter_extent``; it stays there
    so this fix does not move that helper.
    """

    if (
        located is None
        or located.inference is not None
        or located.locator is None
        or not located.locator.has_location()
    ):
        return False
    extent = _positive_extent(located)
    return extent is not None and extent[2] == "interval"


def _impossible_kems_geometry_value(
    located: Located[Value | Decimal] | None,
    *,
    clausing_factor: bool = False,
) -> bool:
    # Refuse only when every admissible value violates the physical domain.
    # A straddling interval or bound remains missing/unverified because it
    # still admits a physically possible value.
    if located is None or not located.state.is_value:
        return False
    raw = located.state.value
    if raw is None:
        return False
    if not isinstance(raw, Value):
        if clausing_factor:
            return not _clausing_value_ok(raw)
        return not _positive_decimal(raw)
    if raw.kind is ValueKind.POINT:
        return not (
            _clausing_value_ok(raw.point)
            if clausing_factor
            else _positive_decimal(raw.point)
        )
    if raw.kind is ValueKind.INTERVAL:
        low = raw.interval_low
        high = raw.interval_high
        if low is None or high is None or low > high:
            return True
        bounds = _pressure_bounds(located)
        if bounds is None:
            return True
        low, high, kind = bounds
        if kind != "interval" or low is None or high is None:
            return True
        if not _positive_decimal(high):
            return True
        if not clausing_factor:
            return False
        extent = _positive_extent(located)
        return extent is not None and low > _CLAUSING_FACTOR_MAX
    if raw.kind is ValueKind.BOUND:
        bound = raw.bound_value
        if bound is None:
            return True
        operator_semantics = _bound_operator_semantics(raw.bound_operator)
        direction = raw.bound_direction
        inclusivity = raw.bound_inclusivity
        if direction is None and operator_semantics is not None:
            direction, inclusivity = operator_semantics
        if direction is None or inclusivity is None:
            return True
        if direction is BoundDirection.UPPER:
            return not _positive_decimal(bound)
        if not clausing_factor:
            return False
        return bound > _CLAUSING_FACTOR_MAX or (
            bound == _CLAUSING_FACTOR_MAX
            and inclusivity is BoundInclusivity.EXCLUDED
        )
    # Geometry extracts use POINT, INTERVAL, and legacy scalar values; BOUND
    # is also supported by the parser and handled above. Other Value kinds do
    # not occur on these fields and are not inferred as numeric ranges here.
    return False


def _invalid_kems_geometry_fields(
    geometry: ApparatusGeometry | None,
) -> tuple[str, ...]:
    if geometry is None:
        return ()
    invalid: list[str] = []
    area = geometry.orifice_area_m2
    if _impossible_kems_geometry_value(area):
        invalid.append("orifice_area_m2")
    diameter = geometry.orifice_diameter_m
    if _impossible_kems_geometry_value(diameter):
        invalid.append("orifice_diameter_m")
    clausing = geometry.clausing_factor
    if _impossible_kems_geometry_value(clausing, clausing_factor=True):
        invalid.append("clausing_factor")
    return tuple(invalid)


def _calibration_entries_for_observation(
    calibration: object,
    *,
    observation: Observation | None,
    experiment_id: str | None,
) -> Mapping[str, object] | None:
    """Resolve scoped apparatus fields to the linked run and gas channel.

    ``field@all_species`` is the shared fallback; ``field@species=<formula>``
    applies to one identity channel and takes precedence, including when that
    channel explicitly records an unknown.
    """

    if not isinstance(calibration, Mapping):
        return None
    scoped = any(isinstance(key, str) and "@" in key for key in calibration)
    if not scoped:
        return None
    if (
        observation is None
        or not experiment_id
        or observation.experiment_id != experiment_id
        or not isinstance(observation.identity, Identity)
        or observation.identity.species is None
    ):
        return {}

    channel_scope = f"species={observation.identity.species.formula}"
    fields = {
        key.rsplit("@", 1)[0] if "@" in key else key
        for key in calibration
        if isinstance(key, str)
    }
    resolved: dict[str, object] = {}
    for field in fields:
        channel_key = f"{field}@{channel_scope}"
        shared_key = f"{field}@all_species"
        if channel_key in calibration:
            resolved[field] = calibration[channel_key]
        elif shared_key in calibration:
            resolved[field] = calibration[shared_key]
        elif field in calibration:
            resolved[field] = calibration[field]
    return resolved


def _calibration_grounded(
    calibration: object,
    *,
    observation: Observation | None = None,
    experiment_id: str | None = None,
) -> bool:
    if not calibration or not isinstance(calibration, Mapping):
        return False
    scoped_entries = _calibration_entries_for_observation(
        calibration, observation=observation, experiment_id=experiment_id
    )
    if scoped_entries is not None:
        if not scoped_entries:
            return False
        standards = [
            located
            for field, located in scoped_entries.items()
            if field in _CALIBRATION_STANDARD_FIELDS
        ]
        if not any(
            isinstance(located, Located)
            and isinstance(located.state, State)
            and located.state.is_value
            and located.state.value is not None
            for located in standards
        ):
            return False
        for located in scoped_entries.values():
            if (
                not isinstance(located, Located)
                or not isinstance(located.state, State)
                or located.locator is None
                or not located.locator.has_location()
                or located.inference is not None
                or not (located.state.is_value or located.state.is_unknown)
            ):
                return False
        return True

    for located in calibration.values():
        if not isinstance(located, Located):
            return False
        if (
            not located.state.is_value
            or located.state.value is None
            or located.locator is None
            or not located.locator.has_location()
            or located.inference is not None
        ):
            return False
    return True


def _calibration_not_grounded_detail(
    calibration: object,
    *,
    observation: Observation | None = None,
    experiment_id: str | None = None,
) -> dict[str, str] | None:
    if calibration is None or (isinstance(calibration, dict) and not calibration):
        return {
            "flag": "calibration_not_grounded",
            "calibration_record_status": "not_recorded",
            "calibration_notice": "No calibration entry has been recorded yet.",
        }
    scoped_entries = _calibration_entries_for_observation(
        calibration, observation=observation, experiment_id=experiment_id
    )
    if scoped_entries is not None:
        if not scoped_entries:
            return {
                "flag": "calibration_not_grounded",
                "calibration_record_status": "not_recorded",
                "calibration_notice": "No calibration entry has been recorded yet.",
            }
        calibration = scoped_entries
    if isinstance(calibration, Mapping):
        printed_standard = any(
            field in _CALIBRATION_STANDARD_FIELDS
            and isinstance(located, Located)
            and located.state.is_value
            for field, located in calibration.items()
        )
        for field, located in calibration.items():
            if not isinstance(located, Located) or located.state.is_value:
                continue
            reason = (located.state.reason or "").casefold()
            if "not printed" in reason:
                record_status = "not_printed"
                notice = "The source record says the calibration was not printed."
            elif "not_published" in reason:
                if field not in _CALIBRATION_STANDARD_FIELDS and printed_standard:
                    continue
                record_status = "not_published"
                notice = "The source record says the calibration standard was not published."
            else:
                continue
            return {
                "flag": "calibration_not_grounded",
                "calibration_record_status": record_status,
                "calibration_notice": notice,
            }
    return None


def table_self_consistency(
    *,
    delta_fG_kJ_mol: object,
    log10_Kf: object,
    T_K: object,
    per: object | None = None,
    standard_pressure_Pa: object | None = None,
    reaction_id: str | None = None,
    printed_log10_Kf: str | None = None,
) -> GateOutcome:
    """Existing ΔfG ↔ logK check. Inconsistent printed pair → invalid_source.

    Both printed values are retained on the Observation; this gate only
    refuses the comparison. Matching reaction/per/p° is the caller's
    identity; this function assumes they already match.
    """

    recomputed = log10K_from_delta_fG_kJ_mol(delta_fG_kJ_mol, T_K)
    printed = as_decimal(log10_Kf)
    residual = printed - recomputed
    abs_residual = abs(residual)
    check = GateCheck(
        name="delta_fG_logK",
        passed=abs_residual <= TABLE_SELF_CHECK_FINDING_DEX,
        detail={
            "delta_fG_kJ_mol": str(as_decimal(delta_fG_kJ_mol)),
            "log10_Kf": str(printed),
            "recomputed_log10_Kf": str(recomputed),
            "residual_dex": str(residual),
            "finding_floor_dex": str(TABLE_SELF_CHECK_FINDING_DEX),
            "T_K": str(as_decimal(T_K)),
            "per": None if per is None else str(per),
            "standard_pressure_Pa": None
            if standard_pressure_Pa is None
            else str(as_decimal(standard_pressure_Pa)),
            "reaction_id": reaction_id,
            "printed_log10_Kf": printed_log10_Kf,
        },
    )
    if check.passed:
        return _pass([check])
    return _fail(RefusalReason.INVALID_SOURCE, [check], "delta_fG_logK")


def _is_effusion_pressure(method: MethodToken, quantity: Quantity) -> bool:
    return method is MethodToken.KNUDSEN_EFFUSION and quantity in {
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
        Quantity.P_REFERENCE,
        Quantity.ACTIVITY,
        Quantity.ACTIVITY_COEFFICIENT,
        Quantity.LOG10_KF,
    }


def _is_kems_calibrated_pressure(method: MethodToken, quantity: Quantity) -> bool:
    return method is MethodToken.KNUDSEN_EFFUSION and quantity in {
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
    }


def _is_langmuir_pressure_or_alpha(method: MethodToken, quantity: Quantity) -> bool:
    return method is MethodToken.LANGMUIR_FREE_EVAPORATION and quantity in {
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
        Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        Quantity.EVAPORATION_RATE,
        Quantity.MASS_LOSS_RATE,
    }


def _is_kinetic_or_yield(quantity: Quantity) -> bool:
    return quantity in {
        Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        Quantity.EVAPORATION_RATE,
        Quantity.MASS_LOSS_RATE,
        Quantity.MASS_LOSS_FRACTION,
        Quantity.MASS_LOSS_FRACTION_VS_T,
        Quantity.YIELD_FRACTION,
        Quantity.EVOLVED_GAS_YIELD,
        Quantity.O2_YIELD,
        Quantity.CONDENSATE_COMPOSITION,
        Quantity.WALL_DEPOSIT_MASS,
    }


COMPARISON_RATIO_KINDS = frozenset({"ratio", "comparison_ratio"})


def effective_melt_reference_pairing_kind(
    pairing_kind: Any, *, explicitly_same: bool = False
) -> Any:
    if pairing_kind in {None, "not_printed", "not_reported", "unknown"}:
        return "same_cell" if explicitly_same else "same_effective_setup_assumed"
    return pairing_kind


def comparison_method_cell_constant_cancels(
    provenance: Mapping[str, Any] | None,
) -> bool:
    """Return true for comparison ratios unless separate cells are recorded."""

    if not isinstance(provenance, Mapping):
        return False
    method = provenance.get("comparison_method")
    cell_constant = provenance.get("common_knudsen_cell_constant")
    pairing = provenance.get("melt_reference_pairing")
    pairing_kind = pairing.get("kind") if isinstance(pairing, Mapping) else None
    pairing_kind = effective_melt_reference_pairing_kind(pairing_kind)
    return (
        isinstance(method, Mapping)
        and method.get("kind") in COMPARISON_RATIO_KINDS
        and isinstance(cell_constant, Mapping)
        and cell_constant.get("cancels") is True
        and pairing_kind
        in {"same_cell", "same_effective_setup", "same_effective_setup_assumed"}
    )


# Quantity classes the schema scopes by method. Unknown method on these
# cannot decide which geometry packet applies; it is not an orifice
# failure. Thermochemistry is intentionally absent.
_METHOD_SCOPED_APPARATUS_QUANTITIES = frozenset(
    {
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
        Quantity.P_REFERENCE,
        Quantity.ACTIVITY,
        Quantity.ACTIVITY_COEFFICIENT,
        Quantity.LOG10_KF,
        Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        Quantity.EVAPORATION_RATE,
        Quantity.MASS_LOSS_RATE,
        Quantity.MASS_LOSS_FRACTION,
        Quantity.MASS_LOSS_FRACTION_VS_T,
        Quantity.YIELD_FRACTION,
        Quantity.EVOLVED_GAS_YIELD,
        Quantity.O2_YIELD,
        Quantity.CONDENSATE_COMPOSITION,
        Quantity.WALL_DEPOSIT_MASS,
    }
)


def _quantity_scopes_apparatus_by_method(quantity: Quantity) -> bool:
    return quantity in _METHOD_SCOPED_APPARATUS_QUANTITIES


def underdetermined_apparatus(
    experiment: Experiment,
    quantity: Quantity,
    *,
    observation: Observation | None = None,
) -> GateOutcome:
    """Pressure/flux conversion requires the method's geometry determinants."""

    method_state = experiment.method
    checks: list[GateCheck] = []
    if method_state.tag is not StateTag.VALUE or method_state.value is None:
        if _quantity_scopes_apparatus_by_method(quantity):
            checks.append(
                GateCheck(
                    "method",
                    False,
                    {
                        "reason": method_state.reason or "method unknown",
                        "quantity": quantity.value,
                    },
                )
            )
            return _fail(RefusalReason.METHOD_UNKNOWN, checks, "method")
        checks.append(
            GateCheck(
                "method",
                True,
                {
                    "reason": "method not required for this quantity class",
                    "quantity": quantity.value,
                },
            )
        )
        return _pass(checks)
    method = method_state.value
    geometry = None if experiment.apparatus is None else experiment.apparatus.geometry
    missing: list[str] = []
    invalid: tuple[str, ...] = ()
    if _is_kems_calibrated_pressure(method, quantity):
        # Knudsen-effusion mass spectrometry uses the calibrated pressure
        # relation P_i = k_i I_i^+ T.  k_i comes from a calibration (for
        # example, a reference vaporization such as Ag, or a weight-loss
        # calibration), so this route needs neither orifice area nor a
        # Clausing factor. Those geometry terms enter only the absolute-flux
        # Hertz-Knudsen weight-loss route:
        # p = (dm/dt)/(A W) * sqrt(2 pi R T / M).
        calibration = None if experiment.apparatus is None else experiment.apparatus.calibration
        calibration_observation = (
            observation if quantity is Quantity.P_PARTIAL else None
        )
        calibrated = _calibration_grounded(
            calibration,
            observation=calibration_observation,
            experiment_id=experiment.experiment_id,
        )
        ungrounded_detail = (
            None
            if calibrated
            else _calibration_not_grounded_detail(
                calibration,
                observation=calibration_observation,
                experiment_id=experiment.experiment_id,
            )
        )
        calibration_flagged = (
            quantity
            in {
                Quantity.P_SAT,
                Quantity.P_PARTIAL,
                Quantity.P_REFERENCE,
            }
            and ungrounded_detail is not None
        )
        checks.append(
            GateCheck(
                "kems_calibration",
                calibrated or calibration_flagged,
                {
                    "missing": [] if calibrated else ["calibration"],
                    "method": method.value,
                    "quantity": quantity.value,
                    "reason": None
                    if calibrated
                    else "KEMS pressure requires a recorded calibration",
                    **(ungrounded_detail if calibration_flagged else {}),
                },
            )
        )
        if not calibrated and not calibration_flagged:
            return _fail(RefusalReason.UNDERDETERMINED_APPARATUS, checks, "kems_calibration")
        # A calibrated KEMS pressure does not need geometry when it is absent.
        # A supplied point must be positive. A supplied diameter interval is
        # usable only when it is printed: area does not enter p = k I T, so
        # that interval is not a value band (d-326 case 1). An inferred or
        # unlocated interval, a bound, and an area interval stay missing.
        invalid = _invalid_kems_geometry_fields(geometry)
        if geometry is not None:
            if (
                geometry.orifice_area_m2 is not None
                and geometry.orifice_area_m2.state.is_value
                and _finite_positive(geometry.orifice_area_m2) is None
                and "orifice_area_m2" not in invalid
            ):
                missing.append("orifice_area_m2")
            if (
                geometry.orifice_diameter_m is not None
                and geometry.orifice_diameter_m.state.is_value
                and _finite_positive(geometry.orifice_diameter_m) is None
                and not _printed_positive_diameter_interval(
                    geometry.orifice_diameter_m
                )
                and "orifice_diameter_m" not in invalid
            ):
                missing.append("orifice_diameter_m")
            if (
                geometry.clausing_factor is not None
                and geometry.clausing_factor.state.is_value
                and not _clausing_ok(geometry.clausing_factor)
                and "clausing_factor" not in invalid
            ):
                missing.append("clausing_factor")
    elif _is_effusion_pressure(method, quantity):
        invalid = _invalid_kems_geometry_fields(geometry)
        provenance = observation.provenance if observation is not None else None
        method_provenance = (
            provenance.get("comparison_method")
            if isinstance(provenance, Mapping)
            else None
        )
        pairing = (
            provenance.get("melt_reference_pairing")
            if isinstance(provenance, Mapping)
            else None
        )
        different_comparison_setup = (
            quantity in {Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT}
            and isinstance(method_provenance, Mapping)
            and method_provenance.get("kind") in COMPARISON_RATIO_KINDS
            and isinstance(pairing, Mapping)
            and pairing.get("kind") == "different_cells_or_geometry"
        )
        comparison_activity = (
            quantity is Quantity.ACTIVITY
            and observation is not None
            and comparison_method_cell_constant_cancels(observation.provenance)
        )
        if different_comparison_setup:
            checks.append(
                GateCheck(
                    "comparison_method_cell_constant_cancels",
                    False,
                    {
                        "method": "comparison_ratio",
                        "pairing": "different_cells_or_geometry",
                        "reason": "sample and standard use different cells or geometries",
                    },
                )
            )
            missing.append("comparison_pairing_different_cells_or_geometry")
        elif comparison_activity:
            # For same-instrument sample/pure-oxide current ratios, a_i ~ I_i/I_i°:
            # area, Clausing factor, and sensitivity cancel to first order. Effusion
            # still applies; explicit different-cell/geometry pairing disables this.
            pairing = observation.provenance.get("melt_reference_pairing")
            pairing_kind = (
                pairing.get("kind")
                if isinstance(pairing, Mapping)
                else effective_melt_reference_pairing_kind(None)
            )
            checks.append(
                GateCheck(
                    "comparison_method_cell_constant_cancels",
                    True,
                    {
                        "method": "comparison_ratio",
                        "pairing": pairing_kind,
                        "geometry": "not_required_for_normalized_activity",
                    },
                )
            )
        elif geometry is None:
            missing.extend(KEMS_GEOMETRY_FACTS)
        else:
            if (
                _finite_positive(geometry.orifice_area_m2) is None
                and _finite_positive(geometry.orifice_diameter_m) is None
                and "orifice_area_m2" not in invalid
            ):
                missing.append("orifice_area_m2")
            if (
                not _clausing_ok(geometry.clausing_factor)
                and "clausing_factor" not in invalid
            ):
                missing.append("clausing_factor")
        calibration = None if experiment.apparatus is None else experiment.apparatus.calibration
        calibrated = _calibration_grounded(calibration)
        activity_quantity = quantity in {
            Quantity.ACTIVITY,
            Quantity.ACTIVITY_COEFFICIENT,
        }
        ungrounded_detail = (
            None if calibrated else _calibration_not_grounded_detail(calibration)
        )
        calibration_flagged = (
            activity_quantity and ungrounded_detail is not None
        )
        if activity_quantity:
            checks.append(
                GateCheck(
                    "kems_calibration",
                    calibrated or calibration_flagged,
                    {
                        "missing": [] if calibrated else ["calibration"],
                        "method": method.value,
                        "quantity": quantity.value,
                        "reason": None
                        if calibrated
                        else "KEMS activity requires a recorded calibration",
                        **(ungrounded_detail if calibration_flagged else {}),
                    },
                )
            )
        if not calibrated and not calibration_flagged:
            missing.append("calibration")
    if _is_langmuir_pressure_or_alpha(method, quantity):
        if geometry is None or _finite_positive(geometry.exposed_area_m2) is None:
            missing.append("exposed_area_m2")
    if _is_kinetic_or_yield(quantity) and method in {
        MethodToken.KNUDSEN_EFFUSION,
        MethodToken.LANGMUIR_FREE_EVAPORATION,
        MethodToken.TGA,
        MethodToken.SOLAR_FURNACE_PYROLYSIS,
        MethodToken.VACUUM_CHAMBER_PYROLYSIS,
    }:
        if quantity is Quantity.WALL_DEPOSIT_MASS:
            wall = None if experiment.apparatus is None else experiment.apparatus.wall
            if not wall or "temperature_K" not in wall or "material" not in wall:
                missing.append("wall.temperature_K/material")
        if method is MethodToken.KNUDSEN_EFFUSION:
            # Case 2 (d-326): a printed diameter interval is the orifice.
            # The reduction evaluates d_gm and carries the dex band. A point
            # area still satisfies the determinant. A point diameter does
            # not, and neither does an inferred or unlocated interval.
            printed_diameter_interval = geometry is not None and (
                _printed_positive_diameter_interval(geometry.orifice_diameter_m)
            )
            point_area = (
                geometry is not None
                and _finite_positive(geometry.orifice_area_m2) is not None
            )
            if (
                not printed_diameter_interval
                and not point_area
                and "orifice_area_m2" not in missing
            ):
                missing.append("orifice_area_m2")
        elif geometry is None or (
            _finite_positive(geometry.exposed_area_m2) is None
            and _finite_positive(geometry.orifice_area_m2) is None
        ):
            if method is MethodToken.LANGMUIR_FREE_EVAPORATION:
                if "exposed_area_m2" not in missing:
                    missing.append("exposed_area_m2")
            elif "exposed_area_m2" not in missing:
                missing.append("exposed_area_m2")
    detail = {"missing": missing, "method": method.value, "quantity": quantity.value}
    if invalid:
        detail["invalid"] = list(invalid)
    if _is_kems_calibrated_pressure(method, quantity):
        interval_notice = apparatus_geometry_interval_notice(
            experiment,
            quantity,
            origin=experiment.experiment_id,
        )
        if interval_notice is not None:
            # Case 1: the interval is recorded, not folded into the pressure.
            detail["apparatus_geometry_interval_printed"] = True
            detail["regime_checked_at"] = _interval_regime_check_label(experiment)
            detail["value_band"] = None
    checks.append(GateCheck("geometry_determinants", not missing and not invalid, detail))
    if missing or invalid:
        return _fail(
            RefusalReason.UNDERDETERMINED_APPARATUS, checks, "geometry_determinants"
        )
    return _pass(checks)


def effusion_regime_unverified(
    experiment: Experiment,
    quantity: Quantity,
    *,
    observation: Observation | None = None,
    point_observations: Iterable[Observation] = (),
) -> GateOutcome:
    """Prefer the published regime checks; use same-point pressures only as fallback."""

    method_state = experiment.method
    checks: list[GateCheck] = []
    if method_state.is_unknown:
        evidence_state = None if observation is None else observation.evidence.class_
        pressure_quantity = quantity in {
            Quantity.P_SAT,
            Quantity.P_PARTIAL,
            Quantity.P_REFERENCE,
        }
        evidence_needs_regime_check = (
            evidence_state is not None
            and (
                evidence_state.is_unknown
                or (
                    evidence_state.is_value
                    and evidence_state.value is EvidenceClass.FIGURE_ONLY
                )
            )
        )
        if not pressure_quantity or not evidence_needs_regime_check:
            return _pass(checks)
        # A figure-only or evidence-unknown pressure claim cannot rule out
        # effusion. Require the conservative same-point in-cell fallback.
    elif method_state.tag is not StateTag.VALUE or method_state.value is None:
        return _pass(checks)
    elif method_state.value is not MethodToken.KNUDSEN_EFFUSION:
        return _pass(checks)
    if quantity not in {
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
        Quantity.P_REFERENCE,
        Quantity.ACTIVITY,
        Quantity.ACTIVITY_COEFFICIENT,
    }:
        return _pass(checks)
    regime = experiment.pressure_environment.regime
    kn_located = regime.knudsen_number_orifice
    kn = _located_decimal(kn_located)
    if kn is not None:
        molecular = kn >= Decimal(str(FREE_MOLECULAR_KNUDSEN_MIN))
        checks.append(
            GateCheck(
                "orifice_knudsen",
                molecular,
                {
                    "knudsen_number_orifice": str(kn),
                    "threshold": str(FREE_MOLECULAR_KNUDSEN_MIN),
                },
            )
        )
        if not molecular:
            return _effusion_policy_outcome(
                checks,
                "effusion_regime_violated",
                reason="printed orifice Knudsen number is below the molecular limit",
            )
        return _pass(checks)

    diameters = _regime_diameters(experiment)
    cell_pressure = (
        None if observation is None else _printed_in_cell_total_pressure(observation)
    )
    if diameters and cell_pressure is not None and observation is not None:
        # Every printed diameter must admit the pressure. A point has one
        # diameter, so that row stays on min(10 Pa, d * 1000). An interval
        # checks both ends. The coded limit rises with d, so the strict end
        # is d_min; the recorded limit is that minimum.
        end_limits = tuple(_p_over_d_limit(diameter) for diameter in diameters)
        passed = all(cell_pressure <= limit for limit in end_limits)
        pressure_limit = min(end_limits)
        strict_diameter = min(diameters)
        ratio_Pa_per_mm = cell_pressure / (strict_diameter * Decimal("1000"))
        detail = {
            "in_cell_total_pressure_Pa": str(cell_pressure),
            "orifice_diameter_m": str(strict_diameter),
            "pressure_to_diameter_Pa_per_mm": str(ratio_Pa_per_mm),
            "threshold_Pa_per_mm": "1",
            "pressure_limit_Pa": str(pressure_limit),
            "limit_source": "Drowart et al. 2005, p. 689",
            "route": "printed_orifice_diameter_and_cell_pressure",
        }
        if len(diameters) == 2:
            detail["orifice_diameter_low_m"] = str(diameters[0])
            detail["orifice_diameter_high_m"] = str(diameters[1])
            detail["regime_checked_at"] = "both_ends"
        checks.append(
            GateCheck(
                "orifice_pressure_to_diameter",
                passed,
                detail,
            )
        )
        if not passed:
            return _effusion_policy_outcome(
                checks,
                "effusion_regime_violated",
                reason="printed cell pressure and orifice diameter fail the molecular limit",
            )
        return _pass(checks)

    calibration = None if experiment.apparatus is None else experiment.apparatus.calibration
    total_bounds = _pressure_bounds(experiment.pressure_environment.total_pressure_Pa)
    calibration_observation = (
        observation if quantity is Quantity.P_PARTIAL else None
    )
    calibration_grounded = _calibration_grounded(
        calibration,
        observation=calibration_observation,
        experiment_id=experiment.experiment_id,
    )
    if calibration_grounded and total_bounds is not None:
        _lower, upper, pressure_kind = total_bounds
        if (
            pressure_kind in {"interval", "upper_bound"}
            and upper is not None
            and upper <= KEMS_BACKGROUND_HIGH_PA
        ):
            checks.append(
                GateCheck(
                    "orifice_knudsen",
                    True,
                    {
                        "reason": (
                            "orifice Knudsen number not published; calibrated KEMS "
                            "pressure and a wholly low background interval are retained"
                        ),
                        "flag": "orifice_knudsen_not_published",
                        "background_upper_bound_Pa": str(upper),
                    },
                )
            )
            return _pass(checks)
        # Chamber pressure cannot verify the cell regime here. Continue to the
        # printed in-cell sum, which is the independent fallback evidence.

    ungrounded_detail = (
        _calibration_not_grounded_detail(
            calibration,
            observation=calibration_observation,
            experiment_id=experiment.experiment_id,
        )
        if not calibration_grounded
        and quantity
        in {
            Quantity.P_PARTIAL,
            Quantity.ACTIVITY,
            Quantity.ACTIVITY_COEFFICIENT,
        }
        else None
    )
    if observation is None:
        pressure_sum = None
        pressure_detail = {"reason": "scored observation is unavailable"}
    else:
        pressure_sum, pressure_detail = _printed_in_cell_pressure_sum(
            experiment,
            observation,
            point_observations,
        )
    pressure_limit = _in_cell_pressure_limit(experiment)
    diameter_basis = (
        "printed_orifice_diameter_p_over_d"
        if diameters
        else "Drowart_standalone_usual_10_Pa_limit; no defensible d printed"
    )
    pressure_detail.update(
        {
            "pressure_limit_Pa": str(pressure_limit),
            "limit_source": "Drowart et al. 2005, p. 689",
            "limit_basis": diameter_basis,
            "route": "in_cell_fallback",
        }
    )
    if pressure_sum is None:
        lower_bound = pressure_detail.get("printed_partial_pressure_lower_bound_Pa")
        if lower_bound is not None and Decimal(str(lower_bound)) > pressure_limit:
            # A lower bound already above the limit proves the cell is outside
            # the regime, even though species coverage is incomplete.
            pressure_detail["printed_partial_pressure_sum_Pa"] = str(lower_bound)
            checks.append(
                GateCheck("in_cell_partial_pressure_sum", False, pressure_detail)
            )
            if method_state.is_value and method_state.value is MethodToken.KNUDSEN_EFFUSION:
                return _effusion_policy_outcome(
                    checks,
                    "effusion_regime_violated",
                    reason="printed pressure lower bound exceeds the molecular limit",
                )
            return _fail(
                RefusalReason.EFFUSION_REGIME_UNVERIFIED,
                checks,
                "in_cell_partial_pressure_sum",
            )
        if method_state.is_value and method_state.value is MethodToken.KNUDSEN_EFFUSION:
            return _effusion_policy_outcome(
                checks,
                "effusion_regime_assumed",
                reason="no printed regime check is available for this reduced KEMS value",
            )
        checks.append(
            GateCheck(
                "in_cell_partial_pressure_sum",
                False,
                pressure_detail,
            )
        )
        return _fail(
            (
                RefusalReason.METHOD_UNKNOWN
                if method_state.is_unknown
                else RefusalReason.EFFUSION_REGIME_UNVERIFIED
            ),
            checks,
            "in_cell_partial_pressure_sum",
        )
    passed = pressure_sum <= pressure_limit
    if passed:
        missing_orifice = (
            "orifice Kn not printed" if diameters else "orifice not printed"
        )
        pressure_detail["flag"] = (
            f"{missing_orifice}; regime verified from printed in-cell pressure sum "
            "(limit source: Drowart et al. 2005, p. 689; "
            f"{diameter_basis})"
        )
    if ungrounded_detail is not None:
        gate_detail = {
            **pressure_detail,
            **ungrounded_detail,
        }
        if passed:
            gate_detail.update(
                {
                    "reason": "calibration is not grounded for the in-cell fallback",
                    "route": "in_cell_fallback",
                    "pressure_sum_detail": pressure_detail,
                }
            )
    else:
        gate_detail = pressure_detail
    checks.append(
        GateCheck("in_cell_partial_pressure_sum", passed, gate_detail)
    )
    if not passed:
        return _effusion_policy_outcome(
            checks,
            "effusion_regime_violated",
            reason="printed in-cell pressure sum exceeds the molecular limit",
        )
    return _pass(checks)


def _partial_pressure_observations_by_experiment(
    observations: Iterable[Observation],
) -> dict[str, tuple[Observation, ...]]:
    grouped: dict[str, list[Observation]] = {}
    for observation in observations:
        identity = observation.identity
        if (
            isinstance(identity, Identity)
            and quantity_token(identity) is Quantity.P_PARTIAL
        ):
            grouped.setdefault(observation.experiment_id, []).append(observation)
    return {experiment_id: tuple(rows) for experiment_id, rows in grouped.items()}


def _located_condition_value(value: object) -> tuple[bool, object | None]:
    if not isinstance(value, Located) or not value.state.is_value:
        return False, None
    return True, value.state.value


def _same_point_conditions(left: Observation, right: Observation) -> bool:
    left_conditions = left.point_conditions
    right_conditions = right.point_conditions
    if not left_conditions or not right_conditions:
        return False
    if left_conditions.keys() != right_conditions.keys():
        return False
    for key in left_conditions:
        left_known, left_value = _located_condition_value(left_conditions[key])
        right_known, right_value = _located_condition_value(right_conditions[key])
        if not left_known or not right_known or left_value != right_value:
            return False
    return True


def _record_composition_failure(
    failure_details: list[str] | None,
    detail: str,
) -> None:
    if failure_details is not None:
        failure_details.append(detail)


def _composition_components(
    value: object | None,
    *,
    failure_details: list[str] | None = None,
) -> tuple[tuple[str, Decimal], ...] | None:
    if isinstance(value, Located):
        value = value.state
    if isinstance(value, State):
        if not value.is_value:
            _record_composition_failure(
                failure_details,
                value.reason or f"composition state is {value.tag.value}",
            )
            return None
        value = value.value
    if isinstance(value, Composition):
        # An initial-charge proxy is an engine input for prediction, not a
        # measurement-state composition that can establish in-cell species
        # coverage.
        if value.proxy_flag == INITIAL_CHARGE_ONLY_PROXY_FLAG:
            return None
        value = value.components
    elif isinstance(value, Mapping):
        value = value.get("components", tuple(value.items()))
    if not isinstance(value, (tuple, list)) or not value:
        _record_composition_failure(
            failure_details,
            "composition component list is empty or malformed",
        )
        return None
    components: list[tuple[str, Decimal]] = []
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            _record_composition_failure(
                failure_details,
                "composition component entry is malformed",
            )
            return None
        formula, raw_amount = item
        try:
            amount = as_decimal(raw_amount)
        except (InvalidOperation, TypeError, ValueError):
            _record_composition_failure(
                failure_details,
                "composition component amount is not a decimal",
            )
            return None
        if not amount.is_finite():
            _record_composition_failure(
                failure_details,
                "composition component amount is not finite",
            )
            return None
        if amount < 0:
            _record_composition_failure(
                failure_details,
                "composition component amount is negative",
            )
            return None
        components.append((str(formula), amount))
    return tuple(sorted(components))


_POINT_COMPOSITION_CONDITION_ALIASES = ("composition", "sample_composition")


def _observation_composition(
    observation: Observation,
    *,
    composition_failure_details: list[str] | None = None,
) -> tuple[tuple[str, Decimal], ...] | None:
    discovered_details: list[str] = []
    identity = observation.identity
    if isinstance(identity, Identity) and identity.composition is not None:
        components = _composition_components(
            identity.composition,
            failure_details=discovered_details,
        )
        if components is not None:
            return components
        if composition_failure_details is not None:
            composition_failure_details.extend(discovered_details)
        return None
    if observation.point_conditions:
        for key, located in observation.point_conditions.items():
            if key.lower() not in _POINT_COMPOSITION_CONDITION_ALIASES:
                continue
            components = _composition_components(
                located,
                failure_details=discovered_details,
            )
            if components is not None:
                return components
            if composition_failure_details is not None:
                composition_failure_details.extend(discovered_details)
            return None
    if composition_failure_details is not None:
        composition_failure_details.extend(discovered_details)
    # Sample.initial_composition describes the starting charge. It is not a
    # run-composition source for measured in-cell pressure coverage.
    return None


def _skipped_composition_candidate_detail(
    observation_id: str,
    details: list[str],
) -> dict[str, str]:
    return {
        "observation_id": observation_id,
        "typed_reason": RefusalReason.EFFUSION_REGIME_UNVERIFIED.value,
        "detail": "; ".join(details),
    }


def _point_temperature(observation: Observation) -> Decimal | None:
    identity = observation.identity
    if not isinstance(identity, Identity):
        return None
    state = identity.temperature_K
    if state is None or not state.is_value or state.value is None:
        return None
    value = as_decimal(state.value)
    return value if value.is_finite() and value > 0 else None


# Do not mistake nonmetal components such as sulfur in a sulfide for cations.
_NON_METAL_ELEMENTS = frozenset(
    {
        "H",
        "He",
        "C",
        "N",
        "O",
        "F",
        "Ne",
        "P",
        "S",
        "Cl",
        "Ar",
        "Se",
        "Br",
        "Kr",
        "I",
        "Xe",
        "Rn",
        "At",
        "Ts",
        "Og",
    }
)


def _expected_dominant_vapours(
    composition: tuple[tuple[str, Decimal], ...] | None,
) -> tuple[str, ...] | None:
    """Return composition cations covered by at least one measured vapour.

    Oxygen species may be absent: they are usually calculated rather than
    measured and are minor in the in-cell pressure sum. Every positive printed
    species joins the sum, while unknown composition cannot claim coverage.
    """
    if not composition:
        return None
    present = [(formula, amount) for formula, amount in composition if amount > 0]
    if not present:
        return None
    cations: set[str] = set()
    for formula, _ in present:
        elements = formula_composition(formula)
        if elements is None:
            return None
        cations.update(
            element for element, _ in elements if element not in _NON_METAL_ELEMENTS
        )
    if not cations:
        return None
    return tuple(sorted(cations))


def _printed_in_cell_pressure_sum(
    experiment: Experiment,
    observation: Observation,
    point_observations: Iterable[Observation],
) -> tuple[Decimal | None, dict[str, Any]]:
    temperature = _point_temperature(observation)
    composition_failure_details: list[str] = []
    composition = _observation_composition(
        observation,
        composition_failure_details=composition_failure_details,
    )
    conditions = observation.point_conditions
    point_context_incomplete = temperature is None or not conditions
    incomplete_point_context_reason = (
        "same-point temperature/point_conditions are incomplete"
    )
    printed: dict[str, list[tuple[Decimal, str]]] = {}
    coverage_species: set[str] = set()
    skipped_pressure_candidates: list[dict[str, str]] = []
    skipped_composition_candidates: list[dict[str, str]] = []
    candidates = tuple(point_observations)
    if not any(
        candidate.observation_id == observation.observation_id
        for candidate in candidates
    ):
        candidates += (observation,)
    for candidate in candidates:
        identity = candidate.identity
        is_scored_observation = (
            candidate.observation_id == observation.observation_id
        )
        if (
            candidate.experiment_id != observation.experiment_id
            or not isinstance(identity, Identity)
            or quantity_token(identity) is not Quantity.P_PARTIAL
            or candidate.admission.status is not AdmissionStatus.ADMITTED
            or not candidate.evidence.class_.is_value
            or candidate.evidence.class_.value not in MEASURED_EVIDENCE
            or candidate.locator is None
            or not candidate.locator.has_location()
            or (
                not is_scored_observation
                and (
                    temperature is None
                    or _point_temperature(candidate) != temperature
                )
            )
        ):
            continue
        if composition is None:
            if not is_scored_observation:
                continue
        else:
            candidate_composition_details: list[str] = []
            candidate_composition = _observation_composition(
                candidate,
                composition_failure_details=candidate_composition_details,
            )
            if candidate_composition != composition:
                if candidate_composition_details:
                    skipped_composition_candidates.append(
                        _skipped_composition_candidate_detail(
                            candidate.observation_id,
                            candidate_composition_details,
                        )
                    )
                continue
        if candidate.value.kind is not ValueKind.POINT:
            continue
        # The scored row may establish a pressure lower bound, but only the
        # base same-point predicate can establish measured species coverage.
        same_point_conditions = _same_point_conditions(observation, candidate)
        if not is_scored_observation and not same_point_conditions:
            continue
        raw_pressure = candidate.value.point
        try:
            pressure = as_decimal(raw_pressure)
        except (InvalidOperation, TypeError, ValueError, ArithmeticError):
            pressure = None
        if pressure is None or not pressure.is_finite() or pressure <= 0:
            skipped_pressure_candidates.append(
                {
                    "observation_id": candidate.observation_id,
                    "typed_reason": RefusalReason.EFFUSION_REGIME_UNVERIFIED.value,
                    "detail": "candidate pressure point is not a finite positive decimal",
                }
            )
            continue
        printed.setdefault(identity.species.formula, []).append(
            (pressure, candidate.observation_id)
        )
        if same_point_conditions:
            coverage_species.add(identity.species.formula)
    if not printed:
        detail: dict[str, Any] = {
            "reason": (
                incomplete_point_context_reason
                if point_context_incomplete
                else "no admitted printed partial pressures at this exact point"
            )
        }
        if skipped_pressure_candidates:
            detail["typed_reason"] = RefusalReason.EFFUSION_REGIME_UNVERIFIED.value
            detail["skipped_pressure_candidates"] = skipped_pressure_candidates
        if composition_failure_details:
            detail["typed_reason"] = RefusalReason.EFFUSION_REGIME_UNVERIFIED.value
            detail["composition_scope_details"] = composition_failure_details
        if skipped_composition_candidates:
            detail["typed_reason"] = RefusalReason.EFFUSION_REGIME_UNVERIFIED.value
            detail["skipped_composition_candidates"] = skipped_composition_candidates
        return None, detail
    # Repeated determinations for one species are not distinct gases. The
    # largest admitted value per species is a conservative lower bound even
    # when the printed species coverage is incomplete.
    selected = {
        species: max(rows, key=lambda row: (row[0], row[1]))
        for species, rows in printed.items()
    }
    pressure_sum = sum((row[0] for row in selected.values()), Decimal(0))
    sum_detail = {
        "printed_partial_pressures_Pa": {
            species: str(row[0]) for species, row in sorted(selected.items())
        },
        "included_observation_ids": sorted(row[1] for row in selected.values()),
        "point_condition_keys": sorted(conditions or {}),
    }
    if skipped_pressure_candidates:
        sum_detail["skipped_pressure_candidates"] = skipped_pressure_candidates
    if composition_failure_details:
        sum_detail["composition_scope_details"] = composition_failure_details
    if skipped_composition_candidates:
        sum_detail["skipped_composition_candidates"] = skipped_composition_candidates
    lower_bound_detail = {
        "printed_partial_pressure_lower_bound_Pa": str(pressure_sum)
    }
    if point_context_incomplete:
        return None, {
            "reason": incomplete_point_context_reason,
            "typed_reason": RefusalReason.EFFUSION_REGIME_UNVERIFIED.value,
            **lower_bound_detail,
            **sum_detail,
        }
    if composition is None:
        detail = {
            "reason": "composition is unknown; printed species coverage cannot be established",
            "typed_reason": RefusalReason.EFFUSION_REGIME_UNVERIFIED.value,
            "printed_species": sorted(printed),
            **lower_bound_detail,
            **sum_detail,
        }
        return None, detail
    expected = _expected_dominant_vapours(composition)
    if expected is None:
        return None, {
            "reason": "composition does not establish which vapour species could dominate",
            "typed_reason": RefusalReason.EFFUSION_REGIME_UNVERIFIED.value,
            "printed_species": sorted(printed),
            **lower_bound_detail,
            **sum_detail,
        }
    covered = set()
    for formula in coverage_species:
        elements = formula_composition(formula)
        if elements is not None:
            covered.update(element for element, _ in elements)
    missing = sorted(set(expected) - covered)
    if missing:
        return None, {
            "reason": "incomplete printed species coverage; pressure sum is only a lower bound",
            "typed_reason": RefusalReason.EFFUSION_REGIME_UNVERIFIED.value,
            "required_dominant_species": list(expected),
            "printed_species": sorted(printed),
            "missing_species": missing,
            **lower_bound_detail,
            **sum_detail,
        }
    return pressure_sum, {
        "printed_partial_pressure_sum_Pa": str(pressure_sum),
        "required_dominant_species": list(expected),
        **sum_detail,
    }


def _printed_diameter_extent(
    experiment: Experiment,
) -> tuple[Decimal, Decimal, str] | None:
    """Printed positive orifice diameter, as a point or a closed interval.

    Printed means a source locator and no inference. An inferred convenience
    conversion is not this interval.
    """

    geometry = None if experiment.apparatus is None else experiment.apparatus.geometry
    if geometry is None:
        return None
    located_diameter = geometry.orifice_diameter_m
    if (
        located_diameter is None
        or located_diameter.inference is not None
        or located_diameter.locator is None
        or not located_diameter.locator.has_location()
    ):
        return None
    return _positive_extent(located_diameter)


def _regime_diameters(experiment: Experiment) -> tuple[Decimal, ...] | None:
    """Diameters at which the coded p/d predicate must pass.

    A printed point is that one diameter, so a point row keeps
    ``p_limit = min(10 Pa, d_m * 1000)``. A printed interval is both ends.
    The coded limit rises with diameter, so the strict end is ``d_min``.
    Every diameter between the ends is looser than ``d_min``. Do not flip
    the point-row formula: a mean-free-path product would move point rows,
    and that predicate is a separate ruling.
    """

    extent = _printed_diameter_extent(experiment)
    if extent is None:
        return None
    low, high, kind = extent
    if kind == "point":
        return (low,)
    return (low, high)


def _interval_regime_check_label(experiment: Experiment) -> str:
    """Which regime evidence an interval notice may claim.

    A printed orifice Kn is taken before any diameter. The notice may say
    both ends were checked only when that diameter path is the one that runs.
    """

    kn = _located_decimal(
        experiment.pressure_environment.regime.knudsen_number_orifice
    )
    if kn is not None:
        return "printed_orifice_kn"
    return "both_ends"


def _printed_in_cell_total_pressure(observation: Observation) -> Decimal | None:
    located = (observation.point_conditions or {}).get("total_pressure_Pa")
    if (
        not isinstance(located, Located)
        or located.locator is None
        or not located.locator.has_location()
        or located.inference is not None
    ):
        return None
    return _finite_positive(located)


def _p_over_d_limit(diameter_m: Decimal) -> Decimal:
    """Coded Drowart limit ``min(10 Pa, d_m * 1000)``. Not a P·d product."""

    return min(KEMS_CELL_PRESSURE_MAX_PA, diameter_m * Decimal("1000"))


def _in_cell_pressure_limit(
    experiment: Experiment,
) -> Decimal:
    """Strict coded limit over every printed regime diameter.

    A point uses that point. An interval must pass at both ends. The coded
    limit rises with diameter, so that minimum is the limit at ``d_min``.
    """

    diameters = _regime_diameters(experiment)
    if not diameters:
        return KEMS_CELL_PRESSURE_MAX_PA
    return min(_p_over_d_limit(diameter) for diameter in diameters)


def apparatus_geometry_interval_notice(
    experiment: Experiment,
    quantity: Quantity,
    *,
    origin: str,
) -> Notice | None:
    """Advisory notice for a printed orifice-diameter interval (d-326 case 1).

    Calibrated KEMS pressures are p = k I T. Orifice area does not enter,
    so the interval is not a value band. When orifice Kn is absent, the
    regime gate applies the coded p/d limit at both ends. A printed Kn is
    the regime check instead, and this notice does not claim the diameter
    path. This notice is not a flagged stratum.
    """

    method_state = experiment.method
    if method_state.tag is not StateTag.VALUE or method_state.value is None:
        return None
    if not _is_kems_calibrated_pressure(method_state.value, quantity):
        return None
    extent = _printed_diameter_extent(experiment)
    if extent is None:
        return None
    low, high, kind = extent
    if kind != "interval":
        return None
    if _interval_regime_check_label(experiment) == "printed_orifice_kn":
        regime_clause = "effusion regime uses the printed orifice Knudsen number; "
    else:
        regime_clause = (
            f"effusion regime checked at both ends ({low} m and {high} m); "
        )
    return Notice(
        kind=NoticeKind.APPARATUS_GEOMETRY_INTERVAL_PRINTED,
        affected_quantities=(quantity,),
        reason=(
            "apparatus_geometry_interval_printed: printed orifice diameter "
            f"interval {low}-{high} m; "
            + regime_clause
            + "orifice area does not enter the calibrated KEMS pressure; no value band"
        ),
        origin=origin,
    )


@dataclass(frozen=True)
class KnudsenEffusionPressure:
    """Pressure reduced from one Knudsen mass-loss rate."""

    pressure_Pa: Decimal
    evaluation_diameter_m: Decimal
    band_dex: Decimal | None
    notice: Notice | None


def _orifice_area_m2(diameter_m: Decimal) -> Decimal:
    """pi d^2 / 4. pi matches the float Hertz-Knudsen helper."""

    radius = diameter_m / 2
    return Decimal(repr(math.pi)) * radius * radius


def _constant_clausing(
    clausing_factor: Located[Value | Decimal] | Decimal | None,
    orifice_channel_length_m: Located[Value | Decimal] | Decimal | None,
) -> Decimal:
    """Point Clausing factor, or 1 when neither C nor a length was printed.

    A printed length with no point C is refused. Short-orifice W(l/r) is
    not owned; long-tube Clausing (L/D >= 10) is the wrong regime. The
    ideal-orifice default C = 1 also lives in
    simulator/diagnostic_helpers/extract_reproduction.py as a float on the
    engine-reproduction path. That copy is not Located geometry and is not
    this reduction.
    """

    if isinstance(clausing_factor, Decimal):
        clausing = clausing_factor
    elif clausing_factor is None:
        clausing = None
    else:
        clausing = _located_decimal(clausing_factor)
        if clausing is None and clausing_factor.state.is_value:
            raise ValueError(
                "missing clausing_factor: a Clausing interval is not a point"
            )
    if clausing is not None:
        if not _clausing_value_ok(clausing):
            raise ValueError("clausing_factor must be a point in (0, 1]")
        return clausing
    length_printed = isinstance(orifice_channel_length_m, Decimal) or (
        isinstance(orifice_channel_length_m, Located)
        and orifice_channel_length_m.state.is_value
    )
    if length_printed:
        raise ValueError(
            "missing clausing_factor: printed orifice length makes the "
            "Clausing factor depend on l/r, and no short-orifice W(l/r) is owned"
        )
    return Decimal("1")


def knudsen_effusion_pressure_from_mass_rate(
    mass_rate_kg_s: Decimal,
    temperature_K: Decimal,
    molar_mass_kg_mol: Decimal,
    orifice_diameter_m: Located[Value | Decimal] | Decimal,
    *,
    clausing_factor: Located[Value | Decimal] | Decimal | None = None,
    orifice_channel_length_m: Located[Value | Decimal] | Decimal | None = None,
    origin: str,
    quantity: Quantity = Quantity.P_PARTIAL,
) -> KnudsenEffusionPressure:
    """Reduce a raw Knudsen mass-loss rate to pressure (d-326 case 2).

    Premise. Author-tabulated KEMS pressures already use p = k I T, so
    orifice area does not enter those values (case 1). A rate reduced here
    is the Hertz-Knudsen effusion relation
    p = (dm/dt) / (A C k_HK), with A = pi d^2 / 4. k_HK =
    sqrt(M / (2 pi R T)) is hertz_knudsen_k_kg_s_m2_pa. This function does
    not re-derive that square root.

    Algebra. A diameter interval is evaluated at the geometric mean
    d_gm = sqrt(d_min d_max). Because A ~ d^2, d_gm is the diameter whose
    area is the geometric mean of the end areas. With C independent of d,
    (d_max / d_gm)^2 = d_max / d_min. The symmetric dex half-width is
    log10(d_max / d_min). The stored band is the larger of
    |log10(p(d_end) / p(d_gm))|, so a future c_at(d) that differed at the
    ends would widen it. c_at(d) is the printed point at every d today.

    Unit check. k_HK is kg/(m^2 Pa s). (kg/s) / m^2 / (kg/(m^2 Pa s)) = Pa.
    The same product is (kg/s)/m^2 * sqrt((J/(mol K) * K) / (kg/mol)) = Pa,
    because Pa = kg/(m s^2) and J = kg m^2 / s^2. R cancels in the dex band.

    Sanity. 0.25-0.30 mm gives d_gm = sqrt(0.25 * 0.30) mm = 0.273861... mm,
    which rounds to 0.2739 mm. (0.30 / 0.2739)^2 = 1.20 and
    log10(1.20) = 0.07918... dex, the ruling's +/-0.079 dex.

    A point diameter returns that pressure with band_dex None and no notice.
    """

    rate = as_decimal(mass_rate_kg_s)
    temperature = as_decimal(temperature_K)
    molar_mass = as_decimal(molar_mass_kg_mol)
    if (
        not rate.is_finite()
        or rate <= 0
        or not temperature.is_finite()
        or temperature <= 0
        or not molar_mass.is_finite()
        or molar_mass <= 0
    ):
        raise ValueError("mass rate, temperature, and molar mass must be positive")
    if isinstance(orifice_diameter_m, Decimal):
        if not orifice_diameter_m.is_finite() or orifice_diameter_m <= 0:
            raise ValueError("orifice diameter must be positive")
        low = high = orifice_diameter_m
        kind = "point"
    else:
        extent = _positive_extent(orifice_diameter_m)
        if extent is None:
            raise ValueError(
                "orifice diameter must be a positive point or a positive closed interval"
            )
        low, high, kind = extent
    clausing = _constant_clausing(clausing_factor, orifice_channel_length_m)
    from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa

    k_hk = Decimal(
        repr(
            hertz_knudsen_k_kg_s_m2_pa(float(temperature), float(molar_mass))
        )
    )
    if k_hk <= 0:
        raise ValueError("Hertz-Knudsen coefficient must be positive")

    def c_at(_diameter_m: Decimal) -> Decimal:
        # Printed point C. Length-dependent W(l/r) is not evaluated.
        return clausing

    def pressure_at(diameter_m: Decimal) -> Decimal:
        return rate / (_orifice_area_m2(diameter_m) * c_at(diameter_m) * k_hk)

    if kind == "point":
        return KnudsenEffusionPressure(
            pressure_Pa=pressure_at(low),
            evaluation_diameter_m=low,
            band_dex=None,
            notice=None,
        )
    d_gm = (low * high).sqrt()
    pressure_gm = pressure_at(d_gm)
    # Ends versus the evaluation pressure. Constant C reproduces
    # log10(d_max / d_min); a diameter-dependent C would be larger.
    band = max(
        abs((pressure_at(low) / pressure_gm).ln() / Decimal(10).ln()),
        abs((pressure_at(high) / pressure_gm).ln() / Decimal(10).ln()),
    )
    return KnudsenEffusionPressure(
        pressure_Pa=pressure_gm,
        evaluation_diameter_m=d_gm,
        band_dex=band,
        notice=Notice(
            kind=NoticeKind.APPARATUS_GEOMETRY_INTERVAL_PRINTED,
            affected_quantities=(quantity,),
            reason=(
                "apparatus_geometry_interval_printed: printed orifice diameter "
                f"interval {low}-{high} m evaluated at geometric mean {d_gm} m; "
                f"effusion pressure band +/-{band} dex from A ~ d^2"
            ),
            origin=origin,
            band=f"+/-{band} dex",
        ),
    )


def background_pressure_high(
    experiment: Experiment,
    quantity: Quantity,
) -> GateOutcome:
    """Apply the low-background ceiling to a point or typed pressure range."""

    method_state = experiment.method
    checks: list[GateCheck] = []
    if method_state.tag is not StateTag.VALUE or method_state.value is None:
        return _pass(checks)
    if method_state.value is not MethodToken.KNUDSEN_EFFUSION:
        return _pass(checks)
    if quantity not in {
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
        Quantity.P_REFERENCE,
        Quantity.ACTIVITY,
        Quantity.ACTIVITY_COEFFICIENT,
    }:
        # millibar bench kinetic experiments are not this gate
        return _pass(checks)
    bounds = _pressure_bounds(experiment.pressure_environment.total_pressure_Pa)
    if bounds is None:
        # missing pressure already fails effusion; do not double-count here
        return _pass(checks)
    lower, upper, pressure_kind = bounds
    detail = {
        "lower_bound_Pa": None if lower is None else str(lower),
        "upper_bound_Pa": None if upper is None else str(upper),
        "threshold_Pa": str(KEMS_BACKGROUND_HIGH_PA),
        "pressure_kind": pressure_kind,
    }
    if upper is not None and upper <= KEMS_BACKGROUND_HIGH_PA:
        if pressure_kind != "point":
            detail["flag"] = "background_pressure_interval_upper_bound"
        checks.append(GateCheck("background_pressure", True, detail))
        return _pass(checks)
    if lower is not None and lower > KEMS_BACKGROUND_HIGH_PA:
        checks.append(GateCheck("background_pressure", False, detail))
        return _fail(RefusalReason.BACKGROUND_PRESSURE_HIGH, checks, "background_pressure")
    detail["flag"] = "background_pressure_interval_straddles"
    checks.append(
        GateCheck(
            "background_pressure",
            False,
            detail,
        )
    )
    return _fail(
        RefusalReason.BACKGROUND_PRESSURE_INTERVAL_STRADDLES,
        checks,
        "background_pressure",
    )


def run_validity_gates(
    experiment: Experiment,
    observation: Observation,
    *,
    table: dict[str, Any] | None = None,
    tables: Sequence[dict[str, Any]] | None = None,
    point_observations: Iterable[Observation] | None = None,
) -> GateOutcome:
    """Run all four gates. Record every failure; first is the primary reason."""

    quantity = quantity_token(observation.identity)
    checks: list[GateCheck] = []
    primary: RefusalReason | None = None
    primary_name: str | None = None

    def absorb(outcome: GateOutcome) -> None:
        nonlocal primary, primary_name
        checks.extend(outcome.checks)
        if not outcome.passed and primary is None:
            primary = outcome.reason
            primary_name = outcome.primary_check

    payloads: list[dict[str, Any]] = []
    if tables:
        payloads.extend(tables)
    elif table is not None:
        payloads.append(table)
    for payload in payloads:
        if "delta_fG_kJ_mol" not in payload or "log10_Kf" not in payload:
            continue
        absorb(
            table_self_consistency(
                delta_fG_kJ_mol=payload["delta_fG_kJ_mol"],
                log10_Kf=payload["log10_Kf"],
                T_K=payload["T_K"],
                per=payload.get("per"),
                standard_pressure_Pa=payload.get("standard_pressure_Pa"),
                reaction_id=payload.get("reaction_id"),
                printed_log10_Kf=payload.get("printed_log10_Kf"),
            )
        )
    if quantity is not None:
        evidence_class = observation.evidence.class_
        if (
            evidence_class.is_value
            and evidence_class.value not in MEASURED_EVIDENCE
            and evidence_class.value is not EvidenceClass.FIGURE_ONLY
        ):
            checks.append(
                GateCheck(
                    "apparatus_applicability",
                    True,
                    {"reason": "apparatus gate not applicable: non-measured evidence"},
                )
            )
        else:
            absorb(
                effusion_regime_unverified(
                    experiment,
                    quantity,
                    observation=observation,
                    point_observations=(
                        (observation,)
                        if point_observations is None
                        else point_observations
                    ),
                )
            )
            absorb(
                underdetermined_apparatus(
                    experiment, quantity, observation=observation
                )
            )
        absorb(background_pressure_high(experiment, quantity))
    if primary is not None:
        return GateOutcome(
            passed=False,
            reason=primary,
            checks=tuple(checks),
            primary_check=primary_name,
        )
    return _pass(checks)
