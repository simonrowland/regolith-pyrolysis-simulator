"""Tests for the Raoultian endmember-limit checker (benchmarks/raoultian_limit.py).

Certification against synthetic ground truth, both directions: an analytically
Raoultian model must pass, a constant-gamma model must be flagged, a
domain-refusing engine must read endmember_unreachable (a disclosure, neither
pass nor violation). Real-engine verdicts are commissioning findings,
deliberately unpinned (instrument before gate).
"""

from __future__ import annotations

import math

import pytest

from benchmarks.gibbs_duhem import GibbsDuhemInapplicable, mole_fractions_from_wt
from benchmarks.raoultian_limit import (
    LN_GAMMA_TOLERANCE,
    RaoultianLimitReport,
    raoultian_limit,
)

COMPONENT, DILUENT = "SiO2", "CaO"


def _regular_solution(wt):
    """ln gamma_i = A x_j^2: gamma -> 1 as x_i -> 1, analytically Raoultian."""

    x = mole_fractions_from_wt(wt)
    A = 2.0
    return {
        COMPONENT: x[COMPONENT] * math.exp(A * x[DILUENT] ** 2),
        DILUENT: x[DILUENT] * math.exp(A * x[COMPONENT] ** 2),
    }


def _constant_gamma(wt):
    """gamma = 0.7 everywhere: contradicts the pure-liquid standard state."""

    x = mole_fractions_from_wt(wt)
    return {name: 0.7 * value for name, value in x.items()}


def test_regular_solution_approaches_raoultian():
    report = raoultian_limit(
        _regular_solution, COMPONENT, DILUENT, T_K=1673.15, engine_name="synthetic"
    )
    assert report.verdict == "approaches_raoultian"
    assert report.x_reached > 0.999
    assert report.abs_ln_gamma_at_reached < 1e-3


def test_constant_gamma_violates_raoultian():
    report = raoultian_limit(
        _constant_gamma, COMPONENT, DILUENT, T_K=1673.15, engine_name="synthetic"
    )
    assert report.verdict == "violates_raoultian"
    # the flag is the standard-state contradiction, quantified
    assert report.abs_ln_gamma_at_reached == pytest.approx(abs(math.log(0.7)), rel=1e-9)
    assert any("does not approach 1" in n for n in report.notes)


def test_domain_refusal_reads_endmember_unreachable():
    def refuses_near_endmember(wt):
        x = mole_fractions_from_wt(wt)
        if x[COMPONENT] > 0.95:
            return None  # typed refusal territory
        return _regular_solution(wt)

    report = raoultian_limit(
        refuses_near_endmember, COMPONENT, DILUENT, T_K=1673.15, engine_name="synthetic"
    )
    assert report.verdict == "endmember_unreachable"
    assert report.x_reached <= 0.95
    assert any("not tested" in n for n in report.notes)
    assert any("refused above" in n for n in report.notes)


def test_all_refusing_engine_is_not_evaluable():
    report = raoultian_limit(
        lambda wt: None, COMPONENT, DILUENT, T_K=1673.15, engine_name="synthetic"
    )
    assert report.verdict == "not_evaluable"
    assert report.n_usable == 0


def test_missing_component_activity_counts_as_refusal_not_crash():
    def diluent_only(wt):
        x = mole_fractions_from_wt(wt)
        return {DILUENT: x[DILUENT]}

    report = raoultian_limit(
        diluent_only, COMPONENT, DILUENT, T_K=1673.15, engine_name="partial-adapter"
    )
    assert report.verdict == "not_evaluable"


def test_redox_open_walk_refuses_via_mole_fractions():
    with pytest.raises(GibbsDuhemInapplicable):
        raoultian_limit(
            _regular_solution, "SiO2", "FeO", T_K=1673.15, engine_name="synthetic"
        )


def test_tolerance_boundary_sides():
    """|ln gamma| clearly below tolerance passes; clearly above is flagged.

    Margins of 1e-3, not exact equality: log(exp(t)) round-trips a few ulp off
    t, so an exact-boundary pin would test float rounding, not semantics.
    """

    for eps, expected in ((-1e-3, "approaches_raoultian"), (1e-3, "violates_raoultian")):
        gamma = math.exp(LN_GAMMA_TOLERANCE + eps)

        def fixed_gamma(wt, g=gamma):
            x = mole_fractions_from_wt(wt)
            return {name: g * value for name, value in x.items()}

        report = raoultian_limit(
            fixed_gamma, COMPONENT, DILUENT, T_K=1673.15, engine_name="synthetic"
        )
        assert report.verdict == expected, (eps, report.verdict)


