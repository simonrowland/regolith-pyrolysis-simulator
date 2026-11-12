"""Lossless checks against the original ANL snapshot, not simulator predictions."""

import hashlib
import html
import re
from decimal import Decimal

import pytest
import yaml

from tools.harvest_atct_compilation import (
    ATCT_ROOT, ROLE, SOURCE_NAME, feedstock_coverage, load_records, numeric_token,
    parse_source,
)
from tests.helpers.named_manifests import assert_named_rows


@pytest.fixture(scope="module")
def corpus():
    manifest = yaml.safe_load((ATCT_ROOT / "manifest.yaml").read_text())
    return manifest, (ATCT_ROOT / "source" / SOURCE_NAME).read_bytes()


def published(fragment):
    return html.unescape(re.sub(r"<[^>]*>", "", fragment)).strip()


def test_every_source_row_round_trips(corpus):
    manifest, payload = corpus
    original = payload.decode("utf-8")
    parsed_records = parse_source(payload)
    actual_ids = [record["record_id"] for record in parsed_records]
    expected_ids = [entry["record_id"] for entry in manifest["entries"]]
    assert len(actual_ids) == len(set(actual_ids)), "ATcT source has duplicate record IDs"
    assert len(expected_ids) == len(set(expected_ids)), "ATcT manifest has duplicate record IDs"
    assert_named_rows(
        {record_id: None for record_id in actual_ids},
        {record_id: None for record_id in expected_ids},
        label="ATcT source record IDs",
    )
    records = list(load_records())
    # Count the value cells independently of the harvester's row selector.
    assert original.count('<td class="bkgDHf298">') == len(records) == 3442
    assert len(manifest["entries"]) == len(records)
    assert len(list((ATCT_ROOT / "records").glob("*.json"))) == len(records)
    assert records == parsed_records
    for entry, record in zip(manifest["entries"], records, strict=True):
        locator = record["source_locator"]
        row = payload[locator["byte_start"]:locator["byte_end"]].decode("utf-8")
        assert row.startswith('<tr id="' + locator["html_row_id"] + '"')
        assert entry["source_locator"] == locator
        assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
        assert entry["row_count"] == 1
        assert entry["source"] == manifest["source"]
        for field in ("record_id", "formula", "phase", "name_as_published", "ambiguities"):
            assert entry[field] == record[field]
        assert entry["ambiguity_count"] == len(record["ambiguities"])
        for column, field in (
            ("DHf0", "formation_enthalpy_0_K_as_published"),
            ("DHf298", "formation_enthalpy_298_15_K_as_published"),
            ("Uncert", "uncertainty_as_published"),
            ("Units", "units_as_published"),
            ("Mass", "relative_molecular_mass_as_published"),
            ("ATcTID", "atct_id"),
        ):
            token = published(re.search(r'<span class="' + column + r'">(.*?)</span>', row, re.S)[1])
            assert record[field] == token
            if column in ("DHf0", "DHf298", "Uncert"):
                expected = None if token in ("", "exact") else Decimal(token.replace("±", "").strip())
                assert numeric_token(record[field]) == expected
        label = published(re.search(r"<button[^>]*>(.*?)</button>", row, re.S)[1])
        assert record["formula_as_published"] == label
        assert record["name_as_published"] == published(re.search(r'<span class="Name">(.*?)</span>', row, re.S)[1])
        assert record["cas_as_published"] == re.search(r'\bCAS([^" ]+)', row)[1]
        assert record["version"] == "1.222"
        assert record["compilation_role"] == ROLE
    for source in manifest["source_files"]:
        assert hashlib.sha256((ATCT_ROOT / source["path"]).read_bytes()).hexdigest() == source["sha256"]


def test_source_gaps_and_native_states_are_explicit(corpus):
    manifest, _ = corpus
    records = list(load_records())
    assert manifest["corpus_status"]["claimed_species_count"] == 3444
    assert manifest["corpus_status"]["ambiguities"] == [
        "page claims 3444 species; supplied HTML contains 3442 species rows"
    ]
    assert sum(r["formation_enthalpy_0_K_as_published"] == "" for r in records) == 590
    assert sum(r["units_as_published"] == "" for r in records) == 20
    for record in records:
        assert "malformed_formula_cell_attribute; button text retained verbatim" in record["ambiguities"]
        if not record["formation_enthalpy_0_K_as_published"]:
            assert "DHf0_blank_as_published; not inferred" in record["ambiguities"]
    assert any(r["phase"] == "cr,l" for r in records)
    assert any(r["phase"].startswith("aq,") for r in records)
    assert any(r["phase"].startswith("ad,") for r in records)
    assert any(r["formula"] == "Ar-" for r in records)
    assert any(r["formula"] == "D2" for r in records)
    by_id = {r["record_id"]: r for r in records}
    for record_id in ("atct-1.222-2243", "atct-1.222-0679", "atct-1.222-2582"):
        assert "published_isomer_or_state_label_retained; not canonicalized or split" in by_id[record_id]["ambiguities"]
    assert manifest["summary"]["record_ambiguity_count"] == sum(len(r["ambiguities"]) for r in records)


def test_feedstock_element_coverage(corpus):
    manifest, _ = corpus
    records = list(load_records())
    coverage = feedstock_coverage(records)
    assert set(coverage) == set("Ag Al As Au B Ba Bi Br C Ca Cd Ce Cl Co Cr Cs Cu Dy Er Eu F Fe Ga Gd Ge H Hf Ho I In Ir K La Li Lu Mg Mn Mo N Na Nb Nd Ni O Os P Pb Pr Pt Rb S Sb Sc Se Si Sm Sn Sr Tb Te Th Ti Tm U V W Y Yb Zn Zr".split())
    assert {element for element, count in coverage.items() if count} == set("B Br C Cl F H I N O Pt S Si".split())
    assert coverage == manifest["feedstock_element_coverage"]


@pytest.mark.parametrize("token", ["footnote a", "1.2?", "±", "1,234"])
def test_ambiguous_numbers_are_never_guessed(token):
    with pytest.raises(ValueError, match="non-numeric"):
        numeric_token(token)
