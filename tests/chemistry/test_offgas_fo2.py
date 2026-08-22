"""Tests for the b-203 offgas fO2 coupling (water-gas-shift equilibrated).

The tests are deliberately split by WHAT KIND of claim they defend, because they
are not equally strong evidence and should not be read as if they were:

  * PHYSICS (external, non-circular) -- the shift constant against textbook
    water-gas-shift values, and the imposed fO2 against the iron-wustite buffer.
    These use nothing from this module's reconciliation and can genuinely fail.
  * ALGEBRA -- the identity linking the couple disagreement to log10(Q/K).
  * IMPLEMENTATION -- post-equilibration agreement of the two couples. This is
    ALGEBRAICALLY GUARANTEED by construction, so it proves the solver converged
    and the code matches the derivation. It is NOT evidence about the physics,
    and a green result here must never be quoted as if it were.
  * CONSERVATION -- atoms and moles across the shift.
  * REFUSAL -- the cases where no honest number exists.
"""

from __future__ import annotations

import math

import pytest

from simulator.chemistry.offgas_fo2 import (
    _COUPLE_RECORDS,
    _effective_domain_K,
    CO_COUPLE_ONLY,
    COMPUTED_ASSUMPTION_UNVERIFIED,
    COMPUTED_NO_RECONCILIATION,
    H2_COUPLE_ONLY,
    WGS_EQUILIBRATED,
    OffgasFO2Unavailable,
    imposed_fo2,
    load_buffer_polynomials,
    shift_extent,
    water_gas_shift_log10_K,
)


@pytest.fixture(scope="module")
def polys():
    return load_buffer_polynomials()


# --------------------------------------------------------------------------
# PHYSICS -- external references. These can actually fail.
# --------------------------------------------------------------------------


def test_wgs_constant_matches_textbook_values(polys):
    """K_wgs against published water-gas-shift equilibrium constants.

    Non-circular: the CEA polynomials were never fitted to shift data, so
    reproducing the tabulated K is external corroboration of both the records
    and the K2/K1 derivation. Published K_wgs is ~4.0-4.4 at 800 K, ~1 at
    ~1100 K, and ~0.7 at 1200 K. Tolerances are loose enough to accommodate
    source-to-source spread and tight enough to catch a wrong reaction.
    """

    assert 3.8 <= 10 ** water_gas_shift_log10_K(polys, 800.0) <= 4.6
    assert 0.65 <= 10 ** water_gas_shift_log10_K(polys, 1200.0) <= 0.80


def test_wgs_constant_falls_with_temperature_and_crosses_unity(polys):
    """The shift is mildly exothermic: K falls with T, passing 1 near 1100 K."""

    temps = [700.0, 800.0, 900.0, 1000.0, 1100.0, 1300.0, 1600.0, 1873.15]
    values = [water_gas_shift_log10_K(polys, T) for T in temps]
    assert all(b < a for a, b in zip(values, values[1:])), values

    assert water_gas_shift_log10_K(polys, 1000.0) > 0.0
    assert water_gas_shift_log10_K(polys, 1200.0) < 0.0
    # The crossover sits near 1100 K, not merely somewhere in a 200 K window.
    assert abs(water_gas_shift_log10_K(polys, 1100.0)) < 0.05


def test_unit_ratio_h2_buffer_stays_near_iron_wustite_in_frost_range(polys):
    """At H2O/H2 = 1 the imposed fO2 sits near IW -- a COARSE sanity check.

    Scope stated honestly, after review. Frost 1991's IW expression is quoted
    for 565-1200 C, i.e. ~838-1473 K. An earlier version of this test asserted
    agreement at 1673 K and 1873 K, which are OUTSIDE that window: the tight
    agreement there is agreement with an extrapolation, and asserting it would
    have dressed an extrapolation up as validation. Temperatures here stay
    inside the stated range.

    What this test can and cannot do: H2O/H2 = 1 and iron-wustite are DIFFERENT
    constraints that merely happen to lie close, so exact agreement is not
    expected and a tight tolerance would be meaningless. The job here is to
    catch gross errors -- a sign flip, a missing factor of two, the wrong
    reaction -- which would move the result by many dex. Accuracy is certified
    by the published-K_wgs comparison above, not by this.
    """

    for T_K in (900.0, 1100.0, 1300.0, 1473.15):
        result = imposed_fo2({"H2": 1.0, "H2O": 1.0}, T_K, polys)
        iw = -27489.0 / T_K + 6.702
        assert result.coupling == H2_COUPLE_ONLY
        assert abs(result.log10_fO2 - iw) < 1.0, (
            f"T={T_K}: imposed {result.log10_fO2:.3f} vs IW {iw:.3f} -- a gap "
            "this large means a wrong reaction, not a buffer difference"
        )


# --------------------------------------------------------------------------
# ALGEBRA
# --------------------------------------------------------------------------


