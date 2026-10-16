from __future__ import annotations

import pytest

from engines.builtin.fe_redox_respeciation import (
    BuiltinFeRedoxRespeciationProvider,
)
from simulator.chemistry.kernel import (
    ChemistryIntent,
    IntentRequest,
    ProviderAccountView,
)
from simulator.chemistry.kernel.validation import (
    validate_atom_balance,
    validate_proposal_accounts,
)
from tests.chemistry.conftest import _atom_check, _build_sim


DECLARED_ACCOUNTS = frozenset({
    "process.cleaned_melt",
    "process.spent_reductant_residue",
    "process.overhead_gas",
    "reservoir.fo2_buffer",
})


def _request(
    registry,
    accounts,
    *,
    fO2_log: float,
    temperature_C: float = 1600.0,
    pressure_bar: float = 1.0e-5,
    control_inputs: dict | None = None,
) -> IntentRequest:
    controls = {"source": "test"}
    controls.update(control_inputs or {})
    return IntentRequest(
        intent=ChemistryIntent.FE_REDOX_RESPECIATION,
        account_view=ProviderAccountView(
            accounts=accounts,
            species_formula_registry=registry,
        ),
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        fO2_log=fO2_log,
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


def test_provider_declares_fe_redox_respeciation_authority_and_accounts():
    profile = BuiltinFeRedoxRespeciationProvider().capability_profile()

    assert profile.intents == frozenset({ChemistryIntent.FE_REDOX_RESPECIATION})
    assert profile.is_authoritative_for == frozenset({
        ChemistryIntent.FE_REDOX_RESPECIATION,
    })
    assert profile.declared_accounts == DECLARED_ACCOUNTS


def test_fe_redox_respeciation_intent_authority_is_registered(
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
    assert summary[ChemistryIntent.FE_REDOX_RESPECIATION.value][
        "authoritative"
    ] == "builtin-fe-redox-respeciation"


def test_respeciation_at_intrinsic_potential_keeps_spent_oxide_equilibrium(
    formula_registry,
):
    from simulator.accounting.ledger import AtomLedger
    from simulator.chemistry.kernel.account_filters import build_provider_account_view
    from simulator.core import PyrolysisSimulator
    from simulator.fe_redox import kress91_intrinsic_state, melt_mol_fractions_for_kress91

    accounts = {
        'process.cleaned_melt': {'SiO2': 200.0, 'FeO': 100.0, 'Fe2O3': 1.0},
        'process.spent_reductant_residue': {'Na2O': 50.0},
    }
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.atom_ledger = AtomLedger(formula_registry)
    for account, species_mol in accounts.items():
        sim.atom_ledger.load_external_mol(
            account, species_mol, source='fixture', material_origin='feedstock')
    composition = sim._melt_oxide_wt_pct()
    intrinsic = kress91_intrinsic_state(
        feo_mol=100.0, fe2o3_mol=1.0,
        mol_fractions=melt_mol_fractions_for_kress91(composition),
        T_K=2423.15, pressure_bar=1e-9, exchange_fO2_log=-9.0,
    )['fO2_log']
    provider = BuiltinFeRedoxRespeciationProvider()
    view = build_provider_account_view(
        sim.atom_ledger, provider.capability_profile().declared_accounts,
        formula_registry)
    request = IntentRequest(
        intent=ChemistryIntent.FE_REDOX_RESPECIATION,
        account_view=view, temperature_C=2150.0, pressure_bar=1e-9,
        fO2_log=intrinsic,
    )
    before_mol = sim.atom_ledger.mol_by_account()

    result = provider.dispatch(request)

    assert result.status == 'ok'
    assert result.transition is None
    assert result.diagnostic['delta_fe2o3_mol'] == pytest.approx(0.0, abs=1e-12)
    assert sim.atom_ledger.mol_by_account() == before_mol


def test_oxidizing_respeciation_consumes_explicit_o2_and_matches_kress91(
    formula_registry,
):
    result = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"FeO": 10.0, "SiO2": 20.0},
                "process.overhead_gas": {"O2": 10.0},
            },
            fO2_log=-3.0,
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    transition = result.transition
    assert transition.accounts_touched() == frozenset({
        "process.cleaned_melt",
        "process.overhead_gas",
    })
    validate_proposal_accounts(
        transition,
        BuiltinFeRedoxRespeciationProvider().capability_profile().declared_accounts,
    )
    validate_atom_balance(transition, formula_registry)
    _atom_check(transition, formula_registry, tol=1e-12)

    fe2o3_credit = transition.credits["process.cleaned_melt"]["Fe2O3"]
    assert transition.debits["process.cleaned_melt"]["FeO"] == pytest.approx(
        2.0 * fe2o3_credit,
    )
    assert transition.debits["process.overhead_gas"]["O2"] == pytest.approx(
        0.5 * fe2o3_credit,
    )
    final_ferric = 2.0 * fe2o3_credit / 10.0
    assert final_ferric == pytest.approx(
        result.diagnostic["target_ferric_fraction"],
        abs=1e-12,
    )
    assert result.diagnostic["direction"] == "oxidizing"


def test_respeciation_provider_refuses_below_authoritative_temperature_band(
    formula_registry,
):
    result = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"FeO": 10.0, "SiO2": 20.0},
                "process.overhead_gas": {"O2": 10.0},
            },
            fO2_log=-3.0,
            temperature_C=1100.0,
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason"] == (
        "fe_redox_respeciation_temperature_unauthorized"
    )
    assert result.diagnostic["temperature_band_authoritative"] is False
    assert result.diagnostic["temperature_band_case"] == "below_1200C_extrapolation"


