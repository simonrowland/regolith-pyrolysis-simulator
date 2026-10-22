"""Source-grounded pins for the evaluated Janz molten-salt compilation."""

from copy import deepcopy
import os
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/janz-1979-nsrds61-part2-molten-salt-properties.yaml"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
SID = "janz-1979-nsrds61-part2-molten-salt-properties"
SOURCE_TEXT = CORPUS / "text" / SID / "pdftotext-layout.txt"

pytestmark = pytest.mark.skipif(
    not SOURCE_TEXT.is_file(), reason="source text is absent from REGOLITH_CORPUS_ROOT"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_relevant_evaluated_property_tables_keep_numeric_cells_and_physical_pages() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    context = document["species"]["Na2O"]["context"]
    assert len(context) == 84
    assert sum(row["type"] == "measured_table_context" for row in context) == 28
    assert sum(row["type"] == "model_table_context" for row in context) == 56
    by_key = {row["locator"]["table_key"]: row for row in context}
    estimated_fusion = {
        "p206-t277", "p271-t358", "p280-t372", "p289-t385", "p299-t402",
        "p319-t426", "p334-t447", "p343-t456", "p373-t489",
    }
    assert all(by_key[key]["type"] == "model_table_context" for key in estimated_fusion)
    fusion = by_key["p020-t18"]
    assert fusion["values"]["caption_as_printed"] == "Table 1.6. Heat of fusion"
    assert fusion["locator"]["section_header_as_printed"][0] == "LiF"
    assert fusion["locator"]["page"] == 20
    assert fusion["values"]["printed_table_grid"][-1] == ["6.43", "$\\sim \\pm 0.8\\%$"]
    heat_capacity = by_key["p021-t19"]
    assert heat_capacity["locator"]["page"] == 21
    assert heat_capacity["values"]["printed_table_grid"][1][0] == "1120"
    assert heat_capacity["values"]["printed_table_grid"][1][2] == "1280"
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in context)
    assert all(row["values"]["printed_table_grid"] for row in context)


def test_pinned_printed_cell_mutation_is_detected() -> None:
    document = _document()
    expected = ["6.43", "$\\sim \\pm 0.8\\%$"]
    fusion = next(
        row for row in document["species"]["Na2O"]["context"]
        if row["locator"].get("table_key") == "p020-t18"
    )
    assert fusion["values"]["printed_table_grid"][-1] == expected
    mutated = deepcopy(document)
    row = next(
        row for row in mutated["species"]["Na2O"]["context"]
        if row["locator"].get("table_key") == "p020-t18"
    )
    row["values"]["printed_table_grid"][-1][0] = "6.42"
    assert row["values"]["printed_table_grid"][-1] != expected


def test_context_only_migration_has_no_unavailable_observations_or_hard_issues() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.observations == {}
    assert not migrator.result.validation.hard_issues
