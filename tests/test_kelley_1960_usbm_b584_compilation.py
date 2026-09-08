"""Completeness, source round-trip, OCR, and exact-grid contracts for B584."""

import json
import runpy
import re

import pytest
import yaml

from simulator.reference_data.kelley_1960_usbm_b584_loader import (
    COMPILATION_ROOT,
    OCRSuspectRow,
    PrintedTemperatureUnavailable,
    load_manifest,
    load_records,
    lookup_temperature,
)


IMAGE_VERIFIED_CELLS = {
    ("table-012", 6, 0, "heat_content"): ("5.280", 5280.0),
    ("table-036", 1, 1, "temperature"): ("1.000", 1000.0),
    ("table-058", 1, 1, "entropy_increment"): ("9,43", 9.43),
    ("table-141", 4, 0, "heat_content"): ("3.510", 3510.0),
    ("table-146", 1, 1, "entropy_increment"): ("84,59", 84.59),
    ("table-183", 7, 1, "heat_content"): ("15.750", 15750.0),
    ("table-187", 2, 1, "heat_content"): ("12.040", 12040.0),
    ("table-270", 6, 1, "heat_content"): ("19.655", 19655.0),
    ("table-311", 5, 0, "entropy_increment"): ("9,72", 9.72),
    ("table-353", 1, 1, "temperature"): ("1.000", 1000.0),
    ("table-417", 7, 0, "heat_content"): ("4.675", 4675.0),
    ("table-452", 2, 1, "heat_content"): ("11.500", 11500.0),
    ("table-500", 2, 1, "heat_content"): ("25.540", 25540.0),
    ("table-568", 2, 0, "heat_content"): ("3.580", 3580.0),
    ("table-743", 2, 0, "entropy_increment"): ("29,38", 29.38),
    ("table-755", 7, 1, "heat_content"): ("43.830", 43830.0),
    ("table-812", 6, 0, "heat_content"): ("5.325", 5325.0),
    ("table-882", 3, 1, "entropy_increment"): ("12.26", 13.26),
    ("table-882", 5, 0, "heat_content"): ("4.260", 4260.0),
    ("table-883", 2, 0, "heat_content"): ("5.120", 5120.0),
}


def _numeric_cells(value):
    if isinstance(value, dict):
        if "raw" in value and "value" in value:
            yield value
        for child in value.values():
            yield from _numeric_cells(child)
    elif isinstance(value, list):
        for child in value:
            yield from _numeric_cells(child)


def _assert_image_verified_cells(records, expected):
    by_id = {record["record_id"]: record for record in records}
    for (record_id, source_row_index, panel_index, column), (ocr_token, value) in expected.items():
        row = next(
            row
            for row in by_id[record_id]["rows"]
            if row["source_row_index"] == source_row_index and row["panel_index"] == panel_index
        )
        assert row["cells"][column]["raw"] == ocr_token
        assert row["cells"][column]["value"] == value


def test_stored_values_match_independent_image_verified_fixture():
    _assert_image_verified_cells(list(load_records()), IMAGE_VERIFIED_CELLS)


def test_image_verified_fixture_mutation_is_detected():
    mutated = dict(IMAGE_VERIFIED_CELLS)
    key = ("table-500", 2, 1, "heat_content")
    mutated[key] = ("25.540", 25.54)
    with pytest.raises(AssertionError):
        _assert_image_verified_cells(list(load_records()), mutated)


def test_image_verified_corrections_are_applied_and_retain_ocr_suspect():
    manifest = load_manifest()
    corrections = []
    for record in load_records():
        for correction in record["corrections"]:
            row = next(
                row
                for row in record["rows"]
                if row["source_row_index"] == correction["source_row_index"]
                and row["panel_index"] == correction["panel_index"]
            )
            cell = row["cells"][correction["column"]]
            assert cell["raw"] == correction["ocr_token"]
            assert cell["value"] == float(correction["printed_token"].replace(",", ""))
            assert cell["ocr_suspect"] is True
            assert correction["printed_token"] in correction["quote"]
            corrections.append({"record_id": record["record_id"], **correction})
    assert len(corrections) == manifest["summary"]["correction_count"] == 60
    assert corrections == manifest["corrections"]


def test_punctuation_anomaly_class_has_image_verified_corrections():
    flagged = set()
    corrected = set()
    for record in load_records():
        for row in record["rows"]:
            for column, cell in row["cells"].items():
                raw = cell.get("numeric_token") or cell["raw"]
                inconsistent = (
                    column == "heat_content" and re.match(r"^[+-]?\d{1,3}\.\d{3}(?:\D|$)", raw)
                ) or (
                    column == "temperature" and re.match(r"^\d{1,3}\.\d{3}(?:\D|$)", raw)
                ) or (
                    column == "entropy_increment" and re.search(r"\d,\d{2}(?:\D|$)", raw)
                )
                if inconsistent:
                    flagged.add((record["record_id"], row["source_row_index"], row["panel_index"], column))
        corrected.update(
            (record["record_id"], item["source_row_index"], item["panel_index"], item["column"])
            for item in record["corrections"]
            if item["ocr_token"] != "12.26"
        )
    assert len(flagged) == 59
    assert flagged == corrected


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
    assert lookup_temperature("table-001", 400)
    with pytest.raises(KeyError):
        lookup_temperature("not-a-record", 400)


def test_refuses_ocr_suspect_row_with_typed_error():
    with pytest.raises(OCRSuspectRow, match="heat_content"):
        lookup_temperature("table-500", 1200)


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
