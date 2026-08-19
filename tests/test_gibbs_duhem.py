"""Tests for the Gibbs-Duhem commissioning checker (benchmarks/gibbs_duhem.py).

Evidence discipline: the checker is certified against SYNTHETIC ground truth —
an analytically consistent model that must score ~0 and an analytically
inconsistent one that must score O(1). Real-engine runs are smoke-level here
(typed report produced), because a real engine's residual is a FINDING to be
reported, not a value to be pinned before anyone has ruled on it
(instrument before gate).

Synthetic ground truth, derived:
  Consistent  — binary regular solution  ln g1 = A x2^2, ln g2 = A x1^2:
      x1 d ln g1 + x2 d ln g2 = 2A x1 x2 (dx2 + dx1) = 0   since dx1 = -dx2.
  Inconsistent — ln g1 = B x1 with g2 = 1:
      sum x d ln g = B x1 dx1, integrating to (B/2)(x1_end^2 - x1_start^2) != 0,
      and TV equals |residual| on a monotonic path, so the index -> ~1.
"""

from __future__ import annotations

import math

import pytest

from benchmarks.gibbs_duhem import (
    GibbsDuhemInapplicable,
    gibbs_duhem_residual,
    mole_fractions_from_wt,
)

# Binary CMAS pair used throughout; wt% blends of these stay two-component.
START = {"SiO2": 80.0, "CaO": 20.0}
END = {"SiO2": 30.0, "CaO": 70.0}


def _consistent_activities(wt):
    """Regular-solution binary: analytically Gibbs-Duhem consistent."""

    x = mole_fractions_from_wt(wt)
    x_si, x_ca = x["SiO2"], x["CaO"]
    A = 2.0
    return {
        "SiO2": x_si * math.exp(A * x_ca**2),
        "CaO": x_ca * math.exp(A * x_si**2),
    }


def _inconsistent_activities(wt):
    """Independent per-species gammas: analytically inconsistent."""

    x = mole_fractions_from_wt(wt)
    B = 2.0
    return {
        "SiO2": x["SiO2"] * math.exp(B * x["SiO2"]),
        "CaO": x["CaO"] * 1.0,
    }


def _constant_gamma_activities(wt):
    x = mole_fractions_from_wt(wt)
    return {name: 0.7 * value for name, value in x.items()}


def test_consistent_model_scores_near_zero():
    report = gibbs_duhem_residual(
        _consistent_activities, START, END, T_K=1673.15, engine_name="synthetic"
    )
    assert report.total_variation > 0.0, "path must traverse gamma variation"
    assert report.consistency_index is not None
    assert report.consistency_index < 0.02, report.as_dict()


def test_inconsistent_model_is_flagged_order_one():
    report = gibbs_duhem_residual(
        _inconsistent_activities, START, END, T_K=1673.15, engine_name="synthetic"
    )
    assert report.consistency_index is not None
    assert report.consistency_index > 0.5, report.as_dict()


def test_discretisation_error_shrinks_but_real_inconsistency_persists():
    """The step-halving discriminator: the whole reason it is reported.

    Review 2026-08-19 caught that the original consistent-side assertion was
    vacuous (|~0| < |~0|/2 + eps passes trivially near machine zero). The
    consistent side now runs at a deliberately COARSE grid so the residual is
    measurably nonzero, and asserts the actual ~4x midpoint-rule shrink.
    """

    # The plain regular solution cannot serve here: its ln-gammas are
    # QUADRATIC, and the midpoint rule is exact on quadratics
    # (r_k = 2A xbar1 xbar2 (dx1 + dx2) = 0 identically), so its residual is
    # machine-zero at ANY grid — which is precisely why the original version
    # of this assertion was vacuous. A two-term Redlich-Kister
    # (G_ex/RT = x1 x2 [A + B(x1 - x2)]) is GD-consistent by construction and
    # cubic in x, so it carries real discretisation error.
    def rk2(wt):
        x = mole_fractions_from_wt(wt)
        x1, x2 = x["SiO2"], x["CaO"]
        A, B = 1.5, 1.0
        ln_g1 = x2**2 * (A + B * (3.0 * x1 - x2))
        ln_g2 = x1**2 * (A - B * (3.0 * x2 - x1))
        return {"SiO2": x1 * math.exp(ln_g1), "CaO": x2 * math.exp(ln_g2)}

    consistent = gibbs_duhem_residual(rk2, START, END, T_K=1673.15, n_nodes=3)
    assert consistent.residual_at_double_resolution is not None
    coarse = abs(consistent.integrated_residual)
    fine = abs(consistent.residual_at_double_resolution)
    assert coarse > 1e-8, "coarse grid must have measurable discretisation error"
    ratio = fine / coarse
    assert 0.1 < ratio < 0.5, f"expected ~0.25 midpoint shrink, got {ratio:.3f}"

    inconsistent = gibbs_duhem_residual(
        _inconsistent_activities, START, END, T_K=1673.15, n_nodes=11
    )
    assert inconsistent.residual_at_double_resolution is not None
    # A genuine inconsistency does NOT shrink with resolution.
    assert abs(inconsistent.residual_at_double_resolution) > (
        abs(inconsistent.integrated_residual) * 0.8
    )


