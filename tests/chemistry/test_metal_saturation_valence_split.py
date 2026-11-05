"""Iron-saturation valence split: the self-consistent tie point, not pure IW."""

from __future__ import annotations

import math

import pytest

from simulator.fe_redox import (
    IRON_SATURATION_SPECIATION_ACTIVITY_UNAVAILABLE,
    IRON_SATURATION_SPECIATION_DID_NOT_CONVERGE,
    IRON_SATURATION_SPECIATION_MISSING_TEMPERATURE,
    IRON_SATURATION_SPECIATION_NO_TOTAL_IRON,
    IronSaturationSpeciationRefusal,
    _calphad_feo_activity_components,
    feo_iw_log10_fO2_bar,
    kress91_split,
    melt_mol_fractions_for_kress91,
    metal_saturation_valence_split,
)
from simulator.state import MOLAR_MASS

# Kunisada & Iwai 1986 flux E final slag, Table 1(II): elemental T.Fe beside
# the other oxides. 1600 C is the section III.2 temperature. 1 bar is the
# test pressure control; the extract does not print total pressure.
_KUNISADA_E_OXIDES = {
    "Na2O": 40.9,
    "SiO2": 39.5,
    "MgO": 17.5,
    "P2O5": 1.95,
}
_KUNISADA_E_T_FE = 2.3
_T_K = 1600.0 + 273.15
_P_BAR = 1.0


def _feot_slag(elemental_fe_wt: float = _KUNISADA_E_T_FE) -> dict[str, float]:
    composition = dict(_KUNISADA_E_OXIDES)
    composition["FeOT"] = (
        elemental_fe_wt * float(MOLAR_MASS["FeO"]) / float(MOLAR_MASS["Fe"])
    )
    return composition


def _pure_iw_fe3(comp_wt: dict[str, float], T_K: float) -> float:
    pure = feo_iw_log10_fO2_bar(T_K, a_feo=1.0)
    split = kress91_split(
        fO2_log=pure,
        mol_fractions=melt_mol_fractions_for_kress91(comp_wt),
        T_K=T_K,
        pressure_bar=_P_BAR,
    )
    return float(split["fe3"])


def test_tfe_slag_fe3_is_below_the_pure_iw_edge() -> None:
    composition = _feot_slag()
    result = metal_saturation_valence_split(
        composition, T_K=_T_K, pressure_bar=_P_BAR
    )
    edge = _pure_iw_fe3(composition, _T_K)

    assert result.fe3_over_sigma_fe < edge
    assert result.iterations >= 2
    assert result.iterations <= 50
    assert result.band[0] == pytest.approx(result.fe3_over_sigma_fe)
    assert result.band[1] == pytest.approx(edge)
    assert result.band[0] < result.band[1]
    assert result.delta_iw_pure_feo < 0.0
    assert 0.0 < result.a_FeO < 1.0
    assert result.a_FeO_authoritative_clamped_to_pure_feo_ceiling is False
    assert result.log10_fO2_sat == pytest.approx(
        feo_iw_log10_fO2_bar(_T_K, a_feo=1.0) + result.delta_iw_pure_feo
    )
    again = _calphad_feo_activity_components(
        comp_wt=composition,
        fO2_log=result.log10_fO2_sat,
        T_K=_T_K,
        pressure_bar=_P_BAR,
    )
    tie = again["metal_saturation_tie_point"][
        "current_melt_metal_saturation_log10_fO2_bar"
    ]
    assert abs(float(tie) - result.log10_fO2_sat) < 1e-6
    direct = kress91_split(
        fO2_log=result.log10_fO2_sat,
        mol_fractions=melt_mol_fractions_for_kress91(composition),
        T_K=_T_K,
        pressure_bar=_P_BAR,
    )
    assert result.fe3_over_sigma_fe == float(direct["fe3"])
    assert result.kress91_extrapolation is bool(direct["extrapolation"])
    assert result.kress91_high_uncertainty is bool(direct["high_uncertainty"])


def test_iron_rich_slag_carries_the_pure_feo_ceiling_flag() -> None:
    # Kunisada & Iwai 1986 Table 1(I) row D-2, table row 27, 1550 C.
    # The tie point's unclamped a_FeO is above 1, so the existing ceiling
    # flag is true. This split does not apply a second clamp.
    composition = {
        "Na2O": 25.9,
        "SiO2": 20.8,
        "MgO": 4.9,
        "P2O5": 2.44,
    }
    composition["FeOT"] = 30.4 * float(MOLAR_MASS["FeO"]) / float(MOLAR_MASS["Fe"])
    temperature = 1550.0 + 273.15
    result = metal_saturation_valence_split(
        composition, T_K=temperature, pressure_bar=_P_BAR
    )

    assert result.a_FeO_authoritative_clamped_to_pure_feo_ceiling is True
    assert result.a_FeO == pytest.approx(1.0)
    assert result.fe3_over_sigma_fe == pytest.approx(
        _pure_iw_fe3(composition, temperature)
    )
    assert result.delta_iw_pure_feo == pytest.approx(0.0, abs=1e-6)


