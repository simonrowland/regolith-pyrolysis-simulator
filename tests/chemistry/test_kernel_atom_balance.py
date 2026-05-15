"""Kernel invariant: unbalanced transitions never reach the ledger.

A :class:`LedgerTransitionProposal` whose debits and credits do not
conserve atoms element-by-element raises :class:`AtomBalanceError`
BEFORE the kernel applies it.  A balanced proposal applies cleanly.
"""

from __future__ import annotations

import pytest

from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.kernel import (
    AtomBalanceError,
    CapabilityProfile,
    ChemistryIntent,
    ChemistryKernel,
    ChemistryProvider,
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
    ProviderRegistry,
)
from simulator.chemistry.kernel.validation import validate_atom_balance


def test_unbalanced_proposal_rejected_synchronously():
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 1.0}},
        credits={"process.overhead_gas": {"SiO2": 0.5}},  # half the SiO2 vanishes
        reason="bad",
    )
    with pytest.raises(AtomBalanceError):
        validate_atom_balance(proposal, species_formula_registry={})


def test_balanced_proposal_passes_validation():
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 1.0}},
        credits={"process.overhead_gas": {"SiO2": 1.0}},
        reason="ok",
    )
    validate_atom_balance(proposal, species_formula_registry={})


def test_proof_disagreeing_with_actual_atoms_rejected():
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 1.0}},
        credits={"process.overhead_gas": {"SiO2": 1.0}},
        reason="ok",
        atom_balance_proof={"O": 1.0},  # claims +1 mol O net; truth is 0
    )
    with pytest.raises(AtomBalanceError):
        validate_atom_balance(proposal, species_formula_registry={})


# ---------------------------------------------------------------------------
# A mock provider whose proposal is balanced -- the commit path actually
# applies it to the ledger and the ledger remains balanced afterwards.


class _BalancedProvider(ChemistryProvider):
    name = "balanced"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="balanced",
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
                debits={"process.cleaned_melt": {"SiO2": 0.1}},
                credits={"process.overhead_gas": {"SiO2": 0.1}},
                reason="balanced_evap",
            ),
            control_audit=None,
            diagnostic={},
            warnings=(),
        )


def test_balanced_provider_proposal_commits_via_kernel():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 5.0})
    registry = ProviderRegistry()
    registry.register(
        _BalancedProvider(), [ChemistryIntent.EVAPORATION_TRANSITION]
    )
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    result = kernel.dispatch(
        ChemistryIntent.EVAPORATION_TRANSITION,
        temperature_C=1500.0,
        pressure_bar=1e-6,
    )
    assert result.transition is not None

    transition = kernel.commit_batch(result.transition)
    assert transition is not None
    ledger.assert_balanced()
    # The 0.1 mol of SiO2 should now be in process.overhead_gas, not
    # cleaned_melt.
    melt_mol = ledger.mol_by_account("process.cleaned_melt")
    gas_mol = ledger.mol_by_account("process.overhead_gas")
    assert gas_mol.get("SiO2", 0.0) > 0.0
    assert melt_mol.get("SiO2", 0.0) < 5.0


class _UnbalancedProvider(ChemistryProvider):
    name = "unbalanced"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="unbalanced",
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
                debits={"process.cleaned_melt": {"SiO2": 1.0}},
                credits={"process.overhead_gas": {"SiO2": 0.5}},  # broken
                reason="bad_evap",
            ),
            control_audit=None,
            diagnostic={},
            warnings=(),
        )


def test_unbalanced_provider_proposal_rejected_by_kernel():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 5.0})
    registry = ProviderRegistry()
    registry.register(
        _UnbalancedProvider(), [ChemistryIntent.EVAPORATION_TRANSITION]
    )
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    with pytest.raises(AtomBalanceError):
        kernel.dispatch(
            ChemistryIntent.EVAPORATION_TRANSITION,
            temperature_C=1500.0,
            pressure_bar=1e-6,
        )
    # Ledger must be untouched.
    assert ledger.mol_by_account("process.cleaned_melt")["SiO2"] > 0.0
    assert "process.overhead_gas" not in ledger.mol_by_account()
