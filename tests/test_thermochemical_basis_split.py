from __future__ import annotations

import pytest

from simulator.chemistry.ellingham_thermo import ELLINGHAM_THERMO
from simulator.thermal_budget import (
    _OXIDE_DISSOCIATION_KJ_PER_MOL,
    evaporation_enthalpy_budget,
)


@pytest.mark.parametrize(
    (
        "metal",
        "oxide",
        "metal_molar_mass_g_mol",
        "expected_n_metal",
        "expected_n_oxide",
        "expected_cf8_kj_per_mol_oxide",
        "expected_cf8_kj_per_mol_o2",
        "expected_ellingham_kj_per_mol_o2",
        "expected_delta_percent",
    ),
    [
        (
            "Na",
            "Na2O",
            22.98976928,
            4.0,
            2.0,
            414.22,
            828.440,
            1135.130,
            -27.018,
        ),
        ("K", "K2O", 39.0983, 4.0, 2.0, 363.17, 726.340, 975.838, -25.568),
        (
            "Mg",
            "MgO",
            24.305,
            2.0,
            2.0,
            601.60,
            1203.200,
            1342.444,
            -10.372,
        ),
    ],
)
def test_cf8_298k_and_legacy_ellingham_fit_bases_stay_distinct(
    metal: str,
    oxide: str,
    metal_molar_mass_g_mol: float,
    expected_n_metal: float,
    expected_n_oxide: float,
    expected_cf8_kj_per_mol_oxide: float,
    expected_cf8_kj_per_mol_o2: float,
    expected_ellingham_kj_per_mol_o2: float,
    expected_delta_percent: float,
) -> None:
    """Pin the intentional split between the two thermochemical bases.

    CF-8 reverses the oxide's 298 K standard formation enthalpy, using
    Na2O(s)/K2O(s)/MgO(s) and Na(s)/K(s)/Mg(s) + O2(g) reference states.
    Multiplication by ``n_ox`` converts its per-parent-oxide value to the
    Ellingham convention of one mol O2.  The legacy Ellingham tuple instead
    stores the effective
    intercept of a 1100-1700 K linear dG(T) refit.  Its JANAF reaction states
    include high-temperature phase choices: Na and K are gaseous over most or
    all of that fit band, while Mg crosses from liquid to gas.  Phase enthalpy
    offsets therefore live in the fit intercept, which is not a 298 K dH.

    Magnitude check at 1400 K, using JANAF ``H-H(298)`` in kJ/mol and the
    reaction balance ``n_M*dH_M + dH_O2 - n_ox*dH_oxide``: Na gives
    ``4*130.203 + 36.957 - 2*112.719 = 318.331`` versus the 306.690 gap,
    and K gives ``4*111.904 + 36.957 - 2*118.641 = 251.291`` versus
    249.498.  Thus the metal phase/sensible term sets the scale, while oxide
    and O2 sensible terms are required for closure; latent heat alone would
    overstate it (389.680 Na, 307.600 K, 254.800 Mg).  For Mg, the all-gas
    1400 K endpoint gives ``2*170.003 + 36.957 - 2*53.918 = 269.127``, not
    the 139.244 flat-fit gap: that fit spans Mg(l)->Mg(g), so its intermediate
    magnitude cannot honestly be reconstructed as an all-gas endpoint.  As a
    reference-state sanity check, the flat Na intercept, -1135.130, lies near
    the gas-basis segment intercept, -1207.826, rather than the liquid-basis
    -731.077 segment intercept.

    The resulting -27.018%, -25.568%, and -10.372% gaps are expected; allow
    only 0.05 percentage point rounding movement while independently pinning
    both source values to 0.001 kJ/mol O2.  A physics change must update this
    derivation explicitly rather than silently forcing the bases to agree.
    """

    ellingham_dh, _ellingham_ds, n_metal, n_oxide = ELLINGHAM_THERMO[metal]
    cf8_kj_per_mol_oxide = _OXIDE_DISSOCIATION_KJ_PER_MOL[oxide].kJ_per_mol
    cf8_kj_per_mol_o2 = cf8_kj_per_mol_oxide * expected_n_oxide
    ellingham_kj_per_mol_o2 = -ellingham_dh
    delta_percent = (
        cf8_kj_per_mol_o2 / ellingham_kj_per_mol_o2 - 1.0
    ) * 100.0

    assert cf8_kj_per_mol_oxide == pytest.approx(
        expected_cf8_kj_per_mol_oxide,
        rel=0.0,
        abs=0.001,
    )
    assert n_metal == pytest.approx(expected_n_metal, rel=0.0, abs=1e-12)
    assert n_oxide == pytest.approx(expected_n_oxide, rel=0.0, abs=1e-12)
    assert cf8_kj_per_mol_o2 == pytest.approx(
        expected_cf8_kj_per_mol_o2,
        rel=0.0,
        abs=0.001,
    )
    assert ellingham_kj_per_mol_o2 == pytest.approx(
        expected_ellingham_kj_per_mol_o2,
        rel=0.0,
        abs=0.001,
    )
    assert delta_percent == pytest.approx(
        expected_delta_percent,
        rel=0.0,
        abs=0.05,
    )

    # One mol O2 worth of evaporated metal exercises the actual CF-8 consumer
    # and must book the same independently normalized dissociation magnitude.
    thermal_result = evaporation_enthalpy_budget(
        {metal: expected_n_metal * metal_molar_mass_g_mol / 1000.0},
        vapor_pressures={
            "metals": {
                metal: {
                    "parent_oxide": oxide,
                    "molar_mass_g_mol": metal_molar_mass_g_mol,
                }
            }
        },
    )
    assert thermal_result["dissociation_by_species_kWh"][metal] * 3600.0 == (
        pytest.approx(expected_cf8_kj_per_mol_o2, rel=0.0, abs=0.001)
    )
