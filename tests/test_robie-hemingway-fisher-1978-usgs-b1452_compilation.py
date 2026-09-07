"""Coverage and source-losslessness checks for USGS Bulletin 1452."""

import copy
import hashlib
import re
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader import (
    COMPILATION_ROOT,
    ROLE,
    SOURCE_ID,
    SOURCE_SHA256,
    OcrSuspectGridTokenError,
    OcrSuspectTableValueError,
    TemperatureNotInPrintedGridError,
    UntranscribedTableError,
    feedstock_coverage,
    iter_published_numbers,
    load_manifest,
    load_records,
    lookup_temperature,
)


SOURCE_PDF = Path(
    "/Users/simonrowland/Repos/regolith-corpus/raw/"
    "robie-hemingway-fisher-1978-usgs-b1452/"
    "robie-hemingway-fisher-1978-usgs-b1452.pdf"
)
NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")


@pytest.fixture(scope="module")
def compilation():
    return load_manifest(), list(load_records())


@pytest.fixture(scope="module")
def source_layout(compilation):
    _, records = compilation
    assert SOURCE_PDF.is_file(), f"read-only corpus PDF is required: {SOURCE_PDF}"
    assert hashlib.sha256(SOURCE_PDF.read_bytes()).hexdigest() == SOURCE_SHA256
    pdftotext = shutil.which("pdftotext")
    assert pdftotext, "pdftotext is required for the independent source round trip"
    pages = sorted({page for record in records for page in record["source_locator"]["pdf_pages"]})
    return {
        page: subprocess.run(
            [pdftotext, "-f", str(page), "-l", str(page), "-layout", str(SOURCE_PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        for page in pages
    }


def assert_record_matches_source(record, layouts):
    for number in [record["formula_weight"], *record["uncertainty_values"]]:
        if number["as_published"]:
            span = number["source_text_span"]
            assert span is not None
            page_lines = layouts[record["source_locator"]["pdf_pages"][0]]
            raw = page_lines[span["line"] - 1][span["start"] : span["end"]]
            assert number["as_published"] == raw
    if not record["rows"]:
        return
    page_lines = layouts[record["source_locator"]["pdf_pages"][0]]
    for row in record["rows"]:
        for column, span in row["source_text_spans"].items():
            source_line = page_lines[span["line"] - 1]
            raw = source_line[span["start"] : span["end"]]
            assert row["cells"][column]["as_published"] == raw
            assert span["start"] == 0 or source_line[span["start"] - 1].isspace()
            assert span["end"] == len(source_line) or source_line[span["end"]].isspace()


def token_has_printed_shape(raw, column):
    core = raw.strip().strip("*•†‡").strip()
    if NUMBER_RE.fullmatch(core) is None:
        return False
    return column != "temperature" or Decimal("250") <= Decimal(core) <= Decimal("2500")


def test_manifest_table_census_and_phase_record_count(compilation):
    manifest, records = compilation
    record_files = sorted((COMPILATION_ROOT / "records").glob("*.json"))
    assert manifest["summary"] == {
        "census_count": 400,
        "record_count": 536,
        "transcribed_table_count": 235,
        "untranscribed_table_count": 165,
        "transcribed_record_count": 371,
        "untranscribed_record_count": 165,
        "temperature_row_count": 3442,
        "phase_split_record_count": 136,
        "image_temperature_confirmed_table_count": 146,
        "ocr_suspect_record_count": 483,
        "ocr_suspect_numeric_cell_count": 7128,
        "printed_shape_failure_count": 4164,
        "admitted_shape_failure_count": 0,
        "identity_check_disagreement_count": 677,
        "image_temperature_disagreement_count": 108,
        "ambiguity_count": 1037,
    }
    assert len(records) == len(record_files) == manifest["summary"]["record_count"]
    assert len({record["census_id"] for record in records}) == 400
    assert len(manifest["corpus_status"]["census_record_ids"]) == 400
    remainder = manifest["corpus_status"]["mineru_followup"]
    assert remainder["store_task"] == "t-852"
    assert len(remainder["untranscribed"]) == 165
    assert manifest["corpus_status"]["excluded_non_table_page"] == {
        "printed_page": 427,
        "pdf_page": 433,
        "reason": "visually blank",
    }


def test_every_numeric_token_round_trips_to_page_layout(compilation, source_layout):
    _, records = compilation
    for record in records:
        assert_record_matches_source(record, source_layout)


def test_source_round_trip_rejects_consistent_token_value_mutation(compilation, source_layout):
    _, records = compilation
    record = copy.deepcopy(next(record for record in records if record["rows"]))
    cell = record["rows"][0]["cells"]["temperature"]
    cell["as_published"] = "299.15"
    cell["value"] = 299.15
    with pytest.raises(AssertionError):
        assert_record_matches_source(record, source_layout)

    metadata_record = copy.deepcopy(
        next(record for record in records if record["record_id"] == f"{SOURCE_ID}-0397")
    )
    metadata_record["formula_weight"]["as_published"] = "400"
    metadata_record["formula_weight"]["value"] = 400.0
    with pytest.raises(AssertionError):
        assert_record_matches_source(metadata_record, source_layout)


def test_shape_failures_are_suspect_and_never_admitted(compilation):
    manifest, records = compilation
    admitted_failures = []
    shape_failures = []
    suspect_count = 0
    for record in records:
        for row in record["rows"]:
            for column, cell in row["cells"].items():
                shape_ok = token_has_printed_shape(cell["as_published"], column)
                if not shape_ok:
                    shape_failures.append((record["record_id"], column, cell["as_published"]))
                    if cell["value"] is not None or not cell["ocr_suspect"]:
                        admitted_failures.append((record["record_id"], column, cell))
                suspect_count += cell["ocr_suspect"]
    assert not admitted_failures
    assert len(shape_failures) == manifest["summary"]["printed_shape_failure_count"]
    assert suspect_count == manifest["summary"]["ocr_suspect_numeric_cell_count"]
    assert manifest["summary"]["admitted_shape_failure_count"] == 0


def test_phase_records_have_strict_safe_grids_and_no_embedded_headers(compilation, source_layout):
    manifest, records = compilation
    for record in records:
        values = [
            row["cells"]["temperature"]["value"]
            for row in record["rows"]
            if row["cells"]["temperature"]["value"] is not None
        ]
        assert all(right > left for left, right in zip(values, values[1:]))
        if not record["rows"]:
            continue
        page_lines = source_layout[record["source_locator"]["pdf_pages"][0]]
        first = record["rows"][0]["source_text_line"]
        last = record["rows"][-1]["source_text_line"]
        embedded = [
            line
            for line in page_lines[first:last]
            if re.match(r"^\s{0,3}[A-Za-z][A-Za-z0-9(){}]*\s*:", line)
        ]
        assert not embedded, (record["record_id"], embedded)
    assert manifest["summary"]["phase_split_record_count"] == 136


def test_lookup_refuses_suspect_and_off_grid_values():
    with pytest.raises(OcrSuspectGridTokenError):
        lookup_temperature(f"{SOURCE_ID}-0005", 100)
    with pytest.raises(TemperatureNotInPrintedGridError):
        lookup_temperature(f"{SOURCE_ID}-0005", 700)
    with pytest.raises(OcrSuspectTableValueError):
        lookup_temperature(f"{SOURCE_ID}-0005", 400)
    rows = lookup_temperature(f"{SOURCE_ID}-0231-phase-02", 1700)
    assert len(rows) == 1
    assert rows[0]["cells"]["temperature"]["as_published"] == "1700"
    with pytest.raises(OcrSuspectTableValueError):
        lookup_temperature(f"{SOURCE_ID}-0010", 400)


def test_numeric_looking_formula_weight_disagreement_is_not_admitted(compilation):
    _, records = compilation
    record = next(record for record in records if record["record_id"] == f"{SOURCE_ID}-0397")
    assert record["formula_weight"]["as_published"] == "360.311"
    assert record["formula_weight"]["value"] is None
    assert record["formula_weight"]["ocr_suspect"] is True


def test_feedstock_element_coverage_report(compilation):
    manifest, records = compilation
    coverage = feedstock_coverage(records)
    assert coverage == manifest["feedstock_element_coverage"]
    assert set(coverage) == set(
        "Ag Al As Au B Ba Bi Br C Ca Cd Ce Cl Co Cr Cs Cu Dy Er Eu F Fe Ga Gd Ge H Hf Ho I In Ir K "
        "La Li Lu Mg Mn Mo N Na Nb Nd Ni O Os P Pb Pr Pt Rb S Sb Sc Se Si Sm Sn Sr Tb Te Th Ti Tm U V "
        "W Y Yb Zn Zr".split()
    )
    chromium = next(record for record in records if record["record_id"] == f"{SOURCE_ID}-0021")
    assert chromium["formula_as_published"] == "cr"
    assert coverage["Cr"] > 0


def test_compilation_is_never_measurement_or_battery_scoring(compilation):
    manifest, records = compilation
    assert manifest["compilation_role"] == ROLE
    assert ROLE["validation_measurement"] is False
    assert ROLE["scoring_eligible"] is False
    assert ROLE["battery_refusal"] == "gibbs_table_not_runtime_observable"
    assert all(record["compilation_role"] == ROLE for record in records)
    assert all(list(iter_published_numbers(record)) for record in records)
