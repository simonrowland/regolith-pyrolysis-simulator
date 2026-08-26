"""Shomate evaluator — degenerate-input refusals (SC-130).

In-domain algebra anchors live in ``tests/test_vr4b_runtime_thermo_families.py``.
This file covers the fail-closed gates for NaN/inf coefficients and T.
"""

from __future__ import annotations

import math

import pytest

from simulator.vapour_rail.shomate import (
    ShomateConventionError,
    ShomateDomainError,
    ShomatePolynomial,
    ShomateSegment,
    ShomateSegmentError,
    coefficients_from_mapping,
)

# NIST WebBook SRD 69 O2(g) 100–700 K (Chase 1998). Same tuple as vr4b.
_O2_SHOMATE_100_700 = (
    31.32234,
    -20.23531,
    57.86644,
    -36.50624,
    -0.007374,
    -8.903471,
    246.7945,
    0.0,
)


def _o2_segment() -> ShomateSegment:
    return ShomateSegment(100.0, 700.0, _O2_SHOMATE_100_700)


def _o2_poly() -> ShomatePolynomial:
    return ShomatePolynomial(
        name="O2",
        standard_state="gas",
        segments=(_o2_segment(),),
    )


def _coeffs_with(*, H: float) -> tuple[float, ...]:
    return _O2_SHOMATE_100_700[:-1] + (H,)


def test_segment_evaluate_in_domain_o2_stays_finite() -> None:
    st = _o2_segment().evaluate(298.15)
    assert math.isfinite(st.cp_J_per_mol_K)
    assert math.isfinite(st.s_J_per_mol_K)
    assert st.cp_J_per_mol_K == pytest.approx(29.38, rel=5e-4)
    assert st.s_J_per_mol_K == pytest.approx(205.15, rel=1e-3)


def test_segment_evaluate_refuses_nonfinite_T() -> None:
    seg = _o2_segment()
    for T in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ShomateDomainError, match="finite T > 0"):
            seg.evaluate(T)


def test_segment_evaluate_refuses_non_positive_T() -> None:
    seg = _o2_segment()
    for T in (0.0, -1.0):
        with pytest.raises(ShomateDomainError, match="finite T > 0"):
            seg.evaluate(T)


def test_polynomial_evaluate_refuses_nan_with_nan_message() -> None:
    poly = _o2_poly()
    with pytest.raises(ShomateDomainError, match="T is NaN"):
        poly.evaluate(float("nan"))
    with pytest.raises(ShomateDomainError, match="outside domain"):
        poly.evaluate(float("inf"))


def test_coefficients_from_mapping_refuses_nonfinite() -> None:
    base = {k: 0.0 for k in "ABCDEFGH"}
    for key in ("H", "A"):
        for bad in (float("nan"), float("inf"), float("-inf"), "nan", "inf", "-inf"):
            raw = dict(base)
            raw[key] = bad
            with pytest.raises(ShomateConventionError, match="finite real"):
                coefficients_from_mapping(raw)
    seq = [0.0] * 8
    seq[7] = float("nan")
    with pytest.raises(ShomateConventionError, match="finite real"):
        coefficients_from_mapping(seq)


def test_segment_construction_refuses_nonfinite_coefficients() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ShomateConventionError, match="finite real"):
            ShomateSegment(100.0, 700.0, _coeffs_with(H=bad))


def test_segment_construction_refuses_nonfinite_bounds() -> None:
    with pytest.raises(ShomateSegmentError, match="finite real"):
        ShomateSegment(float("nan"), 700.0, _O2_SHOMATE_100_700)
    with pytest.raises(ShomateSegmentError, match="finite real"):
        ShomateSegment(100.0, float("inf"), _O2_SHOMATE_100_700)


def test_polynomial_refuses_nonfinite_delta_f_H() -> None:
    seg = _o2_segment()
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ShomateConventionError, match="delta_f_H_298_15"):
            ShomatePolynomial(
                name="O2",
                standard_state="gas",
                segments=(seg,),
                delta_f_H_298_15_J_per_mol=bad,
            )


def test_two_segment_nan_H_refused_at_construction() -> None:
    """Former agreement-gate bypass: finite first H plus NaN later H."""
    with pytest.raises(ShomateConventionError, match="finite real"):
        ShomatePolynomial(
            name="O2_split",
            standard_state="gas",
            segments=(
                ShomateSegment(100.0, 700.0, _coeffs_with(H=0.0)),
                ShomateSegment(700.0, 2000.0, _coeffs_with(H=float("nan"))),
            ),
        )
