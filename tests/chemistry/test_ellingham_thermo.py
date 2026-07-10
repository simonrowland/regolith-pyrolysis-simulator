from __future__ import annotations

import pytest

from simulator.chemistry.ellingham_thermo import (
    ellingham_fit_segments,
    ellingham_segment_for_temperature,
)


def _delta_g_jump_at_breakpoint(species: str, temperature_K: float) -> float:
    before = next(
        segment
        for segment in ellingham_fit_segments(species)
        if segment.range_K[1] == pytest.approx(temperature_K)
    )
    after = next(
        segment
        for segment in ellingham_fit_segments(species)
        if segment.range_K[0] == pytest.approx(temperature_K)
    )
    return after.delta_g_kJ_per_mol_O2(
        temperature_K
    ) - before.delta_g_kJ_per_mol_O2(temperature_K)


@pytest.mark.parametrize(
    ("species", "boiling_point_K"),
    [
        ("Na", 1156.1),
        ("Mg", 1366.0),
        ("Ca", 1757.0),
    ],
)
def test_metal_boiling_breakpoints_are_delta_g_continuous(
    species: str,
    boiling_point_K: float,
) -> None:
    assert _delta_g_jump_at_breakpoint(species, boiling_point_K) == pytest.approx(
        0.0,
        abs=1e-6,
    )


def test_mn_primary_fit_is_split_at_solid_allotrope_breakpoints() -> None:
    mn_ranges = [segment.range_K for segment in ellingham_fit_segments("Mn")[:3]]
    assert mn_ranges == [
        (1100.0, 1360.0),
        (1360.0, 1410.0),
        (1410.0, 1519.0),
    ]

    assert "Mn(beta,s)" in ellingham_segment_for_temperature(
        "Mn",
        1359.0,
    ).phase_basis
    assert "Mn(gamma,s)" in ellingham_segment_for_temperature(
        "Mn",
        1361.0,
    ).phase_basis
    assert "Mn(delta,s)" in ellingham_segment_for_temperature(
        "Mn",
        1411.0,
    ).phase_basis

    assert _delta_g_jump_at_breakpoint("Mn", 1360.0) == pytest.approx(
        0.0,
        abs=1e-6,
    )
    assert _delta_g_jump_at_breakpoint("Mn", 1410.0) == pytest.approx(
        0.0,
        abs=1e-6,
    )
