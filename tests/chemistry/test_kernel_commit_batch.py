"""Kernel invariant: :meth:`ChemistryKernel.commit_batch` is the sole writer.

The kernel never exposes the :class:`AtomLedger` to providers.  The
provider ABC carries no ledger reference, the
:class:`IntentRequest` DTO does not expose the ledger, and providers
have no transitive route to it.  This test file proves those holes are
plugged at the API level.

It also exercises the positive path: a well-formed proposal that goes
through :meth:`commit_batch` actually lands in the ledger.
"""

from __future__ import annotations

import inspect
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
    ProposalRejected,
    ProviderRegistry,
)


def test_provider_abc_does_not_accept_ledger_reference():
    """A :class:`ChemistryProvider` has no ledger field or method."""

    members = dict(inspect.getmembers(ChemistryProvider))
    public_names = {
        name for name in members if not name.startswith("_")
    }
    for forbidden in ("ledger", "atom_ledger", "commit", "commit_batch", "apply"):
        assert forbidden not in public_names, (
            f"ChemistryProvider exposes forbidden attribute {forbidden!r}"
        )


def test_intent_request_does_not_expose_ledger_reference():
    """The request DTO carries only a filtered view, not the ledger."""

    fields = {f for f in IntentRequest.__dataclass_fields__}
    for forbidden in ("ledger", "atom_ledger"):
        assert forbidden not in fields, (
            f"IntentRequest exposes forbidden field {forbidden!r}"
        )


def test_provider_can_only_reach_ledger_via_kernel_commit_batch():
    """Walk the public API surface and confirm there is no provider->ledger path.

    Every public method of :class:`ChemistryProvider` returns either an
    :class:`IntentResult` or its :class:`CapabilityProfile`.  There is
    no setter or getter for a ledger reference, no callback hook, and
    no other route into the kernel's writable path.  This is a
    structural assertion -- if someone adds such a path in the future,
    this test will trip.
    """

    public_provider_methods = {
        name for name, obj in inspect.getmembers(ChemistryProvider)
        if not name.startswith("_") and callable(obj)
    }
    # Whitelist of methods/attrs the provider ABC may carry.
    allowed = {"capability_profile", "dispatch", "emits_ledger_transition", "name"}
    extras = public_provider_methods - allowed
    assert extras == set(), (
        f"ChemistryProvider has unexpected public members: {sorted(extras)}; "
        f"a new public surface needs review for ledger-leak risk"
    )


# ---------------------------------------------------------------------------
# Positive path: commit_batch applies the proposal to the ledger.


class _CommitProvider(ChemistryProvider):
    name = "commit_provider"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="commit_provider",
            intents=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            is_authoritative_for=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            declared_accounts=frozenset(
                {"process.cleaned_melt", "process.overhead_gas"}
            ),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": 0.25}},
                credits={"process.overhead_gas": {"SiO2": 0.25}},
                reason="evap_step",
            ),
            control_audit=None,
            diagnostic={},
            warnings=(),
        )


def test_commit_batch_is_sole_writer_and_applies_transition():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    registry = ProviderRegistry()
    registry.register(_CommitProvider(), [ChemistryIntent.EVAPORATION_TRANSITION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    result = kernel.dispatch(
        ChemistryIntent.EVAPORATION_TRANSITION,
        temperature_C=1500.0,
        pressure_bar=1e-6,
    )
    assert result.transition is not None
    # Before commit_batch -- ledger unchanged.
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(
        1.0, rel=1e-9
    )
    assert "process.overhead_gas" not in ledger.mol_by_account()

    transition = kernel.commit_batch(result.transition)
    assert transition is not None

    # After commit_batch -- ledger reflects the transition.
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(
        0.75, rel=1e-9
    )
    assert ledger.mol_by_account("process.overhead_gas")["SiO2"] == pytest.approx(
        0.25, rel=1e-9
    )
    ledger.assert_balanced()


def test_commit_batch_rejects_unbalanced_proposal():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    registry = ProviderRegistry()
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    bad = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 1.0}},
        credits={"process.overhead_gas": {"SiO2": 0.5}},
        reason="bad",
    )
    with pytest.raises(Exception):
        kernel.commit_batch(bad)
    # Ledger must be untouched.
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(
        1.0, rel=1e-9
    )


def test_commit_batch_propagates_overdraft_as_proposal_rejected():
    """Pulling more material than the account holds raises ProposalRejected."""

    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 0.1})
    registry = ProviderRegistry()
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    overdraft = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 10.0}},
        credits={"process.overhead_gas": {"SiO2": 10.0}},
        reason="overdraft",
    )
    with pytest.raises(ProposalRejected):
        kernel.commit_batch(overdraft)
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(
        0.1, rel=1e-9
    )
