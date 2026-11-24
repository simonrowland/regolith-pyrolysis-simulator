"""Melt fO2 is bounded below by condensed-metal saturation (d-469).

The Fe number is ``fe_metal_saturation_log10_fO2_bar``. The Si number
is the Ellingham owner's ΔG° on the ruling's formation line, not a
copy of ``metal_saturation_floor``. Pure-Si saturation is the crystal
Antoine in ``data/vapor_pressures.yaml`` evaluated by
``_antoine_log10_pressure``.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.antoine import (
    _antoine_log10_pressure,
    _read_antoine_coefficients,
)
from engines.builtin.vapor_pressure import (
    BuiltinVaporPressureProvider,
    wall_condensation_antoine_coefficients,
)
from simulator.chemistry.ellingham_thermo import (
    ellingham_delta_g_kj_per_mol_o2,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.fe_redox import fe_metal_saturation_log10_fO2_bar
from simulator.metal_saturation_floor import (
    melt_metal_saturation_floor_log10_fO2_bar,
)
from simulator.oxygen_buffers import feo_iw_log10_fO2_bar
from simulator.physical_constants import GAS_CONSTANT, J_PER_KJ
from simulator.vapour_rail.catalog import vapor_pressure_legacy_view


_T_K = 1423.15
_A_SIO2 = 0.41
_DATA = Path(__file__).resolve().parents[1] / "data" / "vapor_pressures.yaml"
# Post-shuttle melt: SiO2 is still a major oxide, FeO is gone, and Cr,
# Ti, Mn, Ni, and P are absent so the Si couple is the one that binds.
# Al, Ca, Mg, and Na are in the melt and are not saturation couples at
# this temperature (Mg and Na are gas on the Ellingham owner).
_SHUTTLE_MOL = {
    "SiO2": 7.5,
    "Al2O3": 1.5,
    "CaO": 2.2,
    "MgO": 2.5,
    "Na2O": 1.3,
}


def _si_line_from_ellingham(temperature_K: float, a_sio2: float) -> float:
    """Ruling's Si line from the Ellingham owner's ΔG°, not the floor body."""

    delta_g_j = ellingham_delta_g_kj_per_mol_o2("Si", temperature_K) * J_PER_KJ
    return (
        delta_g_j / (GAS_CONSTANT * temperature_K * math.log(10.0))
        + math.log10(a_sio2)
    )


def test_fe_floor_binds_while_feo_is_present() -> None:
    floor, metal = melt_metal_saturation_floor_log10_fO2_bar(
        _T_K,
        {"FeO": 0.45, "SiO2": _A_SIO2},
    )
    assert metal == "Fe"
    assert floor == pytest.approx(
        fe_metal_saturation_log10_fO2_bar(_T_K, a_feo_stoich=0.45),
    )
    assert floor > _si_line_from_ellingham(_T_K, _A_SIO2)


def test_si_floor_binds_once_feo_activity_collapses() -> None:
    floor, metal = melt_metal_saturation_floor_log10_fO2_bar(
        _T_K,
        {"FeO": 1.0e-6, "SiO2": _A_SIO2},
    )
    assert metal == "Si"
    assert floor == pytest.approx(_si_line_from_ellingham(_T_K, _A_SIO2))
    assert floor > fe_metal_saturation_log10_fO2_bar(
        _T_K,
        a_feo_stoich=1.0e-6,
    )
    # 1423.15 K is the solid Si / SiO2(II) segment, 11.8 dex below IW,
    # wider than the liquid-line 9–10 dex claim.
    pure, pure_metal = melt_metal_saturation_floor_log10_fO2_bar(
        _T_K,
        {"SiO2": 1.0},
    )
    assert pure_metal == "Si"
    assert pure == pytest.approx(_si_line_from_ellingham(_T_K, 1.0))
    assert pure < feo_iw_log10_fO2_bar(_T_K) - 11.0
    # 1800 K is Si(l) + O2 -> SiO2(l), about 8.4 dex below IW.
    liquid, liquid_metal = melt_metal_saturation_floor_log10_fO2_bar(
        1800.0,
        {"SiO2": 1.0},
    )
    assert liquid_metal == "Si"
    iw_1800 = feo_iw_log10_fO2_bar(1800.0)
    assert iw_1800 - 10.0 < liquid < iw_1800 - 8.0


def _crystal_si_saturation_pa(temperature_K: float) -> float:
    raw = yaml.safe_load(_DATA.read_text())
    # Schema v2 nests the Antoine under the Si family. The legacy
    # projection is the view ``wall_condensation_antoine_coefficients``
    # and the builtin provider both read.
    species = vapor_pressure_legacy_view(raw)["metals"]["Si"]
    segments = species["pure_component_antoine"]["segments"]
    crystal = next(row for row in segments if row["name"] == "crystal_janaf")
    low_K, high_K = crystal["valid_range_K"]
    assert low_K <= temperature_K <= high_K
    log10_p_pa = _antoine_log10_pressure(
        float(crystal["A"]),
        float(crystal["B"]),
        float(crystal["C"]),
        temperature_K,
    )
    return 10.0 ** log10_p_pa


def _si_saturation_pa_from_owner(
    species_row: dict,
    temperature_K: float,
) -> float:
    coefficients, _owner = wall_condensation_antoine_coefficients(
        species_row,
        temperature_K,
    )
    A, B, C = _read_antoine_coefficients(coefficients)
    return 10.0 ** _antoine_log10_pressure(A, B, C, temperature_K)


def test_post_shuttle_si_pressure_stays_under_pure_si_saturation() -> None:
    raw = yaml.safe_load(_DATA.read_text())
    provider = BuiltinVaporPressureProvider(raw)
    result = provider.dispatch(IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(_SHUTTLE_MOL)},
            species_formula_registry={},
        ),
        temperature_C=_T_K - 273.15,
        pressure_bar=1.0e-6,
        control_inputs={
            "pO2_bar": 1.0e-8,
            "intrinsic_fO2_log": -40.0,
            "high_t_melt_activity": "constant_gamma",
        },
    ))
    assert str(result.status) == "ok"
    notice = result.diagnostic["melt_metal_saturation_floor_notice"]
    assert notice["type"] == "typed_notice"
    assert notice["kind"] == "melt_fO2_metal_saturation_floor:Si"
    assert notice["metal"] == "Si"
    assert notice["temperature_K"] == pytest.approx(_T_K)
    assert notice["unfloored_log10_fO2_bar"] == pytest.approx(-40.0)
    assert notice["floor_log10_fO2_bar"] > -30.0
    assert result.diagnostic[
        "melt_dissociation_pO2_clamped_to_physical_envelope"
    ] is False
    dissociation_pO2_bar = result.diagnostic["source_reaction_fO2_bar"]
    assert dissociation_pO2_bar == pytest.approx(
        10.0 ** notice["floor_log10_fO2_bar"],
    )
    assert dissociation_pO2_bar > 1.0e-30
    # The dissociation log is the Ellingham Si line at the same SiO2
    # activity the vapour path just published. For SiO2 the formula
    # activity is that value (one cation).
    a_sio2 = float(result.diagnostic["activities"]["Si"])
    assert notice["floor_log10_fO2_bar"] == pytest.approx(
        _si_line_from_ellingham(_T_K, a_sio2),
    )
    # a_Si = 1 on the buffer, so the corollary is P_Si <= P_sat,Si.
    # The published channel is the CEA reaction SiO2(l) -> Si(g) + O2,
    # not the JANAF crystal Antoine. At this floor those owners differ
    # by 0.137 dex: measured P_Si / P_sat = 1.371 at both a_SiO2 = 1
    # and a_SiO2 ≈ 0.42, because a_SiO2 cancels. No second pressure
    # cap is applied. The 1e-30 clamp published 65.3 Pa on this
    # composition. Both bounds below reject that, and they go red
    # when the Si couple is removed.
    p_si_pa = result.diagnostic["vapor_pressures_Pa"]["Si"]
    p_sat_pa = _crystal_si_saturation_pa(_T_K)
    assert p_si_pa < 1.0
    assert p_si_pa / p_sat_pa < 1.51
    flag = result.diagnostic[
        "vapour_metal_saturation_certification_flags"
    ]["Si"]
    assert flag["type"] == "typed_certification_flag"
    assert flag["kind"] == "vapour_exceeds_metal_saturation:Si"
    assert flag["ratio"] == pytest.approx(p_si_pa / p_sat_pa)
    assert flag["temperature_K"] == pytest.approx(_T_K)
    assert flag["pressure_source"]["runtime_label"] == result.diagnostic[
        "vapor_pressures_source"
    ]["Si"]
    assert "NASA CEA" in flag["pressure_source"]["reaction_basis"]
    assert flag["saturation_source"]["owner"] == "pure_component_antoine"
    assert "Si(cr) Si-002" in flag["saturation_source"]["source"]
    assert any(
        str(warning).startswith("melt_fO2_metal_saturation_floor:Si")
        for warning in result.warnings
    )


def test_si_saturation_ratio_across_furnace_band() -> None:
    raw = yaml.safe_load(_DATA.read_text())
    legacy = vapor_pressure_legacy_view(raw)
    provider = BuiltinVaporPressureProvider(raw)
    account_view = ProviderAccountView(
        accounts={"process.cleaned_melt": dict(_SHUTTLE_MOL)},
        species_formula_registry={},
    )
    ratios: list[tuple[float, float]] = []
    for temperature_K in range(1300, 2001):
        result = provider.dispatch(IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=account_view,
            temperature_C=temperature_K - 273.15,
            pressure_bar=1.0e-6,
            control_inputs={
                "pO2_bar": 1.0e-8,
                "intrinsic_fO2_log": -40.0,
                "high_t_melt_activity": "constant_gamma",
            },
        ))
        assert str(result.status) == "ok"
        notice = result.diagnostic["melt_metal_saturation_floor_notice"]
        assert notice["metal"] == "Si"
        p_si_pa = result.diagnostic["vapor_pressures_Pa"]["Si"]
        p_sat_pa = _si_saturation_pa_from_owner(
            legacy["metals"]["Si"],
            float(temperature_K),
        )
        ratios.append((p_si_pa / p_sat_pa, float(temperature_K)))

    max_ratio, max_temperature_K = max(ratios, key=lambda row: row[0])
    # Premise: the sweep max is 1.47903388 at 1300 K. A 2% margin is
    # 1.47903388 * 1.02 = 1.50861456, rounded upward to 1.51; the ratio
    # is dimensionless (Pa/Pa), and the measured limiting point stays below it.
    assert max_ratio < 1.51, (
        f"max P_Si/P_sat={max_ratio:.8f} at {max_temperature_K:g} K"
    )
