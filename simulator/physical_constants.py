"""Dependency-free physical and unit constants.

One owner for SI constants and exact unit conversions. Shorter spellings
that are not bit-identical to the CODATA values live here under explicit
names. Do not alias a truncation back to the full constant: the relative
gaps are pinned in tests/test_physical_constant_ownership.py.
"""

from __future__ import annotations

from decimal import Decimal

# Fundamental constants (SI 2019 / CODATA exact-derived).
GAS_CONSTANT = 8.31446261815324  # J/(mol K); R = N_A k_B.
# Decimal of that float via str(), so a second digit spelling cannot drift.
# Equal to Decimal("8.31446261815324"). Not the 6-sig or 10-sig floats.
GAS_CONSTANT_DECIMAL = Decimal(str(GAS_CONSTANT))
FARADAY = 96485.33212  # C/mol; F = N_A e.
AVOGADRO = 6.02214076e23  # 1/mol; exact by SI definition.
BOLTZMANN = 1.380649e-23  # J/K; exact by SI definition.
STEFAN_BOLTZMANN = 5.670374419e-8  # W/(m2 K4); exact-derived.
PLANCK = 6.62607015e-34  # J s; exact by SI definition.
ELEMENTARY_CHARGE = 1.602176634e-19  # C; exact by SI definition.
STANDARD_GRAVITY = 9.80665  # m/s2; exact defined standard gravity.

# Spellings that are not bit-identical to the values above. Each relative
# gap is (spelling - full) / full.
# GAS_CONSTANT_6SIG: -3.149e-7. state, ellingham_graph, organics_pyrolysis.
# GAS_CONSTANT_10SIG: -1.843e-11. condensation, NASA-CEA, pumping, foulants.
# FARADAY_10SIG: -2.073e-10. state.FARADAY, used by electrolysis.
# STEFAN_BOLTZMANN_7SIG: -7.389e-8. state and equipment radiative power.
GAS_CONSTANT_6SIG = 8.31446
GAS_CONSTANT_10SIG = 8.314462618
FARADAY_10SIG = 96485.3321
STEFAN_BOLTZMANN_7SIG = 5.670374e-8

# Unit conversions (exact by definition unless noted).
CELSIUS_TO_KELVIN_OFFSET = 273.15  # T/K = t/deg C + 273.15.
PA_PER_BAR = 1e5
# Same object as PA_PER_BAR. The brief's name for the bar-to-pascal factor.
BAR_PA = PA_PER_BAR
# Exact decimal of 1 bar. 100000 is an integer, so this is not a float parse.
BAR_PA_DECIMAL = Decimal(100000)
PA_PER_MBAR = 100.0
MBAR_PER_BAR = 1000.0
J_PER_KJ = 1000.0
ANGSTROM_PER_M = 1e10
M2_PER_CM2 = 1e4
STANDARD_ATMOSPHERE_PA = 101325.0
# Same object as STANDARD_ATMOSPHERE_PA. The brief's name for 1 atm.
ATM_PA = STANDARD_ATMOSPHERE_PA
# Exact decimal. 101325 is an integer below 2^53, so the float is exact.
ATM_PA_DECIMAL = Decimal(101325)

# Printed-precision table self-check floor shared by battery validity and
# compilation diagnostics. This is a policy scalar, not a physical constant.
TABLE_SELF_CHECK_FINDING_DEX = Decimal("0.1")

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
    "ATM_PA",
    "ATM_PA_DECIMAL",
    "AVOGADRO",
    "BAR_PA",
    "BAR_PA_DECIMAL",
    "BOLTZMANN",
    "CATALOG_PHYSICAL_PRESSURE_CEILING_PA",
    "CELSIUS_TO_KELVIN_OFFSET",
    "ELEMENTARY_CHARGE",
    "FARADAY",
    "FARADAY_10SIG",
    "GAS_CONSTANT",
    "GAS_CONSTANT_10SIG",
    "GAS_CONSTANT_6SIG",
    "GAS_CONSTANT_DECIMAL",
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
    "STEFAN_BOLTZMANN_7SIG",
    "TABLE_SELF_CHECK_FINDING_DEX",
)
