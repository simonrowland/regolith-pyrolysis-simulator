import importlib

import pytest


def _required_attr(module_name, attr_name):
    module = importlib.import_module(module_name)
    assert hasattr(module, attr_name), (
        f"{module_name}.{attr_name} is required by the reagent reservoir "
        "contract"
    )
    return getattr(module, attr_name)


def _ledger():
    AtomLedger = _required_attr("simulator.accounting", "AtomLedger")
    return AtomLedger()


def _material_lot():
    return _required_attr("simulator.accounting", "MaterialLot")


def test_unspent_additive_is_reservoir_balance_not_product():
    ledger = _ledger()

    ledger.load_external(
        "reservoir.reagent.K", {"K": 10.0}, source="operator inventory"
    )

    assert ledger.kg_by_account("reservoir.reagent.K")["K"] == pytest.approx(
        10.0
    )
    assert ledger.reservoir_balances()["reservoir.reagent.K"]["K"] == (
        pytest.approx(10.0)
    )
    assert ledger.kg_by_account("terminal.drain_tap_material").get(
        "K", 0.0
    ) == pytest.approx(0.0)


def test_recovered_reagent_transfer_is_zero_sum_debit_credit():
    ledger = _ledger()
    MaterialLot = _material_lot()
    ledger.load_external(
        "process.condensation_train", {"K": 2.0}, source="stage 3 recovery"
    )
    before_total_k = ledger.kg_by_species()["K"]

    ledger.transfer(
        "recover_k_to_reagent_inventory",
        debits=(
            MaterialLot(
                "process.condensation_train",
                {"K": 2.0},
                source="stage 3 recovery",
            ),
        ),
        credits=(
            MaterialLot(
                "process.reagent_inventory",
                {"K": 2.0},
                source="C3 recovered K",
            ),
        ),
        reason="recovered K is moved, not duplicated",
    )

    ledger.assert_balanced()
    assert ledger.kg_by_account("process.condensation_train").get(
        "K", 0.0
    ) == pytest.approx(0.0)
    assert ledger.kg_by_account("process.reagent_inventory")[
        "K"
    ] == pytest.approx(2.0)
    assert ledger.kg_by_species()["K"] == pytest.approx(before_total_k)


def test_recovered_credit_cannot_be_spent_twice():
    ledger = _ledger()
    MaterialLot = _material_lot()
    ledger.load_external(
        "process.condensation_train", {"Na": 4.0}, source="stage 3 recovery"
    )

    ledger.transfer(
        "recover_na_to_reagent_inventory",
        debits=(
            MaterialLot(
                "process.condensation_train",
                {"Na": 4.0},
                source="stage 3 recovery",
            ),
        ),
        credits=(
            MaterialLot(
                "process.reagent_inventory",
                {"Na": 4.0},
                source="C3 recovered Na",
            ),
        ),
        reason="first recovered Na spend",
    )

    with pytest.raises(Exception, match="insufficient|spent|available"):
        ledger.transfer(
            "recover_na_to_reagent_inventory_again",
            debits=(
                MaterialLot(
                    "process.condensation_train",
                    {"Na": 0.001},
                    source="stage 3 recovery",
                ),
            ),
            credits=(
                MaterialLot(
                    "process.reagent_inventory",
                    {"Na": 0.001},
                    source="duplicate recovered Na spend",
                ),
            ),
            reason="duplicate spend must fail",
        )


def test_terminal_accounts_cannot_flow_back_to_process():
    ledger = _ledger()

    ledger.load_external(
        "terminal.offgas", {"H2O": 1.0}, source="Stage 0 offgas"
    )

    with pytest.raises(Exception, match="terminal account|cannot be debited"):
        ledger.move(
            "bad_terminal_reversal",
            "terminal.offgas",
            "process.cleaned_melt",
            {"H2O": 1.0},
            reason="terminal material cannot re-enter process",
        )


def test_stored_oxygen_can_move_to_vented_terminal_account():
    ledger = _ledger()

    ledger.load_external(
        "terminal.oxygen_melt_offgas_stored", {"O2": 2.0}, source="oxygen storage"
    )

    ledger.move(
        "vent_stored_oxygen",
        "terminal.oxygen_melt_offgas_stored",
        "terminal.oxygen_melt_offgas_vented_to_vacuum",
        {"O2": 1.0},
        reason="controlled vent",
    )

    assert ledger.kg_by_account("terminal.oxygen_melt_offgas_stored")[
        "O2"
    ] == pytest.approx(1.0)
    assert ledger.kg_by_account("terminal.oxygen_melt_offgas_vented_to_vacuum")[
        "O2"
    ] == pytest.approx(1.0)


def test_oxygen_terminal_accounts_reject_non_o2_species():
    ledger = _ledger()

    with pytest.raises(Exception, match="only accepts species|got 'N2'"):
        ledger.load_external(
            "terminal.oxygen_melt_offgas_stored", {"N2": 1.0}, source="bad oxygen storage"
        )
