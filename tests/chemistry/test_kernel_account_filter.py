"""Kernel invariant: undeclared accounts never reach a provider.

Covers the binding-spec §7 prohibition on "VapoRock receiving
metal/sulfide/salt accounts. Filter at entry." (and the general rule
that no provider may see an account outside its declared set).
"""

from __future__ import annotations

import pytest

from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.kernel import (
    AccountFilterViolation,
    CapabilityProfile,
    ChemistryIntent,
    ProviderAccountView,
)
from simulator.chemistry.kernel.account_filters import build_provider_account_view


def _ledger_with_accounts() -> AtomLedger:
    ledger = AtomLedger()
    ledger.load_external("process.cleaned_melt", {"SiO2": 10.0, "FeO": 2.0})
    ledger.load_external("process.metal_phase", {"Fe": 0.5})
    ledger.load_external("process.overhead_gas", {"O2": 0.001})
    return ledger


def test_filter_returns_only_declared_accounts():
    ledger = _ledger_with_accounts()
    view = build_provider_account_view(
        ledger,
        frozenset({"process.cleaned_melt"}),
        species_formula_registry={},
    )

    assert isinstance(view, ProviderAccountView)
    assert set(view.accounts.keys()) == {"process.cleaned_melt"}
    # Concrete species values flowed through.
    assert view.accounts["process.cleaned_melt"]["SiO2"] > 0.0
    assert view.accounts["process.cleaned_melt"]["FeO"] > 0.0


def test_filter_blocks_undeclared_metal_phase():
    """A silicate-only provider must not see process.metal_phase."""

    ledger = _ledger_with_accounts()
    view = build_provider_account_view(
        ledger,
        frozenset({"process.cleaned_melt"}),
        species_formula_registry={},
    )

    # The metal_phase account holds material in the ledger, but the
    # silicate-only provider must never see it.
    assert "process.metal_phase" not in view.accounts
    assert "process.overhead_gas" not in view.accounts


def test_filter_blocks_undeclared_overhead_gas():
    """A melt-side provider must not see overhead-gas accounts."""

    ledger = _ledger_with_accounts()
    view = build_provider_account_view(
        ledger,
        frozenset({"process.cleaned_melt", "process.metal_phase"}),
        species_formula_registry={},
    )
    assert "process.overhead_gas" not in view.accounts


def test_filter_empty_declared_accounts_raises():
    ledger = _ledger_with_accounts()
    with pytest.raises(AccountFilterViolation):
        build_provider_account_view(
            ledger,
            frozenset(),
            species_formula_registry={},
        )


def test_filter_declared_but_empty_account_appears_as_empty_dict():
    """Declared accounts with no balance still appear (as ``{}``).

    Lets providers iterate their declared set without conditional
    lookups; the kernel never silently drops a declared account.
    """

    ledger = _ledger_with_accounts()
    view = build_provider_account_view(
        ledger,
        frozenset({"process.cleaned_melt", "process.salt_phase"}),
        species_formula_registry={},
    )
    assert view.accounts["process.salt_phase"] == {}


def test_provider_view_is_immutable():
    ledger = _ledger_with_accounts()
    view = build_provider_account_view(
        ledger,
        frozenset({"process.cleaned_melt"}),
        species_formula_registry={},
    )
    with pytest.raises(TypeError):
        view.accounts["process.metal_phase"] = {"Fe": 1.0}  # type: ignore[index]
    with pytest.raises(TypeError):
        view.accounts["process.cleaned_melt"]["FOO"] = 1.0  # type: ignore[index]


# ---------------------------------------------------------------------------
# A MockProvider here demonstrates that the kernel actually filters before
# dispatch.  The provider records every account name it sees; the test
# asserts no undeclared name ever appears, even in the presence of a richly
# populated ledger.


from simulator.chemistry.kernel import (
    ChemistryKernel,
    ChemistryProvider,
    IntentRequest,
    IntentResult,
    ProviderRegistry,
)


class _AccountRecordingProvider(ChemistryProvider):
    name = "account_recorder"

    def __init__(self) -> None:
        self.seen_accounts: list[frozenset[str]] = []

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="account_recorder",
            intents=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
            is_authoritative_for=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.seen_accounts.append(frozenset(request.account_view.accounts))
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=None,
            control_audit=None,
            diagnostic={},
            warnings=(),
        )


def test_kernel_dispatch_filters_account_view_before_provider_sees_it():
    ledger = _ledger_with_accounts()
    registry = ProviderRegistry()
    provider = _AccountRecordingProvider()
    registry.register(provider, [ChemistryIntent.SILICATE_LIQUIDUS])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    kernel.dispatch(
        ChemistryIntent.SILICATE_LIQUIDUS,
        temperature_C=1400.0,
        pressure_bar=1.0,
    )

    assert provider.seen_accounts, "provider was never dispatched"
    seen = provider.seen_accounts[0]
    assert "process.metal_phase" not in seen
    assert "process.overhead_gas" not in seen
    assert seen <= {"process.cleaned_melt"}