@pytest.mark.parametrize("temperature_C", [1670.0, 2300.0, 2600.0])
def test_hot_respeciation_predicts_flagged_kress_split(formula_registry, temperature_C):
    from engines.builtin._common import composition_wt_pct_from_account_view
    from simulator.fe_redox import (
        kress91_fe3_over_sigma_fe, kress91_temperature_band_case,
        melt_mol_fractions_for_kress91,
    )

    request = _request(
        formula_registry,
        {
            "process.cleaned_melt": {"FeO": 10.0, "SiO2": 20.0},
            "process.overhead_gas": {"O2": 10.0},
        },
        fO2_log=-3.6989700043360187,  # pure O2 at 0.2 mbar = 2e-4 bar
        temperature_C=temperature_C,
        pressure_bar=2e-4,
    )
    result = BuiltinFeRedoxRespeciationProvider().dispatch(
        request,
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert result.diagnostic["temperature_band_authoritative"] is False
    assert "OUT_OF_CERTIFIED_BAND" in result.diagnostic["flags"]
    band_case = kress91_temperature_band_case(temperature_C)["case"]
    assert result.diagnostic["temperature_band_case"] == band_case
    assert band_case in result.diagnostic["flags"]
    assert result.diagnostic["authority"] == "extrapolated"
    validate_atom_balance(result.transition, formula_registry)
    expected = kress91_fe3_over_sigma_fe(
        fO2_log=request.fO2_log, T_K=temperature_C + 273.15, pressure_bar=2e-4,
        mol_fractions=melt_mol_fractions_for_kress91(
            composition_wt_pct_from_account_view(request.account_view, "process.cleaned_melt"),
        ),
    )
    assert result.transition.credits["process.cleaned_melt"]["Fe2O3"] == pytest.approx(
        5.0 * expected,
    )
    assert result.transition.debits["process.cleaned_melt"]["FeO"] == pytest.approx(
        10.0 * expected,
    )


def test_oxidizing_respeciation_can_consume_internal_evaporative_o_carrier(
    formula_registry,
):
    result = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"FeO": 10.0, "SiO2": 20.0},
                "process.overhead_gas": {},
                "reservoir.fo2_buffer": {},
            },
            fO2_log=-3.0,
            control_inputs={
                "oxygen_source": "evaporative_metal_loss_internal",
                "internal_o2_capacity_mol": 10.0,
            },
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    transition = result.transition
    assert transition.accounts_touched() == frozenset({
        "process.cleaned_melt",
        "reservoir.fo2_buffer",
    })
    validate_atom_balance(transition, formula_registry)
    _atom_check(transition, formula_registry, tol=1e-12)

    fe2o3_credit = transition.credits["process.cleaned_melt"]["Fe2O3"]
    assert transition.debits["process.cleaned_melt"]["FeO"] == pytest.approx(
        2.0 * fe2o3_credit,
    )
    assert transition.debits["reservoir.fo2_buffer"]["O2"] == pytest.approx(
        0.5 * fe2o3_credit,
    )
    assert "process.overhead_gas" not in transition.debits
    assert result.diagnostic["oxygen_source"] == (
        "evaporative_metal_loss_internal"
    )


def test_oxidizing_respeciation_can_consume_fo2_buffer_carrier(
    formula_registry,
):
    result = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"FeO": 10.0, "SiO2": 20.0},
                "process.overhead_gas": {},
                "reservoir.fo2_buffer": {},
            },
            fO2_log=-3.0,
            control_inputs={
                "oxygen_source": "fo2_buffer",
                "internal_o2_capacity_mol": 10.0,
            },
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    transition = result.transition
    assert transition.accounts_touched() == frozenset({
        "process.cleaned_melt",
        "reservoir.fo2_buffer",
    })
    validate_atom_balance(transition, formula_registry)
    _atom_check(transition, formula_registry, tol=1e-12)

    fe2o3_credit = transition.credits["process.cleaned_melt"]["Fe2O3"]
    assert transition.debits["reservoir.fo2_buffer"]["O2"] == pytest.approx(
        0.5 * fe2o3_credit,
    )
    assert result.diagnostic["oxygen_source"] == "fo2_buffer"


def test_reducing_respeciation_credits_explicit_o2_and_matches_kress91(
    formula_registry,
):
    result = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {
                    "FeO": 2.0,
                    "Fe2O3": 4.0,
                    "SiO2": 20.0,
                },
                "process.overhead_gas": {},
            },
            fO2_log=-20.0,
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    transition = result.transition
    validate_atom_balance(transition, formula_registry)
    _atom_check(transition, formula_registry, tol=1e-12)

    fe2o3_debit = transition.debits["process.cleaned_melt"]["Fe2O3"]
    assert transition.credits["process.cleaned_melt"]["FeO"] == pytest.approx(
        2.0 * fe2o3_debit,
    )
    assert transition.credits["process.overhead_gas"]["O2"] == pytest.approx(
        0.5 * fe2o3_debit,
    )
    total_fe = 2.0 + 2.0 * 4.0
    final_fe2o3 = 4.0 - fe2o3_debit
    assert (2.0 * final_fe2o3 / total_fe) == pytest.approx(
        result.diagnostic["target_ferric_fraction"],
        abs=1e-12,
    )
    assert result.diagnostic["direction"] == "reducing"


def test_oxidizing_respeciation_refuses_managed_scalar_without_ledger_o2(
    formula_registry,
):
    result = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": {"FeO": 10.0, "SiO2": 20.0},
                "process.overhead_gas": {},
            },
            fO2_log=-3.0,
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason"] == "fe_redox_respeciation_o2_unavailable"
    assert result.diagnostic["required_o2_mol"] > 0.0
    assert result.diagnostic["available_o2_mol"] == pytest.approx(0.0)
