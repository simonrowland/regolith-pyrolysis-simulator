from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, PhaseRole, Quantity, UncertaintyKind, ValueKind
from simulator.battery.identity import celsius_to_kelvin
from tests.battery.factories import migrate_one_extract
from tests.battery.literature_extract_grids import (
    assert_observation_grid_matches,
    decimal_text,
    read_observation_grid_fixture,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/babaian-2024-fe-mg-si-air-equilibria.yaml"
RENDER_GRID = ROOT / "tests/fixtures/babaian-2024-render-grid.tsv"
GRID_FIELDS = (
    "observation_id",
    "species_formula",
    "sample_id_as_published",
    "experiment",
    "T_C",
    "phase_label_as_published",
    "assemblage_as_published",
    "basis",
    "component_amount",
    "component_amount_as_published",
    "sigma",
    "page",
    "table",
    "method_class",
)


def _read_fixture() -> list[dict[str, str]]:
    return read_observation_grid_fixture(
        RENDER_GRID, ("T_C", "component_amount", "sigma")
    )


def _extract_grid(document: dict) -> list[dict[str, str]]:
    rows = []
    for species_formula, species in document["species"].items():
        records = list(species.get("observations") or []) + [
            row
            for row in (species.get("context") or [])
            if row.get("values", {}).get("quantity_as_published")
            == "equilibrium_phase_composition"
        ]
        for observation in records:
            values = observation["values"]
            amount = values.get("component_amount")
            if amount is None:
                amount = values["component_amount_as_published"].split()[0]
            rows.append(
                {
                    "observation_id": observation["observation_id"],
                    "species_formula": values.get("formula", species_formula),
                    "sample_id_as_published": values["sample_id_as_published"],
                    "experiment": observation["experiment"],
                    "T_C": decimal_text(values["T_C"]),
                    "phase_label_as_published": values["phase_label_as_published"],
                    "assemblage_as_published": values["assemblage_as_published"],
                    "basis": values["basis"],
                    "component_amount": decimal_text(amount),
                    "component_amount_as_published": str(values["component_amount_as_published"]),
                    "sigma": decimal_text((observation.get("uncertainty") or {}).get("sigma")),
                    "page": str(observation["locator"]["page"]),
                    "table": str(observation["locator"]["table"]),
                    "method_class": values["method_class"],
                }
            )
    return sorted(rows, key=lambda row: row["observation_id"])


def _assert_grid_matches(document: dict, fixture: list[dict[str, str]]) -> None:
    assert_observation_grid_matches(_extract_grid(document), fixture, GRID_FIELDS)


def test_render_grid_covers_typed_components_and_rejects_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    fixture = _read_fixture()
    _assert_grid_matches(document, fixture)
    assert len(fixture) == 168

    figure_records = [
        record
        for species in document["species"].values()
        for record in (species.get("context") or [])
        if record.get("type") == "figure_record"
    ]
    assert {record["values"]["figure"]: record["locator"]["page"] for record in figure_records} == {
        1: 3,
        2: 5,
        3: 8,
        4: 8,
        5: 9,
        6: 9,
        7: 9,
    }
    assert all(record["values"]["points"] == [] for record in figure_records)
    assert all(record["values"]["digitisation"] == "deferred to a figure pass" for record in figure_records)

    mutated_grid = deepcopy(fixture)
    mutated_grid[0]["component_amount"] = str(Decimal(mutated_grid[0]["component_amount"]) + 1)
    with pytest.raises(AssertionError):
        _assert_grid_matches(document, mutated_grid)

    mutated_extract = deepcopy(document)
    first_id = _extract_grid(mutated_extract)[0]["observation_id"]
    first = next(
        observation
        for species in mutated_extract["species"].values()
        for observation in (species.get("observations") or [])
        if observation["observation_id"] == first_id
    )
    first["values"]["component_amount"] += 1
    with pytest.raises(AssertionError):
        _assert_grid_matches(mutated_extract, fixture)


def test_every_typed_liquid_component_migrates_as_a_point_with_identity_and_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    fixture = _read_fixture()
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    observations = {
        observation.observation_id.split("::", 1)[1]: observation
        for observation in migrator.result.observations.values()
    }
    zero_rows = [row for row in fixture if Decimal(row["component_amount"]) == 0]
    expected_ids = {
        row["observation_id"] for row in fixture if Decimal(row["component_amount"]) > 0
    }
    assert len(observations) == len(expected_ids) == 165
    assert set(observations) == expected_ids
    assert not migrator.result.validation.hard_issues
    context = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in (species.get("context") or [])
    }
    for row in zero_rows:
        item = context[row["observation_id"]]
        assert item["type"] == "context_only"
        assert item["values"]["basis"] == "mole_fraction"
        assert item["values"]["component_amount_as_published"] == "0 mol.%"
        assert "component_amount" not in item["values"]
        assert item["locator"]["page"] == int(row["page"])

    for row in fixture:
        if Decimal(row["component_amount"]) == 0:
            continue
        observation = observations[row["observation_id"]]
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(row["component_amount"])
        assert Decimal(0) < observation.value.point <= Decimal(1)
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.formula == row["species_formula"]
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.phase_role.value is PhaseRole.MELT
        assert observation.identity.subtype.value == row["basis"]
        assert observation.identity.temperature_K.value == celsius_to_kelvin(Decimal(row["T_C"]))
        temperature = observation.point_conditions["T_C"]
        assert temperature.state.value == Decimal(row["T_C"])
        assert temperature.locator is not None
        assert temperature.locator.page == int(row["page"])
        assert temperature.locator.pdf_page_index == int(row["page"]) - 1
        assert observation.point_conditions["atmosphere"].state.value == "air"
        if row["sigma"]:
            assert observation.uncertainty.kind is UncertaintyKind.PRINTED
            assert Decimal(str(observation.uncertainty.verbatim["sigma"])) == Decimal(row["sigma"])
            assert observation.uncertainty.verbatim["units"] == "mol.%"
        else:
            assert observation.uncertainty.kind is UncertaintyKind.NONE
        assert not observation.notices

    assert sum(row["table"] == "2" for row in fixture) == 147
    assert sum(row["table"] == "2" and Decimal(row["component_amount"]) > 0 for row in fixture) == 144
    assert sum(row["table"] == "3" for row in fixture) == 21
    assert sum(row["method_class"] == "quoted_attributed" for row in fixture) == 18
