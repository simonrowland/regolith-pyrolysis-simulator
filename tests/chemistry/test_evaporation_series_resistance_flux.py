"""Source-side series-resistance evaporation flux tests."""

from __future__ import annotations

import math

import pytest

from engines.builtin.evaporation_flux import (
    BuiltinEvaporationFluxProvider,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import GAS_CONSTANT_J_MOL_K
from simulator.state import MAX_STIR_FACTOR
from simulator.transport_constants import FREE_MOLECULAR_KNUDSEN_MIN


_K_BASE = {
    "species": "K",
    "P_eq_pa": 80.0,
    "P_bulk_pa": 5.0,
    "T_surface_K": 1800.0,
    "molar_mass_kg_mol": 0.0390983,
    "alpha_i": 0.13,
    "knudsen_number": 1.0,
    "pipe_diameter_m": 0.12,
    "overhead_pressure_pa": 1000.0,
    "axial_stir_factor": 1.0,
    "radial_stir_factor": 1.0,
    "cold_skull_envelope": {"frozen_skull_stir_ceiling": MAX_STIR_FACTOR},
    "carrier_gas": "N2",
    "T_gas_K": 1800.0,
    "melt_resistance_enabled": True,
}


def _evap(**overrides):
    kwargs = dict(_K_BASE)
    kwargs.update(overrides)
    return _series_resistance_evaporation_flux_kg_m2_s(**kwargs)


def test_free_molecular_limit_recovers_intrinsic_alpha_hk():
    result = _evap(
        knudsen_number=FREE_MOLECULAR_KNUDSEN_MIN * 1000.0,
        gas_resistance_enabled=False,
        melt_resistance_enabled=False,
    )

    delta_p = _K_BASE["P_eq_pa"] - _K_BASE["P_bulk_pa"]
    k_hk = math.sqrt(
        _K_BASE["molar_mass_kg_mol"]
        / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * _K_BASE["T_surface_K"])
    )
    expected = _K_BASE["alpha_i"] * delta_p * k_hk

    assert result.r_gas == 0.0
    assert result.r_melt == 0.0
    assert result.flux_kg_s_m2 == pytest.approx(expected, rel=1e-12)
    assert result.alpha_effective == pytest.approx(_K_BASE["alpha_i"], rel=1e-12)


def test_continuum_limit_is_transport_limited_by_gas_resistance():
    result = _evap(
        alpha_i=1.0,
        knudsen_number=1.0e-7,
        radial_stir_factor=1.0,
        melt_resistance_enabled=False,
    )

    delta_p = _K_BASE["P_eq_pa"] - _K_BASE["P_bulk_pa"]
    assert result.r_gas > result.r_interface * 50.0
    assert result.gas_resistance_weight == pytest.approx(1.0, rel=1e-5)
    assert result.flux_kg_s_m2 == pytest.approx(delta_p / result.r_gas, rel=0.02)


def test_alpha_effective_never_exceeds_intrinsic_alpha_across_kn_and_stir():
    kn_values = [0.0, 1.0e-7, 0.01, 0.1, 1.0, 10.0, math.inf]
    stir_values = [0.0, 1.0, MAX_STIR_FACTOR, 1000.0]

    for alpha in (0.02, 0.13, 1.0):
        for kn in kn_values:
            for axial in stir_values:
                for radial in stir_values:
                    result = _evap(
                        alpha_i=alpha,
                        knudsen_number=kn,
                        axial_stir_factor=axial,
                        radial_stir_factor=radial,
                    )
                    assert 0.0 <= result.alpha_effective <= alpha + 1e-15


