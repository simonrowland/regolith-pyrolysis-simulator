from __future__ import annotations

import pytest

from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.accounting.ledger import AtomLedger, LedgerTransition
from simulator.accounting.lots import MaterialLot
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import MOLAR_MASS, CampaignPhase


def _lot(account: str, species: str, amount_mol: float) -> MaterialLot:
    return MaterialLot(
        account,
        {species: amount_mol * MOLAR_MASS[species] / 1000.0},
    )


@pytest.mark.parametrize(
    ("oxide", "element", "feedstock_mol", "reagent_mol", "reaction"),
    [
        ("K2O", "K", 1.0, 2.0, "c3_k_shuttle_fe_reduction"),
        ("MgO", "Mg", 3.0, 3.0, "c6_mg_thermite_primary"),
    ],
)
def test_melt_shuttle_credit_allows_partial_evaporation_with_pro_rata_origins(
    oxide, element, feedstock_mol, reagent_mol, reaction
):
    ledger = AtomLedger()
    ledger.load_external_mol(
        "process.cleaned_melt", {oxide: feedstock_mol}, material_origin="feedstock"
    )
    ledger.load_external_mol(
        "process.reagent_inventory", {element: reagent_mol}, material_origin="reagent"
    )

    if element == "K":
        ledger.load_external_mol(
            "process.cleaned_melt", {"FeO": 1.0}, material_origin="feedstock"
        )
        shuttle_transition = LedgerTransition(
            name=reaction,
            debits=(
                _lot("process.reagent_inventory", "K", 2.0),
                _lot("process.cleaned_melt", "FeO", 1.0),
            ),
            credits=(
                _lot("process.cleaned_melt", "K2O", 1.0),
                _lot("process.metal_phase", "Fe", 1.0),
            ),
        )
        partial_evaporation = LedgerTransition(
            name="evaporate_K",
            debits=(_lot("process.cleaned_melt", "K2O", 1.0),),
            credits=(
                _lot("process.overhead_gas", "K", 2.0),
                _lot("process.overhead_gas", "O2", 0.5),
            ),
        )
    else:
        ledger.load_external_mol(
            "process.cleaned_melt", {"Al2O3": 1.0}, material_origin="feedstock"
        )
        shuttle_transition = LedgerTransition(
            name=reaction,
            debits=(
                _lot("process.reagent_inventory", "Mg", 3.0),
                _lot("process.cleaned_melt", "Al2O3", 1.0),
            ),
            credits=(
                _lot("process.cleaned_melt", "MgO", 3.0),
                _lot("process.metal_phase", "Al", 2.0),
            ),
        )
        partial_evaporation = LedgerTransition(
            name="evaporate_Mg",
            debits=(_lot("process.cleaned_melt", "MgO", 3.0),),
            credits=(
                _lot("process.overhead_gas", "Mg", 3.0),
                _lot("process.overhead_gas", "O2", 1.5),
            ),
        )

    ledger.apply(shuttle_transition)
    ledger.mark_amalgamated_pool("process.cleaned_melt", (element,))
    ledger.apply(partial_evaporation)

    origin_debit = partial_evaporation.debits[0].origin_atom_moles
    if element == "K":
        assert origin_debit["feedstock"]["K"] == pytest.approx(1.0)
        assert origin_debit["reagent"]["K"] == pytest.approx(1.0)
    else:
        assert origin_debit["feedstock"]["Mg"] == pytest.approx(1.5)
        assert origin_debit["reagent"]["Mg"] == pytest.approx(1.5)


