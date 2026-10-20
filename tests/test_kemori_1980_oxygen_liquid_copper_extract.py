"""Source and isolated-migration pins for Kemori et al. (1980)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = REPO_ROOT / "data/literature/extracts/kemori-1980-oxygen-liquid-copper.yaml"
TABLE4_RENDER_GRID = {
    "1508": ("0.87455", "0.12545", "0.22157", "0.77843"),
    "1523": ("0.86487", "0.13513", "0.24979", "0.75021"),
    "1543": ("0.84378", "0.15622", "0.26596", "0.73404"),
    "1558": ("0.82170", "0.17830", "0.33084", "0.66916"),
    "1573": ("0.80846", "0.19154", "0.35407", "0.64593"),
    "1583": ("0.78342", "0.21658", "0.39581", "0.60419"),
}
TABLE2_GAMMAS = [
    "0.287", "0.12", "0.205", "0.145", "0.120", "0.150", "0.195",
    "0.122", "0.204", "0.12", "0.128", "0.333", "0.115", "0.195",
    "0.286",
]


def _table4_grid(document: dict) -> dict[str, tuple[str, ...]]:
    grid: dict[str, list[str | None]] = {
        temperature: [None, None, None, None]
        for temperature in TABLE4_RENDER_GRID
    }
    for species, column in (("Cu", 0), ("Cu2O", 1)):
        for observation in document["species"][species]["observations"]:
            if "cu2o_liquid" in observation["observation_id"]:
                continue
            for point in observation["values"]["points"]:
                grid[str(point["T_K"])][column] = point["value"]
    for species, column in (("Cu", 2), ("Cu2O", 3)):
        for observation in document["species"][species]["observations"]:
            if "cu2o_liquid" not in observation["observation_id"]:
                continue
            for point in observation["values"]["points"]:
                grid[str(point["T_K"])][column] = point["value"]
    assert all(all(value is not None for value in row) for row in grid.values())
    return {temperature: tuple(row) for temperature, row in grid.items()}


def test_measured_infinite_dilution_coefficient_matches_table_2() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation = document["species"]["O"]["observations"][0]
    assert observation["type"] == "activity_coefficient"
    assert observation["standard_state"].startswith("Henry's law")
    assert observation["values"]["points"][0]["gamma"] == "0.287"
    assert observation["values"]["points"][0]["uncertainty"]["value"] == "0.005"
    assert observation["locator"]["pdf_page_index"] == 4

    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    migrated = next(
        observation for observation in result.observations.values()
        if observation.identity.species.formula == "O"
    )
    assert migrated.value.kind is ValueKind.POINT
    assert migrated.identity.species.formula == "O"
    assert migrated.identity.temperature_K.value == 1573
    assert migrated.value.point == Decimal("0.287")
    assert "temperature_K" in migrated.point_conditions
    assert migrated.point_conditions["temperature_K"].state.is_value
    assert migrated.point_conditions["temperature_K"].locator is not None


def test_table4_grid_matches_independent_600_dpi_transcription() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert _table4_grid(document) == TABLE4_RENDER_GRID
    mutated_grid = deepcopy(TABLE4_RENDER_GRID)
    mutated_grid["1508"] = ("0.87456", *mutated_grid["1508"][1:])
    assert _table4_grid(document) != mutated_grid

    mutated_extract = deepcopy(document)
    mutated_extract["species"]["Cu"]["observations"][0]["values"]["points"][0]["value"] = "0.87456"
    with pytest.raises(AssertionError):
        assert _table4_grid(mutated_extract) == TABLE4_RENDER_GRID


def test_all_migrated_table4_values_keep_species_temperature_and_composition() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    table4 = [
        observation for observation in result.observations.values()
        if observation.identity.species.formula in {"Cu", "Cu2O"}
    ]
    assert len(table4) == 24
    assert all(observation.value.kind is ValueKind.POINT for observation in table4)
    assert {observation.identity.species.formula for observation in table4} == {"Cu", "Cu2O"}
    assert all(observation.identity.temperature_K.is_value for observation in table4)
    assert all("temperature_K" in observation.point_conditions for observation in table4)
    assert all("composition" in observation.point_conditions for observation in table4)
    assert all(
        observation.point_conditions["composition"].state.is_value
        and observation.point_conditions["composition"].locator is not None
        for observation in table4
    )


def test_every_scored_table2_and_table4_cell_migrates_as_its_printed_point() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = list(migrator.result.observations.values())
    assert len(observations) == 39
    assert all(observation.value.kind is ValueKind.POINT for observation in observations)
    oxygen = [row for row in observations if row.identity.species.formula == "O"]
    assert sorted(row.value.point for row in oxygen) == sorted(
        Decimal(value) for value in TABLE2_GAMMAS
    )
    assert all(row.identity.temperature_K.is_value for row in oxygen)
    assert all("temperature_K" in row.point_conditions for row in oxygen)
    assert all(
        row.point_conditions["temperature_K"].state.is_value
        and row.point_conditions["temperature_K"].locator is not None
        for row in oxygen
    )
