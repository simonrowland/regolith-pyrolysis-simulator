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

# Melt-dissociation pO2 envelope for vapor-pressure mass action (b-148).
#
# Premise: silicate-melt oxygen fugacity spans near-vacuum reducing floors
# through pure-O2 oxidizing caps. Mass-action p_V ∝ (pO2/pO2_ref)^n applies
# the *physical* melt pO2, not a float-range clamp.
# Algebra: pO2_bar = 10**(fO2_log10_bar). Unit check: bar absolute (not Pa).
# Sanity: air ≈ 0.21 bar; pure O2 = 1 bar; IW @ 1800 K ≈ 1e-8 bar.
# The prior 1e300 upper clamp is a float sentinel, not a melt state: for
# AlO2 (pO2_exp = +0.25), (1e300)^0.25 = 1e75 multiplies a ~1e-8–1e-12 Pa
# unit-ref pressure into ~1e63–1e66 Pa — the b-148 CI full-run dump. Cap at
# 100 bar (fO2_log = +2), generously above pure O2, so oxygen-dependent
# carriers cannot invent multi-GPa vapor from a non-physical clamp.
MELT_DISSOCIATION_PO2_MIN_BAR = 1.0e-30
MELT_DISSOCIATION_PO2_MAX_BAR = 100.0

# Catalog operating-envelope physical pressure ceiling (b-148 regression).
# 1 bar = 1e5 Pa; 1e9 Pa = 10 kbar is already far above any vacuum-pyrolysis
# vapor partial pressure. Values above this under the envelope are defects.
CATALOG_PHYSICAL_PRESSURE_CEILING_PA = 1.0e9

__all__ = (
    "ANGSTROM_PER_M",
    "AVOGADRO",
    "BOLTZMANN",
    "CATALOG_PHYSICAL_PRESSURE_CEILING_PA",
    "CELSIUS_TO_KELVIN_OFFSET",
    "ELEMENTARY_CHARGE",
    "FARADAY",
    "GAS_CONSTANT",
    "J_PER_KJ",
    "M2_PER_CM2",
    "MBAR_PER_BAR",
    "MELT_DISSOCIATION_PO2_MAX_BAR",
    "MELT_DISSOCIATION_PO2_MIN_BAR",
    "PA_PER_BAR",
    "PA_PER_MBAR",
    "PLANCK",
    "STANDARD_ATMOSPHERE_PA",
    "STANDARD_GRAVITY",
    "STEFAN_BOLTZMANN",
)
