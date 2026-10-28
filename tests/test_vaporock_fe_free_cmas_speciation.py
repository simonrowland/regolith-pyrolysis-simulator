"""VapoRock refuses a CMAS melt outside the MELTS endmember simplex.

Mathieu 2009 admitted row 1 is CaO-rich and Fe-free. Its MELTS projection
drives the dependent SiO2 endmember mole negative (CaSiO3 + Mg2SiO4 +
Na2SiO3 oversubscribe Si). That is outside the liquid simplex. Absent
Fe, Ti, K and Cr are not the cause: an in-simplex Fe-free melt stays
finite, and those absent oxides are the -inf inventory mask.

The adapter refuses before speciation. Status stays not_converged. The
detail string names the endmember and its mole. No partial pressure is
returned. The Fe-bearing pin is the installed engine's log10(bar) table
for an in-simplex composition and must stay bit-identical.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tests.engine_availability import thermoengine_package_available

# Mathieu 2009, Table III.1, page 145, analysed ACMS13/CMS8.
# T = 1673.15 K, fO2 = 2.11e-6 atm. Wt% are the printed oxides.
MATHIEU_ROW1_WT_PCT = {
    "SiO2": 49.1,
    "Al2O3": 0.05,
    "CaO": 41.29,
    "MgO": 5.06,
    "Na2O": 4.5,
}
# In-simplex Fe-free control from the t1539 review. Every MELTS
# endmember mole is non-negative.
FE_FREE_IN_SIMPLEX_WT_PCT = {
    "SiO2": 70.0,
    "Al2O3": 15.0,
    "CaO": 8.0,
    "MgO": 5.0,
    "Na2O": 2.0,
}
# In-simplex control: every MELTS endmember mole is non-negative.
FE_BEARING_CONTROL_WT_PCT = {
    "SiO2": 50.0,
    "TiO2": 1.0,
    "Al2O3": 15.0,
    "FeO": 10.0,
    "MgO": 8.0,
    "CaO": 10.0,
    "Na2O": 3.0,
    "K2O": 1.0,
}
T_K = 1673.15
LOG10_FO2 = math.log10(2.11e-6)
T_C = T_K - 273.15

# Installed VapoRock (pin 0159678 + patches 0001 and 0002).
# log10(partial pressure / bar). An in-simplex composition, so the
# out-of-simplex refusal does not apply.
FE_BEARING_CONTROL_LOG10_BAR = {
    "O(g)": -7.3133208026195,
    "O2(g)": -5.675717544702314,
    "Mg(g)": -10.141884168528835,
    "MgO(g)": -11.92542961073669,
    "Mg2(g)": -23.980303188021544,
    "Ca(g)": -14.783281579197523,
    "CaO(g)": -15.538723359292959,
    "Ca2(g)": -32.503742492794174,
    "Al(g)": -17.783231377048775,
    "AlO(g)": -15.089915706282898,
    "AlO2(g)": -16.4724617043426,
    "Al2(g)": -35.19930593289172,
    "Al2O(g)": -22.685359675576372,
    "Al2O2(g)": -21.45589636458047,
    "Si(g)": -20.240366165551002,
    "SiO(g)": -9.18101188364049,
    "SiO2(g)": -9.918235407251393,
    "Si2(g)": -36.51553646202836,
    "Si3(g)": -50.98627328188632,
    "Na(g)": -5.457345426519034,
    "NaO(g)": -8.967775385546558,
    "Na2(g)": -12.826405525093824,
    "K(g)": -6.326439531626815,
    "KO(g)": -9.866132472177139,
    "K2(g)": -14.816784988326143,
    "Fe(g)": -8.123076040070867,
    "FeO(g)": -8.248603594324974,
    "TiO(g)": -15.659847913753847,
    "Ti(g)": -23.197179546201472,
    "TiO2(g)": -11.254624340937285,
    "Cr(g)": float("-inf"),
    "CrO(g)": float("-inf"),
    "CrO2(g)": float("-inf"),
    "CrO3(g)": float("-inf"),
    "Mn(g)": float("-inf"),
    "Ni(g)": float("-inf"),
    "Co(g)": float("-inf"),
    "NiO(g)": float("-inf"),
}

pytestmark = pytest.mark.skipif(
    not thermoengine_package_available(),
    reason="ThermoEngine dylibs/package unavailable on this machine",
)


@pytest.fixture(scope="module")
def vaporock_system():
    from simulator.engine_local_config import setup_thermoengine_dylib_path

    setup_thermoengine_dylib_path()
    import vaporock

    return vaporock.System()


def _log10_bar(system, composition_wt_pct):
    system.set_melt_comp(dict(composition_wt_pct))
    table = system.eval_gas_abundances(T_K, LOG10_FO2)
    return table.iloc[:, 0].astype(float)


def test_fe_bearing_control_speciation_matches_pre_fix_pin(vaporock_system):
    """In-simplex Fe-bearing rows are the bit-identical control."""
    column = _log10_bar(vaporock_system, FE_BEARING_CONTROL_WT_PCT)
    moles = np.asarray(vaporock_system.mol_liq, dtype=float).ravel()
    assert np.all(moles >= 0.0)
    assert list(column.index) == list(FE_BEARING_CONTROL_LOG10_BAR)
    for species, expected in FE_BEARING_CONTROL_LOG10_BAR.items():
        actual = float(column[species])
        if math.isinf(expected):
            assert math.isinf(actual) and actual < 0.0
        else:
            assert actual == expected


def test_in_simplex_fe_free_cmas_speciation_is_finite(vaporock_system):
    """An Fe-free melt inside the simplex still has finite gas rows."""
    from simulator.melt_backend.vaporock import (
        _melts_liquid_out_of_simplex_detail,
    )

    column = _log10_bar(vaporock_system, FE_FREE_IN_SIMPLEX_WT_PCT)
    assert _melts_liquid_out_of_simplex_detail(vaporock_system) is None
    moles = np.asarray(vaporock_system.mol_liq, dtype=float).ravel()
    assert np.all(moles >= 0.0)
    values = column.to_numpy(dtype=float)
    assert values.size == 38
    assert int(np.isnan(values).sum()) == 0
    assert int(np.isfinite(values).sum()) == 22
    assert int(np.isneginf(values).sum()) == 16
    assert math.isfinite(float(column["Na(g)"]))
    assert math.isfinite(float(column["SiO(g)"]))

    from simulator.engine_local_config import setup_thermoengine_dylib_path
    from simulator.melt_backend.vaporock import VapoRockBackend

    setup_thermoengine_dylib_path()
    backend = VapoRockBackend()
    assert backend.initialize({})
    result = backend.equilibrate(
        T_C,
        composition_kg=dict(FE_FREE_IN_SIMPLEX_WT_PCT),
        fO2_log=LOG10_FO2,
        pressure_bar=1e-6,
    )
    speciation = result.vaporock_full_speciation_Pa
    assert result.status == "non_authoritative"
    assert math.isfinite(speciation["Na"]) and speciation["Na"] > 0.0
    assert not any(
        "melts_liquid_endmember_out_of_simplex" in warning
        for warning in result.warnings
    )


def test_mathieu_row1_adapter_refuses_out_of_simplex():
    """Row 1 is a typed not_converged refusal, not a finite pressure."""
    from simulator.engine_local_config import setup_thermoengine_dylib_path
    from simulator.melt_backend.vaporock import (
        VapoRockBackend,
        _melts_liquid_out_of_simplex_detail,
    )

    setup_thermoengine_dylib_path()
    import vaporock

    system = vaporock.System()
    system.set_melt_comp(dict(MATHIEU_ROW1_WT_PCT))
    detail = _melts_liquid_out_of_simplex_detail(system)
    assert detail is not None
    assert detail.startswith("melts_liquid_endmember_out_of_simplex: ")
    sio2_text = detail.split("SiO2=", 1)[1].split(",", 1)[0]
    assert float(sio2_text) < 0.0

    backend = VapoRockBackend()
    assert backend.initialize({})
    result = backend.equilibrate(
        T_C,
        composition_kg=dict(MATHIEU_ROW1_WT_PCT),
        fO2_log=LOG10_FO2,
        pressure_bar=1e-6,
    )
    speciation = getattr(result, "vaporock_full_speciation_Pa", {})
    assert result.status == "not_converged"
    assert result.vapor_pressures_Pa == {}
    assert speciation == {}
    assert detail in result.warnings
    assert result.diagnostics.get("detail") == detail
    assert "Na" not in speciation


# Mathieu 2009, Table IV.5, page 179 (CMAS23-CMAS27) and Table IV.9,
# pages 183–184 (SPM). Printed oxides. SiO2 is below 30 wt%, and the
# MELTS SiO2 endmember mole is negative. Same T and fO2 as Table III.1.
MATHIEU_OUT_OF_BAND_OUT_OF_SIMPLEX = (
    (
        "IV.5 CMAS23",
        {"SiO2": 8.24, "Al2O3": 3.96, "CaO": 18.98, "MgO": 44.47, "Na2O": 24.34},
    ),
    (
        "IV.5 CMAS24",
        {"SiO2": 6.9, "Al2O3": 9.71, "CaO": 19.12, "MgO": 41.93, "Na2O": 22.32},
    ),
    (
        "IV.5 CMAS25",
        {"SiO2": 7.4, "Al2O3": 15.45, "CaO": 16.35, "MgO": 38.17, "Na2O": 22.62},
    ),
    (
        "IV.5 CMAS26",
        {"SiO2": 6.23, "Al2O3": 10.06, "CaO": 18.34, "MgO": 41.98, "Na2O": 23.4},
    ),
    (
        "IV.5 CMAS27",
        {"SiO2": 11.4, "Al2O3": 12.62, "CaO": 12.62, "MgO": 39.61, "Na2O": 23.74},
    ),
    (
        "IV.9 SPM",
        {"SiO2": 13.44, "Al2O3": 34.79, "CaO": 43.14, "MgO": 8.46, "Na2O": 0.18},
    ),
)


@pytest.mark.parametrize(
    ("label", "composition_wt_pct"),
    MATHIEU_OUT_OF_BAND_OUT_OF_SIMPLEX,
    ids=[item[0] for item in MATHIEU_OUT_OF_BAND_OUT_OF_SIMPLEX],
)
def test_out_of_band_out_of_simplex_refuses_without_commissioning_notice(
    label, composition_wt_pct
):
    """A simplex refusal never calls the engine, so it carries no notice."""
    from simulator.engine_local_config import setup_thermoengine_dylib_path
    from simulator.melt_backend.vaporock import VapoRockBackend

    setup_thermoengine_dylib_path()
    backend = VapoRockBackend()
    assert backend.initialize({})
    result = backend.equilibrate(
        T_C,
        composition_kg=dict(composition_wt_pct),
        fO2_log=LOG10_FO2,
        pressure_bar=1e-6,
    )
    speciation = getattr(result, "vaporock_full_speciation_Pa", {})
    assert result.status == "not_converged", label
    assert result.vapor_pressures_Pa == {}, label
    assert speciation == {}, label
    assert result.diagnostics.get("detail", "").startswith(
        "melts_liquid_endmember_out_of_simplex: "
    ), label
    assert "SiO2=" in result.diagnostics["detail"], label
    assert result.diagnostics["detail"] in result.warnings, label
    assert "commissioning_notice" not in result.diagnostics, label
    assert "authority" not in result.diagnostics, label
    assert "certified_band" not in result.diagnostics, label
    assert not any(
        "CommissioningNotice" in warning or "engine will run" in warning
        for warning in result.warnings
    ), label
