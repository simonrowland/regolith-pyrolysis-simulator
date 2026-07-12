from __future__ import annotations

import pytest

from engines.builtin.native_fe_metallic_tap import (
    BuiltinNativeFeMetallicTapProvider,
)
from engines.builtin.native_fe_saturation import BuiltinNativeFeSaturationProvider
from simulator.chemistry.kernel import (
    AccountFilterViolation,
    ChemistryIntent,
    IntentRequest,
    LedgerTransitionProposal,
    ProviderAccountView,
)
from simulator.chemistry.kernel.validation import (
    validate_atom_balance,
    validate_proposal_accounts,
)
from tests.chemistry.conftest import _atom_check, _build_sim


DECLARED_ACCOUNTS = frozenset({
    "process.cleaned_melt",
    "terminal.drain_tap_material",
    "process.overhead_gas",
})
METALLIC_TAP_DECLARED_ACCOUNTS = frozenset({
    "process.metal_phase",
    "terminal.drain_tap_material",
    "process.overhead_gas",
})
PINNED_NATIVE_FE_MIGRATION_GOLDENS = {
    "lunar_mare_low_ti": {
        "native_fe_mol": 1610.7768609700126,
        "native_vapor_Fe_kg": 0.15843535412267737,
    },
    "mars_basalt": {
        # Recomputed after 9de6ffb8: counterfactual tracing attributes the
        # move to the corrected Stage-0 carbonate-decomposition sigmoid width
        # (one thermodynamic e-fold; old-width emulation reproduces the prior
        # golden within tolerance) — NOT the carbon-partition change,
        # which changes cleaned-melt CaO and therefore FeO activity.  The
        # regression below proves this remains the FeO-saturation path: the
        # later metallic-tap fold never touches process.metal_phase here.
        "native_fe_mol": 1683.5583423071594,
    },
}


def _request(registry, accounts, native_fe_mol: float) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.NATIVE_FE_SATURATION,
        account_view=ProviderAccountView(
            accounts=accounts,
            species_formula_registry=registry,
        ),
        temperature_C=1600.0,
        pressure_bar=1e-5,
        control_inputs={"native_fe_mol": native_fe_mol},
    )


@pytest.fixture(scope="module")
def formula_registry(vapor_pressure_data, feedstocks_data, setpoints_data):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    return sim.species_formula_registry


def test_provider_declares_only_native_fe_intent_and_scoped_accounts():
    profile = BuiltinNativeFeSaturationProvider().capability_profile()

    assert profile.intents == frozenset({ChemistryIntent.NATIVE_FE_SATURATION})
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.NATIVE_FE_SATURATION}
    )
    assert profile.declared_accounts == DECLARED_ACCOUNTS


def test_provider_emits_only_declared_accounts(formula_registry):
    result = BuiltinNativeFeSaturationProvider().dispatch(
        _request(
            formula_registry,
            {"process.cleaned_melt": {"FeO": 3.0}},
            native_fe_mol=1.25,
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert result.transition.accounts_touched() == DECLARED_ACCOUNTS
    validate_proposal_accounts(
        result.transition,
        BuiltinNativeFeSaturationProvider().capability_profile().declared_accounts,
    )


def test_native_fe_intent_authority_is_registered(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    summary = sim._chem_registry.capability_summary()
    assert summary[ChemistryIntent.NATIVE_FE_SATURATION.value][
        "authoritative"
    ] == "builtin-native-fe-saturation"
    assert summary[ChemistryIntent.NATIVE_FE_METALLIC_TAP.value][
        "authoritative"
    ] == "builtin-native-fe-metallic-tap"


def test_account_filter_rejects_wider_native_fe_proposal():
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"FeO": 1.0}},
        credits={"process.metal_phase": {"Fe": 1.0}},
        reason="native_fe_saturation_bad_account",
    )

    with pytest.raises(AccountFilterViolation):
        validate_proposal_accounts(proposal, DECLARED_ACCOUNTS)


