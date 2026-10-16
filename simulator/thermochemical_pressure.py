"""Shared equilibrium-constant and pressure algebra for thermochemical data.

Premise: for M(condensed) ⇌ M(g) at unit condensed activity, the equilibrium
constant is Kp = P/P0. Algebra: log10(Kp) = -ΔvapG°/(R T ln 10), then
P = P0 × 10**log10(Kp). Units: ΔG in kJ/mol is multiplied by 1000 to J/mol;
R is J/(mol K), T is K, and Kp is dimensionless; P0 in Pa therefore yields
P in Pa. Sanity: at a normal boiling point ΔvapG° = 0, so log10(Kp) = 0 and
P = P0 = 1 bar = 100000 Pa for JANAF.
"""

from __future__ import annotations

import math

from simulator.physical_constants import J_PER_KJ

LOG_10 = math.log(10.0)


def log10_equilibrium_constant_from_delta_g_kj_mol(
    delta_g_kj_mol: float,
    temperature_K: float,
    gas_constant_j_mol_K: float,
) -> float:
    """Return log10(Kp) from ΔG° in kJ/mol and source-era R in J/(mol K)."""

    delta_g = float(delta_g_kj_mol)
    temperature = float(temperature_K)
    gas_constant = float(gas_constant_j_mol_K)
    if not all(math.isfinite(value) for value in (delta_g, temperature, gas_constant)):
        raise ValueError("ΔG, temperature, and gas constant must be finite")
    if temperature <= 0.0 or gas_constant <= 0.0:
        raise ValueError("temperature and gas constant must be positive")
    return -(delta_g * J_PER_KJ) / (gas_constant * temperature * LOG_10)


def pressure_pa_from_log10_equilibrium_constant(
    log10_equilibrium_constant: float,
    standard_pressure_pa: float,
) -> float:
    """Return P in Pa from log10(P/P0) and the declared standard pressure."""

    log_ratio = float(log10_equilibrium_constant)
    standard_pressure = float(standard_pressure_pa)
    if not math.isfinite(log_ratio) or not math.isfinite(standard_pressure):
        raise ValueError("log10(Kp) and standard pressure must be finite")
    if standard_pressure <= 0.0:
        raise ValueError("standard pressure must be positive")
    return standard_pressure * 10.0**log_ratio


def log10_pressure_ratio_from_pa(
    pressure_pa: float,
    standard_pressure_pa: float,
) -> float:
    """Return log10(P/P0) for positive pressures expressed in Pa."""

    pressure = float(pressure_pa)
    standard_pressure = float(standard_pressure_pa)
    if not math.isfinite(pressure) or not math.isfinite(standard_pressure):
        raise ValueError("pressure and standard pressure must be finite")
    if pressure <= 0.0 or standard_pressure <= 0.0:
        raise ValueError("pressure and standard pressure must be positive")
    return math.log10(pressure / standard_pressure)


__all__ = (
    "log10_equilibrium_constant_from_delta_g_kj_mol",
    "log10_pressure_ratio_from_pa",
    "pressure_pa_from_log10_equilibrium_constant",
)
