"""Broad-proxy and absent-alpha evaporation predictions carry their authority."""

from pathlib import Path

import pytest
import yaml

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.evaporation import (
    _legacy_evaporation_shadow_pressure_map,
    _load_evaporation_alpha_by_species,
)
from simulator.evaporation_classes import (
    BROAD_PROXY_NOT_INTRINSIC_TAG,
    is_broad_proxy_not_intrinsic_row,
)
from simulator.vapour_rail.instrumentation import CONTROL_FLUX_PRESSURES_KEY

VAPOR_PRESSURES_PATH = Path("data/vapor_pressures.yaml")

# Rows carrying ``tag: broad_proxy_not_intrinsic`` inside the loader's
# (metals, oxide_vapors) groups on the current data. This set is the class
# the tag marks: inherited from another measurement family, never an
# intrinsic melt coefficient.
BROAD_PROXY_SPECIES = {
    "AlO",
    "Al2O",
    "Al2",
    "Al2O2",
    "Al2O3_gas",
    "AlO2",
    "CrO",
    "CrO2",
}

# Untagged bare-scalar rows that remain admitted. Cr is the same bare-float
# shape as CrO2 but carries NO proxy tag (Pound McCabe-Hudson Paxton
# pure-solid-Cr measurement family); withdrawing untagged rows is outside
# this fix's scope.
UNTAGGED_BARE_STILL_ADMITTED = {"Cr", "Fe", "Mg", "K"}

# Rows with OTHER structured proxy tags (``broad_proxy``, ``proxy``,
# ``pure_elemental_only``, ``proxy_not_intrinsic``) — several are
# owner-ratified admissions (Mn per docs/model-limitations.md). The b-314
# wiring covers only ``broad_proxy_not_intrinsic``; these stay admitted and
# are reported in the sibling sweep for owner adjudication.
OTHER_PROXY_TAG_STILL_ADMITTED = {
    "Al",
    "Si",
    "Mn",
    "CrO3",
    "K2",
    "K2O_gas",
    "Mg2",
    "MgO_gas",
    "Si2",
    "Si3",
    "SiO2_gas",
}


def _alpha_witness(species, alpha):
    return BuiltinEvaporationFluxProvider().dispatch(IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"Cr2O3": 10.0}},
            species_formula_registry={},
        ),
        temperature_C=1500.0,
        pressure_bar=0.01,
        fO2_log=None,
        control_inputs={
            CONTROL_FLUX_PRESSURES_KEY: {species: 100.0},
            "overhead_pressure_pa": 1000.0,
            "carrier_gas": "N2",
            "molar_mass_kg_mol": {species: 0.084},
            "stoich_by_species": {species: {
                "parent_oxide": "Cr2O3", "oxide_per_product_kg": 1.0,
            }},
            "available_oxide_kg": {species: 10.0},
            "melt_surface_area_m2": 0.2,
            "alpha": alpha,
        },
    ))


def test_cro2_predicts_with_proxy_authority_by_default():
    result = _alpha_witness("CrO2", _load_evaporation_alpha_by_species(_raw_data()))
    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"]["CrO2"] > 0.0
    assert result.diagnostic["alpha_used_by_species"]["CrO2"] == 0.9
    assert result.diagnostic["unmeasured_alpha_fallback_species"] == ["CrO2"]
    evaluation = result.diagnostic["alpha_s_evaluation_by_species"]["CrO2"]
    assert evaluation["alpha_authority_status"] == "proxy/bridge"
    assert "metals_cr_family" in evaluation["source"]
    assert "no CrO2-specific alpha" in evaluation["source"]


def test_absent_alpha_predicts_explicit_upper_bound_by_default():
    result = _alpha_witness("CrO2", {})
    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"]["CrO2"] > 0.0
    assert result.diagnostic["alpha_used_by_species"]["CrO2"] == 1.0
    assert result.diagnostic["unmeasured_alpha_fallback_species"] == ["CrO2"]
    evaluation = result.diagnostic["alpha_s_evaluation_by_species"]["CrO2"]
    assert evaluation["alpha_authority_status"] == "analytical_upper_bound"
    assert "upper bound" in evaluation["source"]


