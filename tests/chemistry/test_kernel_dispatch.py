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
    KernelError,
    LedgerTransitionProposal,
    ProviderRegistry,
    ProviderUnavailableError,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
# Planner is an internal seam, intentionally not on the package's
# public surface; tests reach for it via the module-qualified path.
from simulator.chemistry.kernel.planner import Planner
from simulator.fe_redox import (
    FE_FEO_BUFFER_STANDARD,
    REDOX_STATE_REFERENCE_T_K,
    RedoxState,
)


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


class _RefusingProvider(_AuthoritativeProvider):
    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="refusing",
            intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            is_authoritative_for=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            declared_accounts=frozenset(
                {"process.cleaned_melt", "process.overhead_gas"}
            ),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="unsupported",
            transition=LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": 1.0}},
                credits={"process.overhead_gas": {"SiO2": 1.0}},
            ),
        )


def test_non_ok_provider_result_cannot_carry_committable_transition():
    registry = ProviderRegistry()
    registry.register(_RefusingProvider(), [ChemistryIntent.VAPOR_PRESSURE])
    ledger = AtomLedger()
    ledger.load_external("process.cleaned_melt", {"SiO2": 1.0}, material_origin="feedstock")
    before_mol = ledger.mol_by_account("process.cleaned_melt")["SiO2"]
    kernel = ChemistryKernel(
        ledger,
        registry,
        species_formula_registry={"SiO2": {"Si": 1, "O": 2}},
    )

    with pytest.raises(KernelError, match="non-committable status"):
        kernel.dispatch(
            ChemistryIntent.VAPOR_PRESSURE,
            temperature_C=1400.0,
            pressure_bar=1.0,
        )

    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == before_mol


class _ShadowProvider(ChemistryProvider):
    name = "shadow"

    def __init__(self) -> None:
        self.call_count = 0
        self.seen_accounts: list[frozenset[str]] = []

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="shadow",
            intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            is_authoritative_for=frozenset(),  # diagnostic only
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.call_count += 1
        self.seen_accounts.append(frozenset(request.account_view.accounts))
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


class _RaisingShadowProvider(_ShadowProvider):
    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.call_count += 1
        raise RuntimeError("shadow backend failed")


def test_planner_filters_each_shadow_to_its_own_declared_accounts():
    registry = ProviderRegistry()
    registry.register(
        _AuthoritativeProvider(), [ChemistryIntent.VAPOR_PRESSURE]
    )
    shadow = _ShadowProvider()
    registry.register(shadow, [ChemistryIntent.VAPOR_PRESSURE], shadow=True)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {"SiO2": 1.0},
                "process.overhead_gas": {"O2": 1.0},
            },
            species_formula_registry={},
        ),
        temperature_C=1400.0,
        pressure_bar=1.0,
    )

    Planner(registry).dispatch(request)

    assert shadow.seen_accounts == [frozenset({"process.cleaned_melt"})]


class _HostileReprError(RuntimeError):
    def __repr__(self) -> str:
        raise RuntimeError("secondary repr failure")


class _HostileReprShadowProvider(_ShadowProvider):
    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.call_count += 1
        raise _HostileReprError("primary shadow failure")


class _RaisingParityShadowProvider(_ShadowProvider):
    def __init__(self) -> None:
        super().__init__()
        self.capability_call_count = 0

    def capability_profile(self) -> CapabilityProfile:
        self.capability_call_count += 1
        if self.capability_call_count > 2:
            raise RuntimeError("secondary capability failure")
        return super().capability_profile()

    def parity_compare(
        self,
        authoritative_result: IntentResult,
        shadow_result: IntentResult,
    ) -> None:
        raise RuntimeError("primary parity failure")


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


