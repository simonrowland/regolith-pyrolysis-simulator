"""Source-grounded checks for the transcribed USGS B1259 tables."""

from __future__ import annotations

import copy
import hashlib
import inspect
from collections import Counter

import pytest

from simulator.reference_data import robie_waldbaum_1968_usgs_b1259_loader as loader
from simulator.reference_data.robie_waldbaum_1968_usgs_b1259_loader import (
    COMPILATION_ROOT,
    EXPECTED_298K_PAGE_COUNTS,
    PDF_SHA256,
    ROLE,
    OCRSuspectRow,
    TemperatureNotOnPrintedGrid,
    load_manifest,
    load_records,
    lookup,
    numeric_token,
    validate_record_source_tokens,
)


IMAGE_VERIFIED_OCR_SWEEP = (
    ("b1259-table-02-atomic-weights", 10, None, "weight", "9994", "15.9994", "Oxygen | O | 15.9994"),
    ("b1259-table-02-atomic-weights", 10, None, "weight", "01", "9.0122", "Beryllium | Be | 9.0122"),
    ("b1259-table-02-atomic-weights", 10, None, "weight", "99", "", "Protactinium | Pa | [blank]"),
    ("b1259-ht-0016-co", 47, 700, "H_minus_H298", "2.608", "2.808", "700 | 2.808 | 12.970 | 8.963"),
    ("b1259-ht-0055-methane-ideal-gas", 86, 500, "H_minus_H298", "960", "1.960", "500 | 1.960 | 49.45 | 45.53"),
    ("b1259-ht-0055-methane-ideal-gas", 86, 600, "H_minus_H298", "138", "3.138", "600 | 3.138 | 51.60 | 46.37"),
    ("b1259-ht-0055-methane-ideal-gas", 86, 700, "H_minus_H298", "454", "4.454", "700 | 4.454 | 53.62 | 47.26"),
    ("b1259-ht-0055-methane-ideal-gas", 86, 800, "H_minus_H298", "897", "5.897", "800 | 5.897 | 55.55 | 48.18"),
    ("b1259-ht-0055-methane-ideal-gas", 86, 900, "H_minus_H298", "458", "7.458", "900 | 7.458 | 57.38 | 49.10"),
    ("b1259-ht-0062-alabandite", 93, 400, "H_minus_H298", "220", "1.220", "400 | 1.220 | 22.21 | 19.16"),
    ("b1259-ht-0062-alabandite", 93, 500, "H_minus_H298", "440", "2.440", "500 | 2.440 | 24.93 | 20.05"),
    ("b1259-ht-0062-alabandite", 93, 600, "H_minus_H298", "690", "3.690", "600 | 3.690 | 27.21 | 21.06"),
    ("b1259-ht-0062-alabandite", 93, 700, "H_minus_H298", "970", "4.970", "700 | 4.970 | 29.18 | 22.08"),
    ("b1259-ht-0101-nitrogen-dioxide-ideal-gas", 132, 1200, "entropy", "72162", "72.62", "1200 | 10.324 | 72.62 | 64.02"),
    ("b1259-ht-0108-minium", 139, 1900, "gibbs_function", "68.86", "88.86", "1900 | 66.273 | 123.74 | 88.86"),
    ("b1259-ht-0126-zincite", 157, 400, "H_minus_H298", "070", "1.070", "400 | 1.070 | 13.51 | 10.83"),
    ("b1259-ht-0126-zincite", 157, 500, "H_minus_H298", "190", "2.190", "500 | 2.190 | 16.01 | 11.63"),
    ("b1259-ht-0126-zincite", 157, 600, "H_minus_H298", "350", "3.350", "600 | 3.350 | 18.12 | 12.54"),
    ("b1259-ht-0126-zincite", 157, 700, "H_minus_H298", "530", "4.530", "700 | 4.530 | 19.94 | 13.47"),
    ("b1259-ht-0126-zincite", 157, 800, "H_minus_H298", "740", "5.740", "800 | 5.740 | 21.56 | 14.38"),
    ("b1259-ht-0136-geikelite", 167, 1000, "log_Kf", "557905", "66.905", "1000 | -376.086 | -306.130 | 66.905"),
    ("b1259-ht-0149-fluorite", 180, 1200, "delta_f_H", "-2897250", "-289.250", "1200 | 17.850 | 43.10 | 28.22 | -289.250"),
    ("b1259-ht-0156-aragonite", 187, 800, "log_Kf", "657690", "65.690", "800 | -287.601 | -238.264 | 65.690"),
    ("b1259-ht-0161-strontianite", 192, 900, "delta_f_H", "-2957159", "-293.159", "900 | 15.250 | 50.45 | 33.51 | -293.159"),
    ("b1259-ht-0175-hydroxylapatite", 206, 800, "log_Kf", "8167026", "816.026", "800 | -3272.661 | -2987.065 | 816.026"),
    ("b1259-ht-0188-fayalite", 219, 1000, "H_minus_H298", "23.310", "28.310", "1000 | 28.310 | 82.78 | 54.47"),
)

