"""Validity gates (v2.1 §Validity gates).

Run after referential/schema validation, before equality. Each gate returns
a typed outcome; a failure is a refused Residual reason, never a deleted
Observation. All failed checks are recorded; the first is the primary reason.

Ambiguity resolutions:
- Table self-consistency uses ``log10K_from_delta_fG_kJ_mol``
  (−ΔfG/(R T ln 10)) at matching reaction/per/p°. The finding floor is
  0.1 dex, matching ``TABLE_SELF_CHECK_FINDING_DEX`` in
  species_rail_differential (JANAF printed-precision grain). A consistent
  O2 identity (ΔfG=0, log10 Kf=0) passes. The gate does not score engines.
- Effusion Kn threshold is ``FREE_MOLECULAR_KNUDSEN_MIN`` (10) from
  transport_constants. Kn is the *orifice* (cell-local) number, not chamber
  pressure masquerading as cell pressure. Unknown/missing chamber
  background also fails this gate (v2.1: missing pressure already fails
  effusion; the background-high gate does not double-count). A calibrated
  KEMS pressure comparison with a wholly low typed background interval may
  proceed without inventing an orifice Kn point; the check carries a flag.
- Background ≥ 1e-2 Pa fails KEMS *equilibrium* pressure/activity only.
  Millibar bench kinetic experiments are out of this gate's scope.
- Apparatus determinants are those the actual derivation needs: calibrated
  KEMS p_partial/p_sat requires a grounded calibration but no orifice
  geometry; absolute-flux effusion and Langmuir pressure/alpha require their
  respective geometry. Determinants must be grounded VALUES (unknown
  calibration fails), physically valid (area > 0, Clausing in (0, 1]), and
  present for TGA/solar/vacuum kinetic area.
  Missing required geometry is ``underdetermined_apparatus``. Unknown
  method is ``method_unknown`` when the quantity class is one the schema
  scopes by method (effusion pressure, Langmuir pressure/alpha,
  kinetic/yield); thermochemistry at a stated T/reference state is not
  that class and does not borrow the apparatus token. Archival
  storage of incomplete apparatus is allowed; the gate fails the
  comparison, not the record.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

from simulator.battery.enums import (
    MethodToken,
    Quantity,
    RefusalReason,
    StateTag,
    ValueKind,
)
from simulator.battery.identity import log10K_from_delta_fG_kJ_mol, quantity_token
from simulator.battery.records import (
    Experiment,
    Located,
    Observation,
    Value,
    as_decimal,
)
from simulator.transport_constants import FREE_MOLECULAR_KNUDSEN_MIN

# Printed-precision self-check floor. Same constant as
# simulator.diagnostic_helpers.species_rail_differential.TABLE_SELF_CHECK_FINDING_DEX.
# JANAF low-T rows disagree with CODATA-R recomputation at ~0.02–0.08 dex;
# a finding is reserved for residuals well above that grain.
TABLE_SELF_CHECK_FINDING_DEX = Decimal("0.1")

# KEMS equilibrium background ceiling (Pa). Missing pressure already fails
# the effusion gate; this threshold does not forbid millibar kinetics.
KEMS_BACKGROUND_HIGH_PA = Decimal("1e-2")


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


def _finite_positive(located: Located[Value | Decimal] | None) -> Decimal | None:
    value = _located_decimal(located)
    if value is None or not value.is_finite() or value <= 0:
        return None
    return value


def _clausing_ok(located: Located[Value | Decimal] | None) -> bool:
    value = _located_decimal(located)
    return value is not None and value.is_finite() and value > 0 and value <= 1


def _calibration_grounded(calibration: object) -> bool:
    if not calibration or not isinstance(calibration, dict):
        return False
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
    if _is_kems_calibrated_pressure(method, quantity):
        # Knudsen-effusion mass spectrometry uses the calibrated pressure
        # relation P_i = k_i I_i^+ T.  k_i comes from a calibration (for
        # example, a reference vaporization such as Ag, or a weight-loss
        # calibration), so this route needs neither orifice area nor a
        # Clausing factor. Those geometry terms enter only the absolute-flux
        # Hertz-Knudsen weight-loss route:
        # p = (dm/dt)/(A W) * sqrt(2 pi R T / M).
        calibration = None if experiment.apparatus is None else experiment.apparatus.calibration
        calibrated = _calibration_grounded(calibration)
        checks.append(
            GateCheck(
                "kems_calibration",
                calibrated,
                {
                    "missing": [] if calibrated else ["calibration"],
                    "method": method.value,
                    "quantity": quantity.value,
                    "reason": None
                    if calibrated
                    else "KEMS pressure requires a recorded calibration",
                },
            )
        )
        if not calibrated:
            return _fail(RefusalReason.UNDERDETERMINED_APPARATUS, checks, "kems_calibration")
    elif _is_effusion_pressure(method, quantity):
        if geometry is None:
            missing.extend(["orifice_area_m2", "clausing_factor"])
        else:
            if (
                _finite_positive(geometry.orifice_area_m2) is None
                and _finite_positive(geometry.orifice_diameter_m) is None
            ):
                missing.append("orifice_area_m2")
            if not _clausing_ok(geometry.clausing_factor):
                missing.append("clausing_factor")
        calibration = None if experiment.apparatus is None else experiment.apparatus.calibration
        if not _calibration_grounded(calibration):
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
            needs_orifice = geometry is None or _finite_positive(geometry.orifice_area_m2) is None
            if needs_orifice and "orifice_area_m2" not in missing:
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
    checks.append(
        GateCheck(
            "geometry_determinants",
            not missing,
            {"missing": missing, "method": method.value, "quantity": quantity.value},
        )
    )
    if missing:
        return _fail(
            RefusalReason.UNDERDETERMINED_APPARATUS, checks, "geometry_determinants"
        )
    return _pass(checks)


def effusion_regime_unverified(
    experiment: Experiment,
    quantity: Quantity,
) -> GateOutcome:
    """Check KEMS regime without inventing a missing orifice Kn point."""

    method_state = experiment.method
    checks: list[GateCheck] = []
    if method_state.tag is not StateTag.VALUE or method_state.value is None:
        return _pass(checks)  # not an effusion claim
    if method_state.value is not MethodToken.KNUDSEN_EFFUSION:
        return _pass(checks)
    if quantity not in {
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
        Quantity.P_REFERENCE,
        Quantity.ACTIVITY,
        Quantity.ACTIVITY_COEFFICIENT,
    }:
        return _pass(checks)
    total_bounds = _pressure_bounds(experiment.pressure_environment.total_pressure_Pa)
    if total_bounds is None:
        checks.append(
            GateCheck(
                "background_pressure_stated",
                False,
                {
                    "reason": "unknown KEMS background pressure; missing pressure fails effusion",
                },
            )
        )
        return _fail(
            RefusalReason.EFFUSION_REGIME_UNVERIFIED, checks, "background_pressure_stated"
        )
    regime = experiment.pressure_environment.regime
    kn_located = regime.knudsen_number_orifice
    kn = _located_decimal(kn_located)
    if kn is None:
        _lower, upper, pressure_kind = total_bounds
        calibration = None if experiment.apparatus is None else experiment.apparatus.calibration
        if (
            _calibration_grounded(calibration)
            and pressure_kind in {"interval", "upper_bound"}
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
        checks.append(
            GateCheck(
                "orifice_knudsen",
                False,
                {
                    "reason": "unknown orifice Knudsen number; chamber pressure is not cell pressure",
                },
            )
        )
        return _fail(RefusalReason.EFFUSION_REGIME_UNVERIFIED, checks, "orifice_knudsen")
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
        return _fail(
            RefusalReason.EFFUSION_REGIME_UNVERIFIED, checks, "orifice_knudsen"
        )
    return _pass(checks)


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
        absorb(underdetermined_apparatus(experiment, quantity))
        absorb(effusion_regime_unverified(experiment, quantity))
        absorb(background_pressure_high(experiment, quantity))
    if primary is not None:
        return GateOutcome(
            passed=False,
            reason=primary,
            checks=tuple(checks),
            primary_check=primary_name,
        )
    return _pass(checks)
