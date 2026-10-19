"""Shared operations on account-to-species amount views."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def merge_species_amounts_from_accounts(
    balances: Mapping[str, Mapping[str, float]],
    accounts: Iterable[str],
) -> dict[str, float]:
    """Sum selected accounts in order; all amounts must use the same unit."""
    merged: dict[str, float] = {}
    for account in accounts:
        for species, value in (balances.get(account, {}) or {}).items():
            amount = float(value)
            if amount:
                merged[species] = merged.get(species, 0.0) + amount
    return merged
