"""Kernel invariant: planner routes to authoritative, runs shadows, rejects unknowns.

Tested:

* :meth:`Planner.dispatch` calls the authoritative provider and every
  shadow provider for the requested intent.
* Shadow provider proposals are recorded in the trace but NEVER
  committed.
* :class:`ProviderUnavailableError` is raised when no authoritative
  provider is registered for an intent.
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
    Planner,
    ProviderRegistry,
    ProviderUnavailableError,
)
from simulator.chemistry.kernel.dto import ProviderAccountView


def _make_request(intent: ChemistryIntent) -> IntentRequest:
    return IntentRequest(
        intent=intent,
        account_view=ProviderAccountView(accounts={}, species_formula_registry={}),
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=None,
        control_inputs={},
    )


class _AuthoritativeProvider(ChemistryProvider):
    name = "auth"

    def __init__(self) -> None:
        self.call_count = 0

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="auth",
            intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            is_authoritative_for=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.call_count += 1
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=None,
            control_audit=None,
            diagnostic={"source": "authoritative"},
            warnings=(),
        )


class _ShadowProvider(ChemistryProvider):
    name = "shadow"

    def __init__(self) -> None:
        self.call_count = 0

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="shadow",
            intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            is_authoritative_for=frozenset(),  # diagnostic only
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.call_count += 1
        # Even if a shadow ATTEMPTS a transition, the planner never
        # routes it to commit.
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": 1.0}},
                credits={"process.overhead_gas": {"SiO2": 1.0}},
                reason="shadow_attempt",
            ),
            control_audit=None,
            diagnostic={"source": "shadow"},
            warnings=(),
        )


def test_planner_routes_to_authoritative_and_returns_its_result():
    registry = ProviderRegistry()
    auth = _AuthoritativeProvider()
    shadow = _ShadowProvider()
    registry.register(auth, [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(shadow, [ChemistryIntent.VAPOR_PRESSURE], shadow=True)

    planner = Planner(registry)
    result = planner.dispatch(_make_request(ChemistryIntent.VAPOR_PRESSURE))

    assert auth.call_count == 1
    assert shadow.call_count == 1
    assert result.diagnostic.get("source") == "authoritative"


def test_planner_records_shadow_trace_separately():
    registry = ProviderRegistry()
    registry.register(_AuthoritativeProvider(), [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(_ShadowProvider(), [ChemistryIntent.VAPOR_PRESSURE], shadow=True)

    planner = Planner(registry)
    planner.dispatch(_make_request(ChemistryIntent.VAPOR_PRESSURE))

    trace = planner.shadow_trace
    assert len(trace) == 1
    assert trace[0]["provider_id"] == "shadow"
    # The shadow's proposal IS captured in trace, but as data, not as a
    # commit instruction.
    assert trace[0]["result"].transition is not None


def test_kernel_dispatch_does_not_commit_shadow_transitions():
    """Even though the shadow returns a transition, the ledger is untouched."""

    ledger = AtomLedger()
    ledger.load_external("process.cleaned_melt", {"SiO2": 5.0})
    before_mol = ledger.mol_by_account()
    registry = ProviderRegistry()
    registry.register(_AuthoritativeProvider(), [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(_ShadowProvider(), [ChemistryIntent.VAPOR_PRESSURE], shadow=True)
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        declared_accounts=frozenset({"process.cleaned_melt"}),
    )
    after_mol = ledger.mol_by_account()
    assert before_mol == after_mol


def test_planner_raises_when_no_authoritative_registered():
    registry = ProviderRegistry()
    planner = Planner(registry)
    with pytest.raises(ProviderUnavailableError):
        planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))


def test_kernel_dispatch_raises_when_no_authoritative_registered():
    ledger = AtomLedger()
    registry = ProviderRegistry()
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})
    with pytest.raises(ProviderUnavailableError):
        kernel.dispatch(
            ChemistryIntent.SILICATE_LIQUIDUS,
            temperature_C=1400.0,
            pressure_bar=1.0,
        )


def test_registry_rejects_double_authoritative_registration():
    """At most one authoritative provider per intent."""

    registry = ProviderRegistry()
    registry.register(_AuthoritativeProvider(), [ChemistryIntent.VAPOR_PRESSURE])
    with pytest.raises(Exception):
        registry.register(
            _AuthoritativeProvider(),
            [ChemistryIntent.VAPOR_PRESSURE],
        )


def test_registry_allows_multiple_shadows():
    registry = ProviderRegistry()
    registry.register(_AuthoritativeProvider(), [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(_ShadowProvider(), [ChemistryIntent.VAPOR_PRESSURE], shadow=True)
    registry.register(_ShadowProvider(), [ChemistryIntent.VAPOR_PRESSURE], shadow=True)
    shadows = registry.shadows_for(ChemistryIntent.VAPOR_PRESSURE)
    assert len(shadows) == 2
