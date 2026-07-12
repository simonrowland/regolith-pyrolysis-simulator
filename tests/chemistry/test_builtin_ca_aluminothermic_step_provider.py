from __future__ import annotations

import pytest

from engines.builtin.ca_aluminothermic_step import (
    BuiltinCaAluminothermicStepProvider,
    REACTION_FAMILY_C7_CA_ALUMINOTHERMIC,
)
from engines.builtin.metallothermic_step import (
    BuiltinMetallothermicStepProvider,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)
from simulator.account_ids import C7_AL_CREDIT_ACCOUNT
from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import resolve_species_formula
from simulator.accounting.ledger import AtomLedger, LedgerTransition
from simulator.chemistry.kernel import (
    AccountFilterViolation,
    CapabilityProfile,
    ChemistryIntent,
    ChemistryKernel,
    ChemistryProvider,
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
    ProviderAccountView,
    ProviderRegistry,
    ProposalRejected,
)
from simulator.chemistry.kernel.validation import validate_proposal_accounts
from tests.chemistry.conftest import _atom_check, _build_sim


@pytest.fixture(scope="module")
def formula_registry(vapor_pressure_data, feedstocks_data, setpoints_data):
    sim = _build_sim(
        "targeted_super_kreep_ore",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    return sim.species_formula_registry


def _controls(**overrides):
    controls = {
        "reaction_family": REACTION_FAMILY_C7_CA_ALUMINOTHERMIC,
        "campaign": "C7_CA_ALUMINOTHERMIC",
        "decision": "yes",
        "reductant_species": "Al",
        "objective": "ree_enrichment",
        "hold_temp_C": 1200.0,
        "p_total_mbar": 0.05,
        "pO2_mbar": 0.0,
        "active_ca_condensation_route": True,
        "dedicated_ca_condenser": True,
        "ca_condensation_species": "Ca",
        "ca_condenser_temperature_C": 780.0,
        "thermo_margin_kj_per_mol_o2": 2.0,
        "aluminate_mode": "C3A",
        "al_source_account": C7_AL_CREDIT_ACCOUNT,
        "objective_extent_mol": 1.0,
        "transport_extent_mol": 1.0,
        "extent_fraction": 1.0,
        "allow_partial_extent": False,
    }
    controls.update(overrides)
    return controls


class _ThermoStubbedCaProvider(BuiltinCaAluminothermicStepProvider):
    def __init__(self, margin_kj_per_mol_o2: float = 2.0):
        self._margin_kj_per_mol_o2 = margin_kj_per_mol_o2

    def _computed_thermo_margin_kj_per_mol_o2(
        self,
        hold_temp_C: float,
    ) -> float:
        return self._margin_kj_per_mol_o2


def _provider(margin_kj_per_mol_o2: float = 2.0):
    return _ThermoStubbedCaProvider(margin_kj_per_mol_o2)


class _MalformedC7TerminalSlagProvider(ChemistryProvider):
    name = "malformed_c7_terminal_slag_provider"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.CA_ALUMINOTHERMIC_STEP}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.CA_ALUMINOTHERMIC_STEP}
            ),
            declared_accounts=frozenset(
                {
                    C7_AL_CREDIT_ACCOUNT,
                    "process.cleaned_melt",
                    "process.overhead_gas",
                    "terminal.slag",
                }
            ),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=LedgerTransitionProposal(
                debits={
                    "terminal.slag": {"CaO": 6.0},
                    C7_AL_CREDIT_ACCOUNT: {"Al": 2.0},
                },
                credits={
                    "process.overhead_gas": {"Ca": 3.0},
                    "process.cleaned_melt": {"Ca3Al2O6": 1.0},
                },
                reason="ca_aluminothermic_c3a_wrong_slag_destination",
            ),
        )


def _request(registry, accounts, controls):
    return IntentRequest(
        intent=ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
        account_view=ProviderAccountView(
            accounts=accounts,
            species_formula_registry=registry,
        ),
        temperature_C=1200.0,
        pressure_bar=5e-5,
        control_inputs=controls,
    )


def test_provider_declares_only_c7_intent_and_scoped_accounts():
    provider = BuiltinCaAluminothermicStepProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset(
        {ChemistryIntent.CA_ALUMINOTHERMIC_STEP}
    )
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.CA_ALUMINOTHERMIC_STEP}
    )
    assert profile.declared_accounts == frozenset(
        {
            "process.cleaned_melt",
            "process.metal_phase",
            C7_AL_CREDIT_ACCOUNT,
            "process.overhead_gas",
            "process.condensation_train",
            "process.wall_deposit",
            "terminal.slag",
        }
    )


