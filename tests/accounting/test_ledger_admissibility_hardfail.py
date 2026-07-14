from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from simulator.accounting.exceptions import (
    AccountingError,
    OverdraftError,
    UnbalancedTransitionError,
)
from simulator.accounting.formulas import resolve_species_formula
from simulator.accounting.ledger import (
    DEFAULT_BALANCE_TOLERANCE_KG,
    DEFAULT_MASS_TOLERANCE_KG,
    KNOWN_LEDGER_ACCOUNT_PREFIXES,
    KNOWN_LEDGER_ACCOUNTS,
    AccountPolicy,
    AtomLedger,
    LedgerTransition,
)
from simulator.accounting.lots import MaterialLot
from simulator.chemistry.kernel import (
    AtomBalanceError,
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


Action = Callable[[], None]


class _AdmissibilityProvider(ChemistryProvider):
    name = "admissibility_provider"

    def __init__(
        self,
        declared_accounts: set[str] | frozenset[str],
        transition: LedgerTransitionProposal | None = None,
    ) -> None:
        self._declared_accounts = frozenset(declared_accounts)
        self._transition = transition

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            is_authoritative_for=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            declared_accounts=self._declared_accounts,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        if self._transition is None:
            return IntentResult(intent=request.intent, status="unsupported")
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=self._transition,
        )


def _strict_ledger(**kwargs) -> AtomLedger:
    return AtomLedger(
        allowed_accounts=KNOWN_LEDGER_ACCOUNTS,
        allowed_account_prefixes=KNOWN_LEDGER_ACCOUNT_PREFIXES,
        **kwargs,
    )


def _kernel(
    ledger: AtomLedger,
    *,
    declared_accounts: set[str] | frozenset[str] | None = None,
    transition: LedgerTransitionProposal | None = None,
) -> ChemistryKernel:
    accounts = set(KNOWN_LEDGER_ACCOUNTS)
    accounts.update(
        {
            "process.typo_account",
            "process.wall_deposit_segment_001",
            "reservoir.reagent.C",
            "reservoir.reagent.K",
        }
    )
    if declared_accounts is not None:
        accounts.update(declared_accounts)
    registry = ProviderRegistry()
    registry.register(
        _AdmissibilityProvider(accounts, transition),
        [ChemistryIntent.EVAPORATION_TRANSITION],
    )
    return ChemistryKernel(ledger, registry, species_formula_registry={})


def _commit(ledger: AtomLedger, proposal: LedgerTransitionProposal) -> None:
    kernel = _kernel(ledger, transition=proposal)
    result = kernel.dispatch(
        ChemistryIntent.EVAPORATION_TRANSITION,
        temperature_C=1400.0,
        pressure_bar=1.0,
    )
    assert result.transition is not None
    kernel.commit_batch(ChemistryIntent.EVAPORATION_TRANSITION, result.transition)


def _assert_valid_kernel_control() -> None:
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    _commit(
        ledger,
        LedgerTransitionProposal(
            debits={"process.cleaned_melt": {"SiO2": 0.25}},
            credits={"process.overhead_gas": {"SiO2": 0.25}},
            reason="valid_control",
        ),
    )


