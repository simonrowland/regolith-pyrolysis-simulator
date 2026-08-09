from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import (
    CondensationModel,
    _species_has_antoine_data,
    _try_antoine_psat_pa,
)
from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CondensationTrain, EvaporationFlux, MeltState
from simulator.volatile_properties import _catalog as _volatile_runtime_catalog
from simulator.vapour_rail.catalog import (
    CatalogCompileError,
    OUT_OF_RANGE_STATUS,
    compile_vapour_rail_catalog,
    validate_species_catalog,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
COLLISION_GASES = {
    "Al2O3_gas",
    "CaO_gas",
    "CoO_gas",
    "Cr2O3_gas",
    "Fe2O3_gas",
    "FeO_gas",
    "K2O_gas",
    "MgO_gas",
    "MnO_gas",
    "Na2O_gas",
    "NiO_gas",
    "P2O5_gas",
    "SiO2_gas",
    "TiO2_gas",
}
ACTIVE_COLLISION_GASES = {
    "Al2O3_gas",
    "CaO_gas",
    "K2O_gas",
    "MgO_gas",
    "Na2O_gas",
    "SiO2_gas",
    "TiO2_gas",
}
CARRIER_ONLY = {
    "CH4_NH3_HCN",
    "CO_CH4_propellant",
    "CO_CO2",
    "Fe_Ni_alloy",
    "NH3_HCN",
    "NaCl_KCl_salts",
    "REE_oxides",
    "carbonate_salts",
    "generic_carbonaceous_hydrocarbon",
    "generic_carbonaceous_organic",
    "metallic_FeNi",
}


def _yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _reaction_fixture() -> dict:
    return {
        "schema_version": 2,
        "families": {
            "potassium_test_family": {
                "physical_properties": {
                    "species": {
                        "K": {
                            "formula": "K",
                            "source_reactions": [
                                {
                                    "id": "ko0_5_to_k",
                                    "reactants": [
                                        {"formula": "KO0.5", "stoichiometry": 1.0}
                                    ],
                                    "products": [
                                        {"formula": "K", "stoichiometry": 1.0},
                                        {"formula": "O2", "stoichiometry": 0.25},
                                    ],
                                    "activity_input": {
                                        "component_id": "KO0.5",
                                        "standard_state": {
                                            "convention": "raoultian_pure_endmember",
                                            "phase": "liquid",
                                            "reference_pressure_bar": 1.0,
                                            "component_basis": "raoultian_pure_endmember",
                                        },
                                        "activity_model": "provider_reported_thermodynamic_activity",
                                        "allow_henrian_upper_bound": False,
                                        "compound_bearing": False,
                                        "require_assemblage_match": False,
                                    },
                                }
                            ],
                            "pressure_models": [
                                {
                                    "evaluator_family": "standard_reaction_term",
                                    "fit_target": "standard_reaction_term",
                                    "pressure_kind": "equilibrium_partial_pressure",
                                    "species_basis": "monomer",
                                    "valid_domain": {"temperature_K": [1000.0, 1200.0]},
                                    "source_reaction_id": "ko0_5_to_k",
                                    "activity_semantics": "source_reaction_activity",
                                    "reference_pressure_model": {
                                        "evaluator_family": "tabulated_equilibrium",
                                        "points": [
                                            {"temperature_K": 1000.0, "pressure_Pa": 1.0},
                                            {"temperature_K": 1200.0, "pressure_Pa": 100.0},
                                        ],
                                    },
                                    "activity_exponent": 1.0,
                                    "pO2_exponent": -0.25,
                                    "pO2_reference_bar": 1.0,
                                    "oxygen_fugacity_channel": "intrinsic_melt",
                                }
                            ],
                            "validation": {
                                "status": "pending_validation",
                                "anchor_refs": [],
                            },
                            "parent_oxide": "K2O",
                            "oxide_activity_exponent": 1.0,
                            "pO2_exponent": -0.25,
                            "pO2_reference_bar": 1.0,
                            "molar_mass_g_mol": 39.0983,
                        }
                    }
                },
                "fiat_routing": {
                    "plant_bin": None,
                    "engineering_capture_policy": "temperature_threshold",
                    "products_and_coproducts": [],
                    "process_or_terminal_destination": "process.condensation_train",
                    "condensation_reference_at_1mbar_C": 420.0,
                },
                "vaporisation_coefficients": {
                    "evaporation_alpha": {"value": 0.13},
                    "alpha_domain_and_uncertainty": {},
                    "extrapolation_policy": "conservative_slope_continuation",
                    "out_of_range_status": OUT_OF_RANGE_STATUS,
                    "acquisition_flag": "acquire:test:K",
                },
                "code_metadata": {
                    "formula_id": "K",
                    "source_account": "process.cleaned_melt",
                    "request_rule": "source_inventory_present",
                    "solve_group_id": "potassium_test_family",
                    "compatibility_projection": "metals",
                    "canonical_aliases": [],
                    "hot_train_applicability": "applicable",
                },
            }
        },
    }


@pytest.mark.parametrize(
    "availability",
    [
        {"status": "unavailable_pending_acquisition"},
        "unknown_pending_state",
    ],
)
def test_pressure_model_availability_is_a_fail_closed_scalar_enum(
    availability,
) -> None:
    payload = _reaction_fixture()
    model = payload["families"]["potassium_test_family"]["physical_properties"][
        "species"
    ]["K"]["pressure_models"][0]
    model["availability"] = availability

    with pytest.raises(CatalogCompileError, match="availability must be"):
        compile_vapour_rail_catalog(payload)


def test_pressure_model_availability_omitted_or_explicitly_unavailable() -> None:
    available = compile_vapour_rail_catalog(_reaction_fixture())
    assert available.species["K"].evaluator is not None

    payload = _reaction_fixture()
    model = payload["families"]["potassium_test_family"]["physical_properties"][
        "species"
    ]["K"]["pressure_models"][0]
    model["availability"] = "unavailable_pending_acquisition"
    unavailable = compile_vapour_rail_catalog(payload)
    assert unavailable.species["K"].evaluator is None


def test_policy_identity_cannot_compile_as_executable_alpha() -> None:
    payload = _reaction_fixture()
    alpha = payload["families"]["potassium_test_family"][
        "vaporisation_coefficients"
    ]["evaporation_alpha"]
    alpha["value"] = {"type": "policy", "status": "no_data"}

    with pytest.raises(CatalogCompileError, match="correlation form"):
        compile_vapour_rail_catalog(payload)


@pytest.mark.parametrize(
    ("field", "hostile_value"),
    [
        ("A", True),
        ("B", False),
        ("valid_range_K", [True, 2000.0]),
        ("valid_range_K", [300.0, False]),
        ("uncertainty_envelope", [True, 1.0]),
        ("uncertainty_envelope", [0.0, False]),
    ],
)
def test_arrhenius_alpha_rejects_nested_boolean_values(
    field,
    hostile_value,
) -> None:
    payload = _reaction_fixture()
    alpha = payload["families"]["potassium_test_family"][
        "vaporisation_coefficients"
    ]["evaporation_alpha"]
    alpha["value"] = {
        "form": "arrhenius",
        "A": 0.5,
        "B": 1000.0,
        "valid_range_K": [300.0, 2000.0],
        "uncertainty_envelope": [0.1, 0.9],
        "cite": "hostile type regression",
        "status": "CITED",
    }
    alpha["value"][field] = hostile_value

    with pytest.raises(CatalogCompileError, match="not boolean"):
        compile_vapour_rail_catalog(payload)


def test_production_schema_compiles_exact_four_strata_and_legacy_projection() -> None:
    payload = _yaml("vapor_pressures.yaml")
    assert payload["schema_version"] == 2
    assert payload["families"]
    for family in payload["families"].values():
        assert set(family) == {
            "physical_properties",
            "fiat_routing",
            "vaporisation_coefficients",
            "code_metadata",
        }

    catalog = compile_vapour_rail_catalog(payload)
    # VR-7 adds dormant acquisition families; VR-8 adds monatomic O (dormant).
    # Combined catalog is larger than either chunk alone.
    assert len(catalog.species) >= 16
    assert "O" in catalog.species
    assert catalog.species["O"].evaluator is None
    assert catalog.species["O"].code_metadata.hot_train_applicability == (
        "not_applicable"
    )
    legacy = catalog.legacy_view()
    assert len(legacy["metals"]) == 10
    # The oxide, phosphorus, and MC-4 systematic-carrier chunks intentionally
    # activate twenty carriers through the pre-RG compatibility seam.
    # 2026-08-05 MC-4 wave-1 integration: the union of wave A (Al/C/Ca/Cl/
    # Cr/Fe/H) and wave B (K/Mg/N/Na/P/S/Si) carriers projects 30 oxide-vapor
    # rows through the pre-RG compatibility seam (20 from A alone; +10 exact
    # CEA-composed K2O/MgO/Na2O/P2O5/SiO2-class carriers from B).
    assert len(legacy["oxide_vapors"]) == 29  # 29 after the P2O5_gas tombstone restore
    # 2026-08-05 MC-4 integration: the A+B union projects 24 foulant rows
    # (wave A adds the CaCl2/Cl-family foulant carriers; wave B the chloride
    # dimers) — one more than either wave alone pinned.
    assert len(legacy["foulant_vapor"]) == 24
    # MC-4b adds ten exact CEA-composed K/Mg/Na/P/Si carriers through the
    # same oxide-vapor compatibility seam. Each is independently listed;
    # the parent ledger still performs one grouped reservoir debit.
    # MC-4b activates five previously catalog-only Stage-0 overhead carriers:
    # N2, NH3, SO2, and the K/Na salt dimers. MgCl2 remains NEEDS-BASE because
    # no runtime Stage-0 MgCl2 reservoir exists. (The B-side ==8 foulant pin
    # was superseded by the A+B union count asserted above.)
    assert set(legacy["metals"]) == {
        "Na",
        "K",
        "Mg",
        "Fe",
        "Ca",
        "Al",
        "Si",
        "Ti",
        "Cr",
        "Mn",
    }
    assert set(legacy["oxide_vapors"]) == {
        "SiO",
        "CrO2",
        "TiO",
        "TiO2_gas",
        "CaO_gas",
        "AlO",
        "Al2O",
        "Al2",
        "Al2O2",
        "Al2O3_gas",
        "AlO2",
        "Ca2",
        "CrO",
        "CrO3",
        "PO",
        "PO2",
        "P2",
        "P4",
        "P4O6",
        "P4O10",
        "K2",
        "K2O_gas",
        "Mg2",
        "MgO_gas",
        "Na2",
        "Na2O_gas",
        "Si2",
        "Si3",
        "SiO2_gas",
    }
    # 2026-08-05 MC-4 integration: wave A deliberately projects all Stage-0
    # overhead species (volatiles/organics included) through the foulant_vapor
    # compatibility group; the curated salt-only membership B pinned is
    # superseded by the A+B union view.
    assert set(legacy["foulant_vapor"]) == {
        "C2H5OH",
        "C2H6",
        "CH3COOH",
        "CH4",
        "CO",
        "CO2",
        "COS",
        "CS2",
        "CaCl2",
        "Cl2",
        "H2O",
        "H2S",
        "HCHO",
        "HCN",
        "HCl",
        "HNCO",
        "K2Cl2",
        "KCl",
        "N2",
        "NH3",
        "Na2Cl2",
        "NaCl",
        "NaF",
        "SO2",
    }
    # (duplicate B-side membership pin superseded by the union set above)
    assert legacy["metals"]["K"]["antoine"]["A"] == pytest.approx(10.641294)
    # Activity-corrected schema-v2 models must not leak into the legacy
    # projection that still feeds the pre-RG equilibrium-backend seam.
    assert {
        species_id: legacy["metals"][species_id]["antoine"]
        for species_id in ("Ca", "Al", "Ti", "Cr", "Mn")
    } == {
        "Ca": {"A": 11.238, "B": 9520, "C": 0},
        "Al": {"A": 11.553, "B": 17340, "C": 0},
        "Ti": {"A": 11.65, "B": 23200, "C": 0},
        "Cr": {"A": 11.42, "B": 20730, "C": 0},
        "Mn": {"A": 11.183, "B": 14740, "C": 0},
    }
    sodium = catalog.species["Na"]
    assert sodium.fiat_routing.process_or_terminal_destination == (
        "process.condensation_train"
    )
    assert sodium.vaporisation_coefficients.extrapolation_policy == (
        "conservative_slope_continuation"
    )
    assert sodium.code_metadata.formula_id == "Na"
    assert sodium.code_metadata.source_account == "process.cleaned_melt"

    bundle = load_config_bundle(DATA_DIR)
    assert bundle.vapor_pressures == legacy
    assert bundle.vapor_pressures.catalog_payload == payload


def test_production_p_carriers_share_parent_activity_and_never_sparsify() -> None:
    payload = _yaml("vapor_pressures.yaml")
    provider = BuiltinVaporPressureProvider(payload)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {"P2O5": 1.0, "SiO2": 99.0}
            },
            species_formula_registry={},
        ),
        temperature_C=1200.0,
        pressure_bar=0.009,
        fO2_log=-9.0,
        control_inputs={
            "pO2_bar": 0.009,
            "intrinsic_fO2_log": -9.0,
            "process_phase": "stage0",
        },
    )

    result = provider.dispatch(request)
    diagnostic = result.diagnostic or {}
    carriers = {"PO", "PO2", "P2", "P4", "P4O6", "P4O10"}
    pressures = diagnostic["vapor_pressures_Pa"]
    activities = diagnostic["activities"]
    assert carriers <= set(pressures)
    assert carriers <= set(activities)

    catalog = compile_vapour_rail_catalog(
        payload, emit_u0_request_rules=False
    )
    intrinsic_fO2_bar = diagnostic["source_reaction_fO2_bar"]
    for species in carriers:
        evaluator = catalog.evaluator_for(species)
        expected = evaluator.evaluate(
            1473.15,
            source_activity=activities[species],
            pO2_bar=intrinsic_fO2_bar,
        ).pressure_pa
        assert pressures[species] == pytest.approx(expected, rel=1.0e-12)
        provenance = diagnostic["vapor_pressure_numerator_provenance"][species]
        assert provenance["equivalent_parent_oxide_activity"] == pytest.approx(
            activities[species]
        )
        assert provenance["melt_oxide_activity_authority_status"] == (
            "out_of_gamma_domain_status_bearing_non_authoritative"
        )


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("malformed_antoine", "compatibility_antoine.A"),
        ("missing_antoine_key", "must contain exactly A, B, C"),
        ("extra_antoine_key", "must contain exactly A, B, C"),
        ("nonfinite_antoine", "compatibility_antoine.B must be a finite number"),
        ("boolean_antoine", "compatibility_antoine.A must be a finite number"),
        ("reversed_range", "compatibility_valid_range_K must be increasing"),
        ("malformed_range_shape", r"must be \[low, high\]"),
        ("nonfinite_range", r"compatibility_valid_range_K\[1\] must be finite"),
        ("boolean_range", "compatibility_valid_range_K.*must be numeric, not boolean"),
    ],
)
def test_legacy_compatibility_projection_rejects_malformed_flux_inputs(
    mutation: str,
    match: str,
) -> None:
    payload = _yaml("vapor_pressures.yaml")
    ca_model = payload["families"]["metals_ca_family"]["physical_properties"][
        "species"
    ]["Ca"]["pressure_models"][0]
    if mutation == "malformed_antoine":
        ca_model["compatibility_antoine"]["A"] = "bogus"
    elif mutation == "missing_antoine_key":
        ca_model["compatibility_antoine"].pop("B")
    elif mutation == "extra_antoine_key":
        ca_model["compatibility_antoine"]["D"] = 0.0
    elif mutation == "nonfinite_antoine":
        ca_model["compatibility_antoine"]["B"] = math.nan
    elif mutation == "boolean_antoine":
        ca_model["compatibility_antoine"]["A"] = True
    elif mutation == "reversed_range":
        ca_model["compatibility_valid_range_K"] = [2000.0, 1000.0]
    elif mutation == "malformed_range_shape":
        ca_model["compatibility_valid_range_K"] = [1000.0]
    elif mutation == "nonfinite_range":
        ca_model["compatibility_valid_range_K"] = [1000.0, math.inf]
    else:
        ca_model["compatibility_valid_range_K"] = [True, 1000.0]

    with pytest.raises(CatalogCompileError, match=match):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_nonfinite_named_field_yields_field_specific_not_digest_message() -> None:
    """Ordering regression: schema field message must beat digest non-finite guard.

    t-517's compile-input digest refuses NaN/Inf with a generic message. That
    guard must not fire *before* field validation, or operators lose which
    named coefficient/range entry is bad. Both paths stay fail-closed; this
    asserts the diagnostic that wins is the field-specific one.
    """

    payload = _yaml("vapor_pressures.yaml")
    ca_model = payload["families"]["metals_ca_family"]["physical_properties"][
        "species"
    ]["Ca"]["pressure_models"][0]
    ca_model["compatibility_antoine"]["B"] = math.nan

    with pytest.raises(
        CatalogCompileError,
        match=r"compatibility_antoine\.B must be a finite number",
    ) as caught:
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    assert "compile-input digest requires finite floats" not in str(caught.value)


