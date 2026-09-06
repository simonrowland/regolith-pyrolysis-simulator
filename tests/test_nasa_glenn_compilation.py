"""NASA Glenn / CEA thermo.inp compilation ingest.

Round-trip: every manifest record reloads to the published numbers in
``source/thermo.inp``. Coverage: which ``data/feedstocks.yaml`` elements
have at least one record. Compilations are reference functions, not
validation measurements.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from simulator.reference_data.nasa_glenn import (
    COMPILATION_ROOT,
    NASA7_COEFF_FIELD_WIDTH,
    NASA9_COEFF_FIELD_WIDTH,
    SOURCE_REL,
    coverage_by_element,
    feedstock_element_symbols,
    is_nasa7_four_line_record,
    iter_record_paths,
    load_all_record_documents,
    load_manifest,
    parse_coeff_fields,
    parse_nasa7_coefficient_lines,
    parse_nasa7_header_line,
    parse_thermo_inp,
    published_float_pairs,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = COMPILATION_ROOT / "source" / "thermo.inp"
EXTRACT = ROOT / "data" / "literature" / "extracts" / "nasa-cea-thermo.yaml"
MAJOR_FEEDSTOCK_ELEMENTS = ("Si", "Fe", "O", "Al", "Mg", "Ca", "Ti", "Na", "K")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


@pytest.fixture(scope="module")
def record_docs():
    return load_all_record_documents()


def _source_sha256() -> str:
    digest = hashlib.sha256()
    with SOURCE.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_source_snapshot_is_byte_identical_to_manifest_sha256(manifest) -> None:
    expected = manifest["source"]["sha256"]
    assert SOURCE.is_file()
    assert _source_sha256() == expected
    assert expected == "fa7746572952d74e249e818a82a35c113829742fb421a308e167185528884363"


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


def test_every_record_round_trips_to_thermo_inp_numbers(manifest, record_docs) -> None:
    """Null hypothesis: a harvested coefficient can drift from thermo.inp.

    Reloading each JSON and comparing source_text plus parsed values against
    a fresh fixed-column parse of the committed snapshot must stay exact.
    """
    text = SOURCE.read_text(encoding="latin-1")
    lines = text.splitlines()
    parsed = parse_thermo_inp(
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
        name_line = lines[rec.name_line_number - 1]
        header_line = lines[rec.header_line_number - 1]
        if doc["source_text"]["name_line"] != name_line:
            mismatches.append(f"{rec.record_id} name_line")
        if doc["source_text"]["header_line"] != header_line:
            mismatches.append(f"{rec.record_id} header_line")
        expected_interval_lines: list[str] = []
        for iv in rec.intervals:
            expected_interval_lines.append(iv.raw_t_line)
            expected_interval_lines.extend(iv.raw_coeff_lines)
        if doc["source_text"]["interval_lines"] != expected_interval_lines:
            mismatches.append(f"{rec.record_id} interval_lines")
        if doc["name_as_published"] != rec.name_as_published:
            mismatches.append(f"{rec.record_id} name")
        if doc["n_intervals_declared"] != rec.n_intervals_declared:
            mismatches.append(f"{rec.record_id} n_intervals")
        if doc["coefficient_count"] != rec.coefficient_count:
            mismatches.append(f"{rec.record_id} coefficient_count")
        fresh = rec.to_dict()
        if published_float_pairs(doc) != published_float_pairs(fresh):
            mismatches.append(f"{rec.record_id} published_numbers")
        if len(mismatches) > 20:
            break
    assert mismatches == []


def test_complete_ingest_keeps_inverted_ions_reactants_and_assigned_enthalpy(
    record_docs,
) -> None:
    docs = record_docs
    by_name = {doc["name_as_published"]: [] for doc in docs}
    for doc in docs:
        by_name.setdefault(doc["name_as_published"], []).append(doc)
    br2 = by_name["Br2(cr)"][0]
    tmin = br2["intervals"][0]["T_min_K"]["value"]
    tmax = br2["intervals"][0]["T_max_K"]["value"]
    assert tmin == 300.0
    assert tmax == 265.9
    kinds = {item["kind"] for item in br2["ambiguities"]}
    assert "inverted_or_zero_width_T_interval" in kinds
    electron = by_name["e-"][0]
    assert electron["molecular_weight"]["value"] == 0.000548579903
    assert electron["phase"] == "gas"
    assert electron["phase_as_published"] == "0"
    sio2_aqz = by_name["SiO2(a-qz)"][0]
    assert sio2_aqz["phase_as_published"] == "a-qz"
    assert sio2_aqz["phase"] == "condensed"
    assert any(
        item["kind"] == "phase_suffix_normalized_to_condensed"
        and item["phase_as_published"] == "a-qz"
        for item in sio2_aqz["ambiguities"]
    )
    unlisted = [
        doc
        for doc in docs
        if any(
            item.get("kind") == "phase_suffix_normalized_to_condensed"
            for item in doc["ambiguities"]
        )
    ]
    assert len(unlisted) == 16
    suffixes = {
        next(
            item["phase_as_published"]
            for item in doc["ambiguities"]
            if item["kind"] == "phase_suffix_normalized_to_condensed"
        )
        for doc in unlisted
    }
    assert "a-qz" in suffixes
    assert "b-qz" in suffixes
    assert "b-crt" in suffixes
    assert "crI" in suffixes
    assert "an" in suffixes
    assert "Air" in by_name
    assert by_name["Air"][0]["cea_section"] == "reactants"
    assigned = [
        doc
        for doc in docs
        if doc["n_intervals_declared"] == 0 and doc["coefficient_count"] == 0
    ]
    assert len(assigned) == 54
    assert any(doc["name_as_published"] == "n-Butanol" for doc in assigned)


def test_feedstock_element_coverage_table(manifest, record_docs) -> None:
    elements = feedstock_element_symbols()
    assert "Si" in elements and "Fe" in elements and "O" in elements
    table = coverage_by_element(record_docs, elements)
    assert set(table) == set(elements)
    covered = [el for el, row in table.items() if row["has_record"]]
    uncovered = [el for el, row in table.items() if not row["has_record"]]
    for element in MAJOR_FEEDSTOCK_ELEMENTS:
        assert table[element]["has_record"], f"{element} has no NASA Glenn record"
        assert table[element]["record_count"] >= 1
    assert covered
    # Coverage is reported, not curated: REE/PGM feedstock elements with no
    # thermo.inp record stay listed as uncovered.
    for rare in ("Ce", "La", "Y", "Nd", "Hf"):
        assert rare in table
        assert table[rare]["has_record"] is False
        assert rare in uncovered
    assert manifest["feedstock_element_coverage"]["covered_elements"] == covered
    assert manifest["feedstock_element_coverage"]["uncovered_elements"] == uncovered


def test_held_extract_left_in_place_and_marked_incomplete(manifest) -> None:
    assert EXTRACT.is_file()
    missing = manifest["held_extract_missing_records"]
    assert len(missing) == 485
    assert sum(1 for row in missing if row["cea_section"] == "products") == 404
    assert sum(1 for row in missing if row["cea_section"] == "reactants") == 81
    names = {row["name_as_published"] for row in missing}
    assert "e-" in names
    assert "Air" in names
    assert "Br2(cr)" in names
    text = EXTRACT.read_text(encoding="utf-8")
    assert "schema_version: literature_extract.v1" in text
    assert "source_id: nasa-cea-thermo" in text


# Shared NASA-7 / NASA-9 coefficient field splitter (Burcat uses the 7-coeff form).
_NASA7_AG_SOLID = [
    "Ag (solid)        T 6/12AG 1.   0.   0.   0.S   200.000  1235.080  A 107.86820 1",
    " 2.07216824E+00 2.46393729E-03-1.34351116E-06 3.69321107E-10 0.00000000E+00    2",
    "-6.37725170E+02-7.18810718E+00 2.25225065E+00 5.43263008E-03-1.32153990E-05    3",
    " 1.50423505E-08-5.94991675E-12-8.23132027E+02-8.86835190E+00 0.00000000E+00    4",
]


def test_coeff_field_parser_serves_nasa9_and_nasa7_widths() -> None:
    """One splitter; 16-char CEA NASA-9 fields and 15-char NASA-7 fields."""
    nasa9 = " 0.000000000D+00 0.000000000D+00 2.500000000D+00 0.000000000D+00 0.000000000D+00"
    nasa9_fields = parse_coeff_fields(
        nasa9, field_width=NASA9_COEFF_FIELD_WIDTH, line_width=80
    )
    assert len(nasa9_fields) == 5
    assert nasa9_fields[2].strip() == "2.500000000D+00"
    nasa7_fields = parse_coeff_fields(
        _NASA7_AG_SOLID[1],
        field_width=NASA7_COEFF_FIELD_WIDTH,
        line_width=75,
    )
    assert len(nasa7_fields) == 5
    assert nasa7_fields[0].strip() == "2.07216824E+00"
    assert nasa7_fields[3].strip() == "3.69321107E-10"


def test_nasa7_four_line_variant_parses_burcat_header_and_fifteen_coeffs() -> None:
    assert is_nasa7_four_line_record(_NASA7_AG_SOLID, 0)
    header = parse_nasa7_header_line(_NASA7_AG_SOLID[0])
    assert header["name_as_published"] == "Ag (solid)"
    assert header["phase_as_published"] == "S"
    assert header["formula"] == "Ag"
    assert header["T_min_K"].value == 200.0
    assert header["T_max_K"].value == 1235.08
    assert header["molecular_weight"].value == 107.86820
    assert header["calc_quality_as_published"] == "A"
    coeffs, ambiguities = parse_nasa7_coefficient_lines(_NASA7_AG_SOLID[1:])
    assert ambiguities == []
    assert len(coeffs) == 15
    assert coeffs[0].as_published == "2.07216824E+00"
    assert coeffs[0].value == 2.07216824e00
    assert coeffs[6].value == -7.18810718e00
    assert coeffs[7].value == 2.25225065e00
    assert coeffs[14].value == 0.0
