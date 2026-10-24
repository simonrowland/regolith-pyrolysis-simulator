from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/jeon-2025-metal-slag-refractory-thesis.yaml"
RENDER_PINS = (
    ("jeonthesis_t8_s1_olivine_eds_mgo", "MgO", 61.1, 57),
    ("jeonthesis_t8_s19_zincite_epma_zno", "ZnO", 96.7, 57),
    ("jeonthesis_t8_s19_slag_eds_mgo", "MgO", 9.3, 58),
    ("jeonthesis_t8_s23_slag_epma_sio2", "SiO2", 54.2, 58),
)


def _document() -> dict:
    loaded = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _rows(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (species, row)
        for species, block in document["species"].items()
        for row in block.get("observations", [])
    }


def _assert_render_pins(document: dict, pins=RENDER_PINS) -> None:
    rows = _rows(document)
    for observation_id, species, expected, page in pins:
        actual_species, row = rows[observation_id]
        assert actual_species == species
        assert row["values"]["value"] == expected
        assert row["locator"]["page"] == page
        assert row["locator"]["pdf_page_index"] == page - 1
        assert row["locator"]["published_page"] == page - 12


def test_table8_render_pins_reject_mutated_grid_and_extract_cells() -> None:
    document = _document()
    _assert_render_pins(document)
    changed_grid = list(RENDER_PINS)
    changed_grid[0] = (changed_grid[0][0], "MgO", 61.0, 57)
    with pytest.raises(AssertionError):
        _assert_render_pins(document, changed_grid)
    changed_extract = deepcopy(document)
    _rows(changed_extract)[RENDER_PINS[0][0]][1]["values"]["value"] = 61.0
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_extract)


def test_thesis_only_table8_migrates_all_components_and_duplicate_tables_stay_context(tmp_path: Path) -> None:
    document = _document()
    result = _migrate_real_extract(
        tmp_path, "jeon-2025-metal-slag-refractory-thesis.yaml"
    )
    assert result.validation is not None
    assert not result.validation.hard_issues
    source_rows = _rows(document)
    assert len(source_rows) == 330
    migrated = {
        item.observation_id.rsplit("::", 1)[-1]: item
        for item in result.observations.values()
    }
    assert set(migrated) == set(source_rows)
    for observation_id, item in migrated.items():
        species, source = source_rows[observation_id]
        assert item.value.kind.value == "point"
        assert item.value.point == Decimal(str(source["values"]["value"]))
        assert item.identity.species.formula == species
        assert item.identity.temperature_K.is_value
        assert item.point_conditions["T_C"].state.is_value
        assert item.point_conditions["sample_id"].state.is_value
        assert item.point_conditions["composition"].state.is_value
        assert item.point_conditions["equilibration_time_hours"].state.is_value
        assert item.point_conditions["atmosphere"].state.is_value
        assert not item.notices
        if "uncertainty" in source:
            assert source["uncertainty"]["as_published"] not in {"± 0", "± 0.0"}
    assert migrated["jeonthesis_t8_s1_olivine_eds_mgo"].value.point == Decimal("61.1")
    assert migrated["jeonthesis_t8_s23_slag_epma_sio2"].value.point == Decimal("54.2")
    contexts = [row for block in document["species"].values() for row in block.get("context", [])]
    linked = [row for row in contexts if row.get("observation_id", "").startswith(("jeonthesis_t5_", "jeonthesis_t9_", "jeonthesis_t10_")) and row.get("values", {}).get("linked_observation_id")]
    assert len(linked) == 305
    assert {row["values"]["published_source_id"] for row in linked} == {
        "jeon-lindberg-2023-mg-ca-zn-oxide-equilibria",
        "jeon-lindberg-2025-mgo-refractory-slag-interface",
    }


def test_all_thesis_figures_are_deferred_records_without_numeric_points() -> None:
    document = _document()
    figures = [
        row
        for block in document["species"].values()
        for row in block.get("context", [])
        if row.get("observation_id", "").startswith("jeonthesis_figure_")
    ]
    assert len(figures) == 32
    assert {row["locator"]["figure"] for row in figures} == {
        str(number) for number in range(1, 33)
    }
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("value" not in row["values"] for row in figures)
    assert next(row for row in figures if row["locator"]["figure"] == "20")["locator"]["page"] == 56
    assert next(row for row in figures if row["locator"]["figure"] == "32")["locator"]["page"] == 70
