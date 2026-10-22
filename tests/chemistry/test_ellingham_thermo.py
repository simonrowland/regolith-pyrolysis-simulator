from __future__ import annotations

import math

import pytest

from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_AUTHORITY_LIMIT_FLAG,
    ELLINGHAM_EXTENSION_AUTHORITY,
    ELLINGHAM_EXTENSION_ERROR_ENVELOPE_KJ_PER_MOL,
    ELLINGHAM_METAL_PHASE_CONDENSED,
    ELLINGHAM_METAL_PHASE_GAS,
    ELLINGHAM_OUTSIDE_SEGMENT_REASON,
    ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG,
    ELLINGHAM_RECONSTRUCTED_AUTHORITY_STATUS,
    ELLINGHAM_THERMO,
    MG_NORMAL_BOILING_POINT_K,
    ellingham_authority_limit,
    ellingham_authority_diagnostic,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_extension_error_bound_dex,
    ellingham_fit_extrapolation,
    ellingham_fit_segments,
    ellingham_metal_phase_kind,
    ellingham_same_phase_extension,
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


def _envelope_dex(temperature_K: float) -> tuple[float, float]:
    """δlog10 K = δG / (R T ln 10), written out here.

    R is the CODATA value 8.31446261815324 J/(mol·K). δG is the named
    envelope in kJ/mol, converted to J/mol. This is not a call to the owner.
    """

    gas_constant = 8.31446261815324
    median_kJ, maximum_kJ = (0.72, 4.76)
    scale = gas_constant * temperature_K * math.log(10.0)
    return (median_kJ * 1000.0 / scale, maximum_kJ * 1000.0 / scale)