def test_couple_disagreement_equals_two_log_q_over_k(polys):
    """gap = log10 fO2(CO) - log10 fO2(H2) = 2 * log10(Q / K_wgs), exactly.

    This identity is what makes the reconciliation auditable: the whole spread
    between the two couples is one number, and that number is the gas's distance
    from shift equilibrium. It must hold to machine precision at every T.
    """

    gas = {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}
    for T_K in (700.0, 900.0, 1100.0, 1300.0, 1600.0, 1873.15):
        r = imposed_fo2(gas, T_K, polys)
        assert r.raw_couple_disagreement_dex == pytest.approx(
            2.0 * r.log10_Q_over_K, abs=1e-9
        ), f"identity broke at {T_K} K"


def test_gap_and_extent_change_sign_together_at_the_crossover(polys):
    """The couple gap and the shift extent must change sign at the same point.

    What sets the direction is sign(K_wgs - Q), NOT temperature. T enters only
    through K_wgs, so a fixed gas turns over where K_wgs crosses ITS OWN Q --
    for this fixture (Q = 0.898) at 1128.6 K, where a bisection on the extent
    sign and a bisection on K_wgs = Q agree to six figures. That is NOT the
    1095.7 K point at which K_wgs crosses unity; the two coincide only for a gas
    that happens to sit near Q = 1, which is why an earlier version of this
    docstring read as a temperature law ("below ~1100 K the shift runs forward")
    and was wrong for every gas but this one. Gases that never flip at all
    across the same span, pinned in the companion test below: Q = 2e4 runs
    reverse at both 700 K and 1873 K, Q = 2e-6 forward at both.

    What this test asserts is the AGREEMENT, not the threshold: a sign
    disagreement would mean the extent solver and the fO2 inversion disagree
    about which way the reaction goes.
    """

    gas = {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}
    low = imposed_fo2(gas, 700.0, polys)
    high = imposed_fo2(gas, 1873.15, polys)

    assert low.raw_couple_disagreement_dex < 0.0
    assert low.extent_mol > 0.0
    assert high.raw_couple_disagreement_dex > 0.0
    assert high.extent_mol < 0.0


def test_shift_direction_follows_q_versus_k_not_temperature(polys):
    """Direction is sign(K_wgs - Q); temperature alone decides nothing.

    The regression this guards: a reader who takes "below ~1100 K it runs
    forward" as the law will mis-predict every gas whose Q is not near 1, and
    will read a correct result as a bug. Both gases below straddle the 1095.7 K
    unity point and the 1100 K figure, and neither changes direction.

    K_wgs falls monotonically with T over this span (9.40 at 700 K, 0.244 at
    1873 K), so a gas held far above that range is reverse throughout and one
    held far below is forward throughout.
    """

    strongly_shifted = {"H2": 10.0, "H2O": 0.05, "CO": 0.05, "CO2": 5.0}
    barely_shifted = {"H2": 0.01, "H2O": 5.0, "CO": 10.0, "CO2": 0.01}

    for T_K in (700.0, 1873.15):
        high_q = imposed_fo2(strongly_shifted, T_K, polys)
        assert high_q.log10_Q_over_K > 0.0, f"Q > K expected at {T_K} K"
        assert high_q.extent_mol < 0.0, (
            f"Q = 2e4 must run REVERSE at {T_K} K; a temperature law would "
            "predict forward below ~1100 K"
        )

        low_q = imposed_fo2(barely_shifted, T_K, polys)
        assert low_q.log10_Q_over_K < 0.0, f"Q < K expected at {T_K} K"
        assert low_q.extent_mol > 0.0, (
            f"Q = 2e-6 must run FORWARD at {T_K} K; a temperature law would "
            "predict reverse above ~1100 K"
        )


def test_fixture_crossover_tracks_q_not_the_unity_point(polys):
    """The fixture gas turns over at K_wgs = Q, not at K_wgs = 1.

    These two temperatures differ by ~33 K for this gas, which is what makes the
    coincidence detectable at all: near Q = 1 they are close enough that the
    wrong explanation still predicts the right answer.
    """

    gas = {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}

    def bisect(f, lo, hi):
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if f(lo) * f(mid) <= 0.0:
                hi = mid
            else:
                lo = mid
        return 0.5 * (lo + hi)

    extent_flip = bisect(lambda T: imposed_fo2(gas, T, polys).extent_mol, 400.0, 3000.0)
    q_crossing = bisect(lambda T: imposed_fo2(gas, T, polys).log10_Q_over_K, 400.0, 3000.0)
    unity = bisect(lambda T: water_gas_shift_log10_K(polys, T), 400.0, 3000.0)

    assert extent_flip == pytest.approx(q_crossing, abs=1e-3), (
        "the extent must turn over exactly where Q crosses K"
    )
    assert extent_flip == pytest.approx(1128.6, abs=0.5)
    assert unity == pytest.approx(1095.7, abs=0.5)
    assert abs(extent_flip - unity) > 25.0, (
        "if these collapsed together the test could no longer tell the correct "
        "explanation from the temperature-law one"
    )


def test_equilibrated_fo2_lies_between_the_raw_couples(polys):
    """The reconciliation interpolates; it must never extrapolate past either.

    Labelled honestly after review: this follows from the construction (the
    extent moves both ratios monotonically toward the common equilibrium), so it
    is a guard against a solver that overshoots, NOT independent evidence that
    the reconciliation is physically right.
    """

    gas = {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}
    for T_K in (700.0, 1100.0, 1873.15):
        r = imposed_fo2(gas, T_K, polys)
        lo = min(r.log10_fO2_h2_couple_raw, r.log10_fO2_co_couple_raw)
        hi = max(r.log10_fO2_h2_couple_raw, r.log10_fO2_co_couple_raw)
        assert lo - 1e-9 <= r.log10_fO2 <= hi + 1e-9


