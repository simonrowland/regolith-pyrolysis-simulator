from __future__ import annotations

import pytest

from engines.builtin.fe_redox_respeciation import (
    BuiltinFeRedoxRespeciationProvider,
    fe2o3_mol_for_ferric_o2,
)
from simulator.condensation import CondensationRouteResult
from simulator.core import FE_REDOX_OXYGEN_SOURCE_OVERHEAD
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


def test_1670c_respeciation_predicts_flagged_kress_split(formula_registry):
    from engines.builtin._common import composition_wt_pct_from_account_view
    from simulator.fe_redox import (
        kress91_fe3_over_sigma_fe, melt_mol_fractions_for_kress91,
    )

    request = _request(
        formula_registry,
        {
            "process.cleaned_melt": {"FeO": 10.0, "SiO2": 20.0},
            "process.overhead_gas": {"O2": 10.0},
        },
        fO2_log=-3.6989700043360187,  # pure O2 at 0.2 mbar = 2e-4 bar
        temperature_C=1670.0,
        pressure_bar=2e-4,
    )
    result = BuiltinFeRedoxRespeciationProvider().dispatch(
        request,
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert result.diagnostic["temperature_band_authoritative"] is False
    assert "OUT_OF_CERTIFIED_BAND" in result.diagnostic["flags"]
    assert result.diagnostic["temperature_band_case"] == (
        "1630C_2100C_extrapolation_experimentally_confirmed"
    )
    validate_atom_balance(result.transition, formula_registry)
    expected = kress91_fe3_over_sigma_fe(
        fO2_log=request.fO2_log, T_K=1943.15, pressure_bar=2e-4,
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


def test_overhead_oxidation_does_not_consume_evaporation_coproduct_o2(
    formula_registry,
):
    """The same overhead O2 oxidizes when it is headspace gas, and is
    refused when that inventory is this hour's evaporation coproduct."""

    accounts = {
        "process.cleaned_melt": {"FeO": 10.0, "SiO2": 20.0},
        "process.overhead_gas": {"O2": 10.0},
    }
    funded = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(formula_registry, accounts, fO2_log=-3.0)
    )
    excluded = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            accounts,
            fO2_log=-3.0,
            control_inputs={"evaporation_coproduct_o2_mol": 10.0},
        )
    )

    assert funded.status == "ok"
    assert funded.transition is not None
    assert funded.transition.credits["process.cleaned_melt"]["Fe2O3"] > 0.0
    assert excluded.status == "refused"
    assert excluded.transition is None
    assert excluded.diagnostic["reason"] == "fe_redox_respeciation_o2_unavailable"
    assert excluded.diagnostic["available_o2_mol"] == pytest.approx(0.0)


def test_fo2_buffer_oxidation_ignores_evaporation_coproduct_exclusion(
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
                "evaporation_coproduct_o2_mol": 10.0,
            },
        )
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert result.transition.debits["reservoir.fo2_buffer"]["O2"] > 0.0
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


def test_overhead_oxidation_spends_a_tap_short_of_the_kress_delta(
    formula_registry,
):
    """2 FeO + 1/2 O2 = Fe2O3. A positive tap short of the delta is
    spent. Zero available leaves the couple unchanged. Available O2 at
    or above the delta's O2 applies the full delta."""

    melt = {"FeO": 10.0, "SiO2": 20.0}
    funded = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": dict(melt),
                "process.overhead_gas": {"O2": 10.0},
            },
            fO2_log=-3.0,
        )
    )
    required_o2_mol = float(funded.diagnostic["required_o2_mol"])
    assert funded.status == "ok"
    assert required_o2_mol > 1.0e-6
    assert required_o2_mol <= 10.0
    assert funded.transition.credits["process.cleaned_melt"]["Fe2O3"] == (
        pytest.approx(fe2o3_mol_for_ferric_o2(required_o2_mol))
    )

    none = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": dict(melt),
                "process.overhead_gas": {},
            },
            fO2_log=-3.0,
        )
    )
    assert none.status == "refused"
    assert none.transition is None
    assert none.diagnostic["available_o2_mol"] == pytest.approx(0.0)

    tap_o2_mol = 0.5 * required_o2_mol
    partial = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": dict(melt),
                "process.overhead_gas": {"O2": tap_o2_mol},
            },
            fO2_log=-3.0,
        )
    )
    assert partial.status == "ok"
    assert partial.transition is not None
    assert partial.diagnostic["respeciation_status"] == "partial"
    assert partial.diagnostic["applied_o2_mol"] == pytest.approx(tap_o2_mol)
    assert partial.diagnostic["unfunded_o2_mol"] == pytest.approx(
        required_o2_mol - tap_o2_mol,
    )
    assert partial.transition.debits["process.overhead_gas"]["O2"] == (
        pytest.approx(tap_o2_mol)
    )
    assert partial.transition.credits["process.cleaned_melt"]["Fe2O3"] == (
        pytest.approx(fe2o3_mol_for_ferric_o2(tap_o2_mol))
    )
    validate_atom_balance(partial.transition, formula_registry)

    coproduct_o2_mol = 5.0
    mixed = BuiltinFeRedoxRespeciationProvider().dispatch(
        _request(
            formula_registry,
            {
                "process.cleaned_melt": dict(melt),
                "process.overhead_gas": {"O2": tap_o2_mol + coproduct_o2_mol},
            },
            fO2_log=-3.0,
            control_inputs={"evaporation_coproduct_o2_mol": coproduct_o2_mol},
        )
    )
    assert mixed.status == "ok"
    assert mixed.diagnostic["available_o2_mol"] == pytest.approx(tap_o2_mol)
    assert mixed.diagnostic["applied_o2_mol"] == pytest.approx(tap_o2_mol)
    assert mixed.transition.debits["process.overhead_gas"]["O2"] == (
        pytest.approx(tap_o2_mol)
    )


