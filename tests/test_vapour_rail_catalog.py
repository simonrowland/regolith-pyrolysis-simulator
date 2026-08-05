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
    assert len(legacy["oxide_vapors"]) == 2
    assert len(legacy["foulant_vapor"]) == 3
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
    assert set(legacy["oxide_vapors"]) == {"SiO", "CrO2"}
    assert set(legacy["foulant_vapor"]) == {"NaCl", "KCl", "NaF"}
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
        assert row["code_metadata"]["collision_only_suffix"] is True

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

    assert COLLISION_GASES.isdisjoint(formulas)
    assert COLLISION_GASES.isdisjoint(aliases)
    assert COLLISION_GASES.isdisjoint(catalog_specs)
    assert COLLISION_GASES.isdisjoint(formula_texts)


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


def test_anti_cliff_continuation_is_status_bearing_coating_conservative() -> None:
    """Out-of-domain continues with a coating-conservative point estimate.

    Rationale (b-118): the prior half-slope always under-stated outward
    volatility (anti-conservative for coating). The anti-cliff intent is
    preserved (nonzero, continuous from the domain edge); the continuation
    direction is conservative for coating so the estimate never under-states flux.
    """
    evaluator = compile_vapour_rail_catalog(_reaction_fixture()).evaluator_for("K")
    endpoint = evaluator.evaluate(
        1200.0, source_activity=1.0, pO2_bar=1.0
    ).pressure_pa
    beyond = evaluator.evaluate(1300.0, source_activity=1.0, pO2_bar=1.0)
    straight_log10 = math.log10(endpoint) + (2.0 / 200.0) * 100.0
    straight_pa = 10.0**straight_log10

    assert beyond.out_of_range is True
    assert beyond.status == OUT_OF_RANGE_STATUS
    assert beyond.acquisition_flag == "acquire:test:K"
    # Anti-cliff: strictly above the domain-edge value, never silent zero.
    assert beyond.pressure_pa > endpoint > 0.0
    # Coating-conservative estimate: at or above straight log-linear continuation.
    assert beyond.pressure_pa >= straight_pa
    assert (
        evaluator.evaluate(900.0, source_activity=1.0, pO2_bar=1.0).pressure_pa
        > 0.0
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
