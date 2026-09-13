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
  effusion; the background-high gate does not double-count).
- Background ≥ 1e-2 Pa fails KEMS *equilibrium* pressure/activity only.
  Millibar bench kinetic experiments are out of this gate's scope.
- Apparatus determinants are those the actual derivation needs: effusion
  pressure requires orifice area + Clausing/geometry; Langmuir
  pressure/alpha requires exposed area. Determinants must be grounded
  VALUES (unknown calibration fails), physically valid (area > 0,
  Clausing in (0, 1]), and present for TGA/solar/vacuum kinetic area.
  Missing required geometry is ``underdetermined_apparatus``. Archival
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
)
from simulator.battery.identity import log10K_from_delta_fG_kJ_mol, quantity_token
from simulator.battery.records import (
    Experiment,
    Located,
    Observation,
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


def _located_decimal(located: Located[Decimal] | None) -> Decimal | None:
    if located is None or not located.state.is_value or located.state.value is None:
        return None
    return as_decimal(located.state.value)


def _finite_positive(located: Located[Decimal] | None) -> Decimal | None:
    value = _located_decimal(located)
    if value is None or not value.is_finite() or value <= 0:
        return None
    return value


def _clausing_ok(located: Located[Decimal] | None) -> bool:
    value = _located_decimal(located)
    return value is not None and value.is_finite() and value > 0 and value <= 1


def _calibration_grounded(calibration: object) -> bool:
    if not calibration or not isinstance(calibration, dict):
        return False
    for located in calibration.values():
        if not isinstance(located, Located):
            return False
        if not located.state.is_value or located.state.value is None:
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


def underdetermined_apparatus(
    experiment: Experiment,
    quantity: Quantity,
) -> GateOutcome:
    """Pressure/flux conversion requires the method's geometry determinants."""

    method_state = experiment.method
    checks: list[GateCheck] = []
    if method_state.tag is not StateTag.VALUE or method_state.value is None:
        checks.append(
            GateCheck(
                "method",
                False,
                {"reason": method_state.reason or "method unknown"},
            )
        )
        return _fail(RefusalReason.UNDERDETERMINED_APPARATUS, checks, "method")
    method = method_state.value
    geometry = None if experiment.apparatus is None else experiment.apparatus.geometry
    missing: list[str] = []
    if _is_effusion_pressure(method, quantity):
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
        if geometry is None or (
            _finite_positive(geometry.exposed_area_m2) is None
            and _finite_positive(geometry.orifice_area_m2) is None
        ):
            if method is MethodToken.LANGMUIR_FREE_EVAPORATION:
                if "exposed_area_m2" not in missing:
                    missing.append("exposed_area_m2")
            elif method is MethodToken.KNUDSEN_EFFUSION:
                if "orifice_area_m2" not in missing:
                    missing.append("orifice_area_m2")
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
    """KEMS equilibrium pressure/activity: orifice Kn ≥ 10 from cell-local gas."""

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
    total = _located_decimal(experiment.pressure_environment.total_pressure_Pa)
    if total is None:
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
    """KEMS equilibrium comparison with background ≥ 1e-2 Pa fails."""

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
    total = _located_decimal(experiment.pressure_environment.total_pressure_Pa)
    if total is None:
        # missing pressure already fails effusion; do not double-count here
        return _pass(checks)
    ok = total < KEMS_BACKGROUND_HIGH_PA
    checks.append(
        GateCheck(
            "background_pressure",
            ok,
            {
                "total_pressure_Pa": str(total),
                "threshold_Pa": str(KEMS_BACKGROUND_HIGH_PA),
            },
        )
    )
    if not ok:
        return _fail(RefusalReason.BACKGROUND_PRESSURE_HIGH, checks, "background_pressure")
    return _pass(checks)


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
