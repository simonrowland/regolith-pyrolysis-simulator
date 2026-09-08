"""Coverage, source-token fidelity, OCR safety, and exact-grid tests for B592."""

from __future__ import annotations

import copy
import inspect
import json
import math
import re
from html.parser import HTMLParser

import pytest
import yaml

from simulator.reference_data import kelley_king_1961_usbm_b592_loader as b592_loader
from simulator.reference_data.kelley_king_1961_usbm_b592_loader import (
    COMPILATION_ROOT,
    OCRSuspectRow,
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
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def _parse_html_table(source):
    parser = _TableParser()
    parser.feed(source)
    return parser.rows


def _records():
    return list(load_records(include_ocr_suspect=True))


def _numeric_cells(value):
    if isinstance(value, dict):
        if "raw" in value and "value" in value:
            yield value
        for child in value.values():
            yield from _numeric_cells(child)
    elif isinstance(value, list):
        for child in value:
            yield from _numeric_cells(child)


def _assert_fixture(records, fixture):
    by_id = {record["record_id"]: record for record in records}
    for expected in fixture["cells"]:
        cell = by_id[expected["record_id"]]["rows"][expected["row_index"]]["cells"][expected["column"]]
        assert cell["raw"] == expected["raw"]
        assert math.isclose(cell["value"], expected["value"], rel_tol=0.0, abs_tol=1e-18)
        assert expected["image_quote"]
    for expected in fixture.get("metadata", []):
        record = by_id[expected["record_id"]]
        for key in ("substance_as_published", "formula_as_published", "phase_as_published", "footnote_markers"):
            assert record[key] == expected[key]
        assert expected["image_quote"]


def test_image_verified_fixture_matches_records():
    fixture = json.loads((COMPILATION_ROOT / "source/image-verified-fixture.json").read_text())
    _assert_fixture(_records(), fixture)


def test_image_verified_fixture_mutation_is_detected():
    fixture = json.loads((COMPILATION_ROOT / "source/image-verified-fixture.json").read_text())
    mutated = copy.deepcopy(_records())
    target = fixture["cells"][0]
    record = next(record for record in mutated if record["record_id"] == target["record_id"])
    record["rows"][target["row_index"]]["cells"][target["column"]]["raw"] = "36.89"
    with pytest.raises(AssertionError):
        _assert_fixture(mutated, fixture)


def test_every_record_round_trips_to_cached_native_source_tokens():
    blocks = [
        json.loads(line)
        for line in (COMPILATION_ROOT / "source/mineru-tables.jsonl").read_text().splitlines()
    ]
    for record in _records():
        block = blocks[record["source_ref"]["line"] - 1]
        source_rows = _parse_html_table(block["html"])
        for row in record["rows"]:
            source = source_rows[row["source_row_index"]]
            if record["corrections"]:
                corrected_columns = {item["column"] for item in record["corrections"]}
                for column, cell in row["cells"].items():
                    if column in corrected_columns:
                        assert any(
                            item["column"] == column and item["printed_token"] == cell["raw"] and item["quote"]
                            for item in record["corrections"]
                        )
                    elif "raw" in cell:
                        assert cell["raw"] in source
            elif record["table_number"] == 7 and len(source) == 3:
                assert row["raw"][1:] == source
            else:
                assert row["raw"] == source
        for cell in _numeric_cells(record["rows"]):
            assert isinstance(cell["raw"], str)
            assert isinstance(cell["parsed_values"], list)
            assert cell["value"] == (cell["parsed_values"][0] if cell["parsed_values"] else None)
            if cell["value"] is None and re.search(r"\d", cell["raw"]):
                assert cell["ocr_suspect"]


def test_manifest_record_and_bulletin_census_coverage_match():
    manifest = load_manifest(include_ocr_suspect=True)
    records = _records()
    census = json.loads((COMPILATION_ROOT / "census.json").read_text())
    ids = [record["record_id"] for record in records]
    assert len(ids) == len(set(ids))
    assert len(records) == manifest["summary"]["record_count"] == census["record_count"] == 1418
    assert manifest["census"] == census
    assert ids == census["record_ids"]
    assert census["numbered_table_count"] == 7
    assert census["physical_table_block_count"] == 25
    assert census["record_counts_by_table"] == {"1": 6, "2": 1, "3": 1, "4": 6, "5": 12, "6": 1321, "7": 71}
    assert [(item["table_number"], item["printed_page_range"], item["physical_block_count"]) for item in census["tables"]] == [
        (1, [7, 7], 1),
        (2, [9, 9], 1),
        (3, [9, 9], 1),
        (4, [10, 10], 1),
        (5, [12, 12], 1),
        (6, [101, 117], 17),
        (7, [118, 120], 3),
    ]
    assert manifest["summary"]["untranscribed_record_count"] == 0
    assert manifest["untranscribed"] == []
    assert manifest["compilation_role"]["validation_measurement"] is False
    assert manifest["compilation_role"]["scoring_eligible"] is False


def test_every_yaml_under_compilation_and_access_status_parses():
    yaml_paths = list(COMPILATION_ROOT.rglob("*.yaml")) + [COMPILATION_ROOT.parent / "access-status.yaml"]
    assert yaml_paths
    for path in yaml_paths:
        assert yaml.safe_load(path.read_text(encoding="utf-8")) is not None, path


def test_formula_keys_have_no_whitespace_and_metadata_clean_requires_image_check():
    for record in _records():
        if record["formula"] is not None:
            assert not re.search(r"\s", record["formula"]), record["record_id"]
        if not record["metadata_ocr_suspect"]:
            assert record["metadata_ocr_check"] in {
                "raster_ocr_text_agreement",
                "image_verified_printed_superscript_footnote_marker",
            }
        assert record["formula_as_published"] is None or isinstance(record["formula_as_published"], str)
        assert isinstance(record["name_as_published"], str)


def test_actinium_image_proven_row_alignment_correction_is_explicit():
    record = next(record for record in _records() if record["record_id"] == "table-006-0001")
    assert record["substance_as_published"] == "Ac(c)"
    assert record["heading_context_as_published"] == "Actinium:"
    assert record["rows"][0]["cells"]["entropy_other_sources"]["raw"] == "15.0±1.0"
    assert record["rows"][0]["cells"]["entropy_recommended"]["value"] == 15.0
    assert len(record["corrections"]) == 2
    for correction in record["corrections"]:
        assert correction["page"] == 101
        assert correction["printed_token"] == correction["ocr_token"] == "15.0±1.0"
        assert correction["quote"]
        assert record["rows"][0]["cells"][correction["column"]]["ocr_suspect"]
    assert load_manifest(include_ocr_suspect=True)["summary"]["correction_count"] == 54


def test_formula_integrity_census_is_fully_image_corrected():
    records = _records()
    corrections = [
        correction
        for record in records
        for correction in record["corrections"]
        if correction["kind"] == "image_verified_metadata_token_correction"
    ]
    issues = [
        (record["record_id"], field, token, reason)
        for record in records
        for field, token, reason in b592_loader._formula_integrity_issues(record)
    ]
    assert issues == []
    assert len(corrections) == 16
    assert all(
        correction["record_id"]
        and correction["page"]
        and correction["printed_token"]
        and correction["ocr_token"]
        and correction["quote"]
        for correction in corrections
    )
    assert {correction["record_id"] for correction in corrections} == {
        "table-006-0049",
        "table-006-0052",
        "table-006-0053",
        "table-006-0300",
        "table-006-0380",
        "table-006-0447",
        "table-006-0536",
        "table-006-0541",
        "table-006-0678",
        "table-006-0777",
        "table-006-0932",
        "table-006-1014",
        "table-006-1095",
        "table-006-1196",
        "table-006-1248",
        "table-006-1269",
    }


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("DCIO(g)", "uppercase I in a Cl-shaped formula position"),
        ("$IF_8(g)$", "invalid iodine-fluoride stoichiometry"),
        ("$IR_6(g)$", "unknown chemical element token(s): R"),
        ("$Mg(OH_2(c)$", "unbalanced chemical-formula delimiters"),
        ("Antimony-Con.", "continued-section label is not a chemical formula"),
        ("O10F2(c)", "orphaned wrapped-formula continuation"),
    ],
)
def test_formula_integrity_predicate_covers_each_reviewed_failure_class(token, reason):
    issues = b592_loader._formula_integrity_issues(
        {"formula": token, "formula_as_published": token, "name_as_published": token}
    )
    assert any(issue_reason == reason for _, _, issue_reason in issues)