def test_sign_cancelling_inconsistency_caught_by_rectified_index():
    """Reviewer-constructed MISS, now pinned.

    ln gamma_Si = 2 sin(2 pi x_Si) with gamma_Ca = 1 is inconsistent, but its
    residual changes sign along a wide path and the SIGNED index cancels to
    ~0.035 — below the 0.5 the naive test used. The rectified index cannot
    cancel: with only one species varying, every segment's |r_k| equals its
    TV contribution, so it sits at exactly 1.0.
    """

    def oscillator(wt):
        x = mole_fractions_from_wt(wt)
        return {
            "SiO2": x["SiO2"] * math.exp(2.0 * math.sin(2.0 * math.pi * x["SiO2"])),
            "CaO": x["CaO"] * 1.0,
        }

    report = gibbs_duhem_residual(
        oscillator, {"SiO2": 1.0, "CaO": 99.0}, {"SiO2": 99.0, "CaO": 1.0},
        T_K=1673.15, n_nodes=41,
    )
    assert report.consistency_index is not None
    assert report.consistency_index < 0.2, "the signed index misses this by design"
    assert report.rectified_index is not None
    assert report.rectified_index > 0.9, report.as_dict()
    # And the discriminator agrees: the residual does not shrink with resolution.
    assert report.residual_at_double_resolution is not None


def test_consistent_model_has_small_rectified_index_at_default_resolution():
    """The rectified index's other half: consistent models stay near zero."""

    report = gibbs_duhem_residual(
        _consistent_activities, START, END, T_K=1673.15
    )
    assert report.rectified_index is not None
    assert report.rectified_index < 0.02, report.as_dict()


def test_component_birth_segments_are_skipped_not_partially_summed():
    """Reviewer P3: a component appearing mid-path must not shrink the sum.

    Segments where the component set changes are skipped with a note, because
    ln gamma has no value at x = 0 on one side and a shared-set partial sum
    would be a silent incomplete closure.
    """

    def ternary(wt):
        x = mole_fractions_from_wt(wt)
        A = 2.0
        # Symmetric regular solution, consistent by construction.
        out = {}
        for name, x_i in x.items():
            others = sum(v for k, v in x.items() if k != name)
            out[name] = x_i * math.exp(A * others**2)
        return out

    report = gibbs_duhem_residual(
        ternary,
        {"SiO2": 80.0, "CaO": 20.0, "MgO": 0.0},
        {"SiO2": 60.0, "CaO": 20.0, "MgO": 20.0},
        T_K=1673.15,
        n_nodes=11,
    )
    assert any("appears or vanishes" in note for note in report.notes)


def test_alkali_paths_are_now_supported():
    """The motivating defect class is alkali-shaped; Na2O/K2O must be checkable."""

    x = mole_fractions_from_wt({"SiO2": 80.0, "Na2O": 20.0})
    assert x["Na2O"] > 0.0
    report = gibbs_duhem_residual(
        _consistent_na_activities,
        {"SiO2": 95.0, "Na2O": 5.0},
        {"SiO2": 70.0, "Na2O": 30.0},
        T_K=1473.15,
    )
    assert report.consistency_index is not None
    assert report.consistency_index < 0.02


