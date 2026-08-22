"""Tests for the buffer-reproduction checker (benchmarks/buffer_reproduction.py).

Certified against synthetic ground truth in every direction the verdict
vocabulary can go, and — after review 2026-08-22 — with the RESIDUAL SHAPE of
each error type pinned explicitly, because the module's prose originally had
entropy and enthalpy transposed and no test caught it. Real-engine residuals are
findings, deliberately unpinned (instrument before gate).
"""

from __future__ import annotations

import math

import pytest

from benchmarks.buffer_reproduction import (
    ENTROPY_TOLERANCE_J_MOL_K,
    GAS_CONSTANT_J_MOL_K,
    PROVENANCE_RECALL,
    PROVENANCE_VERIFIED,
    PUBLISHED_BUFFERS,
    RESIDUAL_TOLERANCE_DEX,
    buffer_reproduction,
    enthalpy_tolerance_J_mol_O2,
    log10_fo2_from_delta_g,
)

LN10 = math.log(10.0)
SCALE = LN10 * GAS_CONSTANT_J_MOL_K
ENGINE_WINDOW = (1100.0, 2000.0)


def _exact_fn(buffer_key: str, dH_shift_J: float = 0.0, dS_shift_J: float = 0.0):
    """A delta_g_fn reproducing a published fit, optionally perturbed."""

    pub = PUBLISHED_BUFFERS[buffer_key]
    dH = pub.A_K * SCALE + dH_shift_J
    dS = -pub.B * SCALE + dS_shift_J

    def fn(species: str, T_K: float) -> float:
        return dH - T_K * dS

    return fn


# ---------------------------------------------------------------------------
# Residual SHAPE — the claim the module originally got backwards
# ---------------------------------------------------------------------------

def test_entropy_error_is_a_constant_offset_at_every_temperature():
    """An entropy error must produce the SAME dex residual at every T. Review
    2026-08-22: the module previously described this as growing under
    extrapolation, which is the enthalpy behaviour."""

    shift = 15.0
    pub = PUBLISHED_BUFFERS["NNO"]
    fn = _exact_fn("NNO", dS_shift_J=shift)
    residuals = [
        log10_fo2_from_delta_g(fn("Ni", T), T) - pub.log10_fo2(T)
        for T in (900.0, 1100.0, 1300.0, 1473.0, 2000.0)
    ]
    expected = -shift / SCALE
    for r in residuals:
        assert r == pytest.approx(expected, abs=1e-12)
    assert max(residuals) - min(residuals) == pytest.approx(0.0, abs=1e-12)


def test_enthalpy_error_is_a_decaying_one_over_T_residual():
    """An enthalpy error must SHRINK as temperature rises — the shape the
    module previously attributed to entropy."""

    shift = 15_000.0
    pub = PUBLISHED_BUFFERS["NNO"]
    fn = _exact_fn("NNO", dH_shift_J=shift)
    temps = [900.0, 1100.0, 1300.0, 1473.0, 2000.0]
    residuals = [
        log10_fo2_from_delta_g(fn("Ni", T), T) - pub.log10_fo2(T) for T in temps
    ]
    for T, r in zip(temps, residuals):
        assert r == pytest.approx(shift / (SCALE * T), abs=1e-12)
    assert all(a > b for a, b in zip(residuals, residuals[1:]))


# ---------------------------------------------------------------------------
# Verdict vocabulary
# ---------------------------------------------------------------------------

def test_exact_reproduction_scores_within_tolerance():
    r = buffer_reproduction(
        _exact_fn("NNO"), "NNO", engine_window_K=ENGINE_WINDOW, engine_name="synthetic"
    )
    assert r.verdict == "reproduces_within_tolerance"
    assert r.max_abs_residual_dex < 1e-9
    assert r.implied_dH_J_mol_O2 == pytest.approx(r.published_dH_J_mol_O2, rel=1e-9)
    assert r.implied_dS_J_mol_K_O2 == pytest.approx(r.published_dS_J_mol_K_O2, rel=1e-9)