def test_pure_component_phase_requires_explicit_non_melt_identity() -> None:
    payload = _yaml("vapor_pressures.yaml")
    nacl_model = payload["families"]["foulant_vapor_nacl_family"][
        "physical_properties"
    ]["species"]["NaCl"]["pressure_models"][0]
    nacl_model.pop("activity_semantics")
    with pytest.raises(CatalogCompileError, match="pure-component saturation requires"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)

    payload = _yaml("vapor_pressures.yaml")
    nacl_family = payload["families"]["foulant_vapor_nacl_family"]
    nacl_family["code_metadata"]["source_account"] = "process.cleaned_melt"
    identity = nacl_family["physical_properties"]["species"]["NaCl"][
        "pressure_models"
    ][0]["pure_condensed_phase_identity"]
    identity["source_account"] = "process.cleaned_melt"
    with pytest.raises(CatalogCompileError, match="dedicated non-melt"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("missing_identity", "pure_condensed_phase_identity must be a mapping"),
        ("wrong_component", "does not match species formula"),
        ("gas_phase", "phase must be 'condensed_solid' or 'condensed_liquid'"),
        ("wrong_account", "does not match code_metadata"),
    ],
)
def test_pure_component_phase_identity_is_independently_enforced(
    mutation: str,
    match: str,
) -> None:
    payload = _yaml("vapor_pressures.yaml")
    nacl_model = payload["families"]["foulant_vapor_nacl_family"][
        "physical_properties"
    ]["species"]["NaCl"]["pressure_models"][0]
    identity = nacl_model["pure_condensed_phase_identity"]
    if mutation == "missing_identity":
        nacl_model.pop("pure_condensed_phase_identity")
    elif mutation == "wrong_component":
        identity["component_id"] = "KCl"
    elif mutation == "gas_phase":
        identity["phase"] = "gas"
    else:
        identity["source_account"] = "process.other_foulant"

    with pytest.raises(CatalogCompileError, match=match):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_species_catalog_closes_collision_gases_and_carrier_only_rows() -> None:
    payload = _yaml("species_catalog.yaml")
    validate_species_catalog(payload)
    by_id = {row["id"]: row for row in payload["species"]}

    collision_rows = {
        species_id for species_id, row in by_id.items() if row.get("catalog_role") == "gas"
    }
    assert collision_rows == COLLISION_GASES
    for species_id in COLLISION_GASES:
        row = by_id[species_id]
        assert row["formula"] == species_id.removesuffix("_gas")
        assert row["atoms"]
        assert row["phase"] == "gas"
        assert row["validation"]["status"] == "pending_validation"
        assert row["pressure_observable"] == "equilibrium_partial_pressure"
        assert row["valid_domain"]
        assert row["extrapolation_policy"] == "conservative_slope_continuation"
        assert row["out_of_range_status"] == OUT_OF_RANGE_STATUS
        assert row["acquisition_flag"].endswith(species_id)
        assert row["code_metadata"]["formula_id"] == row["formula"]
        assert row["code_metadata"]["collision_only_suffix"] is (
            species_id not in ACTIVE_COLLISION_GASES
        )

    carrier_rows = {
        species_id
        for species_id, row in by_id.items()
        if row.get("catalog_role") == "carrier_only"
    }
    assert carrier_rows == CARRIER_ONLY
    for species_id in CARRIER_ONLY:
        row = by_id[species_id]
        assert row["formula"] is None
        assert row["pressure_models"] == []
        assert row["direct_vapour_flux"] is False
        assert row["requires_balanced_decomposition"] is True


