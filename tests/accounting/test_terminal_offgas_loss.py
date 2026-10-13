"""Escaped vapor remains visible as loss, never as recovered metal."""

from types import SimpleNamespace

from simulator.accounting.queries import AccountingQueries
from simulator.accounting.yield_disposition import _ACCOUNT_TO_BIN
from simulator.three_product_report import classify_products


def test_terminal_offgas_is_excluded_from_product_and_capture_totals():
    accounts = {
        "terminal.offgas": {"Fe": 20.0, "Na": 3.0, "H2O": 2.0},
        "process.condensation_train": {"Fe": 5.0, "Na": 1.0},
    }
    sim = SimpleNamespace(
        atom_ledger=SimpleNamespace(project_account_kg=lambda account: accounts.get(account, {})),
        _unspent_additive_reagents_kg=lambda: {},
        train=SimpleNamespace(stages=[]),
    )
    sim.product_ledger = lambda: AccountingQueries(sim).product_ledger()

    assert sim.product_ledger() == {"Fe": 5.0, "Na": 1.0, "H2O": 0.0}
    classified = classify_products(sim)
    assert classified["metals_plus_O2"]["metals_kg"] == {"Fe": 5.0, "Na": 1.0}
    assert classified["captured_volatiles"]["kg_by_species"] == {"Fe": 5.0, "Na": 1.0}
    # Existing loss vocabulary still owns the escaped account; no mass moves.
    assert _ACCOUNT_TO_BIN["terminal.offgas"] == "offgas_vented"
    assert sim.atom_ledger.project_account_kg("terminal.offgas") == accounts["terminal.offgas"]


def test_fully_escaped_product_has_explicit_zero_recovery():
    accounts = {"terminal.offgas": {"Na": 3.0}}
    sim = SimpleNamespace(
        atom_ledger=SimpleNamespace(project_account_kg=lambda account: accounts.get(account, {})),
        _unspent_additive_reagents_kg=lambda: {},
    )
    assert AccountingQueries(sim).product_ledger()["Na"] == 0.0
    assert accounts["terminal.offgas"] == {"Na": 3.0}


def test_later_available_capture_does_not_certify_earlier_unavailable_parcel():
    refusal = {"Na": {"status": "refused", "reason": "missing collector area"}}
    model = SimpleNamespace(
        operating_history=[{"condensation_refusals_by_species": refusal}],
        last_condensation_refusals_by_species={"Na": {"status": "pass_through"}},
    )
    sim = SimpleNamespace(
        atom_ledger=SimpleNamespace(project_account_kg=lambda account:
                                   {"Na": 3.0} if account == "terminal.offgas" else {}),
        _unspent_additive_reagents_kg=lambda: {},
        _condensation_model=model,
    )
    assert "Na" not in AccountingQueries(sim).product_ledger()
    assert model.operating_history[0]["condensation_refusals_by_species"]["Na"]["reason"]
