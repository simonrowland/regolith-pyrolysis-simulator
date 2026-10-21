"""Shared arithmetic for Antoine vapour-pressure correlations."""

from __future__ import annotations

from collections.abc import Mapping


def _antoine_skip_notice(
    coefficients: Mapping[str, object],
    *,
    species: str,
    site: str,
    temperature_K: float,
) -> str:
    """Describe a skipped Antoine row without changing its evaluation."""
    reason = (
        "missing_antoine_A"
        if coefficients.get("A") is None
        else "nonpositive_antoine_A"
    )
    return (
        "antoine_species_skipped: "
        f"species={species} site={site} reason={reason} "
        f"temperature_K={temperature_K:.3f}"
    )


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
