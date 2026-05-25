"""Engine-independent tests for the liquidus/solidus bisection helper."""

from __future__ import annotations

import pytest

from simulator.melt_backend.liquidus import find_liquidus_solidus_by_fraction


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


def test_liquidus_finder_guards_non_monotone_fraction_curve():
    values = {
        800.0: 0.0,
        900.0: 0.4,
        1000.0: 0.2,
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
