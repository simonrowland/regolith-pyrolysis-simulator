"""Rough sub-ambient pumping-cost model for pressure-lever economics.

Purpose (KNOB-COST-PRESSURE, #52): give the optimizer a first-order energy cost
AND a feasibility flag when a recipe asks to hold an overhead pressure BELOW the
local ambient. This encodes the Moon-vs-Mars asymmetry that the pressure lever
lives or dies on:

  * Vacuum bodies (Moon ~nanotorr, asteroids lower): the ambient is already below
    any useful process pressure, so evolved offgas VENTS OUT for free. The deep
    low-pO2 Ellingham points are essentially free -> "vent-free" regime, ~zero cost.
  * Mars (~610 Pa datum; ~72 Pa at Olympus Mons summit): to run below ambient you
    must PUMP the offgas up against the CO2 back-pressure. Two costs appear:
      (1) compression ENERGY  ~ n_dot * R * T * ln(P_ambient / P_target) / eff
      (2) a pump-SIZE wall: the volumetric speed needed at the chamber pressure is
          S = n_dot * R * T / P_target, which grows as 1/P_target and quickly
          exceeds any real pump train -> "exponential pumpdown can't keep up with
          offgassing" (owner, 2026-07-04). Below the feasibility speed the target
          pressure simply cannot be held.

This is a ROUGH grounding model for optimizer costing, NOT a validated pump-train
design. Refinements (atmospheric in-leak through seals, real pump-curve S(P),
staged inter-cooling, condenser-exit gas temperature) are noted inline as
follow-ups.
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


@dataclass(frozen=True)
class PumpingCostParameter:
    name: str
    value: float
    units: str
    source_tag: str
    ticket: str
    status: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": float(self.value),
            "units": self.units,
            "source_tag": self.source_tag,
            "ticket": self.ticket,
            "status": self.status,
        }


DEFAULT_PUMP_ISOTHERMAL_EFFICIENCY = PumpingCostParameter(
    name="pump_isothermal_efficiency",
    value=0.15,
    units="fraction",
    source_tag=(
        "owner-ratify-placeholder:2026-07-02-knob-grounding:"
        "eta_wire_to_gas_range_0.05_to_0.25"
    ),
    ticket="COST-PARAM-PUMP-ISOTHERMAL-EFFICIENCY",
    status="owner-ratify-placeholder",
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
        DEFAULT_PUMP_ISOTHERMAL_EFFICIENCY,
        DEFAULT_MAX_PUMP_SPEED_M3_S,
    )


@dataclass(frozen=True)
class SubambientPumpCost:
    """Rough sub-ambient pumping cost + feasibility for one stage."""

    regime: str  # "vent-free" (target >= ambient) | "pump" (target < ambient)
    energy_kWh: float  # electrical energy over the stage duration
    mean_power_W: float
    required_pump_speed_m3_s: float  # volumetric speed the pump must provide at P_target
    compression_ratio: float  # P_ambient / P_target
    feasible: bool  # required speed <= max_pump_speed_m3_s
    status: str = "ok"

    def to_json(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "energy_kWh": float(self.energy_kWh),
            "mean_power_W": float(self.mean_power_W),
            "required_pump_speed_m3_s": float(self.required_pump_speed_m3_s),
            "compression_ratio": float(self.compression_ratio),
            "feasible": bool(self.feasible),
            "status": self.status,
        }


def estimate_subambient_pump_cost(
    target_pressure_pa: float,
    offgas_mol_per_s: float,
    duration_s: float,
    *,
    ambient_pressure_pa: float = MARS_OLYMPUS_SUMMIT_AMBIENT_PA,
    gas_temperature_K: float = 500.0,
    pump_isothermal_efficiency: float = DEFAULT_PUMP_ISOTHERMAL_EFFICIENCY.value,
    max_pump_speed_m3_s: float = DEFAULT_MAX_PUMP_SPEED_M3_S.value,
) -> SubambientPumpCost:
    """Estimate the energy + feasibility of holding ``target_pressure_pa`` below
    ``ambient_pressure_pa`` while the melt evolves ``offgas_mol_per_s`` of
    (non-condensable) gas that must be pumped out.

    ``gas_temperature_K`` is the gas temperature at the pump inlet (default 500 K
    assumes the metal-vapor products have already condensed in the train and the
    pump handles the cooled non-condensable O2 + inert sweep). Returns a
    :class:`SubambientPumpCost`. Fail-soft: degenerate probes return a diagnostic
    result rather than raising. A non-positive target pressure is infeasible
    when gas must be moved; zero offgas remains vent-free because there is
    nothing to pump.
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

    ratio = ambient_pressure_pa / target_pressure_pa
    # (1) Isothermal minimum compression work per mole, scaled by pump efficiency.
    work_per_mol_J = _R_J_PER_MOL_K * gas_temperature_K * math.log(ratio)
    eff = _positive_or_default(
        pump_isothermal_efficiency,
        DEFAULT_PUMP_ISOTHERMAL_EFFICIENCY.value,
    )
    mean_power_W = offgas_mol_per_s * work_per_mol_J / eff
    energy_kWh = mean_power_W * duration_s / 3.6e6
    # (2) Volumetric pumping speed required at the chamber pressure (the size wall).
    throughput_pa_m3_s = offgas_mol_per_s * _R_J_PER_MOL_K * gas_temperature_K
    required_speed_m3_s = throughput_pa_m3_s / target_pressure_pa
    speed_ceiling = _positive_or_default(
        max_pump_speed_m3_s,
        DEFAULT_MAX_PUMP_SPEED_M3_S.value,
    )
    feasible = math.isfinite(required_speed_m3_s) and required_speed_m3_s <= speed_ceiling
    return SubambientPumpCost(
        "pump",
        energy_kWh,
        mean_power_W,
        required_speed_m3_s,
        ratio,
        feasible,
    )