def test_collision_gases_do_not_enter_legacy_volatile_runtime_registry() -> None:
    formulas, aliases, catalog_specs, formula_texts = _volatile_runtime_catalog()

    inactive_collision_gases = COLLISION_GASES - ACTIVE_COLLISION_GASES
    assert inactive_collision_gases.isdisjoint(formulas)
    assert inactive_collision_gases.isdisjoint(aliases)
    assert inactive_collision_gases.isdisjoint(catalog_specs)
    assert inactive_collision_gases.isdisjoint(formula_texts)
    # Once a collision-safe gas row becomes a direct carrier its suffixed id,
    # formula alias, atom spec, and formula text must all enter together; ledger
    # code cannot infer TiO2(g) from condensed TiO2 by string collision.
    for species_id in ACTIVE_COLLISION_GASES:
        assert species_id in formulas
        assert species_id in aliases
        assert species_id in catalog_specs
        assert species_id in formula_texts


def test_b1_standard_reaction_without_antoine_uses_shared_evaluator() -> None:
    payload = _reaction_fixture()
    serialized = yaml.safe_dump(payload)
    assert "antoine" not in serialized

    catalog = compile_vapour_rail_catalog(payload)
    evaporation_evaluator = catalog.evaluator_for_evaporation("K")
    condensation_evaluator = catalog.evaluator_for_condensation("K")
    assert evaporation_evaluator is condensation_evaluator

    evaporation = evaporation_evaluator.evaluate(
        1100.0,
        source_activity=0.25,
        pO2_bar=1.0e-4,
    )
    condensation = condensation_evaluator.evaluate(
        1100.0,
        source_activity=0.25,
        pO2_bar=1.0e-4,
    )
    assert evaporation.pressure_pa == pytest.approx(condensation.pressure_pa)
    assert evaporation.pressure_pa > 0.0

    # Direct evaluator callers cannot bypass the typed activity/fO2 path by
    # inheriting unity activity or the oxygen reference pressure.
    with pytest.raises(CatalogCompileError, match="explicit source_activity"):
        condensation_evaluator.evaluate(1100.0)
    with pytest.raises(CatalogCompileError, match="explicit source_activity"):
        condensation_evaluator.evaluate(1100.0, pO2_bar=1.0e-4)
    with pytest.raises(CatalogCompileError, match="explicit intrinsic_melt"):
        condensation_evaluator.evaluate(1100.0, source_activity=0.25)


