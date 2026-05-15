"""Kernel invariant: providers can only emit transitions for owned intents.

A provider's :class:`CapabilityProfile.is_authoritative_for` is the
authoritative set.  If a result carries a
:class:`LedgerTransitionProposal` for an intent outside that set, the
kernel raises :class:`UnauthorizedIntentError` BEFORE the proposal is
seen by the rest of the system.
"""

from __future__ import annotations

import pytest

from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.kernel import (
    CapabilityProfile,
    ChemistryIntent,
    ChemistryKernel,
    ChemistryProvider,
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
    ProviderRegistry,
    UnauthorizedIntentError,
)
from simulator.chemistry.kernel.validation import validate_intent_authority


def _balanced_proposal() -> LedgerTransitionProposal:
    return LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 1.0}},
        credits={"process.overhead_gas": {"SiO2": 1.0}},
        reason="test",
    )


# ---------------------------------------------------------------------------
# Capability-profile-level assertions


def test_capability_profile_rejects_unauthorised_subset():
    with pytest.raises(ValueError):
        CapabilityProfile(
            provider_id="bad",
            intents=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
            is_authoritative_for=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )


def test_validate_intent_authority_raises_for_outside_set():
    profile = CapabilityProfile(
        provider_id="shadow",
        intents=frozenset({ChemistryIntent.SILICATE_EQUILIBRIUM}),
        is_authoritative_for=frozenset(),  # diagnostic only
        declared_accounts=frozenset({"process.cleaned_melt"}),
    )
    with pytest.raises(UnauthorizedIntentError):
        validate_intent_authority(ChemistryIntent.SILICATE_EQUILIBRIUM, profile)


def test_validate_intent_authority_allows_owned_intent():
    profile = CapabilityProfile(
        provider_id="owner",
        intents=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
        is_authoritative_for=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
        declared_accounts=frozenset({"process.cleaned_melt", "process.overhead_gas"}),
    )
    validate_intent_authority(ChemistryIntent.EVAPORATION_TRANSITION, profile)


# ---------------------------------------------------------------------------
# Kernel-level: a shadow provider's transition is rejected when committed
# via the kernel dispatch path.


class _DiagnosticOnlyProvider(ChemistryProvider):
    """A provider that wrongly tries to emit a transition.

    Its :class:`CapabilityProfile.is_authoritative_for` is empty (it
    declares itself diagnostic-only), but its :meth:`dispatch`
    returns a populated :attr:`IntentResult.transition`.  The kernel
    must reject this with :class:`UnauthorizedIntentError`.
    """

    name = "buggy_diagnostic"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="buggy_diagnostic",
            intents=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
            is_authoritative_for=frozenset(),  # diagnostic only
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=_balanced_proposal(),
            control_audit=None,
            diagnostic={},
            warnings=(),
        )


def test_kernel_rejects_diagnostic_provider_emitting_transition_at_registration():
    ledger = AtomLedger()
    registry = ProviderRegistry()
    provider = _DiagnosticOnlyProvider()
    # Diagnostic providers cannot be registered as authoritative.
    with pytest.raises(Exception):
        registry.register(provider, [ChemistryIntent.SILICATE_LIQUIDUS])


class _MisalignedAuthorityProvider(ChemistryProvider):
    """A provider whose profile drifts: dispatched intent is not in its authority set.

    Tests the runtime gate: even if the registry accidentally let it
    register (e.g. via a buggy patch), :meth:`ChemistryKernel.dispatch`
    must still reject the transition because the profile's
    ``is_authoritative_for`` set excludes the requested intent.
    """

    name = "drifting_authority"

    def __init__(self, *, mismatch: bool) -> None:
        self._mismatch = mismatch

    def capability_profile(self) -> CapabilityProfile:
        if self._mismatch:
            return CapabilityProfile(
                provider_id="drifting_authority",
                intents=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
                is_authoritative_for=frozenset(),  # drifted
                declared_accounts=frozenset({"process.cleaned_melt"}),
            )
        return CapabilityProfile(
            provider_id="drifting_authority",
            intents=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
            is_authoritative_for=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=_balanced_proposal(),
            control_audit=None,
            diagnostic={},
            warnings=(),
        )


def test_kernel_dispatch_rejects_transition_when_provider_authority_drifts(monkeypatch):
    """Register with valid authority, then mutate profile to simulate drift."""

    ledger = AtomLedger()
    ledger.load_external("process.cleaned_melt", {"SiO2": 5.0})
    registry = ProviderRegistry()
    provider = _MisalignedAuthorityProvider(mismatch=False)
    registry.register(provider, [ChemistryIntent.SILICATE_LIQUIDUS])
    # Now flip the provider's profile to declare zero authority.  This
    # exercises the runtime check in :meth:`ChemistryKernel.dispatch`.
    provider._mismatch = True
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    with pytest.raises(UnauthorizedIntentError):
        kernel.dispatch(
            ChemistryIntent.SILICATE_LIQUIDUS,
            temperature_C=1400.0,
            pressure_bar=1.0,
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )
