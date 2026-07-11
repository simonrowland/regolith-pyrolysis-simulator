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