def _direct_overdraft_normal() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.load_external("process.cleaned_melt", {"SiO2": 1.0})
        ledger.move(
            "valid_normal_debit",
            "process.cleaned_melt",
            "process.overhead_gas",
            {"SiO2": 0.25},
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.load_external("process.cleaned_melt", {"SiO2": 1.0})
        ledger.move(
            "normal_overdraft",
            "process.cleaned_melt",
            "process.overhead_gas",
            {"SiO2": 1.1},
        )

    return valid, invalid, OverdraftError


def _direct_reservoir_credit_exceeded() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.set_account_policy(
            "reservoir.reagent.C",
            AccountPolicy.reservoir(
                "reservoir.reagent.C",
                credit_limit_kg_by_species={"C": 1.0},
            ),
        )
        ledger.move(
            "valid_reagent_draw",
            "reservoir.reagent.C",
            "process.reagent_inventory",
            {"C": 0.25},
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.set_account_policy(
            "reservoir.reagent.C",
            AccountPolicy.reservoir(
                "reservoir.reagent.C",
                credit_limit_kg_by_species={"C": 1.0},
            ),
        )
        ledger.move(
            "reagent_credit_exceeded",
            "reservoir.reagent.C",
            "process.reagent_inventory",
            {"C": 1.1},
        )

    return valid, invalid, OverdraftError


def _direct_reservoir_no_limit() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.set_account_policy(
            "reservoir.reagent.C",
            AccountPolicy.reservoir(
                "reservoir.reagent.C",
                credit_limit_kg_by_species={"C": 1.0},
            ),
        )
        ledger.move(
            "valid_limited_reagent_draw",
            "reservoir.reagent.C",
            "process.reagent_inventory",
            {"C": 0.25},
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.set_account_policy("reservoir.reagent.C", "reservoir")
        ledger.move(
            "reagent_no_limit",
            "reservoir.reagent.C",
            "process.reagent_inventory",
            {"C": 0.25},
        )

    return valid, invalid, OverdraftError


def _direct_terminal_disallowed_species() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.load_external("process.cleaned_melt", {"O2": 1.0})
        ledger.transfer(
            "valid_oxygen_storage",
            debits=(MaterialLot("process.cleaned_melt", {"O2": 1.0}),),
            credits=(
                MaterialLot("terminal.oxygen_melt_offgas_stored", {"O2": 1.0}),
            ),
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.load_external("process.cleaned_melt", {"N2": 1.0})
        ledger.transfer(
            "bad_oxygen_storage_species",
            debits=(MaterialLot("process.cleaned_melt", {"N2": 1.0}),),
            credits=(
                MaterialLot("terminal.oxygen_melt_offgas_stored", {"N2": 1.0}),
            ),
        )

    return valid, invalid, AccountingError


def _direct_terminal_debit_forbidden() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.load_external(
            "terminal.oxygen_melt_offgas_stored",
            {"O2": 1.0},
        )
        ledger.move(
            "valid_terminal_oxygen_vent",
            "terminal.oxygen_melt_offgas_stored",
            "terminal.oxygen_melt_offgas_vented_to_vacuum",
            {"O2": 1.0},
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.load_external("terminal.offgas", {"H2O": 1.0})
        ledger.move(
            "bad_terminal_reversal",
            "terminal.offgas",
            "process.cleaned_melt",
            {"H2O": 1.0},
        )

    return valid, invalid, AccountingError


def _direct_atom_imbalance() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.load_external("process.cleaned_melt", {"SiO2": 1.0})
        ledger.apply(
            LedgerTransition(
                "valid_atom_balance",
                debits=(MaterialLot("process.cleaned_melt", {"SiO2": 1.0}),),
                credits=(MaterialLot("process.overhead_gas", {"SiO2": 1.0}),),
            )
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.load_external("process.cleaned_melt", {"H2O": 1.0})
        ledger.apply(
            LedgerTransition(
                "atom_imbalance",
                debits=(MaterialLot("process.cleaned_melt", {"H2O": 1.0}),),
                credits=(MaterialLot("process.overhead_gas", {"H2": 1.0}),),
            )
        )

    return valid, invalid, UnbalancedTransitionError


def _direct_mass_imbalance() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.load_external("process.cleaned_melt", {"SiO2": 2.0})
        ledger.apply(
            LedgerTransition(
                "valid_mass_balance",
                debits=(MaterialLot("process.cleaned_melt", {"SiO2": 1.0}),),
                credits=(MaterialLot("process.overhead_gas", {"SiO2": 1.0}),),
            )
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.load_external("process.cleaned_melt", {"SiO2": 2.0})
        ledger.apply(
            LedgerTransition(
                "mass_imbalance",
                debits=(MaterialLot("process.cleaned_melt", {"SiO2": 1.0}),),
                credits=(MaterialLot("process.overhead_gas", {"SiO2": 1.03}),),
            )
        )

    return valid, invalid, UnbalancedTransitionError


def _direct_nonfinite_load() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        AtomLedger().load_external("process.cleaned_melt", {"SiO2": 1.0})

    def invalid() -> None:
        AtomLedger().load_external("process.cleaned_melt", {"SiO2": math.inf})

    return valid, invalid, AccountingError


def _direct_negative_load() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        AtomLedger().load_external("process.cleaned_melt", {"SiO2": 1.0})

    def invalid() -> None:
        AtomLedger().load_external("process.cleaned_melt", {"SiO2": -1.0})

    return valid, invalid, AccountingError


def _direct_unknown_species() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.load_external("process.cleaned_melt", {"SiO2": 1.0})
        ledger.transfer(
            "valid_known_species",
            debits=(MaterialLot("process.cleaned_melt", {"SiO2": 1.0}),),
            credits=(MaterialLot("process.overhead_gas", {"SiO2": 1.0}),),
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.transfer(
            "unknown_species",
            debits=(MaterialLot("process.cleaned_melt", {"Xx2": 1.0}),),
            credits=(MaterialLot("process.overhead_gas", {"Xx2": 1.0}),),
        )

    return valid, invalid, AccountingError


def _direct_unknown_account_strict() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = _strict_ledger()
        ledger.load_external("process.cleaned_melt", {"SiO2": 1.0})
        ledger.transfer(
            "valid_known_account",
            debits=(MaterialLot("process.cleaned_melt", {"SiO2": 0.5}),),
            credits=(MaterialLot("process.overhead_gas", {"SiO2": 0.5}),),
        )

    def invalid() -> None:
        ledger = _strict_ledger()
        ledger.load_external("process.cleaned_melt", {"SiO2": 1.0})
        ledger.transfer(
            "unknown_account",
            debits=(MaterialLot("process.cleaned_melt", {"SiO2": 0.5}),),
            credits=(MaterialLot("process.typo_account", {"SiO2": 0.5}),),
        )

    return valid, invalid, AccountingError


@pytest.mark.parametrize(
    ("name", "builder"),
    [
        ("overdraft-normal", _direct_overdraft_normal),
        ("reservoir-credit-exceeded", _direct_reservoir_credit_exceeded),
        ("reservoir-no-limit", _direct_reservoir_no_limit),
        ("terminal-disallowed-species", _direct_terminal_disallowed_species),
        ("terminal-debit-forbidden", _direct_terminal_debit_forbidden),
        ("atom-imbalance", _direct_atom_imbalance),
        ("mass-imbalance", _direct_mass_imbalance),
        ("nonfinite-load", _direct_nonfinite_load),
        ("negative-load", _direct_negative_load),
        ("unknown-species", _direct_unknown_species),
        ("unknown-account-strict", _direct_unknown_account_strict),
    ],
)
def test_atom_ledger_hard_fails_inadmissible_paths(
    name: str,
    builder: Callable[[], tuple[Action, Action, type[BaseException]]],
) -> None:
    valid, invalid, expected = builder()

    valid()
    with pytest.raises(expected) as exc_info:
        invalid()
    assert exc_info.value is not None, name


def _kernel_overdraft_normal() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        _assert_valid_kernel_control()

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": 2.0}},
                credits={"process.overhead_gas": {"SiO2": 2.0}},
                reason="kernel_normal_overdraft",
            ),
        )

    return valid, invalid, ProposalRejected


def _kernel_reservoir_credit_exceeded() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.set_account_policy(
            "reservoir.reagent.C",
            AccountPolicy.reservoir(
                "reservoir.reagent.C",
                credit_limit_kg_by_species={"C": 0.02},
            ),
        )
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"reservoir.reagent.C": {"C": 0.5}},
                credits={"process.reagent_inventory": {"C": 0.5}},
                reason="valid_kernel_reagent_draw",
            ),
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.set_account_policy(
            "reservoir.reagent.C",
            AccountPolicy.reservoir(
                "reservoir.reagent.C",
                credit_limit_kg_by_species={"C": 0.01},
            ),
        )
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"reservoir.reagent.C": {"C": 2.0}},
                credits={"process.reagent_inventory": {"C": 2.0}},
                reason="kernel_reagent_credit_exceeded",
            ),
        )

    return valid, invalid, ProposalRejected


def _kernel_reservoir_no_limit() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.set_account_policy(
            "reservoir.reagent.C",
            AccountPolicy.reservoir(
                "reservoir.reagent.C",
                credit_limit_kg_by_species={"C": 0.02},
            ),
        )
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"reservoir.reagent.C": {"C": 0.5}},
                credits={"process.reagent_inventory": {"C": 0.5}},
                reason="valid_kernel_limited_reagent_draw",
            ),
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.set_account_policy("reservoir.reagent.C", "reservoir")
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"reservoir.reagent.C": {"C": 0.5}},
                credits={"process.reagent_inventory": {"C": 0.5}},
                reason="kernel_reagent_no_limit",
            ),
        )

    return valid, invalid, ProposalRejected


