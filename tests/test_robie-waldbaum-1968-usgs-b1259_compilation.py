"""Lossless checks against the transcribed USGS B1259 tables, not simulator predictions."""

from __future__ import annotations

import pytest

from simulator.reference_data.robie_waldbaum_1968_usgs_b1259_loader import (
    COMPILATION_ROOT,
    HIGH_T_COLUMNS,
    PDF_SHA256,
    ROLE,
    TemperatureNotOnPrintedGrid,
    load_manifest,
    load_records,
    lookup,
    numeric_token,
)


@pytest.fixture(scope="module")
def corpus():
    manifest = load_manifest()
    records = list(load_records())
    return manifest, records


def test_coverage_manifest_equals_records_equals_census(corpus):
    manifest, records = corpus
    census = manifest["census"]
    assert len(manifest["entries"]) == len(records) == census["total"]
    assert len(list((COMPILATION_ROOT / "records").glob("*.json"))) == census["total"]
    assert census["transcribed"] + census["untranscribed_count"] == census["total"]
    assert census["high_temperature_substance_tables"] == 212
    assert census["toc_named_tables"] == 3
    assert census["untranscribed_count"] == 1
    assert {r["record_id"] for r in records} == {e["record_id"] for e in manifest["entries"]}


def test_round_trip_clean_tokens_reproduce_parsed_values(corpus):
    _, records = corpus
    checked = 0
    for record in records:
        assert record["compilation_role"]["battery_refusal"] == ROLE["battery_refusal"]
        assert record["source"]["sha256"] == PDF_SHA256
        if record.get("untranscribed"):
            assert record["rows"] == []
            continue
        cells = []
        if record.get("table_kind") == "high_temperature":
            for row in record.get("rows") or []:
                if row.get("kind") != "data":
                    continue
                for column in HIGH_T_COLUMNS:
                    cells.append(row[column])
        elif record.get("table_kind") == "properties_298k":
            cells.append(record["gram_formula_weight"])
            for key in ("entropy", "molar_volume", "delta_f_H", "delta_f_G", "log_Kf"):
                cells.append(record[key])
        for cell in cells:
            if cell.get("ocr_suspect") or cell.get("value") is None or not cell.get("as_published"):
                continue
            assert float(numeric_token(cell["as_published"])) == cell["value"]
            checked += 1
    assert checked > 10000


def test_lookup_refuses_off_grid_and_returns_printed_rows(corpus):
    _, records = corpus
    silver = next(r for r in records if r["record_id"].startswith("b1259-ht-0001"))
    at_400 = lookup(silver, 400)
    assert len(at_400) == 1
    assert at_400[0]["H_minus_H298"]["as_published"] == "0.625"
    assert at_400[0]["H_minus_H298"]["value"] == 0.625
    at_1234 = lookup(silver, 1234)
    assert len(at_1234) == 2
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not a printed grid point"):
        lookup(silver, 350)
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not a printed grid point"):
        lookup(silver, 0)
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not a printed grid point"):
        lookup(silver, 3000)
    untranscribed = next(r for r in records if r.get("untranscribed"))
    with pytest.raises(TemperatureNotOnPrintedGrid, match="untranscribed"):
        lookup(untranscribed, 298.15)
    table1 = next(r for r in records if r["table_kind"] == "symbols_and_constants")
    with pytest.raises(TemperatureNotOnPrintedGrid, match="high-temperature"):
        lookup(table1, 298.15)


@pytest.mark.parametrize("token", ["10.2CO", "13^750", "footnote a", "1,234"])
def test_ambiguous_numbers_are_never_guessed(token):
    with pytest.raises(ValueError, match="non-numeric"):
        numeric_token(token)