def test_enthalpy_only_error_is_named_enthalpy():
    r = buffer_reproduction(
        _exact_fn("NNO", dH_shift_J=20_000.0),
        "NNO",
        engine_window_K=ENGINE_WINDOW,
        engine_name="synthetic",
    )
    assert r.verdict == "enthalpy_disagrees"
    assert r.delta_dS_J_mol_K_O2 == pytest.approx(0.0, abs=1e-6)
    assert r.delta_dH_J_mol_O2 == pytest.approx(20_000.0, rel=1e-6)
    assert any("1/T-shaped" in n for n in r.notes)


def test_entropy_only_error_is_named_entropy():
    r = buffer_reproduction(
        _exact_fn("NNO", dS_shift_J=15.0),
        "NNO",
        engine_window_K=ENGINE_WINDOW,
        engine_name="synthetic",
    )
    assert r.verdict == "entropy_disagrees"
    assert any("CONSTANT" in n and "does not decay" in n for n in r.notes)


def test_both_errors_are_named_together():
    r = buffer_reproduction(
        _exact_fn("NNO", dH_shift_J=40_000.0, dS_shift_J=15.0),
        "NNO",
        engine_window_K=ENGINE_WINDOW,
        engine_name="synthetic",
    )
    assert r.verdict == "enthalpy_and_entropy_disagree"


# ---------------------------------------------------------------------------
# Tolerance boundaries — epsilon neighbours on BOTH sides (review P2-3)
# ---------------------------------------------------------------------------

def test_entropy_tolerance_is_derived_not_asserted():
    """The entropy tolerance must equal the entropy error that produces exactly
    the residual tolerance — that is the whole justification."""

    assert ENTROPY_TOLERANCE_J_MOL_K == pytest.approx(
        RESIDUAL_TOLERANCE_DEX * SCALE, rel=1e-12
    )
    assert enthalpy_tolerance_J_mol_O2(1200.0) == pytest.approx(
        RESIDUAL_TOLERANCE_DEX * SCALE * 1200.0, rel=1e-12
    )


def test_epsilon_neighbours_of_the_residual_tolerance():
    """Just inside the residual tolerance reproduces; just outside does not.
    Uses a pure entropy error, whose residual is exactly -dS/(ln10 R) at every
    temperature, so the residual is known in closed form."""

    for factor, expect_reproduces in ((0.99, True), (1.01, False)):
        dS = factor * RESIDUAL_TOLERANCE_DEX * SCALE
        r = buffer_reproduction(
            _exact_fn("NNO", dS_shift_J=-dS),
            "NNO",
            engine_window_K=ENGINE_WINDOW,
            engine_name="synthetic",
        )
        assert r.max_abs_residual_dex == pytest.approx(
            factor * RESIDUAL_TOLERANCE_DEX, rel=1e-9
        )
        assert (r.verdict == "reproduces_within_tolerance") is expect_reproduces


def test_boundary_is_tight_from_both_sides():
    """The pass/fail boundary sits between 0.9999x and 1.0001x of the
    tolerance, i.e. it is the tolerance and not some other number.

    Exact float equality at the boundary is deliberately NOT pinned: the
    residual reaches the tolerance only through a log/exp round trip, so an
    equality assertion would be testing floating-point rounding rather than the
    comparison's semantics (the same trap already met when calibrating the
    Raoultian checker)."""

    for factor, expect_reproduces in ((0.9999, True), (1.0001, False)):
        dS = factor * RESIDUAL_TOLERANCE_DEX * SCALE
        r = buffer_reproduction(
            _exact_fn("NNO", dS_shift_J=-dS),
            "NNO",
            engine_window_K=ENGINE_WINDOW,
            engine_name="synthetic",
        )
        assert (r.verdict == "reproduces_within_tolerance") is expect_reproduces, factor


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_multiphase_buffer_refuses_rather_than_approximating():
    r = buffer_reproduction(
        _exact_fn("NNO"), "QFM", engine_window_K=ENGINE_WINDOW, engine_name="synthetic"
    )
    assert r.verdict == "not_expressible"
    assert r.max_abs_residual_dex is None


def test_disjoint_windows_refuse_rather_than_extrapolate():
    r = buffer_reproduction(
        _exact_fn("IW"), "IW", engine_window_K=(1600.0, 2600.0), engine_name="synthetic"
    )
    assert r.verdict == "not_evaluable"
    assert r.window_K is None
    assert any("extrapolation, not agreement" in n for n in r.notes)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_engine_output_refuses_instead_of_scoring(bad):
    """Fail-closed (review P2-2): a NaN/inf dG previously produced a typed
    scientific verdict with NaN thermodynamics attached."""

    r = buffer_reproduction(
        lambda species, T_K: bad,
        "NNO",
        engine_window_K=ENGINE_WINDOW,
        engine_name="synthetic",
    )
    assert r.verdict == "not_evaluable"
    assert r.max_abs_residual_dex is None
    assert any("non-finite" in n for n in r.notes)


