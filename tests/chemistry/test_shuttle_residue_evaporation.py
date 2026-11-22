"""Shuttle Na2O in the residue account evaporates with native Na2O.

The C3 alkali shuttle credits Na2O to process.spent_reductant_residue.
Evaporation must draw that account in proportion to its share of the
melt-resident species. Yield completeness still sees a separate account.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from engines.builtin.evaporation_transition import (
    BuiltinEvaporationTransitionProvider,
)
from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.accounting.formulas import resolve_species_formula
from simulator.capacity_coupling import CapacityShadowRefusal
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import (
    IntentRequest,
    IntentResult,
    ProviderAccountView,
)
from simulator.melt_resident import (
    CLEANED_MELT_ACCOUNT,
    split_parent_debit_mol,
)
from simulator.state import CampaignPhase, EvaporationFlux
from simulator.thermal_train import FiniteCapacity
from tests.chemistry.conftest import _build_sim

CLEANED = CLEANED_MELT_ACCOUNT
RESIDUE = SPENT_REDUCTANT_RESIDUE_ACCOUNT


def _lunar(vapor_pressure_data, feedstocks_data, setpoints_data):
    return _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )


def _species_data(sim, species: str) -> dict:
    return (
        sim.vapor_pressures.get("metals", {}).get(species, {})
        or sim.vapor_pressures.get("oxide_vapors", {}).get(species, {})
    )


def _set_c4(sim) -> None:
    sim.melt.campaign = CampaignPhase.C4
    sim.melt.temperature_C = 1670.0
    sim.melt.p_total_mbar = 0.2


def _seed_equal_residue(sim, species: str) -> float:
    native_kg = float(sim.atom_ledger.kg_by_account(CLEANED)[species])
    sim.atom_ledger.load_external(
        RESIDUE,
        {species: native_kg},
        source="c3_na_shuttle_reduction",
        material_origin="reagent",
    )
    sim._project_cleaned_melt_from_atom_ledger()
    return native_kg


def _na_draw(sim, raw_rate_kg_hr: float) -> tuple[float, float]:
    flux = EvaporationFlux(species_kg_hr={"Na": raw_rate_kg_hr})
    flux.update_totals()
    smoothed = sim._apply_analytic_evaporation_depletion(flux)
    stoich = sim._evaporation_stoich("Na", _species_data(sim, "Na"))
    rate = float(smoothed.species_kg_hr.get("Na", 0.0))
    return rate, rate * float(stoich["oxide_per_product_kg"])


def _status_bearing_carrier(species: str) -> dict:
    return {
        "species_id": species,
        "pressure": {"kind": "value", "pa": 1.0},
        "flux": {"kind": "eligible", "alpha_ref": "test:alpha"},
        "is_refused": False,
        "is_union_flux_eligible": True,
        "is_flux_active": True,
        "validation_status": "modeled-PENDING",
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
    }


def _account_kg(sim, account: str, species: str) -> float:
    return float(sim.atom_ledger.kg_by_account(account).get(species, 0.0))


def test_split_parent_debit_mol_conserves_mol_and_keeps_the_cleaned_float():
    oxide_mol = 0.12345678901234567
    only_cleaned = split_parent_debit_mol(
        oxide_mol,
        {CLEANED: 4.0},
    )
    assert set(only_cleaned) == {CLEANED}
    assert only_cleaned[CLEANED].hex() == oxide_mol.hex()

    split = split_parent_debit_mol(
        oxide_mol,
        {CLEANED: 1.0, RESIDUE: 3.0},
    )
    assert sum(split.values()) == oxide_mol
    assert (
        split[CLEANED] / split[RESIDUE]
    ) == pytest.approx(1.0 / 3.0)

    residue_only = split_parent_debit_mol(oxide_mol, {RESIDUE: 2.0})
    assert set(residue_only) == {RESIDUE}
    assert sum(residue_only.values()) == oxide_mol
    assert split_parent_debit_mol(oxide_mol, {}) == {}

    with pytest.raises(ValueError, match="positive finite"):
        split_parent_debit_mol(0.0, {CLEANED: 1.0})


@pytest.mark.xdist_group("serial")
def test_c4_shuttle_na2o_evaporates_with_total_melt_inventory(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    """C4 Na removal scales with native plus shuttle Na2O, and the ledger closes."""

    sim = _lunar(vapor_pressure_data, feedstocks_data, setpoints_data)
    _set_c4(sim)
    native_kg = float(sim.atom_ledger.kg_by_account(CLEANED)["Na2O"])
    stoich = sim._evaporation_stoich("Na", _species_data(sim, "Na"))
    oxide_per_product = float(stoich["oxide_per_product_kg"])
    raw_rate = 50.0 * native_kg / oxide_per_product

    _rate_without, draw_without = _na_draw(sim, raw_rate)
    _seed_equal_residue(sim, "Na2O")
    rate_with, draw_with = _na_draw(sim, raw_rate)
    residue_kg = _account_kg(sim, RESIDUE, "Na2O")

    assert draw_with > native_kg
    assert draw_with > draw_without * 1.5
    assert draw_with < native_kg + residue_kg

    before_cleaned = _account_kg(sim, CLEANED, "Na2O")
    before_residue = _account_kg(sim, RESIDUE, "Na2O")
    share = before_cleaned / (before_cleaned + before_residue)
    sim._credit_evaporation_transition(
        "Na",
        rate_with,
        rate_with,
        _species_data(sim, "Na"),
        apply_evaporative_redox_source_terms=False,
    )
    cleaned_loss = before_cleaned - _account_kg(sim, CLEANED, "Na2O")
    residue_loss = before_residue - _account_kg(sim, RESIDUE, "Na2O")

    assert cleaned_loss > 0.0
    assert residue_loss > 0.0
    assert cleaned_loss + residue_loss > native_kg
    assert cleaned_loss / (cleaned_loss + residue_loss) == pytest.approx(
        share, rel=1e-9,
    )
    sim.atom_ledger.assert_balanced()
    origins = sim.atom_ledger.origin_atom_moles_by_account()[
        "process.overhead_gas"
    ]["Na"]
    assert origins["feedstock"] > 0.0
    assert origins["reagent"] > 0.0
    assert origins["feedstock"] == pytest.approx(origins["reagent"], rel=1e-6)


@pytest.mark.xdist_group("serial")
def test_provider_splits_residue_and_keeps_cleaned_only_debit_float(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _lunar(vapor_pressure_data, feedstocks_data, setpoints_data)
    provider = BuiltinEvaporationTransitionProvider()
    stoich = sim._evaporation_stoich("Na", _species_data(sim, "Na"))
    rate_kg_hr = 0.01
    controls = {
        "species": "Na",
        "stoich": dict(stoich),
        "sp_data": {},
        "rate_kg_hr": rate_kg_hr,
        "remaining_kg_hr": rate_kg_hr,
        "dt_hr": 1.0,
        "available_kg": 10.0,
    }

    def dispatch(accounts: dict):
        request = IntentRequest(
            intent=ChemistryIntent.EVAPORATION_TRANSITION,
            account_view=ProviderAccountView(
                accounts=accounts,
                species_formula_registry=sim.species_formula_registry,
            ),
            temperature_C=1670.0,
            pressure_bar=2.0e-7,
            control_inputs=controls,
        )
        result = provider.dispatch(request)
        assert result.status == "ok"
        assert result.transition is not None
        return result.transition

    empty_sides = {
        "process.overhead_gas": {},
        "process.condensation_train": {},
        "reservoir.fo2_buffer": {},
    }
    solo = dispatch({CLEANED: {"Na2O": 2.0}, **empty_sides})
    both = dispatch({
        CLEANED: {"Na2O": 1.0},
        RESIDUE: {"Na2O": 1.0},
        **empty_sides,
    })
    oxide_mol = (
        rate_kg_hr
        * float(stoich["oxide_per_product_kg"])
        / resolve_species_formula(
            "Na2O", sim.species_formula_registry,
        ).molar_mass_kg_per_mol()
    )

    assert set(solo.debits) == {CLEANED}
    assert solo.debits[CLEANED]["Na2O"].hex() == oxide_mol.hex()
    assert set(both.debits) == {CLEANED, RESIDUE}
    assert (
        both.debits[CLEANED]["Na2O"] + both.debits[RESIDUE]["Na2O"]
    ) == solo.debits[CLEANED]["Na2O"]
    assert both.debits[RESIDUE]["Na2O"] > 0.0


@pytest.mark.xdist_group("serial")
def test_route_to_condensation_debits_residue_na2o(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _lunar(vapor_pressure_data, feedstocks_data, setpoints_data)
    _set_c4(sim)
    _seed_equal_residue(sim, "Na2O")
    total_kg = (
        _account_kg(sim, CLEANED, "Na2O")
        + _account_kg(sim, RESIDUE, "Na2O")
    )
    stoich = sim._evaporation_stoich("Na", _species_data(sim, "Na"))
    rate = 0.2 * total_kg / float(stoich["oxide_per_product_kg"])
    before_cleaned = _account_kg(sim, CLEANED, "Na2O")
    before_residue = _account_kg(sim, RESIDUE, "Na2O")
    flux = EvaporationFlux(
        species_kg_hr={"Na": rate},
        total_kg_hr=rate,
        carrier_authority_by_species={"Na": _status_bearing_carrier("Na")},
    )
    sim._configure_condensation_operating_conditions(flux)
    sim._route_to_condensation(flux)

    assert _account_kg(sim, CLEANED, "Na2O") < before_cleaned
    assert _account_kg(sim, RESIDUE, "Na2O") < before_residue
    sim.atom_ledger.assert_balanced()


@pytest.mark.xdist_group("serial")
def test_native_fe_vapor_debit_includes_residue_feo(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _lunar(vapor_pressure_data, feedstocks_data, setpoints_data)
    _set_c4(sim)
    _seed_equal_residue(sim, "FeO")
    before_cleaned = _account_kg(sim, CLEANED, "FeO")
    before_residue = _account_kg(sim, RESIDUE, "FeO")
    record = _status_bearing_carrier("Fe")
    original_route = sim.condensation_model.route

    def route(flux, melt, *args, **kwargs):
        authority = dict(getattr(flux, "carrier_authority_by_species", {}) or {})
        authority.setdefault("Fe", record)
        flux.carrier_authority_by_species = authority
        return original_route(flux, melt, *args, **kwargs)

    sim.condensation_model.route = route
    try:
        result = sim._route_native_fe_vapor_to_condensation(1.0e-3)
    finally:
        sim.condensation_model.route = original_route

    cleaned_loss = before_cleaned - _account_kg(sim, CLEANED, "FeO")
    residue_loss = before_residue - _account_kg(sim, RESIDUE, "FeO")
    feo_mw = resolve_species_formula(
        "FeO", sim.species_formula_registry,
    ).molar_mass_kg_per_mol()

    assert result["native_fe_vapor_route_status"] == "committed"
    assert cleaned_loss > 0.0
    assert residue_loss > 0.0
    assert result["native_fe_vapor_feo_debit_mol"] == pytest.approx(
        (cleaned_loss + residue_loss) / feo_mw,
        rel=1e-9,
    )
    sim.atom_ledger.assert_balanced()


@pytest.mark.xdist_group("serial")
def test_capacity_shadow_depletion_is_not_capped_at_native_na2o(
    vapor_pressure_data, feedstocks_data, setpoints_data, monkeypatch,
):
    sim = _lunar(vapor_pressure_data, feedstocks_data, setpoints_data)
    _set_c4(sim)
    native_kg = _seed_equal_residue(sim, "Na2O")
    stoich = sim._evaporation_stoich("Na", _species_data(sim, "Na"))
    oxide_per_product = float(stoich["oxide_per_product_kg"])
    raw_rate = 50.0 * native_kg / oxide_per_product
    _capacity, cold_train = sim._cold_train_capacity_policy()
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (FiniteCapacity(1.0), cold_train),
    )
    monkeypatch.setattr(sim, "_freeze_gate_enabled", lambda: False)

    def dispatch(self, _request):
        return IntentResult(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": {"Na": raw_rate}},
        )

    monkeypatch.setattr(BuiltinEvaporationFluxProvider, "dispatch", dispatch)
    captured: dict[str, float] = {}

    def solve(**kwargs):
        captured.update(kwargs["flux_kg_hr_at_partials"]({}))
        return CapacityShadowRefusal(
            reason="picard_non_convergence",
            iterations=1,
        )

    monkeypatch.setattr(
        "simulator.capacity_coupling.solve_capacity_shadow",
        solve,
    )
    before_mol = sim.atom_ledger.mol_by_account()
    before_transitions = len(sim.atom_ledger.transitions)
    result = sim._compute_capacity_coupling_shadow(
        SimpleNamespace(vapor_pressures_Pa={}, liquid_fraction=0.0),
    )

    assert isinstance(result, CapacityShadowRefusal)
    parent_draw_kg = float(captured["Na"]) * oxide_per_product
    assert parent_draw_kg > native_kg
    assert sim.atom_ledger.mol_by_account() == before_mol
    assert len(sim.atom_ledger.transitions) == before_transitions
