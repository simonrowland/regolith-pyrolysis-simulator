"""Source-grounded checks for the transcribed USGS B1259 tables."""

from __future__ import annotations

import copy
import hashlib
from collections import Counter

import pytest

from simulator.reference_data.robie_waldbaum_1968_usgs_b1259_loader import (
    COMPILATION_ROOT,
    EXPECTED_298K_PAGE_COUNTS,
    PDF_SHA256,
    ROLE,
    TemperatureNotOnPrintedGrid,
    load_manifest,
    load_records,
    lookup,
    numeric_token,
    validate_record_source_tokens,
)


@pytest.fixture(scope="module")
def corpus():
    manifest = load_manifest()
    records = list(load_records())
    return manifest, records


@pytest.fixture(scope="module")
def layout_pages():
    text = (COMPILATION_ROOT / "source/layout.txt").read_text(encoding="utf-8")
    pages = text.split("\f")
    assert len(pages) == 263
    return pages


def test_source_census_and_repository_integrity(corpus):
    manifest, records = corpus
    census = manifest["census"]
    assert len(manifest["entries"]) == len(records) == census["total"] == 549
    assert len(list((COMPILATION_ROOT / "records").glob("*.json"))) == 549
    assert census["properties_298k_substances"] == 334
    assert census["high_temperature_substance_tables"] == 212
    assert census["toc_named_tables"] == 3
    assert census["transcribed"] == 548
    assert census["untranscribed_count"] == 1
    page_counts = Counter(
        record["pdf_page"] for record in records if record["table_kind"] == "properties_298k"
    )
    assert tuple(page_counts[page] for page in range(17, 32)) == EXPECTED_298K_PAGE_COUNTS
    assert {record["record_id"] for record in records} == {
        entry["record_id"] for entry in manifest["entries"]
    }
    for entry in manifest["entries"]:
        path = COMPILATION_ROOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["record_sha256"]


def test_298k_census_contains_omissions_and_no_header_fragments(corpus):
    _, records = corpus
    names = {
        record["name_as_published"]
        for record in records
        if record["table_kind"] == "properties_298k"
    }
    for expected in (
        "Al+++ aqueous ion",
        "Ca++ aqueous ion",
        "Ce+++ aqueous ion",
        "Hg₂++ aqueous ion",
        "K+ aqueous ion",
        "U++++ aqueous ion",
        "V+++ aqueous ion",
        "Zn++ aqueous ion",
        "3.2 Mullite",
    ):
        assert expected in names
    assert not {"(cal deg-", "(cal", "(cal deg- (cm:l ) (cal", "gfw ') in"} & names


def test_every_stored_raw_token_matches_its_layout_page(corpus, layout_pages):
    manifest, records = corpus
    layout_entry = next(item for item in manifest["source_files"] if item["path"] == "source/layout.txt")
    layout_bytes = (COMPILATION_ROOT / layout_entry["path"]).read_bytes()
    assert hashlib.sha256(layout_bytes).hexdigest() == layout_entry["sha256"]
    checked = 0
    for record in records:
        assert record["compilation_role"]["battery_refusal"] == ROLE["battery_refusal"]
        assert record["source"]["sha256"] == PDF_SHA256
        checked += validate_record_source_tokens(record, layout_pages[record["pdf_page"] - 1])
    assert checked > 10000
    for correction in manifest["corrections"]:
        raw = correction.get("layout_as_extracted", "")
        if raw:
            assert raw in layout_pages[correction["pdf_page"] - 1]
    ledger_ids = [correction["correction_id"] for correction in manifest["corrections"]]
    record_ids = [
        correction["correction_id"]
        for record in records
        for correction in record.get("corrections", [])
    ]
    assert len(ledger_ids) == len(set(ledger_ids))
    assert ledger_ids == record_ids
    attached_ids = {
        cell["correction_id"]
        for record in records
        for row in record.get("rows", [])
        for cell in row.values()
        if isinstance(cell, dict) and "correction_id" in cell
    }
    assert attached_ids <= set(ledger_ids)


