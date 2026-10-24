from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import iter_locators, source_page_text


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "boulliung-wood-2023-sulfur-solubility"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
TABLE1_COLUMNS = (
    "Composition", "SiO2", "Al2O3", "MgO", "CaO", "FeO", "TiO2",
    "K2O", "Na2O", "MnO", "P2O5", "Total", "n",
)
TABLE1_GRID = (
    ("T-Basalt", "47.0 (1)", "16.4 (1)", "4.3 (1)", "9.4 (1)", "10.3 (2)", "3.33 (4)", "1.87 (3)", "4.68 (4)", "0.21 (2)", "1.24", "98.7", "10"),
    ("Lunar Basalt", "42.3 (2)", "11.9 (1)", "7.5 (1)", "9.6 (1)", "16.9 (1)", "8.34 (6)", "0.20 (3)", "0.88 (2)", "0.58 (2)", "–", "98.2", "10"),
    ("NIB", "49.6 (1)", "14.6 (1)", "8.7 (1)", "12.4 (1)", "9.0 (1)", "0.92 (2)", "0.21 (6)", "2.25 (6)", "0.17 (5)", "0.30", "98.2", "10"),
    ("Nephelinite", "41.7 (1)", "14.2 (1)", "8.0 (1)", "13.9 (1)", "9.9 (1)", "2.68 (1)", "3.21 (3)", "3.95 (4)", "2.45 (2)", "–", "99.9", "8"),
    ("Andac", "61.6 (9)", "19.0 (9)", "2.0 (2)", "4.1 (3)", "4.9 (3)", "1.04 (4)", "4.60 (9)", "0.87 (3)", "b.d.l", "–", "98.1", "8"),
    ("B-A", "53.9 (5)", "14.6 (2)", "9.4 (2)", "9.9 (2)", "6.4 (1)", "0.61 (1)", "1.34 (5)", "2.79 (6)", "b.d.l", "–", "99.0", "8"),
    ("Andesite", "61.1 (6)", "18.0 (2)", "3.5 (1)", "6.9 (2)", "3.9 (1)", "0.61 (1)", "1.19 (4)", "4.07 (6)", "b.d.l", "", "99.2", "10"),
    ("Basanite", "43.8 (1)", "10.9 (1)", "13.1 (1)", "9.4 (1)", "11.5 (1)", "2.28 (3)", "1.89 (2)", "3.94 (4)", "0.18 (5)", "1.45", "98.4", "10"),
    ("T-Phonolite", "53.3 (9)", "22.9 (9)", "1.9 (1)", "4.8 (3)", "4.4 (1)", "1.50 (6)", "3.64 (9)", "7.77 (9)", "b.d.l", "–", "100.4", "8"),
    ("SLC", "39.9 (1)", "10.9 (1)", "15.4 (1)", "17.3 (1)", "14.4 (1)", "b.d.l", "b.d.l", "0.13 (3)", "b.d.l", "–", "98.0", "8"),
    ("CMAS1", "48.4 (1)", "16.8 (2)", "12.4 (1)", "22.3 (1)", "b.d.l", "b.d.l", "b.d.l", "b.d.l", "b.d.l", "–", "100.1", "10"),
    ("CMAS2", "45.5 (1)", "18.7 (1)", "17.4 (1)", "18.4 (1)", "b.d.l", "b.d.l", "b.d.l", "b.d.l", "b.d.l", "–", "100.1", "10"),
    ("CMAS3", "50.4 (1)", "15.6 (1)", "14.5 (1)", "19.6 (1)", "b.d.l", "b.d.l", "b.d.l", "b.d.l", "b.d.l", "–", "100.2", "10"),
)
TABLE2_COLUMNS = (
    "Temperature (K)", "CO", "CO2", "SO2", "log fO2", "FMQ", "log fS2", "t (h)",
)
TABLE2_GRID = (
    ("1473", "110", "70", "70", "− 9.40", "− 1.1", "− 1.05", "8"),
    ("1523", "103", "87", "60", "− 8.98", "− 1.24", "− 1.15", "8"),
    ("1573", "148", "99", "48", "− 8.98", "− 1.76", "− 1.21", "8"),
    ("1623", "175", "30", "45", "− 8.99", "− 2.26", "− 1.08", "8"),
    ("1673", "200", "12.5", "37.5", "− 8.97", "− 2.71", "− 1.15", "8"),
    ("1723", "208", "22", "20", "− 8.99", "− 3.16", "− 1.44", "6"),
    ("1773", "219", "19", "12", "− 8.94", "− 3.52", "− 1.61", "3"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context_grid(document: dict, observation_id: str, columns: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    context = document["species"]["S"]["context"]
    observation = next(item for item in context if item["observation_id"] == observation_id)
    return tuple(
        tuple(str(row[column]) for column in columns)
        for row in observation["values"]["rows"]
    )


def test_render_transcription_binds_both_printed_tables_and_mutations() -> None:
    document = _document()
    actual_table1 = _context_grid(
        document, "boulliung_wood_2023_table_1_starting_materials", TABLE1_COLUMNS
    )
    actual_table2 = _context_grid(
        document, "boulliung_wood_2023_table_2_run_conditions", TABLE2_COLUMNS
    )
    assert len(TABLE1_GRID) == 13
    assert all(len(row) == len(TABLE1_COLUMNS) for row in TABLE1_GRID)
    assert len(TABLE2_GRID) == 7
    assert all(len(row) == len(TABLE2_COLUMNS) for row in TABLE2_GRID)
    assert actual_table1 == TABLE1_GRID
    assert actual_table2 == TABLE2_GRID

    # Each mutation must fail the same full-grid comparison independently.
    changed_grid = list(TABLE1_GRID)
    changed_grid[0] = (changed_grid[0][0], "47.1 (1)", *changed_grid[0][2:])
    with pytest.raises(AssertionError):
        assert actual_table1 == tuple(changed_grid)

    changed_extract = copy.deepcopy(document)
    table1 = next(
        item for item in changed_extract["species"]["S"]["context"]
        if item["observation_id"] == "boulliung_wood_2023_table_1_starting_materials"
    )
    table1["values"]["rows"][0]["SiO2"] = "47.1 (1)"
    with pytest.raises(AssertionError):
        assert _context_grid(
            changed_extract, "boulliung_wood_2023_table_1_starting_materials", TABLE1_COLUMNS
        ) == TABLE1_GRID

    assert actual_table1[6][10] == ""
    assert actual_table1[1][10] == "–"
    assert actual_table1[4][9] == "b.d.l"


def test_isolated_migration_preserves_species_and_every_printed_condition() -> None:
    document = _document()
    assert all(
        locator["page"] == locator["pdf_page_index"] + 1
        for locator in iter_locators(document)
        if "page" in locator and "pdf_page_index" in locator
    )

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert not result.validation.hard_issues

    rows = [
        row for row in result.observations.values()
        if row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(rows) == 2
    assert sorted(row.value.point for row in rows) == [Decimal("760"), Decimal("2433")]
    expected_fO2_log = {
        Decimal("1473"): Decimal("-9.40"),
        Decimal("1673"): Decimal("-8.97"),
    }
    for row in rows:
        assert row.identity.species.formula == "S"
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value.value == "glass"
        assert row.identity.temperature_K.is_value
        assert row.identity.fO2_Pa.is_not_applicable
        assert row.identity.total_pressure_Pa.is_not_applicable
        assert row.identity.composition.is_not_applicable
        assert row.point_conditions is not None
        conditions = row.point_conditions
        assert conditions["temperature_K"].state.is_value
        assert conditions["temperature_K"].state.value == row.identity.temperature_K.value
        assert conditions["fO2_log"].state.is_value
        assert conditions["fO2_log"].state.value == expected_fO2_log[row.identity.temperature_K.value]
        assert conditions["fS2_log"].state.is_value
        assert conditions["delta_FMQ"].state.is_value
        assert conditions["total_pressure_Pa"].state.is_unknown
        assert conditions["composition"].state.is_unknown


def test_source_pages_match_each_table_measurement_and_figure_locator() -> None:
    page3 = source_page_text(SOURCE_ID, 3)
    assert "Table 1" in page3 and "Table 2" in page3
    page4 = source_page_text(SOURCE_ID, 4)
    assert "760 ± 25" in page4 and "2433 ± 39" in page4
    assert "quenched in the reduced S-bearing atmosphere" in page4
    for page, figure_numbers in (
        (5, (1, 2)), (6, (3,)), (7, (4,)), (8, (5,)),
        (9, (6,)), (10, (7,)), (11, (8,)), (12, (9,)),
    ):
        text = source_page_text(SOURCE_ID, page)
        for figure_number in figure_numbers:
            assert f"Fig. {figure_number}" in text
