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
from simulator.vapour_rail.batch import (
    FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
    FluxActivationContext,
    FluxRefusal,
    PressureRefusal,
)
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.instrumentation import (
    CONTROL_FLUX_PRESSURES_KEY,
    EffectivePressureSource,
    flux_pressures_from_batch,
    serialize_vapour_answer,
)
from tests.chemistry.corpus_fixtures import alpha_envelope_anchors


REPO_ROOT = Path(__file__).resolve().parents[2]
VAPOR_PRESSURES_PATH = REPO_ROOT / "data" / "vapor_pressures.yaml"
EXPECTED_ALPHA_BY_SPECIES = {
    "Fe": 0.02,
    "Mg": 0.20,
    "Na": 1.0,
    "K": 0.13,
    # b-136/t-559 withdrew the mis-tagged Zhang-2014 Ca/Ti proxy; both
    # carriers now expose marked Hertz-Knudsen ideal upper-bound ceilings.
    "Ca": 1.0,
    "Al": 0.30,
    "Si": 1.0,
    "Ti": 1.0,
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
        assert alpha_s(
            species,
            SIO_ALPHA_FORM_T_K,
            {"coefficient_spec": alpha_by_species[species]},
        ) == pytest.approx(expected_alpha)
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
            "overhead_pressure_pa": 0.0,
            CONTROL_FLUX_PRESSURES_KEY: {name: 100.0 for name in species},
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
    assert result.status == "ok"
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
            "overhead_pressure_pa": 0.0,
            CONTROL_FLUX_PRESSURES_KEY: {"Ca": 100.0, "Ti": 100.0},
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
    assert flux["Ti"] / flux["Ca"] == pytest.approx(
        EXPECTED_ALPHA_BY_SPECIES["Ti"] / EXPECTED_ALPHA_BY_SPECIES["Ca"]
    )


def test_cro2_missing_alpha_refuses_only_cro2_and_retains_parent_oxide():
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {"Cr2O3": 10.0, "Na2O": 10.0}
            },
            species_formula_registry={},
        ),
        temperature_C=1700.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            "overhead_pressure_pa": 0.0,
            CONTROL_FLUX_PRESSURES_KEY: {"CrO2": 100.0, "Na": 100.0},
            "overhead_partials_Pa": {},
            "molar_mass_kg_mol": {"CrO2": 0.084, "Na": 0.023},
            "stoich_by_species": {
                "CrO2": {
                    "parent_oxide": "Cr2O3",
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                },
                "Na": {
                    "parent_oxide": "Na2O",
                    "oxide_per_product_kg": 1.347,
                    "O2_per_product_kg": 0.347,
                },
            },
            "available_oxide_kg": {"CrO2": 10.0, "Na": 10.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": {"Na": 0.5},
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)

    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"]["Na"] > 0.0
    assert "CrO2" not in result.diagnostic["evaporation_flux_kg_hr"]
    assert set(result.diagnostic["missing_alpha"]) == {"CrO2"}
    refusal = result.diagnostic["species_refusals"]["CrO2"]
    assert refusal["policy"] == "fail_loud_missing_alpha"
    assert refusal["fallback_control"] == (
        "chemistry_kernel.allow_unmeasured_alpha_fallback"
    )
    assert refusal["P_eq_Pa"] == 100.0
    assert refusal["P_bulk_Pa"] == 0.0
    assert refusal["baseline_alpha_1_rate_kg_hr"] > 1e-12
    assert refusal["status"] == "refused"
    assert refusal["reason"] == "missing_evaporation_alpha"
    assert refusal["disposition"] == "retained_in_condensed_parent_oxide"
    assert refusal["parent_oxide"] == "Cr2O3"
    assert "per-species evaporation refusal" in result.warnings[0]


def test_cro2_composite_refuses_missing_activity_evidence() -> None:
    """An executable alpha does not replace the required melt activity."""

    catalog = compile_vapour_rail_catalog(_vapor_pressure_data())
    batch = catalog.resolve_batch(
        {"process.cleaned_melt": {"Cr2O3": 1.0}},
        state={
            "temperature_K": 1800.0,
            "process_phase": "hot_train",
            "stage": "evaporation",
            "fO2_bar": 1.0e-6,
        },
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST
        ),
    )

    answer = batch.channel("CrO2")
    assert answer.refusal_code == "missing_evidence"
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.selected_runtime_pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)
    assert "assemblage/potential evidence" in answer.pressure.detail
    assert "CrO2" not in batch.flux_active_species_ids
    assert serialize_vapour_answer(answer)["is_refused"] is True
    flux_pressures, _ = flux_pressures_from_batch(
        batch,
        effective_pressure_source=EffectivePressureSource("test", {}),
    )
    assert "CrO2" not in flux_pressures


def test_sio_typed_alpha_correlation_remains_flux_eligible() -> None:
    catalog = compile_vapour_rail_catalog(_vapor_pressure_data())
    batch = catalog.resolve_batch(
        {"process.cleaned_melt": {"SiO2": 1.0}},
        state={
            "temperature_K": 1500.0,
            "process_phase": "hot_train",
            "stage": "evaporation",
            "fO2_bar": 1.0e-6,
        },
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST
        ),
    )

    answer = batch.channel("SiO")
    assert not answer.is_refused
    assert "SiO" in batch.flux_active_species_ids


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
            "overhead_pressure_pa": 0.0,
            CONTROL_FLUX_PRESSURES_KEY: {"Cr": 100.0},
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
