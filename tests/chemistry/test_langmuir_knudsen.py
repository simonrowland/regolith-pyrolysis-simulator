"""Langmuir / Knudsen analytical flux model tests."""

from __future__ import annotations

import math

import pytest

from engines.builtin.evaporation_flux import _series_resistance_evaporation_flux_kg_m2_s
from simulator.chemistry.langmuir_knudsen import (
    grounded_alpha,
    knudsen_effusion_flux,
    langmuir_flux,
    pseudo_antoine_p_eq_pa,
    series_flux,
    species_molar_mass_kg_mol,
    validate_against_baseline,
)
from simulator.condensation import GAS_CONSTANT_J_MOL_K
from simulator.transport_constants import FREE_MOLECULAR_KNUDSEN_MIN


_K_BASE = {
    "species": "K",
    "p_eq_pa": 80.0,
    "p_bulk_pa": 5.0,
    "T_surface_K": 1800.0,
    "molar_mass_kg_mol": 0.0390983,
    "alpha": 0.13,
}


def test_langmuir_flux_matches_hk_formula():
    molar_mass = 0.0390983
    T_K = 1800.0
    alpha = 0.13
    p_eq = 80.0
    p_bulk = 5.0
    delta_p = p_eq - p_bulk
    k_hk = math.sqrt(
        molar_mass / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * T_K)
    )
    expected = alpha * delta_p * k_hk
    assert langmuir_flux(
        "K",
        T_K,
        p_eq,
        p_bulk,
        alpha,
        molar_mass_kg_mol=molar_mass,
    ) == pytest.approx(expected, rel=1e-12)


def test_knudsen_effusion_is_alpha_one_langmuir():
    molar_mass = species_molar_mass_kg_mol("Na")
    T_K = 1700.0
    p_eq = 0.596
    assert knudsen_effusion_flux(
        "Na",
        T_K,
        p_eq,
        molar_mass_kg_mol=molar_mass,
    ) == pytest.approx(
        langmuir_flux(
            "Na",
            T_K,
            p_eq,
            0.0,
            1.0,
            molar_mass_kg_mol=molar_mass,
        ),
        rel=1e-12,
    )


def test_series_flux_reduces_to_langmuir_as_kn_goes_to_infinity():
    molar_mass = _K_BASE["molar_mass_kg_mol"]
    result = series_flux(
        species=_K_BASE["species"],
        p_eq_pa=_K_BASE["p_eq_pa"],
        p_bulk_pa=_K_BASE["p_bulk_pa"],
        T_surface_K=_K_BASE["T_surface_K"],
        molar_mass_kg_mol=molar_mass,
        alpha=_K_BASE["alpha"],
        knudsen_number=FREE_MOLECULAR_KNUDSEN_MIN * 1000.0,
        axial_stir_factor=0.0,
        melt_resistance_enabled=False,
        gas_resistance_enabled=False,
    )
    expected = langmuir_flux(
        _K_BASE["species"],
        _K_BASE["T_surface_K"],
        _K_BASE["p_eq_pa"],
        _K_BASE["p_bulk_pa"],
        _K_BASE["alpha"],
        molar_mass_kg_mol=molar_mass,
    )
    assert result.r_gas == 0.0
    assert result.r_melt == 0.0
    assert result.gas_resistance_weight == 0.0
    assert result.flux_kg_s_m2 == pytest.approx(expected, rel=1e-9)
    assert result.langmuir_flux_kg_s_m2 == pytest.approx(expected, rel=1e-12)


def test_series_flux_continuum_limit_is_gas_transport_limited():
    result = series_flux(
        species=_K_BASE["species"],
        p_eq_pa=_K_BASE["p_eq_pa"],
        p_bulk_pa=_K_BASE["p_bulk_pa"],
        T_surface_K=_K_BASE["T_surface_K"],
        molar_mass_kg_mol=_K_BASE["molar_mass_kg_mol"],
        alpha=1.0,
        knudsen_number=1.0e-7,
        overhead_pressure_pa=1000.0,
        axial_stir_factor=0.0,
        radial_stir_factor=1.0,
        melt_resistance_enabled=False,
    )
    delta_p = _K_BASE["p_eq_pa"] - _K_BASE["p_bulk_pa"]
    assert result.r_gas > result.r_interface * 50.0
    assert result.gas_resistance_weight == pytest.approx(1.0, rel=1e-5)
    assert result.flux_kg_s_m2 == pytest.approx(delta_p / result.r_gas, rel=0.02)
    assert result.flux_kg_s_m2 < result.langmuir_flux_kg_s_m2


def test_grounded_alpha_and_p_eq_match_provider_wiring():
    T_K = 1700.0
    alpha_na, _ = grounded_alpha("Na", T_K)
    alpha_k, _ = grounded_alpha("K", T_K)
    alpha_sio, _ = grounded_alpha("SiO", T_K)
    assert alpha_na == pytest.approx(1.0)
    assert alpha_k == pytest.approx(0.13)
    assert alpha_sio == pytest.approx(0.52 * math.exp(-3685.0 / T_K))
    p_na = pseudo_antoine_p_eq_pa("Na", T_K)
    p_k = pseudo_antoine_p_eq_pa("K", T_K)
    p_sio = pseudo_antoine_p_eq_pa("SiO", T_K)
    assert p_na > 0.0
    assert p_k > 0.0
    assert p_sio > 0.0
    provider = _series_resistance_evaporation_flux_kg_m2_s(
        species="Na",
        P_eq_pa=p_na,
        P_bulk_pa=0.0,
        T_surface_K=T_K,
        molar_mass_kg_mol=species_molar_mass_kg_mol("Na"),
        alpha_i=alpha_na,
        knudsen_number=math.inf,
        axial_stir_factor=0.0,
        gas_resistance_enabled=False,
        melt_resistance_enabled=False,
    )
    assert provider.flux_kg_s_m2 == pytest.approx(
        langmuir_flux(
            "Na",
            T_K,
            p_na,
            0.0,
            alpha_na,
            molar_mass_kg_mol=species_molar_mass_kg_mol("Na"),
        ),
        rel=1e-12,
    )


def test_validate_against_baseline_reports_ratios_without_tuning():
    rows = validate_against_baseline()
    assert len(rows) == 3
    by_species = {row.species: row for row in rows}
    for species in ("Na", "K", "SiO"):
        row = by_species[species]
        assert row.modeled_flux_kg_s_m2 > 0.0
        assert row.measured_flux_kg_s_m2 > 0.0
        assert math.isfinite(row.ratio_modeled_over_measured)
        # Grounding rows are self-consistent at alpha=1 HK back-solve basis;
        # ratios near unity confirm the analytical limit wiring, not tuning.
        assert row.ratio_modeled_over_measured == pytest.approx(1.0, rel=0.02)