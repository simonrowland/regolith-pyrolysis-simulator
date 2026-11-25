"""d-383: pure-Fe saturation pressure from NIST-JANAF 4th Fe-008 dfG.

Premise: Fe(ref) -> Fe(g), K = P/p0, and dfG = -RT ln K. The table reference
state includes the solid and liquid Fe phases. Algebra: log10(P/bar) =
-dfG(Fe,g;T) / (R T ln 10), where dfG is the printed Fe-008 formation Gibbs
energy in kJ/mol and R is 8.31441 J mol^-1 K^-1; interpolate log10 P in 1/T.
Unit check: p0 = 0.1 MPa = 1 bar = 1e5 Pa, not 1 atm. The liquid Antoine is
only a cross-check: about 8.04 Pa at 1873.15 K, versus about 8.06 Pa from
Fe-008; Fe-008 gives about 1.01e5 Pa near the 3135 K boil.
"""

from __future__ import annotations

import inspect
import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider, fe008_saturation
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.core import PyrolysisSimulator


_TABLE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "literature"
    / "compilations"
    / "janaf"
    / "tables"
    / "Fe-008.yaml"
)
# JANAF 4th printed gas constant. Written here so a wrong R in the owner fails.
_R_J_PER_MOL_K = 8.31441
_LN10 = math.log(10.0)
_CERTIFIED_FLOOR_K = 298.15
_ANTOINE_A = 10.5800108010
_ANTOINE_B = 16600.8522745789
_ANTOINE_C = -157.2155777135
_ANTOINE_RESIDUAL_DEX = 0.004690522701
_RETIRED_CC_A = 10.670336
_RETIRED_CC_B = 17759.430841
_PRINTED_EXACT_K = (298.15, 1400.0, 1500.0, 1800.0, 1900.0, 3100.0)


def _condensed_reference_rows() -> tuple[tuple[float, float], ...]:
    """Printed (T_K, dfG kJ/mol) rows of the Fe-008 condensed-reference series."""

    document = yaml.safe_load(_TABLE.read_text())
    rows: list[tuple[float, float]] = []
    for row in document["table"]["values"]:
        temperature = row["temperature"]["value"]
        gibbs = row["formation_gibbs_energy"]["value"]
        enthalpy = (row.get("formation_enthalpy") or {}).get("value")
        if temperature is None or gibbs is None:
            continue
        temperature_K = float(temperature)
        gibbs_kJ = float(gibbs)
        if temperature_K < _CERTIFIED_FLOOR_K:
            continue
        if enthalpy is not None and float(enthalpy) == 0.0 and gibbs_kJ == 0.0:
            break
        rows.append((temperature_K, gibbs_kJ))
    return tuple(rows)


def _log10_p_bar(dfg_kJ_mol: float, temperature_K: float) -> float:
    """Brief's closed form. Not a call into the production interpolator."""

    return -dfg_kJ_mol * 1000.0 / (_R_J_PER_MOL_K * temperature_K * _LN10)


def _antoine_draft_pa(temperature_K: float) -> float:
    return 10.0 ** (
        _ANTOINE_A - _ANTOINE_B / (temperature_K + _ANTOINE_C)
    )


def _runtime_fe_reference_pa(vapor_pressure_data: dict, temperature_K: float) -> dict:
    result = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(
        IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": {"FeO": 1.0}},
                species_formula_registry={},
            ),
            temperature_C=temperature_K - 273.15,
            pressure_bar=1e-6,
            control_inputs={
                "pO2_bar": 1e-9,
                "high_t_melt_activity": "constant_gamma",
            },
        )
    )
    assert result.status == "ok"
    return result.diagnostic


def test_fe008_printed_rows_match_the_closed_form() -> None:
    """A printed temperature is that row's dfG, not an interpolation."""

    rows = dict(_condensed_reference_rows())
    for temperature_K in _PRINTED_EXACT_K:
        saturation = fe008_saturation(temperature_K)
        expected_log10_pa = _log10_p_bar(rows[temperature_K], temperature_K) + 5.0
        assert saturation.extrapolated is False
        assert saturation.log10_pressure_pa == expected_log10_pa
        assert saturation.pressure_pa == 10.0 ** expected_log10_pa


