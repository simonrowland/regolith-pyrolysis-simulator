"""Canonical printed-oxygen identity pressure.

Migration, prediction-identity construction, and comparison all use this
conversion, so one printed log becomes one ``identity.fO2_Pa``. Comparison
does not convert again: it checks exact equality of states this function
built. A sibling that writes a resolved log into a comparison identity
imports this module. It is a leaf: it does not import the migrator or the
scorer.

Predictor inputs stay the printed log. This function is only the pascal
identity.
"""

from __future__ import annotations

from decimal import Decimal

# 1 bar = 1e5 Pa exactly. An atmosphere frame passes 101325 Pa instead.
BAR_PA = Decimal("100000")


def log10_fo2_to_pa(
    log10_fO2: Decimal, reference_pressure_pa: Decimal = BAR_PA
) -> Decimal:
    """log10 fO2 (bar) -> Pa = 10^x × 1e5.

    An atm frame uses 101325 Pa instead of 1e5. Callers pass that reference.
    Unit check: 10^x is dimensionless, so 10^x × Pa is Pa.
    Sanity: log10 fO2 = -9.56 (bar) -> 2.754e-5 Pa
    (1e5 × 10^-9.56 = 0.00002754228703338166448631210659 Pa).
    """

    return reference_pressure_pa * (Decimal(10) ** log10_fO2)