def _kernel_terminal_disallowed_species() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.load_external_mol("process.cleaned_melt", {"O2": 1.0})
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"O2": 1.0}},
                credits={"terminal.oxygen_melt_offgas_stored": {"O2": 1.0}},
                reason="valid_kernel_oxygen_storage",
            ),
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.load_external_mol("process.cleaned_melt", {"N2": 1.0})
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"N2": 1.0}},
                credits={"terminal.oxygen_melt_offgas_stored": {"N2": 1.0}},
                reason="kernel_bad_oxygen_storage_species",
            ),
        )

    return valid, invalid, ProposalRejected


def _kernel_terminal_debit_forbidden() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = AtomLedger()
        ledger.load_external_mol("terminal.oxygen_melt_offgas_stored", {"O2": 1.0})
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"terminal.oxygen_melt_offgas_stored": {"O2": 1.0}},
                credits={
                    "terminal.oxygen_melt_offgas_vented_to_vacuum": {"O2": 1.0},
                },
                reason="valid_kernel_terminal_oxygen_vent",
            ),
        )

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.load_external_mol("terminal.offgas", {"H2O": 1.0})
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"terminal.offgas": {"H2O": 1.0}},
                credits={"process.cleaned_melt": {"H2O": 1.0}},
                reason="kernel_bad_terminal_reversal",
            ),
        )

    return valid, invalid, ProposalRejected


