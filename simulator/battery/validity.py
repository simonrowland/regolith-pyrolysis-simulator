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
from typing import Any, Iterable, Mapping, Sequence

from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    MEASURED_EVIDENCE,
    MethodToken,
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
    Composition,
    Experiment,
    Located,
    Observation,
    State,
    Value,
    as_decimal,
)
from simulator.reference_data.janaf import formula_composition
from simulator.physical_constants import TABLE_SELF_CHECK_FINDING_DEX
from simulator.transport_constants import FREE_MOLECULAR_KNUDSEN_MIN

# KEMS equilibrium background ceiling (Pa). This remains a separate signal
# quality check; it does not determine the in-cell effusion regime.
KEMS_BACKGROUND_HIGH_PA = Decimal("1e-2")

# Drowart et al. state that p/d <= 1 Pa/mm meets the mean-free-path criterion
# in practice, and that about 10 Pa is the usual KEMS cell-pressure ceiling
# (2005, p. 689). Use only these sourced limits; do not infer a kinetic limit
# from an unsourced collision diameter. Without a located diameter, use the
# ~10 Pa ceiling alone.
KEMS_CELL_PRESSURE_MAX_PA = Decimal("10")


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


def _calibration_not_grounded_detail(calibration: object) -> dict[str, str] | None:
    if calibration is None or (isinstance(calibration, dict) and not calibration):
        return {
            "flag": "calibration_not_grounded",
            "calibration_record_status": "not_recorded",
            "calibration_notice": "No calibration entry has been recorded yet.",
        }
    if isinstance(calibration, dict) and any(
        isinstance(located, Located)
        and not located.state.is_value
        and "not printed" in (located.state.reason or "").casefold()
        for located in calibration.values()
    ):
        return {
            "flag": "calibration_not_grounded",
            "calibration_record_status": "not_printed",
            "calibration_notice": "The source record says the calibration was not printed.",
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
        ungrounded_detail = (
            None if calibrated else _calibration_not_grounded_detail(calibration)
        )
        calibration_flagged = (
            quantity is Quantity.P_PARTIAL and ungrounded_detail is not None
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
        # A calibrated KEMS pressure does not need geometry when it is absent,
        # but supplied geometry must still be a usable point. Never let an
        # interval, bound, or unavailable value masquerade as one.
        if geometry is not None:
            if (
                geometry.orifice_area_m2 is not None
                and geometry.orifice_area_m2.state.is_value
                and _finite_positive(geometry.orifice_area_m2) is None
            ):
                missing.append("orifice_area_m2")
            if (
                geometry.orifice_diameter_m is not None
                and geometry.orifice_diameter_m.state.is_value
                and _finite_positive(geometry.orifice_diameter_m) is None
            ):
                missing.append("orifice_diameter_m")
            if (
                geometry.clausing_factor is not None
                and geometry.clausing_factor.state.is_value
                and not _clausing_ok(geometry.clausing_factor)
            ):
                missing.append("clausing_factor")
    elif _is_effusion_pressure(method, quantity):
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
            quantity in {Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT}
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
        calibrated = _calibration_grounded(calibration)
        activity_quantity = quantity in {
            Quantity.ACTIVITY,
            Quantity.ACTIVITY_COEFFICIENT,
        }
        ungrounded_detail = (
            None if calibrated else _calibration_not_grounded_detail(calibration)
        )
        calibration_flagged = activity_quantity and ungrounded_detail is not None
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
    *,
    observation: Observation | None = None,
    point_observations: Iterable[Observation] = (),
) -> GateOutcome:
    """Prefer the published regime checks; use same-point pressures only as fallback."""

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
            return _fail(
                RefusalReason.EFFUSION_REGIME_UNVERIFIED, checks, "orifice_knudsen"
            )
        return _pass(checks)

    diameter = _printed_orifice_diameter(experiment)
    cell_pressure = (
        None if observation is None else _printed_in_cell_total_pressure(observation)
    )
    if diameter is not None and cell_pressure is not None and observation is not None:
        pressure_limit = _in_cell_pressure_limit(experiment)
        ratio_Pa_per_mm = cell_pressure / (diameter * Decimal("1000"))
        passed = cell_pressure <= pressure_limit
        checks.append(
            GateCheck(
                "orifice_pressure_to_diameter",
                passed,
                {
                    "in_cell_total_pressure_Pa": str(cell_pressure),
                    "orifice_diameter_m": str(diameter),
                    "pressure_to_diameter_Pa_per_mm": str(ratio_Pa_per_mm),
                    "threshold_Pa_per_mm": "1",
                    "pressure_limit_Pa": str(pressure_limit),
                    "limit_source": "Drowart et al. 2005, p. 689",
                    "route": "printed_orifice_diameter_and_cell_pressure",
                },
            )
        )
        if not passed:
            return _fail(
                RefusalReason.EFFUSION_REGIME_UNVERIFIED,
                checks,
                "orifice_pressure_to_diameter",
            )
        return _pass(checks)

    calibration = None if experiment.apparatus is None else experiment.apparatus.calibration
    total_bounds = _pressure_bounds(experiment.pressure_environment.total_pressure_Pa)
    if _calibration_grounded(calibration) and total_bounds is not None:
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

    if not _calibration_grounded(calibration):
        ungrounded_detail = (
            _calibration_not_grounded_detail(calibration)
            if quantity
            in {
                Quantity.P_PARTIAL,
                Quantity.ACTIVITY,
                Quantity.ACTIVITY_COEFFICIENT,
            }
            else None
        )
        if ungrounded_detail is None:
            checks.append(
                GateCheck(
                    "in_cell_partial_pressure_sum",
                    False,
                    {
                        "reason": "calibration is not grounded for the in-cell fallback",
                        "route": "in_cell_fallback",
                    },
                )
            )
            return _fail(
                RefusalReason.EFFUSION_REGIME_UNVERIFIED,
                checks,
                "in_cell_partial_pressure_sum",
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
            if diameter is not None
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
        if pressure_sum is None and quantity in {
            Quantity.ACTIVITY,
            Quantity.ACTIVITY_COEFFICIENT,
        }:
            checks.append(
                GateCheck(
                    "in_cell_partial_pressure_sum",
                    False,
                    pressure_detail,
                )
            )
            return _fail(
                RefusalReason.EFFUSION_REGIME_UNVERIFIED,
                checks,
                "in_cell_partial_pressure_sum",
            )
        if pressure_sum is not None and pressure_sum > pressure_limit:
            checks.append(
                GateCheck("in_cell_partial_pressure_sum", False, pressure_detail)
            )
            return _fail(
                RefusalReason.EFFUSION_REGIME_UNVERIFIED,
                checks,
                "in_cell_partial_pressure_sum",
            )
        checks.append(
            GateCheck(
                "in_cell_partial_pressure_sum",
                True,
                {
                    **ungrounded_detail,
                    "reason": "calibration is not grounded for the in-cell fallback",
                    "route": "in_cell_fallback",
                    "pressure_sum_detail": pressure_detail,
                },
            )
        )
        return _pass(checks)
    if observation is None:
        pressure_sum = None
        pressure_detail = {"reason": "scored observation is unavailable"}
    else:
        pressure_sum, pressure_detail = _printed_in_cell_pressure_sum(
            experiment,
            observation,
            point_observations,
        )
    if pressure_sum is None:
        checks.append(
            GateCheck(
                "in_cell_partial_pressure_sum",
                False,
                {**pressure_detail, "route": "in_cell_fallback"},
            )
        )
        return _fail(
            RefusalReason.EFFUSION_REGIME_UNVERIFIED,
            checks,
            "in_cell_partial_pressure_sum",
        )
    pressure_limit = _in_cell_pressure_limit(experiment)
    passed = pressure_sum <= pressure_limit
    diameter_basis = (
        "printed_orifice_diameter_p_over_d"
        if diameter is not None
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
    if passed:
        missing_orifice = "orifice Kn not printed" if diameter is not None else "orifice not printed"
        pressure_detail["flag"] = (
            f"{missing_orifice}; regime verified from printed in-cell pressure sum "
            "(limit source: Drowart et al. 2005, p. 689; "
            f"{diameter_basis})"
        )
    checks.append(
        GateCheck("in_cell_partial_pressure_sum", passed, pressure_detail)
    )
    if not passed:
        return _fail(
            RefusalReason.EFFUSION_REGIME_UNVERIFIED,
            checks,
            "in_cell_partial_pressure_sum",
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


def _composition_components(
    value: object | None,
) -> tuple[tuple[str, Decimal], ...] | None:
    if isinstance(value, State):
        if not value.is_value:
            return None
        value = value.value
    if isinstance(value, Located):
        if not value.state.is_value:
            return None
        value = value.state.value
    if isinstance(value, Composition):
        value = value.components
    elif isinstance(value, Mapping):
        value = value.get("components", tuple(value.items()))
    if not isinstance(value, (tuple, list)) or not value:
        return None
    components: list[tuple[str, Decimal]] = []
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            return None
        formula, raw_amount = item
        try:
            amount = as_decimal(raw_amount)
        except (TypeError, ValueError):
            return None
        if not amount.is_finite() or amount < 0:
            return None
        components.append((str(formula), amount))
    return tuple(sorted(components))


def _observation_composition(
    experiment: Experiment,
    observation: Observation,
) -> tuple[tuple[str, Decimal], ...] | None:
    identity = observation.identity
    if isinstance(identity, Identity) and identity.composition is not None:
        components = _composition_components(identity.composition)
        if components is not None:
            return components
    if observation.point_conditions:
        for key, located in observation.point_conditions.items():
            if key.lower() in {"composition", "sample_composition"}:
                components = _composition_components(located)
                if components is not None:
                    return components
    return _composition_components(experiment.sample.initial_composition)


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
    composition = _observation_composition(experiment, observation)
    conditions = observation.point_conditions
    if temperature is None or not conditions:
        return None, {"reason": "same-point temperature/point_conditions are incomplete"}
    if composition is None:
        return None, {
            "reason": "composition is unknown; printed species coverage cannot be established",
            "typed_reason": RefusalReason.EFFUSION_REGIME_UNVERIFIED.value,
        }
    printed: dict[str, list[tuple[Decimal, str]]] = {}
    for candidate in point_observations:
        identity = candidate.identity
        if (
            candidate.experiment_id != observation.experiment_id
            or not isinstance(identity, Identity)
            or quantity_token(identity) is not Quantity.P_PARTIAL
            or candidate.admission.status is not AdmissionStatus.ADMITTED
            or not candidate.evidence.class_.is_value
            or candidate.evidence.class_.value not in MEASURED_EVIDENCE
            or candidate.locator is None
            or not candidate.locator.has_location()
            or _point_temperature(candidate) != temperature
            or _observation_composition(experiment, candidate) != composition
            or not _same_point_conditions(observation, candidate)
            or candidate.value.kind is not ValueKind.POINT
            or candidate.value.point is None
        ):
            continue
        pressure = candidate.value.point
        if pressure.is_finite() and pressure > 0:
            printed.setdefault(identity.species.formula, []).append(
                (pressure, candidate.observation_id)
            )
    if not printed:
        return None, {"reason": "no admitted printed partial pressures at this exact point"}
    expected = _expected_dominant_vapours(composition)
    if expected is None:
        return None, {
            "reason": "composition does not establish which vapour species could dominate",
            "typed_reason": RefusalReason.EFFUSION_REGIME_UNVERIFIED.value,
            "printed_species": sorted(printed),
        }
    covered = set()
    for formula in printed:
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
        }
    # Multiple points for one species are repeated determinations, not distinct
    # gases. Use the largest printed value to avoid double-counting and keep
    # the total-pressure bound conservative.
    selected = {
        species: max(rows, key=lambda row: (row[0], row[1]))
        for species, rows in printed.items()
    }
    pressure_sum = sum((row[0] for row in selected.values()), Decimal(0))
    return pressure_sum, {
        "printed_partial_pressure_sum_Pa": str(pressure_sum),
        "printed_partial_pressures_Pa": {
            species: str(row[0]) for species, row in sorted(selected.items())
        },
        "included_observation_ids": sorted(row[1] for row in selected.values()),
        "point_condition_keys": sorted(conditions),
        "required_dominant_species": list(expected),
    }


def _printed_orifice_diameter(experiment: Experiment) -> Decimal | None:
    geometry = None if experiment.apparatus is None else experiment.apparatus.geometry
    if geometry is not None:
        located_diameter = geometry.orifice_diameter_m
        if (
            located_diameter is not None
            and located_diameter.inference is None
            and located_diameter.locator is not None
            and located_diameter.locator.has_location()
        ):
            return _finite_positive(located_diameter)
    return None


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


def _in_cell_pressure_limit(
    experiment: Experiment,
) -> Decimal:
    diameter = _printed_orifice_diameter(experiment)
    if diameter is None:
        return KEMS_CELL_PRESSURE_MAX_PA
    # Drowart's printed p/d <= 1 Pa/mm is 1000 Pa/m times the printed d.
    pressure_to_diameter_limit = diameter * Decimal("1000")
    return min(KEMS_CELL_PRESSURE_MAX_PA, pressure_to_diameter_limit)


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