def test_kress91_flags_follow_the_temperature_band() -> None:
    composition = _feot_slag()
    hot = metal_saturation_valence_split(
        composition, T_K=1700.0 + 273.15, pressure_bar=_P_BAR
    )
    cold = metal_saturation_valence_split(
        composition, T_K=1000.0 + 273.15, pressure_bar=_P_BAR
    )

    assert hot.kress91_extrapolation is True
    assert hot.kress91_high_uncertainty is False
    assert cold.kress91_extrapolation is True
    assert cold.kress91_high_uncertainty is True
    assert cold.fe3_over_sigma_fe < _pure_iw_fe3(composition, 1000.0 + 273.15)


@pytest.mark.parametrize(
    "T_K",
    [None, True, False, 0.0, -10.0, float("nan"), float("inf"), "hot", ""],
)
def test_missing_temperature_refuses_typed(T_K: object) -> None:
    with pytest.raises(IronSaturationSpeciationRefusal) as exc:
        metal_saturation_valence_split(
            _feot_slag(), T_K=T_K, pressure_bar=_P_BAR
        )
    assert exc.value.reason == IRON_SATURATION_SPECIATION_MISSING_TEMPERATURE


def test_no_total_iron_refuses_typed() -> None:
    with pytest.raises(IronSaturationSpeciationRefusal) as exc:
        metal_saturation_valence_split(
            {"SiO2": 60.0, "CaO": 40.0}, T_K=_T_K, pressure_bar=_P_BAR
        )
    assert exc.value.reason == IRON_SATURATION_SPECIATION_NO_TOTAL_IRON


def test_nonconvergence_refuses_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def walk(**kwargs: object) -> dict[str, object]:
        fO2_log = float(kwargs["fO2_log"])  # type: ignore[arg-type]
        return {
            "status": "ok",
            "a_FeO_authoritative": 0.5,
            "metal_saturation_tie_point": {
                "current_melt_metal_saturation_log10_fO2_bar": fO2_log + 1.0,
            },
        }

    monkeypatch.setattr(
        "simulator.fe_redox._calphad_feo_activity_components", walk
    )
    with pytest.raises(IronSaturationSpeciationRefusal) as exc:
        metal_saturation_valence_split(
            _feot_slag(), T_K=_T_K, pressure_bar=_P_BAR
        )
    assert exc.value.reason == IRON_SATURATION_SPECIATION_DID_NOT_CONVERGE


def test_fe3_is_not_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    def fixed(**kwargs: object) -> dict[str, object]:
        return {
            "status": "ok",
            "a_FeO_authoritative": 1.7,
            "a_FeO_authoritative_clamped_to_pure_feo_ceiling": True,
            "metal_saturation_tie_point": {
                "current_melt_metal_saturation_log10_fO2_bar": float(
                    kwargs["fO2_log"]  # type: ignore[arg-type]
                ),
            },
        }

    def unclamped(**kwargs: object) -> dict[str, object]:
        return {
            "fe3": 1.2,
            "extrapolation": False,
            "high_uncertainty": False,
        }

    monkeypatch.setattr(
        "simulator.fe_redox._calphad_feo_activity_components", fixed
    )
    monkeypatch.setattr("simulator.fe_redox.kress91_split", unclamped)
    result = metal_saturation_valence_split(
        _feot_slag(), T_K=_T_K, pressure_bar=_P_BAR
    )
    assert result.fe3_over_sigma_fe == 1.2
    assert result.a_FeO == 1.7
    assert result.a_FeO_authoritative_clamped_to_pure_feo_ceiling is True
    assert result.band == (1.2, 1.2)


def test_missing_activity_refuses_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_activity(**kwargs: object) -> dict[str, object]:
        return {
            "status": "ok",
            "metal_saturation_tie_point": {
                "current_melt_metal_saturation_log10_fO2_bar": float(
                    kwargs["fO2_log"]  # type: ignore[arg-type]
                ),
            },
        }

    monkeypatch.setattr(
        "simulator.fe_redox._calphad_feo_activity_components", no_activity
    )
    with pytest.raises(IronSaturationSpeciationRefusal) as exc:
        metal_saturation_valence_split(
            _feot_slag(), T_K=_T_K, pressure_bar=_P_BAR
        )
    assert exc.value.reason == IRON_SATURATION_SPECIATION_ACTIVITY_UNAVAILABLE


def test_tie_point_matches_activity_definition() -> None:
    composition = _feot_slag()
    result = metal_saturation_valence_split(
        composition, T_K=_T_K, pressure_bar=_P_BAR
    )
    assert result.log10_fO2_sat == pytest.approx(
        feo_iw_log10_fO2_bar(_T_K, a_feo=result.a_FeO)
    )
    assert math.isfinite(result.fe3_over_sigma_fe)
