"""Planner and kernel orchestrator.

:class:`Planner` walks the :class:`ProviderRegistry` for a single
:class:`IntentRequest`: it runs the authoritative provider and every
shadow provider for that intent.  Shadow results are captured into the
trace but their proposals are NEVER applied.

:class:`ChemistryKernel` is the top-level entry point.  It:

* builds a :class:`ProviderAccountView` from the live :class:`AtomLedger`
  (using :func:`build_provider_account_view`),
* constructs an :class:`IntentRequest`,
* dispatches it through the :class:`Planner`,
* runs the validation suite (intent authority, account filter, atom
  balance, control audit) against the authoritative result, and
* exposes the SOLE writable path into the ledger via
  :meth:`commit_batch`.

Providers see only frozen DTOs.  They never receive an ``AtomLedger``
reference.  This is the invariant covered by
``tests/chemistry/test_kernel_commit_batch.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from simulator.accounting.ledger import AtomLedger, LedgerTransition
from simulator.chemistry.kernel.account_filters import build_provider_account_view
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import (
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
    ProviderAccountView,
)
from simulator.chemistry.kernel.errors import (
    KernelError,
    ProposalRejected,
    ProviderUnavailableError,
)
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.chemistry.kernel.registry import ProviderRegistry
from simulator.chemistry.kernel.validation import (
    _proposal_to_ledger_transition,
    validate_atom_balance,
    validate_control_audit,
    validate_intent_authority,
    validate_proposal_accounts,
)


class Planner:
    """Routes a single :class:`IntentRequest` to authoritative + shadows."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry
        self._shadow_trace: list[dict[str, Any]] = []

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    @property
    def shadow_trace(self) -> tuple[dict[str, Any], ...]:
        """Tuple of ``{provider_id, intent, result}`` records.

        Populated by every call to :meth:`dispatch`; only shadow
        provider results are recorded here -- the authoritative result
        is the return value.
        """

        return tuple(self._shadow_trace)

    def dispatch(self, request: IntentRequest) -> IntentResult:
        """Run the authoritative + shadow providers for ``request``.

        Returns the authoritative provider's :class:`IntentResult`.
        Shadow results are appended to :attr:`shadow_trace` for the
        caller (typically :class:`ChemistryKernel`) to surface.

        Raises:
            ProviderUnavailableError: No authoritative provider is
                registered for ``request.intent``.
        """

        authoritative = self._registry.authoritative_for(request.intent)
        if authoritative is None:
            raise ProviderUnavailableError(
                f"no authoritative provider registered for intent {request.intent.value!r}"
            )

        for shadow in self._registry.shadows_for(request.intent):
            shadow_result = shadow.dispatch(request)
            self._shadow_trace.append(
                {
                    "provider_id": shadow.capability_profile().provider_id,
                    "intent": request.intent.value,
                    "result": shadow_result,
                }
            )
            # Shadow proposals NEVER apply.  We don't even pass them
            # along.  If the shadow accidentally tried to set a
            # transition, that data is captured here for parity
            # logging but the kernel will reject it on its own commit
            # path because the shadow isn't registered as
            # authoritative.

        return authoritative.dispatch(request)


class ChemistryKernel:
    """Top-level kernel: filters, dispatches, validates, commits.

    The kernel owns the only writable path into the
    :class:`AtomLedger` -- :meth:`commit_batch`.  Providers receive
    only :class:`IntentRequest` instances (whose
    :class:`ProviderAccountView` is already filtered), and may return
    :class:`IntentResult` instances; they have no way to reach the
    ledger directly.
    """

    def __init__(
        self,
        ledger: AtomLedger,
        registry: ProviderRegistry,
        species_formula_registry: Mapping[str, Any],
    ) -> None:
        self._ledger = ledger
        self._registry = registry
        self._species_formula_registry = dict(species_formula_registry or {})
        self._planner = Planner(registry)

    @property
    def planner(self) -> Planner:
        return self._planner

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    @property
    def species_formula_registry(self) -> Mapping[str, Any]:
        return dict(self._species_formula_registry)

    def dispatch(
        self,
        intent: ChemistryIntent,
        *,
        temperature_C: float,
        pressure_bar: float,
        fO2_log: Optional[float] = None,
        control_inputs: Optional[Mapping[str, Any]] = None,
        declared_accounts: Optional[frozenset[str]] = None,
    ) -> IntentResult:
        """Orchestrate one chemistry intent end-to-end.

        Args:
            intent: The :class:`ChemistryIntent` to dispatch.
            temperature_C, pressure_bar, fO2_log: Engine controls.
            control_inputs: Extra control payload routed through to the
                provider unchanged.
            declared_accounts: Override the authoritative provider's
                declared-accounts set; defaults to whatever the
                authoritative provider declares in its
                :class:`CapabilityProfile`.

        Returns the authoritative provider's :class:`IntentResult`
        AFTER it has passed every kernel validator.

        Raises:
            ProviderUnavailableError: No authoritative provider for
                ``intent``.
            UnauthorizedIntentError, AccountFilterViolation,
            AtomBalanceError, ControlAuditMismatch: One of the
            validators rejected the authoritative result.
        """

        provider = self._registry.authoritative_for(intent)
        if provider is None:
            raise ProviderUnavailableError(
                f"no authoritative provider registered for intent {intent.value!r}"
            )

        profile = provider.capability_profile()
        if declared_accounts is None:
            declared_accounts = profile.declared_accounts
        declared_accounts = frozenset(declared_accounts or ())

        account_view = build_provider_account_view(
            self._ledger,
            declared_accounts,
            self._species_formula_registry,
        )
        request = IntentRequest(
            intent=intent,
            account_view=account_view,
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            control_inputs=control_inputs or {},
        )
        result = self._planner.dispatch(request)

        if result.intent != intent:
            raise KernelError(
                f"provider {profile.provider_id!r} returned intent "
                f"{result.intent.value!r} for request {intent.value!r}"
            )

        if result.transition is not None:
            validate_intent_authority(intent, profile)
            validate_proposal_accounts(result.transition, declared_accounts)
            validate_atom_balance(result.transition, self._species_formula_registry)
        if result.control_audit is not None:
            validate_control_audit(result.control_audit, request)

        return result

    def commit_batch(self, proposal: LedgerTransitionProposal) -> LedgerTransition:
        """Apply ``proposal`` to the ledger -- the ONLY writable path.

        Re-runs the atom-balance validator against the current registry
        (defence in depth: even if a provider produced a balanced
        proposal, an out-of-band registry change between dispatch and
        commit must not slip through), translates the proposal into a
        canonical :class:`LedgerTransition`, and applies it via
        :meth:`AtomLedger.apply`.

        Returns the applied :class:`LedgerTransition` so callers can
        record it in their trace.  Raises :class:`ProposalRejected` on
        any pre-commit failure.
        """

        try:
            validate_atom_balance(proposal, self._species_formula_registry)
        except KernelError:
            raise
        except Exception as exc:  # noqa: BLE001 -- surface as ProposalRejected
            raise ProposalRejected(str(exc)) from exc

        transition = _proposal_to_ledger_transition(
            proposal, self._species_formula_registry
        )
        try:
            return self._ledger.apply(transition)
        except Exception as exc:  # noqa: BLE001
            raise ProposalRejected(str(exc)) from exc
