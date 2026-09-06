"""Native record coverage, OCR fidelity, and exact-grid refusal contract."""

import json
import re

import pytest

from simulator.reference_data.robie_hemingway_1995_usgs_b2131_loader import (
    COMPILATION_ROOT,
    PrintedTemperatureUnavailable,
    UntranscribedTableError,
    load_manifest,
    load_records,
    lookup_temperature,
)


def numeric_cells(value):
    if isinstance(value, dict):
        if "raw" in value and "value" in value:
            yield value
        for child in value.values():
            yield from numeric_cells(child)
    elif isinstance(value, list):
        for child in value:
            yield from numeric_cells(child)


def test_round_trip_every_record_and_numeric_token():
    manifest = load_manifest()
    for entry, record in zip(manifest["entries"], load_records(), strict=True):
        original = json.loads((COMPILATION_ROOT / entry["path"]).read_text())
        assert record == original
        if record.get("table_kind") == "high_temperature":
            for row in record["rows"]:
                assert re.split(r"\s{2,}", row["raw"].strip()) == [cell["raw"] for cell in row["cells"].values()]
        if "source_text" in record:
            source_lines = record["source_text"].splitlines()
            for row in record["rows"]:
                for cell in numeric_cells(row):
                    if cell.get("start_offset") is not None:
                        assert source_lines[row["source_line_index"]][cell["start_offset"]:cell["end_offset"]] == cell["raw"]
        for cell in numeric_cells(record):
            assert isinstance(cell["raw"], str)
            if cell["value"] is not None:
                assert cell["value"] == float(cell.get("numeric_token", cell["raw"]))
            elif cell["raw"].strip():
                assert cell["ocr_suspect"]


def test_census_coverage_includes_explicit_untranscribed_tables():
    manifest = load_manifest()
    records = list(load_records())
    ids = [record["record_id"] for record in records]
    assert len(ids) == len(set(ids))
    assert len(records) == manifest["summary"]["record_count"] == manifest["census"]["record_count"]
    assert set(ids) == set(manifest["census"]["record_ids"])
    independent_census = json.loads((COMPILATION_ROOT / "census.json").read_text())
    assert manifest["census"] == independent_census
    for page in independent_census["summary_page_record_counts"]:
        assert sum(record["pdf_page"] == page["pdf_page"] for record in records) == page["record_count"]
    assert {record["pdf_page"] for record in records if record.get("table_kind") == "high_temperature"} == set(range(73, 403))
    assert sum(record.get("transcription_status") == "untranscribed" for record in records) == manifest["summary"]["untranscribed_record_count"]
    assert manifest["compilation_role"]["scoring_eligible"] is False
    assert manifest["compilation_role"]["validation_measurement"] is False


def test_feedstock_element_coverage_matches_existing_coverage_helper():
    from tools.harvest_atct_compilation import feedstock_coverage

    records = [{"formula": record.get("formula_as_published") or ""} for record in load_records()]
    assert feedstock_coverage(records) == load_manifest()["feedstock_element_coverage"]


def test_silver_printed_anchor_and_transition_rows():
    rows = lookup_temperature("high-temperature-p067", 298.15)
    assert len(rows) == 1
    assert rows[0]["cells"]["cp"]["raw"] == "25.40"
    assert rows[0]["cells"]["cp"]["value"] == 25.4
    assert rows[0]["cells"]["entropy"]["value"] == 42.55
    # Source OCR reads o.oo; numeric zero must not be manufactured.
    assert rows[0]["cells"]["enthalpy_function"]["raw"] == "o.oo"
    assert rows[0]["cells"]["enthalpy_function"]["value"] is None
    assert rows[0]["cells"]["enthalpy_function"]["ocr_suspect"]
    transition = lookup_temperature("high-temperature-p067", 1234.9)
    assert [row["cells"]["cp"]["value"] for row in transition] == [31.96, 33.47]


@pytest.mark.parametrize("temperature", [0, 298.14, 299, 1800.01, float("nan"), float("inf")])
def test_refuses_every_non_grid_temperature(temperature):
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("high-temperature-p067", temperature)


def test_refuses_missing_ocr_table():
    with pytest.raises(UntranscribedTableError):
        lookup_temperature("high-temperature-p176", 298.15)


def test_unknown_record_has_no_default():
    with pytest.raises(KeyError):
        lookup_temperature("not-a-record", 298.15)


def test_atomic_weight_has_no_temperature_grid():
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("atomic-weight-001", 298.15)


def test_reference_table_accepts_only_its_single_printed_temperature():
    rows = lookup_temperature("reference-p005-01", 298.15)
    assert rows[0]["cells"]["volume"]["value"] == 10.272
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("reference-p005-01", 300)


def test_coefficient_bounds_are_not_a_printed_function_grid():
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("cp-p041-01", 298.15)