def test_hno2_superscript_one_is_a_printed_footnote_marker_not_a_repair():
    record = next(record for record in _records() if record["record_id"] == "table-006-0470")
    assert record["substance_as_published"] == "HNO2(equ1,g)"
    assert record["formula"] == record["formula_as_published"] == "HNO2(equ1,g)"
    assert record["footnote_markers"] == "1"
    assert not record["metadata_ocr_suspect"]
    assert record["metadata_ocr_check"] == "image_verified_printed_superscript_footnote_marker"
    assert not record["corrections"]
    assert record["metadata_annotations"] == [
        {
            "record_id": "table-006-0470",
            "pdf_page": 111,
            "page": 107,
            "source_row_index": 31,
            "column": "substance",
            "kind": "image_verified_printed_superscript_footnote_marker",
            "printed_token": "HNO2(equ¹,g)",
            "footnote_marker": "1",
            "quote": "HNO2(equ¹,g) ... 60.8±0.3; ¹equ = equilibrium.",
            "basis": "The 300-dpi PDF page render proves 1 is a printed superscript footnote marker, not an OCR error or phase qualifier.",
        }
    ]


def test_image_proven_merged_rows_are_split_without_cross_substance_values():
    by_id = {record["record_id"]: record for record in _records()}
    ba_c, ba_g = by_id["table-006-0070"], by_id["table-006-0071"]
    assert (ba_c["substance_as_published"], ba_g["substance_as_published"]) == ("$Ba(c)$", "$Ba(g)$")
    assert ba_c["rows"][0]["cells"]["cp_298_15_k"]["raw"] == ""
    assert ba_c["rows"][0]["cells"]["entropy_other_sources"]["raw"] == "16.0±0.5"
    assert ba_g["rows"][0]["cells"]["cp_298_15_k"]["raw"] == "4.97"
    assert ba_g["rows"][0]["cells"]["entropy_recommended"]["raw"] == "40.67±0.01"
    hfcl3, hfcl4 = by_id["table-006-0416"], by_id["table-006-0417"]
    assert (hfcl3["substance_as_published"], hfcl4["substance_as_published"]) == ("HfCl3(c)", "HfCl4(c)")
    assert hfcl3["rows"][0]["cells"]["cp_10_k"]["raw"] == ""
    assert hfcl3["rows"][0]["cells"]["entropy_other_sources"]["raw"] == "10.9±0.3"
    assert hfcl4["rows"][0]["cells"]["cp_10_k"]["raw"] == "(1.18)"
    assert hfcl4["rows"][0]["cells"]["entropy_recommended"]["raw"] == "45.6±0.6"
    assert all(item["kind"] == "image_verified_merged_row_split" for record in (ba_c, ba_g, hfcl3, hfcl4) for item in record["corrections"])