# --------------------------------------------------------------------------
# IMPLEMENTATION -- guaranteed by construction. Not physics evidence.
# --------------------------------------------------------------------------


def test_both_couples_agree_after_equilibration(polys):
    """Solver/algebra check ONLY -- and it must actually exercise BOTH couples.

    Setting Q = K_wgs forces agreement, so this cannot fail for a physical
    reason; it fails only if the extent solver did not converge or an inversion
    was miscoded. Do not cite a green result here as evidence the coupling is
    physically right.

    An earlier version recomputed only the H2 side and compared it to a value
    that IS the H2 side -- a tautology that would have stayed green with the CO
    path completely broken. Review caught it. Both couples are now reconstructed
    independently from the equilibrated moles.
    """

    gas = {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}
    for T_K in (700.0, 1100.0, 1873.15):
        r = imposed_fo2(gas, T_K, polys)
        eq = r.equilibrated_mol

        # Recover each K from its own RAW inversion, then re-apply to the
        # EQUILIBRATED moles. K1 and K2 are recovered separately, so a fault in
        # either path shows up here.
        log10_K1 = math.log10(gas["H2O"] / gas["H2"]) - 0.5 * (
            r.log10_fO2_h2_couple_raw
        )
        log10_K2 = math.log10(gas["CO2"] / gas["CO"]) - 0.5 * (
            r.log10_fO2_co_couple_raw
        )
        h2_side = 2.0 * (math.log10(eq["H2O"] / eq["H2"]) - log10_K1)
        co_side = 2.0 * (math.log10(eq["CO2"] / eq["CO"]) - log10_K2)

        assert h2_side == pytest.approx(r.log10_fO2, abs=1e-9)
        assert co_side == pytest.approx(r.log10_fO2, abs=1e-9), (
            f"T={T_K}: CO couple gives {co_side:.9f}, reported {r.log10_fO2:.9f}"
        )
        assert co_side == pytest.approx(h2_side, abs=1e-9)


