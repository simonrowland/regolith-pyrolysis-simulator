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
from simulator.state import Atmosphere, CampaignPhase, EvaporationFlux, MOLAR_MASS
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
        # 2026-08-05 MC-1 trace wiring d1b4f5d: Co/Ni/S/Cl passengers shift
        # the lunar cleaned-melt basis; migration routing assertions below stay.
        "native_fe_mol": 1607.3611432553353,
        "native_vapor_Fe_kg": 0.1682078458614615,
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
        bound = next(
            (
                notice for notice in partition.get("notices") or []
                if notice.get("kind") == "unclosed_vacuum_hkl_upper_bound"
            ),
            None,
        )
        closure = partition.get("same_hour_headspace_closure") or {}
        selected_kg_hr = partition["native_fe_vapor_capacity_kg_hr"]
        assert selected_kg_hr > 0.0
        assert selected_kg_hr == pytest.approx(closure["rates_kg_hr"]["Fe"])
        assert partition["native_fe_vapor_mol"] * MOLAR_MASS["Fe"] / 1000.0 == pytest.approx(selected_kg_hr)
        assert partition["selected_partition"] == "same_hour_headspace_closure"
        _assert_selected_forward_pressure(sim, partition)
        assert partition["P_bulk_Pa"] > 0.0
        assert partition["reason"] == "viscous_p_bulk_transport_out_of_domain"
        assert bound is not None
        assert bound["upper_bound_kg_hr"] == pytest.approx(
            golden["native_vapor_Fe_kg"],
            abs=1e-9,
        )
        assert bound["authority"] == "unclosed_upper_bound"
        assert closure["converged"] is True
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
        # Route the closed prediction, retaining the distinct HKL bound.
        assert routed_fe_kg == pytest.approx(selected_kg_hr, abs=1e-12)
        assert selected_kg_hr < bound["upper_bound_kg_hr"]
        assert 0.0 < sim.train.stages[1].collected_kg["Fe"] <= routed_fe_kg


def _assert_selected_forward_pressure(sim, partition):
    selected_rate = partition["native_fe_vapor_capacity_kg_hr"]
    flux = EvaporationFlux(species_kg_hr={"Fe": selected_rate})
    flux.update_totals()
    transport = sim.overhead_model.estimate_transport_state(
        flux, sim.melt, p_downstream_bar=sim._headspace_downstream_pressure_bar(None),
    )
    partials = sim.overhead_model.species_partial_pressures(
        flux, transport["vapor_pressure_mbar"], sim.species_formula_registry,
    )
    assert partials["Fe"] * 100.0 == pytest.approx(partition["P_bulk_Pa"], rel=1e-8)
    notice = next(n for n in partition["notices"] if n.get("kind") == "transport_domain_extension")
    assert notice["overhead_pressure_pa"] == partition["overhead_pressure_pa"]
    assert notice["knudsen_number"] == partition["series_resistance"]["knudsen_number"]
    assert notice["authority"] == "extrapolated"
    assert partition["ledger_yields_authorized"] is True


@pytest.mark.parametrize("pool_mol", [100.0, 0.5])
def test_native_fe_selects_closed_transitional_stream(
    vapor_pressure_data, feedstocks_data, setpoints_data, pool_mol
):
    sim = _build_sim("lunar_mare_low_ti", vapor_pressure_data, feedstocks_data, setpoints_data)
    sim.melt.temperature_C = 1670.0
    sim.melt.campaign = CampaignPhase.C2A_STAGED
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    sim.melt.p_total_mbar = 0.0
    sim.overhead.headspace_temperature_K = 1943.15
    partition = sim._native_fe_partition_diagnostic(pool_mol)
    closure = partition["same_hour_headspace_closure"]
    assert closure["converged"] is True
    assert partition["native_fe_vapor_capacity_kg_hr"] == pytest.approx(closure["rates_kg_hr"]["Fe"])
    assert partition["native_fe_vapor_capacity_kg_hr"] > 0.0
    assert partition["native_fe_vapor_mol"] + partition["native_fe_tap_mol"] == pytest.approx(pool_mol)
    _assert_selected_forward_pressure(sim, partition)
