"""The journey's per-test cap must outlive its own declared step budgets.

Needs no browser: journey_budget is deliberately playwright-free, so this guard
runs everywhere the suite runs, including hosts where the e2e tests all skip.

Regression: the cap was a bare 300 s while the declared budgets summed to 995 s
before the advance loop even iterated. The journey could therefore never report
on steps 5-7 -- the outer signal always killed it first, the evidence harvest
never ran (empty artifact directory), and the strict xfail then absorbed a
timeout whose attributed cause had never been observed.
"""

from __future__ import annotations

from tests.e2e import journey_budget as budget


def test_per_test_cap_exceeds_every_declared_step_budget() -> None:
    """The cap must exceed the worst-case sum, or the journey cannot report."""
    assert budget.JOURNEY_TIMEOUT_S * 1000 > budget.JOURNEY_BUDGET_MS, (
        f"journey cap {budget.JOURNEY_TIMEOUT_S}s does not exceed the declared "
        f"step budget {budget.JOURNEY_BUDGET_MS / 1000}s; steps at the end of "
        "the journey cannot render a verdict"
    )


def test_declared_budget_is_the_sum_of_its_parts() -> None:
    """Anti-vacuity: a budget that silently dropped a term would pass above."""
    expected = (
        budget.PAGE_LOAD_MS
        + budget.FEEDSTOCK_CARD_MS
        + budget.SOCKET_CONNECT_MS
        + budget.STATUS_CHANGE_MS
        + budget.START_ACK_MS
        + budget.TICK_ADVANCE_MS
        + budget.RUN_COMPLETE_TOTAL_MS
        + budget.OPTIMIZER_BOUND_MS
        + budget.THERMAL_TRAIN_MS
    )
    assert budget.JOURNEY_BUDGET_MS == expected


def test_advance_loop_total_bounds_a_single_stall_window() -> None:
    """The loop's total bound must exceed one stall window.

    Otherwise the total would fire first and every stall would be misreported as
    "kept advancing but never completed" -- the two failure modes swap names.
    """
    assert budget.RUN_COMPLETE_TOTAL_MS > budget.RUN_COMPLETE_MS


REFUSAL = (
    "run terminated as 'REFUSED' instead of completing: refused — Overhead "
    "pressure fell below the viscous-flow domain (Knudsen > 0.01, about 0.26 "
    "mbar for this duct), where the continuum transport model has no valid "
    "solution."
)


def test_all_steps_ok_is_a_pass() -> None:
    ledger = [("1-land", True, "ok"), ("6-optimizer", True, "11 rows")]
    assert budget.journey_verdict(ledger) == ("pass", "")


def test_the_observed_gap_alone_is_excused() -> None:
    ledger = [("4-advances", True, "ok"), ("5-results", False, REFUSAL)]
    outcome, detail = budget.journey_verdict(ledger)
    assert outcome == "xfail"
    assert "viscous-flow domain" in detail


def test_a_regression_elsewhere_is_not_absorbed() -> None:
    """The point of the rule: a step that passes today must not go quiet.

    A blanket xfail reported this case as the known gap. The optimizer hanging
    was the owner's original complaint, so losing it again silently is the
    specific outcome this guards.
    """
    ledger = [("5-results", True, "ok"), ("6-optimizer", False, "leaderboard never rendered")]
    assert budget.journey_verdict(ledger) == ("fail", "6-optimizer")


def test_the_gap_plus_a_regression_is_not_excused() -> None:
    ledger = [
        ("5-results", False, REFUSAL),
        ("7-thermal-train", False, "no_data shell"),
    ]
    outcome, _ = budget.journey_verdict(ledger)
    assert outcome == "fail"


def test_step_5_failing_for_another_reason_is_not_excused() -> None:
    """The excuse is for one named cause, not for one step number."""
    ledger = [("5-results", False, "run stalled at Hour: 7 — no further hour within 180s")]
    outcome, _ = budget.journey_verdict(ledger)
    assert outcome == "fail"


def test_a_partial_mark_match_is_not_excused() -> None:
    """Both marks required: 'Knudsen' alone appears in unrelated transport prose."""
    ledger = [("5-results", False, "some other failure mentioning Knudsen in passing")]
    outcome, _ = budget.journey_verdict(ledger)
    assert outcome == "fail"


def test_control_journey_cap_exceeds_its_declared_step_budget() -> None:
    assert budget.CONTROL_JOURNEY_TIMEOUT_S * 1000 > budget.CONTROL_JOURNEY_BUDGET_MS, (
        f"control-journey cap {budget.CONTROL_JOURNEY_TIMEOUT_S}s does not exceed "
        f"the declared step budget {budget.CONTROL_JOURNEY_BUDGET_MS / 1000}s"
    )


def test_control_journey_budget_is_the_sum_of_its_parts() -> None:
    expected = (
        budget.PAGE_LOAD_MS
        + budget.FEEDSTOCK_CARD_MS
        + budget.SOCKET_CONNECT_MS
        + budget.STATUS_CHANGE_MS
        + budget.START_ACK_MS
        + budget.TICK_ADVANCE_MS
        + budget.STATUS_CHANGE_MS
        + budget.PAUSE_HOLD_MS
        + budget.STATUS_CHANGE_MS
        + budget.TICK_ADVANCE_MS
        + budget.STATUS_CHANGE_MS
        + budget.START_ACK_MS
        + budget.TICK_ADVANCE_MS
    )
    assert budget.CONTROL_JOURNEY_BUDGET_MS == expected


def test_pause_hold_is_not_shorter_than_one_advance_window() -> None:
    """A shorter hold would pass if the next hour is merely slow."""
    assert budget.PAUSE_HOLD_MS >= budget.TICK_ADVANCE_MS