def _kernel_atom_imbalance() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        _assert_valid_kernel_control()

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.load_external_mol("process.cleaned_melt", {"H2O": 1.0})
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"H2O": 1.0}},
                credits={"process.overhead_gas": {"H2": 1.0}},
                reason="kernel_atom_imbalance",
            ),
        )

    return valid, invalid, AtomBalanceError


def _kernel_mass_imbalance() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        _assert_valid_kernel_control()

    def invalid() -> None:
        ledger = AtomLedger()
        ledger.load_external_mol("process.cleaned_melt", {"SiO2": 2.0})
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": 1.0}},
                credits={"process.overhead_gas": {"SiO2": 2.0}},
                reason="kernel_mass_imbalance",
            ),
        )

    return valid, invalid, AtomBalanceError


def _kernel_negative_amount() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        _assert_valid_kernel_control()

    def invalid() -> None:
        ledger = AtomLedger()
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": -0.1}},
                credits={"process.overhead_gas": {"SiO2": -0.1}},
                reason="kernel_negative_amount",
            ),
        )

    return valid, invalid, AtomBalanceError


def _kernel_unknown_species() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        _assert_valid_kernel_control()

    def invalid() -> None:
        ledger = AtomLedger()
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"Xx2": 1.0}},
                credits={"process.overhead_gas": {"Xx2": 1.0}},
                reason="kernel_unknown_species",
            ),
        )

    return valid, invalid, AtomBalanceError


