"""Simulator-owned additive stoichiometry."""

from __future__ import annotations


def k_shuttle_potassium_additive_kg(
    ferrous_oxide_kg: float,
    ferric_oxide_kg: float = 0.0,
) -> float:
    """Return potassium charge for the C3 shuttle, including oxide losses."""
    potassium_for_ferrous = ferrous_oxide_kg * (2.0 * 39.10 / 71.84)
    potassium_for_ferric = ferric_oxide_kg * (6.0 * 39.10 / 159.687)
    return (potassium_for_ferrous + potassium_for_ferric) * 0.25 * 1.2