def test_evaporated_sodium_allows_partial_withdrawal_from_mixed_overhead_gas() -> None:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "sodium_oxide": {
                "label": "Sodium oxide",
                "composition_wt_pct": {"Na2O": 100.0},
            }
        },
        {"metals": {"Na": {"parent_oxide": "Na2O"}}, "oxide_vapors": {}},
    )
    sim.load_batch("sodium_oxide", mass_kg=1.0)
    ledger = sim.atom_ledger
    ledger.load_external_mol(
        "process.reagent_inventory", {"Na": 2.0}, material_origin="reagent"
    )
    ledger.load_external_mol(
        "process.cleaned_melt", {"FeO": 1.0}, material_origin="feedstock"
    )
    ledger.apply(
        LedgerTransition(
            name="c3_na_shuttle_fe_reduction",
            debits=(
                _lot("process.reagent_inventory", "Na", 2.0),
                _lot("process.cleaned_melt", "FeO", 1.0),
            ),
            credits=(
                _lot("process.cleaned_melt", "Na2O", 1.0),
                _lot("process.metal_phase", "Fe", 1.0),
            ),
        )
    )
    ledger.mark_amalgamated_pool("process.cleaned_melt", ("Na",))

    sodium_data = sim.vapor_pressures["metals"]["Na"]
    stoich = sim._evaporation_stoich("Na", sodium_data)
    available_oxide_kg = ledger.kg_by_account("process.cleaned_melt")["Na2O"]
    sodium_kg = 0.25 * available_oxide_kg / stoich["oxide_per_product_kg"]
    sim._credit_evaporation_transition(
        "Na",
        sodium_kg,
        sodium_kg,
        sodium_data,
        apply_evaporative_redox_source_terms=False,
    )

    overhead_origins = ledger.origin_atom_moles_by_account()["process.overhead_gas"]["Na"]
    assert overhead_origins["feedstock"] > 0.0
    assert overhead_origins["reagent"] > 0.0
    overhead_na_kg = ledger.kg_by_account("process.overhead_gas")["Na"]
    ledger.move(
        "partial_sodium_condensation",
        "process.overhead_gas",
        "process.condensation_train",
        {"Na": overhead_na_kg / 2.0},
    )

    # A half-pool withdrawal takes half of each known origin, preserving their
    # source ratio; the ledger's existing allocator supplies that split.
    origin_debit = ledger.transitions[-1].debits[0].origin_atom_moles["feedstock"]["Na"]
    assert origin_debit == pytest.approx(overhead_origins["feedstock"] / 2.0)
    assert ledger.transitions[-1].debits[0].origin_atom_moles["reagent"]["Na"] == pytest.approx(
        overhead_origins["reagent"] / 2.0
    )
    ledger.assert_balanced()


@pytest.mark.parametrize(
    ("element", "oxide", "reactant_oxide", "reagent_mol"),
    [("K", "K2O", "FeO", 2.0), ("Mg", "MgO", "Al2O3", 3.0)],
)
def test_evaporated_shuttle_cations_allow_partial_withdrawal_from_overhead_gas(
    element: str,
    oxide: str,
    reactant_oxide: str,
    reagent_mol: float,
) -> None:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    feedstock_id = f"{element.lower()}_oxide"
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            feedstock_id: {
                "label": f"{element} oxide",
                "composition_wt_pct": {oxide: 100.0},
            }
        },
        {"metals": {element: {"parent_oxide": oxide}}, "oxide_vapors": {}},
    )
    sim.load_batch(feedstock_id, mass_kg=1.0)
    ledger = sim.atom_ledger
    ledger.load_external_mol(
        "process.reagent_inventory", {element: reagent_mol}, material_origin="reagent"
    )
    ledger.load_external_mol(
        "process.cleaned_melt", {reactant_oxide: 1.0}, material_origin="feedstock"
    )

    if element == "K":
        shuttle_transition = LedgerTransition(
            name="c3_k_shuttle_fe_reduction",
            debits=(
                _lot("process.reagent_inventory", "K", 2.0),
                _lot("process.cleaned_melt", "FeO", 1.0),
            ),
            credits=(
                _lot("process.cleaned_melt", "K2O", 1.0),
                _lot("process.metal_phase", "Fe", 1.0),
            ),
        )
    else:
        shuttle_transition = LedgerTransition(
            name="c6_mg_thermite_primary",
            debits=(
                _lot("process.reagent_inventory", "Mg", 3.0),
                _lot("process.cleaned_melt", "Al2O3", 1.0),
            ),
            credits=(
                _lot("process.cleaned_melt", "MgO", 3.0),
                _lot("process.metal_phase", "Al", 2.0),
            ),
        )
    ledger.apply(shuttle_transition)
    ledger.mark_amalgamated_pool("process.cleaned_melt", (element,))

    vapor_data = sim.vapor_pressures["metals"][element]
    stoich = sim._evaporation_stoich(element, vapor_data)
    available_oxide_kg = ledger.kg_by_account("process.cleaned_melt")[oxide]
    evaporated_element_kg = (
        0.25 * available_oxide_kg / stoich["oxide_per_product_kg"]
    )
    sim._credit_evaporation_transition(
        element,
        evaporated_element_kg,
        evaporated_element_kg,
        vapor_data,
        apply_evaporative_redox_source_terms=False,
    )

    overhead_origins = ledger.origin_atom_moles_by_account()["process.overhead_gas"][
        element
    ]
    assert overhead_origins["feedstock"] > 0.0
    assert overhead_origins["reagent"] > 0.0
    overhead_element_kg = ledger.kg_by_account("process.overhead_gas")[element]
    ledger.move(
        f"partial_{element}_condensation",
        "process.overhead_gas",
        "process.condensation_train",
        {element: overhead_element_kg / 2.0},
    )

    origin_debit = ledger.transitions[-1].debits[0].origin_atom_moles
    assert origin_debit["feedstock"][element] == pytest.approx(
        overhead_origins["feedstock"] / 2.0
    )
    assert origin_debit["reagent"][element] == pytest.approx(
        overhead_origins["reagent"] / 2.0
    )
    ledger.assert_balanced()