def test_c3_c6_metallothermic_provider_does_not_gain_c7_write_scope():
    profile = BuiltinMetallothermicStepProvider().capability_profile()

    assert profile.declared_accounts == frozenset(
        {
            "process.cleaned_melt",
            "process.metal_phase",
            "process.reagent_inventory",
            SPENT_REDUCTANT_RESIDUE_ACCOUNT,
        }
    )
    assert C7_AL_CREDIT_ACCOUNT not in profile.declared_accounts
    assert "process.overhead_gas" not in profile.declared_accounts
    assert "process.condensation_train" not in profile.declared_accounts
    assert "terminal.slag" not in profile.declared_accounts


def test_c7_overhead_proposal_is_rejected_by_c3_c6_account_filter():
    profile = BuiltinMetallothermicStepProvider().capability_profile()
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"CaO": 1.0}},
        credits={"process.overhead_gas": {"Ca": 1.0}},
        reason="c7_overhead_exploit",
    )

    with pytest.raises(AccountFilterViolation):
        validate_proposal_accounts(proposal, profile.declared_accounts)


def test_formula_registry_contains_c7_aluminates(formula_registry):
    c3a = resolve_species_formula("Ca3Al2O6", formula_registry)
    c12a7 = resolve_species_formula("Ca12Al14O33", formula_registry)

    assert dict(c3a.elements) == {"Al": 2.0, "Ca": 3.0, "O": 6.0}
    assert dict(c12a7.elements) == {"Al": 14.0, "Ca": 12.0, "O": 33.0}