def _kernel_unknown_account_strict() -> tuple[Action, Action, type[BaseException]]:
    def valid() -> None:
        ledger = _strict_ledger()
        ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": 0.25}},
                credits={"process.overhead_gas": {"SiO2": 0.25}},
                reason="valid_kernel_strict_known_account",
            ),
        )

    def invalid() -> None:
        ledger = _strict_ledger()
        ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": 0.25}},
                credits={"process.typo_account": {"SiO2": 0.25}},
                reason="kernel_unknown_account",
            ),
        )

    return valid, invalid, ProposalRejected


@pytest.mark.parametrize(
    ("name", "builder"),
    [
        ("overdraft-normal", _kernel_overdraft_normal),
        ("reservoir-credit-exceeded", _kernel_reservoir_credit_exceeded),
        ("reservoir-no-limit", _kernel_reservoir_no_limit),
        ("terminal-disallowed-species", _kernel_terminal_disallowed_species),
        ("terminal-debit-forbidden", _kernel_terminal_debit_forbidden),
        ("atom-imbalance", _kernel_atom_imbalance),
        ("mass-imbalance", _kernel_mass_imbalance),
        ("negative-amount", _kernel_negative_amount),
        ("unknown-species", _kernel_unknown_species),
        ("unknown-account-strict", _kernel_unknown_account_strict),
    ],
)
def test_kernel_commit_batch_hard_fails_inadmissible_paths(
    name: str,
    builder: Callable[[], tuple[Action, Action, type[BaseException]]],
) -> None:
    valid, invalid, expected = builder()

    valid()
    with pytest.raises(expected) as exc_info:
        invalid()
    assert exc_info.value is not None, name


@pytest.mark.parametrize(
    "proposal_kwargs",
    [
        {"debits": {"process.cleaned_melt": {"SiO2": math.nan}}},
        {"credits": {"process.overhead_gas": {"SiO2": math.inf}}},
        {"atom_balance_proof": {"Si": math.nan}},
        {"atom_balance_proof": {"Si": -math.inf}},
    ],
)
def test_ledger_transition_proposal_rejects_nonfinite_construction(
    proposal_kwargs: dict,
) -> None:
    valid = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"SiO2": 0.25}},
        credits={"process.overhead_gas": {"SiO2": 0.25}},
        reason="valid_finite_proposal",
        atom_balance_proof={"Si": 0.0, "O": 0.0},
    )
    assert valid.debits["process.cleaned_melt"]["SiO2"] == pytest.approx(0.25)

    kwargs = {
        "debits": {"process.cleaned_melt": {"SiO2": 0.25}},
        "credits": {"process.overhead_gas": {"SiO2": 0.25}},
        "reason": "nonfinite_proposal",
        "atom_balance_proof": {"Si": 0.0, "O": 0.0},
    }
    kwargs.update(proposal_kwargs)
    with pytest.raises(ValueError, match="finite"):
        LedgerTransitionProposal(**kwargs)


def test_strict_allowlist_accepts_known_accounts_and_dynamic_prefixes() -> None:
    ledger = _strict_ledger()

    ledger.load_external("process.cleaned_melt", {"SiO2": 1.0})
    ledger.load_external("process.wall_deposit_segment_001", {"SiO2": 0.25})
    ledger.load_external("reservoir.reagent.K", {"K": 0.25})
    ledger.set_account_policy(
        "reservoir.reagent.C",
        AccountPolicy.reservoir(
            "reservoir.reagent.C",
            credit_limit_kg_by_species={"C": 1.0},
        ),
    )

    with pytest.raises(AccountingError, match="unknown ledger account"):
        ledger.load_external("process.typo_account", {"SiO2": 0.25})
    with pytest.raises(AccountingError, match="unknown ledger account"):
        ledger.set_account_policy("process.typo_account", "normal")


