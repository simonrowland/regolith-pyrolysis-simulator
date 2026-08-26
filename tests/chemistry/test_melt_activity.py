"""Fail-closed and prose-contract tests for simulator.chemistry.melt_activity."""

from __future__ import annotations

import math

import pytest

from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_COEFFICIENTS,
    MELT_OXIDE_ACTIVITY_LIMITATION,
    P2O5_ACTIVITY_COEFFICIENT,
    melt_oxide_activity,
    melt_oxide_gamma_domain_authority,
    na_reductant_activity_shift_kj_per_mol_o2,
    single_cation_mole_fractions,
    table_gamma_effective,
)


# Sossi & Fegley 2018 Table 2 printed envelopes, as transcribed in
# data/literature/extracts/kems-041-sossi-fegley-2018.yaml and
# tests/fixtures/corpus/sossi-fegley-2018-volatility/benchmark-fixture.yaml.
_TABLE2_RANGE = {
    "CaO": (0.001, 0.15),
    "Al2O3": (0.28, 0.37),
    "SiO2": (0.9, 1.1),
    "TiO2": (1.5, 1.7),
    "Cr2O3": (23.0, 42.0),
    "MgO": (0.25, 4.0),
    "MnO": (0.5, 7.2),  # CAS row (Ohta & Suito 1995), not FCMS(P)
}


def test_limitation_does_not_claim_cited_point_or_universal_domain_status() -> None:
    text = MELT_OXIDE_ACTIVITY_LIMITATION
    assert "cited table coefficient" not in text
    assert "geometric midpoint" in text
    assert "valid_range_K is unset" in text
    assert "status-bearing" in text


def test_table2_range_rows_remain_geometric_midpoints_without_domains() -> None:
    for parent, (low, high) in _TABLE2_RANGE.items():
        coeff = MELT_OXIDE_ACTIVITY_COEFFICIENTS[parent]
        geometric = math.sqrt(low * high)
        assert coeff.gamma == pytest.approx(geometric, rel=0.03)
        assert coeff.valid_range_K is None
        assert "geometric midpoint" in coeff.citation
        assert "DOI" in coeff.citation


def test_na_and_k_remain_cited_point_anchors_with_domains() -> None:
    na = MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"]
    k = MELT_OXIDE_ACTIVITY_COEFFICIENTS["K2O"]
    assert na.gamma == pytest.approx(1.0e-3)
    assert k.gamma == pytest.approx(3.5e-5)
    assert na.valid_range_K == (1673.0, 1673.0)
    assert k.valid_range_K == (1500.0, 1500.0)
    assert P2O5_ACTIVITY_COEFFICIENT.valid_range_K == (1823.0, 1923.0)


def test_lunar_ca_mid_range_activity_is_unchanged() -> None:
    activity = melt_oxide_activity(
        "CaO", {}, cation_mol_fraction={"CaO": 0.1156}
    )
    assert activity is not None
    assert activity.effective_gamma == pytest.approx(0.012)
    assert activity.activity == pytest.approx(0.012 * 0.1156)


@pytest.mark.parametrize("temperature_K", [float("nan"), float("inf"), 0.0, -1.0])
def test_gamma_domain_authority_refuses_nonphysical_temperature(temperature_K):
    with pytest.raises(ValueError, match="temperature_K must be finite and positive"):
        melt_oxide_gamma_domain_authority("Na2O", temperature_K)


@pytest.mark.parametrize("gamma", [float("nan"), float("inf"), 0.0, -1.0])
def test_gamma_domain_authority_refuses_nonphysical_gamma(gamma):
    with pytest.raises(ValueError, match="gamma must be finite and positive"):
        melt_oxide_gamma_domain_authority("Na2O", 1673.0, gamma=gamma)


def test_gamma_domain_authority_in_domain_anchor_still_labels_na() -> None:
    payload = melt_oxide_gamma_domain_authority("Na2O", 1673.0)
    assert payload is not None
    assert payload["authority_status"] == "in_domain"
    assert payload["gamma"] == pytest.approx(1.0e-3)
    assert melt_oxide_gamma_domain_authority("CaO", 1873.0) is None


@pytest.mark.parametrize("x_value", [-0.1, float("nan"), float("inf"), 1.5])
def test_melt_oxide_activity_refuses_degenerate_supplied_mole_fraction(x_value):
    with pytest.raises(ValueError, match="must be finite and within \\[0, 1\\]"):
        melt_oxide_activity(
            "Na2O",
            {},
            cation_mol_fraction={"Na2O": x_value},
            temperature_K=1673.0,
        )


def test_zero_supplied_mole_fraction_remains_zero_activity() -> None:
    activity = melt_oxide_activity(
        "Na2O",
        {},
        cation_mol_fraction={"Na2O": 0.0},
        temperature_K=1673.0,
    )
    assert activity is not None
    assert activity.x_single_cation == 0.0
    assert activity.activity == 0.0
    assert activity.authority_status == "in_gamma_domain"


def test_na_reductant_shift_none_path_stays_table_gamma() -> None:
    temperature_K = 1500.0
    gamma = MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"].gamma
    expected = 4.0 * 8.31446261815324e-3 * temperature_K * math.log(gamma)
    assert na_reductant_activity_shift_kj_per_mol_o2(temperature_K) == pytest.approx(
        expected
    )
    assert expected == pytest.approx(-344.60563825468614)


def test_na_reductant_shift_na_free_melt_is_negative_inf() -> None:
    assert na_reductant_activity_shift_kj_per_mol_o2(
        1500.0, {"SiO2": 1.0}
    ) == float("-inf")


@pytest.mark.parametrize("account_mol", [{}, {"unknown": 1.0}])
def test_na_reductant_shift_refuses_unresolved_explicit_inventory(account_mol):
    with pytest.raises(ValueError, match="did not resolve a Na2O activity"):
        na_reductant_activity_shift_kj_per_mol_o2(1500.0, account_mol)


def test_single_cation_overflow_refuses_nonfinite_projection() -> None:
    with pytest.raises(ValueError, match="overflowed"):
        single_cation_mole_fractions({"Al2O3": 1e308})
    with pytest.raises(ValueError, match="overflowed"):
        single_cation_mole_fractions({"SiO2": 1e308, "Al2O3": 1e308})


def test_single_cation_finite_inventory_still_normalizes() -> None:
    fractions = single_cation_mole_fractions({"SiO2": 1.0, "Al2O3": 1.0})
    assert fractions["SiO2"] == pytest.approx(1.0 / 3.0)
    assert fractions["Al2O3"] == pytest.approx(2.0 / 3.0)


def test_signed_dust_floor_is_unchanged() -> None:
    assert single_cation_mole_fractions({"Cr2O3": -1.76e-15}) == {}
    assert single_cation_mole_fractions({"Cr2O3": -1.0e-12}) == {}
    with pytest.raises(ValueError, match="non-negative"):
        single_cation_mole_fractions({"Cr2O3": -1.0001e-12})


def test_blend_start_seam_is_still_the_shipped_0_99_cutoff() -> None:
    gamma = 31.1
    assert table_gamma_effective(gamma, 0.99) == pytest.approx(31.1)
    assert table_gamma_effective(gamma, 0.99, blend_start=0.99) == pytest.approx(31.1)
    near_unity = table_gamma_effective(gamma, 1.0 - 1.0e-9)
    assert near_unity * (1.0 - 1.0e-9) == pytest.approx(1.0, abs=2.0e-9)