def test_na_shuttle_cleaned_melt_partial_evaporation_keeps_origin_shares():
    """Mixed-origin dissolved Na evaporates pro rata once the shuttle marks it.

    Recovered feedstock Na and the reagent dose share one reagent pool.
    The Na shuttle credits both origins into process.cleaned_melt.
    A partial evaporation must withdraw each origin in that pool's ratio
    and carry both onto the overhead gas, then into the condensation train.
    """
    from tests.chemistry.conftest import _build_sim, _load_yaml

    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
        additives_kg={"Na": 30.0},
    )
    sim.melt.temperature_C = 1150.0
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    sim.atom_ledger.load_external(
        "process.reagent_inventory",
        {"Na": 5.0},
        source="recovered_condensate",
        material_origin="feedstock",
    )
    # The condensate transfer marks this pool; the dose draw does not.
    sim.atom_ledger.mark_amalgamated_pool("process.reagent_inventory", ("Na",))
    sim.shuttle_Na_inventory_kg = sim._sync_reagent_counter_from_ledger("Na")
    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)

    melt_origins = sim.atom_ledger.origin_atom_moles_by_account()[
        "process.cleaned_melt"
    ]["Na"]
    feedstock_before = float(melt_origins["feedstock"])
    reagent_before = float(melt_origins["reagent"])
    assert feedstock_before > 0.0
    assert reagent_before > 0.0
    melt_kg_before = sim.atom_ledger.kg_by_account(
        "process.cleaned_melt"
    )["Na2O"]
    assert melt_kg_before > 0.0
    assert sim.atom_ledger.kg_by_account(
        SPENT_REDUCTANT_RESIDUE_ACCOUNT
    ).get("Na2O", 0.0) == 0.0

    sodium_data = sim.vapor_pressures["metals"]["Na"]
    stoich = sim._evaporation_stoich("Na", sodium_data)
    sodium_kg = 0.25 * melt_kg_before / stoich["oxide_per_product_kg"]
    sim.melt.campaign = CampaignPhase.C4
    sim.melt.temperature_C = 1670.0
    sim.melt.p_total_mbar = 0.2
    transition = sim._credit_evaporation_transition(
        "Na",
        sodium_kg,
        sodium_kg,
        sodium_data,
        apply_evaporative_redox_source_terms=False,
        return_transition=True,
    )[1]
    assert transition is not None
    melt_lot = next(
        lot
        for lot in transition.debits
        if lot.account == "process.cleaned_melt" and "Na2O" in lot.species_kg
    )
    withdrawn = melt_lot.origin_atom_moles
    feedstock_withdrawn = float(withdrawn["feedstock"]["Na"])
    reagent_withdrawn = float(withdrawn["reagent"]["Na"])
    assert feedstock_withdrawn > 0.0
    assert reagent_withdrawn > 0.0
    assert feedstock_withdrawn < feedstock_before
    assert reagent_withdrawn < reagent_before
    assert feedstock_withdrawn / reagent_withdrawn == pytest.approx(
        feedstock_before / reagent_before
    )
    assert melt_lot.attribution_method_by_element["Na"] == "pool_ratio"
    assert (
        sim.atom_ledger.kg_by_account("process.cleaned_melt")["Na2O"]
        < melt_kg_before
    )

    overhead_origins = sim.atom_ledger.origin_atom_moles_by_account()[
        "process.overhead_gas"
    ]["Na"]
    assert overhead_origins["feedstock"] > 0.0
    assert overhead_origins["reagent"] > 0.0
    overhead_na_kg = sim.atom_ledger.kg_by_account("process.overhead_gas")["Na"]
    sim.atom_ledger.move(
        "partial_sodium_condensation",
        "process.overhead_gas",
        "process.condensation_train",
        {"Na": overhead_na_kg / 2.0},
    )
    condensed = sim.atom_ledger.origin_atom_moles_by_account()[
        "process.condensation_train"
    ]["Na"]
    assert condensed["feedstock"] == pytest.approx(
        overhead_origins["feedstock"] / 2.0
    )
    assert condensed["reagent"] == pytest.approx(overhead_origins["reagent"] / 2.0)
    sim.atom_ledger.assert_balanced()
