"""NIST-JANAF compilation ingest — round-trip numbers and feedstock coverage.

Compilations are engine reference functions, not measurements. These tests
pin that every stored number in all 1655 tables re-parses from its
``as_published`` token, and that the 26 JANAF-absent feedstock elements
stay explicitly uncovered.
"""

from __future__ import annotations

from pathlib import Path

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
from tools.harvest_janaf_compilation import parse_table

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data" / "literature" / "extracts" / "janaf-4th.yaml"
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
    assert html_era == 380
    assert txt_era == 1275
    assert failures == []


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
