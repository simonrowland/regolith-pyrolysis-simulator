"""d-383: pure-Fe saturation pressure against NIST-JANAF 4th Fe(l)/Fe(g).

The oracle reads printed log Kf from Fe-006 and Fe-008 and interpolates
each column in 1/T. It does not read the Antoine coefficients. Restoring
the retired Clausius-Clapeyron anchor (A=10.670336, B=17759.430841, C=0)
misses this oracle by about 0.28 dex at 1873.15 K and turns
``test_runtime_fe_psat_matches_janaf_liquid_logkf`` red.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView


_TABLES = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "literature"
    / "compilations"
    / "janaf"
    / "tables"
)
_JANAF_P0_PA = 1.0e5
# Max |log10(P_fit / P_JANAF)| of the stored liquid fit versus 1/T
# interpolation of log Kf on 1809-3134 K. d-383 keeps the Antoine
# evaluator only because this is <= 0.01 dex.
_FE_JANAF_BAND_RESIDUAL_DEX = 0.004690522701
_LIQUID_BAND_K = (1809.0, 3134.0)
_PIN_TEMPERATURES_K = (1873.15, 2023.15)
_RETIRED_CC_A = 10.670336
_RETIRED_CC_B = 17759.430841


def _logkf_rows(table_id: str) -> tuple[tuple[float, float], ...]:
    document = yaml.safe_load((_TABLES / f"{table_id}.yaml").read_text())
    rows: list[tuple[float, float]] = []
    for row in document["table"]["values"]:
        temperature = row["temperature"]["value"]
        log_kf = row["log10_formation_equilibrium_constant"]["value"]
        if temperature is None or log_kf is None:
            continue
        rows.append((float(temperature), float(log_kf)))
    rows.sort()
    return tuple(rows)


def _interp_logkf_inv_t(
    rows: tuple[tuple[float, float], ...],
    temperature_K: float,
) -> float:
    """Linear interpolation of printed log Kf in 1/T."""

    if temperature_K < rows[0][0] or temperature_K > rows[-1][0]:
        raise ValueError(f"{temperature_K} K is outside {rows[0][0]}-{rows[-1][0]}")
    for (left_T, left_y), (right_T, right_y) in zip(rows, rows[1:]):
        if left_T <= temperature_K <= right_T:
            if temperature_K == left_T:
                return left_y
            if temperature_K == right_T:
                return right_y
            left_u = 1.0 / left_T
            right_u = 1.0 / right_T
            weight = (1.0 / temperature_K - left_u) / (right_u - left_u)
            return left_y + (right_y - left_y) * weight
    raise ValueError(temperature_K)


def janaf_fe_liquid_psat_pa(temperature_K: float) -> float:
    """Pure liquid Fe saturation pressure from JANAF-4th Fe-006 and Fe-008.

    Premise: Fe(l) = Fe(g) with unit liquid activity. Standard pressure is
    the table p° of 0.1 MPa.
    Algebra: log10(P/bar) = log Kf(Fe(g)) - log Kf(Fe(l)), each log Kf
    interpolated in 1/T between adjacent printed rows;
    P/Pa = 10**log10(P/bar) * 1e5.
    Unit check: 1 bar = 1e5 Pa, so the factor is 1e5 and not 101325.
    """

    log10_p_bar = _interp_logkf_inv_t(
        _logkf_rows("Fe-008"), temperature_K
    ) - _interp_logkf_inv_t(_logkf_rows("Fe-006"), temperature_K)
    return 10.0 ** log10_p_bar * _JANAF_P0_PA


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


def test_janaf_helper_matches_printed_fe_logkf_nodes() -> None:
    """A shared node is the printed log Kf difference, not an interpolation."""

    liquid = dict(_logkf_rows("Fe-006"))
    gas = dict(_logkf_rows("Fe-008"))
    for temperature_K in (1900.0, 2000.0, 3100.0):
        expected_pa = 10.0 ** (gas[temperature_K] - liquid[temperature_K]) * _JANAF_P0_PA
        assert janaf_fe_liquid_psat_pa(temperature_K) == pytest.approx(
            expected_pa, rel=0.0, abs=0.0
        )


def test_retired_cc_anchor_misses_janaf_liquid_psat() -> None:
    """The pre-d-383 constant-dH anchor is the mutation this pin must reject."""

    for temperature_K, minimum_dex in ((1873.15, 0.2), (2023.15, 0.2)):
        retired_pa = 10.0 ** (_RETIRED_CC_A - _RETIRED_CC_B / temperature_K)
        janaf_pa = janaf_fe_liquid_psat_pa(temperature_K)
        assert abs(math.log10(retired_pa / janaf_pa)) > minimum_dex


def test_runtime_fe_psat_matches_janaf_liquid_logkf(vapor_pressure_data) -> None:
    sidecar = vapor_pressure_data["metals"]["Fe"]["pure_component_antoine"]
    stated = sidecar.get("max_abs_log10_residual_vs_invT_band")
    if stated is not None:
        assert float(stated) == pytest.approx(_FE_JANAF_BAND_RESIDUAL_DEX, abs=1e-15)
        assert float(stated) <= 0.01
    tolerance = _FE_JANAF_BAND_RESIDUAL_DEX if stated is None else float(stated)

    for temperature_K in _PIN_TEMPERATURES_K:
        diagnostic = _runtime_fe_reference_pa(vapor_pressure_data, temperature_K)
        provenance = diagnostic["vapor_pressure_numerator_provenance"]["Fe"]
        assert provenance["pressure_rail"] == "condensed_raoult_psat"
        runtime_pa = provenance["P_reference_Antoine_Pa"]
        janaf_pa = janaf_fe_liquid_psat_pa(temperature_K)
        assert abs(math.log10(runtime_pa / janaf_pa)) <= tolerance
        assert "Fe" not in diagnostic["extrapolated_beyond_valid_range_K"]


def test_fe_below_janaf_liquid_band_is_predicted_and_flagged(
    vapor_pressure_data,
) -> None:
    temperature_K = 1700.0
    diagnostic = _runtime_fe_reference_pa(vapor_pressure_data, temperature_K)
    provenance = diagnostic["vapor_pressure_numerator_provenance"]["Fe"]

    assert provenance["P_reference_Antoine_Pa"] > 0.0
    assert diagnostic["vapor_pressures_Pa"]["Fe"] > 0.0
    notice = diagnostic["extrapolated_beyond_valid_range_K"]["Fe"]
    assert notice["authority_level"] == "extrapolated"
    assert notice["valid_range_K"] == _LIQUID_BAND_K
    assert notice["temperature_K"] == pytest.approx(temperature_K)
    assert diagnostic["vapor_pressures_source"]["Fe"].endswith(
        "extrapolated_beyond_valid_range_K"
    )