def test_activity_dependent_model_requires_explicit_activity_declaration() -> None:
    payload = _reaction_fixture()
    reaction = payload["families"]["potassium_test_family"][
        "physical_properties"
    ]["species"]["K"]["source_reactions"][0]
    reaction.pop("activity_input")

    with pytest.raises(CatalogCompileError, match="silent a=1 is forbidden"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("missing_exponent", "implicit a=1 is forbidden"),
        ("missing_semantics", "activity_semantics"),
        ("unknown_model", "unsupported by catalog channel evaluation"),
        ("wrong_component", "is not a selected-reaction reactant"),
    ],
)
def test_activity_contract_identity_mutations_fail_compile(
    mutation: str, match: str
) -> None:
    payload = _reaction_fixture()
    species = payload["families"]["potassium_test_family"][
        "physical_properties"
    ]["species"]["K"]
    model = species["pressure_models"][0]
    activity_input = species["source_reactions"][0]["activity_input"]
    if mutation == "missing_exponent":
        model.pop("activity_exponent")
    elif mutation == "missing_semantics":
        model.pop("activity_semantics")
    elif mutation == "unknown_model":
        activity_input["activity_model"] = "nonsense_unvalidated"
    else:
        activity_input["component_id"] = "DefinitelyNotAReactant"

    with pytest.raises(CatalogCompileError, match=match):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_b1_oxide_row_requires_activity_for_condensation_without_antoine() -> None:
    payload = _reaction_fixture()
    family = payload["families"]["potassium_test_family"]
    family["code_metadata"]["compatibility_projection"] = "oxide_vapors"
    provider = BuiltinVaporPressureProvider(payload)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"K2O": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=826.85,
        pressure_bar=1.0e-6,
        fO2_log=-4.0,
        control_inputs={"pO2_bar": 1.0e-4, "intrinsic_fO2_log": -4.0},
    )

    result = provider.dispatch(request)
    diagnostic = result.diagnostic or {}
    assert diagnostic["vapor_pressures_Pa"]["K"] > 0.0
    assert diagnostic["vapor_pressure_numerator_provenance"]["K"][
        "P_reference_model_Pa"
    ] > 0.0
    assert _species_has_antoine_data("K", vapor_pressure_data=payload)
    pressure_pa, refused = _try_antoine_psat_pa(
        "K", 1100.0, vapor_pressure_data=payload
    )
    assert refused is True
    assert pressure_pa is None

    model = CondensationModel(
        CondensationTrain.create_default(),
        vapor_pressure_data=payload,
        wall_temperature_C=400.0,
    )
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        pipe_diameter_m=0.12,
        carrier_gas="O2",
        species_partial_pressures_mbar={"K": 1.0},
    )
    melt = MeltState()
    melt.temperature_C = 826.85
    route = model.route(
        EvaporationFlux(species_kg_hr={"K": 1.0}, total_kg_hr=1.0),
        melt,
    )
    # Condensation has no source-activity evidence for this synthetic row and
    # must status-bearing pass through instead of evaluating at implicit a=1.
    k_refusal = route.condensation_refusals_by_species.get("K")
    assert k_refusal is not None
    assert k_refusal.get("status") == "pass_through"
    assert "K" not in route.wall_deposit_by_species
    assert route.remaining_by_species["K"] == pytest.approx(1.0)


