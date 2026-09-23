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
    log10(fO2 / bar) = -21803 / T_K + 4.325 + 0.171 * (P_bar - 1) / T_K

where P_bar is the printed CO pressure (or total pressure when the paper
states the gas is CO), converted Pa → bar by / 1e5.

Units
-----
T_K / T_K and (K/bar) * bar / T_K are dimensionless; the constant terms are
already on a log10(fO2/bar) scale.

Sanity
------
At T = 1473.15 K and P_CO = 1.01325 bar (1 atm):
    log10(fO2/bar) ≈ -10.475
which matches the published CCO line and the numeric values previously
computed for ts1985 via engines.builtin.cco_redox_buffer (same coefficients).
"""

from __future__ import annotations

import math
import re

from simulator.scalar_boundary import is_declared_real_scalar

# Same coefficients as engines.builtin.cco_redox_buffer (Jakobsson & Oskarsson
# 1994 via LEPR). Kept here so oxygen_condition can import without pulling the
# heavy engines.builtin package root.
_CCO_A = -21_803.0
_CCO_B = 4.325
_CCO_C = 0.171

C_CO_FORMULATION = "JakobssonOskarsson1994_CCO_via_LEPR_graphite_CO_CO2"
C_CO_SOURCE = (
    "Jakobsson & Oskarsson 1994 GCA (CCO point formula, via LEPR/ThermoEngine); "
    "Stagno & Frost 2010 EPSL 300:72-84 graphite-saturation context"
)

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
    return _CCO_A / temperature + _CCO_B + _CCO_C * (pressure - 1.0) / temperature


__all__ = [
    "C_CO_FORMULATION",
    "C_CO_SOURCE",
    "is_c_co_buffer_token",
    "log10_fo2_c_co_bar",
]
