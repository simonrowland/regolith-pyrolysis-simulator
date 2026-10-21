"""Existing indoor evaporation output, captured before adding outdoor routing."""
from engines.builtin.evaporation_transition import BuiltinEvaporationTransitionProvider
from simulator.accounting.formulas import parse_formula
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import crown_carrier_allocation


def test_indoor_mg_evaporation_unchanged():
    mass = {s: parse_formula(s).molar_mass_kg_per_mol()
            for s in ("Mg", "MgO", "O2")}
    result = BuiltinEvaporationTransitionProvider().dispatch(IntentRequest(
        intent=ChemistryIntent.EVAPORATION_TRANSITION,
        account_view=ProviderAccountView(accounts={}, species_formula_registry={}),
        temperature_C=1726.85, pressure_bar=0,
        control_inputs={
            "species": "Mg", "rate_kg_hr": mass["Mg"],
            "remaining_kg_hr": .25 * mass["Mg"], "dt_hr": 1.,
            "available_kg": mass["MgO"],
            "stoich": {"parent_oxide": "MgO",
                       "oxide_per_product_kg": mass["MgO"] / mass["Mg"],
                       "O2_per_product_kg": .5 * mass["O2"] / mass["Mg"]},
        }))
    assert result.status == "ok"
    assert {a: {s: v.hex() for s, v in values.items()}
            for a, values in result.transition.debits.items()} == {
        "process.cleaned_melt": {"MgO": "0x1.0000000000000p+0"}}
    assert {a: {s: v.hex() for s, v in values.items()}
            for a, values in result.transition.credits.items()} == {
        "process.condensation_train": {"Mg": "0x1.8000000000001p-1"},
        "reservoir.fo2_buffer": {"O2": "0x1.0000000000000p-1"},
        "process.overhead_gas": {"Mg": "0x1.0000000000000p-2"},
    }


def test_station_allocation_before_sharing_routing_types():
    from dataclasses import asdict

    allocation = crown_carrier_allocation(
        1., .2, 0., 1., view_factor_from_melt=.5, return_view_factor=.5,
        sticking=1., return_accommodation=1.)
    assert {k: v.hex() for k, v in asdict(allocation).items()} == {
        "forward_mol_s": "0x1.0000000000000p+0",
        "gross_mol_s": "0x1.0000000000000p+0",
        "incidence_mol_s": "0x1.0000000000000p-1",
        "reemission_mol_s": "0x1.999999999999ap-3",
        "return_mol_s": "0x1.999999999999ap-4",
        "net_source_mol_s": "0x1.ccccccccccccdp-1",
        "dish_gain_mol_s": "0x1.3333333333333p-2",
        "sky_mol_s": "0x1.3333333333333p-1",
        "oxidized_mol_s": "0x0.0p+0",
        "inventory_mol": "0x1.3333333333333p-2",
    }