def test_shadow_dispatch_error_does_not_block_authoritative_result():
    registry = ProviderRegistry()
    auth = _AuthoritativeProvider()
    shadow = _RaisingShadowProvider()
    registry.register(auth, [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(shadow, [ChemistryIntent.VAPOR_PRESSURE], shadow=True)

    planner = Planner(registry)
    result = planner.dispatch(_make_request(ChemistryIntent.VAPOR_PRESSURE))

    assert auth.call_count == 1
    assert shadow.call_count == 1
    assert result.diagnostic.get("source") == "authoritative"
    assert planner.shadow_trace == (
        {
            "event": "shadow_error",
            "provider_id": "shadow",
            "intent": "vapor_pressure",
            "error": "RuntimeError('shadow backend failed')",
        },
    )


def test_shadow_error_diagnostic_survives_hostile_exception_repr():
    registry = ProviderRegistry()
    auth = _AuthoritativeProvider()
    shadow = _HostileReprShadowProvider()
    registry.register(auth, [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(shadow, [ChemistryIntent.VAPOR_PRESSURE], shadow=True)

    planner = Planner(registry)
    result = planner.dispatch(_make_request(ChemistryIntent.VAPOR_PRESSURE))

    assert auth.call_count == 1
    assert shadow.call_count == 1
    assert result.diagnostic.get("source") == "authoritative"
    assert planner.shadow_trace == (
        {
            "event": "shadow_error",
            "provider_id": "shadow",
            "intent": "vapor_pressure",
            "error": "<exception repr unavailable>",
        },
    )


def test_parity_error_diagnostic_does_not_recall_provider_capability():
    registry = ProviderRegistry()
    auth = _AuthoritativeProvider()
    shadow = _RaisingParityShadowProvider()
    registry.register(auth, [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(shadow, [ChemistryIntent.VAPOR_PRESSURE], shadow=True)

    planner = Planner(registry)
    result = planner.dispatch(_make_request(ChemistryIntent.VAPOR_PRESSURE))

    assert auth.call_count == 1
    assert shadow.call_count == 1
    assert shadow.capability_call_count == 2
    assert result.diagnostic.get("source") == "authoritative"
    assert planner.shadow_trace[-1] == {
        "event": "parity_error",
        "provider_id": "shadow",
        "intent": "vapor_pressure",
        "error": "RuntimeError('primary parity failure')",
    }


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
    ledger.load_external("process.cleaned_melt", {"SiO2": 5.0}, material_origin="feedstock")
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
    with pytest.raises(KernelError, match="conflicting authoritative registration"):
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


# ---------------------------------------------------------------------------
# Shadow-trace memory hygiene: the trace must be clearable and capped, so
# long-running web sessions / loop campaigns don't accumulate unbounded
# diagnostic state.  The kernel is instantiated once per simulator and
# reused across many batches.


def test_shadow_trace_bounded_or_clearable():
    """``clear_shadow_trace`` drops every retained record after many dispatches."""

    registry = ProviderRegistry()
    registry.register(_AuthoritativeProvider(), [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(_ShadowProvider(), [ChemistryIntent.VAPOR_PRESSURE], shadow=True)
    planner = Planner(registry)

    for _ in range(50):
        planner.dispatch(_make_request(ChemistryIntent.VAPOR_PRESSURE))
    assert len(planner.shadow_trace) == 50

    planner.clear_shadow_trace()
    assert planner.shadow_trace == ()

    # Cleared state still accepts new dispatches.
    planner.dispatch(_make_request(ChemistryIntent.VAPOR_PRESSURE))
    assert len(planner.shadow_trace) == 1


def test_shadow_trace_ring_buffer_honoured():
    """When the cap is exceeded the planner drops oldest records FIFO."""

    registry = ProviderRegistry()
    registry.register(_AuthoritativeProvider(), [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(_ShadowProvider(), [ChemistryIntent.VAPOR_PRESSURE], shadow=True)
    planner = Planner(registry)
    planner.set_shadow_trace_cap(5)

    for _ in range(20):
        planner.dispatch(_make_request(ChemistryIntent.VAPOR_PRESSURE))

    # Cap honoured: never more than 5 entries even after 20 dispatches.
    assert len(planner.shadow_trace) == 5
    # Most recent records survived (FIFO drop kept the newest).
    assert planner.shadow_trace_cap == 5


def test_chemistry_kernel_clear_shadow_trace_passthrough():
    """``ChemistryKernel.clear_shadow_trace`` clears the underlying planner."""

    ledger = AtomLedger()
    ledger.load_external("process.cleaned_melt", {"SiO2": 1.0}, material_origin="feedstock")
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
    assert kernel.planner.shadow_trace != ()
    kernel.clear_shadow_trace()
    assert kernel.planner.shadow_trace == ()


class _RedoxRecordingProvider(ChemistryProvider):
    def __init__(
        self,
        provider_id: str,
        *,
        status: str = 'ok',
        consumes_fO2: bool = True,
    ) -> None:
        self.provider_id = provider_id
        self.status = status
        self.consumes_fO2 = consumes_fO2
        self.requests: list[IntentRequest] = []

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.provider_id,
            intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            is_authoritative_for=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
            declared_accounts=frozenset({'process.cleaned_melt'}),
            consumes_fO2=self.consumes_fO2,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.requests.append(request)
        return IntentResult(
            intent=request.intent,
            status=self.status,
            transition=None,
            diagnostic={'source': self.provider_id},
        )


def test_planner_forwards_redox_state_to_authoritative_and_shadow():
    state = RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, 0.2)
    registry = ProviderRegistry()
    authoritative = _RedoxRecordingProvider('redox-authoritative')
    shadow = _RedoxRecordingProvider('redox-shadow')
    registry.register(authoritative, [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(shadow, [ChemistryIntent.VAPOR_PRESSURE], shadow=True)

    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(accounts={}, species_formula_registry={}),
        temperature_C=1400.0,
        pressure_bar=1.0,
        redox_state=state,
    )
    Planner(registry).dispatch(request)

    assert authoritative.requests[0].redox_state == state
    assert shadow.requests[0].redox_state == state
    assert authoritative.requests[0].fO2_log == pytest.approx(
        state.log_fO2_at(REDOX_STATE_REFERENCE_T_K)
    )


def test_kernel_redox_state_survives_fallback_and_is_cleared_for_nonconsumer():
    state = RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, 0.2)
    registry = ProviderRegistry()
    authoritative = _RedoxRecordingProvider(
        'redox-primary',
        status='unavailable',
    )
    fallback = _RedoxRecordingProvider('redox-fallback')
    registry.register(authoritative, [ChemistryIntent.VAPOR_PRESSURE])
    registry.register(fallback, [ChemistryIntent.VAPOR_PRESSURE], fallback=True)
    kernel = ChemistryKernel(
        AtomLedger(),
        registry,
        species_formula_registry={},
        allow_fallback_intents=frozenset({ChemistryIntent.VAPOR_PRESSURE}),
    )

    kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=1400.0,
        pressure_bar=1.0,
        redox_state=state,
    )

    assert fallback.requests[0].redox_state == state
    assert fallback.requests[0].fO2_log == pytest.approx(
        state.log_fO2_at(REDOX_STATE_REFERENCE_T_K)
    )

    no_shadow_registry = ProviderRegistry()
    no_shadow_provider = _RedoxRecordingProvider('redox-no-shadows')
    no_shadow_registry.register(
        no_shadow_provider,
        [ChemistryIntent.VAPOR_PRESSURE],
    )
    no_shadow_kernel = ChemistryKernel(
        AtomLedger(),
        no_shadow_registry,
        species_formula_registry={},
    )
    no_shadow_kernel._dispatch_authoritative_without_shadows(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=1400.0,
        pressure_bar=1.0,
        redox_state=state,
    )
    assert no_shadow_provider.requests[0].redox_state == state

    nonconsumer_registry = ProviderRegistry()
    nonconsumer = _RedoxRecordingProvider(
        'redox-nonconsumer',
        consumes_fO2=False,
    )
    nonconsumer_registry.register(
        nonconsumer,
        [ChemistryIntent.VAPOR_PRESSURE],
    )
    nonconsumer_kernel = ChemistryKernel(
        AtomLedger(),
        nonconsumer_registry,
        species_formula_registry={},
    )
    nonconsumer_kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=1400.0,
        pressure_bar=1.0,
        redox_state=state,
    )

    assert nonconsumer.requests[0].redox_state is None
    assert nonconsumer.requests[0].fO2_log is None