def _consistent_na_activities(wt):
    x = mole_fractions_from_wt(wt)
    A = 1.5
    return {
        "SiO2": x["SiO2"] * math.exp(A * x["Na2O"] ** 2),
        "Na2O": x["Na2O"] * math.exp(A * x["SiO2"] ** 2),
    }


def test_analytic_value_of_the_inconsistent_residual():
    """Not just 'big': the integrated residual matches (B/2)(x_e^2 - x_s^2)."""

    x_start = mole_fractions_from_wt(START)["SiO2"]
    x_end = mole_fractions_from_wt(END)["SiO2"]
    expected = (2.0 / 2.0) * (x_end**2 - x_start**2)
    report = gibbs_duhem_residual(
        _inconsistent_activities, START, END, T_K=1673.15, n_nodes=81
    )
    assert report.integrated_residual == pytest.approx(expected, rel=2e-3)


def test_constant_gamma_is_trivial_not_evidence():
    """TV == 0 must yield index None plus the explicit triviality note.

    A constant-gamma model satisfies the identity by having nothing to test.
    Reporting index 0.0 would let a trivially-satisfied run masquerade as a
    passed consistency check.
    """

    report = gibbs_duhem_residual(
        _constant_gamma_activities, START, END, T_K=1673.15
    )
    from benchmarks.gibbs_duhem import TRIVIAL_TOTAL_VARIATION_FLOOR

    # Not exactly zero: ln(a/x) carries ~1e-16/term float noise even for an
    # exactly constant gamma. The floor exists precisely for this.
    assert report.total_variation < TRIVIAL_TOTAL_VARIATION_FLOOR
    assert report.consistency_index is None
    assert any("trivially" in note for note in report.notes)


def test_fe_couple_and_unsupported_components_refuse():
    """Fe couples refuse for the OPERATIONAL reason (cannot re-speciate);
    unlisted oxides refuse as unsupported; alkalis are supported (they are
    the motivating class). MnO/Cr2O3 were removed from the refusal list on
    review — in this project they are single-valence as modelled."""

    with pytest.raises(GibbsDuhemInapplicable, match="re-speciate"):
        mole_fractions_from_wt({"SiO2": 60.0, "FeO": 40.0})
    with pytest.raises(GibbsDuhemInapplicable, match="unsupported"):
        mole_fractions_from_wt({"SiO2": 60.0, "TiO2": 40.0})
    with pytest.raises(GibbsDuhemInapplicable, match="empty"):
        mole_fractions_from_wt({"SiO2": 0.0})


def test_mole_fraction_conversion_direction():
    """Equal weights: the lighter formula unit carries more moles."""

    x = mole_fractions_from_wt({"SiO2": 50.0, "MgO": 50.0})
    assert x["MgO"] > x["SiO2"]
    assert sum(x.values()) == pytest.approx(1.0)


def test_engine_refusals_become_skipped_nodes_not_numbers():
    calls = {"n": 0}

    def flaky(wt):
        calls["n"] += 1
        if calls["n"] % 4 == 0:
            return None  # typed engine refusal at this node
        return _consistent_activities(wt)

    report = gibbs_duhem_residual(flaky, START, END, T_K=1673.15, n_nodes=13)
    assert report.skipped_nodes, "refusals must surface"
    assert report.residual_at_double_resolution is None, (
        "step-halving comparison is meaningless across differing skip patterns"
    )
    assert any("skipped" in note for note in report.notes)


def test_incomplete_activity_coverage_skips_the_node():
    """An engine answering for only one melt component cannot close the sum."""

    def partial(wt):
        full = _consistent_activities(wt)
        return {"SiO2": full["SiO2"]}  # CaO missing

    report = gibbs_duhem_residual(partial, START, END, T_K=1673.15, n_nodes=7)
    assert len(report.skipped_nodes) == 7
    assert report.total_variation == 0.0