def test_metals_projection_reference_evaluation_declares_neutral_inputs() -> None:
    payload = _reaction_fixture()
    provider = BuiltinVaporPressureProvider(payload)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"K2O": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=826.85,
        pressure_bar=1.0e-6,
        fO2_log=-4.0,
        control_inputs={"pO2_bar": 1.0e-4, "intrinsic_fO2_log": -4.0},
    )

    result = provider.dispatch(request)
    diagnostic = result.diagnostic or {}
    assert diagnostic["vapor_pressures_Pa"]["K"] > 0.0


def test_config_bundle_retains_compiler_capability_for_b1(
    tmp_path: Path,
) -> None:
    path = tmp_path / "vapor_pressures.yaml"
    path.write_text(yaml.safe_dump(_reaction_fixture()))
    bundle = load_config_bundle(DATA_DIR, vapor_pressures_path=path)

    catalog_payload = bundle.vapor_pressures.catalog_payload
    assert catalog_payload["schema_version"] == 2
    assert _species_has_antoine_data(
        "K", vapor_pressure_data=catalog_payload
    )
    pressure_pa, refused = _try_antoine_psat_pa(
        "K", 1100.0, vapor_pressure_data=catalog_payload
    )
    assert refused is True
    assert pressure_pa is None

    sim = PyrolysisSimulator(
        InternalAnalyticalBackend(),
        bundle.setpoints,
        bundle.feedstocks,
        bundle.vapor_pressures,
        materials=bundle.materials,
    )
    assert sim.vapour_rail_catalog is not None
    assert sim.vapour_rail_catalog.evaluator_for_evaporation(
        "K"
    ) is sim.vapour_rail_catalog.evaluator_for_condensation("K")