def pumping_context_from_sim(sim: Any, snapshots: Any) -> dict[str, Any]:
    melt = getattr(sim, "melt", None)
    body = normalize_body_name(getattr(melt, "body", ""))
    ambient_pressure_mbar = _float_or_nan(
        getattr(melt, "ambient_pressure_mbar", math.nan)
    )
    vacuum_floor_bar = _float_or_nan(_call_or_nan(getattr(sim, "_vacuum_floor_bar", None)))
    ambient_pressure_pa = _ambient_pressure_pa(
        body=body,
        ambient_pressure_mbar=ambient_pressure_mbar,
        vacuum_floor_bar=vacuum_floor_bar,
    )
    rows: list[dict[str, Any]] = []
    try:
        iterable = tuple(snapshots or ())
    except TypeError:
        iterable = ()
    for snapshot in iterable:
        overhead = getattr(snapshot, "overhead", None)
        pressure_mbar = _float_or_nan(getattr(overhead, "pressure_mbar", math.nan))
        target_pressure_pa = pressure_mbar * _PA_PER_MBAR
        gas_temperature_K = _float_or_nan(
            getattr(overhead, "headspace_temperature_K", math.nan)
        )
        if not math.isfinite(gas_temperature_K) or gas_temperature_K <= 0.0:
            temperature_C = _float_or_nan(getattr(snapshot, "temperature_C", math.nan))
            gas_temperature_K = temperature_C + 273.15
        melt_offgas_mol_hr = max(
            0.0,
            _float_or_nan(getattr(snapshot, "melt_offgas_O2_mol_hr", 0.0)),
        )
        mre_anode_mol_hr = max(
            0.0,
            _float_or_nan(getattr(snapshot, "mre_anode_O2_mol_hr", 0.0)),
        )
        offgas_mol_per_s = (melt_offgas_mol_hr + mre_anode_mol_hr) / _SECONDS_PER_HOUR
        if offgas_mol_per_s <= 0.0:
            continue
        rows.append(
            {
                "hour": int(getattr(snapshot, "hour", len(rows))),
                "target_pressure_pa": target_pressure_pa,
                "offgas_mol_per_s": offgas_mol_per_s,
                "duration_s": _SECONDS_PER_HOUR,
                "gas_temperature_K": gas_temperature_K,
            }
        )
    return {
        "schema_version": "pumping-context-v1",
        "body": body,
        "ambient_pressure_pa": ambient_pressure_pa,
        "ambient_pressure_source": (
            "melt.ambient_pressure_mbar"
            if math.isfinite(ambient_pressure_mbar) and ambient_pressure_mbar > 0.0
            else "environment.vacuum_floor_bar_for_body"
        ),
        "rows": tuple(rows),
    }


def _ambient_pressure_pa(
    *,
    body: str,
    ambient_pressure_mbar: float,
    vacuum_floor_bar: float,
) -> float:
    if math.isfinite(ambient_pressure_mbar) and ambient_pressure_mbar > 0.0:
        return ambient_pressure_mbar * _PA_PER_MBAR
    if math.isfinite(vacuum_floor_bar) and vacuum_floor_bar > 0.0:
        return vacuum_floor_bar * _PA_PER_BAR
    if body == "mars":
        return MARS_DATUM_AMBIENT_PA
    if body == "moon":
        return MOON_AMBIENT_PA
    if body == "asteroid":
        return ASTEROID_AMBIENT_PA
    return MOON_AMBIENT_PA


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


def _call_or_nan(value: Any) -> float:
    if not callable(value):
        return math.nan
    try:
        return float(value())
    except Exception:
        return math.nan
