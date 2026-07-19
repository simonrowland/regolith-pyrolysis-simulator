from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.cost_parameters import (
    RECIPE_COST_PARAMETERS_KEY,
    SHUTTLE_REAGENT_SPECIES,
    cost_parameter_values,
    cost_parameters_from_mapping,
    default_cost_parameters_block,
    recipe_cost_parameters_from_payload,
)
from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.recipe_io import (
    load_recipe_patch,
    read_recipe_cost_parameters,
    save_recipe_to_library,
    write_recipe_patch,
)


DATA_DIGESTS = {
    "setpoints": "setpoints-digest",
    "feedstocks": "feedstock-digest",
    "foulant_thermo": "foulant-thermo-digest",
    "materials": "materials-digest",
    "vapor_pressures": "vapor-digest",
    "species_catalog": "species-catalog-digest",
    "profile": "profile-digest",
}


def _spec(cost_parameters: dict) -> EvalSpec:
    return EvalSpec(
        recipe_id="recipe-id",
        feedstock_recipe_digest="feedstock-recipe-digest",
        feedstock_id="lunar_mare_low_ti",
        profile_id="profile-id",
        fidelity="stub",
        code_version=current_code_version(),
        data_digests=DATA_DIGESTS,
        cost_parameters=cost_parameters,
    )


def _with_value(block: dict, name: str, value: float) -> dict:
    changed = copy.deepcopy(block)
    changed["parameters"][name]["value"] = value
    return changed


def test_yaml_defaults_load_with_recipe_default_provenance() -> None:
    block = default_cost_parameters_block()
    values = cost_parameter_values(block)["parameters"]

    assert values["electricity_cost_per_kWh"] == pytest.approx(10.0)
    assert values["solar_heat_cost_per_kWh"] == pytest.approx(0.05)
    assert values["furnace_resinter_cost_usd"] > 0.0
    assert values["depreciation_expense_per_run"] > 0.0
    assert set(values["shuttle_reagent_replacement_cost_per_kg"]) == SHUTTLE_REAGENT_SPECIES

    defaulted = recipe_cost_parameters_from_payload({}, source="legacy.recipe.yaml")
    assert defaulted["provenance"]["defaults_applied"] is True
    assert "legacy.recipe.yaml" in defaulted["provenance"]["recipe_source"]
    assert cost_parameter_values(defaulted) == cost_parameter_values(block)


def test_recipe_round_trip_carries_cost_block(tmp_path: Path) -> None:
    recipe = {
        "campaigns": {
            "C3": {
                "alkali_dosing": {
                    "Na_kg": 12.0,
                }
            }
        },
        RECIPE_COST_PARAMETERS_KEY: _with_value(
            default_cost_parameters_block(),
            "electricity_cost_per_kWh",
            0.42,
        ),
    }
    path = tmp_path / "winner.recipe.yaml"

    write_recipe_patch(path, recipe)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert RECIPE_COST_PARAMETERS_KEY in raw
    assert raw[RECIPE_COST_PARAMETERS_KEY]["parameters"]["electricity_cost_per_kWh"]["value"] == pytest.approx(0.42)
    assert load_recipe_patch(path)["campaigns"]["C3"]["alkali_dosing"]["Na_kg"] == pytest.approx(12.0)
    loaded_costs = read_recipe_cost_parameters(path)
    assert cost_parameter_values(loaded_costs)["parameters"]["electricity_cost_per_kWh"] == pytest.approx(0.42)


def test_library_save_preserves_recipe_cost_block(tmp_path: Path) -> None:
    cost_parameters = _with_value(
        default_cost_parameters_block(),
        "electricity_cost_per_kWh",
        0.42,
    )
    source = tmp_path / "winner.recipe.yaml"
    source.write_bytes(
        b"'cost_parameters': "
        + yaml.safe_dump(
            cost_parameters,
            default_flow_style=True,
            sort_keys=False,
            width=100_000,
        ).encode("utf-8")
        + yaml.safe_dump(
            {
                "campaigns": {"C3": {"alkali_dosing": {"Na_kg": 12.0}}},
            },
            sort_keys=False,
        ).encode("utf-8")
    )
    source_block = source.read_bytes().split(b"campaigns:", 1)[0]

    destination = save_recipe_to_library(
        source,
        "saved",
        library_dir=tmp_path / "recipes",
    )

    destination_block = destination.read_bytes().split(b"campaigns:", 1)[0]
    assert destination_block == source_block


def test_evalspec_hash_tracks_cost_values_but_not_cost_provenance() -> None:
    base = default_cost_parameters_block()
    moved = _with_value(
        base,
        "electricity_cost_per_kWh",
        base["parameters"]["electricity_cost_per_kWh"]["value"] + 0.01,
    )
    provenance_only = copy.deepcopy(base)
    provenance_only["provenance"]["source"] = "another-file.yaml"

    assert cache_key(_spec(moved)) != cache_key(_spec(base))
    assert cache_key(_spec(provenance_only)) == cache_key(_spec(base))

    solar_moved = _with_value(
        base,
        "solar_heat_cost_per_kWh",
        base["parameters"]["solar_heat_cost_per_kWh"]["value"] + 0.01,
    )
    assert cache_key(_spec(solar_moved)) != cache_key(_spec(base))


def test_shuttle_static_replacement_exception_is_exact_species_set() -> None:
    params = cost_parameters_from_mapping(default_cost_parameters_block())

    assert set(params.shuttle_reagent_replacement_cost_per_kg) == {"Na", "K", "Mg", "Ca"}
    for species in ("Na", "K", "Mg", "Ca"):
        assert params.reagent_cost_per_kg(species) == pytest.approx(
            params.shuttle_reagent_replacement_cost_per_kg[species]
        )
    assert params.reagent_cost_per_kg("Fe") == pytest.approx(params.generic_reagent_cost_per_kg)


def test_cost_parameter_sources_are_reproducible() -> None:
    parameters = default_cost_parameters_block()["parameters"]

    electricity_source = parameters["electricity_cost_per_kWh"]["source_tag"]
    assert electricity_source == "owner-t7-two-price-energy-v1"
    assert parameters["solar_heat_cost_per_kWh"]["source_tag"] == electricity_source
    for name in ("furnace_resinter_cost_usd", "depreciation_expense_per_run"):
        assert "owner ratified 2026-07-12" in parameters[name]["source_tag"]
    assert "owner ratified 2026-07-12" in parameters[
        "shuttle_reagent_replacement_cost_per_kg"
    ]["source_tag"]
