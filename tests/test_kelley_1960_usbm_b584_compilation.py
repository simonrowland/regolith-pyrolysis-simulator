"""Completeness, source round-trip, OCR, and exact-grid contracts for B584."""

import json
import runpy
import re
from html.parser import HTMLParser

import pytest
import yaml

from simulator.reference_data.kelley_1960_usbm_b584_loader import (
    COMPILATION_ROOT,
    PrintedTemperatureUnavailable,
    load_manifest,
    load_records,
    lookup_temperature,
)


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def _source_rows(html):
    parser = _TableParser()
    parser.feed(html)
    return parser.rows


def _numeric_cells(value):
    if isinstance(value, dict):
        if "raw" in value and "value" in value:
            yield value
        for child in value.values():
            yield from _numeric_cells(child)
    elif isinstance(value, list):
        for child in value:
            yield from _numeric_cells(child)


def test_round_trip_reparses_mineru_source_tokens():
    sources = [json.loads(line) for line in (COMPILATION_ROOT / "source/mineru-tables.jsonl").read_text().splitlines()]
    records = list(load_records())
    assert len(sources) == len(records) == 893
    for source, record in zip(sources, records, strict=True):
        parsed = _source_rows(source["html"])
        assert record["column_labels_as_published"] == parsed[0]
        assert record["source_rows"] == parsed
        for cell in _numeric_cells(record):
            assert isinstance(cell["raw"], str)
            if cell["value"] is not None:
                token = cell.get("numeric_token") or cell["raw"]
                assert cell["value"] == float(token.replace(",", ""))
                assert cell["ocr_check"] in {"raster_ocr_token_agreement", "raster_ocr_token_disagreement"}


def test_cached_source_matches_original_mineru_decode():
    harvest = runpy.run_path(str(COMPILATION_ROOT / "harvest.py"))
    if not harvest["MINERU_ROOT"].exists():
        pytest.skip("read-only regolith corpus is not mounted")
    live = harvest["mineru_blocks"]()
    cached = [json.loads(line) for line in (COMPILATION_ROOT / "source/mineru-tables.jsonl").read_text().splitlines()]
    assert [{key: value for key, value in block.items() if key != "raster_ocr"} for block in cached] == live


def test_raster_agreement_is_position_aligned_not_table_global():
    harvest = runpy.run_path(str(COMPILATION_ROOT / "harvest.py"))
    rows = [["T", "H", "S"], ["400", "100", "1.0"], ["500", "100", "2.0"]]
    aligned = harvest["raster_alignment"](rows, "400 100 1.0 500 999 2.0")
    assert (1, 1) in aligned
    assert (2, 1) not in aligned


def test_manifest_record_and_bulletin_census_coverage_match():
    manifest = load_manifest()
    records = list(load_records())
    census = json.loads((COMPILATION_ROOT / "census.json").read_text())
    ids = [record["record_id"] for record in records]
    assert len(ids) == len(set(ids)) == 893
    assert len(records) == manifest["summary"]["record_count"] == manifest["census"]["record_count"] == census["record_count"]
    assert ids == manifest["census"]["record_ids"] == census["record_ids"]
    assert [record["census_table_number"] for record in records] == list(range(1, 894))
    assert manifest["summary"]["untranscribed_record_count"] == 0
    assert manifest["compilation_role"]["validation_measurement"] is False
    assert manifest["compilation_role"]["scoring_eligible"] is False


def test_every_compilation_yaml_and_access_status_parses():
    yaml_paths = list(COMPILATION_ROOT.rglob("*.yaml"))
    yaml_paths.append(COMPILATION_ROOT.parent / "access-status.yaml")
    for path in yaml_paths:
        assert yaml.safe_load(path.read_text()) is not None, path


@pytest.mark.parametrize("temperature", [0, 399.9, 401, 99999, float("nan"), float("inf")])
def test_refuses_every_non_grid_temperature(temperature):
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("table-001", temperature)


def test_exact_printed_grid_and_unknown_record_contract():
    record = next(load_records())
    temperature = next(cell["value"] for cell in record["temperature_grid"] if cell["value"] is not None)
    assert lookup_temperature(record["record_id"], temperature)
    with pytest.raises(KeyError):
        lookup_temperature("not-a-record", temperature)


def test_source_numbering_error_is_retained_not_repaired():
    records = list(load_records())
    assert records[41]["census_table_number"] == 42
    assert records[42]["census_table_number"] == 43
    assert records[41]["table_number_as_printed_or_ocr"] == 42
    assert records[42]["table_number_as_printed_or_ocr"] == 42
    assert any(item["kind"] == "table_number_disagreement" for item in records[42]["ambiguities"])


def test_caption_metadata_wins_without_hiding_census_ocr_disagreements():
    records = list(load_records())
    assert records[0]["formula_as_published"] == "Ac"
    assert records[0]["phase_as_published"] == "(c, l)"
    assert records[10]["formula_as_published"] == "AlD"
    assert records[10]["phase_as_published"] == "(g)"
    assert any(item["kind"] == "census_caption_substance_disagreement" for item in records[10]["ambiguities"])
    census_ambiguities = [item for entry in load_manifest()["census"]["entries"] for item in entry["ambiguities"]]
    assert any(item["kind"] == "census_table_number_ocr_disagreement" for item in census_ambiguities)
    assert any(item["kind"] == "census_page_ocr_disagreement" for item in census_ambiguities)


def test_plausible_ocr_confusions_are_never_promoted_to_numbers():
    for record in load_records():
        for cell in _numeric_cells(record["rows"]):
            confusion_surface = re.sub(r"\([^)]*\)", "", cell["raw"])
            if re.search(r"[lIOSB]", confusion_surface, re.IGNORECASE):
                assert cell["ocr_suspect"]
                assert cell["value"] is None