def test_source_row_boundary_census_accounts_for_image_proven_splits():
    blocks = [json.loads(line) for line in (COMPILATION_ROOT / "source/mineru-tables.jsonl").read_text().splitlines()]
    native_rows = [row for block in blocks if block["table_number"] == 6 for row in _parse_html_table(block["html"])[2:]]
    headings = [row for row in native_rows if row[0].strip().endswith(":")]
    assert len(native_rows) == 1411
    assert len(headings) == 92
    assert len(native_rows) - len(headings) + 2 == 1321


def test_units_are_units_not_duplicated_column_labels():
    by_table = {}
    for record in _records():
        by_table.setdefault(record["table_number"], record)
    assert set(by_table[1]["units_as_published"].values()) == {None}
    assert by_table[6]["units_as_published"]["cp_10_k"] == "cal./deg.-mole"
    assert by_table[6]["units_as_published"]["entropy_recommended"] == "cal./deg.-mole"
    assert by_table[7]["units_as_published"]["temperature"] == "°K."
    assert by_table[7]["units_as_published"]["heat_absorbed"] == "cal./mole"


def test_group_headings_are_context_not_substance_records():
    records = [record for record in _records() if record["table_number"] == 6]
    assert all(not record["substance_as_published"].strip().endswith(":") for record in records)
    assert {record.get("heading_context_as_published") for record in records} >= {"Actinium:", "Aluminum:", "Zirconium:"}
    by_id = {record["record_id"]: record for record in records}
    continuation_ids = {
        "table-006-0036",
        "table-006-0119",
        "table-006-0203",
        "table-006-0441",
        "table-006-0610",
        "table-006-0778",
        "table-006-0861",
        "table-006-1020",
        "table-006-1106",
        "table-006-1186",
    }
    assert {
        record_id
        for record_id, record in by_id.items()
        if record.get("record_kind") == "section_continuation_header"
    } == continuation_ids
    for record_id in continuation_ids:
        record = by_id[record_id]
        assert record["formula"] is record["formula_as_published"] is None
        assert record["name_as_published"].endswith("—Con.")
        correction = record["corrections"][0]
        assert correction["record_id"] == record_id
        assert correction["page"] and correction["pdf_page"] and correction["quote"]
        assert correction["kind"] == "image_verified_section_continuation_header_reclassification"


