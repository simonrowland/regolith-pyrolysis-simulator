"""Source-grounded pins for the context-only Al–Ti thermophysical tables."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/brillo-2025-al-ti-thermophysical.yaml"
CORPUS = regolith_corpus_root()
SID = "brillo-2025-al-ti-thermophysical"
SOURCE_TEXT = CORPUS / "text" / SID / "pdftotext-layout.txt"

pytestmark = pytest.mark.skipif(
    not SOURCE_TEXT.is_file(), reason="source text is absent from REGOLITH_CORPUS_ROOT"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_requested_tables_figures_and_physical_pages_are_retained() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    context = document["species"]["Al"]["context"]
    tables = [row for row in context if row["type"].endswith("table_context")]
    figures = [row for row in context if row["type"] == "figure_context"]
    assert len(tables) == 14
    assert len(figures) == 23
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in context)
    by_key = {row["locator"]["table_key"]: row for row in tables}
    density_alloys = by_key["p027-t12"]
    assert density_alloys["locator"]["page"] == 27
    assert density_alloys["values"]["printed_table_grid"][1] == [
        "Ti", "1941", "4.16", "-3.99", "4.184", "11.45"
    ]
    assert by_key["p036-t20"]["values"]["printed_table_grid"][-1][0] == "2"
    assert by_key["p036-t20"]["values"]["printed_table_grid"][-1][1].startswith("$-13.844")
    assert "50.03" in by_key["p051-t25"]["values"]["printed_table_grid"][1][2]
    table_21 = by_key["p053-t26"]
    assert "caption says emissivity" in table_21["values"]["not_scored_reason"]
    by_figure = {row["locator"]["figure"]: row for row in figures}
    assert by_figure["13"]["locator"]["page"] == 38
    assert by_figure["26"]["locator"]["page"] == 50
    assert "linear fits of Eq. 55" in by_figure["26"]["values"]["caption"]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)


def test_pinned_render_cell_mutation_is_detected() -> None:
    document = _document()
    expected = ["Ti", "1941", "4.16", "-3.99", "4.184", "11.45"]
    table = next(
        row for row in document["species"]["Al"]["context"]
        if row["locator"].get("table_key") == "p027-t12"
    )
    assert table["values"]["printed_table_grid"][1] == expected
    mutated = deepcopy(document)
    row = next(
        row for row in mutated["species"]["Al"]["context"]
        if row["locator"].get("table_key") == "p027-t12"
    )
    row["values"]["printed_table_grid"][1][2] = "4.15"
    assert row["values"]["printed_table_grid"][1] != expected


def test_context_only_migration_has_no_unavailable_observations_or_hard_issues() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.observations == {}
    assert not migrator.result.validation.hard_issues