def test_anti_cliff_continuation_is_status_bearing_physical_reciprocal_T() -> None:
    """Out-of-domain continues with a physical 1/T (van 't Hoff) estimate.

    Rationale (t-538 / b-145 class): the prior attenuated linear-T slope
    invented multi-dex low-T pressure. Anti-cliff intent is preserved
    (nonzero, continuous from the domain edge, status-bearing OOR mark);
    the *value* is the family-keyed physical continuation — for this
    tabulated_equilibrium reference, the linear-in-1/T chord through the
    two edge cells.
    """
    evaluator = compile_vapour_rail_catalog(_reaction_fixture()).evaluator_for("K")
    endpoint = evaluator.evaluate(
        1200.0, source_activity=1.0, pO2_bar=1.0
    ).pressure_pa
    beyond = evaluator.evaluate(1300.0, source_activity=1.0, pO2_bar=1.0)
    below = evaluator.evaluate(900.0, source_activity=1.0, pO2_bar=1.0)

    assert beyond.out_of_range is True
    assert beyond.status == OUT_OF_RANGE_STATUS
    assert beyond.acquisition_flag == "acquire:test:K"
    # Anti-cliff: never silent zero; continuous at the edge.
    assert beyond.pressure_pa > 0.0
    assert endpoint > 0.0
    assert below.pressure_pa > 0.0
    assert below.out_of_range is True

    # Premise: tabulated edge cells (1000 K, 1 Pa) and (1200 K, 100 Pa)
    # define a unique linear-in-1/T line. Algebra:
    #   log10 P(T) = log10 P0 + (log10 P1 − log10 P0)
    #                · (1/T − 1/T0) / (1/T1 − 1/T0)
    # Unit Pa. Sanity: at T=T0/T1 the line recovers the cell pressures;
    # at T=1300 K (above) and T=900 K (below) the OOR path matches.
    t0, p0 = 1000.0, 1.0
    t1, p1 = 1200.0, 100.0
    log0, log1 = math.log10(p0), math.log10(p1)
    inv0, inv1 = 1.0 / t0, 1.0 / t1

    def expected_log10(T: float) -> float:
        return log0 + (log1 - log0) * ((1.0 / T) - inv0) / (inv1 - inv0)

    # Outer activity/pO2 factors are unity at a=1, pO2=1 bar with the
    # fixture's exponents, so pressure_pa is the reference continuation.
    assert math.log10(beyond.pressure_pa) == pytest.approx(
        expected_log10(1300.0), rel=0.0, abs=1.0e-12
    )
    assert math.log10(below.pressure_pa) == pytest.approx(
        expected_log10(900.0), rel=0.0, abs=1.0e-12
    )
    # Edge identity: 1/T continuation at T=Tb recovers the boundary value
    # (anti-cliff; no jump). Finite-diff approach the high edge from above.
    just_above = evaluator.evaluate(
        1200.0 + 1.0e-6, source_activity=1.0, pO2_bar=1.0
    )
    assert just_above.out_of_range is True
    assert math.log10(just_above.pressure_pa) == pytest.approx(
        math.log10(endpoint), abs=1.0e-6
    )


