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
