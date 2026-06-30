from __future__ import annotations

import json
import math

import pytest

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.fe_redox import (
    calphad_ferrous_feo_activity_diagnostic,
    feo_iw_log10_fO2_bar,
    holzheid_stoich_feo_gamma_band,
    kress91_ferrous_feo_activity,
    kress91_split,
    melt_mol_fractions_for_kress91,
)
from simulator.state import MOLAR_MASS


_BASALTIC_FE_MELT_WT = {
    "SiO2": 45.0,
    "Al2O3": 13.0,
    "CaO": 10.0,
    "MgO": 10.0,
    "FeO": 12.0,
    "TiO2": 3.0,
    "Na2O": 4.0,
    "K2O": 3.0,
}
_T_K = 1673.15


def _moles_from_wt_pct(comp_wt: dict[str, float]) -> dict[str, float]:
    return {
        oxide: wt / 100.0 / (MOLAR_MASS[oxide] / 1000.0)
        for oxide, wt in comp_wt.items()
    }


def test_calphad_feo_activity_is_banded_and_current_stays_inside_above_iw():
    fO2_log = feo_iw_log10_fO2_bar(_T_K) + 0.5
    diagnostic = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=fO2_log,
        T_K=_T_K,
        pressure_bar=1e-6,
    )

    band = diagnostic["a_FeO_calphad"]
    current = diagnostic["a_FeO_current"]

    assert diagnostic["diagnostic_only"] is False
    assert diagnostic["consumed_by_behavior"] is True
    assert diagnostic["authority_unchanged"] is False
    assert diagnostic["standard_state"] == "stoichiometric_FeO_l"
    assert band["low"] <= current <= band["high"]
    assert diagnostic["current_within_calphad_band"] is True
    assert diagnostic["authority"]["regime"] == "iw_to_iw_plus_1_smooth_blend"
    assert diagnostic["authority"]["calphad_weight"] == pytest.approx(0.5)
    assert diagnostic["gamma_FeO"]["central"] == pytest.approx(3.298, rel=1e-3)
    assert diagnostic["gamma_FeO"]["banya_quadratic"]["status"] == (
        "ok_with_ocr_gaps"
    )
    assert "Mg-Ti" in diagnostic["gamma_FeO"]["banya_quadratic"][
        "excluded_or_missing_pairs"
    ]
    assert diagnostic["gamma_FeO"]["oneill_eggins_subregular"]["status"] == (
        "not_digitized_stepB"
    )


def test_banya_quadratic_status_is_ok_when_all_pairs_are_ocr_clean():
    comp_wt = dict(_BASALTIC_FE_MELT_WT)
    comp_wt.pop("TiO2")
    comp_wt["SiO2"] += 3.0
    diagnostic = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=comp_wt,
        fO2_log=feo_iw_log10_fO2_bar(_T_K) + 0.5,
        T_K=_T_K,
        pressure_bar=1e-6,
    )

    banya = diagnostic["gamma_FeO"]["banya_quadratic"]
    assert banya["status"] == "ok"
    assert banya["excluded_or_missing_pairs"] == []


def test_calphad_feo_activity_central_diverges_below_iw_and_carries_tiepoint():
    fO2_log = feo_iw_log10_fO2_bar(_T_K) - 2.0
    diagnostic = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=fO2_log,
        T_K=_T_K,
        pressure_bar=1e-6,
    )

    current = diagnostic["a_FeO_current"]
    kress91 = diagnostic["a_FeO_kress91"]
    central = diagnostic["a_FeO_calphad"]["central"]
    tiepoint = diagnostic["metal_saturation_tie_point"]

    assert current == pytest.approx(central)
    assert central / kress91 > 2.5
    assert diagnostic["authority"]["regime"] == "calphad_metal_saturated_below_iw"
    assert diagnostic["comparison"]["central_over_current"] == pytest.approx(1.0)
    assert diagnostic["comparison"][
        "delta_iw_log10_shift_central_minus_kress91"
    ] > 0.5
    assert diagnostic["comparison"][
        "delta_iw_log10_shift_central_minus_current"
    ] == pytest.approx(0.0)
    assert tiepoint["pure_iw_log10_fO2_bar"] == pytest.approx(
        feo_iw_log10_fO2_bar(_T_K, a_feo=1.0)
    )
    assert tiepoint["central_melt_metal_saturation_log10_fO2_bar"] == (
        pytest.approx(
            tiepoint["pure_iw_log10_fO2_bar"]
            + tiepoint["central_delta_iw_at_aFe_equal_1"]
        )
    )
    assert tiepoint["current_melt_metal_saturation_log10_fO2_bar"] == (
        pytest.approx(tiepoint["central_melt_metal_saturation_log10_fO2_bar"])
    )
    assert tiepoint["current_delta_iw_at_aFe_equal_1"] == pytest.approx(
        tiepoint["central_delta_iw_at_aFe_equal_1"]
    )


def test_calphad_feo_comparison_uses_json_safe_null_when_current_is_zero():
    diagnostic = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=1000.0,
        T_K=_T_K,
        pressure_bar=1e-6,
    )

    comparison = diagnostic["comparison"]
    assert comparison["status"] == "not_comparable_current_zero"
    assert comparison["central_over_current"] is None
    assert comparison["central_over_kress91"] is None
    assert comparison["log10_central_over_current"] is None
    assert comparison["log10_central_over_kress91"] is None
    assert comparison["delta_iw_log10_shift_central_minus_current"] is None
    assert comparison["delta_iw_log10_shift_central_minus_kress91"] is None
    json.dumps(diagnostic, allow_nan=False)