def test_non_physical_engine_window_raises():
    for bad_window in ((float("nan"), 2000.0), (-100.0, 2000.0), (0.0, 2000.0)):
        with pytest.raises(ValueError):
            buffer_reproduction(_exact_fn("NNO"), "NNO", engine_window_K=bad_window)


def test_too_few_nodes_and_unknown_buffer_raise():
    with pytest.raises(ValueError):
        buffer_reproduction(
            _exact_fn("NNO"), "NNO", engine_window_K=ENGINE_WINDOW, n_nodes=2
        )
    with pytest.raises(ValueError):
        buffer_reproduction(
            _exact_fn("NNO"), "NOT-A-BUFFER", engine_window_K=ENGINE_WINDOW
        )


# ---------------------------------------------------------------------------
# Windows and provenance (review P1-1, P2-4)
# ---------------------------------------------------------------------------

def test_frost_windows_are_the_celsius_bounds_converted():
    """Frost tabulates validity in Celsius; every kelvin bound must be a clean
    C + 273.15. The earlier registry had IW/WM truncated at 1273 K and NNO
    extended to 1573 K, and the NNO error alone flipped a headline verdict."""

    expected_celsius = {
        "IW": (565.0, 1200.0),
        "NNO": (600.0, 1200.0),
        "QFM": (573.0, 1200.0),
        "WM": (565.0, 1200.0),
    }
    for key, (c_lo, c_hi) in expected_celsius.items():
        pub = PUBLISHED_BUFFERS[key]
        assert pub.T_min_K == pytest.approx(c_lo + 273.15, abs=0.01), key
        assert pub.T_max_K == pytest.approx(c_hi + 273.15, abs=0.01), key


def test_window_is_the_intersection_and_is_reported():
    r = buffer_reproduction(
        _exact_fn("IW"), "IW", engine_window_K=(1100.0, 2600.0), engine_name="synthetic"
    )
    assert r.window_K == (1100.0, 1473.15)
    assert r.published_window_K == (838.15, 1473.15)
    assert r.engine_window_K == (1100.0, 2600.0)


def test_every_registered_buffer_carries_provenance_and_pressure_term():
    for key, pub in PUBLISHED_BUFFERS.items():
        assert pub.provenance, key
        assert pub.pressure_coefficient_K_per_bar > 0.0, key
        assert "Table 1" in pub.citation, key


def test_unverified_anchor_would_be_disclosed():
    """The warning fires on anything not primary-verified. Constructed rather
    than relying on a registry entry, so the test keeps working when every
    shipped anchor is verified."""

    from dataclasses import replace

    from benchmarks import buffer_reproduction as mod

    shaky = replace(PUBLISHED_BUFFERS["NNO"], provenance=PROVENANCE_RECALL)
    original = mod.PUBLISHED_BUFFERS["NNO"]
    mod.PUBLISHED_BUFFERS["NNO"] = shaky
    try:
        r = buffer_reproduction(_exact_fn("NNO"), "NNO", engine_window_K=ENGINE_WINDOW)
        assert any("ANCHOR PROVENANCE" in n for n in r.notes)
    finally:
        mod.PUBLISHED_BUFFERS["NNO"] = original
    # a verified anchor stays quiet
    assert PUBLISHED_BUFFERS["NNO"].provenance == PROVENANCE_VERIFIED
    clean = buffer_reproduction(_exact_fn("NNO"), "NNO", engine_window_K=ENGINE_WINDOW)
    assert not any("ANCHOR PROVENANCE" in n for n in clean.notes)


def test_qfm_coefficients_match_their_other_home():
    """QFM must stay identical to the PySulfSat-derived values already used by
    simulator/melt_backend/sulfsat.py. Note this is DUPLICATION, not
    independent verification — both descend from Frost."""

    from simulator.melt_backend.sulfsat import _qfm_logfo2_frost

    qfm = PUBLISHED_BUFFERS["QFM"]
    for T_K in (1000.0, 1200.0, 1400.0):
        assert qfm.log10_fo2(T_K) == pytest.approx(_qfm_logfo2_frost(T_K, 1.0), abs=1e-9)


