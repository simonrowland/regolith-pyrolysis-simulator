"""Shared arithmetic for Antoine vapour-pressure correlations."""

from __future__ import annotations


def _antoine_log10_pressure(
    A: float,
    B: float,
    C: float,
    temperature: float,
) -> float:
    """Return ``log10(P) = A - B / (T + C)`` in the row's pressure unit.

    Callers supply coefficients and temperature in their declared units and
    retain responsibility for unit conversion, coefficient selection, and
    validity policy.
    """
    return A - B / (temperature + C)


def _antoine_reciprocal_temperature_log10(A, B, C, temperature, boundary):
    """Constant-enthalpy CC continuation tangent to Antoine at boundary.

    d ln P/dT = deltaH/(R T²) integrates to
    P = P0 exp[-deltaH/R (1/T - 1/T0)]. Antoine gives
    deltaH/R = B ln(10) T0²/(T0+C)². B, C, T0 have units K, so
    deltaH/R is K and the exponent is dimensionless. T -> T0 recovers
    P0; for every T > 0 there is no shifted-temperature pole.
    """
    denom = boundary + C
    boundary_log = _antoine_log10_pressure(A, B, C, boundary)
    slope = -B * (boundary * boundary) / (denom * denom)
    return boundary_log + slope * (1.0 / temperature - 1.0 / boundary)
