from __future__ import annotations

import pytest

from engines.builtin.native_fe_metallic_tap import (
    BuiltinNativeFeMetallicTapProvider,
)
from engines.builtin.native_fe_saturation import BuiltinNativeFeSaturationProvider
from simulator.accounting import resolve_species_formula
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
from simulator.state import CampaignPhase
from tests.chemistry.conftest import _atom_check, _build_sim
from tests.native_fe_test_support import NATIVE_FE_ROUTE_ACCOUNTS


DECLARED_ACCOUNTS = frozenset({
    "process.cleaned_melt",
    "terminal.drain_tap_material",
    "process.overhead_gas",
    "reservoir.fo2_buffer",
})
METALLIC_TAP_DECLARED_ACCOUNTS = frozenset({
    "process.metal_phase",
    "terminal.drain_tap_material",
    "process.overhead_gas",
})
def _request(
    registry,
    accounts,
    native_fe_mol: float | None = None,
    *,
    control_inputs: dict | None = None,
) -> IntentRequest:
    controls = dict(control_inputs or {})
    if native_fe_mol is not None:
        controls["native_fe_mol"] = native_fe_mol
    return IntentRequest(
        intent=ChemistryIntent.NATIVE_FE_SATURATION,
        account_view=ProviderAccountView(
            accounts=accounts,
            species_formula_registry=registry,
        ),
        temperature_C=1600.0,
        pressure_bar=1e-5,
        control_inputs=controls,
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
    assert result.transition.accounts_touched() <= DECLARED_ACCOUNTS
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
    assert result.transition.reason == "native_fe_saturation_split"
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


def test_native_fe_alias_matches_per_oxide_extent_map(formula_registry):
    accounts = {"process.cleaned_melt": {"FeO": 3.0}}
    provider = BuiltinNativeFeSaturationProvider()
    legacy = provider.dispatch(
        _request(formula_registry, accounts, native_fe_mol=1.25)
    )
    mapped = provider.dispatch(
        _request(
            formula_registry,
            accounts,
            control_inputs={
                "metal_extent_mol_by_oxide": {"FeO": 1.25},
                "oxygen_account": "process.overhead_gas",
            },
        )
    )

    assert legacy.status == mapped.status == "ok"
    assert legacy.transition is not None
    assert mapped.transition is not None
    assert legacy.transition.reason == "native_fe_saturation_split"
    assert mapped.transition.reason == "melt_metal_saturation_buffer"
    assert dict(legacy.transition.debits) == dict(mapped.transition.debits)
    assert dict(legacy.transition.credits) == dict(mapped.transition.credits)
    assert legacy.transition.credits["terminal.drain_tap_material"]["Fe"] == pytest.approx(
        1.25
    )
    _atom_check(mapped.transition, formula_registry, tol=1e-12)


def test_metal_extent_map_routes_ni_fe_and_oxygen_to_buffer(
    formula_registry,
):
    result = BuiltinNativeFeSaturationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {
                    "NiO": 2.0,
                    "FeO": 2.0,
                },
            },
            control_inputs={
                "metal_extent_mol_by_oxide": {
                    "NiO": 0.5,
                    "FeO": 0.75,
                },
                "oxygen_account": "reservoir.fo2_buffer",
            },
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert dict(result.transition.debits) == {
        "process.cleaned_melt": {
            "NiO": 0.5,
            "FeO": 0.75,
        },
    }
    assert dict(result.transition.credits) == {
        # CoO has no citable condensed formation segment, so only NiO and
        # FeO contribute: each monoxide releases one half mol O2 per mol metal.
        "reservoir.fo2_buffer": {"O2": 0.625},
        "terminal.drain_tap_material": {
            "Ni": 0.5,
            "Fe": 0.75,
        },
    }
    assert result.transition.accounts_touched() <= DECLARED_ACCOUNTS
    _atom_check(result.transition, formula_registry, tol=1e-12)
    validate_atom_balance(result.transition, formula_registry)


@pytest.mark.parametrize(
    ("oxide", "extent", "available", "reason"),
    [
        ("NiO", -0.1, 1.0, "negative_metal_extent"),
        ("NiO", 1.1, 1.0, "oxide_inventory_exceeded"),
        ("CoO", 0.1, 1.0, "unsupported_oxide"),
        ("Na2O", 0.1, 1.0, "unsupported_oxide"),
    ],
)
def test_metal_extent_map_refuses_invalid_or_unsupported_oxide(
    formula_registry,
    oxide,
    extent,
    available,
    reason,
):
    result = BuiltinNativeFeSaturationProvider().dispatch(
        _request(
            formula_registry,
            {"process.cleaned_melt": {oxide: available}},
            control_inputs={
                "metal_extent_mol_by_oxide": {oxide: extent},
                "oxygen_account": "reservoir.fo2_buffer",
            },
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason"] == reason


@pytest.mark.parametrize(
    "oxygen_account",
    ["process.cleaned_melt", "", None, 1],
)
def test_metal_extent_map_refuses_invalid_oxygen_account(
    formula_registry,
    oxygen_account,
):
    result = BuiltinNativeFeSaturationProvider().dispatch(
        _request(
            formula_registry,
            {"process.cleaned_melt": {"NiO": 1.0}},
            control_inputs={
                "metal_extent_mol_by_oxide": {"NiO": 0.1},
                "oxygen_account": oxygen_account,
            },
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason"] == "invalid_oxygen_account"


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
    # At this manually imposed 1600 C melt state, use the matching 6 h C2A
    # schedule point. The 0 h 1540 C hot face is below this extreme test
    # state's Fe dew point; the production 6 h 1650 C point passes Fe to the
    # dedicated Stage-1 collector.
    sim.melt.campaign = CampaignPhase.C2A
    sim.melt.campaign_hour = 6.0
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
    before_routed_fe_kg = {
        account: sim.atom_ledger.kg_by_account(account).get("Fe", 0.0)
        for account in NATIVE_FE_ROUTE_ACCOUNTS
    }
    before = {
        account: dict(sim.atom_ledger.mol_by_account(account))
        for account in DECLARED_ACCOUNTS
    }
    before_fo2_buffer = dict(sim.atom_ledger.mol_by_account("reservoir.fo2_buffer"))
    before_metal_phase = dict(
        sim.atom_ledger.mol_by_account("process.metal_phase")
    )
    transition_count_before = len(sim.atom_ledger.transitions)
    # Extent is the production root, not the fixed-fO2 lever. The lever
    # precipitated ~10^3 mol here and is the hour-8 oxygen overshoot.
    expected_native_fe_mol, _line = sim._native_fe_precipitation_mol_to_metal_line(
        sim.melt.temperature_C + 273.15,
    )
    assert expected_native_fe_mol > 1.0e-6

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
            for account in NATIVE_FE_ROUTE_ACCOUNTS
        }
        # d-402 solid-wall equilibrium can capture Fe into the Stage-4
        # cartridge residue; include that explicit sink with gas, train,
        # and segment deposits so this pin still measures the vapor pool.
        routed_fe_kg = sum(
            float(species_kg.get("Fe", 0.0) or 0.0)
            - before_routed_fe_kg[account]
            for account, species_kg in accounts.items()
        )
        fe_mm = resolve_species_formula(
            "Fe",
            sim.species_formula_registry,
        ).molar_mass_kg_per_mol()
        assert routed_fe_kg == pytest.approx(
            partition["native_fe_vapor_mol"] * fe_mm,
            abs=1e-9,
        )
        assert sim.train.stages[1].collected_kg.get("Fe", 0.0) > 0.0
