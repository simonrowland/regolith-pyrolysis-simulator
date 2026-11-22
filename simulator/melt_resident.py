"""Melt-resident oxide inventory shared by projection and evaporation.

Na2O in the melt is Na2O. The alkali shuttle keeps a separate ledger
account so yield completeness does not count it as feedstock. The melt
projection and the evaporation stock both read the same two accounts.
"""

from __future__ import annotations

from collections.abc import Mapping

from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT

CLEANED_MELT_ACCOUNT = "process.cleaned_melt"
# Order is the debit contract: cleaned melt, then spent-reductant residue.
MELT_RESIDENT_OXIDE_ACCOUNTS = (
    CLEANED_MELT_ACCOUNT,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)


def melt_resident_species_kg(
    kg_by_account: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    """Sum kilograms of every species across the melt-resident accounts.

    The first account's values are copied with ``float``. Each later
    account adds every key it carries, including an explicit 0.0, because
    the melt projection did ``projected.get(species, 0.0) + float(kg)``.
    A missing account, or an account with no keys, leaves the earlier
    values unchanged. With no residue the result is the cleaned-melt map.
    """

    totals: dict[str, float] = {}
    copied = False
    for account in MELT_RESIDENT_OXIDE_ACCOUNTS:
        holding = kg_by_account.get(account)
        if holding is None:
            continue
        if not copied:
            totals = {
                str(species): float(kg)
                for species, kg in holding.items()
            }
            copied = True
            continue
        for species, raw in holding.items():
            key = str(species)
            extra = float(raw)
            if key in totals:
                totals[key] = totals[key] + extra
            else:
                totals[key] = 0.0 + extra
    return totals


def melt_resident_parent_kg(
    kg_by_account: Mapping[str, Mapping[str, float]],
    species: str,
) -> float:
    """Kilograms of one species across the melt-resident accounts.

    The first account that holds the key supplies its float. A later
    account is added only when its value is not 0.0, so a missing residue
    key or an explicit zero does not change the cleaned-melt float.
    Neither account holding the key returns 0.0.
    """

    total: float | None = None
    for account in MELT_RESIDENT_OXIDE_ACCOUNTS:
        holding = kg_by_account.get(account)
        if not holding or species not in holding:
            continue
        value = float(holding[species])
        if total is None:
            total = value
        elif value != 0.0:
            total = total + value
    if total is None:
        return 0.0
    return total
