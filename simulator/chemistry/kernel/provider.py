"""Abstract base for every chemistry provider plugged into the kernel.

A :class:`ChemistryProvider` is registered with a :class:`ProviderRegistry`
against one or more :class:`ChemistryIntent` values.  When the planner
dispatches a request, the provider receives an :class:`IntentRequest`
whose :attr:`account_view` has already been filtered against the
provider's :class:`CapabilityProfile.declared_accounts`.  The provider
returns an :class:`IntentResult` -- with a transition proposal if (and
only if) it is authoritative for the requested intent.

Providers MUST NOT mutate the :class:`AtomLedger` directly.  The kernel
holds the only commit path (:meth:`ChemistryKernel.commit_batch`); this
ABC carries no ledger reference and exposes none.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from simulator.chemistry.kernel.capabilities import CapabilityProfile, ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult


class ChemistryProvider(ABC):
    """Provider contract: declare capabilities, dispatch intents."""

    #: Stable provider identifier (override in subclasses or set at construction).
    name: str = "anonymous"

    @abstractmethod
    def capability_profile(self) -> CapabilityProfile:
        """Return this provider's :class:`CapabilityProfile`.

        The kernel calls this at registration time AND again before
        every dispatch -- providers must return a stable profile.
        Changing the declared set at runtime is a kernel violation.
        """

    @abstractmethod
    def dispatch(self, request: IntentRequest) -> IntentResult:
        """Execute the requested intent and return a frozen result.

        Authoritative providers populate
        :attr:`IntentResult.transition` with a
        :class:`LedgerTransitionProposal`.  Shadow / diagnostic
        providers leave it ``None``.  The kernel validates the result
        before applying anything to the ledger.
        """

    def emits_ledger_transition(self, intent: ChemistryIntent) -> bool:
        """Whether ``intent`` is one this provider may commit transitions for.

        Default: membership in the capability profile's
        ``ledger_transition_authority_for`` set.
        Subclasses may override for narrower per-call gating (e.g. an
        adapter that is authoritative on lunar feedstocks but only
        diagnostic on Mars feedstocks) but must NEVER return ``True``
        for an intent outside its declared ledger-transition authority.
        """

        profile = self.capability_profile()
        return profile.may_emit_ledger_transition(intent)
