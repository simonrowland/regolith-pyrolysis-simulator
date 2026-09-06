"""Lossless checks against the USGS B1544 OCR layer, not simulator predictions."""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.reference_data.hemingway_haas_robinson_1982_usgs_b1544_loader import (
    COMPILATION_ROOT,
    EXPECTED_PDF_SHA256,
    ROLE,
    SOURCE_PDF_NAME,
    AmbiguousPrintedTemperature,
    TemperatureNotOnPrintedGrid,
    census_from_records,
    load_records,
    lookup,
    parse_source,
    sha256_file,
)

PDF = COMPILATION_ROOT / "source" / SOURCE_PDF_NAME


@pytest.fixture(scope="module")
def corpus():
    manifest = yaml.safe_load((COMPILATION_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
    records = load_records()
    return manifest, records


def test_coverage_manifest_records_census(corpus):
    manifest, records = corpus
    census = manifest["census"]
    assert len(manifest["entries"]) == len(records) == len(census) == 33
    assert [e["record_id"] for e in manifest["entries"]] == [r["record_id"] for r in records]
    assert [c["record_id"] for c in census] == [r["record_id"] for r in records]
    assert census_from_records(records) == census
    assert manifest["summary"]["record_count"] == 33
    assert manifest["compilation_role"]["validation_measurement"] is False
    assert manifest["compilation_role"]["scoring_eligible"] is False
    assert manifest["compilation_role"]["battery_refusal"] == "gibbs_table_not_runtime_observable"


def test_source_hash_and_role(corpus):
    manifest, records = corpus
    assert sha256_file(PDF) == EXPECTED_PDF_SHA256
    pdf_entry = next(s for s in manifest["source_files"] if s["path"].endswith(".pdf"))
    assert pdf_entry["sha256"] == EXPECTED_PDF_SHA256
    for record in records:
        assert record["compilation_role"] == ROLE
        assert record["schema_version"] == "literature_compilation.v1"


def test_round_trip_parsed_values(corpus):
    _, records = corpus
    parsed = parse_source(PDF, verbose=False)
    assert [r["record_id"] for r in parsed] == [r["record_id"] for r in records]
    for loaded, fresh in zip(records, parsed, strict=True):
        assert loaded["record_id"] == fresh["record_id"]
        assert loaded["row_count"] == fresh["row_count"]
        if loaded["record_id"] == "usgs-b1544-table-1":
            assert [row["phase_as_published"] for row in loaded["rows"]] == [
                row["phase_as_published"] for row in fresh["rows"]
            ]
            continue
        for a, b in zip(loaded["rows"], fresh["rows"], strict=True):
            assert (a.get("temperature") or {}).get("as_published") == (
                b.get("temperature") or {}
            ).get("as_published")
            assert (a.get("entropy") or {}).get("value") == (b.get("entropy") or {}).get("value")
            left = ((a.get("formation") or {}).get("from_the_elements") or {}).get("enthalpy") or {}
            right = ((b.get("formation") or {}).get("from_the_elements") or {}).get("enthalpy") or {}
            assert left.get("as_published") == right.get("as_published")


def test_corundum_printed_298_nodes():
    records = load_records()
    corundum = next(r for r in records if r["record_id"] == "usgs-b1544-corundum")
    row0 = corundum["rows"][0]
    assert row0["temperature"]["as_published"] == "298.15"
    assert row0["entropy"]["as_published"] == "50.92"
    assert row0["planck_function"]["as_published"] == "50.92"
    formed = row0["formation"]["from_the_elements"]
    assert formed["enthalpy"]["as_published"] == "-1675.711"
    assert formed["gibbs_energy"]["as_published"] == "-1582.242"
    assert formed["log_kf"]["as_published"] == "277.203"
    assert lookup("usgs-b1544-corundum", "298.15", "entropy") == Decimal("50.92")
    assert lookup("usgs-b1544-corundum", Decimal("1800"), "heat_capacity") == Decimal(
        corundum["rows"][-1]["heat_capacity"]["value"]
    )


def test_lookup_refuses_off_grid_and_duplicates():
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not on the printed grid"):
        lookup("usgs-b1544-corundum", "350", "entropy")
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not on the printed grid"):
        lookup("usgs-b1544-corundum", "0", "entropy")
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not on the printed grid"):
        lookup("usgs-b1544-corundum", "2500", "entropy")
    with pytest.raises(AmbiguousPrintedTemperature, match="occurs 2 times"):
        lookup("usgs-b1544-quartz", "844", "entropy")


def test_ocr_suspect_keeps_raw_token():
    records = load_records()
    corundum = next(r for r in records if r["record_id"] == "usgs-b1544-corundum")
    hht = corundum["rows"][0]["enthalpy_increment_over_T"]
    assert hht["as_published"] == "o.ooo"
    assert hht["ocr_suspect"] is True
    assert hht["value"] == "0.000"


def test_nothing_typed_measured(corpus):
    manifest, records = corpus
    for record in records:
        assert record["compilation_role"]["validation_measurement"] is False
        assert "measured" not in json_blob(record).lower()
    assert manifest["compilation_role"]["validation_measurement"] is False


def json_blob(record):
    import json

    return json.dumps(record)