def test_fe008_interpolates_log10_p_linearly_in_inverse_temperature() -> None:
    """1450 K is between the printed 1400 K and 1500 K rows."""

    rows = dict(_condensed_reference_rows())
    temperature_K = 1450.0
    left_T, right_T = 1400.0, 1500.0
    left_log = _log10_p_bar(rows[left_T], left_T)
    right_log = _log10_p_bar(rows[right_T], right_T)
    weight = (1.0 / temperature_K - 1.0 / left_T) / (1.0 / right_T - 1.0 / left_T)
    expected_log10_bar = left_log + (right_log - left_log) * weight
    linear_in_T = left_log + (right_log - left_log) * (
        (temperature_K - left_T) / (right_T - left_T)
    )
    saturation = fe008_saturation(temperature_K)
    assert saturation.extrapolated is False
    assert saturation.log10_pressure_pa == expected_log10_bar + 5.0
    assert abs(saturation.log10_pressure_pa - (linear_in_T + 5.0)) > 1.0e-4


def test_fe008_solid_branch_at_1473_K_is_the_hand_computed_bracket() -> None:
    """1473 K is between the printed Fe-008 rows at 1400 K and 1500 K.

    dfG(1400 K) = 209.263 kJ/mol
    dfG(1500 K) = 195.744 kJ/mol
    R = 8.31441 J mol^-1 K^-1
    log10(P/bar) = -dfG_kJ * 1000 / (R * T * ln(10))
    log10(P/bar, 1400) = -7.807595158501941
    log10(P/bar, 1500) = -6.816321628407396
    1/1400 = 0.0007142857142857143
    1/1500 = 0.0006666666666666666
    1/1473 = 0.0006788866259334691
    weight = (1/1473 - 1/1400) / (1/1500 - 1/1400) = 0.7433808553971489
    log10(P/bar, 1473) = -7.807595158501941
        + (-6.816321628407396 - -7.807595158501941) * weight
        = -7.070701393767706
    P/Pa = 10**log10(P/bar) * 1e5 = 0.008497645436488078
    """

    rows = dict(_condensed_reference_rows())
    assert rows[1400.0] == 209.263
    assert rows[1500.0] == 195.744
    saturation = fe008_saturation(1473.0)
    assert saturation.extrapolated is False
    assert saturation.valid_range_K == (298.15, 3100.0)
    assert saturation.pressure_pa == 0.008497645436488078


def test_fe008_sanity_at_1873_k_and_boiling_point() -> None:
    """JANAF scale check: about 8.06 Pa at 1873 K and 1.01e5 Pa at the boil.

    Using the Fe-008 printed rows and the closed form above gives 8.0633 Pa
    at 1873.15 K. The liquid Antoine cross-check gives 8.04 Pa there. The
    end-row 1/T slope gives 100831.94 Pa at 3135.15 K, near the 1 bar boil.
    These are derived sanity checks, not copied run-output pins.
    """

    assert fe008_saturation(1873.15).pressure_pa == pytest.approx(
        8.0632826483911,
        rel=1.0e-12,
    )
    assert _antoine_draft_pa(1873.15) == pytest.approx(8.044, rel=1.0e-3)
    boiling = fe008_saturation(3135.15)
    assert boiling.extrapolated is True
    assert boiling.pressure_pa == pytest.approx(1.01e5, rel=1.0e-2)


def test_fe008_at_1473_K_is_below_the_liquid_antoine() -> None:
    """Direction and order of magnitude only.

    The brief's 0.05-0.3 dex window was sized for a Clausius-Clapeyron
    estimate (~0.17 dex). The printed Fe-008 rows put the Antoine draft
    0.034016 dex above this evaluation at 1473 K, so the 0.05 floor is
    not applied. The upper bound still rejects the retired constant-dH
    anchor, which is about 0.68 dex high here.
    """

    janaf_pa = fe008_saturation(1473.0).pressure_pa
    gap_dex = math.log10(_antoine_draft_pa(1473.0) / janaf_pa)
    assert 0.0 < gap_dex < 0.3


