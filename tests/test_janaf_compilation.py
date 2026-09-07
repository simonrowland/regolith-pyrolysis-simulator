"""NIST-JANAF compilation ingest — round-trip numbers and feedstock coverage.

Compilations are engine reference functions, not measurements. These tests
pin that every stored number in all 1655 tables re-parses from its
``as_published`` token, and that the 26 JANAF-absent feedstock elements
stay explicitly uncovered.
"""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import hashlib
import json
import os

import pytest
import yaml

from simulator.reference_data.janaf import (
    COMPILATION_ROOT,
    NON_STOICHIOMETRIC_TABLES,
    PINNED_JANAF_ABSENT_ELEMENTS,
    TABLES_DIR,
    coverage_by_element,
    feedstock_element_symbols,
    formula_composition,
    formula_normalised,
    harvest_era,
    iter_table_paths,
    load_manifest,
    load_table_document,
    parse_janaf_txt,
    round_trip_failures,
    table_formula_as_published,
)
from tools import harvest_janaf_compilation as harvester
from tools import build_janaf_compilation_manifest as manifest_builder
from tools.harvest_janaf_compilation import parse_table, parse_element_index

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data" / "literature" / "extracts" / "janaf-4th.yaml"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
JANAF_SOURCE_DIR = CORPUS_ROOT / "raw" / "janaf-nist-txt"
LIVE_TXT_SAMPLE = (
    "Aluminum, Ion (Al+)\tAl1+(g)\n"
    "T(K)\tCp\tS\t-[G-H(Tr)]/T\tH-H(Tr)\tdelta-f H\tdelta-f G\tlog Kf\n"
    "0\t0.\t0.\tINFINITE\t-4.539\t0.\t0.\t0.\n"
    "298.15\t20.786\t154.846\t154.846\t0.\t905.814\t888.384\t-155.656\n"
    "933.450\t20.786\t177.123\t160.111\t13.211\tCRYSTAL <--> LIQUID\n"
)


def test_harvester_parses_live_t_k_header() -> None:
    """Null hypothesis: committed parse_table still requires the HTML-era T/K label."""

    parsed = parse_janaf_txt(
        LIVE_TXT_SAMPLE,
        table_id="Al-006",
        url="https://janaf.nist.gov/tables/Al-006.html",
        download_url="https://janaf.nist.gov/tables/Al-006.txt",
    )
    assert parsed.header_as_published.startswith("T(K)")
    assert len(parsed.values) == 2
    assert parsed.values[0]["heat_capacity"]["as_published"] == "0."
    assert parsed.values[0]["heat_capacity"]["value"] == 0.0
    assert parsed.values[0]["negative_gibbs_enthalpy_function"]["as_published"] == "INFINITE"
    assert parsed.values[0]["negative_gibbs_enthalpy_function"]["value"] is None
    assert parsed.values[1]["temperature"]["as_published"] == "298.15"
    assert parsed.values[1]["entropy"]["value"] == 154.846
    assert len(parsed.parse_ambiguities) == 1
    assert "found 6" in parsed.parse_ambiguities[0]["reason"]

    wrapped = parse_table(
        LIVE_TXT_SAMPLE.encode("utf-8"),
        {
            "table_id": "Al-006",
            "url": "https://janaf.nist.gov/tables/Al-006.html",
            "download_url": "https://janaf.nist.gov/tables/Al-006.txt",
            "formula_label": "Al+",
        },
        COMPILATION_ROOT / "source-cache" / "tables" / "Al-006.txt",
    )
    index = wrapped["table"]["index_entry"]
    assert index["formula"] == "Al+"
    assert index["formula_as_published"] == "Al+"
    assert index["formula_normalised"] == "Al"


def test_every_stored_number_reparses_from_table_text() -> None:
    """Null hypothesis: a harvested coefficient can drift from its published token."""

    paths = list(iter_table_paths())
    assert len(paths) == 1655
    failures: list[str] = []
    html_era = 0
    txt_era = 0
    for path in paths:
        document = load_table_document(path)
        era = harvest_era(document)
        if era == "html":
            html_era += 1
        elif era == "txt":
            txt_era += 1
        failures.extend(round_trip_failures(document))
        if len(failures) > 20:
            break
    assert html_era + txt_era == 1655
    assert failures == []


