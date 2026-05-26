"""Frozen data-transfer objects exchanged between planner and providers.

These mirror the on-the-wire shape declared in
``docs-private/chemistry-engine-refactor-plan-2026-05-10.md`` §"Chemistry
Kernel API".  All values are immutable -- a provider must not mutate a
:class:`ProviderAccountView` it receives, and the kernel returns frozen
:class:`IntentResult` instances to its caller.

Account / species amounts are MOL.  The simulator is mol-native (see
``AGENTS.md`` invariant #1); kg conversions happen only at the legacy
projection boundary, never inside the kernel.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Optional

from simulator.chemistry.kernel.capabilities import ChemistryIntent


def _freeze_nested_mol(
    data: Mapping[str, Mapping[str, float]],
) -> Mapping[str, Mapping[str, float]]:
    """Return a read-only copy of an account -> species_mol mapping."""

    frozen: dict[str, Mapping[str, float]] = {}
    for account, species_mol in dict(data or {}).items():
        cleaned = {
            str(species): float(value)
            for species, value in dict(species_mol or {}).items()
        }
        frozen[str(account)] = MappingProxyType(cleaned)
    return MappingProxyType(frozen)


def _freeze_atom_balance(data: Mapping[str, float]) -> Mapping[str, float]:
    cleaned = {str(element): float(value) for element, value in dict(data or {}).items()}
    return MappingProxyType(cleaned)


def _freeze_str_any(data: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(data or {}))


@dataclass(frozen=True)
class ProviderAccountView:
    """Filtered view of the ledger a provider is allowed to see.

    Constructed by :func:`simulator.chemistry.kernel.account_filters.
    build_provider_account_view` from an :class:`AtomLedger` snapshot and
    the provider's :class:`CapabilityProfile`.  Accounts outside the
    provider's ``declared_accounts`` set NEVER appear here -- that is the
    hard invariant the account-filter test enforces.
    """

    accounts: Mapping[str, Mapping[str, float]]
    species_formula_registry: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "accounts", _freeze_nested_mol(self.accounts))
        object.__setattr__(
            self,
            "species_formula_registry",
            _freeze_str_any(self.species_formula_registry),
        )


@dataclass(frozen=True)
class LedgerTransitionProposal:
    """A balanced debit / credit pair proposed -- not yet committed.

    Mirrors the on-wire shape of
    :class:`simulator.accounting.ledger.LedgerTransition` but is
    explicitly *proposed*: only :class:`ChemistryKernel.commit_batch`
    may translate it into a real :class:`LedgerTransition` and apply it
    to the ledger.  ``debits`` and ``credits`` are
    ``account -> species_mol -> amount`` dicts.  ``atom_balance_proof``
    records the net element-by-element atom count the provider asserts
    is zero; the kernel re-checks this on commit.
    """

    debits: Mapping[str, Mapping[str, float]]
    credits: Mapping[str, Mapping[str, float]]
    reason: str = ""
    # Optional provider self-check: element -> claimed net credit-debit
    # mol.  :func:`validate_atom_balance` no-ops if empty; populated
    # entries are cross-checked against the kernel's computed atom
    # totals within :data:`PROOF_CROSSCHECK_TOLERANCE_MOL`.
    atom_balance_proof: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "debits", _freeze_nested_mol(self.debits))
        object.__setattr__(self, "credits", _freeze_nested_mol(self.credits))
        object.__setattr__(self, "reason", str(self.reason or ""))
        object.__setattr__(
            self,
            "atom_balance_proof",
            _freeze_atom_balance(self.atom_balance_proof),
        )

    def accounts_touched(self) -> frozenset[str]:
        """All accounts referenced on either side of the proposal."""

        return frozenset(self.debits) | frozenset(self.credits)


@dataclass(frozen=True)
class ControlAudit:
    """Requested vs applied T / P / fO2 (and any other controls).

    ``requested`` is what the request asked for; ``applied`` is what the
    engine actually used.  ``notes`` carries free-form explanations for
    intentional deviations (e.g. "P clamped to engine minimum 1e-6 bar").
    The kernel validator demands ``requested == applied`` within
    tolerance OR a non-empty ``notes`` entry.
    """

    requested: Mapping[str, Any]
    applied: Mapping[str, Any]
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested", _freeze_str_any(self.requested))
        object.__setattr__(self, "applied", _freeze_str_any(self.applied))
        object.__setattr__(self, "notes", tuple(str(n) for n in self.notes))


@dataclass(frozen=True)
class IntentRequest:
    """Frozen request a provider receives via :meth:`ChemistryProvider.dispatch`.

    Built by :class:`ChemistryKernel` from a ledger snapshot plus the
    caller's T / P / fO2 / control inputs.  ``account_view`` has already
    been filtered against the provider's
    :class:`CapabilityProfile.declared_accounts`.
    """

    intent: ChemistryIntent
    account_view: ProviderAccountView
    temperature_C: float
    pressure_bar: float
    fO2_log: Optional[float] = None
    fe_redox_policy: str = "intrinsic"
    control_inputs: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.intent, ChemistryIntent):
            raise TypeError("IntentRequest.intent must be a ChemistryIntent")
        if not isinstance(self.account_view, ProviderAccountView):
            raise TypeError("IntentRequest.account_view must be a ProviderAccountView")
        object.__setattr__(self, "temperature_C", float(self.temperature_C))
        object.__setattr__(self, "pressure_bar", float(self.pressure_bar))
        if self.fO2_log is not None:
            object.__setattr__(self, "fO2_log", float(self.fO2_log))
        object.__setattr__(self, "fe_redox_policy", str(self.fe_redox_policy))
        object.__setattr__(self, "control_inputs", _freeze_str_any(self.control_inputs))


@dataclass(frozen=True)
class IntentResult:
    """Provider response to an :class:`IntentRequest`.

    ``transition`` is ``None`` for diagnostic / shadow results; only
    authoritative providers populate it.  ``diagnostic`` is free-form
    metadata for trace and UI (phases present, liquidus margin, parity
    deltas, ...).  ``status`` follows the planner-level vocabulary:
    ``ok`` / ``refused`` / ``not_converged`` / ``out_of_domain`` /
    ``unavailable`` / ``unsupported``.  ``refused`` is a policy refusal:
    dispatch met the provider, but the request violates a physics/regime gate
    (for example, reductant margin <= 0).
    """

    intent: ChemistryIntent
    status: str
    transition: Optional[LedgerTransitionProposal] = None
    control_audit: Optional[ControlAudit] = None
    diagnostic: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.intent, ChemistryIntent):
            raise TypeError("IntentResult.intent must be a ChemistryIntent")
        object.__setattr__(self, "status", str(self.status))
        object.__setattr__(self, "diagnostic", _freeze_str_any(self.diagnostic))
        object.__setattr__(self, "warnings", tuple(str(w) for w in self.warnings))
