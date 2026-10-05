"""Image anchors and page-bounded coverage for the B696 species-scope ingest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from tools.ingest_pankratz_1987_usbm_b689 import _rows


ROOT = Path(__file__).resolve().parents[1] / "data/literature/compilations/pankratz-1994-usbm-b696"
FORMULAS = {
    "AgO": (21, ["298.15", "8.410", "58.685", "58.685", "0", "75.000", "67.841", "-49.728"]),
    "BiO": (13, ["298.15", "7.800", "58.910", "58.910", "0", "29.200", "22.984", "-16.848"]),
    "CdO": (23, ["298.15*", "8.090", "55.669", "55.669", "0", "30", "24.399", "-17.885"]),
    "CeO": (23, ["298.15", "7.570", "59.406", "59.406", "0", "-32.000", "-37.278", "27.325"]),
    "DyO": (23, ["298.15", "7.590", "61.505", "61.505", "0", "-18.000", "-23.695", "17.369"]),
    "ErO": (21, ["298.15", "7.570", "60.913", "60.913", "0", "-10.000", "-15.641", "11.465"]),
    "EuO": (22, ["298.15", "7.570", "61.592", "61.592", "0", "-14.000", "-19.513", "14.303"]),
    "GaO": (21, ["298.15", "7.690", "55.158", "55.158", "0", "40.000", "33.769", "-24.753"]),
    "GdO": (23, ["298.15*", "7.560", "60.539", "60.539", "0", "-16.500", "-22.402", "16.421"]),
    "GeO": (21, ["298.15", "7.340", "53.485", "53.485", "0", "-8.500", "-14.926", "10.941"]),
    "HfO": (28, ["298.15", "7.430", "56.285", "56.285", "0", "16.000", "9.628", "-7.057"]),
    "HoO": (23, ["298.15*", "7.560", "61.238", "61.238", "0", "-14.500", "-20.107", "14.739"]),
    "InO": (21, ["298.15", "7.790", "56.831", "56.831", "0", "41.000", "35.482", "-26.008"]),
    "LaO": (25, ["298.15", "7.610", "57.261", "57.261", "0", "-29.000", "-34.712", "25.444"]),
    "LuO": (21, ["298.15", "7.544", "57.833", "57.833", "0", ".500", "-5.806", "4.256"]),
    "NdO": (23, ["298.15*", "7.570", "60.520", "60.520", "0", "-30.000", "-35.640", "26.125"]),
    "PrO": (23, ["298.15*", "7.560", "59.988", "59.988", "0", "-34.000", "-39.312", "28.816"]),
    "ScO": (23, ["298.15", "7.440", "53.647", "53.647", "0", "-13.000", "-19.221", "14.089"]),
    "SmO": (23, ["298.15", "7.560", "61.299", "61.299", "0", "-28.000", "-34.013", "24.932"]),
    "SnO": (21, ["298.15", "7.580", "55.446", "55.446", "0", "4.500", "-1.078", ".790"]),
    "TbO": (23, ["298.15*", "7.550", "61.711", "61.711", "0", "-17.000", "-22.870", "16.764"]),
    "TeO": (19, ["298.15", "9.000", "56.260", "56.260", "0", "-2.665", "-2.910", "2.133"]),
    "TmO": (21, ["298.15*", "7.540", "60.496", "60.496", "0", "-7.000", "-12.457", "9.131"]),
    "UO": (25, ["298.15", "7.570", "62.289", "62.289", "0", "6.000", "-1.688", "1.238"]),
    "YO": (23, ["298.15", "7.540", "55.875", "55.875", "0", "-11.000", "-17.187", "12.599"]),
    "YbO": (26, ["298.15*", "7.540", "60.023", "60.023", "0", "-17.000", "-23.327", "17.099"]),
    "ZnO": (23, ["298.15", "7.820", "53.681", "53.681", "0", "26.000", "20.267", "-14.856"]),
}
COLUMNS = ("temperature", "cp", "entropy", "gibbs_function", "enthalpy_increment", "delta_h", "delta_g", "log_k")
UNITS = ("K", "cal/mol·K", "cal/mol·K", "cal/mol·K", "kcal/mol", "kcal/mol", "kcal/mol", None)


def _records():
    return [json.loads(path.read_text()) for path in sorted((ROOT / "records").glob("*.json"))]


def test_b696_partial_coverage_and_reference_only_role():
    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    coverage = manifest["coverage"]
    records = _records()
    census = json.loads((ROOT / "census.json").read_text())
    assert manifest["source_id"] == "pankratz-1994-usbm-b696"
    assert manifest["record_count"] == manifest["substance_count"] == len(FORMULAS) == 27
    assert coverage == census
    assert coverage["status"] == "partial"
    assert coverage["records_examined"] == 27
    assert sum(record["row_count"] for record in records) == 602
    assert coverage["numeric_cell_count"] == coverage["numeric_cells_image_verified"] == 4816
    assert coverage["non_numeric_cell_count"] == 0
    assert coverage["absent_species"] == [
        {"formula": "PmO", "pages_checked": [3]},
        {"formula": "Se2", "pages_checked": [3]},
        {"formula": "As4", "pages_checked": [3, 46, 47, 48, 49, 50]},
    ]
    assert manifest["compilation_role"]["engine_reference_input"] is True
    assert manifest["compilation_role"]["validation_measurement"] is False
    assert manifest["compilation_role"]["scoring_eligible"] is False
    assert manifest["compilation_role"]["oxide_rail_default"] is False


def test_all_table_rows_match_image_anchors_and_preserve_source_decode():
    records = _records()
    pages = [json.loads(line) for line in (ROOT / "source/mineru-pages.jsonl").read_text().splitlines()]
    pages_by_pdf = {page["pdf_page"]: (line_number, page)
                    for line_number, page in enumerate(pages, 1)}
    fixture = json.loads((ROOT / "source/image-verified-fixture.json").read_text())
    audits = [json.loads(line) for line in (ROOT / "source/formula-audit.jsonl").read_text().splitlines()]
    assert {audit["pdf_page"] for audit in audits} == {record["pdf_page"] for record in records}
    assert all(audit["image_evidence"]["render_dpi"] == 300 for audit in audits)
    assert {record["formula"] for record in records} == set(FORMULAS)

    for record in records:
        expected_count, image_first_row = FORMULAS[record["formula"]]
        assert record["phase"] == "g"
        assert record["row_count"] == expected_count == len(record["rows"])
        assert record["columns"] == list(COLUMNS)
        assert tuple(record["units"][column] for column in COLUMNS) == UNITS
        first_row = [record["rows"][0]["cells"][column]["raw"] for column in COLUMNS]
        assert first_row == image_first_row
        line_number, page = pages_by_pdf[record["pdf_page"]]
        assert record["source_ref"]["line"] == line_number
        assert record["source_ref"]["item_index"] < len(page["items"])
        table = page["items"][record["source_ref"]["item_index"]]
        source_rows = _rows(table["table_body"])[2:]
        assert [row["raw"] for row in record["rows"]] == source_rows
        assert fixture["complete_numeric_pages"][str(record["pdf_page"])] == source_rows
        for row in record["rows"]:
            assert len(row["raw"]) == len(COLUMNS) == 8
            assert all(row["cells"][column]["value"] is not None for column in COLUMNS)
            assert all(row["cells"][column]["ocr_check"] == "image_verified" for column in COLUMNS)
        citations = record["notes"]["lines_raw"]
        assert any(line.lstrip().startswith(("Source:", "Sources:")) for line in citations)


def test_bi_o_ends_at_1200_k_and_b696_sources_are_hash_pinned():
    records = {record["formula"]: record for record in _records()}
    assert records["BiO"]["rows"][-1]["cells"]["temperature"]["value"] == 1200
    manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    for source in manifest["source_files"]:
        if source["path"].startswith("source/"):
            actual = hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest()
            assert actual == source["sha256"], source["path"]

    access = yaml.safe_load((ROOT.parent / "access-status.yaml").read_text())
    entry = access["sources"]["pankratz_1994_usbm_b696"]
    assert entry["local_status"] == "partial_ingest"
    assert entry["harvested_record_count"] == 27