def _assert_source_round_trip(document, source_path):
    table = document["table"]
    fresh = parse_table(source_path.read_bytes(), table, source_path)["table"]
    assert fresh["values"], "parser returned no source rows"
    for key in ("formula_as_published", "formula_normalised", "charge"):
        assert fresh["index_entry"][key] == table["index_entry"][key], key
    keys = tuple(fresh["values"][0])
    actual = [tuple(row[key]["as_published"] for key in keys) for row in table["values"]]
    expected = [tuple(row[key]["as_published"] for key in keys) for row in fresh["values"]]
    assert actual == expected, f"{table['table_id']}: source-token/row mismatch (stale harvest)"
    assert round_trip_failures(document) == []


@pytest.fixture(scope="module")
def janaf_source_dir() -> Path:
    if not JANAF_SOURCE_DIR.is_dir():
        pytest.skip(
            f"janaf_source_directory_absent: {JANAF_SOURCE_DIR}; "
            "source fidelity NOT VERIFIED"
        )
    return JANAF_SOURCE_DIR


@pytest.mark.parametrize("path", list(iter_table_paths()), ids=lambda path: path.stem)
def test_source_txt_round_trip(path, manifest_entries, janaf_source_dir):
    document = load_table_document(path)
    extraction = document["extraction"]
    source_path = janaf_source_dir / f"{path.stem}.txt"
    assert source_path.is_file(), f"corpus source missing: {source_path}"
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == extraction["source_sha256"]
    _assert_source_round_trip(document, source_path)
    manifest_entry = manifest_entries[path.stem]
    assert manifest_entry["row_count"] == len(document["table"]["values"])


@pytest.fixture(scope="module")
def manifest_entries():
    return {entry["table_id"]: entry for entry in load_manifest()["entries"]}


def test_source_round_trip_negative_controls(tmp_path, monkeypatch):
    source_path = tmp_path / "Al-006.txt"
    source_path.write_text(LIVE_TXT_SAMPLE)
    entry = {"table_id": "Al-006", "url": "https://janaf.nist.gov/tables/Al-006.html",
             "download_url": "https://janaf.nist.gov/tables/Al-006.txt"}
    document = parse_table(source_path.read_bytes(), entry, source_path)
    _assert_source_round_trip(document, source_path)
    stale = deepcopy(document)
    stale["table"]["values"][1]["heat_capacity"].update(as_published="999.999", value=999.999)
    with pytest.raises(AssertionError, match="stale harvest"):
        _assert_source_round_trip(stale, source_path)
    empty = deepcopy(document)
    empty["table"]["values"] = []
    with pytest.raises(AssertionError, match="stale harvest"):
        _assert_source_round_trip(empty, source_path)
    source_path.write_text(LIVE_TXT_SAMPLE.replace("20.786", "999.999", 1))
    with pytest.raises(AssertionError, match="stale harvest"):
        _assert_source_round_trip(document, source_path)
    def broken_parser(*args, **kwargs):
        raise RuntimeError("broken source parser")
    monkeypatch.setattr(harvester, "parse_janaf_txt", broken_parser)
    with pytest.raises(RuntimeError, match="broken source parser"):
        _assert_source_round_trip(document, source_path)


def test_native_blank_tail_cells_survive():
    source = LIVE_TXT_SAMPLE + "200\t20.786\t141.651\t151.852\t-2.040\t\t\t\t\t\t\n"
    parsed = parse_janaf_txt(source, table_id="Al-006", url="u", download_url="d")
    row = parsed.values[-1]
    assert len(parsed.values) == 3
    assert row["temperature"]["as_published"] == "200"
    for key in ("formation_enthalpy", "formation_gibbs_energy", "log10_formation_equilibrium_constant"):
        assert row[key]["as_published"] == ""
        assert row[key]["value"] is None


def test_element_index_never_supplies_formula(tmp_path):
    entries = parse_element_index(b'<a href="Al-014.html">Aluminum chloride</a>', "Al")
    source = LIVE_TXT_SAMPLE.replace("Aluminum, Ion (Al+)\tAl1+(g)", "Aluminum Chloride (AlCl)\tAl1Cl1(g)")
    assert len(entries) == 1
    entries[0]["formula_label"] = "Al"
    parsed = parse_table(source.encode(), entries[0], tmp_path / "Al-014.txt")
    assert parsed["table"]["index_entry"]["formula_as_published"] == "AlCl"
    invalid = source.replace("Aluminum Chloride (AlCl)\tAl1Cl1(g)", "Unidentified table")
    with pytest.raises(ValueError, match="unparseable printed header formula"):
        parse_table(invalid.encode(), entries[0], tmp_path / "Al-014.txt")


