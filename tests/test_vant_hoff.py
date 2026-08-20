"""Tests for the van 't Hoff checker (benchmarks/vant_hoff.py).

Certification vs synthetic ground truth in all three directions: a regular
solution with A(T) = A0/T must recover its known H_ex to <1%; an athermal
(T-independent) gamma must be disclosed as such; a curved T-dependence must
be reported nonlinear (a report, never a violation). Real-engine values are
findings, deliberately unpinned (instrument before gate).
"""

from __future__ import annotations

import math

import pytest

from benchmarks.gibbs_duhem import mole_fractions_from_wt
from benchmarks.vant_hoff import (
    GAS_CONSTANT_J_MOL_K,
    VantHoffReport,
    vant_hoff,
)

COMP = {"SiO2": 60.0, "CaO": 40.0}
T_NODES = [1500.0, 1600.0, 1700.0, 1800.0, 1900.0]

A0_K = 4000.0  # regular-solution parameter, ln gamma = (A0/T) x_j^2


def _regular_solution_T(wt, T_K):
    x = mole_fractions_from_wt(wt)
    A = A0_K / T_K
    return {
        "SiO2": x["SiO2"] * math.exp(A * x["CaO"] ** 2),
        "CaO": x["CaO"] * math.exp(A * x["SiO2"] ** 2),
    }


def _athermal(wt, T_K):
    x = mole_fractions_from_wt(wt)
    return {name: 0.7 * value for name, value in x.items()}


def _curved(wt, T_K):
    # ln gamma ~ B/T^2: strongly curved in 1/T
    x = mole_fractions_from_wt(wt)
    B = 6.0e6
    return {
        name: value * math.exp((B / T_K**2) * (1.0 - value)) for name, value in x.items()
    }


def test_regular_solution_recovers_known_H_ex():
    report = vant_hoff(
        _regular_solution_T, "SiO2", COMP, T_NODES, engine_name="synthetic"
    )
    assert report.verdict == "vant_hoff_linear"
    x = mole_fractions_from_wt(COMP)
    # derivation: ln gamma_Si = (A0 x_Ca^2)(1/T) => slope = A0 x_Ca^2,
    # H_ex = R * slope
    expected = GAS_CONSTANT_J_MOL_K * A0_K * x["CaO"] ** 2
    assert report.implied_H_ex_J_mol == pytest.approx(expected, rel=1e-2)


def test_athermal_gamma_is_disclosed():
    report = vant_hoff(_athermal, "SiO2", COMP, T_NODES, engine_name="synthetic")
    assert report.verdict == "athermal_gamma"
    assert abs(report.implied_H_ex_J_mol) < 1.0
    assert any("no temperature dependence" in n for n in report.notes)
    # the dilute-majority caveat must be present so a single small-x row
    # is not read as a model defect (review P1-1 rhetoric fix)
    assert any("PARTIAL molar" in n for n in report.notes)


def test_curvature_is_reported_not_flagged():
    """B/T^2 curvature: half-window fitted slopes drift by
    ~(u_hi-u_lo)/(2 u_mid), measured 0.118 on this window — detectable where
    a plain fit-rms metric measured only ~1% (the miscalibration this test
    originally caught)."""

    report = vant_hoff(_curved, "SiO2", COMP, T_NODES, engine_name="synthetic")
    assert report.verdict == "nonlinear_T_dependence"
    assert report.slope_drift_rel > 0.10
    assert any("not a violation" in n for n in report.notes)


def test_linear_model_has_negligible_slope_drift():
    report = vant_hoff(
        _regular_solution_T, "SiO2", COMP, T_NODES, engine_name="synthetic"
    )
    assert report.slope_drift_rel < 1e-6


def test_refusing_temperatures_reduce_to_not_evaluable():
    def refuses_most(wt, T_K):
        if T_K > 1550.0:
            return None
        return _regular_solution_T(wt, T_K)

    report = vant_hoff(refuses_most, "SiO2", COMP, T_NODES, engine_name="synthetic")
    assert report.verdict == "not_evaluable"
    assert report.n_usable == 1


def test_too_few_nodes_and_bad_component_raise():
    with pytest.raises(ValueError):
        vant_hoff(_athermal, "SiO2", COMP, [1500.0, 1600.0])
    with pytest.raises(ValueError):
        vant_hoff(_athermal, "MgO", COMP, T_NODES)


def test_report_serializes():
    report = vant_hoff(
        _regular_solution_T, "SiO2", COMP, T_NODES, engine_name="synthetic"
    )
    payload = report.as_dict()
    assert payload["schema"] == "vant_hoff.v1"
    assert payload["composition_wt_pct"] == COMP
    assert isinstance(payload["T_nodes_K"], list)


