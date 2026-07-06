from __future__ import annotations

import pytest

from engines.builtin._common import composition_wt_pct_from_account_view
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.structural_activity import (
    reference_activity_coefficients,
    structural_activity_diagnostic,
    structural_activity_features,
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
    assert diagnostic["activities"]["Na"] == pytest.approx(
        comp_wt["Na2O"] / 100.0
    )
