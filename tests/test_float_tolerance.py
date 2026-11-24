from __future__ import annotations

from simulator.float_tolerance import sum_roundoff_slack


def test_sum_slack_covers_two_ulp_gap_but_not_real_overdraw() -> None:
    excess = 5.089568621263581
    buffer = 5.089568621263579
    slack = sum_roundoff_slack((excess, buffer), noop=1e-15)

    assert excess - buffer == 1.7763568394002505e-15
    assert excess <= buffer + slack
    assert buffer + 1e-9 > buffer + slack
