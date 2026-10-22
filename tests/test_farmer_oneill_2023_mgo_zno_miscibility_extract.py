"""Source-grounded context pins for Farmer and O'Neill (2023)."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/farmer-oneill-2023-mgo-zno-miscibility.yaml"
CORPUS = regolith_corpus_root()
SOURCE_TEXT = CORPUS / "text/farmer-oneill-2023-mgo-zno-miscibility/pdftotext-layout.txt"

pytestmark = pytest.mark.skipif(
    not SOURCE_TEXT.is_file(), reason="source text is absent from REGOLITH_CORPUS_ROOT"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_context_tables_preserve_all_printed_rows_and_page_coordinates() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    context = document["species"]["ZnO"]["context"]
    tables = {
        row["locator"].get("table"): row
        for row in context
        if row["type"] in {"measured_table_context", "model_table_context"}
    }
    assert {key: len(row["values"]["rows_as_published"]) for key, row in tables.items()} == {
        "1": 17,
        "2": 39,
        "3": 18,
        "4": 11,
        "5": 7,
    }
    assert tables["1"]["values"]["rows_as_published"][0]["cells_as_printed"][-2:] == [
        "0.247(9)", "0.884(7)"
    ]
    assert tables["2"]["values"]["rows_as_published"][-2]["cells_as_printed"][-1] == "Liq 0.958(6)"
    assert tables["2"]["values"]["rows_as_published"][-1]["cells_as_printed"][-1] == "Liq 0.949a"
    assert tables["3"]["values"]["rows_as_published"][10]["cells_as_printed"][2] == "11.630(25)"
    assert tables["4"]["values"]["rows_as_published"][0][-1] == "−335.93 (5)"
    assert tables["5"]["values"]["rows_as_published"][-1][-1] == "−0.061 (1)"
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in context)
    assert len([row for row in context if row["type"] == "figure_context"]) == 7
    assert all(
        row["values"]["digitisation"] == "deferred to a figure pass"
        for row in context
        if row["type"] == "figure_context"
    )


def test_context_fidelity_pins_reject_a_mutated_printed_cell() -> None:
    document = _document()
    expected = {
        "row_label": "B6-170719",
        "cells_as_printed": ["B6-170719", "941", "20", "Box", "$PbF_2$", "Pt crucible", "0.247(9)", "0.884(7)"],
    }
    mutated = deepcopy(document)
    first_table = next(
        row for row in mutated["species"]["ZnO"]["context"]
        if row["observation_id"] == "farmer_table1_ambient_1atm_epma"
    )
    assert first_table["values"]["rows_as_published"][0] == expected
    first_table["values"]["rows_as_published"][0]["cells_as_printed"][1] = "942"
    assert first_table["values"]["rows_as_published"][0] != expected


def test_isolated_migration_has_no_typed_rows_or_hard_issues() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.observations == {}
    assert not migrator.result.validation.hard_issues