MONOTONICITY_REVERSAL_ALLOWLIST = (
    ("b1259-ht-0002-aluminum-reference-state", 33, "entropy", "6.770", "6.259"),
    ("b1259-ht-0002-aluminum-reference-state", 33, "entropy", "17.604", "16.327"),
    ("b1259-ht-0006-barium-reference-state", 37, "entropy", "51.030", "50.560"),
    ("b1259-ht-0008-bismuth-reference-state", 39, "temperature", "2000", "1900"),
    ("b1259-ht-0008-bismuth-reference-state", 39, "H_minus_H298", "15.190", "14.440"),
    ("b1259-ht-0008-bismuth-reference-state", 39, "entropy", "32.110", "31.720"),
    ("b1259-ht-0028-manganese-reference-state", 59, "H_minus_H298", "17.480", "16.580"),
    ("b1259-ht-0045-strontium-reference-state", 76, "H_minus_H298", "46.769", "46.269"),
    ("b1259-ht-0046-tellurium-reference-state", 77, "H_minus_H298", "24.402", "24.250"),
    ("b1259-ht-0046-tellurium-reference-state", 77, "entropy", "38.644", "38.580"),
    ("b1259-ht-0176-fluorapatite", 207, "entropy", "239.20", "200.20"),
)

IMAGE_VERIFIED_NONMONOTONE_GIBBS = (
    "b1259-ht-0176-fluorapatite",
    207,
    "gibbs_function",
    "154.78",
    "112.22",
)


@pytest.fixture(scope="module")
def corpus():
    manifest = load_manifest()
    records = list(load_records(include_ocr_suspect=True))
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

    value_only = copy.deepcopy(silver)
    row = next(
        row
        for row in value_only["rows"]
        if row.get("kind") == "data" and row["temperature"]["value"] == 400
    )
    row["H_minus_H298"]["value"] = 9.625
    with pytest.raises(ValueError, match=r"b1259-ht-0001.*9\.625.*0\.625"):
        validate_record_source_tokens(value_only, layout_pages[31])
    row["H_minus_H298"]["value"] = 0.625
    row["entropy"]["value"] = 99.01
    with pytest.raises(ValueError, match=r"b1259-ht-0001.*99\.01"):
        validate_record_source_tokens(value_only, layout_pages[31])
    row["entropy"]["value"] = 12.01
    row["delta_f_G"]["value"] = -88.8
    with pytest.raises(ValueError, match=r"b1259-ht-0001.*-88\.8"):
        validate_record_source_tokens(value_only, layout_pages[31])


def _assert_image_verified_ocr_sweep(records):
    by_id = {record["record_id"]: record for record in records}
    for record_id, page, temperature, field, raw, printed, quote in IMAGE_VERIFIED_OCR_SWEEP:
        record = by_id[record_id]
        assert record["pdf_page"] == page
        correction = next(
            item
            for item in record["corrections"]
            if item.get("ocr_token") == raw
            and item["field"] == field
            and item["printed_token"] == printed
        )
        assert correction["record_id"] == record_id
        assert correction["image_quote"] == quote
        row, cell = next(
            (row, row[field])
            for row in record["rows"]
            if isinstance(row.get(field), dict)
            and row[field].get("layout_as_extracted") == raw
            and row[field].get("correction_id") == correction["correction_id"]
        )
        if temperature is not None:
            assert row["temperature"]["value"] == temperature
        assert cell["as_published"] == printed
        assert cell["value"] == (None if printed == "" else float(printed))
        assert cell["ocr_suspect"] is True


def _monotonicity_reversals(records):
    reversals = set()
    for record in records:
        rows = [row for row in record.get("rows", []) if row.get("kind") == "data"]
        for left, right in zip(rows, rows[1:]):
            for field in ("temperature", "H_minus_H298", "entropy"):
                left_cell = left.get(field) or {}
                right_cell = right.get(field) or {}
                if (
                    left_cell.get("value") is not None
                    and right_cell.get("value") is not None
                    and right_cell["value"] < left_cell["value"]
                ):
                    reversals.add(
                        (
                            record["record_id"],
                            record["pdf_page"],
                            field,
                            left_cell["as_published"],
                            right_cell["as_published"],
                        )
                    )
    return reversals


def test_image_verified_ocr_sweep_and_mutation_probe(corpus):
    _, records = corpus
    _assert_image_verified_ocr_sweep(records)
    corrupted = copy.deepcopy(records)
    record = next(item for item in corrupted if item["record_id"] == "b1259-ht-0055-methane-ideal-gas")
    row = next(
        item
        for item in record["rows"]
        if item.get("kind") == "data" and item["temperature"]["value"] == 500
    )
    row["H_minus_H298"]["value"] = 960.0
    with pytest.raises(AssertionError):
        _assert_image_verified_ocr_sweep(corrupted)


def test_monotonicity_detector_matches_image_verified_allowlist(corpus):
    _, records = corpus
    assert _monotonicity_reversals(records) == set(MONOTONICITY_REVERSAL_ALLOWLIST)
    record_id, page, field, left, right = IMAGE_VERIFIED_NONMONOTONE_GIBBS
    record = next(item for item in records if item["record_id"] == record_id)
    assert record["pdf_page"] == page
    tokens = [row[field]["as_published"] for row in record["rows"] if row.get("kind") == "data"]
    assert any(pair == (left, right) for pair in zip(tokens, tokens[1:]))