def test_liquid_antoine_cross_check_stays_within_stated_residual() -> None:
    """Over 1809-3134 K the retained Antoine agrees within 0.004690522701 dex."""

    temperatures = [
        temperature_K
        for temperature_K, _gibbs in _condensed_reference_rows()
        if temperature_K >= 1809.0
    ]
    temperatures.extend((1809.0, 3134.0))
    worst = 0.0
    for temperature_K in temperatures:
        janaf_pa = fe008_saturation(temperature_K).pressure_pa
        worst = max(
            worst,
            abs(math.log10(janaf_pa / _antoine_draft_pa(temperature_K))),
        )
    assert worst <= _ANTOINE_RESIDUAL_DEX


def test_fe008_outside_the_printed_band_is_predicted_and_flagged(
    vapor_pressure_data,
) -> None:
    """Below 298.15 K and above 3100 K the end slope is predicted and flagged.

    The provider's pre-existing T < 400 K short-circuit returns an empty
    map for every metal before any saturation law runs. The cold branch
    is therefore pinned on fe008_saturation itself. 3120 K is above the
    printed band and still on the condensed rail, so the provider flag
    is checked there.
    """

    band = fe008_saturation(1800.0).valid_range_K
    assert band == (298.15, 3100.0)
    for temperature_K in (100.0, 250.0, 3120.0, 3200.0):
        saturation = fe008_saturation(temperature_K)
        assert saturation.extrapolated is True
        assert saturation.pressure_pa > 0.0
        assert saturation.valid_range_K == band
    # 250 K is printed on Fe-008 but it is below 298.15 K, so it is not a
    # certified node. The end-slope value must not be that row's closed form.
    document = yaml.safe_load(_TABLE.read_text())
    gibbs_250 = next(
        float(row["formation_gibbs_energy"]["value"])
        for row in document["table"]["values"]
        if float(row["temperature"]["value"]) == 250.0
    )
    closed_250 = 10.0 ** (_log10_p_bar(gibbs_250, 250.0) + 5.0)
    assert abs(
        math.log10(fe008_saturation(250.0).pressure_pa / closed_250)
    ) > 1.0e-3

    diagnostic = _runtime_fe_reference_pa(vapor_pressure_data, 3120.0)
    saturation = fe008_saturation(3120.0)
    provenance = diagnostic["vapor_pressure_numerator_provenance"]["Fe"]
    assert provenance["P_reference_Antoine_Pa"] == saturation.pressure_pa
    assert diagnostic["vapor_pressures_Pa"]["Fe"] > 0.0
    notice = diagnostic["extrapolated_beyond_valid_range_K"]["Fe"]
    assert notice["authority_level"] == "extrapolated"
    assert tuple(notice["valid_range_K"]) == band
    assert "extrapolated_beyond_valid_range_K" in diagnostic[
        "vapor_pressures_source"
    ]["Fe"].split(":")


def test_in_band_provider_uses_fe008_and_does_not_flag(vapor_pressure_data) -> None:
    for temperature_K in (1473.0, 1873.15):
        diagnostic = _runtime_fe_reference_pa(vapor_pressure_data, temperature_K)
        provenance = diagnostic["vapor_pressure_numerator_provenance"]["Fe"]
        assert provenance["pressure_rail"] == "condensed_raoult_psat"
        assert (
            provenance["P_reference_Antoine_Pa"]
            == fe008_saturation(temperature_K).pressure_pa
        )
        assert "Fe" not in diagnostic["extrapolated_beyond_valid_range_K"]


def test_native_fe_partition_uses_fe008_saturation() -> None:
    source = inspect.getsource(PyrolysisSimulator._native_fe_partition_diagnostic)
    assert "fe008_saturation" in source
    assert "_antoine_log10_pressure" not in source


def test_retired_cc_anchor_misses_fe008() -> None:
    """The pre-d-383 constant-dH anchor is not this evaluation."""

    for temperature_K in (1873.15, 2023.15):
        retired_pa = 10.0 ** (_RETIRED_CC_A - _RETIRED_CC_B / temperature_K)
        janaf_pa = fe008_saturation(temperature_K).pressure_pa
        assert abs(math.log10(retired_pa / janaf_pa)) > 0.2
