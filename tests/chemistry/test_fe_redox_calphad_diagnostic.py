from __future__ import annotations

import json
import math

import pytest

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.fe_redox import (
    HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL,
    HOLZHEID_FEO_GAMMA_WUSTITE_SIGMA,
    HOLZHEID_STOICH_FEO_MULTIPLIER_BY_C,
    Kress91InvalidControls,
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
_IRON_RICH_BASALT_WT = {
    "SiO2": 39.0,
    "Al2O3": 8.0,
    "CaO": 7.0,
    "MgO": 6.0,
    "FeO": 36.0,
    "TiO2": 2.5,
    "Na2O": 1.0,
    "K2O": 0.5,
}
_T_K = 1673.15


def _moles_from_wt_pct(comp_wt: dict[str, float]) -> dict[str, float]:
    return {
        oxide: wt / 100.0 / (MOLAR_MASS[oxide] / 1000.0)
        for oxide, wt in comp_wt.items()
    }


@pytest.mark.parametrize(
    ("temperature_C", "expected_multiplier", "expected_gamma"),
    [
        (1300.001, 2.0199992, 3.43399864),
        (1599.999, 1.6600014, 2.82200238),
    ],
)
def test_holzheid_gamma_interpolates_inside_certified_band(
    temperature_C: float,
    expected_multiplier: float,
    expected_gamma: float,
) -> None:
    band = holzheid_stoich_feo_gamma_band(temperature_C + 273.15)

    assert band["conversion_multiplier"] == pytest.approx(
        expected_multiplier, abs=1e-12
    )
    assert band["central"] == pytest.approx(expected_gamma, abs=1e-12)
    assert band["conversion_status"] == "interpolated_verified_range"
    assert band["authority_level"] == "certified"
    assert band["notice"] is None


@pytest.mark.parametrize("temperature_K", [math.nan, math.inf, -math.inf])
def test_holzheid_gamma_refuses_non_finite_temperature(temperature_K: float) -> None:
    with pytest.raises(Kress91InvalidControls, match="finite positive"):
        holzheid_stoich_feo_gamma_band(temperature_K)


@pytest.mark.parametrize("temperature_K", [0.0, -10.0])
def test_holzheid_gamma_refuses_non_positive_temperature(temperature_K: float) -> None:
    with pytest.raises(Kress91InvalidControls, match="finite positive"):
        holzheid_stoich_feo_gamma_band(temperature_K)


@pytest.mark.parametrize(
    ("edge_C", "outside_delta_C"),
    [(1300.0, -1e-6), (1600.0, 1e-6)],
)
def test_holzheid_gamma_is_continuous_at_certified_edges(
    edge_C: float,
    outside_delta_C: float,
) -> None:
    at_edge = holzheid_stoich_feo_gamma_band(edge_C + 273.15)
    just_outside = holzheid_stoich_feo_gamma_band(
        edge_C + outside_delta_C + 273.15
    )

    assert at_edge["conversion_status"] == "interpolated_verified_range"
    assert just_outside["conversion_status"].startswith("extrapolated_")
    assert just_outside["central"] == pytest.approx(at_edge["central"], abs=1e-8)
    assert just_outside["conversion_multiplier"] == pytest.approx(
        at_edge["conversion_multiplier"], abs=1e-8
    )


def test_holzheid_gamma_preserves_in_span_values() -> None:
    band = holzheid_stoich_feo_gamma_band(1400.0 + 273.15)

    assert band["conversion_multiplier"] == pytest.approx(1.94, abs=1e-12)
    assert band["central"] == pytest.approx(1.70 * 1.94, abs=1e-12)
    assert band["measurement_sigma"] == pytest.approx(0.22 * 1.94, abs=1e-12)
    assert band["measurement_low"] == pytest.approx(1.70 * 1.94 - 0.22 * 1.94)
    assert band["measurement_high"] == pytest.approx(1.70 * 1.94 + 0.22 * 1.94)
    assert band["conversion_status"] == "interpolated_verified_range"
    assert band["authority_level"] == "certified"
    assert band["notice"] is None


def test_holzheid_1300_edge_reproduces_printed_1600_gamma() -> None:
    low_edge_C, low_multiplier = HOLZHEID_STOICH_FEO_MULTIPLIER_BY_C[0]
    _, high_multiplier = HOLZHEID_STOICH_FEO_MULTIPLIER_BY_C[-1]
    low_edge_T_K = low_edge_C + 273.15
    high_edge_T_K = HOLZHEID_STOICH_FEO_MULTIPLIER_BY_C[-1][0] + 273.15
    low_edge_gamma = HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL * low_multiplier
    predicted_1600_gamma = math.exp(
        math.log(low_edge_gamma) * low_edge_T_K / high_edge_T_K
    )
    printed_1600_gamma = HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL * high_multiplier

    assert predicted_1600_gamma == pytest.approx(2.818, abs=0.001)
    assert abs(predicted_1600_gamma - printed_1600_gamma) < 0.01


def test_holzheid_1400_gamma_residual_from_each_edge_is_pinned() -> None:
    points = HOLZHEID_STOICH_FEO_MULTIPLIER_BY_C
    target_T_K = 1400.0 + 273.15
    printed_multiplier = points[1][1]
    predicted_multipliers = []
    residuals = []

    for edge_C, edge_multiplier in (points[0], points[-1]):
        edge_T_K = edge_C + 273.15
        edge_gamma = HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL * edge_multiplier
        predicted_gamma = math.exp(math.log(edge_gamma) * edge_T_K / target_T_K)
        predicted_multiplier = predicted_gamma / HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL
        predicted_multipliers.append(predicted_multiplier)
        residuals.append(
            (predicted_multiplier - printed_multiplier) / printed_multiplier
        )

    # The printed point is about 3.2% (0.0145 dex) above the curve; its W is
    # 16.60 kJ/mol versus 16.14/16.16 at the edges, scatter around constant W.
    assert predicted_multipliers == pytest.approx(
        [1.87641, 1.87917], abs=1e-5
    )
    assert [100.0 * residual for residual in residuals] == pytest.approx(
        [-3.278, -3.136], abs=0.001
    )
    assert all(abs(residual) < 0.05 for residual in residuals)


# Replaces b-1124's linear-only expectations at 1299, 1000, 1601, and 1800 C.
@pytest.mark.parametrize(
    ("temperature_C", "printed_gamma", "expected_uncertainty_band_log10"),
    [
        (1670.0, 2.718, 0.084841284),
        (1750.0, 2.613, 0.088523374),
        (1900.0, 2.445, 0.095121568),
        (2200.0, 2.194, 0.107191610),
        (950.0, 4.888, 0.081914830),
        (225.0, 49.2, 0.274968621),
    ],
)
def test_holzheid_regular_solution_table_matches_independent_edge_law(
    temperature_C: float,
    printed_gamma: float,
    expected_uncertainty_band_log10: float,
) -> None:
    T_K = temperature_C + 273.15
    points = HOLZHEID_STOICH_FEO_MULTIPLIER_BY_C
    below = temperature_C < points[0][0]
    edge_C, edge_multiplier = points[0] if below else points[-1]
    edge_T_K = edge_C + 273.15
    edge_gamma = HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL * edge_multiplier
    independent_gamma = math.exp(math.log(edge_gamma) * edge_T_K / T_K)
    band = holzheid_stoich_feo_gamma_band(T_K)
    notice = band["notice"]

    assert band["central"] == pytest.approx(independent_gamma, rel=1e-14)
    assert band["conversion_multiplier"] == pytest.approx(
        independent_gamma / HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL,
        rel=1e-14,
    )
    assert band["central"] == pytest.approx(printed_gamma, rel=0.0011)
    assert band["conversion_status"] == (
        "extrapolated_below_verified_range"
        if below
        else "extrapolated_above_verified_range"
    )
    assert band["authority_level"] == "extrapolated"
    assert notice["kind"] == "out_of_certified_band"
    assert notice["authority_level"] == "extrapolated"
    assert notice["side"] == ("below" if below else "above")
    assert notice["certified_band"] == {"temperature_C": (1300.0, 1600.0)}
    assert notice["edge_used"] == {
        "temperature_C": edge_C,
        "temperature_K": edge_T_K,
    }
    expected_W_J_mol = 8.314462618 * edge_T_K * math.log(edge_gamma)
    assert notice["W_J_mol"] == pytest.approx(expected_W_J_mol, rel=1e-14)
    assert 16130.0 <= notice["W_J_mol"] <= 16160.0
    assert notice["distance_outside_span_widths"] == pytest.approx(
        abs(temperature_C - edge_C) / (points[-1][0] - points[0][0])
    )

    target_1400_T_K = points[1][0] + 273.15
    model_scatter_log10 = max(
        abs(
            math.log10(
                points[1][1]
                / (
                    math.exp(
                        math.log(
                            HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL
                            * scatter_edge_multiplier
                        )
                        * (scatter_edge_C + 273.15)
                        / target_1400_T_K
                    )
                    / HOLZHEID_FEO_GAMMA_WUSTITE_CENTRAL
                )
            )
        )
        for scatter_edge_C, scatter_edge_multiplier in (points[0], points[-1])
    )
    expected_low = 10.0 ** (
        math.log10(independent_gamma) - expected_uncertainty_band_log10
    )
    expected_high = 10.0 ** (
        math.log10(independent_gamma) + expected_uncertainty_band_log10
    )
    expected_sigma = max(
        independent_gamma - expected_low,
        expected_high - independent_gamma,
    )

    assert notice["model_scatter_log10"] == pytest.approx(model_scatter_log10)
    assert notice["uncertainty_band_log10"] == pytest.approx(
        math.sqrt(
            notice["sigma_band_log10"] ** 2 + model_scatter_log10**2
        )
    )
    assert notice["uncertainty_band_log10"] == pytest.approx(
        expected_uncertainty_band_log10, abs=5e-10
    )
    assert notice["uncertainty_band"] == band["measurement_sigma"]
    assert band["measurement_low"] == pytest.approx(expected_low)
    assert band["measurement_high"] == pytest.approx(expected_high)
    assert band["measurement_sigma"] == pytest.approx(expected_sigma)


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
    assert diagnostic["authority"]["regime"] == (
        "iw_pure_feo_to_iw_pure_feo_plus_1_smooth_blend"
    )
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
    assert diagnostic["authority"]["regime"] == (
        "calphad_metal_saturated_below_iw_pure_feo"
    )
    assert diagnostic["comparison"]["central_over_current"] == pytest.approx(1.0)
    assert diagnostic["comparison"][
        "delta_iw_log10_shift_central_minus_kress91"
    ] > 0.5
    assert diagnostic["comparison"][
        "delta_iw_log10_shift_central_minus_current"
    ] == pytest.approx(0.0)
    assert tiepoint["iw_pure_feo_log10_fO2_bar"] == pytest.approx(
        feo_iw_log10_fO2_bar(_T_K, a_feo=1.0)
    )
    assert tiepoint["central_melt_metal_saturation_log10_fO2_bar"] == (
        pytest.approx(
            tiepoint["iw_pure_feo_log10_fO2_bar"]
            + tiepoint[
                "central_melt_saturation_offset_from_iw_pure_feo_log10_fO2"
            ]
        )
    )
    assert tiepoint["current_melt_metal_saturation_log10_fO2_bar"] == (
        pytest.approx(tiepoint["central_melt_metal_saturation_log10_fO2_bar"])
    )
    assert tiepoint[
        "current_melt_saturation_offset_from_iw_pure_feo_log10_fO2"
    ] == pytest.approx(
        tiepoint[
            "central_melt_saturation_offset_from_iw_pure_feo_log10_fO2"
        ]
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
    assert diagnostic["authority"]["regime"] == (
        "kress91_ferric_limb_above_iw_pure_feo_plus_1"
    )
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
    assert diagnostic["authority"]["regime"] == (
        "calphad_metal_saturated_below_iw_pure_feo"
    )
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


def test_feo_activity_iw_pure_feo_basis_documents_melt_offset():
    iw = feo_iw_log10_fO2_bar(_T_K)
    diagnostic = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=_BASALTIC_FE_MELT_WT,
        fO2_log=iw,
        T_K=_T_K,
        pressure_bar=1e-6,
    )
    central = diagnostic["a_FeO_calphad"]["central"]
    tiepoint = diagnostic["metal_saturation_tie_point"]
    offset = tiepoint[
        "central_melt_saturation_offset_from_iw_pure_feo_log10_fO2"
    ]

    assert diagnostic["authority"]["iw_basis"] == "IW(pure-FeO)"
    assert diagnostic["authority"]["relative_to_iw_pure_feo_log10"] == (
        pytest.approx(0.0)
    )
    assert diagnostic["a_FeO_current"] == pytest.approx(central)
    assert tiepoint["iw_pure_feo_log10_fO2_bar"] == pytest.approx(iw)
    assert offset == pytest.approx(2.0 * math.log10(central))
    assert offset < 0.0
    assert tiepoint["central_melt_metal_saturation_log10_fO2_bar"] == (
        pytest.approx(iw + offset)
    )


def test_iron_rich_basalt_clamp_reports_unclamped_band_and_closes_fe_atoms(
    vapor_pressure_data,
):
    fO2_log = feo_iw_log10_fO2_bar(_T_K) - 0.25
    diagnostic = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=_IRON_RICH_BASALT_WT,
        fO2_log=fO2_log,
        T_K=_T_K,
        pressure_bar=1e-6,
    )

    assert diagnostic["a_FeO_calphad"]["central"] > 1.0
    assert diagnostic["a_FeO_authoritative_unclamped"] > 1.0
    assert diagnostic["a_FeO_authoritative_clamped_to_pure_feo_ceiling"] is True
    assert diagnostic["a_FeO_current"] == pytest.approx(1.0)
    band = diagnostic["a_FeO_calphad"]
    assert diagnostic["current_within_calphad_band"] is (
        band["low"]
        <= diagnostic["a_FeO_authoritative_unclamped"]
        <= band["high"]
    )
    split = diagnostic["kress91_split"]
    feot = melt_mol_fractions_for_kress91(_IRON_RICH_BASALT_WT)["FeOt"]
    assert split["x_feo"] + 2.0 * split["x_fe2o3"] == pytest.approx(feot)
    assert kress91_ferrous_feo_activity(
        comp_wt=_IRON_RICH_BASALT_WT,
        fO2_log=fO2_log,
        T_K=_T_K,
        pressure_bar=1e-6,
    ) == pytest.approx(1.0)

    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": _moles_from_wt_pct(_IRON_RICH_BASALT_WT)
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
    assert result.diagnostic["activities"]["Fe"] == pytest.approx(1.0)