def test_wrapped_formula_fragments_are_typed_and_reconstructed_from_the_image():
    by_id = {record["record_id"]: record for record in _records()}
    expected = {
        "table-006-0931": ("KMg3AlSi3-", "table-006-0932", "KMg3AlSi3-O10F2(c)"),
        "table-006-1094": ("Na2SO4·", "table-006-1095", "Na2SO4·10H2O(c)"),
    }
    for prefix_id, (printed_prefix, substance_id, formula) in expected.items():
        prefix = by_id[prefix_id]
        assert prefix["record_kind"] == "formula_continuation_prefix"
        assert prefix["formula"] is prefix["formula_as_published"] is None
        assert prefix["name_as_published"] == printed_prefix
        assert prefix["corrections"][0]["quote"]
        substance = by_id[substance_id]
        assert substance.get("record_kind", "substance") == "substance"
        assert substance["formula"] == substance["formula_as_published"] == formula
        correction = substance["corrections"][0]
        assert correction["record_id"] == substance_id
        assert correction["page"] and correction["pdf_page"] and correction["quote"]


@pytest.mark.parametrize("temperature", [0, 9.99, 10.01, 298.14, 298.16, 300, float("nan"), float("inf")])
def test_refuses_every_non_grid_temperature(temperature):
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("table-006-0002", temperature)


def test_exact_printed_grid_lookup_and_unknown_record_contract():
    result = lookup_temperature("table-006-0002", 100.0)
    assert result[0]["temperature"]["raw"] == "100° K."
    assert result[0]["heat_capacity"]["raw"] == "3.12"
    assert result[0]["heat_capacity"]["value"] == 3.12
    with pytest.raises(KeyError):
        lookup_temperature("not-a-record", 100.0)


def test_table7_duplicate_temperature_rows_are_preserved():
    record = next(record for record in _records() if record["record_id"] == "table-007-0001")
    rows = tuple(row for row in record["rows"] if row["cells"]["temperature"]["value"] == 83.8)
    assert len(rows) == 2
    assert [row["cells"]["heat_absorbed"]["raw"] for row in rows] == ["281", "1,899"]
    with pytest.raises(OCRSuspectRow):
        lookup_temperature("table-007-0001", 83.8)


def test_suspect_record_and_lookup_require_typed_opt_in_or_refusal():
    with pytest.raises(OCRSuspectRow) as error:
        lookup_temperature("table-006-0001", 298.15)
    assert error.value.record_id == "table-006-0001"
    assert error.value.suspect_cells
    with pytest.raises(OCRSuspectRow):
        list(load_records())
    records = _records()
    assert any(record["contains_ocr_suspect_cells"] for record in records)
    for record in records:
        if record["contains_ocr_suspect_cells"]:
            assert record["metadata_ocr_suspect"] or any(cell["ocr_suspect"] for cell in _numeric_cells(record))