def test_native_fe_proposal_balances_atoms(formula_registry):
    result = BuiltinNativeFeSaturationProvider().dispatch(
        _request(
            formula_registry,
            {"process.cleaned_melt": {"FeO": 3.0}},
            native_fe_mol=1.25,
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert dict(result.transition.debits) == {
        "process.cleaned_melt": {"FeO": 1.25},
    }
    assert dict(result.transition.credits) == {
        "terminal.drain_tap_material": {"Fe": 1.25},
        "process.overhead_gas": {"O2": 0.625},
    }
    _atom_check(result.transition, formula_registry, tol=1e-12)
    validate_atom_balance(result.transition, formula_registry)


def test_native_fe_proposal_partitions_tap_and_vapor(formula_registry):
    request = IntentRequest(
        intent=ChemistryIntent.NATIVE_FE_SATURATION,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"FeO": 3.0}},
            species_formula_registry=formula_registry,
        ),
        temperature_C=1600.0,
        pressure_bar=1e-5,
        control_inputs={
            "native_fe_mol": 2.0,
            "native_fe_vapor_mol": 0.25,
        },
    )

    result = BuiltinNativeFeSaturationProvider().dispatch(request)

    assert result.status == "ok"
    assert result.transition is not None
    assert dict(result.transition.debits) == {
        "process.cleaned_melt": {"FeO": 1.75},
    }
    assert dict(result.transition.credits) == {
        "process.overhead_gas": {"O2": 0.875},
        "terminal.drain_tap_material": {"Fe": 1.75},
    }
    assert result.diagnostic["overhead_fe_credit_mol"] == pytest.approx(0.0)
    assert result.diagnostic["routed_fe_vapor_mol"] == pytest.approx(0.25)
    assert result.diagnostic["tap_fe_credit_mol"] == pytest.approx(1.75)
    _atom_check(result.transition, formula_registry, tol=1e-12)
    validate_atom_balance(result.transition, formula_registry)


def test_native_metal_proposal_partitions_without_creating_oxygen(formula_registry):
    profile = BuiltinNativeFeMetallicTapProvider().capability_profile()
    assert profile.intents == frozenset(
        {ChemistryIntent.NATIVE_FE_METALLIC_TAP}
    )
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.NATIVE_FE_METALLIC_TAP}
    )
    assert profile.declared_accounts == METALLIC_TAP_DECLARED_ACCOUNTS

    request = IntentRequest(
        intent=ChemistryIntent.NATIVE_FE_METALLIC_TAP,
        account_view=ProviderAccountView(
            accounts={"process.metal_phase": {"Fe": 3.0}},
            species_formula_registry=formula_registry,
        ),
        temperature_C=1650.0,
        pressure_bar=1e-5,
        control_inputs={
            "native_fe_mol": 2.0,
            "native_fe_vapor_mol": 0.25,
        },
    )

    result = BuiltinNativeFeMetallicTapProvider().dispatch(request)

    assert result.status == "ok"
    assert result.transition is not None
    assert result.transition.reason == "native_fe_metal_partition"
    assert dict(result.transition.debits) == {
        "process.metal_phase": {"Fe": 2.0},
    }
    assert dict(result.transition.credits) == {
        "process.overhead_gas": {"Fe": 0.25},
        "terminal.drain_tap_material": {"Fe": 1.75},
    }
    assert "O2" not in result.transition.credits["process.overhead_gas"]
    assert result.transition.accounts_touched() == METALLIC_TAP_DECLARED_ACCOUNTS
    assert result.diagnostic["overhead_o2_credit_mol"] == pytest.approx(0.0)
    validate_proposal_accounts(result.transition, profile.declared_accounts)
    _atom_check(result.transition, formula_registry, tol=1e-12)
    validate_atom_balance(result.transition, formula_registry)


def test_metallic_tap_account_filter_rejects_cleaned_melt_proposal():
    proposal = LedgerTransitionProposal(
        debits={"process.cleaned_melt": {"FeO": 1.0}},
        credits={"terminal.drain_tap_material": {"Fe": 1.0}},
        reason="native_fe_metallic_tap_bad_account",
    )

    with pytest.raises(AccountFilterViolation):
        validate_proposal_accounts(proposal, METALLIC_TAP_DECLARED_ACCOUNTS)


