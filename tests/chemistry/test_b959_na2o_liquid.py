"""JANAF Na-013 regressions for b-959; no golden rebaselining."""

import pytest

from simulator.chemistry.ellingham_thermo import ellingham_fit_segments
from simulator.chemistry.melt_activity import (
    _na_activity_reference_standard_delta_g_kj_per_mol_o2,
)


@pytest.mark.parametrize(
    ("temperature_K", "expected_hex"),
    [
        (1200.0, "-0x1.e3d810624dd2fp+8"),
        (1400.0, "-0x1.8014fdf3b645ap+8"),
        (1405.2, "-0x1.7c2ee3fe41b9ep+8"),
        (3001.0, "0x1.5c8dd28d002e4p+8"),
    ],
)
def test_standard_state_owner_move_is_bit_identical(temperature_K, expected_hex):
    # Temporary numeric identity proof committed before the owner move.
    # The physics commit replaces these legacy pins with JANAF assertions.
    assert _na_activity_reference_standard_delta_g_kj_per_mol_o2(
        temperature_K
    ).hex() == expected_hex


@pytest.mark.parametrize(
    ("temperature_K", "expected_kj_per_mol_o2"),
    [
        # Chase 1998, JANAF Na-013, formation Gibbs column, kJ/mol Na2O:
        # 1400 K: -192.041; 1500 K: -167.596; 1600 K: -143.448.
        # Two Na2O form per O2. At 1423.15 K (1150 C), linear interpolation
        # of the printed 1400/1500 K rows, then doubling, gives -372.763965.
        (1423.15, -372.763965),
        (1500.0, -335.192),
        (1600.0, -286.896),
    ],
)
def test_liquid_standard_gibbs_matches_janaf_above_melting(
    temperature_K, expected_kj_per_mol_o2
):
    assert _na_activity_reference_standard_delta_g_kj_per_mol_o2(
        temperature_K
    ) == pytest.approx(expected_kj_per_mol_o2, abs=0.001)


def test_liquid_and_alpha_standard_gibbs_coexist_at_melting():
    # JANAF Na-013 and Na-014 both print ALPHA <--> LIQUID at 1405.200 K.
    # At a first-order transition the two chemical potentials coincide.
    alpha = next(
        segment
        for segment in ellingham_fit_segments("Na")
        if "Na2O(alpha)" in segment.phase_basis
    )
    assert _na_activity_reference_standard_delta_g_kj_per_mol_o2(
        1405.2
    ) == pytest.approx(alpha.delta_g_kJ_per_mol_O2(1405.2), abs=0.001)
