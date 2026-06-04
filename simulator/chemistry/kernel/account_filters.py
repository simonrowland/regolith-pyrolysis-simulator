"""Pre-call account filter for chemistry providers.

A provider declares the AtomLedger accounts it expects to read via
:class:`CapabilityProfile.declared_accounts`.  The kernel constructs a
:class:`ProviderAccountView` containing ONLY those accounts before
handing the request to the provider; every other account is dropped.

The undeclared-accounts-never-cross rule is the strongest isolation
property the kernel offers (see binding-spec §7 -- "VapoRock receiving
metal/sulfide/salt accounts. Filter at entry.").  Violations raise
:class:`AccountFilterViolation` from
``simulator.chemistry.kernel.errors``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.kernel.errors import AccountFilterViolation, KernelError


def build_provider_account_view(
    ledger: AtomLedger,
    declared_accounts: frozenset[str],
    species_formula_registry: Mapping[str, Any],
    account_mol_overrides: Mapping[str, Mapping[str, float]] | None = None,
) -> ProviderAccountView:
    """Build a provider-scoped read-only view of ``ledger``.

    Args:
        ledger: The canonical :class:`AtomLedger` whose mol balances back
            the simulator state.
        declared_accounts: The provider's
            :class:`CapabilityProfile.declared_accounts` -- the only
            account names whose balances may cross into the provider.
        species_formula_registry: Per-species formula objects the
            provider may need to do its own atom-balance bookkeeping.

    Returns:
        A frozen :class:`ProviderAccountView` containing only the
        declared accounts.  Undeclared accounts NEVER appear in the
        view, even if the ledger currently holds material in them.

    Raises:
        KernelError: ``declared_accounts`` is empty.  A provider that
            requests no accounts cannot meaningfully be handed any
            state; this is a registration / configuration error, not
            a cross-account read/write violation, so it surfaces as
            the broader :class:`KernelError` rather than
            :class:`AccountFilterViolation` (which is reserved for an
            authorised provider actually touching an undeclared
            account).
    """

    if not isinstance(declared_accounts, frozenset):
        declared_accounts = frozenset(declared_accounts or ())
    if not declared_accounts:
        raise KernelError(
            "provider declared no accounts; cannot build a non-empty ProviderAccountView"
        )

    snapshot = ledger.mol_by_account()
    if not isinstance(snapshot, Mapping):  # defensive: ledger contract says dict
        raise AccountFilterViolation(
            "ledger.mol_by_account() did not return a mapping; cannot filter"
        )

    overrides: dict[str, dict[str, float]] = {}
    if account_mol_overrides is not None:
        overrides = {
            str(account): {
                str(species): float(value)
                for species, value in dict(species_mol or {}).items()
            }
            for account, species_mol in dict(account_mol_overrides).items()
        }

    filtered: dict[str, dict[str, float]] = {}
    for account in declared_accounts:
        species_mol = (
            overrides[account]
            if account in overrides
            else snapshot.get(account, {})
        )
        if species_mol:
            filtered[account] = {
                str(species): float(value)
                for species, value in dict(species_mol).items()
            }
        else:
            # Preserve the declared account name even when empty so the
            # provider can iterate its declared set without conditional
            # lookups.
            filtered[account] = {}

    return ProviderAccountView(
        accounts=filtered,
        species_formula_registry=species_formula_registry or {},
    )
