from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/jeon-lindberg-2023-mg-ca-zn-oxide-equilibria.yaml"

# Independently transcribed from the 600 dpi physical page 5, Table 2.
RENDER_GRID = (
    (1, "Monoxide", "MgO", 85.4),
    (1, "Monoxide", "ZnO", 14.6),
    (1, "Zincite", "MgO", 0.0),
    (1, "Zincite", "ZnO", 100.0),
    (7, "Monoxide#1", "CaO", 1.3),
    (7, "Monoxide#1", "MgO", 81.3),
    (7, "Monoxide#1", "ZnO", 17.4),
    (7, "Monoxide#2", "CaO", 93.4),
)


def _document() -> dict:
    loaded = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
    }


def _assert_grid(document: dict, grid=RENDER_GRID) -> None:
    rows = _rows(document)
    for sample, phase, species, expected in grid:
        row = next(
            row for row in rows.values()
            if row["point_conditions"]["sample_id"]["state"]["value"] == str(sample)
            and row["phase_as_published"] == phase
            and row["values"]["component_as_published"] == species
        )
        assert row["values"]["value"] == expected
        assert row["locator"]["page"] == 5
        assert row["locator"]["pdf_page_index"] == 4
        assert row["locator"]["published_page"] == 12739


def test_table2_grid_matches_600dpi_render_and_rejects_cell_mutations() -> None:
    document = _document()
    _assert_grid(document)
    changed_grid = list(RENDER_GRID)
    changed_grid[0] = (1, "Monoxide", "MgO", 85.3)
    with pytest.raises(AssertionError):
        _assert_grid(document, changed_grid)
    changed_extract = deepcopy(document)
    row = _rows(changed_extract)["jeon23_s1_r3_mgo"]
    row["values"]["value"] = 85.3
    with pytest.raises(AssertionError):
        _assert_grid(changed_extract)


def test_isolated_migration_preserves_every_point_species_temperature_and_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path, "jeon-lindberg-2023-mg-ca-zn-oxide-equilibria.yaml"
    )
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 102
    source_rows = _rows(_document())
    migrated = {
        item.observation_id.rsplit("::", 1)[-1]: item
        for item in result.observations.values()
    }
    assert set(migrated) == set(source_rows)
    expected_species = {
        row["observation_id"]: species
        for species, block in _document()["species"].items()
        for row in block.get("observations", [])
    }
    for key, item in migrated.items():
        assert item.value.kind.value == "point"
        assert item.identity.species.formula == expected_species[key]
        assert item.identity.temperature_K.is_value
        assert item.point_conditions["sample_id"].state.is_value
        assert item.point_conditions["atmosphere"].state.is_value
        sample = str(item.point_conditions["sample_id"].state.value)
        time = item.point_conditions["equilibration_time_hours"].state
        if sample in {"13", "14"}:
            assert time.is_unknown
        else:
            assert time.is_value
        for field in ("initial_composition_as_published", "composition"):
            composition = item.point_conditions[field].state
            if sample in {"13", "14", "15", "16"}:
                assert composition.is_unknown
        assert not item.notices
        if item.uncertainty.kind.value == "printed":
            assert item.uncertainty.verbatim["reported_status"] == "printed"
            assert item.uncertainty.verbatim["as_published"] != "± 0"
    assert migrated["jeon23_s1_r3_mgo"].value.point == Decimal("85.4")
    assert migrated["jeon23_s1_r3_zno"].value.point == Decimal("14.6")
    assert migrated["jeon23_s7_r15_cao"].value.point == Decimal("1.3")


def test_table_row_count_and_table1_condition_disposition() -> None:
    document = _document()
    rows = _rows(document)
    assert len(rows) == 102
    assert {row["point_conditions"]["sample_id"]["state"]["value"] for row in rows.values()} == {str(i) for i in range(1, 17)}
    assert all(
        row["point_conditions"]["equilibration_time_hours"]["state"]["tag"] == "unknown"
        for row in rows.values()
        if row["point_conditions"]["sample_id"]["state"]["value"] in {"13", "14"}
    )
    assert all(
        row["point_conditions"][field]["state"]["tag"] == "unknown"
        for row in rows.values()
        if row["point_conditions"]["sample_id"]["state"]["value"] in {"13", "14", "15", "16"}
        for field in ("initial_composition_as_published", "composition")
    )

    bad_time = deepcopy(document)
    bad_row = next(
        row for row in _rows(bad_time).values()
        if row["point_conditions"]["sample_id"]["state"]["value"] == "13"
    )
    bad_row["point_conditions"]["equilibration_time_hours"]["state"] = {"tag": "value", "value": 10}
    with pytest.raises(AssertionError):
        assert bad_row["point_conditions"]["equilibration_time_hours"]["state"]["tag"] == "unknown"

    context = document["species"]["SiO2"]["context"]
    footnote = next(row for row in context if row["observation_id"] == "jeon23_table2_p2o5_bound_context")
    assert footnote["values"]["bound_as_published"] == "<0.1 mol%"
    assert footnote["values"]["temperature_as_published"] == "1000 °C"
    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 11
    assert {row["locator"]["figure"] for row in figures} == {str(i) for i in range(1, 12)}
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("points" not in row["values"] for row in figures)
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in figures)
