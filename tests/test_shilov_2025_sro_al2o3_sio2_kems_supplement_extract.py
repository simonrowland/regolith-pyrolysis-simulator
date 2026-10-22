from __future__ import annotations

import copy
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tests.helpers.corpus_paths import regolith_corpus_root

ROOT = Path(__file__).resolve().parents[1]
SID = "shilov-2025-sro-al2o3-sio2-kems-supplement"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS_ROOT = regolith_corpus_root()
PDF = CORPUS_ROOT / "raw" / SID / f"{SID}.pdf"

# Independent transcription of every Table S1 row from the held supplement.
# The original is a Word .doc with no corpus PDF; no 600-dpi source render was
# available in this seat, as recorded in the extract and paper notes.
TABLE_S1_TRANSCRIPTION = (
    ("sample1", ("33", "33", "33"), "1883", "0.3", "3", "240"),
    ("sample1", ("33", "33", "33"), "1943", "0.7", "6", "412"),
    ("sample1", ("33", "33", "33"), "1987", "1.7", "9", "598"),
    ("sample1", ("33", "33", "33"), "2052", "4.5", "15", "1006"),
    ("sample1", ("33", "33", "33"), "2081", "6.6", "20", "1256"),
    ("sample1", ("33", "33", "33"), "2105", "9.6", "25", "1502"),
    ("sample2", ("30", "50", "20"), "2037", "5.0", "80", "2298"),
    ("sample2", ("30", "50", "20"), "1964", "1.8", "21", "1099"),
    ("sample2", ("30", "50", "20"), "2002", "3.6", "42", "1625"),
    ("sample2", ("30", "50", "20"), "2081", "9.0", "170", "3495"),
    ("sample3", ("30", "20", "50"), "2012", "1.3", "18", "1346"),
    ("sample3", ("30", "20", "50"), "2065", "4.5", "30", "2149"),
    ("sample3", ("30", "20", "50"), "1944", "0.5", "9", "711"),
    ("sample3", ("30", "20", "50"), "2030", "2.0", "21.5", "1582"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _actual_table_s1_grid(document: dict) -> tuple[tuple, ...]:
    records = {
        item["observation_id"]: (formula, item)
        for formula, block in document["species"].items()
        for item in block.get("observations", [])
    }
    actual = []
    for sample_id, composition_percent, temperature, *_ in TABLE_S1_TRANSCRIPTION:
        row_records = {}
        for formula, item in records.values():
            values = item["values"]
            row_sample = values.get("paired_sample_row_id", values.get("sample_id"))
            if row_sample != sample_id or values["temperature_K"] != temperature:
                continue
            column = values["table_column_as_printed"]
            if column == "Sample under study Sr+":
                row_records["Sr"] = str(values["ion_intensity"])
            elif column == "Sample under study SiO+":
                row_records["SiO"] = str(values["ion_intensity"])
            elif column == "Pure SrO Sr+":
                row_records["pure_SrO"] = str(values["ion_intensity"])
        composition = tuple(
            _document_composition(records, sample_id, temperature, key)
            for key in ("SrO", "Al2O3", "SiO2")
        )
        actual.append(
            (sample_id, composition, temperature, row_records.get("Sr"), row_records.get("SiO"), row_records.get("pure_SrO"))
        )
    return tuple(actual)


def _document_composition(records: dict, sample_id: str, temperature: str, oxide: str) -> str:
    for formula, item in records.values():
        values = item["values"]
        if (
            formula == "Sr"
            and values.get("sample_id") == sample_id
            and values.get("temperature_K") == temperature
            and values.get("table_column_as_printed") == "Sample under study Sr+"
        ):
            return str(values["composition_mol_percent_as_printed"][oxide])
    raise AssertionError(f"no composition row for {sample_id} at {temperature} K")


def test_table_s1_grid_and_both_cell_mutations_are_detected() -> None:
    document = _document()
    actual = _actual_table_s1_grid(document)
    assert actual == TABLE_S1_TRANSCRIPTION
    assert len(actual) == 14

    changed_grid = [list(row) for row in TABLE_S1_TRANSCRIPTION]
    changed_grid[0][3] = "0.31"
    with pytest.raises(AssertionError):
        assert actual == tuple(tuple(row) for row in changed_grid)

    changed_extract = copy.deepcopy(document)
    changed_cell = next(
        item
        for item in changed_extract["species"]["Sr"]["observations"]
        if item["observation_id"].endswith("sample1_Sr_at_1883K")
    )
    changed_cell["values"]["ion_intensity"] = "0.31"
    with pytest.raises(AssertionError):
        assert _actual_table_s1_grid(changed_extract) == TABLE_S1_TRANSCRIPTION


def test_isolated_migration_types_currents_conditions_and_species() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={SID: document["source"]}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 42
    assert set(result.observations) == {
        f"{SID}::{item['observation_id']}"
        for block in document["species"].values()
        for item in block.get("observations", [])
    }
    source_records = {
        f"{SID}::{item['observation_id']}": (formula, item)
        for formula, block in document["species"].items()
        for item in block.get("observations", [])
    }
    for identifier, migrated in result.observations.items():
        formula, source = source_records[identifier]
        values = source["values"]
        assert migrated.identity.species.formula == formula
        assert migrated.identity.species.phase.is_value
        assert migrated.identity.species.phase.value is Phase.G
        assert migrated.identity.quantity.value is Quantity.ION_INTENSITY
        assert migrated.value.kind is ValueKind.POINT
        assert migrated.value.point == Decimal(values["ion_intensity"])
        assert migrated.identity.temperature_K.is_value
        assert migrated.identity.temperature_K.value == Decimal(values["temperature_K"])
        assert migrated.point_conditions is not None
        temperature = migrated.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal(values["temperature_K"])
        assert temperature.locator is not None
        assert temperature.locator.page == 4
        assert temperature.locator.pdf_page_index == 3
        assert temperature.locator.table == "S1"
        assert migrated.identity.composition.tag.value == "not_applicable"
        if values.get("paired_sample_row_id"):
            expected_components = {"SrO": Decimal("1.0")}
            assert values["sample_id"] == "pure_SrO_reference"
            assert values["paired_sample_row_id"].startswith("sample")
        else:
            printed = values["composition_mol_percent_as_printed"]
            expected_components = {
                "SrO": Decimal(printed["SrO"]) / 100,
                "Al2O3": Decimal(printed["Al2O3"]) / 100,
                "SiO2": Decimal(printed["SiO2"]) / 100,
            }
            assert values["sample_id"] in {"sample1", "sample2", "sample3"}
        stored_composition = {
            key: Decimal(value)
            for key, value in values["composition_mol"].items()
            if key != "basis"
        }
        assert stored_composition == expected_components


def test_figure_records_are_deferred_and_point_free() -> None:
    document = _document()
    figures = [
        record
        for block in document["species"].values()
        for record in block.get("context", [])
        if record.get("what") == "figure_record"
    ]
    assert {record["locator"]["figure"] for record in figures} == {"S1", "S2"}
    for figure in figures:
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
        assert "points" not in figure["values"]


def test_source_pdf_page_locator_when_corpus_pdf_is_available() -> None:
    if not PDF.is_file():
        pytest.skip("source PDF absent; available original is a Word .doc")
    page_text = subprocess.run(
        ["pdftotext", "-f", "4", "-l", "4", "-layout", str(PDF), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Table S1" in page_text
