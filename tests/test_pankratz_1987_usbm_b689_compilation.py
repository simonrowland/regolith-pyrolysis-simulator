"""B689 image anchors, honest partial coverage, native tokens and public OCR guards."""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest
import yaml

from simulator import reference_data
from simulator.reference_data import pankratz_1987_usbm_b689_loader as loader
from tools import ingest_pankratz_1987_usbm_b689 as harvest


ROOT = loader.COMPILATION_ROOT


def _records():
    return [json.loads(path.read_text()) for path in sorted((ROOT / "records").glob("*.json"))]


def _assert_image_anchors(records):
    by_id = {r["record_id"]: r for r in records}
    # Independently read original PDF7, PDF11–12 and PDF34, not derived from the audit sidecar.
    for record_id in ("page-0007", "page-0008"):
        assert by_id[record_id]["formula"] == "AlS"
        assert by_id[record_id]["formula_as_published"] == "AlS(g)"
        assert by_id[record_id]["name_as_published"] == "Aluminum Monosulfide (ideal gas)"
    cells = by_id["page-0003"]["rows"][0]["cells"]
    assert cells["temperature"]["value"] == 298.15
    assert cells["cp"]["value"] == 8.600
    assert cells["delta_h"]["value"] == 94.000
    assert cells["delta_g"]["value"] == 80.700
    assert cells["log_k"]["value"] == -59.154
    corrected = by_id["page-0030"]["rows"][14]["cells"]["log_k"]
    assert corrected["raw"] == "-.968"
    assert corrected["value"] == -0.968
    assert corrected["raw_ocr_token"] == "-968"


def test_independent_image_anchors():
    _assert_image_anchors(_records())


def test_self_consistent_wrong_identity_and_audit_cannot_certify():
    records = _records()
    audits = [json.loads(x) for x in (ROOT / "source/formula-audit.jsonl").read_text().splitlines()]
    for item in records + audits:
        if item["record_id"] == "page-0007":
            item["formula"] = "A1S"
            item["formula_as_published"] = "A1S(g)"
    assert next(a for a in audits if a["record_id"] == "page-0007")["formula"] == "A1S"
    with pytest.raises(AssertionError):
        _assert_image_anchors(records)


def test_reverting_numeric_repair_is_detected():
    records = _records()
    record = next(r for r in records if r["record_id"] == "page-0030")
    record["rows"][14]["cells"]["log_k"].update(raw="-968", value=-968)
    with pytest.raises(AssertionError):
        _assert_image_anchors(records)


def test_complete_page_three_numeric_image_fixture():
    fixture = json.loads((ROOT / "source/image-verified-fixture.json").read_text())
    record = _records()[0]
    for row, expected in zip(record["rows"], fixture["complete_numeric_pages"]["7"], strict=True):
        assert [row["cells"][c]["raw"] for c in harvest.COLUMNS] == expected
        assert all(not c["ocr_suspect"] for c in row["cells"].values())


def test_native_tokens_notes_and_html_census():
    pages = [json.loads(x) for x in (ROOT / "source/mineru-pages.jsonl").read_text().splitlines()]
    assert len(pages) == 40
    assert sum(x["type"] == "table" for p in pages for x in p["items"]) == 59
    for record in _records():
        items = pages[record["pdf_page"] - 1]["items"]
        table_index = record["source_ref"]["item_index"]
        source_rows = harvest._rows(items[table_index]["table_body"])
        for row in record["rows"]:
            assert row["raw"] == source_rows[row["source_row_index"]]
            for column, raw in zip(harvest.COLUMNS, row["raw"]):
                cell = row["cells"][column]
                assert cell.get("raw_ocr_token", cell["raw"]) == raw
                assert cell["value"] == harvest._cell(cell["raw"])["value"]
        assert record["notes"]["blocks_raw"] == harvest._note_blocks(items, table_index)
        assert record["notes"]["raw"] == "\n".join(record["notes"]["blocks_raw"])
        for item in items[table_index:]:
            if item["type"] == "text":
                assert item["text"] in record["notes"]["blocks_raw"]
            for note in item.get("table_footnote", []):
                assert note in record["notes"]["blocks_raw"]


