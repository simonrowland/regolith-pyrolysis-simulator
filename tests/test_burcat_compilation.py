"""Burcat Third Millennium compilation ingest.

Round-trip: every manifest record reloads to the published numbers in
``source/BURCAT.THR.txt``. Coverage: which ``data/feedstocks.yaml``
elements have at least one record. Compilations are reference functions,
not validation measurements.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from simulator.reference_data.burcat import (
    COMPILATION_ROOT,
    SOURCE_REL,
    coverage_by_element,
    feedstock_element_symbols,
    formulas_absent_from_janaf_and_glenn,
    iter_record_paths,
    load_all_record_documents,
    load_manifest,
    parse_burcat_thr,
    parse_burcat_xml,
    peer_formula_sets,
    published_float_pairs,
)
from simulator.reference_data.nasa_glenn import is_nasa7_four_line_record

ROOT = Path(__file__).resolve().parents[1]
SOURCE = COMPILATION_ROOT / "source" / "BURCAT.THR.txt"
XML_SOURCE = COMPILATION_ROOT / "source" / "BURCAT_THR.xml"
SIDECAR = COMPILATION_ROOT / "source" / "sidecar.yaml"
MAJOR_FEEDSTOCK_ELEMENTS = ("Si", "Fe", "O", "Al", "Mg", "Ca", "Ti", "Na", "K")
THR_SHA256 = "42a597ff852d1a2995f81d4fdca069f3cdcaef77ead5e3c66e02e80ed6699101"
XML_SHA256 = "0f4cfd21cfa19f620110bbe63bbb96beff112a2c5e8cf38c885c6d7b3d00a3af"


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


@pytest.fixture(scope="module")
def record_docs():
    return load_all_record_documents()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_source_snapshots_are_byte_identical_to_manifest_sha256(manifest) -> None:
    assert SOURCE.is_file()
    assert XML_SOURCE.is_file()
    assert SIDECAR.is_file()
    assert _sha256(SOURCE) == manifest["source"]["sha256"]
    assert manifest["source"]["sha256"] == THR_SHA256
    assert _sha256(XML_SOURCE) == manifest["source"]["xml_sha256"]
    assert manifest["source"]["xml_sha256"] == XML_SHA256
    assert not (COMPILATION_ROOT / "source" / "Archives.zip").exists()


def test_manifest_lists_every_record_file_and_no_orphans(manifest, record_docs) -> None:
    entries = manifest["entries"]
    assert manifest["summary"]["record_count"] == len(entries)
    files = {path.name: path for path in iter_record_paths()}
    docs = {doc["record_id"]: doc for doc in record_docs}
    assert len(files) == len(entries)
    assert set(docs) == {entry["record_id"] for entry in entries}
    for entry in entries:
        name = f"{entry['record_id']}.json"
        assert name in files
        doc = docs[entry["record_id"]]
        assert doc["name_as_published"] == entry["name_as_published"]
        assert doc["compilation_role"]["scoring_eligible"] is False
        assert doc["compilation_role"]["validation_measurement"] is False
        assert doc["schema_version"] == "literature_compilation.v1"
        assert doc["source_id"] == "burcat"


def test_every_record_round_trips_to_thr_numbers(manifest, record_docs) -> None:
    """Null hypothesis: a harvested coefficient can drift from BURCAT.THR.txt."""
    text = SOURCE.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    parsed = parse_burcat_thr(
        text,
        source_path=SOURCE_REL,
        source_sha256=manifest["source"]["sha256"],
    )
    assert len(parsed.records) == manifest["summary"]["record_count"]
    docs = {doc["record_id"]: doc for doc in record_docs}
    assert set(docs) == {rec.record_id for rec in parsed.records}

    mismatches: list[str] = []
    for rec in parsed.records:
        doc = docs[rec.record_id]
        if rec.record_kind == "nasa7_polynomial":
            header_idx = rec.header_line_number - 1
            if doc["source_text"]["header_line"] != lines[header_idx]:
                mismatches.append(f"{rec.record_id} header_line")
            expected = [lines[header_idx + 1], lines[header_idx + 2], lines[header_idx + 3]]
            if doc["source_text"]["coeff_lines"] != expected:
                mismatches.append(f"{rec.record_id} coeff_lines")
            if not is_nasa7_four_line_record(lines, header_idx):
                mismatches.append(f"{rec.record_id} not_nasa7_block")
        else:
            if doc["record_kind"] != "comment_only":
                mismatches.append(f"{rec.record_id} kind")
            if not doc["comment_block"]:
                mismatches.append(f"{rec.record_id} empty_comment")
        if doc["name_as_published"] != rec.name_as_published:
            mismatches.append(f"{rec.record_id} name")
        if doc["coefficient_count"] != rec.coefficient_count:
            mismatches.append(f"{rec.record_id} coefficient_count")
        if doc["comment_block"] != rec.comment_block:
            mismatches.append(f"{rec.record_id} comment_block")
        fresh = rec.to_dict()
        if published_float_pairs(doc) != published_float_pairs(fresh):
            mismatches.append(f"{rec.record_id} published_numbers")
        if len(mismatches) > 20:
            break
    assert mismatches == []


def test_complete_ingest_keeps_ions_condensed_comment_only_and_na_hf(
    record_docs, manifest
) -> None:
    docs = record_docs
    by_name: dict[str, list] = {}
    for doc in docs:
        by_name.setdefault(doc["name_as_published"], []).append(doc)
    assert "Ag (solid)" in by_name
    assert "Ag (liquid)" in by_name
    assert "Ag+" in by_name
    assert "Ag-" in by_name
    ag_solid = by_name["Ag (solid)"][0]
    assert ag_solid["phase_as_published"] == "solid"
    assert ag_solid["phase_card_as_published"] == "S"
    assert ag_solid["phase"] == "condensed"
    assert any(
        item["kind"] == "phase_suffix_normalized_to_condensed"
        for item in ag_solid["ambiguities"]
    )
    assert ag_solid["comment_block"]
    assert any("CODATA" in line for line in ag_solid["comment_block"])
    ions = [doc for doc in docs if doc["name_as_published"].endswith(("+", "-"))]
    assert len(ions) >= 50
    condensed = [
        doc
        for doc in docs
        if doc["phase_as_published"] in {"S", "L", "C"}
    ]
    assert len(condensed) >= 100
    comment_only = [doc for doc in docs if doc["record_kind"] == "comment_only"]
    assert len(comment_only) == manifest["summary"]["comment_only_record_count"]
    assert comment_only
    assert manifest["summary"]["record_count"] == 3446
    assert manifest["summary"]["polynomial_record_count"] == 3407
    assert manifest["summary"]["comment_only_record_count"] == 39
    na_hf = [
        doc
        for doc in docs
        if doc.get("hf298_div_r")
        and "N/A" in (doc["hf298_div_r"].get("as_published") or "")
    ]
    assert na_hf
    tungsten = by_name.get("W") or []
    assert tungsten
    assert tungsten[0]["calc_quality_as_published"] in {"?", "B", "C", "D", "A"}
    assert manifest["summary"]["polynomial_record_count"] >= 3400


def test_feedstock_element_coverage_table(manifest, record_docs) -> None:
    elements = feedstock_element_symbols()
    assert "Si" in elements and "Fe" in elements and "O" in elements
    table = coverage_by_element(record_docs, elements)
    assert set(table) == set(elements)
    covered = [el for el, row in table.items() if row["has_record"]]
    uncovered = [el for el, row in table.items() if not row["has_record"]]
    for element in MAJOR_FEEDSTOCK_ELEMENTS:
        assert table[element]["has_record"], f"{element} has no Burcat record"
        assert table[element]["record_count"] >= 1
    assert covered
    assert manifest["feedstock_element_coverage"]["covered_elements"] == covered
    assert manifest["feedstock_element_coverage"]["uncovered_elements"] == uncovered


def test_xml_crosscheck_is_recorded_not_used_as_authority(manifest) -> None:
    xml = manifest["xml_crosscheck"]
    assert xml["xml_phase_count"] == 1364
    assert xml["matched_count"] + xml["mismatch_count"] + xml["xml_only_count"] == 1364
    assert xml["matched_count"] >= 1
    phases = parse_burcat_xml(XML_SOURCE)
    assert len(phases) == 1364


def test_formulas_absent_from_janaf_and_nasa_glenn(manifest, record_docs) -> None:
    unique = manifest["formulas_absent_from_janaf_and_nasa_glenn"]
    assert unique["count"] == len(unique["formulas"])
    assert unique["count"] >= 1
    unique_formulas = {row["formula"] for row in unique["formulas"]}
    janaf, glenn = peer_formula_sets()
    assert "SiO2" in (janaf | glenn)
    assert "SiO2" not in unique_formulas
    text = SOURCE.read_text(encoding="utf-8-sig")
    parsed = parse_burcat_thr(text, source_path=SOURCE_REL)
    recomputed = formulas_absent_from_janaf_and_glenn(parsed.records, janaf, glenn)
    assert {row["formula"] for row in recomputed} == unique_formulas
    assert any(len(formula) >= 4 for formula in unique_formulas)
