"""Fail-closed and prose-contract tests for simulator.chemistry.melt_activity."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_COEFFICIENTS,
    MELT_OXIDE_ACTIVITY_LIMITATION,
    MELT_OXIDE_ACTIVITY_REFERENCE_STATE,
    MELT_OXIDE_CRYSTALLINE_ACTIVITY_REFERENCE_STATE,
    P2O5_ACTIVITY_COEFFICIENT,
    crystalline_vapor_activity_standard_state_matches,
    melt_oxide_activity,
    melt_oxide_gamma_domain_authority,
    na_reductant_activity_shift_kj_per_mol_o2,
    single_cation_mole_fractions,
    table_gamma_effective,
)
from simulator.battery.generators.janaf import janaf_fusion_energy
from simulator.reference_data.janaf_fusion import (
    FusionDomainAuthority,
    _d144_fusion_conversion,
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


def test_crystalline_vapor_activity_guard_requires_the_crystalline_id() -> None:
    assert crystalline_vapor_activity_standard_state_matches(
        "MgO", MELT_OXIDE_CRYSTALLINE_ACTIVITY_REFERENCE_STATE
    )
    assert not crystalline_vapor_activity_standard_state_matches(
        "CaO", MELT_OXIDE_ACTIVITY_REFERENCE_STATE
    )
    assert not crystalline_vapor_activity_standard_state_matches("MgO", None)
    assert crystalline_vapor_activity_standard_state_matches("SiO2", None)


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


@pytest.mark.parametrize(
    ("parent", "temperature_K"),
    [
        ("CaO", 1873.0),
        ("CaO", 1973.0),
        ("MgO", 1873.0),
        ("MgO", 1973.0),
    ],
)
def test_solid_reference_gamma_converts_to_liquid_janaf_reference(
    parent: str, temperature_K: float
) -> None:
    gamma_s = MELT_OXIDE_ACTIVITY_COEFFICIENTS[parent].gamma
    delta_g_fus_kJ_per_mol = float(
        janaf_fusion_energy(parent, Decimal(str(temperature_K))).delta_g_fus_kJ_per_mol
    )
    expected_gamma_l = gamma_s * math.exp(
        -delta_g_fus_kJ_per_mol / (8.31446261815324e-3 * temperature_K)
    )
    activity = melt_oxide_activity(
        parent,
        {parent: 1.0, "SiO2": 4.0},
        temperature_K=temperature_K,
    )
    assert activity is not None
    assert activity.gamma == pytest.approx(expected_gamma_l)
    assert activity.effective_gamma == pytest.approx(expected_gamma_l)
    assert activity.activity == pytest.approx(expected_gamma_l * 0.2)


def test_solid_reference_gamma_predicts_above_janaf_range() -> None:
    activity = melt_oxide_activity(
        "MgO",
        {"MgO": 1.0, "SiO2": 4.0},
        temperature_K=4500.0,
    )

    assert activity is not None
    assert activity.activity > 0.0
    assert activity.warning is not None
    assert "terminal Gibbs chord" in activity.warning
    assert "authority=extrapolated" in activity.warning
    assert "certified_band_K=[298.15, 4000]" in activity.warning
    assert activity.provenance()["melt_oxide_activity_warning"] == activity.warning


def test_solid_reference_gamma_flags_janaf_metastable_liquid_below_fusion() -> None:
    temperature_K = 418.15
    activity = melt_oxide_activity(
        "MgO",
        {"MgO": 1.0, "SiO2": 4.0},
        temperature_K=temperature_K,
    )

    assert activity is not None
    assert activity.warning is not None
    assert "metastable (supercooled) liquid" in activity.warning
    assert "authority=bridge" in activity.warning
    assert "certified_band_K=[298.15, 4000]" in activity.warning
    expected_delta_g = janaf_fusion_energy(
        "MgO", Decimal("418.15")
    ).delta_g_fus_kJ_per_mol
    expected_gamma = MELT_OXIDE_ACTIVITY_COEFFICIENTS["MgO"].gamma * math.exp(
        -float(expected_delta_g) / (8.31446261815324e-3 * temperature_K)
    )
    assert activity.gamma == pytest.approx(expected_gamma)


def test_solid_reference_gamma_has_no_notice_above_janaf_fusion_temperature() -> None:
    activity = melt_oxide_activity(
        "MgO",
        {"MgO": 1.0, "SiO2": 4.0},
        temperature_K=3500.0,
    )

    assert activity is not None
    assert activity.warning is None


def test_d144_fusion_owner_returns_typed_domain_verdict() -> None:
    conversion = _d144_fusion_conversion("MgO", Decimal("418.15"))

    assert conversion.domain_verdict.authority is FusionDomainAuthority.BRIDGE
    assert conversion.domain_verdict.certified_band_K == (
        Decimal("298.15"),
        Decimal("4000.0"),
    )
    assert "metastable (supercooled) liquid" in conversion.domain_verdict.reason


@pytest.mark.parametrize("parent", ["CaO", "MgO"])
def test_solid_reference_oxide_activity_requires_temperature(parent: str) -> None:
    with pytest.raises(ValueError, match="temperature_K required"):
        melt_oxide_activity(parent, {parent: 1.0, "SiO2": 4.0})


@pytest.mark.parametrize("parent", ["Al2O3", "SiO2", "TiO2"])
def test_liquid_reference_oxide_gammas_are_unchanged(parent: str) -> None:
    gamma = MELT_OXIDE_ACTIVITY_COEFFICIENTS[parent].gamma
    activity = melt_oxide_activity(
        parent,
        {parent: 1.0, "SiO2": 4.0},
        temperature_K=1873.0,
    )
    assert activity is not None
    assert activity.gamma == pytest.approx(gamma)
    assert activity.effective_gamma == pytest.approx(gamma)


@pytest.mark.parametrize(
    ("parent", "temperature_K", "expected_notice"),
    [
        (
            "MnO",
            1873.0,
            "gamma_reference_state_solid_unconverted (reason: JANAF manifest "
            "lists only elemental Mn-001 through Mn-006; no MnO(cr)/MnO(l) "
            "fusion chord (data/literature/compilations/janaf/manifest.yaml:"
            "28177-28444))",
        ),
        ("Al2O3", 1873.0, "gamma_reference_state_unverified"),
        ("SiO2", 1873.0, "gamma_reference_state_unverified"),
        ("TiO2", 1873.0, "gamma_reference_state_unverified"),
        ("P2O5", 1873.0, "gamma_reference_state_unverified"),
        ("K2O", 1500.0, "gamma_reference_state_unprinted"),
        ("Cr2O3", 1773.0, None),
        ("Na2O", 1673.0, None),
    ],
)
def test_reference_state_notice_comes_from_primary_evidence(
    parent: str,
    temperature_K: float,
    expected_notice: str | None,
) -> None:
    gamma = (
        P2O5_ACTIVITY_COEFFICIENT.gamma
        if parent == "P2O5"
        else MELT_OXIDE_ACTIVITY_COEFFICIENTS[parent].gamma
    )
    activity = melt_oxide_activity(
        parent,
        {parent: 1.0, "SiO2": 4.0},
        temperature_K=temperature_K,
    )
    assert activity is not None
    assert activity.gamma == pytest.approx(gamma)
    assert activity.provenance()["melt_oxide_activity_reference_state"] == (
        MELT_OXIDE_ACTIVITY_REFERENCE_STATE
    )
    provenance = activity.provenance()
    assert activity.warning is None
    assert "melt_oxide_activity_warning" not in provenance
    if expected_notice is None:
        assert "melt_oxide_activity_reference_state_notice" not in provenance
    else:
        assert provenance["melt_oxide_activity_reference_state_notice"] == (
            expected_notice
        )


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