def test_overdraft_tolerance_boundary_apply_and_kernel_commit_batch() -> None:
    molar_mass = resolve_species_formula("SiO2").molar_mass_kg_per_mol()
    within_extra_mol = (DEFAULT_BALANCE_TOLERANCE_KG * 0.5) / molar_mass
    beyond_extra_mol = (DEFAULT_BALANCE_TOLERANCE_KG * 2.0) / molar_mass

    ledger = AtomLedger()
    ledger.load_external("process.cleaned_melt", {"SiO2": 1.0})
    ledger.transfer(
        "within_apply_overdraft_tolerance",
        debits=(
            MaterialLot(
                "process.cleaned_melt",
                {"SiO2": 1.0 + DEFAULT_BALANCE_TOLERANCE_KG * 0.5},
            ),
        ),
        credits=(
            MaterialLot(
                "process.overhead_gas",
                {"SiO2": 1.0 + DEFAULT_BALANCE_TOLERANCE_KG * 0.5},
            ),
        ),
    )

    ledger = AtomLedger()
    ledger.load_external("process.cleaned_melt", {"SiO2": 1.0})
    with pytest.raises(OverdraftError):
        ledger.transfer(
            "beyond_apply_overdraft_tolerance",
            debits=(
                MaterialLot(
                    "process.cleaned_melt",
                    {"SiO2": 1.0 + DEFAULT_BALANCE_TOLERANCE_KG * 2.0},
                ),
            ),
            credits=(
                MaterialLot(
                    "process.overhead_gas",
                    {"SiO2": 1.0 + DEFAULT_BALANCE_TOLERANCE_KG * 2.0},
                ),
            ),
        )

    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    _commit(
        ledger,
        LedgerTransitionProposal(
            debits={"process.cleaned_melt": {"SiO2": 1.0 + within_extra_mol}},
            credits={"process.overhead_gas": {"SiO2": 1.0 + within_extra_mol}},
            reason="within_kernel_overdraft_tolerance",
        ),
    )

    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 1.0})
    with pytest.raises(ProposalRejected):
        _commit(
            ledger,
            LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": 1.0 + beyond_extra_mol}},
                credits={"process.overhead_gas": {"SiO2": 1.0 + beyond_extra_mol}},
                reason="beyond_kernel_overdraft_tolerance",
            ),
        )


def test_mass_tolerance_boundary_on_apply() -> None:
    ledger = AtomLedger(atom_tolerance_mol=1e9)
    ledger.load_external("process.cleaned_melt", {"H2O": 2.0})
    ledger.apply(
        LedgerTransition(
            "within_mass_tolerance",
            debits=(MaterialLot("process.cleaned_melt", {"H2O": 1.0}),),
            credits=(
                MaterialLot(
                    "process.overhead_gas",
                    {"H2O": 1.0 + DEFAULT_MASS_TOLERANCE_KG * 0.5},
                ),
            ),
        )
    )

    ledger = AtomLedger(atom_tolerance_mol=1e9)
    ledger.load_external("process.cleaned_melt", {"H2O": 2.0})
    with pytest.raises(UnbalancedTransitionError, match="does not conserve mass"):
        ledger.apply(
            LedgerTransition(
                "beyond_mass_tolerance",
                debits=(MaterialLot("process.cleaned_melt", {"H2O": 1.0}),),
                credits=(
                    MaterialLot(
                        "process.overhead_gas",
                        {"H2O": 1.0 + DEFAULT_MASS_TOLERANCE_KG * 2.0},
                    ),
                ),
            )
        )


