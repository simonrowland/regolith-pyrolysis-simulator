from __future__ import annotations

import math
import sys

import pytest

from engines.builtin._common import composition_wt_pct_from_account_view
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.structural_activity import (
    NBO_T_ORTHOSILICATE_CEILING,
    estimate_liquidus_flag,
    normalize_formula_unit_moles,
    reference_activity_coefficients,
    structural_activity_diagnostic,
    structural_activity_features,
    structural_gamma_domain_verdict,
)
from simulator.chemistry.melt_activity import (
    melt_oxide_activity,
    na_reductant_activity_shift_kj_per_mol_o2,
    single_cation_mole_fractions,
)
from simulator.state import MOLAR_MASS


_LUNAR_12022_WT_PCT = {
    "SiO2": 44.5,
    "TiO2": 1.5,
    "Al2O3": 13.5,
    "FeO": 16.5,
    "MgO": 9.0,
    "CaO": 11.0,
    "Na2O": 0.4,
    "K2O": 0.10,
    "MnO": 0.20,
    "P2O5": 0.10,
    "Cr2O3": 0.35,
}


def _mol_from_wt_pct(wt_pct: dict[str, float]) -> dict[str, float]:
    return {
        oxide: wt / MOLAR_MASS[oxide]
        for oxide, wt in wt_pct.items()
        if wt > 0.0
    }


def test_structural_features_for_textbook_silica_and_disilicate() -> None:
    silica = structural_activity_features({"SiO2": 1.0})
    assert silica.nbo_t == pytest.approx(0.0)
    assert silica.optical_basicity == pytest.approx(0.48)

    sodium_disilicate = structural_activity_features({"Na2O": 1.0, "SiO2": 2.0})
    assert sodium_disilicate.nbo_t == pytest.approx(1.0)
    assert sodium_disilicate.optical_basicity == pytest.approx(
        (1.0 * 1.15 + 4.0 * 0.48) / 5.0
    )


def test_structural_features_for_lunar_12022_proxy() -> None:
    features = structural_activity_features(_mol_from_wt_pct(_LUNAR_12022_WT_PCT))

    assert features.nbo_t == pytest.approx(1.1439, abs=5e-4)
    assert features.optical_basicity == pytest.approx(0.6148, abs=5e-4)
    assert features.charge_balanced_al_mol > 0.0


def test_reference_gamma_na_reproduces_demaria_seed_anchors() -> None:
    features = structural_activity_features(_mol_from_wt_pct(_LUNAR_12022_WT_PCT))
    gamma_1300 = reference_activity_coefficients(
        nbo_t=features.nbo_t,
        optical_basicity=features.optical_basicity,
        temperature_K=1300.0,
    )
    gamma_1500 = reference_activity_coefficients(
        nbo_t=features.nbo_t,
        optical_basicity=features.optical_basicity,
        temperature_K=1500.0,
    )

    assert gamma_1300["NaO0.5"] == pytest.approx(1.8e-4, rel=1e-12)
    assert gamma_1500["NaO0.5"] == pytest.approx(4.5e-3, rel=1e-12)
    # K anchors from the same primary (Sossi & Fegley 2018 OCR ~line 350,
    # Fig. 5): gamma_KO0.5 = 3.5e-5 @1500 K, 7.2e-5 @1300 K — gamma RISES on
    # cooling (opposite sign to Na). Guards the 2026-07-05 correction of the
    # provisional 6.0e-3 K anchor (~170x high vs the primary).
    assert gamma_1500["KO0.5"] == pytest.approx(3.5e-5, rel=1e-12)
    assert gamma_1300["KO0.5"] == pytest.approx(7.2e-5, rel=1e-12)


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), -1.0])
def test_single_cation_basis_refuses_corrupt_known_oxide_inventory(amount):
    with pytest.raises(ValueError, match="must be finite and non-negative"):
        single_cation_mole_fractions({"SiO2": 1.0, "Na2O": amount})


