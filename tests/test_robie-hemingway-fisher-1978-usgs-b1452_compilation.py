"""Coverage and losslessness checks for USGS Bulletin 1452."""

import json

import pytest

from simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader import (
    COMPILATION_ROOT,
    ROLE,
    SOURCE_ID,
    SOURCE_SHA256,
    TemperatureNotInPrintedGridError,
    UntranscribedTableError,
    iter_published_numbers,
    load_manifest,
    load_records,
    lookup_temperature,
)


@pytest.fixture(scope="module")
def compilation():
    return load_manifest(), list(load_records())


def test_manifest_count_record_count_and_census_are_equal(compilation):
    manifest, records = compilation
    record_files = sorted((COMPILATION_ROOT / "records").glob("*.json"))
    census = manifest["corpus_status"]["census_record_ids"]
    assert manifest["summary"]["census_count"] == 400
    assert manifest["summary"]["record_count"] == 400
    assert len(manifest["entries"]) == len(records) == len(record_files) == len(census) == 400
    assert len(set(census)) == 400
    assert [record["record_id"] for record in records] == census
    assert [record["source_locator"]["printed_pages"][0] for record in records[3:]] == list(range(30, 427))
    assert manifest["corpus_status"]["excluded_non_table_page"] == {
        "printed_page": 427,
        "pdf_page": 433,
        "reason": "visually blank",
    }


def test_every_record_round_trips_its_parsed_values(compilation):
    manifest, records = compilation
    entry_by_id = {entry["record_id"]: entry for entry in manifest["entries"]}
    assert manifest["summary"]["transcribed_count"] == 96
    assert manifest["summary"]["untranscribed_count"] == 304
    assert manifest["summary"]["temperature_row_count"] == 1528
    for record in records:
        entry = entry_by_id[record["record_id"]]
        assert record["source_sha256"] == entry["sha256"] == SOURCE_SHA256
        assert record["compilation_role"] == ROLE
        assert entry["source"] == manifest["source"]


def test_round_trip_values_and_manifest_entries(compilation):
    manifest, records = compilation
    entry_by_id = {entry["record_id"]: entry for entry in manifest["entries"]}
    for record in records:
        entry = entry_by_id[record["record_id"]]
        assert record["compilation_role"] == ROLE
        assert entry["row_count"] == record["row_count"]
        assert entry["transcription_status"] == record["transcription_status"]
        assert entry["ambiguity_count"] == len(record["ambiguities"])
        assert list(iter_published_numbers(record))
        assert record["temperature_grid"] == [row["cells"]["temperature"] for row in record["rows"]]
        persisted = json.loads((COMPILATION_ROOT / entry["path"]).read_text(encoding="utf-8"))
        assert persisted == record
        if record["transcription_status"] == "transcribed":
            assert record["rows"]
            assert all(len(row["cells"]) == 8 for row in record["rows"])
        else:
            assert record["rows"] == []
            assert record["untranscribed_reasons"]


def test_ocr_suspects_keep_raw_tokens_and_are_never_corrected(compilation):
    manifest, records = compilation
    suspect_cells = []
    for record in records:
        suspect_cells.extend(number for number in iter_published_numbers(record) if number.ocr_suspect)
    assert len(suspect_cells) == manifest["summary"]["ocr_suspect_numeric_cell_count"] == 3377
    raw = {number.as_published for number in suspect_cells}
    assert {"107.!168", "o.uoo", "• 000"} <= raw
    assert all(number.value is None for number in suspect_cells)
    disagreements = [
        ambiguity
        for record in records
        for ambiguity in record["ambiguities"]
        if ambiguity["kind"] == "thermodynamic_identity_disagreement"
    ]
    assert len(disagreements) == manifest["summary"]["identity_check_disagreement_count"] == 121
    assert all(ambiguity["detector_only"] is True for ambiguity in disagreements)


def test_lookup_is_exact_grid_only_and_keeps_transition_duplicates():
    record_id = f"{SOURCE_ID}-0004"
    rows = lookup_temperature(record_id, 400)
    assert len(rows) == 1
    assert rows[0]["cells"]["temperature"]["as_published"] == "400"
    transition_rows = lookup_temperature(record_id, "1234")
    assert len(transition_rows) == 2
    with pytest.raises(TemperatureNotInPrintedGridError):
        lookup_temperature(record_id, 350)
    with pytest.raises(TemperatureNotInPrintedGridError):
        lookup_temperature(record_id, 1801)
    with pytest.raises(UntranscribedTableError):
        lookup_temperature(f"{SOURCE_ID}-0011", 400)


def test_compilation_is_never_measurement_or_battery_scoring(compilation):
    manifest, records = compilation
    assert manifest["compilation_role"] == ROLE
    assert ROLE["validation_measurement"] is False
    assert ROLE["scoring_eligible"] is False
    assert ROLE["battery_refusal"] == "gibbs_table_not_runtime_observable"
    assert all(record["compilation_role"] == ROLE for record in records)
