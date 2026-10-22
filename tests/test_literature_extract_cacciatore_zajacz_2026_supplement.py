"""Displayed-cell pins for the Cacciatore and Zajacz supplementary data."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import migrate
from literature_extract_test_utils import isolated_root
from tests.helpers.corpus_paths import regolith_corpus_root

ROOT = Path(__file__).resolve().parents[1]
SID = "cacciatore-zajacz-2026-v-sc-oxybarometer-supplement"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"


def _context(document: dict, worksheet: str) -> dict:
    return next(
        record
        for record in document["species"]["SiO2"]["context"]
        if record.get("locator", {}).get("table") == worksheet
    )


def _cell(record: dict, row_number: int, column: str) -> str:
    row = next(row for row in record["values"]["rows"] if row["row"] == row_number)
    return row["cells"][column]


def _assert_displayed_pins(document: dict, s10_c3: str = "-11.5") -> None:
    assert _cell(_context(document, "Table_2_Exp_cond"), 2, "E") == "-4.31"
    assert _cell(_context(document, "Table_2_Exp_cond"), 2, "G") == "1030"
    assert _cell(_context(document, "Table_S1_RunCondAdd"), 3, "H") == "-7.01"
    assert _cell(_context(document, "Table_S1_RunCondAdd"), 3, "L") == "1030"
    assert _cell(_context(document, "Table_S10_Lit_V&VScExpCpx&Opx"), 3, "C") == s10_c3
    assert _cell(_context(document, "Table_S10_Lit_V&VScExpCpx&Opx"), 3, "G") == "1310"
    assert _cell(_context(document, "Table_3_glass_EPMA&LAICPMS"), 3, "A") == "E1_0_II H2_A_F"
    assert _cell(_context(document, "Table_3_glass_EPMA&LAICPMS"), 3, "B") == "52.08"


def test_spreadsheet_cells_figure_inventory_and_mutations_fail() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    contexts = document["species"]["SiO2"]["context"]
    tables = [record for record in contexts if record["type"] == "supplementary_workbook_table_context"]
    figures = [record for record in contexts if record["type"] == "figure_record"]
    assert len(tables) == 19
    assert len(figures) == 7
    assert sum(record["values"]["displayed_cell_count"] for record in tables) == 97442
    assert all(record["values"]["digitisation"] == "deferred to a figure pass" for record in figures)
    _assert_displayed_pins(document)

    changed_extract = copy.deepcopy(document)
    _context(changed_extract, "Table_S10_Lit_V&VScExpCpx&Opx")["values"]["rows"][2]["cells"]["C"] = "-10.5"
    with pytest.raises(AssertionError):
        _assert_displayed_pins(changed_extract)

    changed_pin = "-10.5"
    with pytest.raises(AssertionError):
        _assert_displayed_pins(document, s10_c3=changed_pin)


def test_supplement_context_migrates_without_scored_observations(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = migrate(isolated_root(tmp_path, document), write=False)
    assert not result.observations
    assert result.validation is None or not result.validation.hard_issues


def test_supplement_external_workbook_check_skips_without_corpus() -> None:
    corpus = regolith_corpus_root()
    source = corpus / f"raw/{SID}/{SID}.zip"
    if not source.is_file():
        pytest.skip("Cacciatore and Zajacz supplement archive is absent")

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_displayed_pins(document)
