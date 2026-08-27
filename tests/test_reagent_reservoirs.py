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
        "reservoir.reagent.K", {"K": 10.0}, source="operator inventory",
        material_origin="reagent",
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


def test_na_k_reagent_reservoir_credit_requires_policy():
    ledger = _ledger()
    AccountPolicy = _required_attr("simulator.accounting", "AccountPolicy")
    OverdraftError = _required_attr("simulator.accounting", "OverdraftError")

    ledger.set_account_policy(
        "reservoir.reagent.Na",
        AccountPolicy.reservoir(
            "reservoir.reagent.Na",
            credit_limit_kg_by_species={"Na": 3.0},
        ),
    )
    ledger.move(
        "draw_na_credit",
        "reservoir.reagent.Na",
        "process.reagent_inventory",
        {"Na": 2.0},
        reason="C3 Na credit line draw",
    )

    ledger.assert_balanced()
    assert ledger.kg_by_account("reservoir.reagent.Na")["Na"] == pytest.approx(
        -2.0
    )
    assert ledger.kg_by_account("process.reagent_inventory")["Na"] == (
        pytest.approx(2.0)
    )
    ledger.set_account_policy(
        "reservoir.reagent.K",
        AccountPolicy.reservoir(
            "reservoir.reagent.K",
            credit_limit_kg_by_species={"K": 2.0},
        ),
    )
    ledger.move(
        "draw_k_credit",
        "reservoir.reagent.K",
        "process.reagent_inventory",
        {"K": 1.0},
        reason="C3 K credit line draw",
    )
    assert ledger.kg_by_account("reservoir.reagent.K")["K"] == pytest.approx(
        -1.0
    )

    no_policy_ledger = _ledger()
    with pytest.raises(OverdraftError, match="insufficient available 'K'"):
        no_policy_ledger.move(
            "draw_k_without_policy",
            "reservoir.reagent.K",
            "process.reagent_inventory",
            {"K": 1.0},
            reason="K credit without policy must fail",
        )


def test_recovered_reagent_transfer_is_zero_sum_debit_credit():
    ledger = _ledger()
    MaterialLot = _material_lot()
    ledger.load_external(
        "process.condensation_train", {"K": 2.0}, source="stage 3 recovery",
        material_origin="feedstock",
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
        "process.condensation_train", {"Na": 4.0}, source="stage 3 recovery",
        material_origin="feedstock",
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
        "terminal.offgas", {"H2O": 1.0}, source="Stage 0 offgas",
        material_origin="feedstock",
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
        "terminal.oxygen_melt_offgas_stored", {"O2": 2.0}, source="oxygen storage",
        material_origin="feedstock",
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
            "terminal.oxygen_melt_offgas_stored", {"N2": 1.0}, source="bad oxygen storage",
            material_origin="feedstock",
        )


def test_missing_credit_limit_is_a_config_error_not_an_inventory_overdraw():
    """b-284 / SC-146: no limit configured is not the same as drew past a limit.

    The two OverdraftErrors either side of this branch describe a draw that
    exceeded a KNOWN bound. This branch says no bound was ever configured, so
    there is nothing to exceed -- a configuration gap, which under the
    three-category rule is missing input, not a physical property of the recipe.

    The distinction is load-bearing: the optimizer catches OverdraftError and
    classifies it as ``inventory_overdraw``, then prunes the candidate as
    infeasible. Raising a config gap in the overdraw family therefore scored a
    perfectly good recipe as impossible AND hid the gap, because the operator
    only ever saw an infeasible candidate.

    Note how little setup this needs. That is the finding: AccountPolicy
    .reservoir() defaults credit_limit_kg_by_species to {}, so this is the
    DEFAULT path for any reservoir built without explicit per-species limits,
    not a corner case someone has to construct.
    """

    AccountPolicy = _required_attr("simulator.accounting", "AccountPolicy")
    OverdraftError = _required_attr("simulator.accounting", "OverdraftError")
    AccountCreditPolicyError = _required_attr(
        "simulator.accounting", "AccountCreditPolicyError"
    )

    ledger = _ledger()
    ledger.set_account_policy(
        "reservoir.reagent.Na",
        AccountPolicy.reservoir("reservoir.reagent.Na"),  # no limits -- the default
    )

    with pytest.raises(AccountCreditPolicyError, match="has no credit limit") as excinfo:
        ledger.move(
            "draw_na_without_configured_limit",
            "reservoir.reagent.Na",
            "process.reagent_inventory",
            {"Na": 2.0},
            reason="b-284 config-gap probe",
        )

    # NOT in the overdraw family, so the optimizer's
    # `except (ProposalRejected, OverdraftError)` cannot launder it into
    # candidate-infeasibility. This is the whole point of the type change.
    assert not isinstance(excinfo.value, OverdraftError)

    # And it must not classify as an overdraw under EITHER string spelling --
    # str(exc) or the run-executor's "ClassName: message" form.
    from simulator.optimize.evaluate import _is_inventory_overdraw_message
    from simulator.run_executor import _safe_exception_text

    assert _is_inventory_overdraw_message(str(excinfo.value)) is False
    assert _is_inventory_overdraw_message(_safe_exception_text(excinfo.value)) is False

    # Contrast: a genuine overdraw is still an OverdraftError and still
    # classifies as one. Narrowing this branch must not cost the real case.
    plain = _ledger()
    with pytest.raises(OverdraftError) as real:
        plain.move(
            "draw_more_than_exists",
            "process.cleaned_melt",
            "process.reagent_inventory",
            {"Na": 5.0},
            reason="b-284 genuine-overdraw contrast",
        )
    assert _is_inventory_overdraw_message(str(real.value)) is True