def test_source_grounded_mutation_probe(corpus, layout_pages):
    _, records = corpus
    silver = next(record for record in records if record["record_id"].startswith("b1259-ht-0001"))
    corrupted = copy.deepcopy(silver)
    row = next(
        row
        for row in corrupted["rows"]
        if row.get("kind") == "data" and row["temperature"]["value"] == 400
    )
    row["H_minus_H298"]["as_published"] = "0.626"
    row["H_minus_H298"]["value"] = 0.626
    with pytest.raises(ValueError, match=r"b1259-ht-0001.*0\.626.*PDF page 32"):
        validate_record_source_tokens(corrupted, layout_pages[31])

    swapped = copy.deepcopy(silver)
    row = next(
        row
        for row in swapped["rows"]
        if row.get("kind") == "data" and row["temperature"]["value"] == 400
    )
    row["H_minus_H298"], row["entropy"] = row["entropy"], row["H_minus_H298"]
    row["source_column_spans"]["H_minus_H298"], row["source_column_spans"]["entropy"] = (
        row["source_column_spans"]["entropy"],
        row["source_column_spans"]["H_minus_H298"],
    )
    with pytest.raises(ValueError, match=r"b1259-ht-0001.*12\.010.*PDF page 32.*0\.625"):
        validate_record_source_tokens(swapped, layout_pages[31])


def test_cited_fidelity_repairs(corpus):
    _, records = corpus
    sulfur = next(record for record in records if "monoclinic-sulfur" in record["record_id"])
    assert [sulfur[key]["as_published"] for key in ("delta_f_H", "delta_f_G", "log_Kf")] == [
        "80",
        "26",
        "-.019",
    ]
    chrysotile = next(
        record
        for record in records
        if record["table_kind"] == "properties_298k" and "chrysotile" in record["record_id"]
    )
    assert chrysotile["entropy"]["as_published"] == ""
    assert chrysotile["molar_volume"]["as_published"] == "108.5"
    assert chrysotile["delta_f_H"]["as_published"] == "-1043180"
    cobalt = next(
        record
        for record in records
        if record["table_kind"] == "properties_298k" and record["name_as_published"] == "Cobalt"
    )
    assert cobalt["formula_as_published"] == "Co"
    assert cobalt["phase"] == "Hexagonal"
    for name, formula in (
        ("Boehmite", "AIO(OH)"),
        ("Diaspore", "AIO(OH)"),
        ("Goethite", "o-FeO(OH)"),
    ):
        record = next(
            record
            for record in records
            if record["table_kind"] == "properties_298k"
            and record["name_as_published"] == name
        )
        assert record["formula_as_published"] == formula
        assert record["phase"] == ""
    silver_298 = next(
        record
        for record in records
        if record["table_kind"] == "properties_298k" and record["name_as_published"] == "Silver"
    )
    assert silver_298["references_as_published"] == ["68", "68", "158"]
    for prefix, formula, temperatures in (
        ("b1259-ht-0069", "AlO(OH)", (298.15, 400, 500)),
        ("b1259-ht-0078", "Ca(OH)2", (298.15, 400, 500, 600, 700)),
    ):
        record = next(item for item in records if item["record_id"].startswith(prefix))
        assert record["formula_as_published"] == formula
        for temperature in temperatures:
            row = lookup(record, temperature)[0]
            assert row["delta_f_H"]["as_published"].startswith("-")
            assert row["delta_f_G"]["as_published"].startswith("-")


def test_lookup_uses_only_source_proven_temperature_grid(corpus):
    _, records = corpus
    silver = next(record for record in records if record["record_id"].startswith("b1259-ht-0001"))
    assert lookup(silver, 400)[0]["H_minus_H298"]["as_published"] == "0.625"
    assert len(lookup(silver, 1234)) == 2
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not a printed grid point"):
        lookup(silver, 350)
    wurtzite = next(record for record in records if record["record_id"].startswith("b1259-ht-0067"))
    assert len(lookup(wurtzite, 600)) == len(lookup(wurtzite, 700)) == 1
    tridymite = next(record for record in records if record["record_id"].startswith("b1259-ht-0114"))
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not a printed grid point"):
        lookup(tridymite, 298)
    assert len(lookup(tridymite, 298.15)) == len(lookup(tridymite, 2000)) == 1
    hydroxylapatite = next(
        record for record in records if record["record_id"].startswith("b1259-ht-0175")
    )
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not a printed grid point"):
        lookup(hydroxylapatite, 57.355)
    assert len(lookup(hydroxylapatite, 400)) == len(lookup(hydroxylapatite, 1500)) == 1
    untranscribed = next(record for record in records if record.get("untranscribed"))
    with pytest.raises(TemperatureNotOnPrintedGrid, match="untranscribed"):
        lookup(untranscribed, 298.15)


@pytest.mark.parametrize("token", ["10.2CO", "13^750", "footnote a", "1,234", "7. 78"])
def test_ambiguous_numbers_are_never_guessed(token):
    with pytest.raises(ValueError, match="non-numeric"):
        numeric_token(token)
