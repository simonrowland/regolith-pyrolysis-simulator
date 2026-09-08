"""Completeness, source round-trip, OCR, and exact-grid contracts for B677."""

import json
import re
from html.parser import HTMLParser

import pytest
import yaml

from simulator.reference_data.pankratz_1984_usbm_b677_loader import (
    COMPILATION_ROOT,
    PrintedTemperatureUnavailable,
    load_manifest,
    load_records,
    lookup_temperature,
)


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.row is not None and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def source_tables(path):
    source = path.read_text(encoding="utf-8")
    result = []
    for match in re.finditer(r"<table>[\s\S]*?</table>", source):
        parser = TableParser()
        parser.feed(match.group())
        result.append(parser.rows)
    return result


def test_round_trip_reparses_mineru_source_and_compares_every_printed_token():
    cache = {}
    for record in load_records():
        locator = record["source_locator"]
        path = COMPILATION_ROOT / locator["file"]
        tables = cache.setdefault(path, source_tables(path))
        printed = tables[locator["table_index"] - 1]
        assert printed == record["source_table_rows_raw"]
        for row in record["rows"]:
            for cell in row["cells"]:
                assert isinstance(cell["raw"], str)
                source_row = cell["source_row_index"]
                source_column = cell["source_column_index"]
                if source_row is None:
                    assert source_column is None and cell["raw"] == ""
                else:
                    assert cell["raw"] == printed[source_row][source_column]


def test_manifest_record_and_toc_census_coverage_are_identical():
    manifest = load_manifest()
    records = list(load_records())
    census = json.loads((COMPILATION_ROOT / "census.json").read_text(encoding="utf-8"))
    ids = [record["record_id"] for record in records]
    assert len(ids) == len(set(ids)) == 1571
    assert len(records) == manifest["summary"]["record_count"] == census["record_count"]
    assert ids == census["record_ids"]
    assert census["found_count"] == census["transcribed_count"] == 1571
    assert census["untranscribed_count"] == 0
    assert sum(section["record_count"] for section in census["sections"]) == 1571
    assert manifest["compilation_role"]["validation_measurement"] is False
    assert manifest["compilation_role"]["scoring_eligible"] is False


def test_feedstock_element_coverage_uses_existing_coverage_contract():
    from tools.harvest_atct_compilation import feedstock_coverage

    records = [{"formula": record.get("formula_as_published") or ""} for record in load_records()]
    assert feedstock_coverage(records) == load_manifest()["feedstock_element_coverage"]


def test_every_compilation_yaml_and_access_status_parse():
    yaml_paths = list(COMPILATION_ROOT.rglob("*.yaml"))
    yaml_paths.append(COMPILATION_ROOT.parent / "access-status.yaml")
    assert yaml_paths
    for path in yaml_paths:
        assert yaml.safe_load(path.read_text(encoding="utf-8")) is not None, path


def test_every_grid_token_is_a_printed_first_column_token():
    for record in load_records():
        assert record["temperature_grid"] == [row["cells"][0] for row in record["rows"]]
        for grid_cell in record["temperature_grid"]:
            assert grid_cell["value"] is not None
        assert lookup_temperature(record["record_id"], record["temperature_grid"][0]["value"])


@pytest.mark.parametrize("temperature", [0, 298.1, 299, 1234.567, float("nan"), float("inf")])
def test_refuses_non_grid_temperature_without_interpolation_or_default(temperature):
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("table-0007", temperature)


def test_transition_duplicates_and_footnote_marker_are_preserved():
    transition = lookup_temperature("table-0007", 1235.0)
    assert len(transition) == 2
    extrapolated = lookup_temperature("table-0007", 1800.0)
    assert extrapolated[0]["cells"][0]["raw"] == "1800*"
    assert extrapolated[0]["cells"][0]["footnote_markers"] == ["*"]


def test_horizontal_chapter_one_grid_is_transcribed_without_guessing():
    record = list(load_records())[3]
    assert record["record_id"] == "table-0004"
    assert record["source_orientation"] == "transposed_for_temperature_lookup"
    assert [row["cells"][0]["raw"] for row in record["rows"]] == ["911", "990", "1121"]
    assert lookup_temperature("table-0004", 990.0)[0]["cells"][1]["raw"] == "2.663"


def test_ocr_t_header_is_not_promoted_to_a_one_kelvin_grid_row():
    record = list(load_records())[392]
    assert record["record_id"] == "table-0393"
    assert record["header_rows_raw"][0][0] == "1"
    assert record["temperature_grid"][0]["raw"] == "298*"
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("table-0393", 1.0)


def test_ocr_disagreements_remain_raw_and_corrections_are_ledgered():
    manifest = load_manifest()
    assert manifest["summary"]["ocr_suspect_count"] > 0
    assert manifest["summary"]["identity_check_disagreement_count"] > 0
    assert manifest["summary"]["correction_count"] == 2
    assert manifest["corrections"][0]["record_id"] == "table-0393"
    assert manifest["corrections"][0]["raw_token"] == "Lungsten hexabromide (ideal gas)"
    assert manifest["corrections"][0]["printed_token"] == "Tungsten hexabromide (ideal gas)"
    assert manifest["corrections"][1]["record_id"] == "table-1167"
    assert manifest["corrections"][1]["printed_token"] == "Er2O3"
    suspects = [cell for record in load_records() for row in record["rows"] for cell in row["cells"] if cell["ocr_suspect"]]
    assert suspects
    assert all(cell["ocr_check"] == "raster_table_bbox_token_disagreement" for cell in suspects)


def test_unknown_record_refuses_without_default():
    with pytest.raises(KeyError):
        lookup_temperature("not-a-record", 298.0)