def test_all_public_loader_entry_points_preserve_suspect_safety(tmp_path):
    public_functions = {
        name
        for name, value in inspect.getmembers(b592_loader, inspect.isfunction)
        if value.__module__ == b592_loader.__name__ and not name.startswith("_")
    }
    assert public_functions == {"load_manifest", "load_records", "lookup_temperature"}
    with pytest.raises(OCRSuspectRow):
        load_manifest()
    manifest = load_manifest(include_ocr_suspect=True)
    assert "rows" not in manifest["entries"][0]
    with pytest.raises(OCRSuspectRow):
        list(load_records())
    with pytest.raises(OCRSuspectRow):
        lookup_temperature("table-006-0001", 298.15)
    explicit = next(record for record in _records() if record["record_id"] == "table-006-0001")
    assert explicit["contains_ocr_suspect_cells"]
    assert all("ocr_suspect" in cell for cell in _numeric_cells(explicit))
    assert lookup_temperature("table-006-0001", 298.15, include_ocr_suspect=True)

    mutated = copy.deepcopy(manifest)
    mutated_entry = next(entry for entry in mutated["entries"] if not entry["metadata_ocr_suspect"])
    mutated_entry["formula"] = "DCIO(g)"
    mutated_entry["formula_as_published"] = "DCIO(g)"
    mutated_entry["name_as_published"] = "DCIO(g)"
    mutated["entries"] = [mutated_entry]
    (tmp_path / "manifest.yaml").write_text(yaml.safe_dump(mutated, sort_keys=False))
    with pytest.raises(OCRSuspectRow) as error:
        load_manifest(tmp_path)
    assert {cell.column for cell in error.value.suspect_cells} == {
        "formula",
        "formula_as_published",
        "name_as_published",
    }
    assert load_manifest(tmp_path, include_ocr_suspect=True)["entries"][0]["formula"] == "DCIO(g)"

    record_probe_root = tmp_path / "record-probe"
    record_entry = next(
        copy.deepcopy(entry)
        for entry in manifest["entries"]
        if not entry["metadata_ocr_suspect"]
    )
    record_entry["ocr_suspect_count"] = 0
    record = json.loads((COMPILATION_ROOT / record_entry["path"]).read_text())
    for cell in _numeric_cells(record):
        cell["ocr_suspect"] = False
    for field in ("substance_as_published", "formula", "formula_as_published", "name_as_published"):
        record[field] = "DCIO(g)"
    record["metadata_ocr_suspect"] = False
    record["metadata_ocr_check"] = "raster_ocr_text_agreement"
    record_manifest = copy.deepcopy(manifest)
    record_manifest["entries"] = [record_entry]
    record_probe_root.mkdir()
    (record_probe_root / "manifest.yaml").write_text(
        yaml.safe_dump(record_manifest, sort_keys=False)
    )
    record_path = record_probe_root / record_entry["path"]
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps(record))
    with pytest.raises(OCRSuspectRow) as record_error:
        list(load_records(record_probe_root))
    assert {cell.column for cell in record_error.value.suspect_cells} == {
        "formula",
        "formula_as_published",
        "name_as_published",
    }
    explicit_record = list(load_records(record_probe_root, include_ocr_suspect=True))[0]
    assert explicit_record["contains_ocr_suspect_cells"]


def test_factor_1000_and_monotonicity_detectors_are_complete_and_non_correcting():
    records = _records()
    magnitude = [(record, item) for record in records for item in record["ambiguities"] if item["kind"] == "column_magnitude_factor_approximately_1000"]
    reversals = [(record, item) for record in records for item in record["ambiguities"] if item["kind"] == "within_record_cp_monotonicity_reversal"]
    identity = [(record, item) for record in records for item in record["ambiguities"] if item["kind"] == "recommended_entropy_not_equal_to_printed_source_column"]
    assert len(magnitude) == 8
    assert len(reversals) == 52
    assert len(identity) == 8
    assert all(item["note"].startswith(("Detector only", "Internal consistency detector only")) for _, item in magnitude + reversals + identity)
    for record, item in magnitude:
        candidates = [cell for cell in _numeric_cells(record["rows"]) if cell["raw"] == item["raw"]]
        assert candidates
        assert item["image_cross_check"] in {"raster_ocr_token_agreement", "raster_ocr_token_disagreement"}
        if item["image_cross_check"] == "raster_ocr_token_disagreement":
            assert any(cell["ocr_suspect"] for cell in candidates)
    assert load_manifest(include_ocr_suspect=True)["summary"]["identity_check_disagreement_count"] == 8