def test_material_ratio_but_immaterial_enthalpy_is_not_nonlinear():
    """Two-condition guard (caught by the first commissioning run): the drift
    RATIO alone must not flag when the curvature is immaterial in enthalpy
    terms. Review P2-1 caught the first version of this test sitting below
    BOTH conditions (vacuous); this one is constructed to STRADDLE them and
    asserts the straddle before asserting the verdict."""

    import math as _math

    def small_slope_material_ratio(wt, T_K):
        x = mole_fractions_from_wt(wt)
        u = 1.0 / T_K
        # window-normalized slope change = 2c*u_span ~ 59 K (h_drift ~ 0.49
        # kJ < 1 kJ floor) on a ~750 K mean slope (rel ~ 0.08 > 0.05 floor);
        # retuned after the grid-invariant normalization (codex round)
        # doubled the raw half-slope difference.
        lg = 2.1e5 * u * u + 500.0 * u
        return {name: value * _math.exp(lg) for name, value in x.items()}

    report = vant_hoff(
        small_slope_material_ratio, "SiO2", COMP, T_NODES, engine_name="synthetic"
    )
    # the straddle must actually hold, else this test is vacuous again
    assert report.slope_drift_rel > 0.05, report.as_dict()
    assert report.slope_drift_H_J_mol < 1000.0, report.as_dict()
    assert report.verdict != "nonlinear_T_dependence", report.as_dict()


def test_sign_changing_H_ex_is_not_athermal():
    """Review P1-1: a U-shaped ln gamma vs 1/T (sign-changing H_ex) has a
    near-zero MEAN slope but large half-window slopes. The old athermal
    branch fabricated drift=0 and called it athermal; it must now report the
    curvature (nonlinear, with an infinite or large drift ratio)."""

    import math as _math

    x_mid_u = 0.5 * (1.0 / 1500.0 + 1.0 / 1900.0)

    def u_shape(wt, T_K):
        x = mole_fractions_from_wt(wt)
        u = 1.0 / T_K
        lg = 3.0e6 * (u - x_mid_u) ** 2  # symmetric about the window midpoint
        return {name: value * _math.exp(lg) for name, value in x.items()}

    report = vant_hoff(u_shape, "SiO2", COMP, T_NODES, engine_name="synthetic")
    assert report.verdict == "nonlinear_T_dependence", report.as_dict()
    assert report.slope_drift_H_J_mol > 1000.0


# ---------------------------------------------------------------------------
# codex round (2026-08-20) regressions
# ---------------------------------------------------------------------------

def test_oscillation_in_estimator_null_space_is_not_athermal():
    """codex P1: ln gamma values orthogonal to all slope moments read
    athermal at span 2.0 under the two-condition rule. The span condition
    must catch it: material span with ~zero slopes is nonlinear, and the
    note says H_ex is not a usable summary."""

    import math as _math

    targets = dict(zip([1500.0, 1600.0, 1700.0, 1800.0, 1900.0],
                       [1.851632, 0.074080, 1.927273, 0.0, 2.0]))

    def oscillatory(wt, T_K):
        x = mole_fractions_from_wt(wt)
        lg = targets[T_K]
        return {name: value * _math.exp(lg) for name, value in x.items()}

    report = vant_hoff(oscillatory, "SiO2", COMP, T_NODES, engine_name="synthetic")
    assert report.verdict == "nonlinear_T_dependence", report.as_dict()
    assert any("NOT a usable summary" in n for n in report.notes)


def test_verdict_is_node_parity_invariant():
    """codex P1: the raw half-slope difference flipped the verdict between
    4/5/6/7 nodes on the same smooth curve (their measured case). The
    window-normalized drift must give ONE verdict at every parity."""

    import math as _math

    def smooth(wt, T_K):
        x = mole_fractions_from_wt(wt)
        u = 1.0 / T_K
        lg = 1900.0 * u + 1.0e6 * u * u
        return {name: value * _math.exp(lg) for name, value in x.items()}

    verdicts = set()
    for n in (4, 5, 6, 7):
        nodes = [1500.0 + k * (400.0 / (n - 1)) for k in range(n)]
        verdicts.add(
            vant_hoff(smooth, "SiO2", COMP, nodes, engine_name="synthetic").verdict
        )
    assert len(verdicts) == 1, verdicts


def test_nonphysical_temperature_grids_are_refused():
    for bad in ([1500.0, 1600.0, float("nan")], [1500.0, 1600.0, -1700.0],
                [1500.0, 1600.0, 0.0]):
        with pytest.raises(ValueError):
            vant_hoff(_athermal, "SiO2", COMP, bad)


def test_adjacent_ulp_temperatures_collapse_not_crash():
    import math as _math

    nodes = [1800.0, 1900.0, _math.nextafter(1900.0, float("inf"))]
    report = vant_hoff(_athermal, "SiO2", COMP, nodes, engine_name="synthetic")
    # 3 distinct T floats but 2 distinct reciprocals: not_evaluable, no crash
    assert report.verdict == "not_evaluable"


def test_report_serializes_complete_key_set():
    """codex P2: the old serialization test passed with fields dropped."""

    report = vant_hoff(
        _regular_solution_T, "SiO2", COMP, T_NODES, engine_name="synthetic"
    )
    payload = report.as_dict()
    assert set(payload) == {
        "schema", "engine", "component", "composition_wt_pct", "T_nodes_K",
        "n_usable", "implied_H_ex_J_mol", "slope_drift_rel", "ln_gamma_span",
        "slope_drift_H_J_mol", "activity_basis", "verdict", "notes",
    }
    assert payload["activity_basis"] == "formula_unit"
    assert payload["engine"] == "synthetic"
    assert payload["verdict"] == "vant_hoff_linear"
    assert payload["slope_drift_H_J_mol"] is not None