def test_measured_alpha_witness_unchanged():
    loaded = _load_evaporation_alpha_by_species(_raw_data())
    result = _alpha_witness("K", loaded)
    explicit = _alpha_witness("K", {"K": 0.13})  # Fedkin 2006 KEMS anchor.
    assert result.diagnostic["alpha_used_by_species"]["K"] == 0.13
    assert result.diagnostic["evaporation_flux_kg_hr"] == explicit.diagnostic["evaporation_flux_kg_hr"]
    assert "unmeasured_alpha_fallback_species" not in result.diagnostic


def _raw_data() -> dict:
    return yaml.safe_load(VAPOR_PRESSURES_PATH.read_text())


def test_proxy_rows_carry_provenance_not_bare_floats():
    """The brief's sharp point: Na and SiO carry provenance into the
    consumption path; CrO2's 0.9 was a naked number indistinguishable from
    a measurement. Tagged rows now load as mappings naming the tag, tier,
    and source."""
    alpha_by_species = _load_evaporation_alpha_by_species(_raw_data())
    for species in BROAD_PROXY_SPECIES:
        spec = alpha_by_species.get(species)
        assert isinstance(spec, dict), (
            f"{species}: proxy row must load as a provenance-carrying "
            f"mapping, got {spec!r}"
        )
        assert spec["alpha_proxy_tag"] == BROAD_PROXY_NOT_INTRINSIC_TAG
        assert spec["alpha_proxy_tier"] == 2
        assert spec["source"].strip()
    assert alpha_by_species["CrO2"]["value"] == pytest.approx(0.9)


def test_all_broad_proxy_species_remain_executable_and_flagged():
    loaded = _load_evaporation_alpha_by_species(_raw_data())
    assert BROAD_PROXY_SPECIES <= loaded.keys()
    for species in BROAD_PROXY_SPECIES:
        result = _alpha_witness(species, loaded)
        assert result.diagnostic["evaporation_flux_kg_hr"][species] > 0.0
        assert result.diagnostic["alpha_authority_status_by_species"][species] == "proxy/bridge"
        evaluation = result.diagnostic["alpha_s_evaluation_by_species"][species]
        assert evaluation["source"] == loaded[species]["source"]
        assert evaluation["authority_reason"] == BROAD_PROXY_NOT_INTRINSIC_TAG


def test_proxy_tagging_is_data_driven_not_hardcoded():
    """The class fix: a synthetic tagged row is marked even for
    a species no production data carries, and removing the tag re-admits
    the row as a plain value."""
    data = {"metals": {"TestSpecies": {
        "evaporation_alpha": {
            "value": 0.9,
            "source": "synthetic test proxy",
            "tag": BROAD_PROXY_NOT_INTRINSIC_TAG,
            "tier": 2,
        }
    }}}
    loaded = _load_evaporation_alpha_by_species(data)
    assert loaded["TestSpecies"]["alpha_proxy_tag"] == (
        BROAD_PROXY_NOT_INTRINSIC_TAG
    )
    assert loaded["TestSpecies"]["value"] == 0.9
    untagged = {"metals": {"TestSpecies": {
        "evaporation_alpha": {"value": 0.9, "source": "synthetic"},
    }}}
    loaded_untagged = _load_evaporation_alpha_by_species(untagged)
    assert loaded_untagged["TestSpecies"] == 0.9
    assert loaded_untagged["TestSpecies"] == 0.9


def test_pressure_gate_keeps_proxy_species():
    """Guard against the wrong-layer fix: the species' pressure must still
    reach the provider, or the refusal can never fire and the species
    evaporates silently to zero (the same absence-toward-confidence class
    this ticket fixes, one layer up)."""
    shadow = _legacy_evaporation_shadow_pressure_map(
        _raw_data(),
        {"CrO2": 3.7e-9, "AlO": 1.0, "Fe": 2.0},
        read_context="test",
    )
    assert shadow == pytest.approx({"CrO2": 3.7e-9, "AlO": 1.0, "Fe": 2.0})



def test_pressure_gate_keeps_species_without_any_alpha_row():
    assert _legacy_evaporation_shadow_pressure_map(
        {}, {"CrO2": 100.0}, read_context="test",
    ) == {"CrO2": 100.0}