ION_CASES = """
Al-022:AlCl2+ Al-023:AlCl2- Al-037:AlF2+ Al-038:AlF2- Al-040:OAlF2-
Al-045:AlF4- Al-078:AlO2- Al-095:Al2O2+ B-027:BCl2+ B-028:BCl2-
B-035:BF2+ B-036:BF2- B-080:BO2- Br-061:MgBr2+ C-035:CF2+
C-038:CF3+ C-096:CO2- C-114:C2- Cl-112:PbCl2+ Cl-115:SCl2+
F-068:KF2- F-070:LiF2- F-076:MgF2+ F-081:NaF2- F-089:PF2+
F-090:PF2- F-097:SF2+ F-098:SF2- F-122:SF3+ F-123:SF3-
F-139:SF4+ F-140:SF4- F-150:SF5+ F-151:SF5- F-155:SF6-
H-051:H2+ H-052:H2- N-024:N2+ N-025:N2- O-030:O2+ O-031:O2-
""".split()


@pytest.mark.parametrize("case", ION_CASES)
def test_all_41_molecular_ions_keep_atom_counts(case, manifest_entries):
    table_id, published = case.split(":")
    index = load_table_document(TABLES_DIR / f"{table_id}.yaml")["table"]["index_entry"]
    assert index["formula_as_published"] == published
    assert index["formula_normalised"] == published[:-1]
    assert index["charge"] == (1 if published[-1] == "+" else -1)
    assert formula_composition(published) == formula_composition(published[:-1])
    assert manifest_entries[table_id]["charge"] == index["charge"]
    assert manifest_entries[table_id]["formula_normalised"] == published[:-1]


def test_mutated_real_source_token_fails(tmp_path, janaf_source_dir):
    document = load_table_document(TABLES_DIR / "Al-006.yaml")
    source_path = janaf_source_dir / "Al-006.txt"
    assert source_path.is_file(), f"corpus source missing: {source_path}"
    _assert_source_round_trip(document, source_path)
    original = source_path.read_text()
    assert "20.786" in original
    scratch = tmp_path / "Al-006.txt"
    scratch.write_text(original.replace("20.786", "999.999", 1))
    with pytest.raises(AssertionError, match="stale harvest"):
        _assert_source_round_trip(document, scratch)


def test_header_refusal_is_listed_in_manifest(tmp_path, monkeypatch):
    tables = tmp_path / "tables"
    tables.mkdir()
    document = load_table_document(TABLES_DIR / "Al-006.yaml")
    (tables / "Al-006.yaml").write_text(json.dumps(document))
    cache = tmp_path / "source-cache"
    cache.mkdir()
    (cache / "harvest-run.yaml").write_text(yaml.safe_dump({"entries": [{
        "table_id": "Al-014", "status": "failed",
        "error": "Al-014: unparseable printed header formula ''",
    }]}))
    monkeypatch.setattr(manifest_builder, "ROOT", tmp_path)
    monkeypatch.setattr(manifest_builder, "JANAF_ROOT", tmp_path)
    monkeypatch.setattr(manifest_builder, "TABLES", tables)
    monkeypatch.setattr(manifest_builder, "MANIFEST", tmp_path / "manifest.yaml")
    assert manifest_builder.main() == 0
    manifest = load_manifest(tmp_path / "manifest.yaml")
    assert manifest["parse_ambiguities"] == [{
        "table_id": "Al-014", "reason": "Al-014: unparseable printed header formula ''",
    }]
    assert manifest["summary"]["parse_ambiguity_count"] == len(document["table"]["parse_ambiguities"]) + 1


def test_hydrate_identity_uses_printed_composition_token(tmp_path):
    source = LIVE_TXT_SAMPLE.replace("Aluminum, Ion (Al+)\tAl1+(g)",
        "Sulfuric Acid, Dihydrate (H2SO4.2H2O)\tH6O6S1(cr,l)")
    entry = {"table_id": "H-094", "url": "u", "download_url": "d", "formula_label": "H"}
    index = parse_table(source.encode(), entry, tmp_path / "H-094.txt")["table"]["index_entry"]
    assert index["formula_as_published"] == "H6O6S1"
    assert formula_composition(index["formula_as_published"]) == (("H", 6.0), ("O", 6.0), ("S", 1.0))