def test_every_manifest_summary_field_matches_an_independent_census():
    manifest = load_manifest(include_ocr_suspect=True)
    records = _records()
    blocks = [
        json.loads(line)
        for line in (COMPILATION_ROOT / "source/mineru-tables.jsonl").read_text().splitlines()
    ]
    row_cells = [cell for record in records for cell in _numeric_cells(record["rows"])]
    grid_cells = [cell for record in records for cell in record["temperature_grid"]]
    independently_counted = {
        "numbered_table_count": len({record["table_number"] for record in records}),
        "physical_table_block_count": len(blocks),
        "record_count": len(records),
        "transcribed_record_count": sum(record["transcription_status"].startswith("transcribed") for record in records),
        "untranscribed_record_count": len(manifest["untranscribed"]),
        "numeric_cell_count": len(row_cells),
        "parsed_value_count": sum(cell["value"] is not None for cell in row_cells),
        "temperature_grid_token_count": len(grid_cells),
        "row_numeric_ocr_suspect_count": sum(cell["ocr_suspect"] for cell in row_cells),
        "temperature_grid_ocr_suspect_count": sum(cell["ocr_suspect"] for cell in grid_cells),
        "metadata_ocr_suspect_count": sum(record["metadata_ocr_suspect"] for record in records),
        "ocr_suspect_count": sum(cell["ocr_suspect"] for cell in row_cells + grid_cells)
        + sum(record["metadata_ocr_suspect"] for record in records),
        "identity_check_disagreement_count": sum(
            ambiguity["kind"] == "recommended_entropy_not_equal_to_printed_source_column"
            for record in records
            for ambiguity in record["ambiguities"]
        ),
        "correction_count": sum(len(record["corrections"]) for record in records),
    }
    assert manifest["summary"] == independently_counted


def test_broad_magnitude_gap_and_comma_decimal_census_are_explained():
    records = _records()
    by_column = {}
    for record in records:
        for row in record["rows"]:
            for column, cell in row["cells"].items():
                value = cell.get("value")
                if isinstance(value, (int, float)) and value:
                    by_column.setdefault((record["table_number"], column), []).append(abs(value))
    medians = {key: sorted(values)[len(values) // 2] for key, values in by_column.items()}
    broad = []
    comma_decimal = []
    for record in records:
        for cell in _numeric_cells(record["rows"]):
            value = cell["value"]
            median = medians.get((record["table_number"], cell["column"]))
            if isinstance(value, (int, float)) and value and median:
                factor = max(abs(value) / median, median / abs(value))
                if 250 <= factor <= 3000:
                    broad.append((record["record_id"], cell["column"], cell["raw"]))
            if re.search(r"(?<!\d)\d+,\d{1,2}(?![\d.])", cell["raw"]):
                comma_decimal.append((record["record_id"], cell["column"], cell["raw"]))
    detector = {
        (record["record_id"], ambiguity["column"], ambiguity["raw"])
        for record in records
        for ambiguity in record["ambiguities"]
        if ambiguity["kind"] == "column_magnitude_factor_approximately_1000"
    }
    assert len(broad) == 10
    assert set(broad) - detector == {
        ("table-002-0001", "energy_partition_term", "$.005 \\times 10^{-14}$"),
        ("table-003-0001", "partition_term", ".006"),
    }
    assert comma_decimal == []


def test_post_table_note_prose_is_retained_verbatim():
    blocks = [json.loads(line) for line in (COMPILATION_ROOT / "source/mineru-tables.jsonl").read_text().splitlines()]
    records = _records()
    noted = 0
    for record in records:
        expected = blocks[record["source_ref"]["line"] - 1]["footnotes_raw"]
        assert record["post_table_notes_verbatim"] == expected
        noted += bool(expected)
    assert noted > 0


def test_scientific_notation_and_leading_decimal_parse_without_factor_error():
    record = next(record for record in _records() if record["record_id"] == "table-003-0001")
    assert record["rows"][1]["cells"]["energy_partition_term"]["raw"] == ".022×10-13"
    assert math.isclose(record["rows"][1]["cells"]["energy_partition_term"]["value"], 2.2e-15)
    assert record["rows"][1]["cells"]["wave_number"]["value"] == 3.686