def test_element_atom_drift_reports_accepted_sub_tolerance_residual() -> None:
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"Si": 2.0})
    residual_mol_atoms = 0.5e-6

    ledger.apply(
        LedgerTransition(
            "sub_tolerance_si_fault",
            debits=(ledger.debit_mol("process.cleaned_melt", {"Si": 1.0}),),
            credits=(
                ledger.credit_mol(
                    "process.overhead_gas",
                    {"Si": 1.0 - residual_mol_atoms},
                ),
            ),
        )
    )

    report = ledger.close_report()
    drift = report["element_atom_drift"]
    assert report["balanced"] is True
    assert drift["accepted_transition_residual_mol_atoms"]["Si"] == pytest.approx(
        -residual_mol_atoms
    )
    assert drift["whole_run_boundary_residual_mol_atoms"]["Si"] == pytest.approx(
        -residual_mol_atoms
    )


def test_element_atom_drift_boundary_catches_pretransition_discard() -> None:
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"Si": 2.0})
    residual_mol_atoms = 0.5e-6

    # Deliberate corruption models material discarded before a transition exists.
    ledger._balances["process.cleaned_melt"]["Si"] -= residual_mol_atoms

    drift = ledger.close_report()["element_atom_drift"]
    assert drift["accepted_transition_residual_mol_atoms"]["Si"] == 0.0
    assert drift["whole_run_boundary_residual_mol_atoms"]["Si"] == pytest.approx(
        -residual_mol_atoms
    )


def test_policy_mapping_rejects_embedded_account_mismatch_before_mutation() -> None:
    ledger = _strict_ledger()

    with pytest.raises(AccountingError, match="does not match key"):
        ledger.set_account_policy(
            "process.cleaned_melt",
            {
                "account": "reservoir.reagent.K",
                "allow_negative": True,
                "credit_limit_kg_by_species": {"SiO2": 1.0},
            },
        )

    assert ledger.account_policy("process.cleaned_melt") == AccountPolicy.normal(
        "process.cleaned_melt"
    )


@pytest.mark.parametrize(
    "field",
    ["mass_tolerance_kg", "atom_tolerance_mol", "relative_tolerance"],
)
@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, -1.0])
def test_ledger_rejects_nonfinite_or_negative_tolerances(
    field: str,
    value: float,
) -> None:
    with pytest.raises(AccountingError, match="finite and non-negative"):
        AtomLedger(**{field: value})


def test_default_tolerances_cannot_admit_hydrogen_to_oxygen_conversion() -> None:
    ledger = AtomLedger()
    ledger.load_external("process.cleaned_melt", {"H2": 1.0})

    # Equal mass does not conserve elements: H2 contributes only H atoms,
    # while O2 contributes only O atoms, so neither elemental total cancels.
    with pytest.raises(UnbalancedTransitionError, match="does not conserve atoms"):
        ledger.move(
            "h2_to_o2",
            "process.cleaned_melt",
            "process.overhead_gas",
            {"H2": 1.0},
            credit_species_kg={"O2": 1.0},
        )


def test_failed_policy_replacement_restores_previous_reservoir_policy() -> None:
    account = "reservoir.reagent.K"
    original = AccountPolicy.reservoir(account, {"K": 1.0})
    ledger = _strict_ledger(account_policies={account: original})
    ledger.move(
        "borrow_k",
        account,
        "process.cleaned_melt",
        {"K": 0.5},
    )

    with pytest.raises(OverdraftError):
        ledger.set_account_policy(account, AccountPolicy.normal(account))

    assert ledger.account_policy(account) == original
    assert ledger.assert_balanced()


def test_explicit_empty_move_credit_is_not_replaced_by_debit_species() -> None:
    ledger = AtomLedger()
    ledger.load_external("process.cleaned_melt", {"SiO2": 1.0})

    with pytest.raises(UnbalancedTransitionError):
        ledger.move(
            "empty_products",
            "process.cleaned_melt",
            "process.overhead_gas",
            {"SiO2": 0.5},
            credit_species_kg={},
        )

    assert ledger.transitions == ()
    assert ledger.kg_by_account("process.cleaned_melt")["SiO2"] == pytest.approx(1.0)
