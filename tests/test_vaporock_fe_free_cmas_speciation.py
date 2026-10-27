"""VapoRock speciation for an Fe-free CMAS melt must stay finite.

Mathieu 2009 admitted row 1 is CaO-rich and Fe-free. Its MELTS endmember
projection drives the dependent SiO2 mole negative. LiquidMelts then
evaluates log(x) of that negative mole, and the NaN spreads through every
present-oxide gas row (22 NaN, 16 -inf). Absent Fe, Ti, K and Cr on an
in-simplex melt do not do this: they are the -inf inventory mask.

The Fe-bearing pin is the installed engine's log10(bar) table for an
in-simplex composition, recorded before the drop-out fix. That table must
stay bit-identical.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tests.engine_availability import thermoengine_package_available

# Mathieu 2009 admitted row 1, docs-private scorability table.
# T = 1673.15 K, fO2 = 2.11e-6 atm. Wt% are the printed oxides.
MATHIEU_ROW1_WT_PCT = {
    "SiO2": 49.1,
    "Al2O3": 0.05,
    "CaO": 41.29,
    "MgO": 5.06,
    "Na2O": 4.5,
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

# Installed VapoRock (pin 0159678 + patches 0001 and 0002) before the
# non-positive endmember drop-out. log10(partial pressure / bar).
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


def test_mathieu_row1_fe_free_cmas_speciation_is_finite(vaporock_system):
    """Present-oxide rows are finite; absent-oxide rows stay -inf."""
    column = _log10_bar(vaporock_system, MATHIEU_ROW1_WT_PCT)
    values = column.to_numpy(dtype=float)
    assert values.size == 38
    assert int(np.isnan(values).sum()) == 0
    assert int(np.isfinite(values).sum()) == 22
    assert int(np.isneginf(values).sum()) == 16
    assert math.isfinite(float(column["Na(g)"]))
    assert math.isfinite(float(column["SiO(g)"]))
    assert math.isclose(
        float(column["O2(g)"]), LOG10_FO2, rel_tol=0.0, abs_tol=1e-12
    )
    for species in ("Fe(g)", "K(g)", "Ti(g)", "Cr(g)", "Mn(g)"):
        actual = float(column[species])
        assert math.isinf(actual) and actual < 0.0


def test_mathieu_row1_adapter_returns_finite_partial_pressures():
    """The adapter must surface the engine rows, not replace NaN with a fill."""
    from simulator.engine_local_config import setup_thermoengine_dylib_path
    from simulator.melt_backend.vaporock import VapoRockBackend

    setup_thermoengine_dylib_path()
    backend = VapoRockBackend()
    assert backend.initialize({})
    result = backend.equilibrate(
        T_C,
        composition_kg=dict(MATHIEU_ROW1_WT_PCT),
        fO2_log=LOG10_FO2,
        pressure_bar=1e-6,
    )
    speciation = result.vaporock_full_speciation_Pa
    assert result.status == "non_authoritative"
    assert math.isfinite(speciation["Na"]) and speciation["Na"] > 0.0
    assert math.isfinite(speciation["SiO"]) and speciation["SiO"] > 0.0
    # Absent Fe is the inventory mask (0 Pa), not a clamped NaN.
    assert speciation["Fe"] == 0.0
    assert all(math.isfinite(value) for value in speciation.values())