def test_sio_typed_evaluator_applies_oxygen_mass_action_once() -> None:
    evaluator = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml")
    ).evaluator_for("SiO")
    at_reference = evaluator.evaluate(
        1800.0, source_activity=1.0, pO2_bar=1.0e-9
    ).pressure_pa
    oxygen_rich = evaluator.evaluate(
        1800.0, source_activity=1.0, pO2_bar=1.0e-6
    ).pressure_pa

    assert evaluator.pO2_exponent == pytest.approx(-0.5)
    assert oxygen_rich / at_reference == pytest.approx(
        math.sqrt(1.0e-9 / 1.0e-6)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_stratum",
        "unbalanced",
        "no_reference",
        "missing_routing_field",
        "missing_code_field",
    ],
)
def test_compiler_rejects_ambiguous_or_invalid_schema(mutation: str) -> None:
    payload = deepcopy(_reaction_fixture())
    family = payload["families"]["potassium_test_family"]
    row = family["physical_properties"]["species"]["K"]
    model = row["pressure_models"][0]
    if mutation == "extra_stratum":
        family["runtime"] = {}
    elif mutation == "unbalanced":
        row["source_reactions"][0]["products"][1]["stoichiometry"] = 0.5
    elif mutation == "no_reference":
        model.pop("reference_pressure_model")
    elif mutation == "missing_routing_field":
        family["fiat_routing"].pop("process_or_terminal_destination")
    else:
        family["code_metadata"].pop("source_account")

    with pytest.raises(CatalogCompileError):
        compile_vapour_rail_catalog(payload)


def test_catalog_operating_envelope_no_nonphysical_pressure() -> None:
    """b-148 regression: no hot-train in-domain row yields P > 1e9 Pa.

    Sweep every *hot-train* compiled evaluator over a physical process
    envelope (T band × melt pO2 band × activity). Stage-0-only carriers
    (P-ladder, …) are excluded: they are gated off the hot train and
    their large negative pO2 powers at unit activity are a separate
    Stage-0 envelope question. A failure here means a stoichiometry /
    sign / units defect or an unbounded oxygen mass-action path — the
    class that produced AlO2 ~1e63 Pa in CI full-run dumps.
    """
    from simulator.physical_constants import (
        CATALOG_PHYSICAL_PRESSURE_CEILING_PA,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )

    catalog = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml"), emit_u0_request_rules=False
    )
    temperatures_K = (1400.0, 1600.0, 1800.0, 2000.0, 2200.0)
    # Process-representative melt pO2 band (inside the physical envelope).
    pO2_bars = (1.0e-12, 1.0e-9, 1.0e-6, 1.0e-3, 1.0, 10.0)
    activities = (1.0e-4, 0.01, 0.1, 1.0)
    ceiling = CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    offenders: list[str] = []
    assert MELT_DISSOCIATION_PO2_MAX_BAR >= 10.0

    for species_id, species in catalog.species.items():
        evaluator = species.evaluator
        if evaluator is None:
            continue
        hot = str(
            getattr(species.code_metadata, "hot_train_applicability", "") or ""
        )
        if hot in {"stage0_only", "not_applicable"}:
            continue
        for temperature_K in temperatures_K:
            for pO2_bar in pO2_bars:
                for activity in activities:
                    kwargs: dict[str, float] = {}
                    if evaluator.activity_exponent:
                        kwargs["source_activity"] = activity
                    if evaluator.pO2_exponent:
                        kwargs["pO2_bar"] = pO2_bar
                    try:
                        evaluation = evaluator.evaluate(temperature_K, **kwargs)
                    except CatalogCompileError:
                        continue
                    # In-domain only: OOR continuation may be multi-dex and is
                    # already status-bearing (t-538 / b-145). The b-148 class
                    # is non-physical pressure *claiming* a usable value.
                    if evaluation.out_of_range:
                        continue
                    if evaluation.pressure_pa > ceiling:
                        offenders.append(
                            f"{species_id}: T={temperature_K:g} K "
                            f"pO2={pO2_bar:g} bar a={activity:g} "
                            f"P={evaluation.pressure_pa:.3e} Pa "
                            f"pO2_exp={evaluator.pO2_exponent}"
                        )

    assert not offenders, (
        "catalog operating-envelope physical ceiling exceeded "
        f"(>{ceiling:g} Pa in-domain):\n" + "\n".join(offenders[:40])
    )


