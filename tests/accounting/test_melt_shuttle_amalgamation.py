from __future__ import annotations

import pytest

from simulator.accounting.ledger import AtomLedger, LedgerTransition
from simulator.accounting.lots import MaterialLot
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import MOLAR_MASS


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