def test_report_serializes():
    report = raoultian_limit(
        _regular_solution, COMPONENT, DILUENT, T_K=1673.15, engine_name="synthetic"
    )
    payload = report.as_dict()
    assert payload["schema"] == "raoultian_limit.v1"
    assert set(payload) >= {"verdict", "x_reached", "abs_ln_gamma_at_reached", "notes"}
    assert isinstance(payload["notes"], list)


def test_internal_analytic_produces_typed_reports_smoke():
    """Smoke only: the real adapter wires up; verdicts are findings, unpinned."""

    from benchmarks import melt_activity_benchmark as bm

    fixture = bm.load_bench_set(bm.DEFAULT_BENCH_SET)
    engine = bm.build_engines(
        ["internal_analytic"], fixture, alphamelts_timeout_s=5.0
    )[0]

    def engine_activities(wt):
        result = bm.execute_engine(engine, wt, 1673.15, 1.0e-9)
        if result.status != "ok" or not result.activities:
            return None
        return result.activities

    report = raoultian_limit(
        engine_activities,
        "SiO2",
        "CaO",
        T_K=1673.15,
        engine_name="internal_analytic",
        n_nodes=9,
    )
    assert report.schema == "raoultian_limit.v1"
    assert report.verdict in (
        "approaches_raoultian",
        "walk_inconclusive",
        "violates_raoultian",
        "endmember_unreachable",
        "not_evaluable",
    )


# ---------------------------------------------------------------------------
# Review 2026-08-19 (grok P1-1) regressions: continuity shells vs the checker
# ---------------------------------------------------------------------------

def _shell_model_fu(ln_gamma_star, n_cations):
    """Simpler equivalent: gamma_fu = exp(n * ln gamma_cat) on the fu x."""

    def fn(wt):
        x = mole_fractions_from_wt(wt)
        out = {}
        for name, x_i in x.items():
            lg_cat = ln_gamma_star
            if x_i > 0.99:
                lg_cat = ln_gamma_star * ((1.0 - x_i) / 0.01) ** 2
            out[name] = x_i * math.exp(n_cations * lg_cat)
        return out

    return fn


def test_steep_shell_is_never_a_violation():
    """The P1-1 split case: |ln gamma*| ~ 10 shell, n = 2 formula unit.

    At the default (deep) floor the shell falls inside tolerance ->
    approaches; at the old shallow 0.05 wt% floor it is over tolerance but
    decaying at p ~ 2 -> walk_inconclusive. It must NEVER read
    violates_raoultian: the shell is approaching its own standard state.
    """

    fn = _shell_model_fu(math.log(3.5e-5), 2)

    deep = raoultian_limit(fn, COMPONENT, DILUENT, T_K=1673.15, engine_name="synthetic")
    assert deep.verdict == "approaches_raoultian", deep.as_dict()

    shallow = raoultian_limit(
        fn,
        COMPONENT,
        DILUENT,
        T_K=1673.15,
        engine_name="synthetic",
        wt_impurity_floor=0.6,
    )
    assert shallow.verdict in ("walk_inconclusive", "approaches_raoultian")
    assert shallow.verdict != "violates_raoultian", shallow.as_dict()
    if shallow.verdict == "walk_inconclusive":
        assert shallow.decay_exponent is not None
        assert shallow.decay_exponent >= 1.0


def test_constant_gamma_has_zero_decay_exponent():
    report = raoultian_limit(
        _constant_gamma, COMPONENT, DILUENT, T_K=1673.15, engine_name="synthetic"
    )
    assert report.verdict == "violates_raoultian"
    assert report.decay_exponent == pytest.approx(0.0, abs=1e-9)
    assert any("no Raoultian decay" in n for n in report.notes)


def test_regular_solution_decay_exponent_is_two():
    report = raoultian_limit(
        _regular_solution, COMPONENT, DILUENT, T_K=1673.15, engine_name="synthetic"
    )
    assert report.decay_exponent == pytest.approx(2.0, rel=0.05)


def test_ladder_bounds_are_validated():
    for start, floor in ((100.0, 0.01), (50.0, 0.0), (0.005, 0.01)):
        with pytest.raises(ValueError):
            raoultian_limit(
                _regular_solution,
                COMPONENT,
                DILUENT,
                T_K=1673.15,
                wt_impurity_start=start,
                wt_impurity_floor=floor,
            )


def test_unreachable_report_labels_midrange_gamma():
    def refuses_near_endmember(wt):
        x = mole_fractions_from_wt(wt)
        if x[COMPONENT] > 0.95:
            return None
        return _regular_solution(wt)

    report = raoultian_limit(
        refuses_near_endmember, COMPONENT, DILUENT, T_K=1673.15, engine_name="synthetic"
    )
    assert report.verdict == "endmember_unreachable"
    assert any("MID-RANGE reading" in n for n in report.notes)
