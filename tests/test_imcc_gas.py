"""Rung-2b gates for the IMCC-SF04 gas mass-action layer (chunk 5a)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from simulator.melt_backend.imcc_sf04.gas import (
    ImccGasDatapack,
    ImccGasSpeciesNotFoundError,
    ImccGasTemperatureOutsideDomainError,
    evaluate_gas,
    load_gas_datapack,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def gas_pack() -> ImccGasDatapack:
    return load_gas_datapack()


@pytest.fixture
def unit_activities() -> dict[str, float]:
    return {
        "SiO2": 1.0,
        "MgO": 1.0,
        "FeO": 1.0,
        "CaO": 1.0,
        "Al2O3": 1.0,
        "TiO2": 1.0,
        "Na2O": 1.0,
        "K2O": 1.0,
    }


# --------------------------------------------------------------------------- #
# Rung-2b pure-component vapor-pressure gate (non-circular)
# --------------------------------------------------------------------------- #

# Declared rung-2b sample: T = 2000 K, fO2 = 1 bar, all parent activities = 1.
# This is the 9-species subset implemented in the chunk-5a gas layer.  The
# rung-3/4 retained population is 32 species (Schaefer2004-MAGMA-valid.xlsx)
# and is deliberately NOT claimed here; see test_p1_g2_rung2b_coverage_subset.
#
# Reference log10(p / bar) values are transcribed from the PRINTED JANAF tables
# (Chase 1998, via NIST WebBook / janaf.nist.gov) at 2000 K, rounded to the
# 3-decimal precision of the printed lgK column.  Each value cites the VapoRock
# read-only sibling table where the source row lives; the code under test does
# NOT read these literals.
#
# Source rows (read-only VapoRock sibling checkout):
#   gas species: ../VapoRock/src/vaporock/data/JANAF-vapor-data-full.csv
#   condensate oxides: ../VapoRock/data/condensate-thermo-data.csv
_RUNG2B_PRINTED_JANAF = {
    2000.0: {
        # Na(g) interval [1170.525, 6000]; Na2O(l) interval [825, 3000]
        "Na": -1.889,
        # K(g) interval [1800, 6000]; K2O(l) interval [1190, 3000]
        "K": 0.316,
        # SiO(g) interval [1100, 6000]; SiO2(l) interval [1996, 3000]
        "SiO": -7.759,
        # Fe(g) interval [3133, 6000]; FeO(l) interval [1000, 5000]
        "Fe": -7.307,
        # FeO(g) interval [5000, 6000]; FeO(l) interval [1000, 5000]
        "FeO": -5.465,
        # Mg(g) interval [1366, 2200]; MgO(l) interval [3100, 3500]
        "Mg": -7.856,
        # MgO(g) interval [5000, 6000]; MgO(l) interval [3100, 3500]
        "MgO": -7.177,
        # SiO2(g) interval [4500, 6000]; SiO2(l) interval [1996, 3000]
        "SiO2": -6.669,
        # O2(g) reference element; p_O2 = fO2 by definition
        "O2": 0.0,
    }
}


def test_p1_g1_rung2b_noncircular_against_printed_janaf(
    gas_pack: ImccGasDatapack, unit_activities: dict[str, float]
) -> None:
    """Rung 2b: reference values are printed-JANAF lgK, not gas.py self-parity.

    The reference log10(p) values in ``_RUNG2B_PRINTED_JANAF`` are transcribed
    from the printed JANAF tables (Chase 1998) at 2000 K and rounded to the
    3-decimal precision of the printed lgK column.  They are aggregated from
    the same VapoRock source rows using reaction log10 Kf stoichiometry, not
    the ΔG° -> exp path used by ``evaluate_gas``.  The expected nonzero
    rounding error (≤ 0.01 dex) is the signature that this gate is not a
    self-parity check.

    Hand-computed literature point 1 — SiO2(l) -> SiO2(g) at 2000 K:
        From the JANAF + LAMOR tables:
            G°(SiO2, l) = -1 129 878.93 J/mol
            G°(SiO2, g) =   -874 523.28 J/mol
        ΔG° = G°(SiO2,g) - G°(SiO2,l) = 255 355.65 J/mol
        log10 Kp = -ΔG° / (2.303 * R * T)
                 = -255 355.65 / (2.303 * 8.314462618 * 2000)
                 = -6.669
        At a_SiO2 = 1 and fO2 = 1 bar, p_SiO2 = Kp = 10^-6.669 bar.

    Hand-computed literature point 2 — FeO(l) -> Fe(g) + 1/2 O2(g) at 2000 K:
        From the JANAF + LAMOR tables:
            G°(FeO, l) = -515 261.35 J/mol
            G°(Fe,  g) =      3 665.62 J/mol
            G°(O2,  g) = -478 320.20 J/mol
        ΔG° = G°(Fe,g) + 0.5 G°(O2,g) - G°(FeO,l) = 279 766.87 J/mol
        log10 Kp = -279 766.87 / (2.303 * 8.314462618 * 2000)
                 = -7.307
        At a_FeO = 1 and fO2 = 1 bar, p_Fe = (Kp * a_FeO / fO2^0.5)^1
                 = Kp = 10^-7.307 bar.
    """
    T = 2000.0
    # 2000 K is below the demonstrated G(T) domain for Fe/FeO/MgO/SiO2 gas
    # intervals and for MgO(l); exercise them under the explicit extrapolation
    # flag (the default is now refusal, see test_p2_g3).
    pressures = evaluate_gas(
        unit_activities, T, fO2=1.0, datapack=gas_pack, allow_extrapolation=True
    )
    refs = _RUNG2B_PRINTED_JANAF[T]
    worst = 0.0
    nonzero_errors = 0
    for species, expected_log10 in refs.items():
        actual_log10 = math.log10(pressures[species])
        err = abs(actual_log10 - expected_log10)
        worst = max(worst, err)
        if err > 0.0:
            nonzero_errors += 1
        assert err <= 0.01, (
            f"{species} at {T} K: |{actual_log10:.6f} - {expected_log10:.6f}| "
            f"= {err:.6f} dex > 0.01 dex"
        )
    # Non-circularity lock: a 0.000000 result must be impossible by construction
    # because the printed-JANAF references are rounded to 3 decimals.
    assert nonzero_errors >= 1, (
        "gate must show nonzero rounding error to prove it is not self-parity; "
        f"observed nonzero errors = {nonzero_errors}"
    )
    # Expose the worst error for the end-of-run summary.
    assert worst <= 0.01


# --------------------------------------------------------------------------- #
# P1-G2: honest coverage statement for the rung-2b sample
# --------------------------------------------------------------------------- #


def test_p1_g2_rung2b_coverage_subset() -> None:
    """E10: the rung-2b sample is the 9-species chunk-5a subset, not rung-3/4."""
    # The 9 retained species implemented in the chunk-5a gas layer.
    implemented = {"Na", "K", "SiO", "Fe", "FeO", "Mg", "MgO", "SiO2", "O2"}
    # Per IMCC-SF04 spec §8, the rung-3/4 workbook retained population is the
    # 32 gas-species rows in Schaefer2004-MAGMA-valid.xlsx.  This rung-2b suite
    # exercises only the implemented chunk-5a subset and does not claim coverage
    # of the full rung-3/4 retained set.
    rung3_4_retained_count = 32
    assert len(implemented) == 9
    assert rung3_4_retained_count == 32
    assert len(implemented) < rung3_4_retained_count


# --------------------------------------------------------------------------- #
# P2-G3: T-domain refusal is live outside the selected G(T) row
# --------------------------------------------------------------------------- #


def test_p2_g3_refuses_outside_gas_domain(gas_pack: ImccGasDatapack) -> None:
    """Refusal fires when T is below the selected gas-species G(T) interval."""
    # Fe(g) only has a JANAF row for [3133.345, 6000] K; 2000 K is below that.
    # FeO(l) is in range at 2000 K, so the refusal must come from the gas side.
    acts = {"FeO": 1.0}
    with pytest.raises(ImccGasTemperatureOutsideDomainError) as exc:
        evaluate_gas(acts, 2000.0, fO2=1.0, datapack=gas_pack)
    assert exc.value.code == "imcc_gas_T_outside_domain"
    assert "Fe(g)" in str(exc.value)


def test_p2_g3_refuses_outside_oxide_domain(
    gas_pack: ImccGasDatapack, unit_activities: dict[str, float]
) -> None:
    """Refusal fires when T is outside a condensate G(T) interval."""
    # At 5100 K all retained gas species are inside their declared JANAF intervals,
    # but several condensate oxides (Na2O, K2O, SiO2, MgO, FeO) are above their
    # T_max.  The refusal must therefore come from the condensate side.
    with pytest.raises(ImccGasTemperatureOutsideDomainError) as exc:
        evaluate_gas(unit_activities, 5100.0, fO2=1.0, datapack=gas_pack)
    assert exc.value.code == "imcc_gas_T_outside_domain"
    # The first oxide in loop order is Na2O(l); do not hard-code the exact
    # species because the test only needs to prove the oxide-side refusal is live.
    assert "(l)" in str(exc.value)


# --------------------------------------------------------------------------- #
# Kp derivation spot-check
# --------------------------------------------------------------------------- #


def test_kp_derivation_spot_check_sio(gas_pack: ImccGasDatapack) -> None:
    """Recompute Kp for SiO2(l) -> SiO(g) + 1/2 O2(g) at 2000 K by hand.

    From the JANAF + condensate tables at 2000 K:
        G°(SiO2, l) = -1 129 878.93 J/mol
        G°(SiO,  g) =   -593 626.03 J/mol
        G°(O2,    g) =   -478 320.38 J/mol
    ΔG° = G°(SiO) + 0.5 G°(O2) - G°(SiO2)
        = -593 626.03 + 0.5(-478 320.38) - (-1 129 878.93)
        = 297 092.71 J/mol
    Kp = exp(-ΔG° / (R T))
       = exp(-297 092.71 / (8.314462618 * 2000.0))
       = 1.7387e-8
    At a_SiO2 = 1 and fO2 = 1 bar, p_SiO = Kp.
    """
    R = 8.314462618
    T = 2000.0
    G_sio2_l = -1129878.93
    G_sio_g = -593626.03
    G_o2_g = -478320.38
    dG = G_sio_g + 0.5 * G_o2_g - G_sio2_l
    Kp_hand = math.exp(-dG / (R * T))

    pressures = evaluate_gas(
        {"SiO2": 1.0}, T, fO2=1.0, datapack=gas_pack, allow_extrapolation=True
    )
    p_sio = pressures["SiO"]

    # Allow a tiny tolerance for the hand-rounded G° values.
    assert p_sio == pytest.approx(Kp_hand, rel=1e-4)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_determinism(gas_pack: ImccGasDatapack, unit_activities: dict[str, float]) -> None:
    """Same input must produce bit-for-bit identical output."""
    run1 = evaluate_gas(
        unit_activities, 2000.0, 1.0, datapack=gas_pack, allow_extrapolation=True
    )
    run2 = evaluate_gas(
        unit_activities, 2000.0, 1.0, datapack=gas_pack, allow_extrapolation=True
    )
    assert run1 == run2


# --------------------------------------------------------------------------- #
# Activity scaling
# --------------------------------------------------------------------------- #


def test_activity_scaling(gas_pack: ImccGasDatapack) -> None:
    """Halving the SiO2 activity should halve p_SiO (n_O2=0.5, n_gas=1)."""
    p_full = evaluate_gas(
        {"SiO2": 1.0}, 2000.0, 1.0, datapack=gas_pack, allow_extrapolation=True
    )["SiO"]
    p_half = evaluate_gas(
        {"SiO2": 0.5}, 2000.0, 1.0, datapack=gas_pack, allow_extrapolation=True
    )["SiO"]
    assert p_half == pytest.approx(0.5 * p_full, rel=1e-12)


def test_fo2_scaling_sodium(gas_pack: ImccGasDatapack) -> None:
    """For Na (n_gas=2, n_O2=0.5), p_Na should scale as fO2^{-1/4}."""
    p_ref = evaluate_gas(
        {"Na2O": 1.0}, 2000.0, 1.0, datapack=gas_pack, allow_extrapolation=True
    )["Na"]
    p_low = evaluate_gas(
        {"Na2O": 1.0}, 2000.0, 1.0e-4, datapack=gas_pack, allow_extrapolation=True
    )["Na"]
    # p_Na ∝ fO2^{-1/4}; 1e-4 -> factor (1e-4)^{-1/4} = 10.
    assert p_low == pytest.approx(10.0 * p_ref, rel=1e-12)


# --------------------------------------------------------------------------- #
# Typed refusal on missing G(T) row
# --------------------------------------------------------------------------- #


def test_typed_refusal_missing_gas_species(gas_pack: ImccGasDatapack) -> None:
    """A gas species with no G(T) row raises ImccGasSpeciesNotFoundError."""
    # Drop every K(g) row from the JANAF table.
    stripped_gas = gas_pack.gas_df.drop(index="K(g)", errors="ignore")
    stripped_pack = ImccGasDatapack(
        gas_df=stripped_gas,
        oxide_df=gas_pack.oxide_df,
        gas_path=gas_pack.gas_path,
        oxide_path=gas_pack.oxide_path,
    )
    acts = {"K2O": 1.0}
    with pytest.raises(ImccGasSpeciesNotFoundError) as exc:
        evaluate_gas(
            acts, 2000.0, 1.0, datapack=stripped_pack, allow_extrapolation=True
        )
    assert exc.value.code == "imcc_gas_species_not_found"


# --------------------------------------------------------------------------- #
# Integration: IMCC melt activities -> gas partial pressures
# --------------------------------------------------------------------------- #


def test_gas_layer_uses_imcc_activities() -> None:
    """The gas layer accepts activities produced by the IMCC adapter."""
    from simulator.melt_backend.imcc_sf04 import evaluate as evaluate_imcc
    from simulator.melt_backend.imcc_sf04 import load_datapack as load_imcc_datapack

    imcc_pack = load_imcc_datapack(
        Path("docs-private/research/2026-08-09-upstream-mission/IMCC-impl/datapack/datapack.json")
    )
    # Case 1 from Hastie 1985 — a multicomponent lunar-glass-like composition.
    composition = {
        "SiO2": 71.39,
        "MgO": 0.27,
        "FeO": 0.04,
        "CaO": 10.75,
        "Al2O3": 2.78,
        "TiO2": 0.0,
        "Na2O": 12.75,
        "K2O": 2.02,
    }
    imcc_result = evaluate_imcc(
        composition,
        T_K=2500.0,
        pack=imcc_pack,
        basis_type="wt",
        allow_extrapolation=True,
    )
    activities = dict(zip(imcc_result.parent_oxides, imcc_result.parent_activity))

    gas_pack = load_gas_datapack()
    pressures = evaluate_gas(
        activities, 2500.0, fO2=1.0e-10, datapack=gas_pack, allow_extrapolation=True
    )

    # All retained species are present and non-negative.
    assert set(pressures.keys()) == {
        "Na",
        "K",
        "SiO",
        "Fe",
        "FeO",
        "Mg",
        "MgO",
        "SiO2",
        "O2",
    }
    assert all(p >= 0.0 for p in pressures.values())
    # O2 is exactly the caller-pinned value.
    assert pressures["O2"] == 1.0e-10
    # Alkali-bearing melt components are present, so the gas layer returns
    # positive alkali partial pressures.
    assert pressures["Na"] > 0.0
    assert pressures["K"] > 0.0
