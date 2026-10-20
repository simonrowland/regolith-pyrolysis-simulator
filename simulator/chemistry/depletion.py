"""Finite-inventory first-order evaporation kinetics."""

from __future__ import annotations

import math


def _finite_parent_debit(
    available_parent: float, requested_parent_rate: float, dt: float,
) -> float:
    """Integrate a frozen first-order parent demand over one interval.

    Premise: dn/dt = -k*n, with k = requested_parent_rate / available_parent.
    Thus debit = n*(1-exp(-k*dt)); expm1 preserves small k*dt. Inventory
    and rate must use the same amount unit (kg or mol), and rate and dt
    reciprocal time units. Zero demand transfers nothing; large k*dt
    approaches the available inventory without exceeding it.
    """
    k = requested_parent_rate / available_parent
    fraction = -math.expm1(-k * dt)
    fraction = max(0.0, min(math.nextafter(1.0, 0.0), fraction))
    return available_parent * fraction