def test_monotonicity_detector_catches_non_allowlisted_reversal(corpus):
    _, records = corpus
    mutated = copy.deepcopy(
        next(record for record in records if record["record_id"] == "b1259-ht-0001-silver-reference-state")
    )
    row = next(item for item in mutated["rows"] if (item.get("temperature") or {}).get("value") == 500)
    row["H_minus_H298"]["value"] = -1.0
    assert _monotonicity_reversals([mutated]) - set(MONOTONICITY_REVERSAL_ALLOWLIST)


def test_public_loaders_refuse_bare_ocr_suspect_rows(corpus):
    public_functions = {
        name
        for name in loader.__all__
        if inspect.isfunction(getattr(loader, name, None))
    }
    assert public_functions == {
        "load_manifest",
        "load_records",
        "lookup",
        "numeric_token",
        "validate_record_source_tokens",
    }
    assert all("rows" not in entry for entry in load_manifest()["entries"])
    with pytest.raises(OCRSuspectRow) as excinfo:
        list(load_records())
    assert excinfo.value.record_id == "b1259-table-02-atomic-weights"
    assert excinfo.value.suspect_cells

    _, records = corpus
    methane = next(record for record in records if record["record_id"] == "b1259-ht-0055-methane-ideal-gas")
    with pytest.raises(OCRSuspectRow) as excinfo:
        lookup(methane, 500)
    assert excinfo.value.record_id == methane["record_id"]
    assert any(cell["ocr_token"] == "960" for cell in excinfo.value.suspect_cells)
    assert lookup(methane, 500, include_ocr_suspect=True)[0]["H_minus_H298"]["value"] == 1.96


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
            row = lookup(record, temperature, include_ocr_suspect=True)[0]
            assert row["delta_f_H"]["as_published"].startswith("-")
            assert row["delta_f_G"]["as_published"].startswith("-")


def test_lookup_uses_only_source_proven_temperature_grid(corpus):
    _, records = corpus
    silver = next(record for record in records if record["record_id"].startswith("b1259-ht-0001"))
    assert lookup(silver, 400, include_ocr_suspect=True)[0]["H_minus_H298"]["as_published"] == "0.625"
    assert len(lookup(silver, 1234, include_ocr_suspect=True)) == 2
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not a printed grid point"):
        lookup(silver, 350)
    wurtzite = next(record for record in records if record["record_id"].startswith("b1259-ht-0067"))
    assert len(lookup(wurtzite, 600, include_ocr_suspect=True)) == len(
        lookup(wurtzite, 700, include_ocr_suspect=True)
    ) == 1
    tridymite = next(record for record in records if record["record_id"].startswith("b1259-ht-0114"))
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not a printed grid point"):
        lookup(tridymite, 298)
    assert len(lookup(tridymite, 298.15, include_ocr_suspect=True)) == len(
        lookup(tridymite, 2000, include_ocr_suspect=True)
    ) == 1
    hydroxylapatite = next(
        record for record in records if record["record_id"].startswith("b1259-ht-0175")
    )
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not a printed grid point"):
        lookup(hydroxylapatite, 57.355)
    assert len(lookup(hydroxylapatite, 400, include_ocr_suspect=True)) == len(
        lookup(hydroxylapatite, 1500, include_ocr_suspect=True)
    ) == 1
    untranscribed = next(record for record in records if record.get("untranscribed"))
    with pytest.raises(TemperatureNotOnPrintedGrid, match="untranscribed"):
        lookup(untranscribed, 298.15)
    ammonia = next(record for record in records if record["record_id"].startswith("b1259-ht-0054"))
    assert lookup(ammonia, 298.15, include_ocr_suspect=True)[0]["temperature"]["layout_as_extracted"] == "298. 15"
    assert lookup(ammonia, 600, include_ocr_suspect=True)[0]["temperature"]["layout_as_extracted"] == "AGO"
    bromine = next(record for record in records if record["record_id"].startswith("b1259-ht-0009"))
    assert {
        row["H_minus_H298"]["as_published"]
        for row in lookup(bromine, 332.62, include_ocr_suspect=True)
    } == {".623", "7.687"}
    assert lookup(bromine, 400, include_ocr_suspect=True)[0]["H_minus_H298"]["as_published"] == "8.273"
    assert lookup(bromine, 500, include_ocr_suspect=True)[0]["H_minus_H298"]["as_published"] == "9.156"
    remaining_refusals = [
        (record["record_id"], record.get("grid_refusals"))
        for record in records
        if record.get("table_kind") == "high_temperature"
        and not record.get("untranscribed")
        and record.get("grid_refusals")
    ]
    assert remaining_refusals == []


@pytest.mark.parametrize("token", ["10.2CO", "13^750", "footnote a", "1,234", "7. 78"])
def test_ambiguous_numbers_are_never_guessed(token):
    with pytest.raises(ValueError, match="non-numeric"):
        numeric_token(token)