def test_internal_analytic_engine_produces_a_typed_report():
    """Smoke: the real adapter wires up. The residual VALUE is a finding for
    the commissioning report, deliberately not pinned here."""

    from benchmarks import melt_activity_benchmark as bm

    fixture = bm.load_bench_set(bm.DEFAULT_BENCH_SET)
    engines = bm.build_engines(["internal_analytic"], fixture, alphamelts_timeout_s=5.0)
    engine = engines[0]

    def engine_activities(wt):
        result = bm.execute_engine(engine, wt, 1673.15, 1.0e-9)
        if result.status != "ok" or not result.activities:
            return None
        return result.activities

    report = gibbs_duhem_residual(
        engine_activities,
        {"SiO2": 55.0, "CaO": 20.0, "MgO": 12.0, "Al2O3": 13.0},
        {"SiO2": 40.0, "CaO": 32.0, "MgO": 15.0, "Al2O3": 13.0},
        T_K=1673.15,
        engine_name="internal_analytic",
        n_nodes=9,
    )
    assert report.schema == "gibbs_duhem_residual.v1"
    assert report.n_nodes == 9
    payload = report.as_dict()
    assert set(payload) >= {
        "integrated_residual",
        "total_variation",
        "consistency_index",
        "skipped_nodes",
    }


# ---------------------------------------------------------------------------
# battery_verdict: the two-condition rule (rectified index AND materiality)
# ---------------------------------------------------------------------------

from benchmarks.gibbs_duhem import battery_verdict  # noqa: E402


def _tiny_tail_inconsistent_activities(wt):
    """Inconsistent in SHAPE but with numerically tiny gamma variation.

    Same independent-gamma construction as _inconsistent_activities but with
    B scaled to 1e-5, mimicking the measured internal_analytic shell-adjacent
    rows: rectified index ~1.0 over TV far below the 1e-3 materiality floor.
    A one-condition verdict on the index alone would flag this as a defect;
    the ratified two-condition rule must not.
    """

    x = mole_fractions_from_wt(wt)
    import math as _math

    return {
        "SiO2": x["SiO2"] * _math.exp(1e-5 * x["SiO2"]),
        "CaO": x["CaO"] * 1.0,
    }


def test_verdict_consistent_on_material_variation():
    report = gibbs_duhem_residual(
        _consistent_activities, START, END, T_K=1673.15, engine_name="synthetic"
    )
    assert report.total_variation > 1e-3
    assert battery_verdict(report) == "consistent_on_this_path"


def test_verdict_flags_material_inconsistency():
    report = gibbs_duhem_residual(
        _inconsistent_activities, START, END, T_K=1673.15, engine_name="synthetic"
    )
    assert report.total_variation > 1e-3
    assert battery_verdict(report) == "inconsistent"


def test_verdict_two_condition_rule_spares_immaterial_violation():
    report = gibbs_duhem_residual(
        _tiny_tail_inconsistent_activities,
        START,
        END,
        T_K=1673.15,
        engine_name="synthetic",
    )
    # The trap this rule exists for: index says O(1), physics says nothing.
    assert report.rectified_index is not None and report.rectified_index > 0.5
    assert report.total_variation < 1e-3
    assert battery_verdict(report) == "immaterial_variation"


def test_verdict_constant_gamma_is_immaterial_not_consistent():
    report = gibbs_duhem_residual(
        _constant_gamma_activities, START, END, T_K=1673.15, engine_name="synthetic"
    )
    assert battery_verdict(report) == "immaterial_variation"


def test_verdict_not_evaluable_when_coverage_cannot_close():
    """Adapter exposing only ONE of two components -> every node skipped.

    This is the structural MELTS-family outcome (parent-oxide adapter carries
    no CaO/MgO/alkali activities): the sum cannot close anywhere, and the
    verdict must say not_evaluable rather than anything reassuring.
    """

    def only_sio2(wt):
        x = mole_fractions_from_wt(wt)
        return {"SiO2": x["SiO2"]}

    report = gibbs_duhem_residual(
        only_sio2, START, END, T_K=1673.15, engine_name="partial-adapter"
    )
    assert len(report.skipped_nodes) == report.n_nodes
    assert battery_verdict(report) == "not_evaluable"


