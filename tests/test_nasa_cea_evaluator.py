"""NASA-7 / NASA-9 polynomial evaluator — hand anchors + loud segment gates.

VR-4 / t-425. Ground-truth: closed-form hand evaluation of published CEA
coefficients (not simulator self-parity). Continuity residuals at shared
segment breakpoints must sit at floating-point noise for continuous source
records. Segment gap/overlap and missing standard-state convention fail loud.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.vapour_rail.nasa_cea import (
    R_J_PER_MOL_K,
    Nasa7Segment,
    Nasa9Segment,
    NasaCeaConventionError,
    NasaCeaDomainError,
    NasaCeaPolynomial,
    NasaCeaSegmentError,
    continuity_residuals,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_THERMO = ROOT / "tests" / "fixtures" / "cea" / "thermo_subset.inp"
FIXTURE_NASA7 = ROOT / "tests" / "fixtures" / "cea" / "nasa7_example.yaml"


# ---------------------------------------------------------------------------
# Hand-evaluated NASA-9 anchors from published O2 (tpis89) low segment
# ---------------------------------------------------------------------------
# Source coefficients (NASA CEA thermo.inp O2, T ∈ [200, 1000] K):
_O2_LOW_A = (
    -3.425563420e04,
    4.847000970e02,
    1.119010961e00,
    4.293889240e-03,
    -6.836300520e-07,
    -2.023372700e-09,
    1.039040018e-12,
)
_O2_LOW_B1 = -3.391454870e03
_O2_LOW_B2 = 1.849699470e01

_O2_MID_A = (
    -1.037939022e06,
    2.344830282e03,
    1.819732036e00,
    1.267847582e-03,
    -2.188067988e-07,
    2.053719572e-11,
    -8.193467050e-16,
)
_O2_MID_B1 = -1.689010929e04
_O2_MID_B2 = 1.738716506e01


def _hand_nasa9(
    a: tuple[float, ...], b1: float, b2: float, T: float
) -> tuple[float, float, float, float]:
    """Independent hand evaluation of the NASA-9 ratio forms.

    Derivation (premise → algebra → units → sanity) is documented on
    :meth:`Nasa9Segment.evaluate_ratios`. This helper re-implements the same
    algebra so the test does not merely call the production path twice.
    """
    a1, a2, a3, a4, a5, a6, a7 = a
    T2 = T * T
    T3 = T2 * T
    T4 = T3 * T
    invT = 1.0 / T
    invT2 = invT * invT
    lnT = math.log(T)
    cp_R = a1 * invT2 + a2 * invT + a3 + a4 * T + a5 * T2 + a6 * T3 + a7 * T4
    h_RT = (
        -a1 * invT2
        + a2 * lnT * invT
        + a3
        + a4 * T / 2.0
        + a5 * T2 / 3.0
        + a6 * T3 / 4.0
        + a7 * T4 / 5.0
        + b1 * invT
    )
    s_R = (
        -a1 * invT2 / 2.0
        - a2 * invT
        + a3 * lnT
        + a4 * T
        + a5 * T2 / 2.0
        + a6 * T3 / 3.0
        + a7 * T4 / 4.0
        + b2
    )
    return cp_R, h_RT, s_R, h_RT - s_R


def _o2_poly() -> NasaCeaPolynomial:
    return NasaCeaPolynomial(
        name="O2",
        family="nasa_cea_9",
        standard_state="gas",
        segments=(
            Nasa9Segment(200.0, 1000.0, _O2_LOW_A, _O2_LOW_B1, _O2_LOW_B2),
            Nasa9Segment(1000.0, 6000.0, _O2_MID_A, _O2_MID_B1, _O2_MID_B2),
        ),
        citation="Gurvich,1989; NASA CEA thermo.inp O2",
        source_ref_code="tpis89",
    )


def test_nasa9_matches_hand_evaluation_at_source_points() -> None:
    poly = _o2_poly()
    for T in (298.15, 500.0, 1000.0, 1500.0):
        st = poly.evaluate(T)
        if T <= 1000.0:
            # At the shared 1000 K breakpoint the lower segment is selected
            # for T < T_max of the non-final segment; at exactly 1000 the
            # upper segment is selected (shared endpoint → higher segment).
            if T < 1000.0:
                hand = _hand_nasa9(_O2_LOW_A, _O2_LOW_B1, _O2_LOW_B2, T)
            else:
                hand = _hand_nasa9(_O2_MID_A, _O2_MID_B1, _O2_MID_B2, T)
        else:
            hand = _hand_nasa9(_O2_MID_A, _O2_MID_B1, _O2_MID_B2, T)
        assert st.cp_over_R == pytest.approx(hand[0], rel=0, abs=1e-12)
        assert st.h_over_RT == pytest.approx(hand[1], rel=0, abs=1e-12)
        assert st.s_over_R == pytest.approx(hand[2], rel=0, abs=1e-12)
        assert st.g_over_RT == pytest.approx(hand[3], rel=0, abs=1e-12)

    # Sanity: O2 Cp at 298.15 K ≈ 29.38 J/(mol·K) (JANAF / CEA table class)
    st298 = poly.evaluate(298.15)
    assert st298.cp_J_per_mol_K == pytest.approx(29.3782, rel=1e-4)


def test_nasa9_continuity_at_segment_breakpoint() -> None:
    poly = _o2_poly()
    residuals = continuity_residuals(poly, 1000.0)
    assert residuals is not None
    # Continuous source records agree far below any engineering threshold.
    assert abs(residuals["d_cp_over_R"]) < 1e-8
    assert abs(residuals["d_h_over_RT"]) < 1e-8
    assert abs(residuals["d_s_over_R"]) < 1e-8


def test_nasa7_constant_cp_hand_check() -> None:
    """Closed-form NASA-7 monatomic-style constant-Cp fixture."""
    data = yaml.safe_load(FIXTURE_NASA7.read_text())
    segs = []
    for raw in data["segments"]:
        segs.append(
            Nasa7Segment(
                T_min_K=float(raw["T_min_K"]),
                T_max_K=float(raw["T_max_K"]),
                coefficients=tuple(raw["coefficients"]),  # type: ignore[arg-type]
            )
        )
    poly = NasaCeaPolynomial(
        name=data["species"],
        family="nasa_cea_7",
        standard_state=data["standard_state"],
        segments=tuple(segs),
    )
    st = poly.evaluate(300.0)
    # Hand: Cp/R = 3.5; H/RT = 3.5 + 1950/300 = 10; S/R = 20 by construction.
    assert st.cp_over_R == pytest.approx(3.5, abs=1e-15)
    assert st.h_over_RT == pytest.approx(10.0, abs=1e-12)
    assert st.s_over_R == pytest.approx(20.0, abs=1e-12)
    assert st.cp_J_per_mol_K == pytest.approx(3.5 * R_J_PER_MOL_K, rel=1e-15)

    pair = poly.evaluate_at_breakpoint_pair(1000.0)
    assert pair is not None
    lo, hi = pair
    assert lo.cp_over_R == pytest.approx(hi.cp_over_R, abs=1e-15)
    assert lo.h_over_RT == pytest.approx(hi.h_over_RT, abs=1e-15)
    assert lo.s_over_R == pytest.approx(hi.s_over_R, abs=1e-15)


def test_segment_gap_fails_loudly() -> None:
    with pytest.raises(NasaCeaSegmentError, match="gap"):
        NasaCeaPolynomial(
            name="gap",
            family="nasa_cea_9",
            standard_state="gas",
            segments=(
                Nasa9Segment(200.0, 1000.0, _O2_LOW_A, _O2_LOW_B1, _O2_LOW_B2),
                Nasa9Segment(1100.0, 2000.0, _O2_MID_A, _O2_MID_B1, _O2_MID_B2),
            ),
        )


def test_segment_overlap_fails_loudly() -> None:
    with pytest.raises(NasaCeaSegmentError, match="overlap"):
        NasaCeaPolynomial(
            name="overlap",
            family="nasa_cea_9",
            standard_state="gas",
            segments=(
                Nasa9Segment(200.0, 1000.0, _O2_LOW_A, _O2_LOW_B1, _O2_LOW_B2),
                Nasa9Segment(900.0, 2000.0, _O2_MID_A, _O2_MID_B1, _O2_MID_B2),
            ),
        )


def test_missing_standard_state_convention_fails_loudly() -> None:
    with pytest.raises(NasaCeaConventionError, match="standard_state"):
        NasaCeaPolynomial(
            name="bad",
            family="nasa_cea_9",
            standard_state="not_a_convention",  # type: ignore[arg-type]
            segments=(
                Nasa9Segment(200.0, 1000.0, _O2_LOW_A, _O2_LOW_B1, _O2_LOW_B2),
            ),
        )


def test_empty_segments_fail_loudly() -> None:
    with pytest.raises(NasaCeaSegmentError, match="at least one"):
        NasaCeaPolynomial(
            name="empty",
            family="nasa_cea_9",
            standard_state="gas",
            segments=(),
        )


def test_temperature_outside_domain_fails_loudly() -> None:
    poly = _o2_poly()
    with pytest.raises(NasaCeaDomainError, match="outside domain"):
        poly.evaluate(50.0)
    with pytest.raises(NasaCeaDomainError, match="outside domain"):
        poly.evaluate(9000.0)


def test_nan_temperature_is_named_not_blamed_on_the_extract() -> None:
    """NaN must be reported as NaN, not as an internal coverage gap.

    The domain check is `T < T_min or T > T_max`, and BOTH comparisons are False
    for NaN, so NaN used to pass it, match no segment and no shared breakpoint,
    and fall through to the bottom refusal: "not covered by any segment
    (internal gap after construction?)". That sent the reader hunting a coverage
    bug in the shipped extract over a value the caller supplied.

    Note +/-inf deliberately is NOT part of this fix: inf compares normally and
    is already reported correctly as outside the domain. NaN is the only value
    the range test cannot place.
    """

    poly = _o2_poly()

    with pytest.raises(NasaCeaDomainError) as excinfo:
        poly.evaluate(math.nan)
    message = str(excinfo.value)
    assert "NaN" in message, message
    assert "internal gap" not in message, message

    # The genuine-gap message must survive for the case it was written for, and
    # the ordinary out-of-domain path must be untouched.
    for bad in (math.inf, -math.inf, 50.0, 9000.0):
        with pytest.raises(NasaCeaDomainError, match="outside domain"):
            poly.evaluate(bad)

    # Valid temperatures, including both inclusive endpoints, still evaluate.
    for good in (poly.T_min_K, 1000.0, poly.T_max_K):
        assert poly.evaluate(good).T_K == good


def test_nasa7_vs_nasa9_family_segment_mismatch_fails() -> None:
    with pytest.raises(NasaCeaConventionError, match="Nasa9Segment"):
        NasaCeaPolynomial(
            name="mismatch",
            family="nasa_cea_9",
            standard_state="gas",
            segments=(
                Nasa7Segment(
                    300.0, 1000.0, (3.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                ),
            ),
        )


def test_fixture_thermo_subset_exists() -> None:
    assert FIXTURE_THERMO.is_file()
    text = FIXTURE_THERMO.read_text()
    assert "O2" in text
    assert "Na" in text
    assert "H2O(cr)" in text
