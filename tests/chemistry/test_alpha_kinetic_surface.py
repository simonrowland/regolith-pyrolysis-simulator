"""Regression tests for the YAML-backed evaporation-alpha kinetic surface."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import alpha_s
from simulator.evaporation import (
    _load_evaporation_alpha_envelope_by_species,
    _load_evaporation_alpha_by_species,
)
from tests.chemistry.corpus_fixtures import alpha_envelope_anchors


REPO_ROOT = Path(__file__).resolve().parents[2]
VAPOR_PRESSURES_PATH = REPO_ROOT / "data" / "vapor_pressures.yaml"
EXPECTED_ALPHA_BY_SPECIES = {
    "Fe": 0.02,
    "Mg": 0.20,
    "Na": 1.0,
    "K": 0.13,
    "Ca": 0.90,
    "Al": 0.30,
    "Si": 1.0,
    "Ti": 0.80,
    "Cr": 0.90,
}
SIO_ALPHA_FORM_T_K = 1500.0 + 273.15
SIO_ALPHA_AT_1500C = 0.52 * math.exp(-3685.0 / SIO_ALPHA_FORM_T_K)


def _vapor_pressure_data() -> dict:
    return yaml.safe_load(VAPOR_PRESSURES_PATH.read_text())


def test_alpha_surface_loads_expected_species_values():
    alpha_by_species = _load_evaporation_alpha_by_species(
        _vapor_pressure_data()
    )

    assert set(EXPECTED_ALPHA_BY_SPECIES) <= set(alpha_by_species)
    for species, expected_alpha in EXPECTED_ALPHA_BY_SPECIES.items():
        assert alpha_by_species[species] == pytest.approx(expected_alpha)
    assert alpha_s(
        "SiO",
        SIO_ALPHA_FORM_T_K,
        {"coefficient_spec": alpha_by_species["SiO"]},
    ) == pytest.approx(SIO_ALPHA_AT_1500C)


def test_alpha_surface_sources_and_envelopes_are_present():
    anchors = {
        anchor.species: anchor
        for anchor in alpha_envelope_anchors()
    }

    assert set(EXPECTED_ALPHA_BY_SPECIES) | {"SiO"} <= set(anchors)
    for species in EXPECTED_ALPHA_BY_SPECIES:
        anchor = anchors[species]
        assert anchor.source.strip()
        assert anchor.T_band_K[0] <= anchor.T_band_K[1]
        assert anchor.envelope[0] <= anchor.value <= anchor.envelope[1]


def test_sio_alpha_stays_inside_literature_envelope():
    anchors = {
        anchor.species: anchor
        for anchor in alpha_envelope_anchors()
    }
    sio = anchors["SiO"]

    assert sio.envelope == pytest.approx((0.003, 0.067))
    assert sio.envelope[0] <= sio.value <= sio.envelope[1]


def test_evaporation_flux_diagnostic_traces_alpha_by_species():
    alpha_by_species = _load_evaporation_alpha_by_species(
        _vapor_pressure_data()
    )
    species = ("SiO", *EXPECTED_ALPHA_BY_SPECIES)
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 10.0}},
            species_formula_registry={},
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            "vapor_pressures_Pa": {name: 100.0 for name in species},
            "overhead_partials_Pa": {},
            "molar_mass_kg_mol": {name: 0.05 for name in species},
            "stoich_by_species": {
                name: {
                    "parent_oxide": "SiO2",
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                }
                for name in species
            },
            "available_oxide_kg": {name: 100.0 for name in species},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": alpha_by_species,
            "alpha_envelope": _load_evaporation_alpha_envelope_by_species(
                _vapor_pressure_data()
            ),
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)
    alpha_used = result.diagnostic["alpha_used_by_species"]
    uncertainty = result.diagnostic["flux_uncertainty_pct"]

    for name, expected_alpha in EXPECTED_ALPHA_BY_SPECIES.items():
        assert alpha_used[name] == pytest.approx(expected_alpha)
        assert uncertainty[name] >= 0.0
    assert alpha_used["SiO"] == pytest.approx(SIO_ALPHA_AT_1500C)
    assert (
        result.diagnostic["alpha_s_evaluation_by_species"]["SiO"][
            "alpha_s_form"
        ]
        == "arrhenius"
    )
    assert uncertainty["SiO"] >= 0.0


def test_new_proxy_species_flux_scales_with_yaml_alpha():
    alpha_by_species = _load_evaporation_alpha_by_species(
        _vapor_pressure_data()
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"CaO": 10.0, "TiO2": 10.0}},
            species_formula_registry={},
        ),
        temperature_C=1700.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            "vapor_pressures_Pa": {"Ca": 100.0, "Ti": 100.0},
            "overhead_partials_Pa": {},
            "molar_mass_kg_mol": {"Ca": 0.05, "Ti": 0.05},
            "stoich_by_species": {
                species: {
                    "parent_oxide": parent,
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                }
                for species, parent in {"Ca": "CaO", "Ti": "TiO2"}.items()
            },
            "available_oxide_kg": {"Ca": 10.0, "Ti": 10.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": alpha_by_species,
            "evaporation_series_resistance": {
                "gas_resistance_enabled": False,
                "melt_resistance_enabled": False,
            },
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)
    flux = result.diagnostic["evaporation_flux_kg_hr"]

    assert result.status == "ok"
    assert flux["Ti"] / flux["Ca"] == pytest.approx(0.80 / 0.90)


def test_cro2_missing_alpha_refuses_nontrivial_flux_by_default():
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"Cr2O3": 10.0}},
            species_formula_registry={},
        ),
        temperature_C=1700.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            "vapor_pressures_Pa": {"CrO2": 100.0},
            "overhead_partials_Pa": {},
            "molar_mass_kg_mol": {"CrO2": 0.084},
            "stoich_by_species": {
                "CrO2": {
                    "parent_oxide": "Cr2O3",
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                }
            },
            "available_oxide_kg": {"CrO2": 10.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": {},
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)

    assert result.status == "unavailable"
    assert result.diagnostic["evaporation_flux_kg_hr"] == {}
    assert set(result.diagnostic["missing_alpha"]) == {"CrO2"}
    assert "missing evaporation_alpha" in result.warnings[0]


def test_grounded_cr_ignores_unmeasured_fallback_opt_in():
    alpha_by_species = _load_evaporation_alpha_by_species(
        _vapor_pressure_data()
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"Cr2O3": 10.0}},
            species_formula_registry={},
        ),
        temperature_C=1700.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            "vapor_pressures_Pa": {"Cr": 100.0},
            "overhead_partials_Pa": {},
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
            "stir_factor": 1.0,
            "alpha": alpha_by_species,
            "allow_unmeasured_alpha_fallback": True,
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)

    assert result.status == "ok"
    assert result.diagnostic["alpha_used_by_species"] == pytest.approx({"Cr": 0.9})
    assert "unmeasured_alpha_fallback_species" not in result.diagnostic
