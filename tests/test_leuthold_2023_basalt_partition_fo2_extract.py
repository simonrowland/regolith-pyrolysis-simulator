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
SOURCE_ID = "leuthold-2023-basalt-partition-fo2"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
TABLE1_GRID = (
    ("SiO2", "45.76", "47.09"),
    ("TiO2", "1.56", "2.14"),
    ("Al2O3", "13.58", "14.12"),
    ("Cr2O3", "0.11", "0.03"),
    ("Fe2O3", "12.38", "13.21"),
    ("MnO", "0.19", "0.2"),
    ("MgO", "12.42", "6.47"),
    ("CaO", "11.25", "10.43"),
    ("NiO", "0.04", "0.01"),
    ("Na2O", "2.23", "2.97"),
    ("K2O", "0.16", "0.57"),
    ("P2O5", "0.13", "0.20"),
    ("LOI", "0.51", "2.58"),
    ("Total", "100.32", "100.04"),
    ("Mg#", "0.67", "0.49"),
)
TABLE2_GRID = (
    ("olivine and clinopyroxene", "E*(V2+–V5+)", "−3.587", "0.059", "1"),
    ("olivine and clinopyroxene", "E*(V3+–V5+)", "−1.174", "0.037", "1"),
    ("olivine and clinopyroxene", "E*(V4+–V5+)", "−0.838", "0.123", "1"),
    ("clinopyroxene", "D(V2+)/D(Sc)", "0.228", "0.024", "2"),
    ("clinopyroxene", "D(V3+)/D(Sc)", "2.66", "0.05", "2"),
    ("clinopyroxene", "D(V4+)/D(Sc)", "0.286", "0.033", "2"),
    ("clinopyroxene", "D(V5+)/D(Sc)", "0.0134", "0.0006", "2"),
    ("clinopyroxene", "D(V2+)", "0.764", "0.059", "2"),
    ("clinopyroxene", "D(V3+)", "4.642", "0.058", "2"),
    ("clinopyroxene", "D(V4+)", "0.607", "0.046", "2"),
    ("clinopyroxene", "D(V5+)", "0.0169", "0.0006", "2"),
    ("olivine", "D(V2+)", "6.84", "0.05", "2"),
    ("olivine", "D(V3+)", "0.107", "0.002", "2"),
    ("olivine", "D(V4+)", "0.0809", "0.0016", "2"),
    ("olivine", "D(V5+)", "0.00270", "0.00004", "2"),
    ("plagioclase", "E*(Eu2+–Eu3+)", "−1.463", "0.004", "2"),
    ("plagioclase", "D(Eu2+)/D(Sr)", "1.047", "–", "3"),
    ("plagioclase", "D(Eu3+)/D(Sr)", "0.0080", "0.0023", "2"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context(document: dict, observation_id: str) -> dict:
    return next(
        item
        for item in document["species"]["SiO2"]["context"]
        if item["observation_id"] == observation_id
    )


def _table1_grid(document: dict) -> tuple[tuple[str, ...], ...]:
    rows = _context(
        document, "leuthold_2023_table_1_starting_material_compositions"
    )["values"]["rows"]
    return tuple(
        (row["component"], row["B62/2_picrite"], row["11JL33_basalt"])
        for row in rows
    )


def _table2_grid(document: dict) -> tuple[tuple[str, ...], ...]:
    rows = _context(document, "leuthold_2023_table_2_fit_parameters")["values"]["rows"]
    return tuple(
        (
            row["mineral"],
            row["parameter"],
            row["value"],
            row.get("SD", row.get("SD_as_printed", "")),
            row["source"],
        )
        for row in rows
    )


def test_render_transcription_of_tables_and_mutations() -> None:
    document = _document()
    actual_table1 = _table1_grid(document)
    actual_table2 = _table2_grid(document)
    assert actual_table1 == TABLE1_GRID
    assert actual_table2 == TABLE2_GRID

    changed_grid = list(TABLE1_GRID)
    changed_grid[0] = ("SiO2", "45.67", "47.09")
    with pytest.raises(AssertionError):
        assert actual_table1 == tuple(changed_grid)

    changed_extract = copy.deepcopy(document)
    changed_table = next(
        item
        for item in changed_extract["species"]["SiO2"]["context"]
        if item["observation_id"]
        == "leuthold_2023_table_1_starting_material_compositions"
    )
    changed_table["values"]["rows"][0]["B62/2_picrite"] = "45.67"
    with pytest.raises(AssertionError):
        assert _table1_grid(changed_extract) == TABLE1_GRID

    # The dash in the fit table is retained as printed, never converted to 0.
    assert actual_table2[16][3] == "–"


def test_all_figures_are_deferred_and_have_no_digitized_points() -> None:
    document = _document()
    figures = [
        item
        for item in document["species"]["SiO2"]["context"]
        if item.get("type") == "figure_record"
    ]
    assert len(figures) == 14
    assert all(
        item["values"]["digitisation"] == "deferred to a figure pass"
        and "points" not in item["values"]
        for item in figures
    )


def test_isolated_migration_keeps_glass_v_species_conditions_and_unknown_composition() -> None:
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
        row
        for row in result.observations.values()
        if row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(rows) == 2
    assert sorted(row.value.point for row in rows) == [Decimal("150"), Decimal("400")]
    expected = {
        Decimal("1190"): Decimal("400"),
        Decimal("1125"): Decimal("150"),
    }
    for row in rows:
        assert row.identity.species.formula == "V"
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value.value == "glass"
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal(
            str(row.point_conditions["T_C"].state.value + Decimal("273.15"))
        )
        assert row.point_conditions is not None
        assert row.point_conditions["T_C"].state.is_value
        assert row.point_conditions["T_C"].state.value in expected
        assert row.point_conditions["T_C"].state.value + Decimal("273.15") == row.identity.temperature_K.value
        assert row.value.point == expected[row.point_conditions["T_C"].state.value]
        assert row.point_conditions["delta_NNO"].state.is_value
        assert row.point_conditions["delta_NNO"].state.value == Decimal("-4")
        assert row.point_conditions["composition"].state.is_unknown
        assert not row.identity.fO2_Pa.is_value


def test_source_pages_contain_the_transcribed_tables_and_figure_labels() -> None:
    page2 = source_page_text(SOURCE_ID, 2)
    assert "Table 1" in page2
    assert "45.76" in page2 and "47.09" in page2

    page3 = source_page_text(SOURCE_ID, 3)
    assert "one-atmosphere" in page3
    assert "drop-quenched" in page3
    assert "into water" in page3
    assert "Online Resource 3" in page3

    page7 = source_page_text(SOURCE_ID, 7)
    assert "400" in page7 and "150" in page7 and "NNO-4" in page7

    page18 = source_page_text(SOURCE_ID, 18)
    assert "Table 2" in page18 and "− 3.587" in page18
    for page, figure_numbers in (
        (4, (1,)),
        (5, (2,)),
        (7, (3,)),
        (9, (4,)),
        (10, (5,)),
        (13, (6,)),
        (14, (7,)),
        (15, (8,)),
        (16, (9,)),
        (18, (10,)),
        (19, (11,)),
        (20, (12,)),
        (21, (13, 14)),
    ):
        text = source_page_text(SOURCE_ID, page)
        for figure_number in figure_numbers:
            assert f"Fig. {figure_number}" in text