def test_resistances_move_monotonically_with_kn_and_stir_axes():
    gas_by_kn = [
        _evap(
            knudsen_number=kn,
            radial_stir_factor=1.0,
            melt_resistance_enabled=False,
        ).r_gas
        for kn in (1.0e-7, 1.0e-4, 0.01, 0.1, 1.0, 10.0, math.inf)
    ]
    assert gas_by_kn == sorted(gas_by_kn, reverse=True)

    melt_by_axial = [
        _evap(
            axial_stir_factor=axial,
            gas_resistance_enabled=False,
        ).r_melt
        for axial in (0.0, 1.0, 4.0, MAX_STIR_FACTOR, 1000.0)
    ]
    assert melt_by_axial == sorted(melt_by_axial, reverse=True)

    gas_by_radial = [
        _evap(
            knudsen_number=1.0e-7,
            radial_stir_factor=radial,
            melt_resistance_enabled=False,
        ).r_gas
        for radial in (0.0, 1.0, 4.0, MAX_STIR_FACTOR, 1000.0)
    ]
    assert gas_by_radial == sorted(gas_by_radial, reverse=True)


def test_anti_exploit_stir_bounds_and_defensive_clamps():
    max_axial = _evap(
        axial_stir_factor=MAX_STIR_FACTOR,
        gas_resistance_enabled=False,
    )
    for axial in (11.0, 1000.0):
        assert _evap(
            axial_stir_factor=axial,
            gas_resistance_enabled=False,
        ).flux_kg_s_m2 == pytest.approx(max_axial.flux_kg_s_m2, rel=1e-12)

    max_radial = _evap(
        knudsen_number=1.0e-7,
        radial_stir_factor=MAX_STIR_FACTOR,
        melt_resistance_enabled=False,
    )
    for radial in (11.0, 1000.0):
        assert _evap(
            knudsen_number=1.0e-7,
            radial_stir_factor=radial,
            melt_resistance_enabled=False,
        ).flux_kg_s_m2 == pytest.approx(max_radial.flux_kg_s_m2, rel=1e-12)

    axial_zero = _evap(axial_stir_factor=0.0, gas_resistance_enabled=False)
    radial_zero = _evap(
        radial_stir_factor=0.0,
        knudsen_number=1.0e-7,
        melt_resistance_enabled=False,
    )
    for bad in (float("nan"), float("inf"), True):
        bad_axial = _evap(axial_stir_factor=bad, gas_resistance_enabled=False)
        bad_radial = _evap(
            radial_stir_factor=bad,
            knudsen_number=1.0e-7,
            melt_resistance_enabled=False,
        )
        assert bad_axial.axial_stir_clamped is True
        assert bad_axial.flux_kg_s_m2 == pytest.approx(
            axial_zero.flux_kg_s_m2, rel=1e-12
        )
        assert bad_radial.radial_stir_clamped is True
        assert bad_radial.flux_kg_s_m2 == pytest.approx(
            radial_zero.flux_kg_s_m2, rel=1e-12
        )


@pytest.mark.parametrize("p_bulk", [_K_BASE["P_eq_pa"], _K_BASE["P_eq_pa"] * 2.0])
def test_double_count_guard_zeroes_nonpositive_driving_pressure(p_bulk):
    result = _evap(P_bulk_pa=p_bulk)

    assert result.flux_kg_s_m2 == 0.0


def test_missing_alpha_policy_uses_baseline_diagnostic_only():
    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"Cr2O3": 10.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=1700.0,
        pressure_bar=1.0e-6,
        fO2_log=None,
        control_inputs={
            "vapor_pressures_Pa": {"Cr": 100.0},
            "overhead_partials_Pa": {"Cr": 0.0},
            "molar_mass_kg_mol": {"Cr": 0.052},
            "stoich_by_species": {
                "Cr": {
                    "parent_oxide": "Cr2O3",
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                }
            },
            "available_oxide_kg": {"Cr": 10.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": {"axial": 1000.0, "radial": 1000.0},
            "alpha": {},
        },
    )

    result = provider.dispatch(request)

    assert result.status == "unavailable"
    assert result.diagnostic["evaporation_flux_kg_hr"] == {}
    missing = result.diagnostic["missing_alpha"]["Cr"]
    assert missing["policy"] == "fail_loud_missing_alpha"
    assert missing["baseline_alpha_1_rate_kg_hr"] > 0.0