def test_coverage_and_nonoxide_policy():
    manifest = loader.load_manifest(include_ocr_suspect=True)
    coverage = manifest["coverage"]
    assert (coverage["records_examined"], coverage["matched"], coverage["corrected"], coverage["unverified"]) == (34, 20, 13, 1)
    assert coverage["remaining_printed_pages"] == [37, 427]
    assert coverage["remaining_pdf_pages"] == [41, 432]
    assert manifest["record_count"] == 34 and manifest["substance_count"] == 33
    assert sum(r["row_count"] for r in _records()) == 506
    assert coverage["numeric_cell_count"] == 4048
    assert coverage["numeric_cells_image_verified"] == 97
    assert manifest["compilation_role"]["non_oxide_policy"] == "warn_not_fail_closed"
    assert not manifest["compilation_role"]["scoring_eligible"]
    assert not manifest["compilation_role"]["validation_measurement"]
    assert not manifest["compilation_role"]["oxide_rail_default"]
    audits = [json.loads(x) for x in (ROOT / "source/formula-audit.jsonl").read_text().splitlines()]
    assert [a["pdf_page"] for a in audits] == list(range(7, 41))
    assert all(a["printed_quote"] and a["image_evidence"]["render_dpi"] == 300 for a in audits)
    assert len(manifest["feedstock_element_coverage"]) > 0
    for correction in manifest["corrections"]:
        assert correction["kind"] in {"repair", "reconstruction"}
        assert correction["ocr_token"] and correction["printed_token"] and correction["image_quote"]
        assert correction["pdf_page"] == correction["printed_page"] + 4


def test_every_yaml_parses():
    for path in [*ROOT.rglob("*.yaml"), ROOT.parent / "access-status.yaml"]:
        assert isinstance(yaml.safe_load(path.read_text()), dict), path


def test_exact_grid_duplicate_rows_and_clean_lookup():
    rows = loader.lookup_temperature("page-0003", 368.3)
    assert len(rows) == 2
    assert [r["cells"]["delta_h"]["value"] for r in rows] == [93.785, 93.689]
    assert loader.lookup_temperature("page-0003", 298.15)[0]["cells"]["cp"]["value"] == 8.6
    with pytest.raises(loader.PrintedTemperatureUnavailable):
        loader.lookup_temperature("page-0003", 299)
    with pytest.raises(KeyError):
        loader.lookup_temperature("page-9999", 298.15)
    for opt_in in (False, True):
        with pytest.raises(KeyError):
            loader.lookup_temperature("page-0014", 298.15, include_ocr_suspect=opt_in)
    assert len(list(loader.load_records(include_ocr_suspect=True))) == 33


