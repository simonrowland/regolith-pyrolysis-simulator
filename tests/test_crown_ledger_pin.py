"""Existing indoor evaporation output, captured before adding outdoor routing."""
from engines.builtin.evaporation_transition import BuiltinEvaporationTransitionProvider
from simulator.accounting.formulas import parse_formula
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView


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
