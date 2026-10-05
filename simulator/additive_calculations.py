"""Simulator-owned additive stoichiometry."""

from __future__ import annotations


SHUTTLE_LOSS_FRACTION = 0.25
ADDITIVE_MASS_MARGIN = 1.2


def k_shuttle_potassium_additive_kg(
    ferrous_oxide_kg: float,
    ferric_oxide_kg: float = 0.0,
) -> float:
    """Return potassium charge for the C3 shuttle, including oxide losses."""
    potassium_for_ferrous = ferrous_oxide_kg * (2.0 * 39.10 / 71.84)
    potassium_for_ferric = ferric_oxide_kg * (6.0 * 39.10 / 159.687)
    return (
        (potassium_for_ferrous + potassium_for_ferric)
        * SHUTTLE_LOSS_FRACTION
        * ADDITIVE_MASS_MARGIN
    )