def _native_fe_saturating_sim(
    feedstock_id: str,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    additives = {"C": 30.0} if feedstock_id == "mars_basalt" else None
    sim = _build_sim(
        feedstock_id,
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg=additives,
    )
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    sim.melt.fO2_log = -10.0
    sim.melt.melt_fO2_log = -10.0
    return sim


@pytest.mark.parametrize("feedstock_id", ["lunar_mare_low_ti", "mars_basalt"])
def test_kernel_native_fe_split_matches_pinned_migration_golden(
    feedstock_id,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _native_fe_saturating_sim(
        feedstock_id,
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    before = {
        account: dict(sim.atom_ledger.mol_by_account(account))
        for account in DECLARED_ACCOUNTS
    }
    before_fo2_buffer = dict(sim.atom_ledger.mol_by_account("reservoir.fo2_buffer"))
    before_metal_phase = dict(
        sim.atom_ledger.mol_by_account("process.metal_phase")
    )
    transition_count_before = len(sim.atom_ledger.transitions)
    golden = PINNED_NATIVE_FE_MIGRATION_GOLDENS[feedstock_id]
    expected_native_fe_mol = golden["native_fe_mol"]

    sim._apply_native_fe_saturation_split()

    after = {
        account: dict(sim.atom_ledger.mol_by_account(account))
        for account in DECLARED_ACCOUNTS
    }
    after_fo2_buffer = dict(sim.atom_ledger.mol_by_account("reservoir.fo2_buffer"))
    after_metal_phase = dict(
        sim.atom_ledger.mol_by_account("process.metal_phase")
    )
    transition_names = [
        transition.name
        for transition in sim.atom_ledger.transitions[transition_count_before:]
    ]
    feo_debit_mol = (
        before["process.cleaned_melt"].get("FeO", 0.0)
        - after["process.cleaned_melt"].get("FeO", 0.0)
    )
    tap_fe_credit_mol = (
        after["terminal.drain_tap_material"].get("Fe", 0.0)
        - before["terminal.drain_tap_material"].get("Fe", 0.0)
    )
    overhead_o2_credit_mol = (
        after["process.overhead_gas"].get("O2", 0.0)
        - before["process.overhead_gas"].get("O2", 0.0)
    )
    retained_o2_mol = (
        after_fo2_buffer.get("O2", 0.0)
        - before_fo2_buffer.get("O2", 0.0)
    )
    partition = sim._compute_fe_redox_split_diagnostic()["native_fe_partition"]
    assert after_metal_phase == before_metal_phase
    assert transition_names.count("native_fe_saturation_split") == 1
    assert "native_fe_metal_partition" not in transition_names
    assert partition["native_fe_source_account"] == "process.cleaned_melt"
    assert feo_debit_mol == pytest.approx(expected_native_fe_mol, abs=2e-2)
    assert partition["native_fe_pool_mol"] == pytest.approx(
        expected_native_fe_mol,
        abs=2e-2,
    )
    assert tap_fe_credit_mol + partition["native_fe_vapor_mol"] == pytest.approx(
        partition["native_fe_pool_mol"],
        abs=1e-9,
    )
    # 2026-07-02 re-speciation #82: vapor Fe retains its oxide O in fO2 buffer.
    assert overhead_o2_credit_mol + retained_o2_mol == pytest.approx(
        0.5 * partition["native_fe_pool_mol"], abs=1e-9
    )
    assert retained_o2_mol == pytest.approx(
        0.5 * partition["native_fe_vapor_mol"], abs=1e-9
    )
    if feedstock_id == "lunar_mare_low_ti":
        accounts = {
            account: sim.atom_ledger.kg_by_account(account)
            for account in (
                "process.overhead_gas",
                "process.condensation_train",
                "process.wall_deposit_segment_stage_0_to_stage_1",
                "process.wall_deposit_segment_stage_1_to_stage_2",
            )
        }
        routed_fe_kg = sum(
            float(species_kg.get("Fe", 0.0) or 0.0)
            for species_kg in accounts.values()
        )
        assert routed_fe_kg == pytest.approx(
            golden["native_vapor_Fe_kg"],
            abs=1e-9,
        )
        assert sim.train.stages[1].collected_kg.get("Fe", 0.0) > 0.0
