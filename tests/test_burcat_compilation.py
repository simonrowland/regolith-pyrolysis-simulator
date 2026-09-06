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
        if doc["intervals"] != fresh["intervals"] or doc["hf298_div_r"] != fresh["hf298_div_r"]:
            mismatches.append(f"{rec.record_id} published_coefficient_tokens")
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
    assert ag_solid["phase"] == "S/solid"
    assert by_name["Ag (liquid)"][0]["phase"] == "L/liquid"
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


def test_same_formula_distinct_phase_census(record_docs) -> None:
    import re
    from collections import defaultdict

    groups = defaultdict(dict)
    gas_count = 0
    for doc in record_docs:
        if doc["record_kind"] != "nasa7_polynomial":
            continue
        card = doc["source_text"]["header_line"][44]
        suffix = re.search(r"\(([^)]+)\)(?:,.*)?$", doc["name_as_published"])
        if card == "G":
            gas_count += 1
            assert doc["phase"] == "gas", doc["record_id"]
            if suffix and suffix[1] in {"s", "cr"}:
                assert any(a["kind"] == "phase_card_suffix_conflict" for a in doc["ambiguities"])
            identity = (card, None)
        else:
            identity = (card, suffix[1] if suffix else None)
            if suffix:
                assert doc["phase"] == f"{card}/{suffix[1]}", doc["record_id"]
        groups[doc["formula"]][identity] = doc["phase"]
    assert gas_count == 3051
    assert all(doc["phase"] != "condensed" for doc in record_docs)
    for formula, phases in groups.items():
        assert len(set(phases.values())) == len(phases), (formula, phases)


def test_fifteen_printed_padded_zero_coefficients_are_values(record_docs) -> None:
    import re

    count = 0
    for doc in record_docs:
        if doc["record_kind"] != "nasa7_polynomial":
            continue
        numbers = [n for iv in doc["intervals"] for n in iv["a_coefficients"]]
        numbers.append(doc["hf298_div_r"])
        raw = doc["source_text"]["coeff_lines"]
        fields = [line[i:i+15].strip() for line in raw for i in range(0, 75, 15)]
        for field, number in zip(fields, numbers):
            if re.fullmatch(r"0\.0+\s+E[+ ]00", field):
                count += 1
                assert number["as_published"] == field
                assert number["value"] == 0.0
    assert count == 15


@pytest.mark.parametrize("record_id,start,end,cas", [
    ("BU-1835", 20629, 20631, "142-82-5"),
    ("BU-2156", 24735, 24738, "112-39-0"),
    ("BU-2473", 27823, 27824, "7440-55-3"),
    ("BU-2636", 29266, 29268, "10377-51-2,"),
    ("BU-2637", 29266, 29268, "10377-51-2,"),
    ("BU-2818", 30706, 30706, "10102-43-9"),
    ("BU-2988", 32133, 32134, "7723-14-0"),
    ("BU-3221", 34225, 34230, "108549=59-3"),
])
def test_prose_between_polynomials_and_nonstandard_cas_are_retained(
    record_docs, record_id, start, end, cas
) -> None:
    doc = next(d for d in record_docs if d["record_id"] == record_id)
    lines = SOURCE.read_text(encoding="utf-8-sig").splitlines()
    assert doc["cas_as_published"].split()[0] == cas
    assert all(line in doc["comment_block"] for line in lines[start-1:end])


def test_round_trip_rejects_format_only_coefficient_mutation(manifest, record_docs) -> None:
    from copy import deepcopy

    mutated = deepcopy(record_docs)
    number = mutated[0]["intervals"][0]["a_coefficients"][0]
    assert number["as_published"] != repr(number["value"])
    number["as_published"] = repr(number["value"])
    with pytest.raises(AssertionError):
        test_every_record_round_trips_to_thr_numbers(manifest, mutated)


def test_shifted_card_keeps_complete_exponent_and_printed_tokens(record_docs) -> None:
    doc = next(d for d in record_docs if d["record_id"] == "BU-2666")
    assert doc["source_text"]["coeff_lines"][2].rstrip().endswith("9.09964431E+04    4")
    assert doc["hf298_div_r"]["as_published"] == "9.09964431E+04"
    assert doc["hf298_div_r"]["value"] == 90996.4431
    irregular_headers = {
        d["record_id"]: d for d in record_docs
        if any(a["kind"] == "nasa7_header_tail_columns_misaligned" for a in d["ambiguities"])
    }
    assert set(irregular_headers) == {"BU-0628", "BU-3016", "BU-3321"}
    for record_id, quality, weight in [
        ("BU-0628", "Bx", "97.01601"),
        ("BU-3016", "B", "37.01607"),
        ("BU-3321", "B", "121.41346"),
    ]:
        header_doc = irregular_headers[record_id]
        assert header_doc["source_text"]["header_line"][65:].split() == [quality, weight, "1"]
        assert header_doc["molecular_weight"]["as_published"] == weight
        assert header_doc["molecular_weight"]["value"] == float(weight)


def test_every_nonblank_source_line_is_retained(record_docs) -> None:
    lines = SOURCE.read_text(encoding="utf-8-sig").splitlines()
    retained = set()
    residual_blocks = []
    for doc in record_docs:
        retained.update(doc["comment_block"])
        retained.add(doc["source_text"]["header_line"])
        retained.update(doc["source_text"]["coeff_lines"])
        for ambiguity in doc["ambiguities"]:
            if ambiguity["kind"] == "unassigned_post_polynomial_prose":
                residual_blocks.append(ambiguity)
                assert ambiguity["text_as_published"] == [lines[n-1] for n in ambiguity["source_lines"]]
                retained.update(ambiguity["text_as_published"])
    assert len(residual_blocks) == 11
    # First published species CAS is line 120; the source preamble is not a stanza.
    first_cas = min(d["source_locator"]["cas_line"] for d in record_docs if d["source_locator"]["cas_line"])
    assert [(n+1, line) for n, line in enumerate(lines) if n >= first_cas-1 and line.strip() and line not in retained] == []
