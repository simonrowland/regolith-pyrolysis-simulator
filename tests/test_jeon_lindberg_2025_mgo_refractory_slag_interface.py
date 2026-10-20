from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/jeon-lindberg-2025-mgo-refractory-slag-interface.yaml"
RENDER_GRID = (
    ("jeon25_2_mgo_al2o3_p3_mgo", "MgO", 12.7, 7),
    ("jeon25_2_mgo_cr2o3_p2_cr2o3", "Cr2O3", 50.9, 7),
    ("jeon25_2_mgo_c_p2_feo", "FeO", 52.0, 7),
    ("jeon25_3_mgo_al2o3_p5_al2o3", "Al2O3", 62.7, 8),
    ("jeon25_3_mgo_cr2o3_p4_cr2o3", "Cr2O3", 66.6, 8),
    ("jeon25_3_mgo_c_p1_sio2", "SiO2", 31.1, 8),
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


def _assert_grid(document: dict, grid=RENDER_GRID) -> None:
    rows = _rows(document)
    for observation_id, component, expected, page in grid:
        species, row = rows[observation_id]
        assert species == component
        assert row["values"]["component_as_published"] == component
        assert row["values"]["value"] == expected
        assert row["locator"]["page"] == page
        assert row["locator"]["pdf_page_index"] == page - 1


def test_table_rows_match_600dpi_render_pins_and_reject_mutations() -> None:
    document = _document()
    _assert_grid(document)
    changed_grid = list(RENDER_GRID)
    changed_grid[0] = (changed_grid[0][0], "MgO", 12.6, 5)
    with pytest.raises(AssertionError):
        _assert_grid(document, changed_grid)
    changed_extract = deepcopy(document)
    _rows(changed_extract)[RENDER_GRID[0][0]][1]["values"]["value"] = 12.6
    with pytest.raises(AssertionError):
        _assert_grid(changed_extract)


def test_isolated_migration_keeps_all_typed_points_species_conditions_and_temperature(tmp_path: Path) -> None:
    document = _document()
    result = _migrate_real_extract(
        tmp_path, "jeon-lindberg-2025-mgo-refractory-slag-interface.yaml"
    )
    assert result.validation is not None
    assert not result.validation.hard_issues
    source_rows = _rows(document)
    assert len(source_rows) == 203
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
        assert item.point_conditions["sample_id"].state.is_value
        assert not item.notices
    assert migrated["jeon25_2_mgo_al2o3_p3_mgo"].value.point == Decimal("12.7")
    assert migrated["jeon25_3_mgo_cr2o3_p4_cr2o3"].value.point == Decimal("66.6")
