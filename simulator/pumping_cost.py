"""Diagnostic sub-ambient pumping-cost sidecar for pressure-lever economics.

Purpose (KNOB-COST-PRESSURE, #52): report first-order compression energy and a
fail-closed holding-pressure diagnostic when a recipe asks to hold an overhead
pressure BELOW local ambient. The cost rollup feeds this sidecar into optimizer
energy objectives and the hard pumping-feasibility gate. It encodes the
Moon-vs-Mars asymmetry that the pressure lever lives or dies on:

  * Vacuum bodies (Moon ~nanotorr, asteroids lower): the ambient is already below
    any useful process pressure, so evolved offgas VENTS OUT for free. The deep
    low-pO2 Ellingham points are essentially free -> "vent-free" regime, ~zero cost.
  * Mars (~610 Pa datum; ~72 Pa at Olympus Mons summit): to run below ambient you
    must PUMP the offgas up against the CO2 back-pressure. Two costs appear:
      (1) compression ENERGY from equal-ratio, intercooled adiabatic stages
      (2) a chamber-throughput speed requirement
          S = n_dot * R * T / P_target, which grows as 1/P_target. Comparing that
          requirement with a nominal pump speed is valid only after a
          Knudsen-regime-appropriate line conductance is supplied.

This is a ROUGH diagnostic model, NOT a validated pump-train design. Refinements
(atmospheric in-leak through seals, real pump-curve S(P), non-ideal intercooling,
condenser-exit gas temperature) are noted inline as follow-ups.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from simulator.environment import (
    ASTEROID_VACUUM_FLOOR_BAR,
    MARS_DATUM_PRESSURE_BAR,
    MARS_OLYMPUS_PRESSURE_BAR,
    MOON_VACUUM_FLOOR_BAR,
    normalize_body_name,
)

# Universal gas constant, J/(mol*K). CODATA 2018.
_R_J_PER_MOL_K = 8.314462618
_PA_PER_BAR = 100_000.0
_PA_PER_MBAR = 100.0
_SECONDS_PER_HOUR = 3600.0

# --- Ambient reference pressures (Pa). Cited so the optimizer can pick a site. ---
# Re-exported from simulator.environment to avoid a second pressure-constant
# authority in the pumping-cost helper.
MARS_DATUM_AMBIENT_PA = MARS_DATUM_PRESSURE_BAR * _PA_PER_BAR
MARS_OLYMPUS_SUMMIT_AMBIENT_PA = MARS_OLYMPUS_PRESSURE_BAR * _PA_PER_BAR
MOON_AMBIENT_PA = MOON_VACUUM_FLOOR_BAR * _PA_PER_BAR
ASTEROID_AMBIENT_PA = ASTEROID_VACUUM_FLOOR_BAR * _PA_PER_BAR

_PUMPING_BODY_BY_FEEDSTOCK = {
    # Lunar feedstocks and simulants.
    **{
        feedstock_id: "moon"
        for feedstock_id in (
            "lunar_mare_low_ti",
            "lunar_mare_high_ti",
            "lunar_highland",
            "lunar_pkt_kreep_average",
            "lunar_spa_kreep_influenced",
            "targeted_super_kreep_ore",
            "lunar_highlands_lhs1",
            "lunar_mare_lms1",
            "lunar_mare_oprl2n",
            "lunar_highlands_nuw_lht_5m",
            "lunar_mare_jsc_1a_legacy",
            "lunar_eac_1a",
            "lunar_mls_1a",
            "lunar_highlands_nu_lht_2m",
        )
    },
    # Airless/deep-space small-body feedstocks.
    **{
        feedstock_id: "asteroid"
        for feedstock_id in (
            "s_type_asteroid_silicate",
            "m_type_metallic_phase",
            "m_type_silicate_phase",
            "v_type_vesta_hed",
            "e_type_enstatite_aubrite",
            "ci_carbonaceous_chondrite",
            "cm_carbonaceous_chondrite",
            "ceres_regolith",
            "comet_nucleus",
        )
    },
    **{
        feedstock_id: "mars"
        for feedstock_id in (
            "mars_global_mgs1",
            "mars_basalt",
            "mars_sulfate_rich",
            "mars_phyllosilicate_clay",
            "mars_perchlorate_rich",
        )
    },
}

_PUMPING_AMBIENT_PA_BY_BODY = {
    "moon": MOON_AMBIENT_PA,
    "asteroid": ASTEROID_AMBIENT_PA,
    "mars": MARS_DATUM_AMBIENT_PA,
}


def pumping_environment_for_feedstock(feedstock_id: object) -> dict[str, Any]:
    """Return the pumping-only body/ambient map for a configured feedstock."""

    key = str(feedstock_id or "").strip()
    body = _PUMPING_BODY_BY_FEEDSTOCK.get(key, "")
    if not body:
        return {
            "schema_version": "pumping-feedstock-environment-v1",
            "status": "refused",
            "reason": "unsupported-feedstock",
            "feedstock_id": key,
            "body": "",
            "ambient_pressure_pa": math.nan,
        }
    return {
        "schema_version": "pumping-feedstock-environment-v1",
        "status": "ok",
        "feedstock_id": key,
        "body": body,
        "ambient_pressure_pa": _PUMPING_AMBIENT_PA_BY_BODY[body],
        "ambient_pressure_source": (
            f"pumping-feedstock-map-v1:{key}->{body}"
        ),
    }


@dataclass(frozen=True)
class PumpingCostParameter:
    name: str
    value: float
    units: str
    source_tag: str
    ticket: str
    status: str
    ratification_note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": float(self.value),
            "units": self.units,
            "source_tag": self.source_tag,
            "ticket": self.ticket,
            "status": self.status,
            "ratification_note": self.ratification_note,
        }


DEFAULT_PUMP_STAGE_ISENTROPIC_EFFICIENCY = PumpingCostParameter(
    name="pump_stage_isentropic_efficiency",
    value=0.70,
    units="fraction",
    source_tag="owner-ratify-placeholder:reciprocating-stage-efficiency",
    ticket="COST-PARAM-PUMP-STAGE-ISENTROPIC-EFFICIENCY",
    status="owner-ratify-placeholder",
    ratification_note=(
        "RATIFICATION NOTE: owner ratifies the positive-displacement, perfectly "
        "intercooled stage premise and placeholder 0.70 efficiency before any "
        "optimizer wiring is authorized."
    ),
)
DEFAULT_PUMP_MOTOR_DRIVE_EFFICIENCY = PumpingCostParameter(
    name="pump_motor_drive_efficiency",
    value=0.90,
    units="fraction",
    source_tag=(
        "DOE-AMO-2014:motor-drive-sourcebook:table-B-1-100hp-1800rpm-"
        "nema-premium-95.4pct-and-ASD-full-load-at-least-95pct"
    ),
    ticket="COST-PARAM-PUMP-MOTOR-DRIVE-EFFICIENCY",
    status="literature-derived-owner-ratify-at-wiring",
    ratification_note=(
        "RATIFICATION NOTE: owner ratifies the representative 100 hp motor and "
        "full-load adjustable-speed-drive premise behind the rounded "
        "0.95 * 0.95 = 0.90 combined efficiency before optimizer wiring."
    ),
)
DEFAULT_MAX_STAGE_PRESSURE_RATIO = PumpingCostParameter(
    name="max_stage_pressure_ratio",
    value=4.0,
    units="ratio",
    source_tag=(
        "DOE-QER-2015:natural-gas-compression:"
        "reciprocating-stage-pressure-ratio-usually-not-above-4"
    ),
    ticket="COST-PARAM-PUMP-MAX-STAGE-PRESSURE-RATIO",
    status="literature-anchored-owner-ratify-at-wiring",
    ratification_note=(
        "RATIFICATION NOTE: owner ratifies the 4:1 maximum stage pressure "
        "ratio when optimizer wiring is authorized."
    ),
)
DEFAULT_MAX_PUMP_SPEED_M3_S = PumpingCostParameter(
    name="max_pump_speed_m3_s",
    value=50.0,
    units="m^3/s",
    source_tag=(
        "owner-ratify-placeholder:single-parallelized-pump-train-speed-ceiling:"
        "real-pump-curve-datasheet-pinning-still-open"
    ),
    ticket="COST-PARAM-PUMP-SPEED-CEILING",
    status="owner-ratify-placeholder",
)


def pumping_cost_parameters() -> tuple[PumpingCostParameter, ...]:
    return (
        DEFAULT_PUMP_STAGE_ISENTROPIC_EFFICIENCY,
        DEFAULT_PUMP_MOTOR_DRIVE_EFFICIENCY,
        DEFAULT_MAX_STAGE_PRESSURE_RATIO,
        DEFAULT_MAX_PUMP_SPEED_M3_S,
    )


@dataclass(frozen=True)
class SubambientPumpCost:
    """Rough sub-ambient pumping cost + feasibility for one stage."""

    regime: str  # "vent-free" (target >= ambient) | "pump" (target < ambient)
    energy_kWh: float  # electrical energy over the stage duration
    mean_power_W: float  # electrical input power
    required_pump_speed_m3_s: float  # volumetric speed the pump must provide at P_target
    compression_ratio: float  # P_ambient / P_target
    feasible: bool | None  # None until line conductance is supplied
    compression_model: str = "none"
    compression_stages: int = 0
    stage_pressure_ratio: float = 1.0
    status: str = "ok"
    line_conductance_m3_s: float = math.nan
    effective_speed_ceiling_m3_s: float = math.nan

    def to_json(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "energy_kWh": float(self.energy_kWh),
            "mean_power_W": float(self.mean_power_W),
            "required_pump_speed_m3_s": float(self.required_pump_speed_m3_s),
            "compression_ratio": float(self.compression_ratio),
            "feasible": self.feasible,
            "compression_model": self.compression_model,
            "compression_stages": int(self.compression_stages),
            "stage_pressure_ratio": float(self.stage_pressure_ratio),
            "status": self.status,
            "line_conductance_m3_s": float(self.line_conductance_m3_s),
            "effective_speed_ceiling_m3_s": float(
                self.effective_speed_ceiling_m3_s
            ),
        }


def estimate_subambient_pump_cost(
    target_pressure_pa: float,
    offgas_mol_per_s: float,
    duration_s: float,
    *,
    ambient_pressure_pa: float = MARS_OLYMPUS_SUMMIT_AMBIENT_PA,
    gas_temperature_K: float = 500.0,
    stage_isentropic_efficiency: float = (
        DEFAULT_PUMP_STAGE_ISENTROPIC_EFFICIENCY.value
    ),
    motor_drive_efficiency: float = DEFAULT_PUMP_MOTOR_DRIVE_EFFICIENCY.value,
    max_stage_pressure_ratio: float = DEFAULT_MAX_STAGE_PRESSURE_RATIO.value,
    heat_capacity_ratio: float = 1.40,
    max_pump_speed_m3_s: float = DEFAULT_MAX_PUMP_SPEED_M3_S.value,
    validated_line_conductance_m3_s: float | None = None,
) -> SubambientPumpCost:
    """Estimate the energy + feasibility of holding ``target_pressure_pa`` below
    ``ambient_pressure_pa`` while the melt evolves ``offgas_mol_per_s`` of
    (non-condensable) gas that must be pumped out.

    ``gas_temperature_K`` is the gas temperature at each stage inlet (default 500 K
    assumes the metal-vapor products have already condensed and intercooling
    returns the non-condensable O2 to that temperature).
    ``motor_drive_efficiency`` converts compressor-shaft work to electrical input.
    ``validated_line_conductance_m3_s`` must come from a geometry and gas-flow
    regime calculation; without it, energy is reported but feasibility is
    ``None``. Returns a :class:`SubambientPumpCost`. Fail-soft: degenerate probes
    return a diagnostic result rather than raising. A non-positive target
    pressure is infeasible when gas must be moved; zero offgas remains vent-free
    because there is nothing to pump.
    """

    target_pressure_pa = _float_or_nan(target_pressure_pa)
    ambient_pressure_pa = _float_or_nan(ambient_pressure_pa)
    offgas_mol_per_s = _float_or_nan(offgas_mol_per_s)
    duration_s = _float_or_nan(duration_s)
    gas_temperature_K = _float_or_nan(gas_temperature_K)

    if (
        not math.isfinite(offgas_mol_per_s)
        or not math.isfinite(duration_s)
        or offgas_mol_per_s <= 0.0
        or duration_s <= 0.0
    ):
        return SubambientPumpCost("vent-free", 0.0, 0.0, 0.0, 1.0, True)
    if not math.isfinite(target_pressure_pa) or target_pressure_pa <= 0.0:
        return _infeasible_degenerate("invalid-target-pressure")
    if not math.isfinite(ambient_pressure_pa) or ambient_pressure_pa <= 0.0:
        return _infeasible_degenerate("invalid-ambient-pressure")
    if not math.isfinite(gas_temperature_K) or gas_temperature_K <= 0.0:
        return _infeasible_degenerate("invalid-gas-temperature")

    # At or above ambient: offgas vents out for free (the Moon/vacuum advantage).
    if target_pressure_pa >= ambient_pressure_pa:
        return SubambientPumpCost("vent-free", 0.0, 0.0, 0.0, 1.0, True)

    log_ratio = math.log(ambient_pressure_pa) - math.log(target_pressure_pa)
    # (1) Intercooled, equal-pressure-ratio adiabatic stages.
    #
    # PREMISE: a positive-displacement compressor train with perfect
    # intercooling, equal stage pressure ratios, a DOE-anchored 4:1 maximum
    # ratio, and an explicitly owner-ratify 0.70 stage efficiency. NASA
    # CR-111843 / LMSC-A903162 section 4 gives the ideal-gas stage work
    #   w_s = gamma/(gamma-1) * R*T * (r_s**((gamma-1)/gamma) - 1).
    # Actual shaft work is w_s/eta_s. Equal stage ratios minimize work when
    # stage efficiencies and inlet temperatures are equal, so with total ratio
    # r and N stages, r_s=r**(1/N), and w=N*w_s/eta_s. As N -> infinity,
    # N*(r**((gamma-1)/(gamma*N))-1) -> ((gamma-1)/gamma)*ln(r),
    # recovering the isothermal lower bound R*T*ln(r).
    # MOTOR/DRIVE PREMISE: DOE AMO's 2014 motor-and-drive sourcebook Table B-1
    # lists 95.4% for a 100 hp, 1800 rpm NEMA Premium motor; its adjustable-speed
    # drive table gives at least 95% at full load. Conservatively round the
    # combined 0.954*0.95=0.9063 to eta_md=0.90 pending hardware ratification.
    # ALGEBRA: electrical work w_e = shaft work / eta_md.
    # UNIT CHECK: gamma, eta_s, eta_md, r_s, and the exponent are dimensionless;
    # R*T is J/mol, so w_e is J/mol; n_dot*w_e is J/s = W; W*s/3.6e6 is kWh.
    # SANITY: r=6.1, N=2, T=300 K, gamma=1.4, eta_s=0.70 gives about
    # 7352 J/mol shaft and 8169 J/mol electrical at eta_md=0.90.
    # DOE's 2015 QER natural-gas compression analysis reports reciprocating
    # stage ratios normally no higher than 4 and explains that intercooling
    # reduces compression horsepower. Hardware selection remains owner-fenced.
    # Sources:
    #   ntrs.nasa.gov/citations/19710027629
    #   energy.gov/sites/prod/files/2015/05/f22/QER%20Analysis%20-%20Opportunities
    #   %20for%20Efficiency%20Improvements%20in%20the%20U.S.%20Natural%20Gas%
    #   20Transmission%20Storage%20and%20Distribution%20System.pdf
    #   energy.gov/sites/prod/files/2014/04/f15/amo_motors_sourcebook_web.pdf
    gamma = _float_or_nan(heat_capacity_ratio)
    if not math.isfinite(gamma) or gamma <= 1.0:
        return _infeasible_degenerate("invalid-heat-capacity-ratio")
    eff = _float_or_nan(stage_isentropic_efficiency)
    if not math.isfinite(eff) or not 0.0 < eff <= 1.0:
        return _infeasible_degenerate("invalid-stage-isentropic-efficiency")
    motor_drive_eff = _float_or_nan(motor_drive_efficiency)
    if not math.isfinite(motor_drive_eff) or not 0.0 < motor_drive_eff <= 1.0:
        return _infeasible_degenerate("invalid-motor-drive-efficiency")
    stage_ratio_ceiling = _float_or_nan(max_stage_pressure_ratio)
    if not math.isfinite(stage_ratio_ceiling) or stage_ratio_ceiling <= 1.0:
        return _infeasible_degenerate("invalid-max-stage-pressure-ratio")
    stages = max(1, math.ceil(log_ratio / math.log(stage_ratio_ceiling)))
    log_stage_ratio = log_ratio / stages
    stage_ratio = math.exp(log_stage_ratio)
    exponent = (gamma - 1.0) / gamma
    shaft_work_per_mol_J = (
        stages
        * gamma
        / (gamma - 1.0)
        * _R_J_PER_MOL_K
        * gas_temperature_K
        * math.expm1(exponent * log_stage_ratio)
        / eff
    )
    electrical_work_per_mol_J = shaft_work_per_mol_J / motor_drive_eff
    mean_power_W = offgas_mol_per_s * electrical_work_per_mol_J
    energy_kWh = mean_power_W * duration_s / 3.6e6
    # (2) Volumetric pumping speed required at the chamber pressure (the size wall).
    required_speed_m3_s = _exp_or_inf(
        math.log(offgas_mol_per_s)
        + math.log(_R_J_PER_MOL_K)
        + math.log(gas_temperature_K)
        - math.log(target_pressure_pa)
    )
    speed_ceiling = _positive_or_default(
        max_pump_speed_m3_s,
        DEFAULT_MAX_PUMP_SPEED_M3_S.value,
    )
    if validated_line_conductance_m3_s is None:
        line_conductance_m3_s = math.nan
        effective_speed_ceiling_m3_s = math.nan
        feasible: bool | None = None
        status = "missing-validated-line-conductance"
    else:
        line_conductance_m3_s = _float_or_nan(validated_line_conductance_m3_s)
        if not math.isfinite(line_conductance_m3_s) or line_conductance_m3_s <= 0.0:
            return _infeasible_degenerate("invalid-line-conductance")
        # Pump and line conductance are in series at the chamber:
        # 1/S_eff = 1/S_pump + 1/C_line. The supplied C_line is required to
        # have already passed the applicable Knudsen-regime correlation.
        effective_speed_ceiling_m3_s = 1.0 / (
            1.0 / speed_ceiling + 1.0 / line_conductance_m3_s
        )
        feasible = (
            math.isfinite(required_speed_m3_s)
            and required_speed_m3_s <= effective_speed_ceiling_m3_s
        )
        status = "ok" if feasible else "pump-speed-limit-exceeded"
    compression_ratio = _exp_or_inf(log_ratio)
    return SubambientPumpCost(
        "pump",
        energy_kWh,
        mean_power_W,
        required_speed_m3_s,
        compression_ratio,
        feasible,
        compression_model="intercooled-staged-adiabatic",
        compression_stages=stages,
        stage_pressure_ratio=stage_ratio,
        status=status,
        line_conductance_m3_s=line_conductance_m3_s,
        effective_speed_ceiling_m3_s=effective_speed_ceiling_m3_s,
    )


def pumping_context_from_sim(
    sim: Any,
    snapshots: Any,
    *,
    feedstock_id_override: str | None = None,
) -> dict[str, Any]:
    melt = getattr(sim, "melt", None)
    record = getattr(sim, "record", None)
    feedstock_id = str(
        feedstock_id_override
        if feedstock_id_override is not None
        else getattr(record, "feedstock_key", "") or ""
    )
    mapped_environment = (
        pumping_environment_for_feedstock(feedstock_id)
        if feedstock_id
        else None
    )
    if mapped_environment is not None:
        if mapped_environment["status"] != "ok":
            return _pumping_context_refusal(
                str(mapped_environment["reason"]),
                feedstock_id=feedstock_id,
            )
        body = str(mapped_environment["body"])
        ambient_pressure_pa = float(mapped_environment["ambient_pressure_pa"])
        ambient_pressure_source = str(
            mapped_environment["ambient_pressure_source"]
        )
    else:
        body = normalize_body_name(getattr(melt, "body", ""))
        ambient_pressure_mbar = _float_or_nan(
            getattr(melt, "ambient_pressure_mbar", math.nan)
        )
        ambient_pressure_pa = _ambient_pressure_pa(
            ambient_pressure_mbar=ambient_pressure_mbar,
        )
        ambient_pressure_source = "melt.ambient_pressure_mbar"
    if not math.isfinite(ambient_pressure_pa) or ambient_pressure_pa <= 0.0:
        return _pumping_context_refusal(
            "missing-ambient-pressure",
            body=body,
            feedstock_id=feedstock_id,
        )
    if body and body not in {"mars", "moon", "asteroid"}:
        return _pumping_context_refusal("unsupported-body", body=body)
    rows: list[dict[str, Any]] = []
    try:
        iterable = tuple(snapshots or ())
    except TypeError:
        iterable = ()
    for snapshot in iterable:
        # The two O2 source fields follow different ledger paths. Melt/offgas
        # O2 enters process.overhead_gas, then its turbine-compressed fraction
        # is already booked as EnergyRecord.turbine_kWh. MRE-anode O2 is instead
        # credited directly to terminal.oxygen_mre_anode_stored; it never
        # traverses this chamber-overhead pump. Adding either stream here would
        # charge the wrong device. Only the melt/offgas fraction that bypassed
        # the turbine and was vented still needs target->Mars-ambient pumping,
        # so this sidecar uses O2_vented_mol_hr alone.
        overhead = getattr(snapshot, "overhead", None)
        uncompressed_o2_mol_hr = _float_or_nan(
            getattr(snapshot, "O2_vented_mol_hr", 0.0)
        )
        if (
            not math.isfinite(uncompressed_o2_mol_hr)
            or uncompressed_o2_mol_hr < 0.0
        ):
            return _pumping_context_refusal(
                "invalid-o2-vented-flow",
                body=body,
                feedstock_id=feedstock_id,
                hour=int(getattr(snapshot, "hour", len(rows))),
            )
        if uncompressed_o2_mol_hr == 0.0:
            continue
        pressure_mbar = _float_or_nan(getattr(overhead, "pressure_mbar", math.nan))
        if not math.isfinite(pressure_mbar) or pressure_mbar <= 0.0:
            return _pumping_context_refusal(
                "missing-target-pressure",
                body=body,
                feedstock_id=feedstock_id,
                hour=int(getattr(snapshot, "hour", len(rows))),
            )
        target_pressure_pa = pressure_mbar * _PA_PER_MBAR
        gas_temperature_K = _float_or_nan(
            getattr(overhead, "headspace_temperature_K", math.nan)
        )
        if not math.isfinite(gas_temperature_K) or gas_temperature_K <= 0.0:
            temperature_C = _float_or_nan(getattr(snapshot, "temperature_C", math.nan))
            gas_temperature_K = temperature_C + 273.15
        offgas_mol_per_s = uncompressed_o2_mol_hr / _SECONDS_PER_HOUR
        row = {
            "hour": int(getattr(snapshot, "hour", len(rows))),
            "target_pressure_pa": target_pressure_pa,
            "offgas_mol_per_s": offgas_mol_per_s,
            "duration_s": _SECONDS_PER_HOUR,
            "gas_temperature_K": gas_temperature_K,
        }
        validated_line_conductance = getattr(
            overhead,
            "validated_line_conductance_m3_s",
            None,
        )
        if validated_line_conductance is not None:
            row["validated_line_conductance_m3_s"] = validated_line_conductance
        # The live pipe_conductance_kg_hr field is a mass-throughput cap, not a
        # regime-validated volumetric line conductance, so it is deliberately
        # not converted here. Without the explicit certified field above, Mars
        # sub-ambient rows remain unresolved and refuse under the conductance
        # fence instead of claiming false feasibility.
        rows.append(row)
    return {
        "schema_version": "pumping-context-v1",
        "status": "ok",
        "feedstock_id": feedstock_id,
        "body": body,
        "ambient_pressure_pa": ambient_pressure_pa,
        "ambient_pressure_source": ambient_pressure_source,
        "energy_accounting_policy": (
            "uncompressed_o2_only; turbine-compressed_o2_is_already_charged"
        ),
        "rows": tuple(rows),
    }


def _ambient_pressure_pa(
    *,
    ambient_pressure_mbar: float,
) -> float:
    if math.isfinite(ambient_pressure_mbar) and ambient_pressure_mbar > 0.0:
        return ambient_pressure_mbar * _PA_PER_MBAR
    return math.nan


def _pumping_context_refusal(
    reason: str,
    *,
    body: str = "",
    feedstock_id: str = "",
    hour: int | None = None,
) -> dict[str, Any]:
    refusal: dict[str, Any] = {
        "schema_version": "pumping-context-v1",
        "status": "refused",
        "reason": reason,
        "feedstock_id": feedstock_id,
        "body": body,
        "ambient_pressure_pa": math.nan,
        "rows": (),
    }
    if hour is not None:
        refusal["hour"] = int(hour)
    return refusal


def _infeasible_degenerate(status: str) -> SubambientPumpCost:
    return SubambientPumpCost(
        status,
        0.0,
        0.0,
        math.inf,
        math.inf,
        False,
        status=status,
    )


def _float_or_nan(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _positive_or_default(value: Any, default: float) -> float:
    number = _float_or_nan(value)
    return number if math.isfinite(number) and number > 0.0 else default


def _exp_or_inf(log_value: float) -> float:
    try:
        return math.exp(log_value)
    except OverflowError:
        return math.inf