@pytest.fixture
def clean_root(tmp_path):
    record = copy.deepcopy(_records()[0])
    # Image-verified table-only fixture; unverified prose is not part of this positive-control record.
    record.pop("notes")
    record["ambiguities"] = []
    manifest = {"entries": [{"record_id": record["record_id"], "path": "record.json", "record_kind": "substance",
                              "metadata_ocr_suspect": False, "ocr_suspect_count": 0}]}
    (tmp_path / "record.json").write_text(json.dumps(record))
    (tmp_path / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    return tmp_path


PUBLIC = {
    "load_manifest": lambda root, **kw: loader.load_manifest(root, **kw),
    "load_records": lambda root, **kw: list(loader.load_records(root, **kw)),
    "lookup_temperature": lambda root, **kw: loader.lookup_temperature("page-0003", 298.15, root, **kw),
}


def test_public_surface_enumeration_including_package_reexports():
    public = {n for n, v in inspect.getmembers(loader, inspect.isfunction) if not n.startswith("_")}
    assert public == PUBLIC.keys()
    assert not [n for n, v in inspect.getmembers(reference_data, inspect.isfunction) if v.__module__ == loader.__name__]
    assert all("include_ocr_suspect" in inspect.signature(getattr(loader, n)).parameters for n in public)


@pytest.mark.parametrize("entry_point", PUBLIC)
def test_each_public_surface_clean_positive_and_manifest_mutation(clean_root, entry_point):
    call = PUBLIC[entry_point]
    assert call(clean_root)
    path = clean_root / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["entries"][0]["metadata_ocr_suspect"] = True
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(loader.OCRSuspectRow):
        call(clean_root)
    assert call(clean_root, include_ocr_suspect=True)


@pytest.mark.parametrize("entry_point", ["load_records", "lookup_temperature"])
@pytest.mark.parametrize("mutation", ["metadata", "cell", "record"])
def test_record_mutations_cannot_bypass_stale_manifest(clean_root, entry_point, mutation):
    path = clean_root / "record.json"
    record = json.loads(path.read_text())
    if mutation == "metadata":
        record["metadata_ocr_suspect"] = True
    elif mutation == "cell":
        record["rows"][0]["cells"]["cp"]["ocr_suspect"] = True
    else:
        record["ocr_suspect"] = True
    path.write_text(json.dumps(record))
    with pytest.raises(loader.OCRSuspectRow):
        PUBLIC[entry_point](clean_root)
    assert PUBLIC[entry_point](clean_root, include_ocr_suspect=True)


def test_nested_note_suspect_does_not_escape_load_records(clean_root):
    path = clean_root / "record.json"
    record = json.loads(path.read_text())
    record["notes"] = {"ocr_suspect": True, "transitions": [{"temperature_K": 450}]}
    path.write_text(json.dumps(record))
    with pytest.raises(loader.OCRSuspectRow):
        list(loader.load_records(clean_root))
    assert list(loader.load_records(clean_root, include_ocr_suspect=True))
    assert loader.lookup_temperature("page-0003", 298.15, clean_root)


@pytest.mark.parametrize("location", ["manifest", "record"])
@pytest.mark.parametrize("kind", ["section_continuation_header", "formula_continuation_prefix", "unverified_identity"])
def test_structural_refusal_at_every_substance_boundary(clean_root, location, kind):
    if location == "manifest":
        path = clean_root / "manifest.yaml"
        data = yaml.safe_load(path.read_text())
        data["entries"][0]["record_kind"] = kind
        path.write_text(yaml.safe_dump(data))
    else:
        path = clean_root / "record.json"
        data = json.loads(path.read_text())
        data["record_kind"] = kind
        path.write_text(json.dumps(data))
    for opt_in in (False, True):
        assert list(loader.load_records(clean_root, include_ocr_suspect=opt_in)) == []
        with pytest.raises(KeyError):
            loader.lookup_temperature("page-0003", 298.15, clean_root, include_ocr_suspect=opt_in)


@pytest.mark.parametrize("captions, expected", [
    (["Al", "S(g)", "Aluminum Monosulfide (ideal gas)"], "identity_line_wrap_or_merge"),
    (["AlS(g) BS(g)", "Merged substances"], "multiple_substance_tokens"),
    (["AlS(g)", "Aluminum Monosulfide [Reaction: Al + S]"], "identity_and_reaction_merged"),
])
def test_wrap_merge_detector_flags_without_repair(captions, expected):
    items = [{"type": "table", "table_caption": captions, "table_body": "<table></table>"}]
    before = copy.deepcopy(items)
    assert expected in {f["kind"] for f in harvest._structure_detectors(items, 0)}
    assert items == before


def test_numeric_detector_is_not_a_repair():
    record = next(r for r in _records() if r["record_id"] == "page-0030")
    rows = copy.deepcopy(record["rows"])
    rows[14]["cells"]["log_k"].update(raw="-968", value=-968)
    before = copy.deepcopy(rows)
    assert any(f["kind"] == "adjacent_factor_approximately_1000" for f in harvest._numeric_detectors(rows))
    assert rows == before


def test_printed_estimate_and_phase_note_anchors_are_preserved():
    records = {r["record_id"]: r for r in _records()}
    assert "Data except enthalpy of formation at 298 K estimated." in records["page-0003"]["notes"]["raw"]
    assert "933.61 K, melting point of Al" in records["page-0008"]["notes"]["raw"]
    assert "2.580" in records["page-0008"]["notes"]["raw"]
    assert "Sources: Enthalpy of formation at 298 K based on Kiukkola" in records["page-0006"]["notes"]["raw"]
