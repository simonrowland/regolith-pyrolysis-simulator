"""Kernel invariant: unbalanced transitions never reach the ledger.

A :class:`LedgerTransitionProposal` whose debits and credits do not
conserve atoms element-by-element raises :class:`AtomBalanceError`
BEFORE the kernel applies it.  A balanced proposal applies cleanly.
"""

from __future__ import annotations

import pytest

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import resolve_species_formula
from simulator.accounting.ledger import AtomLedger
from simulator.accounting.lots import mass_kg_underflows
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


def test_atom_balance_proof_mismatch_within_old_tolerance_now_rejected():
    """Proof cross-check tolerance is now ``PROOF_CROSSCHECK_TOLERANCE_MOL``
    (1e-12) -- tighter than the broader conservation gate at
    ``DEFAULT_ATOM_TOLERANCE_MOL`` (1e-6).

    A proof claim that disagrees with truth by 5e-7 mol used to pass
    silently at the old ``abs_tol=1e-6``.  After A2, the proof
    cross-check rejects any disagreement above 1e-12 mol -- the proof
    is a provider self-bookkeeping claim, not a numerical estimate.
    """

    # The proposal itself is atom-balanced (1 mol SiO2 debit ==
    # 1 mol SiO2 credit), so the conservation gate passes.  But the
    # provider's claim of net +5e-7 mol O disagrees with truth (0 mol)
    # by 5e-7 -- 5 orders of magnitude above the new tight tolerance.
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 1.0}},
        credits={"process.overhead_gas": {"SiO2": 1.0}},
        reason="proof_tolerance_test",
        atom_balance_proof={"O": 5e-7},  # 0.5 microMol claimed; actual = 0
    )
    with pytest.raises(AtomBalanceError):
        validate_atom_balance(proposal, species_formula_registry={})


def test_atom_balance_proof_within_tight_tolerance_passes():
    """A proof claim within ``PROOF_CROSSCHECK_TOLERANCE_MOL`` (1e-12) is OK."""

    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 1.0}},
        credits={"process.overhead_gas": {"SiO2": 1.0}},
        reason="proof_tight_ok",
        atom_balance_proof={"O": 1e-15},  # well below 1e-12 cross-check
    )
    validate_atom_balance(proposal, species_formula_registry={})


def test_captured_hour_261_si_dust_survives_lot_materialization():
    proposal = LedgerTransitionProposal(
        debits={
            "process.metal_phase": {
                "Si": 1208.8340934601026,
            },
        },
        credits={
            "process.metal_phase_float_layer": {
                "Si": 1208.8340934600913,
            },
            "process.metal_phase_bottom_pool": {
                "Si": 1.1487449696710447e-11,
            },
        },
        reason="metal_phase_stratification_diagnostic",
        atom_balance_proof={"Si": 2.2737367544323206e-13},
    )

    validate_atom_balance(proposal, species_formula_registry={})


def test_mol_lot_materialization_keeps_nonzero_species_or_refuses_underflow():
    si_mol = 1.1487449696710447e-11
    lot = AtomLedger().credit_mol(
        "process.metal_phase_bottom_pool",
        {"Si": si_mol},
    )

    assert lot.species_kg.get("Si", 0.0) > 0.0
    kept = lot.without_empty()
    assert kept.species_kg.get("Si", 0.0) > 0.0
    assert kept.species_moles_for()["Si"] == pytest.approx(si_mol, rel=1e-14)

    with pytest.raises(AccountingError, match="underflows"):
        AtomLedger().credit_mol(
            "process.metal_phase_bottom_pool",
            {"Si": 1e-323},
        )

    with pytest.raises(AccountingError, match="non-negative"):
        AtomLedger().credit_mol(
            "process.metal_phase_bottom_pool",
            {"Si": -si_mol},
        )


def test_kernel_drops_unrepresentable_mol_mass_and_keeps_the_sibling():
    """A denormal mole beside a real sibling must not abort the proposal.

    MaterialLot refuses a mol-basis lot whose mass is exact zero. That
    refusal is what poisoned the Robinot hour after earlier commits. The
    denormal has no representable mass, so the kernel omits it and the
    balanced sibling still validates. Direct ``credit_mol`` still refuses.
    """

    dust = 1e-323
    for species in ("MgO", "Fe", "Al", "SiO2"):
        molar_mass = resolve_species_formula(species).molar_mass_kg_per_mol()
        assert mass_kg_underflows(dust, molar_mass)
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 1.0, "MgO": dust}},
        credits={"process.overhead_gas": {"SiO2": 1.0, "MgO": dust}},
        reason="denormal_mgo_beside_sio2",
    )
    validate_atom_balance(proposal, species_formula_registry={})

    from simulator.chemistry.kernel.validation import _proposal_to_ledger_transition

    transition = _proposal_to_ledger_transition(proposal, {})
    for lot in (*transition.debits, *transition.credits):
        assert "MgO" not in lot.species_kg
        assert lot.meta["species_mol"].get("MgO", 0.0) == 0.0
        assert lot.species_kg["SiO2"] > 0.0

    dust_only = LedgerTransitionProposal(
        debits={"process.overhead_gas": {"Fe": dust, "Al": dust}},
        credits={"process.wall_deposit": {"Fe": dust, "Al": dust}},
        reason="denormal_only",
    )
    validate_atom_balance(dust_only, species_formula_registry={})


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
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 5.0}, material_origin="feedstock")
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

    transition = kernel.commit_batch(
        ChemistryIntent.EVAPORATION_TRANSITION, result.transition
    )
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
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 5.0}, material_origin="feedstock")
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
