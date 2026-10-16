"""Unit checks for the shared thermochemical pressure algebra."""

from __future__ import annotations

import pytest

from simulator.thermochemical_pressure import (
    log10_equilibrium_constant_from_delta_g_kj_mol,
    pressure_pa_from_log10_equilibrium_constant,
)


def test_zero_delta_g_uses_janaf_one_bar_standard_pressure() -> None:
    # For ΔvapG° = 0 at any T, Kp = 1 and JANAF P0 is 1 bar.
    log10_kp = log10_equilibrium_constant_from_delta_g_kj_mol(
        0.0,
        1000.0,
        8.31441,
    )
    assert log10_kp == pytest.approx(0.0)
    assert pressure_pa_from_log10_equilibrium_constant(
        log10_kp,
        100_000.0,
    ) == pytest.approx(100_000.0)
