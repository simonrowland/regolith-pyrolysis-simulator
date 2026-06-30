from __future__ import annotations

import json

import pytest

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.fe_redox import (
    calphad_ferrous_feo_activity_diagnostic,
    feo_iw_log10_fO2_bar,
    kress91_ferrous_feo_activity,
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

    assert diagnostic["diagnostic_only"] is True
    assert diagnostic["consumed_by_behavior"] is False
    assert diagnostic["authority_unchanged"] is True
    assert diagnostic["standard_state"] == "stoichiometric_FeO_l"
    assert band["low"] <= current <= band["high"]
    assert diagnostic["current_within_calphad_band"] is True
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
    central = diagnostic["a_FeO_calphad"]["central"]
    tiepoint = diagnostic["metal_saturation_tie_point"]

    assert central / current > 2.5
    assert diagnostic["comparison"][
        "delta_iw_log10_shift_central_minus_current"
    ] > 0.5
    assert tiepoint["pure_iw_log10_fO2_bar"] == pytest.approx(
        feo_iw_log10_fO2_bar(_T_K, a_feo=1.0)
    )
    assert tiepoint["central_melt_metal_saturation_log10_fO2_bar"] == (
        pytest.approx(
            tiepoint["pure_iw_log10_fO2_bar"]
            + tiepoint["central_delta_iw_at_aFe_equal_1"]
        )
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
    assert comparison["log10_central_over_current"] is None
    assert comparison["delta_iw_log10_shift_central_minus_current"] is None
    json.dumps(diagnostic, allow_nan=False)


def test_builtin_vapor_pressure_emits_calphad_feo_diagnostic_without_authority(
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
    expected_current = kress91_ferrous_feo_activity(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=fO2_log,
        T_K=_T_K,
        pressure_bar=1e-6,
    )

    assert result.diagnostic["activities"]["Fe"] == pytest.approx(
        expected_current
    )
    assert diagnostic["a_FeO_current"] == pytest.approx(expected_current)
    assert diagnostic["a_FeO_calphad"]["central"] != pytest.approx(
        expected_current
    )
    assert diagnostic["diagnostic_only"] is True
    assert diagnostic["consumed_by_behavior"] is False
    assert diagnostic["authority_unchanged"] is True
