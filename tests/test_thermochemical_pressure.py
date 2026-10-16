"""Unit checks for the shared thermochemical pressure algebra."""

from __future__ import annotations

import pytest

from simulator.thermochemical_pressure import (
    log10_equilibrium_constant_from_delta_g_kj_mol,
    pressure_pa_from_log10_equilibrium_constant,
)


def test_normal_boiling_point_gives_one_bar() -> None:
    # At the normal boiling point ΔvapG° = 0; JANAF P0 is 1 bar.
    log10_kp = log10_equilibrium_constant_from_delta_g_kj_mol(
        0.0,
        1156.0,
        8.31441,
    )
    assert log10_kp == pytest.approx(0.0)
    assert pressure_pa_from_log10_equilibrium_constant(
        log10_kp,
        100_000.0,
    ) == pytest.approx(100_000.0)