def test_verdict_thresholds_are_overridable_and_defaults_asserted():
    """Both knobs exercised in both directions, and the default pinned.

    Review 2026-08-19 (grok P2-2): the earlier version asserted only one
    hostile direction of one knob and never the default — an always-
    "inconsistent" constant satisfied it. Each assert here excludes at least
    one constant-function replacement.
    """

    report = gibbs_duhem_residual(
        _consistent_activities, START, END, T_K=1673.15, engine_name="synthetic"
    )
    assert battery_verdict(report) == "consistent_on_this_path"
    assert battery_verdict(report, index_threshold=-1.0) == "inconsistent"
    # Floor raised above this path's real TV: material becomes immaterial.
    assert (
        battery_verdict(report, materiality_floor_ln=report.total_variation * 2)
        == "immaterial_variation"
    )


def test_verdict_boundaries_are_exclusive_flag_inclusive_immaterial():
    """TV exactly at the floor is immaterial (<=); index exactly at the
    threshold is not flagged (strict >). Pins the boundary semantics the
    docstring implies (review P3-1)."""

    report = gibbs_duhem_residual(
        _inconsistent_activities, START, END, T_K=1673.15, engine_name="synthetic"
    )
    assert battery_verdict(report, materiality_floor_ln=report.total_variation) == (
        "immaterial_variation"
    )
    assert report.rectified_index is not None
    assert (
        battery_verdict(report, index_threshold=report.rectified_index)
        == "consistent_on_this_path"
    )


def test_verdict_keys_on_closed_segments_not_usable_nodes():
    """The two measured misclassification directions of the node-count
    predicate (review P1-1): isolated usable nodes must be not_evaluable no
    matter how many there are; two ADJACENT usable nodes carrying a material
    inconsistent residual must flag."""

    n_nodes = 21

    # Build refusal patterns by node index via a stateful wrapper: the
    # activity_fn sees compositions, not indices, so count calls instead.
    def by_index(pattern, base_fn):
        calls = {"i": -1}

        def fn(wt):
            calls["i"] += 1
            # gibbs_duhem_residual runs a second fine pass only when nothing
            # was skipped; these patterns always skip, so indices are stable.
            return base_fn(wt) if pattern(calls["i"] % n_nodes) else None

        return fn

    isolated = gibbs_duhem_residual(
        by_index(lambda i: i % 2 == 0, _inconsistent_activities),
        START,
        END,
        T_K=1673.15,
        engine_name="synthetic",
    )
    assert isolated.closed_segments == 0
    assert isolated.n_nodes - len(isolated.skipped_nodes) == 11
    assert battery_verdict(isolated) == "not_evaluable"

    adjacent_pair = gibbs_duhem_residual(
        by_index(lambda i: i in (10, 11), _inconsistent_activities),
        START,
        END,
        T_K=1673.15,
        engine_name="synthetic",
    )
    assert adjacent_pair.closed_segments == 1
    assert adjacent_pair.total_variation > 1e-3
    assert battery_verdict(adjacent_pair) == "inconsistent"


def test_verdict_never_crashes_on_hand_built_reports():
    """A public classifier returns tokens, not AssertionError/TypeError
    (review P2-1): material TV with a None or NaN index is unreadable
    evidence, not a crash."""

    from benchmarks.gibbs_duhem import GibbsDuhemReport

    for index in (None, float("nan")):
        report = GibbsDuhemReport(
            n_nodes=21,
            total_variation=0.01,
            rectified_index=index,
            closed_segments=5,
        )
        assert battery_verdict(report) == "not_evaluable"


def test_zero_segments_note_replaces_trivial_wording():
    """TV == 0 from zero segments must say "untested", never "trivially
    satisfied" (review P1-1: the triviality note dressed an untested path as
    a tested-and-small one)."""

    def only_sio2(wt):
        x = mole_fractions_from_wt(wt)
        return {"SiO2": x["SiO2"]}

    report = gibbs_duhem_residual(
        only_sio2, START, END, T_K=1673.15, engine_name="partial-adapter"
    )
    assert report.closed_segments == 0
    joined = " ".join(report.notes)
    assert "never integrated" in joined
    assert "satisfied trivially" not in joined