def test_c7_c3a_stoichiometry_from_al_credit(formula_registry):
    accounts = {
        "process.cleaned_melt": {"CaO": 60.0},
        "process.metal_phase": {},
        C7_AL_CREDIT_ACCOUNT: {"Al": 20.0},
        "process.overhead_gas": {},
        "process.condensation_train": {},
        "process.wall_deposit": {},
        "terminal.slag": {},
    }

    result = _provider().dispatch(
        _request(
            formula_registry,
            accounts,
            _controls(
                objective_extent_mol=10.0,
                transport_extent_mol=10.0,
            ),
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert dict(result.transition.debits) == {
        "process.cleaned_melt": {"CaO": 60.0},
        C7_AL_CREDIT_ACCOUNT: {"Al": 20.0},
    }
    assert dict(result.transition.credits) == {
        "process.overhead_gas": {"Ca": 30.0},
        "terminal.slag": {"Ca3Al2O6": 10.0},
    }
    _atom_check(result.transition, formula_registry, tol=1e-12)


def test_c7_c12a7_stoichiometry_from_in_situ_al(formula_registry):
    accounts = {
        "process.cleaned_melt": {"CaO": 33.0},
        "process.metal_phase": {"Al": 14.0},
        C7_AL_CREDIT_ACCOUNT: {},
        "process.overhead_gas": {},
        "process.condensation_train": {},
        "process.wall_deposit": {},
        "terminal.slag": {},
    }

    result = _provider().dispatch(
        _request(
            formula_registry,
            accounts,
            _controls(
                aluminate_mode="C12A7",
                al_source_account="process.metal_phase",
                objective_extent_mol=1.0,
                transport_extent_mol=1.0,
            ),
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert dict(result.transition.debits) == {
        "process.cleaned_melt": {"CaO": 33.0},
        "process.metal_phase": {"Al": 14.0},
    }
    assert dict(result.transition.credits) == {
        "process.overhead_gas": {"Ca": 21.0},
        "terminal.slag": {"Ca12Al14O33": 1.0},
    }
    _atom_check(result.transition, formula_registry, tol=1e-12)


def test_c7_can_commit_narrow_terminal_slag_cao_rework(formula_registry):
    provider = _provider()
    registry = ProviderRegistry()
    registry.register(provider, [ChemistryIntent.CA_ALUMINOTHERMIC_STEP])
    ledger = AtomLedger(registry=formula_registry)
    ledger.load_external_mol("terminal.slag", {"CaO": 6.0})
    ledger.load_external_mol(C7_AL_CREDIT_ACCOUNT, {"Al": 2.0})
    kernel = ChemistryKernel(ledger, registry, formula_registry)

    result = kernel.dispatch(
        ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
        temperature_C=1400.0,
        pressure_bar=1e-6,
        control_inputs=_controls(
            objective_extent_mol=1.0,
            transport_extent_mol=1.0,
        ),
    )
    assert result.transition is not None

    kernel.commit_batch(ChemistryIntent.CA_ALUMINOTHERMIC_STEP, result.transition)

    slag = ledger.mol_by_account("terminal.slag")
    assert slag.get("CaO", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert slag["Ca3Al2O6"] == pytest.approx(1.0)
    assert ledger.mol_by_account("process.overhead_gas")["Ca"] == pytest.approx(3.0)


def test_spoofed_c7_transition_cannot_reopen_terminal_slag(formula_registry):
    ledger = AtomLedger(registry=formula_registry)
    ledger.load_external_mol("terminal.slag", {"CaO": 6.0})
    ledger.load_external_mol(C7_AL_CREDIT_ACCOUNT, {"Al": 2.0})
    # Balanced C3A reaction: 6 CaO + 2 Al -> Ca3Al2O6 + 3 Ca.
    spoofed = LedgerTransition(
        name="ca_aluminothermic_c3a_credit_al",
        debits=(
            ledger.debit_mol("terminal.slag", {"CaO": 6.0}),
            ledger.debit_mol(C7_AL_CREDIT_ACCOUNT, {"Al": 2.0}),
        ),
        credits=(
            ledger.credit_mol("process.overhead_gas", {"Ca": 3.0}),
            ledger.credit_mol("terminal.slag", {"Ca3Al2O6": 1.0}),
        ),
    )

    with pytest.raises(AccountingError, match="terminal account"):
        ledger.apply(spoofed)


def test_dispatch_bound_malformed_c7_terminal_slag_rework_is_rejected(
    formula_registry,
):
    provider = _MalformedC7TerminalSlagProvider()
    registry = ProviderRegistry()
    registry.register(provider, [ChemistryIntent.CA_ALUMINOTHERMIC_STEP])
    ledger = AtomLedger(registry=formula_registry)
    ledger.load_external_mol("terminal.slag", {"CaO": 6.0})
    ledger.load_external_mol(C7_AL_CREDIT_ACCOUNT, {"Al": 2.0})
    kernel = ChemistryKernel(ledger, registry, formula_registry)

    result = kernel.dispatch(
        ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
        temperature_C=1400.0,
        pressure_bar=1e-6,
        control_inputs=_controls(),
    )
    assert result.transition is not None
    before_balances = ledger.mol_by_account()
    before_transitions = ledger.transitions

    with pytest.raises(ProposalRejected, match="terminal account"):
        kernel.commit_batch(
            ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
            result.transition,
        )

    assert ledger.mol_by_account() == before_balances
    assert ledger.transitions == before_transitions


def test_off_path_c7_terminal_slag_rework_proposal_is_rejected(formula_registry):
    provider = _provider()
    registry = ProviderRegistry()
    registry.register(provider, [ChemistryIntent.CA_ALUMINOTHERMIC_STEP])
    ledger = AtomLedger(registry=formula_registry)
    ledger.load_external_mol("terminal.slag", {"CaO": 6.0})
    ledger.load_external_mol(C7_AL_CREDIT_ACCOUNT, {"Al": 2.0})
    kernel = ChemistryKernel(ledger, registry, formula_registry)
    proposal = LedgerTransitionProposal(
        debits={
            "terminal.slag": {"CaO": 6.0},
            C7_AL_CREDIT_ACCOUNT: {"Al": 2.0},
        },
        credits={
            "process.overhead_gas": {"Ca": 3.0},
            "terminal.slag": {"Ca3Al2O6": 1.0},
        },
        reason="ca_aluminothermic_c3a_credit_al",
    )
    before_balances = ledger.mol_by_account()
    before_transitions = ledger.transitions

    with pytest.raises(ProposalRejected, match="terminal account"):
        kernel.commit_batch(ChemistryIntent.CA_ALUMINOTHERMIC_STEP, proposal)

    assert ledger.mol_by_account() == before_balances
    assert ledger.transitions == before_transitions


@pytest.mark.parametrize("reductant", ["Na", "K", "Si", "Mg"])
def test_c7_refuses_non_al_reductants(formula_registry, reductant):
    result = _provider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"CaO": 60.0},
                C7_AL_CREDIT_ACCOUNT: {"Al": 20.0},
            },
            _controls(reductant_species=reductant),
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "c7_reductant_not_al"


def test_c7_refuses_generic_reagent_inventory_al(formula_registry):
    result = _provider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"CaO": 60.0},
                "process.reagent_inventory": {"Al": 20.0},
            },
            _controls(al_source_account="process.reagent_inventory"),
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "c7_invalid_al_source_account"


@pytest.mark.parametrize(
    ("override", "reason"),
    (
        ({"p_total_mbar": 1.0}, "c7_total_pressure_outside_vacuum_envelope"),
        (
            {"active_ca_condensation_route": False},
            "c7_no_active_dedicated_ca_condensation_route",
        ),
    ),
)
def test_c7_refuses_bad_vacuum_route_or_thermo(formula_registry, override, reason):
    result = _provider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"CaO": 60.0},
                C7_AL_CREDIT_ACCOUNT: {"Al": 20.0},
            },
            _controls(**override),
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == reason


