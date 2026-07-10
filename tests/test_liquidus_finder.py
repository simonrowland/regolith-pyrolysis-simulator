"""Engine-independent tests for the liquidus/solidus bisection helper."""

from __future__ import annotations

import pytest

import simulator.melt_backend.liquidus as liquidus_module
from simulator.melt_backend.liquidus import find_liquidus_solidus_by_fraction


def _piecewise_fraction(anchors: dict[float, float]):
    ordered = sorted((float(T), float(frac)) for T, frac in anchors.items())

    def sample(temperature_C: float) -> float:
        T = float(temperature_C)
        if T <= ordered[0][0]:
            return ordered[0][1]
        for (left_T, left_frac), (right_T, right_frac) in zip(
            ordered,
            ordered[1:],
        ):
            if T <= right_T:
                span = right_T - left_T
                weight = (T - left_T) / span
                return left_frac + (right_frac - left_frac) * weight
        return ordered[-1][1]

    return sample


def test_liquidus_finder_bisects_monotone_fraction_curve():
    def frac_M(temperature_C: float) -> float:
        return max(0.0, min(1.0, (temperature_C - 1000.0) / 300.0))

    result = find_liquidus_solidus_by_fraction(
        frac_M,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == 'ok'
    assert result.solidus_T_C == pytest.approx(1000.0, abs=1.0)
    assert result.liquidus_T_C == pytest.approx(1300.0, abs=1.0)
    assert result.liquidus_T_K == pytest.approx(result.liquidus_T_C + 273.15)
    assert result.liquidus_T_C >= result.solidus_T_C
    assert frac_M(result.solidus_T_C) <= 1.0e-3
    assert frac_M(result.liquidus_T_C) >= 1.0 - 1.0e-3
    assert result.iterations <= 64


def test_liquidus_finder_is_deterministic():
    def frac_M(temperature_C: float) -> float:
        return max(0.0, min(1.0, (temperature_C - 925.0) / 250.0))

    first = find_liquidus_solidus_by_fraction(
        frac_M,
        min_T_C=700.0,
        max_T_C=1300.0,
        scan_step_C=75.0,
        tolerance_C=2.0,
    )
    second = find_liquidus_solidus_by_fraction(
        frac_M,
        min_T_C=700.0,
        max_T_C=1300.0,
        scan_step_C=75.0,
        tolerance_C=2.0,
    )

    assert first == second


def test_liquidus_finder_refuses_magemin_scale_nonmonotone_dip():
    # 0.09 / 0.33 / 0.05 MAGEMin frac_M dips were observed in the
    # 2026-05-26 freeze-gate flip blast-radius on lunar/mars C2A cases.
    result = find_liquidus_solidus_by_fraction(
        _piecewise_fraction({
            1000.0: 0.0,
            1100.0: 0.5,
            1200.0: 0.98,
            1250.0: 0.98075,
            1300.0: 0.890898,
            1350.0: 0.99,
            1400.0: 1.0,
            1450.0: 1.0,
            1500.0: 0.670427,
            1550.0: 0.945697,
            1600.0: 1.0,
        }),
        min_T_C=1000.0,
        max_T_C=1600.0,
        scan_step_C=50.0,
        tolerance_C=1.0,
    )

    assert result.status == 'not_converged'
    assert result.solidus_T_C is None
    assert result.liquidus_T_C is None
    assert any('non-monotone frac_M' in w for w in result.warnings)
    assert any('would require smoothing' in w for w in result.warnings)


def test_liquidus_finder_guards_non_monotone_fraction_curve():
    values = {
        800.0: 0.0,
        900.0: 1.0,
        1000.0: 0.0,
        1100.0: 1.0,
    }

    result = find_liquidus_solidus_by_fraction(
        lambda temperature_C: values[float(temperature_C)],
        min_T_C=800.0,
        max_T_C=1100.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == 'not_converged'
    assert any('non-monotone frac_M' in warning for warning in result.warnings)


def test_liquidus_finder_reports_missing_bracket_without_crashing():
    result = find_liquidus_solidus_by_fraction(
        lambda temperature_C: 0.2,
        min_T_C=800.0,
        max_T_C=1200.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == 'not_converged'
    assert any('solidus bracket absent' in warning for warning in result.warnings)
    assert any('liquidus bracket absent' in warning for warning in result.warnings)


def test_liquidus_finder_default_budget_is_unbounded():
    """Generic default must not silently bind AlphaMELTS (or unit callables)."""
    calls = {'n': 0}

    def sample(temperature_C: float) -> float:
        calls['n'] += 1
        # Fully solid everywhere -> missing brackets after full grid, not budget.
        return 0.0

    result = find_liquidus_solidus_by_fraction(
        sample,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
    )
    assert result.status == 'not_converged'
    assert calls['n'] == 3  # 800, 900, 1000
    assert result.diagnostics.get('reason') != 'aggregate_budget_exceeded'


def test_liquidus_finder_stops_on_aggregate_budget(monkeypatch):
    clock = {'t': 0.0}
    remaining_seen: list[float] = []

    def fake_monotonic():
        return clock['t']

    def sample(temperature_C: float, remaining_budget_s=None) -> float:
        remaining_seen.append(remaining_budget_s)
        # Each engine call burns wall time after the residual is observed.
        clock['t'] += 0.1
        return 0.0

    monkeypatch.setattr(liquidus_module.time, 'monotonic', fake_monotonic)

    result = find_liquidus_solidus_by_fraction(
        sample,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
        budget_s=0.05,
    )

    assert result.status == 'not_converged'
    assert len(result.samples) == 1
    assert any(
        'liquidus finder exceeded aggregate budget 0.05s after 1 calls'
        in warning
        for warning in result.warnings
    )
    diagnostics = dict(result.diagnostics)
    assert diagnostics['reason'] == 'aggregate_budget_exceeded'
    assert diagnostics['call_count'] == 1
    assert diagnostics['last_T_C'] == pytest.approx(800.0)
    assert diagnostics['budget_s'] == pytest.approx(0.05)
    assert diagnostics['elapsed_s'] == pytest.approx(0.1)
    # Residual budget threaded into the call that then overran.
    assert remaining_seen == [pytest.approx(0.05)]


def test_liquidus_finder_threads_remaining_budget_into_each_call(monkeypatch):
    clock = {'t': 0.0}
    remaining_seen: list[float] = []

    def fake_monotonic():
        return clock['t']

    def sample(temperature_C: float, remaining_budget_s=None) -> float:
        remaining_seen.append(float(remaining_budget_s))
        clock['t'] += 0.2
        # Monotone liquidus-ish curve so the finder keeps sampling.
        return max(0.0, min(1.0, (temperature_C - 850.0) / 100.0))

    monkeypatch.setattr(liquidus_module.time, 'monotonic', fake_monotonic)

    result = find_liquidus_solidus_by_fraction(
        sample,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
        budget_s=0.55,
        tolerance_C=50.0,  # avoid deep bisection; grid alone may finish
        max_bisection_iterations=0,
    )
    # At least two residual observations, strictly decreasing by call cost.
    assert len(remaining_seen) >= 2
    assert remaining_seen[0] == pytest.approx(0.55)
    assert remaining_seen[1] == pytest.approx(0.35)
    assert all(
        earlier > later
        for earlier, later in zip(remaining_seen, remaining_seen[1:])
    )
    # Either ok (with max_bisection 0 path) or not_converged; budget must not
    # have been the failure mode for the first two cheap calls.
    if result.status == 'not_converged':
        assert result.diagnostics.get('reason') != 'aggregate_budget_exceeded' or len(
            result.samples
        ) >= 2


def test_liquidus_finder_engine_residual_timeout_emits_budget_diagnostics(
    monkeypatch,
):
    """Engine path that burns the residual must not fall through as generic fail.

    Simulates the subprocess residual-timeout shape: the sample callable
    observes the residual, burns wall equal to it, then raises a timeout-
    shaped error (never returns a fraction). Structured
    ``aggregate_budget_exceeded`` diagnostics must still be attached.
    """
    clock = {'t': 0.0}
    remaining_seen: list[float] = []

    def fake_monotonic():
        return clock['t']

    def sample(temperature_C: float, remaining_budget_s=None) -> float:
        remaining = float(remaining_budget_s)
        remaining_seen.append(remaining)
        # Burn residual wall as a real residual-clamped subprocess would.
        clock['t'] += remaining
        raise RuntimeError(f'MAGEMin binary timed out after {remaining:g}s')

    monkeypatch.setattr(liquidus_module.time, 'monotonic', fake_monotonic)

    result = find_liquidus_solidus_by_fraction(
        sample,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
        budget_s=0.05,
    )

    assert result.status == 'not_converged'
    assert result.samples == ()  # timed out before a sample was recorded
    assert remaining_seen == [pytest.approx(0.05)]
    assert any(
        'liquidus finder exceeded aggregate budget 0.05s after 0 calls'
        in warning
        for warning in result.warnings
    )
    diagnostics = dict(result.diagnostics)
    assert diagnostics['reason'] == 'aggregate_budget_exceeded'
    assert diagnostics['call_count'] == 0
    assert diagnostics['last_T_C'] == pytest.approx(800.0)
    assert diagnostics['budget_s'] == pytest.approx(0.05)
    assert diagnostics['elapsed_s'] == pytest.approx(0.05)
    # Must not be the generic Exception envelope.
    assert not any(
        warning.startswith('liquidus finder failed:')
        for warning in result.warnings
    )