def test_untagged_rows_still_admitted_with_values():
    measured = _load_evaporation_alpha_by_species(_raw_data())
    for species in UNTAGGED_BARE_STILL_ADMITTED:
        assert species in measured, species
    assert measured["Cr"] == pytest.approx(0.9)
    # Provenance-carrying mappings survive untouched.
    na = measured["Na"]
    assert isinstance(na, dict)
    assert na["alpha_authority_status"] == "analytical_upper_bound"
    sio = measured["SiO"]
    assert isinstance(sio, dict) and sio["form"] == "arrhenius"
    assert sio["status"] == "UNCERTIFIED"


def test_other_proxy_tag_rows_stay_admitted_scope_marker():
    """Scope marker for the sweep: other structured proxy tags are NOT
    wired by b-314 (several are owner-ratified). If the owner later widens
    the wiring, this test is the deliberate place that must change."""
    measured = _load_evaporation_alpha_by_species(_raw_data())
    missing = OTHER_PROXY_TAG_STILL_ADMITTED - set(measured)
    assert not missing, f"unexpectedly stripped: {sorted(missing)}"


def test_flux_dormant_rows_keep_declared_zero():
    """Declared dormancy (``flux_dormant: true``) is a data declaration, not
    an absence: dormant rows keep their explicit 0.0."""
    measured = _load_evaporation_alpha_by_species(_raw_data())
    for species in ("FeO_association_gas", "NiO_gas", "MnO_gas", "CoO_gas"):
        assert measured.get(species) == 0.0, species


def test_no_data_rows_still_absent():
    """Exact ``status: no_data`` remains absence (unchanged semantics)."""
    data = {"metals": {"Ghost": {"evaporation_alpha": {"status": "no_data"}}}}
    loaded = _load_evaporation_alpha_by_species(data)
    assert "Ghost" not in loaded
    assert "Ghost" not in loaded


def test_predicate_shapes():
    assert is_broad_proxy_not_intrinsic_row(
        {"tag": "broad_proxy_not_intrinsic", "value": 0.9}
    )
    assert is_broad_proxy_not_intrinsic_row({"tag": " Broad_Proxy_Not_Intrinsic "})
    assert not is_broad_proxy_not_intrinsic_row({"tag": "proxy_not_intrinsic"})
    assert not is_broad_proxy_not_intrinsic_row({"value": 0.9})
    assert not is_broad_proxy_not_intrinsic_row(None)
    assert not is_broad_proxy_not_intrinsic_row("broad_proxy_not_intrinsic")


def test_cro2_proxy_and_sodium_upper_bound_both_evaporate():
    alpha_by_species = _load_evaporation_alpha_by_species(_raw_data())
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"Cr2O3": 10.0, "Na2O": 10.0}},
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
            "alpha": alpha_by_species,
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)

    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"]["Na"] > 0.0
    assert result.diagnostic["evaporation_flux_kg_hr"]["CrO2"] > 0.0
    assert "missing_alpha" not in result.diagnostic
    assert result.diagnostic["alpha_authority_status_by_species"]["CrO2"] == "proxy/bridge"
    assert result.diagnostic["alpha_authority_status_by_species"]["Na"] == "analytical_upper_bound"


def test_legacy_opt_in_preserves_proxy_coefficient():
    alpha_by_species = _load_evaporation_alpha_by_species(_raw_data())
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
            CONTROL_FLUX_PRESSURES_KEY: {"CrO2": 100.0},
            "overhead_partials_Pa": {},
            "molar_mass_kg_mol": {"CrO2": 0.084},
            "stoich_by_species": {
                "CrO2": {
                    "parent_oxide": "Cr2O3",
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                },
            },
            "available_oxide_kg": {"CrO2": 10.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": alpha_by_species,
            "allow_unmeasured_alpha_fallback": True,
        },
    )

    result = BuiltinEvaporationFluxProvider().dispatch(request)

    assert result.status == "ok"
    assert "missing_alpha" not in result.diagnostic
    assert result.diagnostic["evaporation_flux_kg_hr"]["CrO2"] > 0.0
    assert result.diagnostic["unmeasured_alpha_fallback_species"] == ["CrO2"]
    assert result.diagnostic["alpha_used_by_species"]["CrO2"] == 0.9
