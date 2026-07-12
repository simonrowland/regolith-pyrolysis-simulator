from __future__ import annotations

import pytest

from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_AUTHORITY_LIMIT_FLAG,
    ELLINGHAM_METAL_PHASE_CONDENSED,
    ELLINGHAM_METAL_PHASE_GAS,
    ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG,
    ELLINGHAM_RECONSTRUCTED_AUTHORITY_STATUS,
    MG_NORMAL_BOILING_POINT_K,
    ellingham_authority_limit,
    ellingham_authority_diagnostic,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_extrapolation,
    ellingham_fit_segments,
    ellingham_metal_phase_kind,
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
    ("species", "fit_breakpoint_K"),
    [
        ("Na", 1156.1),
        ("Mg", 1366.0),
        ("Ca", 1757.0),
    ],
)
def test_metal_phase_fit_breakpoints_are_delta_g_continuous(
    species: str,
    fit_breakpoint_K: float,
) -> None:
    assert _delta_g_jump_at_breakpoint(species, fit_breakpoint_K) == pytest.approx(
        0.0,
        abs=1e-6,
    )


def test_mg_runtime_rail_switches_at_physical_boiling_boundary() -> None:
    assert ellingham_metal_phase_kind(
        "Mg",
        MG_NORMAL_BOILING_POINT_K - 1e-6,
    ) == ELLINGHAM_METAL_PHASE_CONDENSED
    assert ellingham_metal_phase_kind(
        "Mg",
        MG_NORMAL_BOILING_POINT_K,
    ) == ELLINGHAM_METAL_PHASE_GAS


@pytest.mark.parametrize("temperature_K", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_temperature_is_refused(temperature_K: float) -> None:
    with pytest.raises(ValueError, match="temperature_K must be finite"):
        ellingham_segment_for_temperature("Na", temperature_K)
    with pytest.raises(ValueError, match="temperature_K must be finite"):
        ellingham_delta_g_kj_per_mol_o2("Na", temperature_K)
    with pytest.raises(ValueError, match="temperature_K must be finite"):
        ellingham_fit_extrapolation(
            temperature_K,
            species="Na",
            consumer="test",
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


def test_mn_reconstructed_tail_is_computable_but_not_authoritative() -> None:
    temperature_K = 1873.15

    segment = ellingham_segment_for_temperature("Mn", temperature_K)
    authority_limit = ellingham_authority_limit(
        temperature_K,
        species="Mn",
        consumer="test",
    )

    assert "Mn(l)" in segment.phase_basis
    assert "confidence: reconstructed" in segment.janaf_anchor
    assert ellingham_delta_g_kj_per_mol_o2("Mn", temperature_K) == pytest.approx(
        -484.2527025,
        abs=1e-9,
    )
    assert authority_limit is not None
    assert authority_limit["authority_status"] == (
        ELLINGHAM_RECONSTRUCTED_AUTHORITY_STATUS
    )
    assert authority_limit["authority_flag"] == ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG
    assert authority_limit[ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG] is True
    assert authority_limit["authority_reason"] == "reconstructed_segment"
    assert authority_limit["segment_range_K"] == (1519.0, 2058.0)
    assert authority_limit["source_basis"] == segment.janaf_anchor

    diagnostic = ellingham_authority_diagnostic(
        {"Mn": authority_limit},
        consumer="test",
    )
    assert diagnostic["status"] == "authority_limited"
    assert diagnostic[ELLINGHAM_AUTHORITY_LIMIT_FLAG] is False
    assert diagnostic[ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG] is True
    assert diagnostic["extrapolated_beyond_fit_range_K"] == {}

    assert ellingham_fit_extrapolation(
        temperature_K,
        species="Mn",
        consumer="test",
    ) is None


@pytest.mark.parametrize(
    ("species", "breakpoint_K", "phase_fragment"),
    [
        ("Na", 1156.1, "Na(g)"),
        ("Mn", 1360.0, "Mn(gamma,s)"),
        ("Mn", 1410.0, "Mn(delta,s)"),
    ],
)
def test_ellingham_shared_breakpoints_select_next_segment(
    species: str,
    breakpoint_K: float,
    phase_fragment: str,
) -> None:
    segment = ellingham_segment_for_temperature(species, breakpoint_K)

    assert phase_fragment in segment.phase_basis
    assert ellingham_delta_g_kj_per_mol_o2(species, breakpoint_K) == pytest.approx(
        segment.delta_g_kJ_per_mol_O2(breakpoint_K)
    )
