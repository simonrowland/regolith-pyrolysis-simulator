"""Graphite / C–CO oxygen fugacity for battery oxygen_condition.

Used when a paper states graphite (or C–CO) buffering and prints temperature
and P_CO (or total pressure when CO is the stated gas). The result is always
DERIVED — never a printed stamp.

Premise
-------
Graphite saturation with a CO-bearing C–O fluid. The elementary half-reaction
the papers name is

    C (graphite) + 1/2 O2 (g) = CO (g)

At graphite saturation the laboratory C–O fluid is the published graphite–
CO–CO2 (CCO) buffer, not the metastable pure-CO limiting case that omits
Boudouard CO2. The certified point expression is Jakobsson & Oskarsson
(1994, GCA), carried in LEPR / ThermoEngine and used as the CCO reference
line by Stagno & Frost (2010, EPSL 300:72-84):

Algebra
-------
For C + 1/2 O2 = CO,

    K = (P_CO / bar) / (fO2 / bar) ** (1/2)
    log10(fO2 / bar) = 2 * (log10(P_CO / bar) - log10(K))

The published CCO expression supplies the 1-atm reference value. At fixed T,
the reaction quotient therefore gives

    log10(fO2) = log10(fO2_ref) + 2 * log10(P_CO / P_ref)

where P_ref = 1 atm = 1.01325 bar.

Units
-----
T_K / T_K and pressure ratios are dimensionless; the constant terms are
already on a log10(fO2/bar) scale.

Sanity
------
At T = 1473.15 K and P_CO = 1.01325 bar (1 atm):
    log10(fO2/bar) = -10.475256413
This matches the published CCO line and the numeric values previously computed
for ts1985 via engines.builtin.cco_redox_buffer (same coefficients).
At the same T and P_CO = 0.101325 bar (0.1 atm):
    log10(fO2/bar) = -12.475256413
which is exactly two log units lower as required by the reaction quotient.
"""

from __future__ import annotations

import math
import re

from simulator.scalar_boundary import is_declared_real_scalar

# Jakobsson & Oskarsson 1994 CCO point coefficients via LEPR / ThermoEngine.
_CCO_A = -21_803.0
_CCO_B = 4.325
_CCO_C = 0.171
_REFERENCE_PRESSURE_BAR = 1.01325
_CCO_EQUATION = (
    f"log10(fO2/bar) = {_CCO_A:g}/T_K + {_CCO_B:g} + "
    f"{_CCO_C:g}*(P_bar - 1)/T_K"
)

C_CO_FORMULATION = "JakobssonOskarsson1994_CCO_via_LEPR_graphite_CO_CO2"
C_CO_SOURCE = (
    "Jakobsson & Oskarsson 1994 GCA (CCO point formula, via LEPR/ThermoEngine); "
    "Stagno & Frost 2010 EPSL 300:72-84 graphite-saturation context"
)

# Chase 1998 NIST-JANAF formation values at 298 K give the linear
# Ellingham approximation for 2 C + O2 -> 2 CO, per mol O2:
# ΔH = 2*(-110.527) = -221.054 kJ; ΔG = 2*(-137.163) = -274.326 kJ;
# ΔS = (ΔH - ΔG)/T = 0.17877 kJ/K. The pure-CO limit is distinct from the
# CCO buffer above, which includes the CO2 share through Boudouard equilibrium.
_C_TO_CO_DH_KJ_PER_MOL_O2 = -221.054
_C_TO_CO_DS_KJ_PER_K_PER_MOL_O2 = 0.17877

# Buffer tokens that mean graphite / C–CO (not Frost IW/NNO/QFM/WM, and not
# free prose about CO/Ar mixing). Normalized by _normalize_buffer_token.
_C_CO_BUFFER_TOKENS = frozenset(
    {
        "C-CO",
        "CCO",
        "C/CO",
        "GRAPHITE-CO",
        "GRAPHITE/CO",
        "GRAPHITE-C-CO",
        "GRAPHITE/C-CO",
        "C-CO-CO2",
        "CCO2",
        "C-CO2",
    }
)


def _normalize_buffer_token(token: str) -> str:
    cleaned = token.strip().upper().replace(" ", "").replace("_", "-")
    # Collapse runs of hyphens from mixed separators.
    return re.sub(r"-{2,}", "-", cleaned)


def is_c_co_buffer_token(token: str) -> bool:
    """True when the extract's fO2_control.buffer names graphite / C–CO."""
    if not token or not isinstance(token, str):
        return False
    return _normalize_buffer_token(token) in _C_CO_BUFFER_TOKENS


def _require_finite_positive(value: float, label: str) -> float:
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise TypeError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{label} must be finite and positive")
    return parsed


def cco_log10_fO2_bar(temperature_K: float, pressure_bar: float = 1.0) -> float:
    """Return Jakobsson & Oskarsson CCO log10(fO2/bar) at T and pressure."""
    temperature = _require_finite_positive(temperature_K, "temperature_K")
    pressure = _require_finite_positive(pressure_bar, "pressure_bar")
    return _CCO_A / temperature + _CCO_B + _CCO_C * (pressure - 1.0) / temperature


def log10_fo2_c_co_bar(temperature_K: float, p_co_bar: float) -> float:
    """Return log10(fO2/bar) for graphite / C–CO at T and P_CO.

    ``p_co_bar`` is the printed CO pressure in bar (or total pressure in bar
    when the paper states CO is the gas).
    """
    if not is_declared_real_scalar(temperature_K, allow_numeric_str=True):
        raise TypeError("temperature_K must be numeric")
    if not is_declared_real_scalar(p_co_bar, allow_numeric_str=True):
        raise TypeError("p_co_bar must be numeric")
    temperature = float(temperature_K)
    pressure = float(p_co_bar)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    if not math.isfinite(pressure) or pressure <= 0.0:
        raise ValueError("p_co_bar must be finite and positive")
    reference_log10_fo2 = cco_log10_fO2_bar(
        temperature,
        _REFERENCE_PRESSURE_BAR,
    )
    return reference_log10_fo2 + 2.0 * math.log10(
        pressure / _REFERENCE_PRESSURE_BAR
    )


def carbon_delta_g_co_kj_per_mol_o2(temperature_K: float) -> float:
    """ΔG(2 C + O2 -> 2 CO) per mol O2. Falls with T."""
    try:
        temperature = float(temperature_K)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"temperature_K must be a finite number, got {temperature_K!r}"
        ) from exc
    if not math.isfinite(temperature):
        raise ValueError(
            f"temperature_K must be a finite number, got {temperature_K!r}"
        )
    return (
        _C_TO_CO_DH_KJ_PER_MOL_O2
        - temperature * _C_TO_CO_DS_KJ_PER_K_PER_MOL_O2
    )


__all__ = [
    "C_CO_FORMULATION",
    "C_CO_SOURCE",
    "carbon_delta_g_co_kj_per_mol_o2",
    "cco_log10_fO2_bar",
    "is_c_co_buffer_token",
    "log10_fo2_c_co_bar",
]