def test_status_never_claims_a_bare_ok(polys):
    """★ The status must not overstate what was established.

    A bare "ok" reads as endorsement, and this module never verifies that the
    shift actually ran -- it cannot, since that depends on residence time and
    catalysis. At 700 K, where uncatalysed shift is effectively frozen, a status
    of "ok" would be actively misleading. So there is no "ok" in the vocabulary.
    """

    full = imposed_fo2({"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}, 700.0, polys)
    assert full.status == COMPUTED_ASSUMPTION_UNVERIFIED
    assert full.status != "ok"

    single = imposed_fo2({"H2": 1.0, "H2O": 1.0}, 1473.15, polys)
    assert single.status == COMPUTED_NO_RECONCILIATION
    assert single.status != "ok"


def test_unmodelled_redox_species_are_reported_not_dropped(polys):
    """★ CH4, H2S and free O2 must not vanish silently.

    Dropping them would return the fO2 of a DIFFERENT gas than the caller passed
    in. Under the module's own equilibrium assumption they are already
    consistent with the reported fO2; insofar as that assumption fails they are
    unaccounted reducing capacity. Either way the caller must be able to see
    them, so they are counted and reported.
    """

    gas = {
        "H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22,
        "CH4": 0.8, "H2S": 0.05, "O2": 1e-9,
    }
    r = imposed_fo2(gas, 1473.15, polys)
    assert r.unmodelled_species_mol == pytest.approx(
        {"CH4": 0.8, "H2S": 0.05, "O2": 1e-9}
    )
    assert any("unmodelled_species_mol" in n for n in r.notes)
    assert r.as_dict()["unmodelled_species_mol"]["CH4"] == pytest.approx(0.8)

    # Absent unmodelled species leave the map empty rather than zero-filled.
    clean = imposed_fo2(
        {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}, 1473.15, polys
    )
    assert clean.unmodelled_species_mol == {}


def test_corrupt_unmodelled_species_still_refuses(polys):
    """A NaN in CH4 must not be quietly excluded from the report."""

    with pytest.raises(OffgasFO2Unavailable):
        imposed_fo2(
            {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22, "CH4": float("nan")},
            1473.15,
            polys,
        )


def test_realistic_primary_pyrolysis_gas(polys):
    """★ The project's own reducing-species set, with CO a proven zero.

    Review pointed out the module was only ever exercised on a balanced
    four-species gas, while this project's organics module treats CO as a proven
    zero for primary pyrolysis and carries CH4 and H2S. Writing this test found
    a real gap.

    My first draft asserted the shift "cannot run" without CO. That was wrong:
    with CO2 present the REVERSE shift (CO2 + H2 -> CO + H2O) generates CO, and
    it consumes most of the CO2 doing so. The code was right and the test was
    wrong.

    But it also showed that the raw gas's Q is infinite here, so the couple
    SPREAD is undefined -- while equilibration still moves fO2 by ~0.3 dex. The
    field reporting how hard the assumption works must not go blind precisely
    when the assumption is most active, which is what this now pins.
    """

    gas = {"H2": 3.0, "H2O": 0.4, "CH4": 1.2, "H2S": 0.2, "CO2": 0.15}
    r = imposed_fo2(gas, 1473.15, polys)

    assert r.coupling == WGS_EQUILIBRATED
    assert r.status == COMPUTED_ASSUMPTION_UNVERIFIED
    assert r.unmodelled_species_mol == pytest.approx({"CH4": 1.2, "H2S": 0.2})

    # Reverse shift: CO is generated, CO2 largely consumed.
    assert r.extent_mol < 0.0
    assert r.equilibrated_mol["CO"] > 0.0
    assert r.equilibrated_mol["CO2"] < gas["CO2"]

    # Q is infinite for the raw gas, so the SPREAD is undefined ...
    assert r.log10_Q_over_K is None
    assert r.raw_couple_disagreement_dex is None
    # ... but the assumption is demonstrably doing work, and must say so.
    h2_only = imposed_fo2({"H2": 3.0, "H2O": 0.4}, 1473.15, polys).log10_fO2
    assert r.assumption_strain_dex is not None, (
        "the shift moved the answer; strain must not report None here"
    )
    assert r.assumption_strain_dex == pytest.approx(abs(r.log10_fO2 - h2_only))
    assert r.assumption_strain_dex > 0.1, r.assumption_strain_dex
    assert any("does not go blind here" in n for n in r.notes)


def test_shift_extent_refuses_non_positive_constant():
    """K <= 0 has no equilibrium; the boundary root the quadratic yields is not one."""

    for bad_K in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(OffgasFO2Unavailable):
            shift_extent(1.4, 0.35, 0.22, 2.0, bad_K)


# --------------------------------------------------------------------------
# CONSERVATION -- bookkeeping checks. The shift is WRITTEN mole-conserving, so
# these verify the implementation did not botch the arithmetic. They are not
# independent physics evidence, and review was right to say so.
# --------------------------------------------------------------------------


def test_shift_conserves_atoms_and_moles(polys):
    gas = {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}
    r = imposed_fo2(gas, 1473.15, polys)
    eq = r.equilibrated_mol

    assert sum(eq.values()) == pytest.approx(sum(gas.values()), rel=1e-12)
    # C: CO + CO2 ; H: 2*(H2 + H2O) ; O: CO + 2*CO2 + H2O
    assert eq["CO"] + eq["CO2"] == pytest.approx(gas["CO"] + gas["CO2"], rel=1e-12)
    assert eq["H2"] + eq["H2O"] == pytest.approx(gas["H2"] + gas["H2O"], rel=1e-12)
    assert eq["CO"] + 2 * eq["CO2"] + eq["H2O"] == pytest.approx(
        gas["CO"] + 2 * gas["CO2"] + gas["H2O"], rel=1e-12
    )


def test_extent_never_drives_a_species_negative(polys):
    """Across lopsided compositions, every equilibrated amount stays >= 0."""

    compositions = [
        {"H2": 1e-6, "H2O": 5.0, "CO": 3.0, "CO2": 1e-6},
        {"H2": 5.0, "H2O": 1e-6, "CO": 1e-6, "CO2": 3.0},
        {"H2": 1.0, "H2O": 1.0, "CO": 1.0, "CO2": 1.0},
        {"H2": 1e3, "H2O": 1e-3, "CO": 1e-3, "CO2": 1e3},
    ]
    for gas in compositions:
        for T_K in (700.0, 1100.0, 1873.15):
            r = imposed_fo2(gas, T_K, polys)
            assert all(v >= -1e-12 for v in r.equilibrated_mol.values()), (
                f"{gas} at {T_K}: {r.equilibrated_mol}"
            )


def test_shift_extent_solver_hits_the_target_constant():
    """Directly: the returned extent reproduces Q = K."""

    for K in (0.05, 0.5, 1.0, 2.0, 40.0):
        a, b, c, d = 1.4, 0.35, 0.22, 2.0
        xi = shift_extent(a, b, c, d, K)
        Q = ((c + xi) * (d + xi)) / ((a - xi) * (b - xi))
        assert Q == pytest.approx(K, rel=1e-9), f"K={K} xi={xi}"


# --------------------------------------------------------------------------
# REFUSAL -- where no honest number exists
# --------------------------------------------------------------------------


def test_no_complete_couple_refuses(polys):
    """Pure H2 imposes an unboundedly low fO2. There is no finite answer."""

    with pytest.raises(OffgasFO2Unavailable, match="no complete redox couple"):
        imposed_fo2({"H2": 1.0}, 1473.15, polys)
    with pytest.raises(OffgasFO2Unavailable):
        imposed_fo2({}, 1473.15, polys)
    with pytest.raises(OffgasFO2Unavailable):
        imposed_fo2({"N2": 5.0, "Ar": 2.0}, 1473.15, polys)


def test_single_couple_reports_q_over_k_as_none_not_zero(polys):
    """★ The honesty case.

    With no carbon carrier the shift cannot proceed at all, so the gas's distance
    from shift equilibrium is UNDEFINED. Reporting 0.0 would assert "already at
    equilibrium" -- a claim about a reaction that cannot even run, and precisely
    the kind of fabricated-zero this project treats as a defect.
    """

    r = imposed_fo2({"H2": 1.0, "H2O": 1.0}, 1473.15, polys)
    assert r.coupling == H2_COUPLE_ONLY
    assert r.log10_Q_over_K is None
    assert r.extent_mol is None
    assert r.log10_fO2 is not None

    r = imposed_fo2({"CO": 1.0, "CO2": 1.0}, 1473.15, polys)
    assert r.coupling == CO_COUPLE_ONLY
    assert r.log10_Q_over_K is None


def test_non_finite_and_negative_amounts_are_not_treated_as_present(polys):
    """A NaN or negative mole count must not masquerade as a declared amount."""

    with pytest.raises(OffgasFO2Unavailable):
        imposed_fo2({"H2": float("nan"), "H2O": float("inf")}, 1473.15, polys)
    with pytest.raises(OffgasFO2Unavailable):
        imposed_fo2({"H2": 1.0, "H2O": -3.0}, 1473.15, polys)


def test_corrupt_entry_beside_a_clean_couple_still_refuses(polys):
    """★ The mixed dirty+clean case, which the all-garbage test missed.

    This is the fabrication path review found: with ONE corrupt entry and one
    clean couple surviving, coercing the corrupt value to 0.0 let the shift run,
    invent the missing species, and return status=ok with a finite fO2. A
    corrupt record must refuse no matter what else is present.
    """

    for bad in (float("nan"), float("inf"), -1.0, "lots", None, [1.0]):
        with pytest.raises(OffgasFO2Unavailable):
            imposed_fo2(
                {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": bad}, 1473.15, polys
            )


def test_tiny_inventories_do_not_get_a_fabricated_equilibrium(polys):
    """★ The interval-pad window, at and below the simulator's own noop floor.

    With an absolute pad the physical root interval could be NARROWER than the
    tolerance, so both algebraic roots were accepted, the unphysical one taken,
    and a wrong-but-finite fO2 returned. Whatever happens now must be either a
    correct extent or a refusal -- never a plausible fabrication.
    """

    for scale in (1e-13, 1e-15, 1e-18):
        gas = {k: v * scale for k, v in
               {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}.items()}
        try:
            r = imposed_fo2(gas, 1473.15, polys)
        except OffgasFO2Unavailable:
            continue  # refusing is a valid outcome
        # If it returned, the extent must be genuinely physical and Q must
        # actually equal K -- scale-invariance makes this checkable: the answer
        # must match the same composition at unit scale.
        assert all(v >= 0.0 for v in r.equilibrated_mol.values()), r.equilibrated_mol
        unit = imposed_fo2(
            {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}, 1473.15, polys
        )
        assert r.log10_fO2 == pytest.approx(unit.log10_fO2, abs=1e-6), (
            f"scale {scale:g} gave {r.log10_fO2:.6f}, unit scale gives "
            f"{unit.log10_fO2:.6f} -- the shift is scale-invariant, so this is "
            "a fabricated extent"
        )


def test_assumption_strain_is_reported_and_is_none_when_nothing_is_reconciled(
    polys,
):
    """The assumption's cost must be visible, and absent when unused."""

    r = imposed_fo2({"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}, 700.0, polys)
    assert r.equilibrium_assumption_verified is False
    # Strain is the MOVEMENT, not the spread: how far equilibration shifted the
    # answer away from what the raw H2 couple alone would have said.
    assert r.assumption_strain_dex == pytest.approx(
        abs(r.log10_fO2 - r.log10_fO2_h2_couple_raw)
    )
    # It must be bounded by the spread, since the result lies between the two.
    assert r.assumption_strain_dex <= abs(r.raw_couple_disagreement_dex) + 1e-9
    assert any("KINETICS are not modelled" in n for n in r.notes)

    single = imposed_fo2({"H2": 1.0, "H2O": 1.0}, 1473.15, polys)
    assert single.assumption_strain_dex is None
    assert single.equilibrium_assumption_verified is False


def test_full_gas_reports_the_assumption_it_made(polys):
    """Every equilibrated result must carry the assumption in its own notes."""

    r = imposed_fo2({"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}, 1473.15, polys)
    assert r.coupling == WGS_EQUILIBRATED
    assert any("water-gas-shift equilibrium" in n for n in r.notes)
    assert r.as_dict()["log10_Q_over_K"] is not None


def test_unusable_temperature_refuses_in_this_module_s_own_type(polys):
    """An unusable T_K must raise OffgasFO2Unavailable, not a foreign type.

    "Unusable" and not "every bad", deliberately: control-flow signals
    (KeyboardInterrupt, SystemExit, GeneratorExit) raised by a caller's
    __float__ still propagate untouched, by the policy argued in _safe_repr.

    Before the guard, T_K was unvalidated and travelled into the CEA
    polynomials, which raise NasaCeaDomainError (<- NasaCeaError <- ValueError)
    while this module's own refusal is OffgasFO2Unavailable (<- Exception).
    The hierarchies are disjoint, so no single exception TYPE covers both, and
    every input below escaped as a foreign one. "Every input below" is the honest
    scope -- an earlier docstring said every bad T_K, which was false while the
    conversion guard caught only a three-type tuple; a __float__ raising an
    ordinary RuntimeError leaked. The guard now catches Exception, and the
    re-entrant/hostile cases are pinned by the tests that follow. (A caller CAN write one clause,
    ``except (OffgasFO2Unavailable, NasaCeaError):`` -- but only by knowing the
    second type is reachable from here, which is the coupling this guard removes.)

    NaN is listed deliberately, but not for the reason an earlier version of
    this docstring gave. The module's range test is NEGATED -- ``not (lo <= T
    <= hi)`` -- so NaN already fails it: the chained comparison is False and its
    negation raises. The separate finite check exists to make the message say
    "non-finite temperature" rather than describe NaN as lying outside a numeric
    interval. (The un-negated form, ``if T < lo or T > hi``, is the one that
    admits NaN; the CEA evaluator uses exactly that form at
    simulator/vapour_rail/nasa_cea.py:373 -- ``if T < T_min_K or T > T_max_K``,
    both False for NaN -- which is why NaN there falls through the segment walk
    and emerges as "not covered by any segment (internal gap after
    construction?)", blaming the data for the caller's input. Tracked as b-223 in
    the goal-flight task store, which lives outside this checkout; the sentence
    above is written to stand alone if that reference cannot be resolved.)
    """

    gas = {"H2": 1.0, "H2O": 1.0, "CO": 1.0, "CO2": 1.0}
    # Unusable for EVERY couple: not a finite number, or outside even the CO
    # couple's 200-20000 K records. Temperatures between 6000 and 20000 K are
    # deliberately NOT here -- they are unusable only for couples that read H2O,
    # and pinning them as universally unusable would re-assert the very
    # over-restriction that per-couple domains exist to remove. That case is
    # covered by test_a_co_only_gas_is_not_refused_for_a_constant_it_never_uses.
    unusable = [
        float("nan"),
        float("inf"),
        float("-inf"),
        0.0,
        -5.0,
        100.0,
        199.9,
        20000.1,
        1e6,
        "hot",
        None,
        [1500],
        10**10000,  # float() raises OverflowError, not ValueError
    ]

    # Every gas SHAPE, not just one. Each constant is now computed lazily, so a
    # single-gas check would prove only that the shape it happens to use
    # validates -- and the refusal would hold by reachability rather than by
    # construction. These six shapes cover both single-couple branches, the
    # shift branch, and the partnerless cases that reach neither couple.
    shapes = [
        gas,
        {"CO": 1.0, "CO2": 1.0},
        {"H2": 1.0, "H2O": 1.0},
        {"H2": 1.0, "CO2": 1.0},
        {"H2O": 1.0, "CO": 1.0},
        {"H2": 1.0, "CO2": 1.0, "H2O": 1.0},
    ]

    for T_K in unusable:
        with pytest.raises(OffgasFO2Unavailable):
            water_gas_shift_log10_K(polys, T_K)
        for shape in shapes:
            with pytest.raises(OffgasFO2Unavailable):
                imposed_fo2(shape, T_K, polys)

    # Between the two ceilings the refusal is couple-specific, not global: the
    # shift and any H2-bearing gas refuse, a CO-only gas computes.
    for T_K in (6000.1, 7000.0, 19999.0):
        with pytest.raises(OffgasFO2Unavailable):
            water_gas_shift_log10_K(polys, T_K)
        for shape in shapes:
            if "H2" in shape or "H2O" in shape:
                with pytest.raises(OffgasFO2Unavailable):
                    imposed_fo2(shape, T_K, polys)
            else:
                assert math.isfinite(imposed_fo2(shape, T_K, polys).log10_fO2)


def test_usable_domain_is_computed_from_the_records_a_couple_actually_reads(polys):
    """The window is the intersection over that couple's OWN three records.

    This test moves the bounds rather than asserting the shipped ones, because
    an implementation hardcoded to (200, 6000) passes any test that only checks
    today's numbers -- which is what the previous version of this test did, and
    why it could not fail when the advertised computed-intersection behaviour
    was broken.
    """

    import dataclasses

    def narrowed(name, lo, hi):
        """Same record, segments clipped to [lo, hi]."""
        base = polys[name]
        segs = tuple(
            dataclasses.replace(
                s,
                T_min_K=max(s.T_min_K, lo),
                T_max_K=min(s.T_max_K, hi),
            )
            for s in base.segments
            if s.T_min_K < hi and s.T_max_K > lo
        )
        return dataclasses.replace(base, segments=segs)

    # Clip ONE record of the CO couple and confirm the ceiling follows it.
    clipped = dict(polys)
    clipped["CO2"] = narrowed("CO2", 200.0, 3000.0)
    assert _effective_domain_K(clipped, _COUPLE_RECORDS["CO"]) == (200.0, 3000.0)
    # ...while the H2 couple, which does not read CO2, is untouched.
    assert _effective_domain_K(clipped, _COUPLE_RECORDS["H2"]) == (200.0, 6000.0)

    # Clip the floor of an O2 record, shared by both couples: both move.
    raised = dict(polys)
    raised["O2"] = narrowed("O2", 500.0, 20000.0)
    assert _effective_domain_K(raised, _COUPLE_RECORDS["CO"])[0] == 500.0
    assert _effective_domain_K(raised, _COUPLE_RECORDS["H2"])[0] == 500.0

    # ...and clip a record that is NOT currently binding. This is the case that
    # gives the test teeth. Clipping only CO2 (today's binding ceiling) and O2
    # (today's binding floor) is still passed by an implementation that hardcodes
    # WHICH record binds -- e.g. one returning (O2.T_min, CO2.T_max) directly. CO
    # is the non-binding record of the CO couple, so an implementation that does
    # not genuinely intersect all three will not follow it down.
    non_binding = dict(polys)
    non_binding["CO"] = narrowed("CO", 200.0, 2500.0)
    assert _effective_domain_K(non_binding, _COUPLE_RECORDS["CO"]) == (200.0, 2500.0), (
        "the ceiling must follow whichever record is lowest, not a remembered one"
    )
    # H2 does not read CO, so its window must be unmoved.
    assert _effective_domain_K(non_binding, _COUPLE_RECORDS["H2"]) == (200.0, 6000.0)


def test_refusal_names_a_temperature_that_round_trips(polys):
    """The refused value must not print as a value inside the interval.

    At a representational boundary a %g format rounds the temperature INTO the
    range it is being refused for: nextafter(20000.0, +inf) is 20000.000000000004
    and printed as "20000", so the message read "20000 K is outside [200, 20000]
    K" -- while the exact boundary 20000.0 really is evaluable. The message
    contradicted itself for precisely the reader who hit the edge.
    """

    just_over = math.nextafter(20000.0, math.inf)
    assert just_over != 20000.0

    # The exact boundary is evaluable, which is what makes the rounding a lie.
    assert math.isfinite(imposed_fo2({"CO": 1.0, "CO2": 1.0}, 20000.0, polys).log10_fO2)

    with pytest.raises(OffgasFO2Unavailable) as excinfo:
        imposed_fo2({"CO": 1.0, "CO2": 1.0}, just_over, polys)
    message = str(excinfo.value)
    assert repr(just_over) in message, message
    assert "20000 K is outside" not in message, message


def test_a_message_builder_survives_ordinary_hostility(polys):
    """_safe_repr must survive values engineered to break the message path.

    Named for what is actually promised. The contract is deliberately NOT "cannot
    raise": BaseException still propagates, because swallowing a KeyboardInterrupt
    to finish formatting an error message would be the worse bug. The previous
    name claimed the absolute and the code never delivered it.

    Error-construction code runs only on inputs nobody tests, which is exactly
    the input set a fail-closed guard is about. An earlier version guarded only
    repr(); len() and the slice sat outside the try, so a str subclass with a
    raising __len__ escaped past it as a foreign OverflowError -- reopening the
    hole the guard exists to close, inside the guard.
    """

    class EvilStr(str):
        def __len__(self):
            raise OverflowError("len overflow")

    class EvilRepr:
        def __float__(self):
            raise OverflowError("float overflow")

        def __repr__(self):
            return EvilStr("evil")

    with pytest.raises(OffgasFO2Unavailable):
        water_gas_shift_log10_K(polys, EvilRepr())
    with pytest.raises(OffgasFO2Unavailable):
        imposed_fo2({"CO": 1.0, "CO2": 1.0}, EvilRepr(), polys)


def test_both_couples_are_evaluated_at_the_same_temperature(polys):
    """A stateful temperature must not yield a cross-temperature subtraction.

    water_gas_shift_log10_K used to hand the caller's raw object to each couple
    evaluation separately. A float-like returning 1000 K then 2000 K produced
    log10 K_wgs = 6.675 -- an equilibrium constant at neither temperature, since
    the fixed-T values are 0.157 and -0.661. It is a subtraction across two
    different states, returned as a thermodynamic quantity.
    """

    class Drifting:
        def __init__(self, *values):
            self._values = list(values)

        def __float__(self):
            return float(self._values.pop(0) if len(self._values) > 1 else self._values[0])

    result = water_gas_shift_log10_K(polys, Drifting(1000.0, 2000.0))
    at_1000 = water_gas_shift_log10_K(polys, 1000.0)
    assert result == pytest.approx(at_1000, abs=1e-12), (
        "the first conversion must fix the temperature for both couples"
    )


def test_shipped_extract_windows_are_what_the_module_documents(polys):
    """Pin today's data separately from the computation that reads it.

    H2O is the binding record for the H2 couple and for the shift; the CO couple
    reaches far higher. The module's comments name these numbers, so they are
    asserted here rather than left to a reader to trust.
    """

    assert _effective_domain_K(polys, _COUPLE_RECORDS["H2"]) == (200.0, 6000.0)
    assert _effective_domain_K(polys, _COUPLE_RECORDS["CO"]) == (200.0, 20000.0)

    for T_K in (200.0, 6000.0):
        assert math.isfinite(water_gas_shift_log10_K(polys, T_K))

    for T_K in (math.nextafter(200.0, 0.0), math.nextafter(6000.0, math.inf)):
        with pytest.raises(OffgasFO2Unavailable, match="outside the shared domain"):
            water_gas_shift_log10_K(polys, T_K)


def test_a_co_only_gas_is_not_refused_for_a_constant_it_never_uses(polys):
    """Above H2O's ceiling the CO couple is still an evaluation, not a guess.

    K2 reads CO2/CO/O2, all of which span 200-20000 K on the shipped extract. A
    gas carrying only that couple has no use for K1, so refusing it at 7000 K
    would reject a computable answer -- and the refusal said "K1 and K2 there
    would be extrapolations", which is false of K2 by 14000 K.

    The temperature is far outside anything this furnace reaches; the point is
    the contract, not the operating point. What must NOT happen is refusing a
    quantity the records support, with a reason that is untrue.
    """

    result = imposed_fo2({"CO": 1.0, "CO2": 1.0}, 7000.0, polys)
    assert math.isfinite(result.log10_fO2)
    # 2 * (log10(1/1) - log10 K2), and log10 K2 at 7000 K is -2.16234846764.
    assert result.log10_fO2 == pytest.approx(2.0 * 2.16234846764, abs=1e-6)

    # The H2 couple genuinely IS out of domain there, and asking for it refuses.
    with pytest.raises(OffgasFO2Unavailable, match="outside the shared domain"):
        water_gas_shift_log10_K(polys, 7000.0)


def test_refusal_survives_a_hostile_exception_type_name(polys):
    """The CAUGHT exception is rendered too, and that lookup can also raise.

    type(exc).__name__ looks total and is not. The value-rendering hole was
    closed first, and this is the same shape one argument to the right: a float-
    like whose __float__ raises a ValueError subclass whose metaclass raises when
    __name__ is read. The conversion exception is caught, the VALUE renders
    safely, and the message then dies describing the exception.
    """

    class ExplodingName(type):
        @property
        def __name__(cls):  # noqa: N805
            raise RuntimeError("exception name exploded")

    class HostileError(ValueError, metaclass=ExplodingName):
        pass

    class Hostile:
        def __float__(self):
            raise HostileError("nope")

    for call in (
        lambda: water_gas_shift_log10_K(polys, Hostile()),
        lambda: imposed_fo2({"CO": 1.0, "CO2": 1.0}, Hostile(), polys),
    ):
        with pytest.raises(OffgasFO2Unavailable):
            call()


def test_the_callers_object_is_converted_once_per_successful_call(polys):
    """Pins the structural claim in _coerce_T_K's docstring, which no test held.

    Counted on the CALLER'S OBJECT, not on float() calls inside the module --
    the module coerces again per couple, idempotently and on purpose. A claim
    about code structure that nothing asserts is maintained by nothing; this
    module has carried five such claims that rotted or were false on the day.
    """

    class Counted:
        def __init__(self, value):
            self.value = value
            self.calls = 0

        def __float__(self):
            self.calls += 1
            return self.value

    for gas in (
        None,
        {"CO": 1.0, "CO2": 1.0},
        {"H2": 1.0, "H2O": 1.0, "CO": 1.0, "CO2": 1.0},
    ):
        obj = Counted(1500.0)
        if gas is None:
            water_gas_shift_log10_K(polys, obj)
        else:
            imposed_fo2(gas, obj, polys)
        assert obj.calls == 1, f"caller object converted {obj.calls}x for {gas}"


def test_a_hostile_returned_type_name_cannot_break_the_refusal(polys):
    """Guarding the LOOKUP is not enough; the returned object gets formatted.

    A metaclass may return from __name__ something that is not a plain str -- or a
    str subclass whose __format__ raises. That object was then interpolated at the
    call site, outside the helper's try, so the refusal died one frame further out
    than the guard reached. Distinct from the raising-__name__ case: there the
    lookup fails, here it succeeds and hands back a bomb.
    """

    class FormatBomb(str):
        def __format__(self, spec):
            raise RuntimeError("name formatting exploded")

    class ExplodingName(type):
        @property
        def __name__(cls):  # noqa: N805
            return FormatBomb("boom")

    class HostileError(ValueError, metaclass=ExplodingName):
        pass

    class Hostile:
        def __float__(self):
            raise HostileError("nope")

    with pytest.raises(OffgasFO2Unavailable):
        water_gas_shift_log10_K(polys, Hostile())
    with pytest.raises(OffgasFO2Unavailable):
        imposed_fo2({"CO": 1.0, "CO2": 1.0}, Hostile(), polys)


def test_any_ordinary_conversion_failure_refuses_in_type(polys):
    """A caller's __float__ may raise anything; all of it must refuse in-type.

    The guard once caught only (TypeError, ValueError, OverflowError). A
    __float__ raising an ordinary RuntimeError therefore leaked that RuntimeError
    from both public APIs -- the exact catch-disjointness the guard exists to
    remove, surviving in the guard's own except clause.
    """

    for exc_type in (RuntimeError, KeyError, ZeroDivisionError, AttributeError):

        class Raising:
            def __float__(self, _e=exc_type):
                raise _e("conversion exploded")

        with pytest.raises(OffgasFO2Unavailable):
            water_gas_shift_log10_K(polys, Raising())
        with pytest.raises(OffgasFO2Unavailable):
            imposed_fo2({"CO": 1.0, "CO2": 1.0}, Raising(), polys)