def test_builtin_vapor_pressure_consumes_calphad_feo_authority_below_iw(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    fO2_log = feo_iw_log10_fO2_bar(_T_K) - 2.0
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": _moles_from_wt_pct(_BASALTIC_FE_MELT_WT)
            },
            species_formula_registry={},
        ),
        temperature_C=_T_K - 273.15,
        pressure_bar=1e-6,
        fO2_log=fO2_log,
        control_inputs={
            "pO2_bar": 1e-9,
            "intrinsic_fO2_log": fO2_log,
        },
    )

    result = provider.dispatch(request)
    diagnostic = result.diagnostic["a_FeO_calphad"]
    expected_authority = kress91_ferrous_feo_activity(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=fO2_log,
        T_K=_T_K,
        pressure_bar=1e-6,
    )

    assert result.diagnostic["activities"]["Fe"] == pytest.approx(
        expected_authority
    )
    assert diagnostic["a_FeO_current"] == pytest.approx(expected_authority)
    assert diagnostic["a_FeO_calphad"]["central"] == pytest.approx(
        expected_authority
    )
    assert diagnostic["a_FeO_kress91"] != pytest.approx(expected_authority)
    assert diagnostic["diagnostic_only"] is False
    assert diagnostic["consumed_by_behavior"] is True
    assert diagnostic["authority_unchanged"] is False


def test_feo_activity_uses_kress91_authority_above_iw_plus_one():
    fO2_log = feo_iw_log10_fO2_bar(_T_K) + 1.25
    activity = kress91_ferrous_feo_activity(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=fO2_log,
        T_K=_T_K,
        pressure_bar=1e-6,
    )
    split = kress91_split(
        fO2_log=fO2_log,
        mol_fractions=melt_mol_fractions_for_kress91(_BASALTIC_FE_MELT_WT),
        T_K=_T_K,
        pressure_bar=1e-6,
    )
    diagnostic = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=fO2_log,
        T_K=_T_K,
        pressure_bar=1e-6,
    )

    assert activity == pytest.approx(split["x_feo"], rel=0, abs=1e-15)
    assert diagnostic["authority"]["regime"] == "kress91_ferric_limb_above_iw_plus_1"
    assert diagnostic["authority"]["calphad_weight"] == 0.0
    assert diagnostic["a_FeO_current"] == pytest.approx(diagnostic["a_FeO_kress91"])
    assert diagnostic["comparison"]["central_over_current"] == pytest.approx(
        diagnostic["comparison"]["central_over_kress91"]
    )


def test_feo_activity_uses_central_calphad_authority_below_iw():
    fO2_log = feo_iw_log10_fO2_bar(_T_K) - 0.25
    diagnostic = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=fO2_log,
        T_K=_T_K,
        pressure_bar=1e-6,
    )

    expected = (
        diagnostic["x_FeO_ferrous"]
        * holzheid_stoich_feo_gamma_band(_T_K)["central"]
    )
    assert kress91_ferrous_feo_activity(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=fO2_log,
        T_K=_T_K,
        pressure_bar=1e-6,
    ) == pytest.approx(expected, rel=0, abs=1e-15)
    assert diagnostic["a_FeO_current"] == pytest.approx(expected, rel=0, abs=1e-15)
    assert diagnostic["authority"]["regime"] == "calphad_metal_saturated_below_iw"
    assert diagnostic["authority"]["central_band_is_authoritative"] is True
    assert diagnostic["authority"]["low_high_band_is_diagnostic"] is True


def test_feo_activity_blend_is_continuous_at_iw_boundaries():
    iw = feo_iw_log10_fO2_bar(_T_K)
    eps = 1e-8
    for boundary in (iw, iw + 1.0):
        below = kress91_ferrous_feo_activity(
            comp_wt=_BASALTIC_FE_MELT_WT,
            fO2_log=boundary - eps,
            T_K=_T_K,
            pressure_bar=1e-6,
        )
        at_boundary = kress91_ferrous_feo_activity(
            comp_wt=_BASALTIC_FE_MELT_WT,
            fO2_log=boundary,
            T_K=_T_K,
            pressure_bar=1e-6,
        )
        above = kress91_ferrous_feo_activity(
            comp_wt=_BASALTIC_FE_MELT_WT,
            fO2_log=boundary + eps,
            T_K=_T_K,
            pressure_bar=1e-6,
        )

        assert abs(below - at_boundary) < 1e-8
        assert abs(above - at_boundary) < 1e-8


def test_feo_activity_iw_tiepoint_uses_holzheid_delta_g():
    iw = feo_iw_log10_fO2_bar(_T_K)
    diagnostic = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=iw,
        T_K=_T_K,
        pressure_bar=1e-6,
    )
    central = diagnostic["a_FeO_calphad"]["central"]
    tiepoint = diagnostic["metal_saturation_tie_point"]

    assert diagnostic["authority"]["relative_to_iw_log10"] == pytest.approx(0.0)
    assert diagnostic["a_FeO_current"] == pytest.approx(central)
    assert tiepoint["pure_iw_log10_fO2_bar"] == pytest.approx(iw)
    assert tiepoint["central_melt_metal_saturation_log10_fO2_bar"] == (
        pytest.approx(iw + 2.0 * math.log10(central))
    )