@pytest.mark.parametrize(
    ("species", "fit_breakpoint_K"),
    [
        ("Na", 1156.1),
        ("Ni", 1728.0),
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


@pytest.mark.parametrize("temperature_K", [0.0, -1.0, -273.15, -1000.0])
def test_nonpositive_temperature_is_refused(temperature_K: float) -> None:
    with pytest.raises(ValueError, match="temperature_K must be > 0 K"):
        ellingham_segment_for_temperature("Na", temperature_K)
    with pytest.raises(ValueError, match="temperature_K must be > 0 K"):
        ellingham_delta_g_kj_per_mol_o2("Na", temperature_K)
    with pytest.raises(ValueError, match="temperature_K must be > 0 K"):
        ellingham_fit_extrapolation(
            temperature_K,
            species="Na",
            consumer="test",
        )
    with pytest.raises(ValueError, match="temperature_K must be > 0 K"):
        ellingham_authority_limit(
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


def test_mn_sub_liquidus_query_uses_solid_segment_not_legacy_liquid_row() -> None:
    temperature_K = 1500.0
    segment = ellingham_segment_for_temperature("Mn", temperature_K)
    dH_f, dS_f, _n_M, _n_ox = ELLINGHAM_THERMO["Mn"]
    legacy_liquid_delta_g = dH_f - temperature_K * dS_f

    assert "Mn(delta,s)" in segment.phase_basis
    assert ellingham_delta_g_kj_per_mol_o2(
        "Mn",
        temperature_K,
    ) == pytest.approx(segment.delta_g_kJ_per_mol_O2(temperature_K))
    assert ellingham_delta_g_kj_per_mol_o2(
        "Mn",
        temperature_K,
    ) != pytest.approx(legacy_liquid_delta_g)


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
    ("species", "temperature_K", "edge_K", "distance_K", "notice_reason"),
    [
        (
            "Ca",
            1000.0,
            1100.0,
            100.0,
            "authority=extrapolated; segment_band_K=1100-1115; "
            "segment_edge_K=1100; extension_distance_K=100; "
            "original_refusal_reason=ellingham-formation-outside-segment",
        ),
        (
            "Ca",
            3100.0,
            2600.0,
            500.0,
            "authority=extrapolated; segment_band_K=2400-2600; "
            "segment_edge_K=2600; extension_distance_K=500; "
            "original_refusal_reason=ellingham-formation-outside-segment",
        ),
        (
            "Al",
            2700.0,
            2600.0,
            100.0,
            "authority=extrapolated; segment_band_K=2327-2600; "
            "segment_edge_K=2600; extension_distance_K=100; "
            "original_refusal_reason=ellingham-formation-outside-segment",
        ),
        (
            "Fe",
            3100.0,
            2600.0,
            500.0,
            "authority=extrapolated; segment_band_K=1809-2600; "
            "segment_edge_K=2600; extension_distance_K=500; "
            "original_refusal_reason=ellingham-formation-outside-segment",
        ),
        (
            "Ti",
            1000.0,
            1100.0,
            100.0,
            "authority=extrapolated; segment_band_K=1100-1166; "
            "segment_edge_K=1100; extension_distance_K=100; "
            "original_refusal_reason=ellingham-formation-outside-segment",
        ),
    ],
    ids=["Ca-1000", "Ca-3100", "Al-2700", "Fe-3100", "Ti-1000"],
)
def test_same_phase_extension_names_the_nearest_segment(
    species: str,
    temperature_K: float,
    edge_K: float,
    distance_K: float,
    notice_reason: str,
) -> None:
    extension = ellingham_same_phase_extension(species, temperature_K)

    assert extension is not None
    assert extension["segment"] is ellingham_segment_for_temperature(
        species,
        temperature_K,
    )
    assert extension["authority"] == ELLINGHAM_EXTENSION_AUTHORITY
    assert extension["segment_edge_K"] == edge_K
    assert extension["extension_distance_K"] == distance_K
    assert extension["original_refusal_reason"] == ELLINGHAM_OUTSIDE_SEGMENT_REASON
    median_dex, maximum_dex = _envelope_dex(temperature_K)
    assert extension["error_bound_dex_median"] == pytest.approx(median_dex)
    assert extension["error_bound_dex_max"] == pytest.approx(maximum_dex)
    prefix, median_text, maximum_text = extension["notice_reason"].rsplit("; ", 2)
    assert prefix == notice_reason
    assert median_text.startswith("error_bound_dex_median=")
    assert maximum_text.startswith("error_bound_dex_max=")
    assert float(median_text.split("=", 1)[1]) == pytest.approx(median_dex)
    assert float(maximum_text.split("=", 1)[1]) == pytest.approx(maximum_dex)


def test_extension_error_bound_dex_shows_the_low_temperature_blowup() -> None:
    """4.76 kJ/mol is about 2.49 dex at 100 K and about 0.12 dex at 2000 K."""

    assert ELLINGHAM_EXTENSION_ERROR_ENVELOPE_KJ_PER_MOL == (0.72, 4.76)
    median_100, maximum_100 = _envelope_dex(100.0)
    median_2000, maximum_2000 = _envelope_dex(2000.0)
    assert ellingham_extension_error_bound_dex(100.0) == pytest.approx(
        (median_100, maximum_100)
    )
    assert ellingham_extension_error_bound_dex(2000.0) == pytest.approx(
        (median_2000, maximum_2000)
    )
    assert maximum_100 == pytest.approx(2.49, abs=0.01)
    assert maximum_2000 == pytest.approx(0.12, abs=0.01)
    ti = ellingham_same_phase_extension("Ti", 100.0)
    assert ti is not None
    assert ti["error_bound_dex_max"] == pytest.approx(maximum_100)


@pytest.mark.parametrize(
    ("species", "temperature_K"),
    [
        ("Ca", 3200.0),
        ("Ca", 700.0),
        ("Ca", 1600.0),
        ("Al", 2800.0),
        ("Fe", 1000.0),
        ("K", 1000.0),
        ("Zr", 2700.0),
    ],
    ids=["Ca-3200", "Ca-700", "Ca-1600", "Al-2800", "Fe-1000", "K-1000", "Zr-2700"],
)
def test_phase_crossing_and_in_range_are_not_extended(
    species: str,
    temperature_K: float,
) -> None:
    """3200 K is CaO's melting point, the start of the liquid field.

    The solid segment's high bound is exclusive, and this fit has no
    CaO(l) segment, so the point stays outside. 1600 K is inside a
    certified Ca segment and is not an extension either.
    """

    assert ellingham_same_phase_extension(species, temperature_K) is None


def test_formation_extension_does_not_change_the_runtime_clamp() -> None:
    flagged = ellingham_fit_extrapolation(1000.0, species="Ca", consumer="test")

    assert flagged is not None
    assert flagged["authority_flag"] == ELLINGHAM_AUTHORITY_LIMIT_FLAG
    segment = ellingham_segment_for_temperature("Ca", 1000.0)
    assert ellingham_delta_g_kj_per_mol_o2("Ca", 1000.0) == (
        segment.delta_g_kJ_per_mol_O2(1000.0)
    )


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
