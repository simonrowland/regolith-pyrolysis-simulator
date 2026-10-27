from copy import deepcopy
from types import SimpleNamespace

import pytest

from simulator.accounting import AtomLedger
from simulator.accounting.campaign_yield import CampaignYieldProjection
from simulator.accounting.formulas import resolve_species_formula


ACCOUNT = "process.condensation_train"


def _kg(species, mol):
    return mol * resolve_species_formula(species, {}).molar_mass_kg_per_mol()


def _projection(ledger, stages):
    return CampaignYieldProjection(SimpleNamespace(
        atom_ledger=ledger, _stage_collection_kg_by_source=stages,
    ))


def test_single_feed_origin_exact_capture_wrong_stage_and_no_mutation():
    ledger = AtomLedger()
    ledger.load_external_mol(ACCOUNT, {"Na": 3}, material_origin="feedstock")
    stages = {(ACCOUNT, 4, "Na"): _kg("Na", 2), (ACCOUNT, 3, "Na"): _kg("Na", 1)}
    before = (ledger.mol_by_account(), ledger.origin_atom_moles_by_account(), deepcopy(stages))
    result = _projection(ledger, stages).captured_feed_atoms("Na", 4)
    assert result.status == "available"
    assert result.captured_mol == pytest.approx(2)
    assert result.wrong_stage_mol == pytest.approx(1)
    assert before == (ledger.mol_by_account(), ledger.origin_atom_moles_by_account(), stages)


@pytest.mark.parametrize("pooled", [False, True])
def test_feed_plus_reagent_is_unavailable_even_with_pool_ratio(pooled):
    ledger = AtomLedger()
    ledger.load_external_mol(ACCOUNT, {"Na": 1}, material_origin="feedstock")
    ledger.load_external_mol(ACCOUNT, {"Na": 1}, material_origin="reagent")
    if pooled:
        ledger.mark_amalgamated_pool(ACCOUNT, ("Na",))
    stages = {(ACCOUNT, 4, "Na"): _kg("Na", 2)}
    result = _projection(ledger, stages).captured_feed_atoms("Na", 4)
    assert result.status == "unavailable"
    assert result.reason == "origin_unresolved_for_stage_split"
    assert result.captured_mol is None
    assert result.diagnostic["origins_mol"] == {"feedstock": pytest.approx(1), "reagent": pytest.approx(1)}


def test_unresolved_origin_is_unavailable():
    ledger = AtomLedger(initial_balances={ACCOUNT: {"Fe": _kg("Fe", 1)}})
    result = _projection(ledger, {(ACCOUNT, 1, "Fe"): _kg("Fe", 1)}).captured_feed_atoms("Fe", 1)
    assert result.reason == "origin_unresolved_for_stage_split"
    assert result.diagnostic["unresolved_mol"] == pytest.approx(1)


def test_reconcile_each_species_not_just_element_total():
    ledger = AtomLedger()
    ledger.load_external_mol(ACCOUNT, {"Na": 1, "Na2O": 1}, material_origin="feedstock")
    # Three Na atoms in both maps, but the chemical carriers do not reconcile.
    result = _projection(ledger, {(ACCOUNT, 4, "Na"): _kg("Na", 3)}).captured_feed_atoms("Na", 4)
    assert result.reason == "stage_projection_unreconciled"
    assert result.diagnostic["stage_projection_kg"] != result.diagnostic["ledger_kg"]


def test_net_recycling_and_separate_feed_losses():
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"Fe": 5}, material_origin="feedstock")
    ledger.move("capture", "process.cleaned_melt", ACCOUNT, {"Fe": _kg("Fe", 3)})
    ledger.move("wall", "process.cleaned_melt", "process.wall_deposit", {"Fe": _kg("Fe", 1)})
    ledger.move("recycle", ACCOUNT, "process.reagent_inventory", {"Fe": _kg("Fe", 1)})
    stages = {(ACCOUNT, 1, "Fe"): _kg("Fe", 2)}
    result = _projection(ledger, stages).inventory(["Fe"], 1)
    assert result["captured"]["Fe"] == pytest.approx(2)
    assert result["extracted"]["Fe"] == pytest.approx(4)
    assert result["losses"]["process.wall_deposit"]["Fe"] == pytest.approx(1)
    assert result["initial_feed_element_inventory"]["Fe"] == pytest.approx(5)


def test_roundoff_allowed_but_real_missing_stage_mass_is_unavailable():
    ledger = AtomLedger()
    ledger.load_external_mol(ACCOUNT, {"Fe": 1}, material_origin="feedstock")
    mass = _kg("Fe", 1)
    good = _projection(ledger, {(ACCOUNT, 1, "Fe"): mass * (1 + 2e-16)})
    assert good.captured_feed_atoms("Fe", 1).status == "available"
    bad = _projection(ledger, {(ACCOUNT, 1, "Fe"): mass * 0.99})
    assert bad.captured_feed_atoms("Fe", 1).reason == "stage_projection_unreconciled"


def test_melt_resident_spent_residue_is_entry_inventory_not_extracted():
    ledger = AtomLedger()
    ledger.load_external_mol('process.cleaned_melt', {'Na': 3}, material_origin='feedstock')
    ledger.move('spent', 'process.cleaned_melt', 'process.spent_reductant_residue', {'Na': _kg('Na', 1)})
    ledger.load_external_mol('process.spent_reductant_residue', {'Na': 5}, material_origin='reagent')
    inventory = _projection(ledger, {}).inventory(['Na'], 4)
    assert inventory['melt']['Na'] == pytest.approx(3)
    assert inventory['extracted']['Na'] == 0
    assert inventory['initial_feed_element_inventory']['Na'] == pytest.approx(3)