# Mixed pool: preexisting overhead, tap, and evaporation coproduct.
# The debit is larger than the legitimate oxidant and smaller than the
# coproduct, so a gross counter treats the debit as if it ate the tap.
_PREEXISTING_O2_MOL = 0.4
_TAP_O2_MOL = 0.6
_COPRODUCT_O2_MOL = 3.0
_DEBIT_O2_MOL = 1.5
_LEGITIMATE_O2_MOL = _PREEXISTING_O2_MOL + _TAP_O2_MOL


def _o2_mol(sim, account: str) -> float:
    return float(
        sim.atom_ledger.mol_by_account(account).get("O2", 0.0) or 0.0
    )


def _product_rate_kg_hr(sim, species: str, sp_data: dict, o2_mol: float) -> float:
    stoich = sim._evaporation_stoich(species, sp_data)
    per_kg = abs(float(stoich["O2_per_product_kg"]))
    molar = sim.species_formula_registry["O2"].molar_mass_kg_per_mol()
    return o2_mol * molar / (sim.process_dt_hr() * per_kg)


def _plant_mixed_overhead(sim) -> None:
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -2.0
    sim.melt.oxygen_reservoir.reference_T_K = (
        float(sim.melt.temperature_C) + 273.15
    )
    sim._sync_oxygen_reservoir_mirror()
    sim._evaporation_coproduct_o2_mol_this_hr = 0.0
    assert _o2_mol(sim, "process.overhead_gas") == pytest.approx(0.0, abs=1.0e-12)
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": _LEGITIMATE_O2_MOL},
        source="test preexisting overhead plus tap",
        material_origin="feedstock",
    )
    fe_data = sim.vapor_pressures["metals"]["Fe"]
    rate = _product_rate_kg_hr(sim, "Fe", fe_data, _COPRODUCT_O2_MOL)
    sim._credit_evaporation_transition(
        "Fe",
        rate,
        rate,
        fe_data,
        apply_evaporative_redox_source_terms=False,
    )
    assert sim._evaporation_coproduct_o2_mol_this_hr == pytest.approx(
        _COPRODUCT_O2_MOL,
        abs=1.0e-9,
    )
    assert _o2_mol(sim, "process.overhead_gas") == pytest.approx(
        _LEGITIMATE_O2_MOL + _COPRODUCT_O2_MOL,
        abs=1.0e-9,
    )


def _ensure_parent_oxide_kg(sim, oxide: str, need_kg: float) -> None:
    have_kg = float(
        sim.atom_ledger.kg_by_account("process.cleaned_melt").get(oxide, 0.0)
        or 0.0
    )
    if have_kg + 1.0e-9 >= need_kg:
        return
    molar = sim.species_formula_registry[oxide].molar_mass_kg_per_mol()
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {oxide: (need_kg - have_kg) / molar + 1.0},
        source="test parent oxide for the overhead debit",
        material_origin="feedstock",
    )


