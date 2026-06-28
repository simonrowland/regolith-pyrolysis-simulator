"""Dependency-free physical and unit constants.

This leaf follows ``simulator/transport_constants.py``: constants are
RELOCATED verbatim -> golden-neutral by construction. This pass only imports
the exact Celsius offset and carrier collision diameters elsewhere; fundamental
constants are parked here for future consolidation.
"""

from __future__ import annotations

# Fundamental constants (SI 2019 / CODATA exact-derived).
GAS_CONSTANT = 8.31446261815324  # J/(mol K); R = N_A k_B.
FARADAY = 96485.33212  # C/mol; F = N_A e.
AVOGADRO = 6.02214076e23  # 1/mol; exact by SI definition.
BOLTZMANN = 1.380649e-23  # J/K; exact by SI definition.
STEFAN_BOLTZMANN = 5.670374419e-8  # W/(m2 K4); exact-derived.
PLANCK = 6.62607015e-34  # J s; exact by SI definition.
ELEMENTARY_CHARGE = 1.602176634e-19  # C; exact by SI definition.
STANDARD_GRAVITY = 9.80665  # m/s2; exact defined standard gravity.

# Unit conversions (exact by definition unless noted).
CELSIUS_TO_KELVIN_OFFSET = 273.15  # T/K = t/deg C + 273.15.
PA_PER_BAR = 1e5
PA_PER_MBAR = 100.0
MBAR_PER_BAR = 1000.0
J_PER_KJ = 1000.0
ANGSTROM_PER_M = 1e10
M2_PER_CM2 = 1e4
STANDARD_ATMOSPHERE_PA = 101325.0

__all__ = (
    "ANGSTROM_PER_M",
    "AVOGADRO",
    "BOLTZMANN",
    "CELSIUS_TO_KELVIN_OFFSET",
    "ELEMENTARY_CHARGE",
    "FARADAY",
    "GAS_CONSTANT",
    "J_PER_KJ",
    "M2_PER_CM2",
    "MBAR_PER_BAR",
    "PA_PER_BAR",
    "PA_PER_MBAR",
    "PLANCK",
    "STANDARD_ATMOSPHERE_PA",
    "STANDARD_GRAVITY",
    "STEFAN_BOLTZMANN",
)
