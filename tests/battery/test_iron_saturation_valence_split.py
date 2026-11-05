"""Battery sibling: elemental T.Fe at iron saturation becomes FeO and Fe2O3."""

from __future__ import annotations

import pytest

from simulator.battery.enums import (
    Authority,
    NoticeKind,
    Quantity,
)
from simulator.battery.score import (
    IRON_SATURATION_SPECIATION_BARE_FE_KEY,
    IRON_SATURATION_SPECIATION_FEOT_ALREADY_PRINTED,
    IRON_SATURATION_SPECIATION_METALLIC_IRON_EXCEEDS_TOTAL,
    IRON_SATURATION_SPECIATION_VALENCE_ALREADY_PRINTED,
    IronSaturationSpeciationRefusal,
    flagged_strata,
    iron_saturation_composition,
    iron_saturation_valence_split,
)
from simulator.fe_redox import (
    IRON_SATURATION_SPECIATION_MISSING_TEMPERATURE,
    IRON_SATURATION_SPECIATION_NO_TOTAL_IRON,
)
from simulator.state import MOLAR_MASS

# Kunisada & Iwai 1986 flux E, Table 1(II). T.Fe is elemental. The metal
# phase is not a slag M.Fe column.
_SLAG = {
    "Na2O": 40.9,
    "SiO2": 39.5,
    "MgO": 17.5,
    "P2O5": 1.95,
    "T.Fe": 2.3,
}
_T_K = 1600.0 + 273.15
_P_BAR = 1.0


def _iron_wt(feo_wt: float, fe2o3_wt: float) -> float:
    return (
        feo_wt * float(MOLAR_MASS["Fe"]) / float(MOLAR_MASS["FeO"])
        + fe2o3_wt * 2.0 * float(MOLAR_MASS["Fe"]) / float(MOLAR_MASS["Fe2O3"])
    )


def _ferric_fraction(feo_wt: float, fe2o3_wt: float) -> float:
    n_fe2 = feo_wt / float(MOLAR_MASS["FeO"])
    n_fe3 = 2.0 * fe2o3_wt / float(MOLAR_MASS["Fe2O3"])
    return n_fe3 / (n_fe2 + n_fe3)


def test_tfe_only_slag_splits_below_the_pure_iw_edge() -> None:
    result = iron_saturation_valence_split(
        _SLAG, T_K=_T_K, pressure_bar=_P_BAR
    )
    factor = float(MOLAR_MASS["FeO"]) / float(MOLAR_MASS["Fe"])

    assert factor == pytest.approx(1.2865, abs=5e-4)
    assert result.metallic_fe_wt is None
    assert result.oxidised_fe_wt == pytest.approx(2.3)
    assert result.feot_wt == pytest.approx(2.3 * factor)
    assert _iron_wt(result.feo_wt, result.fe2o3_wt) == pytest.approx(2.3)
    assert _ferric_fraction(result.feo_wt, result.fe2o3_wt) == pytest.approx(
        result.split.fe3_over_sigma_fe
    )
    assert result.split.fe3_over_sigma_fe < result.split.band[1]
    assert result.fe2o3_wt > 0.0
    assert result.notice.kind is (
        NoticeKind.IRON_VALENCE_SPECIATED_AT_IRON_SATURATION
    )
    assert result.notice.authority is Authority.DERIVED
    assert result.notice.affected_quantities == (
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
    )
    for field in (
        "log10_fO2_sat=",
        "delta_iw_pure_feo=",
        "a_FeO=",
        "fe3_over_sigma_fe=",
        "band=[",
        "iterations=",
        "kress91_extrapolation=",
        "kress91_high_uncertainty=",
    ):
        assert field in result.notice.reason
    assert result.notice.band is not None
    assert flagged_strata((result.notice,)) == ()


def test_printed_metallic_iron_is_subtracted() -> None:
    slag = dict(_SLAG)
    slag["M.Fe"] = 0.8
    bare = iron_saturation_valence_split(_SLAG, T_K=_T_K, pressure_bar=_P_BAR)
    printed = iron_saturation_valence_split(
        slag, T_K=_T_K, pressure_bar=_P_BAR
    )

    assert printed.metallic_fe_wt == pytest.approx(0.8)
    assert printed.oxidised_fe_wt == pytest.approx(1.5)
    assert _iron_wt(printed.feo_wt, printed.fe2o3_wt) == pytest.approx(1.5)
    assert printed.feot_wt < bare.feot_wt
    assert _iron_wt(printed.feo_wt, printed.fe2o3_wt) < _iron_wt(
        bare.feo_wt, bare.fe2o3_wt
    )


