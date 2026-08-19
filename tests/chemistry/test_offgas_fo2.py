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
    """Below ~1100 K the shift runs forward and the CO couple is more reducing.

    Above it, both reverse. They must flip together -- a sign disagreement would
    mean the extent solver and the fO2 inversion disagree about which way the
    reaction goes.
    """

    gas = {"H2": 2.0, "H2O": 0.35, "CO": 1.4, "CO2": 0.22}
    low = imposed_fo2(gas, 700.0, polys)
    high = imposed_fo2(gas, 1873.15, polys)

    assert low.raw_couple_disagreement_dex < 0.0
    assert low.extent_mol > 0.0
    assert high.raw_couple_disagreement_dex > 0.0
    assert high.extent_mol < 0.0


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