@pytest.mark.parametrize(
    "temperature_K", [float("nan"), float("inf"), -1.0, 0.0]
)
def test_na_activity_shift_refuses_invalid_temperature(temperature_K):
    with pytest.raises(ValueError, match="temperature_K"):
        na_reductant_activity_shift_kj_per_mol_o2(temperature_K)


def test_orthosilicate_nbo_t_is_four_and_in_domain() -> None:
    """Ca2SiO4: O = 4T so NBO/T = 4, inclusive in-domain bound."""

    features = structural_activity_features({"CaO": 2.0, "SiO2": 1.0})
    assert features.nbo_t == pytest.approx(4.0)
    status, reason = structural_gamma_domain_verdict(
        features.nbo_t, features.optical_basicity
    )
    assert status == "ok"
    assert reason == ""


def test_past_orthosilicate_is_typed_dilute_network_former_refusal() -> None:
    """One extra CaO past Ca2SiO4 crosses the orthosilicate ceiling."""

    features = structural_activity_features({"CaO": 2.01, "SiO2": 1.0})
    assert features.nbo_t is not None
    assert features.nbo_t > NBO_T_ORTHOSILICATE_CEILING
    status, reason = structural_gamma_domain_verdict(
        features.nbo_t, features.optical_basicity
    )
    assert status == "out_of_domain"
    assert reason.startswith("dilute_network_former_out_of_domain")
    assert "OverflowError" not in reason
    gamma = reference_activity_coefficients(
        nbo_t=features.nbo_t,
        optical_basicity=features.optical_basicity,
        temperature_K=1673.15,
    )
    assert gamma == {}


def test_cao_sio2_2wt_pct_silica_is_out_of_structural_gamma_domain() -> None:
    """The t-717 crash composition: NBO/T ~ 105, KO0.5 10**x would overflow."""

    features = structural_activity_features(_mol_from_wt_pct({"SiO2": 2.0, "CaO": 98.0}))
    assert features.nbo_t is not None
    assert features.nbo_t > 100.0
    # Unconstrained KO0.5 log-linear term at this NBO/T overflows a C double.
    log10_gamma_k = math.log10(3.5e-5)
    log10_gamma_k += math.log10(3.5e-5 / 7.2e-5) / 200.0 * (1673.15 - 1500.0)
    log10_gamma_k += 4.5 * (
        features.optical_basicity - 0.6148157641396143
    )
    log10_gamma_k += 3.0 * (features.nbo_t - 1.143864967345075)
    assert log10_gamma_k > math.log10(sys.float_info.max)
    diagnostic = structural_activity_diagnostic(
        _mol_from_wt_pct({"SiO2": 2.0, "CaO": 98.0}),
        temperature_K=1673.15,
    )
    assert diagnostic["reference_gamma_status"] == "out_of_domain"
    assert diagnostic["reference_gamma_reason"].startswith(
        "dilute_network_former_out_of_domain"
    )
    assert "OverflowError" not in diagnostic["reference_gamma_reason"]
    assert diagnostic["reference_gamma_MOx"] == {}


def test_reference_gamma_nbo_t_slope_hand_check() -> None:
    """At T=1500 K and Lambda*, dNBO/T = -0.5 is a round-trip of the Na slope.

    log10(gamma) = log10(4.5e-3) + 2.8 * (-0.5); 10**x is inside (1e-12, 1)
    so the display envelope is the identity.
    """

    features = structural_activity_features(_mol_from_wt_pct(_LUNAR_12022_WT_PCT))
    delta = -0.5
    gamma = reference_activity_coefficients(
        nbo_t=features.nbo_t + delta,
        optical_basicity=features.optical_basicity,
        temperature_K=1500.0,
    )
    expected = 4.5e-3 * 10.0 ** (2.8 * delta)
    assert 1.0e-12 < expected < 1.0
    assert gamma["NaO0.5"] == pytest.approx(expected, rel=1e-12)


