"""Melt-resident oxide inventory shared by projection and evaporation.

Na2O in the melt is Na2O. The alkali shuttle keeps a separate ledger
account so yield completeness does not count it as feedstock. The melt
projection and the evaporation stock both read the same two accounts.
"""

from __future__ import annotations

import math
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


def split_parent_debit_mol(
    oxide_mol: float,
    holdings: Mapping[str, float],
) -> dict[str, float]:
    """Split one parent-oxide debit across the melt-resident accounts.

    The evaporation rate is one number for the species in the melt. Each
    melt-resident account holds a share of that species, and the debit
    removes that share:

        debit_mol(account) = oxide_mol * holding(account) / sum(holdings)

    for every account whose holding of this species is positive. The last
    positive account, in ``MELT_RESIDENT_OXIDE_ACCOUNTS`` order, receives
    ``oxide_mol - assigned`` so the parts sum to ``oxide_mol`` exactly.
    One ulp of remainder stays inside the 5e-12 % closure.

    Mass is conserved because every part is the same species and therefore
    the same molar mass. The credits are unchanged.
    ``sum(debit_kg) = oxide_mol * M = oxide_removed``. Origin travels with
    the account: feedstock Na stays feedstock and shuttle reagent Na stays
    reagent. The accounts are not merged, so yield completeness still does
    not count the residue as feedstock.

    Limiting case: no residue, or the residue holding of this species is
    not positive. The only positive account is ``process.cleaned_melt``,
    and the returned debit is the original ``oxide_mol`` float with no
    multiply. A run with no shuttle is byte-identical to today's
    single-account debit. No positive holding returns an empty map; the
    caller keeps the historical cleaned-melt debit when the view names
    no stock.
    """

    if not math.isfinite(oxide_mol) or oxide_mol <= 0.0:
        raise ValueError(
            "parent debit mol must be a positive finite amount, "
            f"got {oxide_mol!r}"
        )
    positive: list[tuple[str, float]] = []
    for account in MELT_RESIDENT_OXIDE_ACCOUNTS:
        if account not in holdings:
            continue
        amount = float(holdings[account])
        if not math.isfinite(amount):
            raise ValueError(
                "melt-resident holding must be finite, "
                f"account={account!r} amount={amount!r}"
            )
        if amount > 0.0:
            positive.append((account, amount))
    if not positive:
        return {}
    if (
        len(positive) == 1
        and positive[0][0] == CLEANED_MELT_ACCOUNT
    ):
        return {CLEANED_MELT_ACCOUNT: oxide_mol}
    total = sum(amount for _account, amount in positive)
    assigned = 0.0
    debits: dict[str, float] = {}
    last = len(positive) - 1
    for index, (account, amount) in enumerate(positive):
        if index == last:
            debits[account] = oxide_mol - assigned
        else:
            part = oxide_mol * (amount / total)
            debits[account] = part
            assigned = assigned + part
    return debits