def test_alo2_pathological_fO2_no_longer_explodes() -> None:
    """b-148: AlO2 must not reach ~1e63 Pa when fed the 1e300 pO2 sentinel.

    Hand algebra of the pre-fix path:
      P = P_ref(a=1, pO2_ref=1) * a * (pO2 / pO2_ref)^0.25
      with pO2 = 1e300 → (1e300)^0.25 = 1e75, so unit-ref ~1e-8 Pa → ~1e67 Pa.
    After the physical pO2 envelope clamp, the same call is bounded by
    (100 bar)^0.25 ≈ 3.2 and must stay far below 1e9 Pa.
    """
    from simulator.physical_constants import CATALOG_PHYSICAL_PRESSURE_CEILING_PA

    catalog = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml"), emit_u0_request_rules=False
    )
    evaluator = catalog.evaluator_for("AlO2")
    assert evaluator.pO2_exponent == pytest.approx(0.25)
    assert evaluator.reference_model.physical_composite_ood is True

    # Unit-reference sanity: physical composite at a=1, pO2_ref=1 bar.
    unit = evaluator.evaluate(2000.0, source_activity=1.0, pO2_bar=1.0)
    assert unit.pressure_pa < 1.0e-3
    assert unit.pressure_pa > 0.0

    # Pathological fO2 sentinel that previously produced ~1e63–1e66 Pa dumps.
    exploded = evaluator.evaluate(
        1800.0 + 273.15, source_activity=0.1, pO2_bar=1.0e300
    )
    assert exploded.pressure_pa < CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    # And must be within a few dex of the unit-ref * a * (100)^0.25 bound.
    capped = evaluator.evaluate(
        1800.0 + 273.15, source_activity=0.1, pO2_bar=100.0
    )
    assert exploded.pressure_pa == pytest.approx(capped.pressure_pa, rel=0.0, abs=0.0)


def test_physical_melt_dissociation_pO2_bar_clamps_sentinel() -> None:
    from engines.builtin.vapor_pressure import physical_melt_dissociation_pO2_bar
    from simulator.physical_constants import (
        MELT_DISSOCIATION_PO2_MAX_BAR,
        MELT_DISSOCIATION_PO2_MIN_BAR,
    )

    p, clamped = physical_melt_dissociation_pO2_bar(300.0)
    assert clamped is True
    assert p == MELT_DISSOCIATION_PO2_MAX_BAR

    p, clamped = physical_melt_dissociation_pO2_bar(-9.0)
    assert clamped is False
    assert p == pytest.approx(1.0e-9)

    p, clamped = physical_melt_dissociation_pO2_bar(-400.0)
    assert clamped is True
    assert p == MELT_DISSOCIATION_PO2_MIN_BAR


def test_na_composites_base_matches_lh_monatomic_and_pins() -> None:
    """b-151: Na2/Na2O_gas must track monatomic L&H Pref (not retired pseudo).

    Premise: composite base_reference_pressure_model is the monatomic
    unit-activity Pref; activity is applied once at runtime. The retired
    VapoRock pseudo_psat (A=5.18586 / B=11127.434869 / C=1000) was an
    activity-folded effective pressure — restoring it reopens dual-attribution.
    Algebra: r_v ∝ r_base^ν_Na; unit-activity probes pin the post-fix surface.
    Unit Pa. Sanity: base coeffs byte-match monatomic Na; pin file points exist.
    """
    catalog = compile_vapour_rail_catalog(_yaml("vapor_pressures.yaml"))
    pins = _yaml("vapour_rail_validation_pins.yaml")
    na_ref = catalog.species["Na"].evaluator.reference_model.coefficients
    retired = {"A": 5.18586, "B": 11127.434869, "C": 1000.0}
    lh = {"A": 11.342243, "B": 12140.316409, "C": -163.701}

    assert na_ref["A"] == pytest.approx(lh["A"])
    assert na_ref["B"] == pytest.approx(lh["B"])
    assert na_ref["C"] == pytest.approx(lh["C"])

    for species_id in ("Na2", "Na2O_gas"):
        # Source YAML: composite base_reference must equal monatomic L&H Pref.
        row = None
        for family in _yaml("vapor_pressures.yaml")["families"].values():
            spp = (family.get("physical_properties") or {}).get("species") or {}
            if species_id in spp:
                row = spp[species_id]
                break
        assert row is not None
        model = row["pressure_models"][0]
        base = model["base_reference_pressure_model"]["coefficients"]
        assert base["A"] == pytest.approx(lh["A"])
        assert base["B"] == pytest.approx(lh["B"])
        assert base["C"] == pytest.approx(lh["C"])
        assert base["A"] != pytest.approx(retired["A"])

        probe = pins["species"][species_id]["validations"]["structural"][
            "unit_activity_probe"
        ]
        assert probe["activity"] == pytest.approx(1.0)
        assert probe["pO2_bar"] == pytest.approx(1.0)
        for point in probe["points"]:
            live = catalog.species[species_id].evaluator.evaluate(
                float(point["T_K"]),
                source_activity=1.0,
                pO2_bar=1.0,
            ).pressure_pa
            # Relative tolerance absorbs float/path noise; pins are honest
            # current-state values from the b-151 catalog probe, not tuned.
            assert live == pytest.approx(
                float(point["pinned_pressure_Pa"]), rel=1.0e-6, abs=0.0
            )