def test_reference_gamma_applies_lambda_and_nbo_t_product() -> None:
    """Both structural terms are added in log space on this function's path."""

    features = structural_activity_features(_mol_from_wt_pct(_LUNAR_12022_WT_PCT))
    d_lambda = 0.05
    d_nbo_t = 0.5
    gamma = reference_activity_coefficients(
        nbo_t=features.nbo_t + d_nbo_t,
        optical_basicity=features.optical_basicity + d_lambda,
        temperature_K=1500.0,
    )
    expected_factor = 10.0 ** (4.5 * d_lambda + 2.8 * d_nbo_t)
    assert gamma["NaO0.5"] / 4.5e-3 == pytest.approx(expected_factor, rel=1e-12)
    assert gamma["NaO0.5"] / 4.5e-3 == pytest.approx(
        10.0 ** (4.5 * d_lambda) * 10.0 ** (2.8 * d_nbo_t),
        rel=1e-12,
    )


def test_mgo_display_cap_still_maps_1600k_raw_above_unity_to_one() -> None:
    features = structural_activity_features(_mol_from_wt_pct(_LUNAR_12022_WT_PCT))
    raw = 10.0 ** (5.0e-4 * 100.0)
    assert raw == pytest.approx(1.1220184543019633)
    gamma = reference_activity_coefficients(
        nbo_t=features.nbo_t,
        optical_basicity=features.optical_basicity,
        temperature_K=1600.0,
    )
    assert gamma["MgO"] == 1.0


def test_absent_network_former_is_typed_dilute_refusal() -> None:
    features = structural_activity_features({"CaO": 1.0})
    assert features.nbo_t is None
    status, reason = structural_gamma_domain_verdict(
        features.nbo_t, features.optical_basicity
    )
    assert status == "out_of_domain"
    assert reason.startswith("dilute_network_former_out_of_domain")
    assert "OverflowError" not in reason


def test_liquidus_flag_trips_for_demaria_12022_sub_liquidus_case() -> None:
    diagnostic = structural_activity_diagnostic(
        _mol_from_wt_pct(_LUNAR_12022_WT_PCT),
        temperature_K=1429.0,
    )

    assert diagnostic["liquidus"]["estimated_liquidus_K"] == pytest.approx(
        1573.0,
        abs=1.0,
    )
    assert diagnostic["liquidus"]["sub_liquidus"] is True


def test_builtin_vapor_pressure_exposes_structural_reference_diagnostic_only(
    vapor_pressure_data,
) -> None:
    account_mol = {
        "SiO2": 1.0,
        "Al2O3": 0.2,
        "CaO": 0.2,
        "Na2O": 0.05,
        "K2O": 0.05,
        "MgO": 0.4,
        "FeO": 0.3,
    }
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(account_mol)},
            species_formula_registry={},
        ),
        temperature_C=1500.0 - 273.15,
        pressure_bar=1e-6,
        fO2_log=-9.0,
        control_inputs={"pO2_bar": 1e-3, "intrinsic_fO2_log": -9.0},
    )
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)

    result = provider.dispatch(request)
    diagnostic = result.diagnostic or {}
    structural = diagnostic["structural_activity_reference"]
    comp_wt = composition_wt_pct_from_account_view(
        request.account_view,
        "process.cleaned_melt",
    )

    assert structural["diagnostic_only"] is True
    assert structural["tier"] == "UNCERTIFIED"
    assert structural["reference_gamma_MOx"]["NaO0.5"] != pytest.approx(1.0)
    consumed_na_activity = melt_oxide_activity("Na2O", account_mol)
    assert consumed_na_activity is not None
    # CF-3: the authoritative vapor path consumes single-cation gamma*X, not
    # the old ideal wt%-fraction proxy. The structural reference remains
    # diagnostic-only; this assertion pins the actual consumer.
    assert diagnostic["activities"]["Na"] == pytest.approx(
        consumed_na_activity.activity
    )
    assert diagnostic["vapor_pressure_numerator_provenance"]["Na"][
        "melt_oxide_activity"
    ] == pytest.approx(
        consumed_na_activity.activity
    )