def test_c7_ignores_configured_thermo_scalar_and_uses_computed_margin(
    formula_registry,
):
    result = BuiltinCaAluminothermicStepProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"CaO": 60.0},
                C7_AL_CREDIT_ACCOUNT: {"Al": 20.0},
            },
            _controls(
                thermo_margin_kj_per_mol_o2=999.0,
                thermo_margin_favorable=True,
            ),
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert (
        result.diagnostic["reason_refused"]
        == "c7_vacuum_shifted_thermo_margin_unfavorable"
    )
    assert result.diagnostic["computed_thermo_margin_kj_per_mol_o2"] < 0.0
    assert result.diagnostic["configured_thermo_margin_kj_per_mol_o2"] == 999.0
    assert result.diagnostic["configured_thermo_margin_favorable"] is True


def test_c7_refuses_insufficient_al_when_partial_extent_forbidden(formula_registry):
    result = _provider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"CaO": 60.0},
                C7_AL_CREDIT_ACCOUNT: {"Al": 2.0},
            },
            _controls(
                objective_extent_mol=10.0,
                transport_extent_mol=10.0,
                allow_partial_extent=False,
            ),
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "c7_extent_below_objective"
    assert result.diagnostic["limiting_cap"] == "stoich"


def test_c7_capture_routes_overhead_ca_to_dedicated_train(formula_registry):
    result = _provider().dispatch(
        _request(
            formula_registry,
            {
                "process.overhead_gas": {"Ca": 5.0},
                "process.condensation_train": {},
                "process.wall_deposit": {},
            },
            _controls(
                operation="ca_capture",
                capture_mol=5.0,
                capture_fraction=0.8,
                route_uncaptured_to_wall=True,
            ),
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert dict(result.transition.debits) == {
        "process.overhead_gas": {"Ca": 5.0}
    }
    assert dict(result.transition.credits) == {
        "process.condensation_train": {"Ca": 4.0},
        "process.wall_deposit": {"Ca": 1.0},
    }
    _atom_check(result.transition, formula_registry, tol=1e-12)


def test_c7_ca_shuttle_consumes_only_surplus_after_product_reservation(
    formula_registry,
):
    result = _provider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"Al2O3": 2.0},
                "process.condensation_train": {"Ca": 10.0},
                "process.metal_phase": {},
                "terminal.slag": {},
            },
            _controls(
                operation="ca_shuttle_alumina_feedback",
                reductant_species="Ca",
                captured_ca_mol=10.0,
                ca_shuttle_rate_fraction=1.0,
                ca_shuttle_reserve_ca_product_fraction=0.7,
                ca_shuttle_targets=["Al2O3"],
            ),
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert dict(result.transition.debits) == {
        "process.condensation_train": {"Ca": 3.0},
        "process.cleaned_melt": {"Al2O3": 1.0},
    }
    assert dict(result.transition.credits) == {
        "process.cleaned_melt": {"CaO": 3.0},
        "process.metal_phase": {"Al": 2.0},
    }
    assert result.diagnostic["reserved_product_ca_mol"] == pytest.approx(7.0)
    assert result.diagnostic["shuttle_drawn_ca_mol"] == pytest.approx(3.0)
    assert result.diagnostic["unused_surplus_ca_mol"] == pytest.approx(0.0)
    _atom_check(result.transition, formula_registry, tol=1e-12)


def test_c7_set_it_to_11_knobs_saturate_without_extra_product(formula_registry):
    result = _provider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"Al2O3": 10.0},
                "process.condensation_train": {"Ca": 10.0},
                "process.metal_phase": {},
                "terminal.slag": {},
            },
            _controls(
                operation="ca_shuttle_alumina_feedback",
                reductant_species="Ca",
                captured_ca_mol=10.0,
                ca_shuttle_rate_fraction=11.0,
                ca_shuttle_reserve_ca_product_fraction=-2.0,
                ca_shuttle_targets=["Al2O3"],
            ),
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert dict(result.transition.debits)["process.condensation_train"]["Ca"] == pytest.approx(10.0)
    saturation = result.diagnostic["c7_knob_saturation"]
    assert {row["path"] for row in saturation} == {
        "campaigns.C7.ca_shuttle.rate_fraction",
        "campaigns.C7.ca_shuttle.reserve_ca_product_fraction",
    }
    assert all(row["saturated"] for row in saturation)