def test_explicit_metallic_argument_subtracts_when_the_map_has_no_key() -> None:
    result = iron_saturation_valence_split(
        _SLAG, T_K=_T_K, pressure_bar=_P_BAR, metallic_fe_wt=0.3
    )
    assert result.oxidised_fe_wt == pytest.approx(2.0)
    assert _iron_wt(result.feo_wt, result.fe2o3_wt) == pytest.approx(2.0)


def test_missing_temperature_refuses_typed() -> None:
    with pytest.raises(IronSaturationSpeciationRefusal) as exc:
        iron_saturation_valence_split(_SLAG, T_K=None, pressure_bar=_P_BAR)
    assert exc.value.reason == IRON_SATURATION_SPECIATION_MISSING_TEMPERATURE


def test_metallic_iron_above_total_refuses_typed() -> None:
    slag = dict(_SLAG)
    slag["M.Fe"] = 3.0
    with pytest.raises(IronSaturationSpeciationRefusal) as exc:
        iron_saturation_valence_split(slag, T_K=_T_K, pressure_bar=_P_BAR)
    assert exc.value.reason == (
        IRON_SATURATION_SPECIATION_METALLIC_IRON_EXCEEDS_TOTAL
    )


def test_no_total_iron_refuses_typed() -> None:
    with pytest.raises(IronSaturationSpeciationRefusal) as exc:
        iron_saturation_valence_split(
            {"SiO2": 50.0, "CaO": 50.0}, T_K=_T_K, pressure_bar=_P_BAR
        )
    assert exc.value.reason == IRON_SATURATION_SPECIATION_NO_TOTAL_IRON


def test_printed_valence_and_bare_fe_and_feot_refuse_typed() -> None:
    cases = (
        ({"SiO2": 50.0, "FeO": 10.0, "T.Fe": 2.0}, IRON_SATURATION_SPECIATION_VALENCE_ALREADY_PRINTED),
        ({"SiO2": 50.0, "FeOT": 10.0, "T.Fe": 2.0}, IRON_SATURATION_SPECIATION_FEOT_ALREADY_PRINTED),
        ({"SiO2": 50.0, "Fe": 2.0}, IRON_SATURATION_SPECIATION_BARE_FE_KEY),
    )
    for composition, reason in cases:
        with pytest.raises(IronSaturationSpeciationRefusal) as exc:
            iron_saturation_valence_split(
                composition, T_K=_T_K, pressure_bar=_P_BAR
            )
        assert exc.value.reason == reason


def test_weights_are_not_clamped_when_the_fraction_exceeds_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.fe_redox import MetalSaturationValenceSplit

    def over(_comp: object, *, T_K: object, pressure_bar: float) -> MetalSaturationValenceSplit:
        return MetalSaturationValenceSplit(
            log10_fO2_sat=-8.0,
            delta_iw_pure_feo=-1.0,
            a_FeO=0.3,
            fe3_over_sigma_fe=1.2,
            band=(1.2, 1.2),
            iterations=3,
            kress91_extrapolation=False,
            kress91_high_uncertainty=False,
        )

    monkeypatch.setattr(
        "simulator.battery.score.metal_saturation_valence_split", over
    )
    result = iron_saturation_valence_split(_SLAG, T_K=_T_K, pressure_bar=_P_BAR)
    assert result.split.fe3_over_sigma_fe == 1.2
    assert result.feo_wt < 0.0
    assert result.fe2o3_wt > 0.0


def test_does_not_call_wustite_n0_or_oxygen_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("wustite N0 or oxygen-closure owner called")

    monkeypatch.setattr(
        "simulator.battery.score.iron_saturation_composition", boom
    )
    monkeypatch.setattr(
        "simulator.battery.score.element_mass_fraction_redox", boom
    )
    iron_saturation_valence_split(_SLAG, T_K=_T_K, pressure_bar=_P_BAR)
    assert iron_saturation_composition is not None