@pytest.mark.parametrize(
    "temperature_K",
    [float("nan"), float("inf"), float("-inf"), 0.0, -1.0, True, False, "bad"],
)
def test_structural_paths_refuse_invalid_temperature(temperature_K) -> None:
    features = structural_activity_features(_mol_from_wt_pct(_LUNAR_12022_WT_PCT))
    with pytest.raises(ValueError, match="temperature_K must be a finite number > 0 K"):
        reference_activity_coefficients(
            nbo_t=features.nbo_t,
            optical_basicity=features.optical_basicity,
            temperature_K=temperature_K,
        )
    with pytest.raises(ValueError, match="temperature_K must be a finite number > 0 K"):
        estimate_liquidus_flag(
            formula_unit_mole_fractions=features.formula_unit_mole_fractions,
            temperature_K=temperature_K,
        )
    with pytest.raises(ValueError, match="temperature_K must be a finite number > 0 K"):
        structural_activity_diagnostic(
            _mol_from_wt_pct(_LUNAR_12022_WT_PCT),
            temperature_K=temperature_K,
        )


@pytest.mark.parametrize(
    "amount",
    [float("nan"), float("inf"), float("-inf"), -1.0, True, False, None, "bad"],
)
def test_normalize_refuses_corrupt_known_oxide_inventory(amount) -> None:
    with pytest.raises(ValueError, match="must be finite and non-negative"):
        normalize_formula_unit_moles({"SiO2": amount})
    with pytest.raises(ValueError, match="must be finite and non-negative"):
        structural_activity_features({"SiO2": 1.0, "Na2O": amount})
    with pytest.raises(ValueError, match="must be finite and non-negative"):
        structural_activity_diagnostic({"SiO2": amount}, temperature_K=1500.0)


def test_normalize_treats_signed_dust_and_zero_as_absent() -> None:
    formula_mol, unsupported = normalize_formula_unit_moles(
        {"SiO2": 0.0, "CaO": -1.0e-15, "UnobtaniumO": 1.0}
    )
    assert formula_mol == {}
    assert unsupported == ("UnobtaniumO",)


def test_empty_inventory_still_returns_uncertified_liquidus_plane() -> None:
    diagnostic = structural_activity_diagnostic({}, temperature_K=1500.0)
    assert diagnostic["oxygen_mol"] == 0.0
    assert diagnostic["unsupported_species"] == []
    assert diagnostic["liquidus"]["estimated_liquidus_K"] == pytest.approx(
        1223.9939792205735
    )
    assert diagnostic["liquidus"]["status"] == "UNCERTIFIED_PARAMETERIZED_ESTIMATE"


def test_builtin_vapor_pressure_survives_dilute_silica_structural_ood(
    vapor_pressure_data,
) -> None:
    """Structural gamma OOD must not abort the vapor-pressure path (t-717)."""

    account_mol = _mol_from_wt_pct({"SiO2": 2.0, "CaO": 98.0})
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(account_mol)},
            species_formula_registry={},
        ),
        temperature_C=1673.15 - 273.15,
        pressure_bar=1e-6,
        fO2_log=-9.0,
        control_inputs={"pO2_bar": 1e-9, "intrinsic_fO2_log": -9.0},
    )
    result = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(request)
    diagnostic = result.diagnostic or {}
    structural = diagnostic["structural_activity_reference"]
    assert structural["reference_gamma_status"] == "out_of_domain"
    assert "OverflowError" not in structural["reference_gamma_reason"]
    consumed = melt_oxide_activity("CaO", account_mol, temperature_K=1673.15)
    assert consumed is not None
    assert math.isfinite(consumed.activity) and consumed.activity > 0.0
    assert diagnostic["activities"]["Ca"] == pytest.approx(consumed.activity)