# ---------------------------------------------------------------------------
# Segmented engine data (review P2-1)
# ---------------------------------------------------------------------------

def test_segment_boundary_inside_window_labels_the_global_fit():
    r = buffer_reproduction(
        _exact_fn("IW"),
        "IW",
        engine_window_K=(1100.0, 2600.0),
        engine_name="synthetic",
        segment_boundaries_K=(1184.0,),
    )
    assert r.global_fit_is_regression_summary is True
    assert len(r.segment_fits) == 2
    assert r.segment_fits[0].T_hi_K == 1184.0
    assert r.segment_fits[1].T_lo_K == 1184.0
    assert any("REGRESSION SUMMARY" in n for n in r.notes)


def test_no_boundary_inside_window_leaves_one_segment_unlabelled():
    r = buffer_reproduction(
        _exact_fn("NNO"),
        "NNO",
        engine_window_K=ENGINE_WINDOW,
        engine_name="synthetic",
        segment_boundaries_K=(1728.0,),  # outside the 1100-1473.15 overlap
    )
    assert r.global_fit_is_regression_summary is False
    assert len(r.segment_fits) == 1


def test_log10_fo2_conversion_matches_hand_calculation():
    """dG = -400 kJ/mol O2 at 1200 K -> -400000/(2.302585*8.314463*1200)."""

    got = log10_fo2_from_delta_g(-400_000.0, 1200.0)
    assert got == pytest.approx(-17.41, abs=0.01)
    # scaling and sign both exercised, not just one constant
    assert log10_fo2_from_delta_g(-800_000.0, 1200.0) == pytest.approx(
        2.0 * got, rel=1e-12
    )
    assert log10_fo2_from_delta_g(+400_000.0, 1200.0) == pytest.approx(-got, rel=1e-12)
    assert log10_fo2_from_delta_g(-400_000.0, 2400.0) == pytest.approx(
        got / 2.0, rel=1e-12
    )


def test_report_serializes_complete_key_set():
    r = buffer_reproduction(
        _exact_fn("NNO"), "NNO", engine_window_K=ENGINE_WINDOW, engine_name="synthetic"
    )
    payload = r.as_dict()
    assert set(payload) == {
        "schema", "engine", "buffer", "species", "citation", "anchor_provenance",
        "window_K", "published_window_K", "engine_window_K", "n_nodes",
        "max_abs_residual_dex", "mean_residual_dex", "residual_span_dex",
        "implied_dH_J_mol_O2", "published_dH_J_mol_O2", "implied_dS_J_mol_K_O2",
        "published_dS_J_mol_K_O2", "delta_dH_J_mol_O2", "delta_dS_J_mol_K_O2",
        "enthalpy_tolerance_J_mol_O2", "global_fit_is_regression_summary",
        "segment_fits", "verdict", "notes",
    }
    assert payload["schema"] == "buffer_reproduction.v2"
    assert payload["window_K"] == [1100.0, 1473.15]


def test_live_ellingham_produces_a_typed_report_smoke():
    """Smoke only: the real table wires up. Residual VALUES are commissioning
    findings and are deliberately not pinned here."""

    from simulator.chemistry import ellingham_thermo as et

    def delta_g(species: str, T_K: float) -> float:
        return et.ellingham_delta_g_kj_per_mol_o2(species, T_K) * 1000.0

    for buffer_key in ("IW", "NNO"):
        species = PUBLISHED_BUFFERS[buffer_key].ellingham_species
        r = buffer_reproduction(
            delta_g,
            buffer_key,
            engine_window_K=et.ellingham_fit_range_K(species),
            engine_name="ellingham_thermo",
            segment_boundaries_K=[
                s.range_K[1] for s in et.ellingham_fit_segments(species)
            ],
        )
        assert r.schema == "buffer_reproduction.v2"
        assert r.verdict in (
            "reproduces_within_tolerance",
            "enthalpy_disagrees",
            "entropy_disagrees",
            "enthalpy_and_entropy_disagree",
            "not_evaluable",
            "not_expressible",
        )
