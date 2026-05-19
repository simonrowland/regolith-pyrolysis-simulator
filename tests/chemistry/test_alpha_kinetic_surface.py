"""Regression tests for the YAML-backed evaporation-alpha kinetic surface."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.evaporation import (
    _DEFAULT_EVAPORATION_ALPHA,
    _load_evaporation_alpha_by_species,
)
from tests.chemistry.corpus_fixtures import alpha_envelope_anchors


REPO_ROOT = Path(__file__).resolve().parents[2]
VAPOR_PRESSURES_PATH = REPO_ROOT / "data" / "vapor_pressures.yaml"
EXPECTED_ALPHA_BY_SPECIES = {
    "SiO": 0.04,
    "Fe": 0.5,
    "Mg": 0.8,
    "Na": 1.0,
    "K": 1.0,
}


def _vapor_pressure_data() -> dict:
    return yaml.safe_load(VAPOR_PRESSURES_PATH.read_text())


def test_alpha_surface_loads_expected_species_values():
    alpha_by_species = _load_evaporation_alpha_by_species(
        _vapor_pressure_data()
    )

    assert set(EXPECTED_ALPHA_BY_SPECIES) <= set(alpha_by_species)
    for species, expected_alpha in EXPECTED_ALPHA_BY_SPECIES.items():
        assert alpha_by_species[species] == pytest.approx(expected_alpha)


def test_alpha_surface_sources_and_envelopes_are_present():
    anchors = {
        anchor.species: anchor
        for anchor in alpha_envelope_anchors()
    }

    assert set(EXPECTED_ALPHA_BY_SPECIES) <= set(anchors)
    for species in EXPECTED_ALPHA_BY_SPECIES:
        anchor = anchors[species]
        assert anchor.source.strip()
        assert anchor.T_band_K[0] < anchor.T_band_K[1]
        assert anchor.envelope[0] <= anchor.value <= anchor.envelope[1]


def test_sio_alpha_stays_inside_literature_envelope():
    anchors = {
        anchor.species: anchor
        for anchor in alpha_envelope_anchors()
    }
    sio = anchors["SiO"]

    assert sio.envelope == pytest.approx((0.003, 0.048))
    assert sio.envelope[0] <= sio.value <= sio.envelope[1]


def test_evaporation_flux_diagnostic_traces_alpha_by_species():
    alpha_by_species = _load_evaporation_alpha_by_species(
        _vapor_pressure_data()
    )
    species = (*EXPECTED_ALPHA_BY_SPECIES, "Ca")
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
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)
    alpha_used = result.diagnostic["alpha_used_by_species"]

    for name, expected_alpha in EXPECTED_ALPHA_BY_SPECIES.items():
        assert alpha_used[name] == pytest.approx(expected_alpha)
    assert alpha_used["Ca"] == pytest.approx(_DEFAULT_EVAPORATION_ALPHA)