def test_non_stoichiometric_formulas_are_not_rewritten() -> None:
    manifest = load_manifest()
    listed = {row["table_id"]: row for row in manifest["non_stoichiometric_formulas"]}
    assert set(listed) == set(NON_STOICHIOMETRIC_TABLES)
    for table_id, meta in NON_STOICHIOMETRIC_TABLES.items():
        document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
        table = document["table"]
        index = table["index_entry"]
        published = meta["formula_as_published"]
        assert index["formula"] == published
        assert index["formula_as_published"] == published
        assert index["formula_normalised"] == formula_normalised(published)
        assert "." in published
        kinds = [item.get("kind") for item in table.get("parse_ambiguities") or []]
        assert "non_stoichiometric_formula_not_rewritten" in kinds
        # Integerised form must not be the stored formula, and must not
        # share composition with the published non-stoichiometric formula.
        integerised = meta["previous_integerised_formula"]
        assert formula_composition(published) != formula_composition(integerised)
        assert listed[table_id]["formula_as_published"] == published
    # Wüstite must not be counted as stoichiometric FeO.
    feo_ids = manifest["target_coverage"]["FeO"]["table_ids"]
    assert "Fe-001" not in feo_ids
    assert "Fe-018" in feo_ids


def test_feedstock_element_coverage_pins_26_janaf_absent_elements() -> None:
    elements = feedstock_element_symbols()
    assert "Si" in elements and "Fe" in elements and "O" in elements
    for absent in PINNED_JANAF_ABSENT_ELEMENTS:
        assert absent in elements, absent
    documents = [load_table_document(path) for path in iter_table_paths()]
    table = coverage_by_element(documents, elements)
    missing = tuple(
        sorted(element for element, row in table.items() if not row["has_record"])
    )
    for absent in PINNED_JANAF_ABSENT_ELEMENTS:
        assert table[absent]["has_record"] is False
        assert table[absent]["table_count"] == 0
        assert absent in missing
    for element in ("Si", "Fe", "O", "Al", "Mg", "Ca", "Ti", "Na", "K", "P", "S"):
        assert table[element]["has_record"], element
        assert table[element]["table_count"] >= 1
    manifest = load_manifest()
    assert list(PINNED_JANAF_ABSENT_ELEMENTS) == manifest["feedstock_element_coverage"][
        "janaf_absent_elements"
    ]


def test_compilation_refuses_validation_and_scoring() -> None:
    manifest = load_manifest()
    role = manifest["compilation_role"]
    assert role["engine_reference_input"] is True
    assert role["validation_measurement"] is False
    assert role["scoring_eligible"] is False
    assert role["battery_refusal"] == "gibbs_table_not_runtime_observable"
    sample = load_table_document(TABLES_DIR / "Fe-001.yaml")
    assert sample["compilation_role"]["scoring_eligible"] is False
    assert sample["compilation_role"]["validation_measurement"] is False
    text = (TABLES_DIR / "Fe-001.yaml").read_text(encoding="utf-8")
    assert "typed: measured" not in text
    assert "provenance_class: measured" not in text


def test_legacy_extract_left_in_place() -> None:
    assert EXTRACT.is_file()
    payload = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert payload["source_id"] == "janaf-4th"
    assert payload["schema_version"] == "literature_extract.v1"


def test_every_manifest_entry_carries_formula_as_published() -> None:
    manifest = load_manifest()
    entries = manifest["entries"]
    assert len(entries) == 1655
    files = {path.name for path in iter_table_paths()}
    for entry in entries:
        assert entry["formula"] == entry["formula_as_published"]
        assert entry["formula_as_published"]
        assert entry["formula_normalised"]
        assert f"{entry['table_id']}.yaml" in files
    for table_id in ("Al-001", "Al-006", "Fe-001", "B-001"):
        document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
        index = document["table"]["index_entry"]
        assert index["formula_as_published"]
        assert index["formula"] == index["formula_as_published"]
        assert table_formula_as_published(document["table"]) == index["formula_as_published"]
