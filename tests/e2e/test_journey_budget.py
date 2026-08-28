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
