from __future__ import annotations

import pytest

from simulator.accounting.ledger import AtomLedger, LedgerTransition
from simulator.accounting.lots import MaterialLot
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