def _debit_overhead_o2(sim, kind: str) -> None:
    if kind == "cr_oxide":
        sp_data = sim.vapor_pressures["oxide_vapors"]["CrO2"]
        stoich = sim._evaporation_stoich("CrO2", sp_data)
        rate = _product_rate_kg_hr(sim, "CrO2", sp_data, _DEBIT_O2_MOL)
        _ensure_parent_oxide_kg(
            sim,
            stoich["parent_oxide"],
            rate * sim.process_dt_hr() * float(stoich["oxide_per_product_kg"]),
        )
        sim._credit_evaporation_transition(
            "CrO2",
            rate,
            rate,
            sp_data,
            apply_evaporative_redox_source_terms=False,
        )
        return
    if kind != "condensation":
        raise AssertionError(kind)
    uptake_mol = 4.0 * _DEBIT_O2_MOL
    molar = sim.species_formula_registry["Na"].molar_mass_kg_per_mol()
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"Na": uptake_mol},
        source="test condensation vapor",
        material_origin="feedstock",
    )
    route = CondensationRouteResult(
        stage3_reactive_alkali_by_species={
            "Na": {
                "uptake_mol": uptake_mol,
                "oxide_species": "Na2O",
                "oxide_product_mol": uptake_mol / 2.0,
                "oxygen_debit_mol": _DEBIT_O2_MOL,
            },
        },
    )
    credited_kg = sim._dispatch_condensation_route(
        "Na",
        uptake_mol * molar,
        sim.vapor_pressures["metals"]["Na"],
        route,
    )
    assert credited_kg > 0.0


def _overhead_split_takes_the_tap(sim) -> dict:
    assert abs(sim.melt.oxygen_reservoir.exchange_o2_mol) <= 1.0e-12
    fe2o3_before = float(
        sim.atom_ledger.mol_by_account("process.cleaned_melt").get("Fe2O3", 0.0)
        or 0.0
    )
    counter_before = float(sim._evaporation_coproduct_o2_mol_this_hr)
    diagnostic = sim._apply_fe_redox_respeciation(
        oxygen_source=FE_REDOX_OXYGEN_SOURCE_OVERHEAD,
        fO2_log_override=-2.0,
        gate_authority={"liquidus_T_C": 1200.0, "source": "test"},
    )
    assert float(sim._evaporation_coproduct_o2_mol_this_hr) == pytest.approx(
        counter_before,
        abs=1.0e-12,
    )
    assert diagnostic["status"] == "ok", diagnostic
    assert diagnostic["oxygen_source"] == FE_REDOX_OXYGEN_SOURCE_OVERHEAD
    assert diagnostic["respeciation_status"] == "partial", diagnostic
    assert float(diagnostic["required_o2_mol"]) > _LEGITIMATE_O2_MOL, diagnostic
    assert diagnostic["available_o2_mol"] == pytest.approx(
        _LEGITIMATE_O2_MOL,
        abs=1.0e-9,
    )
    assert diagnostic["applied_o2_mol"] == pytest.approx(
        _LEGITIMATE_O2_MOL,
        abs=1.0e-9,
    )
    assert float(diagnostic["applied_o2_mol"]) >= _TAP_O2_MOL
    fe2o3_after = float(
        sim.atom_ledger.mol_by_account("process.cleaned_melt").get("Fe2O3", 0.0)
        or 0.0
    )
    assert fe2o3_after - fe2o3_before == pytest.approx(
        fe2o3_mol_for_ferric_o2(_LEGITIMATE_O2_MOL),
        abs=1.0e-9,
    )
    assert _o2_mol(sim, "process.overhead_gas") == pytest.approx(
        _COPRODUCT_O2_MOL - _DEBIT_O2_MOL,
        abs=1.0e-9,
    )
    return diagnostic


@pytest.mark.parametrize("kind", ["condensation", "cr_oxide"])
def test_overhead_debit_shrink_lets_fe_split_take_the_remaining_tap(
    kind,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Deleting the counter shrink at the condensation debit or the
    Cr-oxide evaporation debit restores the gross-counter refusal, and
    this overhead split no longer takes the remaining tap."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    assert sim.process_dt_hr() == 1.0
    _plant_mixed_overhead(sim)
    _debit_overhead_o2(sim, kind)
    assert sim._evaporation_coproduct_o2_mol_this_hr == pytest.approx(
        _COPRODUCT_O2_MOL - _DEBIT_O2_MOL,
        abs=1.0e-9,
    )
    _overhead_split_takes_the_tap(sim)
