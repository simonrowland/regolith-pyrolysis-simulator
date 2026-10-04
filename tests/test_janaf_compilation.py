"""NIST-JANAF compilation ingest — round-trip numbers and feedstock coverage.

Compilations are engine reference functions, not measurements. These tests
pin that every stored number in all 1655 tables re-parses from its
``as_published`` token, and that the 26 JANAF-absent feedstock elements
stay explicitly uncovered.
"""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import os

import pytest
import yaml

from simulator.yaml_cache import load_cached_safe_yaml

from simulator.reference_data.janaf import (
    COMPILATION_ROOT,
    NON_STOICHIOMETRIC_TABLES,
    NIST_TAIL_PARSE_REPAIR,
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

EXPECTED_TRANSITION_REFUSALS = {
    "Al-002": (1000,),
    "Al-010": (400,),
    "Al-011": (400,),
    "Al-025": (500,),
    "Al-026": (500,),
    "Al-041": (2600,),
    "Al-042": (2600,),
    "Al-049": (1100,),
    "Al-050": (1100,),
    "Al-052": (900,),
    "Al-053": (900, 1300),
    "Al-054": (900, 1300),
    "Al-064": (500,),
    "Al-065": (500,),
    "Al-068": (2000,),
    "Al-069": (2000,),
    "Al-089": (2500,),
    "Al-090": (2500,),
    "Al-096": (2400,),
    "Al-100": (2400,),
    "Al-106": (1100,),
    "Al-107": (1100,),
    "B-002": (2400,),
    "B-041": (900,),
    "B-042": (900,),
    "B-064": (1300,),
    "B-065": (1300,),
    "B-068": (1200,),
    "B-069": (1200,),
    "B-074": (1300,),
    "B-075": (1300,),
    "B-095": (800,),
    "B-096": (800,),
    "B-100": (3200,),
    "B-101": (3200,),
    "B-103": (3400,),
    "B-104": (3400,),
    "B-115": (1100,),
    "B-116": (1100,),
    "B-118": (1200,),
    "B-119": (1200,),
    "B-122": (1100,),
    "B-132": (1200,),
    "B-133": (1200,),
    "B-136": (400,),
    "B-137": (400,),
    "Ba-002": (1100,),
    "Ba-008": (1200,),
    "Ba-009": (1200,),
    "Ba-013": (1300,),
    "Ba-014": (1300,),
    "Ba-019": (1700,),
    "Ba-020": (1700,),
    "Ba-025": (700,),
    "Ba-026": (700,),
    "Ba-030": (1000,),
    "Ba-031": (1000,),
    "Ba-034": (2300,),
    "Ba-035": (2300,),
    "Br-015": (1100,),
    "Br-019": (900,),
    "Br-027": (1100,),
    "Br-041": (1100,),
    "Br-045": (1000,),
    "Br-070": (1000,),
    "Br-076": (1000,),
    "Br-098": (400,),
    "Br-104": (600,),
    "Br-105": (600,),
    "Br-108": (600,),
    "C-008": (2800,),
    "C-009": (2800,),
    "C-069": (900,),
    "C-070": (900,),
    "C-073": (1200,),
    "C-074": (1200,),
    "C-076": (1000,),
    "C-077": (1000,),
    "C-083": (Decimal("298.15"), 900),
    "C-084": (900,),
    "C-085": (Decimal("298.15"),),
    "C-090": (1200,),
    "C-091": (1200,),
    "C-104": (4300,),
    "C-105": (4300,),
    "C-107": (3300,),
    "C-108": (3300,),
    "C-110": (3900,),
    "C-111": (3900,),
    "Ca-002": (800,),
    "Ca-003": (1200,),
    "Ca-009": (1100,),
    "Ca-010": (1100,),
    "Ca-014": (1700,),
    "Ca-015": (1700,),
    "Ca-023": (1100,),
    "Ca-024": (1100,),
    "Ca-027": (3300,),
    "Ca-028": (3300,),
    "Cl-005": (1000,),
    "Cl-006": (1000,),
    "Cl-009": (800,),
    "Cl-010": (800,),
    "Cl-032": (400,),
    "Cl-033": (400,),
    "Cl-036": (1100,),
    "Cl-037": (1100,),
    "Cl-041": (900,),
    "Cl-042": (900,),
    "Cl-046": (600,),
    "Cl-047": (600,),
    "Cl-053": (1100,),
    "Cl-054": (1100,),
    "Cl-074": (1100,),
    "Cl-075": (1100,),
    "Cl-081": (1000,),
    "Cl-082": (1000,),
    "Cl-093": (1000,),
    "Cl-094": (1000,),
    "Cl-099": (1400,),
    "Cl-100": (1400,),
    "Cl-108": (800,),
    "Cl-109": (800,),
    "Cl-119": (1200,),
    "Cl-120": (1200,),
    "Cl-127": (1100,),
    "Cl-128": (1100,),
    "Cl-134": (600,),
    "Cl-135": (600,),
    "Cl-151": (600,),
    "Cl-152": (600,),
    "Cl-155": (500,),
    "Cl-156": (500,),
    "Cl-161": (Decimal("298.15"),),
    "Cl-162": (Decimal("298.15"),),
    "Cl-169": (500,),
    "Cl-170": (500,),
    "Cl-173": (500,),
    "Cl-174": (500,),
    "Cl-178": (500,),
    "Cl-179": (500,),
    "Cl-182": (600,),
    "Cl-183": (600,),
    "Cl-189": (500, 600),
    "Cl-191": (600,),
    "Cl-192": (500,),
    "Co-002": (1800,),
    "Co-008": (1500,),
    "Co-009": (1500,),
    "Cr-002": (2200,),
    "Cr-014": (2700,),
    "Cr-015": (2700,),
    "Cs-002": (350,),
    "Cs-008": (1000,),
    "Cs-009": (1000,),
    "Cs-012": (600,),
    "Cs-013": (600,),
    "Cs-022": (1000, 1300),
    "Cs-023": (1000,),
    "Cs-024": (1300,),
    "Cu-002": (1400,),
    "Cu-010": (1200,),
    "Cu-011": (1200,),
    "Cu-019": (1600,),
    "Cu-020": (1600,),
    "F-017": (1200,),
    "F-018": (1200,),
    "F-021": (1200,),
    "F-022": (1200,),
    "F-033": (1300,),
    "F-034": (1300,),
    "F-055": (1400,),
    "F-056": (1400,),
    "F-059": (600,),
    "F-060": (600,),
    "F-072": (1600,),
    "F-073": (1600,),
    "F-091": (600,),
    "F-092": (600, 1200),
    "F-093": (1200,),
    "F-102": (1800,),
    "F-103": (1800,),
    "F-107": (1200,),
    "F-108": (1200,),
    "F-133": (400,),
    "F-134": (400,),
    "Fe-004": (1200, 1700, 1900),
    "Fe-005": (1700,),
    "Fe-014": (900,),
    "Fe-015": (900,),
    "Fe-018": (1700,),
    "Fe-019": (1700,),
    "Fe-023": (1500,),
    "Fe-024": (1500,),
    "Ga-002": (350,),
    "H-009": (700,),
    "H-010": (700,),
    "H-014": (1000,),
    "H-015": (1000,),
    "H-018": (800,),
    "H-019": (800,),
    "H-033": (600,),
    "H-034": (600,),
    "H-063": (380,),
    "H-071": (800,),
    "H-072": (800,),
    "H-085": (400,),
    "H-086": (400,),
    "Hf-002": (2100,),
    "Hf-003": (2600,),
    "I-004": (1000,),
    "I-005": (1000,),
    "I-009": (800,),
    "I-015": (1000,),
    "I-016": (1000,),
    "I-024": (400,),
    "I-030": (1000,),
    "I-031": (1000,),
    "I-036": (700,),
    "I-037": (700,),
    "I-041": (900,),
    "I-042": (900,),
    "I-047": (800,),
    "I-048": (800,),
    "I-061": (400,),
    "I-062": (400,),
    "I-065": (500,),
    "I-066": (500,),
    "K-002": (350,),
    "K-014": (1300,),
    "K-015": (1300,),
    "K-017": (900,),
    "K-018": (900, 1400),
    "K-019": (1400,),
    "K-022": (1300,),
    "K-023": (1300,),
    "Li-002": (500,),
    "Li-014": (1900,),
    "Li-015": (1900,),
    "Li-020": (1500,),
    "Li-021": (1500,),
    "Li-023": (1900,),
    "Li-024": (1900,),
    "Li-026": (900,),
    "Li-027": (900, 1200),
    "Li-028": (1200,),
    "Li-031": (1400,),
    "Li-032": (1400,),
    "Mg-002": (1000,),
    "Mg-008": (3200,),
    "Mg-009": (3200,),
    "Mg-012": (1900,),
    "Mg-013": (1900,),
    "Mg-015": (2000,),
    "Mg-016": (2000,),
    "Mg-018": (1500,),
    "Mg-019": (1500,),
    "Mg-022": (2000,),
    "Mg-023": (2000,),
    "Mg-028": (2200,),
    "Mg-029": (2200,),
    "Mg-031": (2100,),
    "Mg-032": (2100,),
    "Mg-034": (1400,),
    "Mg-035": (1400,),
    "Mg-038": (1700,),
    "Mg-039": (1700,),
    "Mn-002": (1600,),
    "Mo-002": (2900,),
    "Mo-014": (1100,),
    "Mo-015": (1100,),
    "Mo-019": (2100,),
    "Mo-020": (2100,),
    "N-014": (3300,),
    "N-015": (3300,),
    "N-019": (3300,),
    "N-020": (3300,),
    "N-029": (Decimal("298.15"),),
    "N-030": (Decimal("298.15"),),
    "Na-002": (400,),
    "Na-012": (1500,),
    "Na-013": (1500,),
    "Na-016": (1400,),
    "Na-017": (1400,),
    "Na-020": (600, 1200),
    "Na-022": (500, 600),
    "Na-023": (500,),
    "Na-024": (1200,),
    "Na-028": (1200,),
    "Na-029": (1200,),
    "Na-031": (1500,),
    "Na-032": (1500,),
    "Na-034": (800,),
    "Na-035": (800,),
    "Nb-002": (2800,),
    "Nb-008": (2300,),
    "Nb-009": (2300,),
    "Nb-012": (2200,),
    "Nb-013": (2200,),
    "Nb-016": (1800,),
    "Nb-017": (1800,),
    "Ni-002": (1800,),
    "Ni-008": (1300,),
    "Ni-009": (1300,),
    "Ni-012": (1300,),
    "Ni-013": (1300,),
    "Ni-015": (1100,),
    "Ni-016": (1100,),
    "O-005": (800,),
    "O-006": (800, 1200),
    "O-007": (1200,),
    "O-013": (3000,),
    "O-014": (3000,),
    "O-018": (1300,),
    "O-019": (1300, 2100),
    "O-020": (2100,),
    "O-023": (2100,),
    "O-024": (2100,),
    "O-037": (1700,),
    "O-038": (1700,),
    "O-043": (2200,),
    "O-044": (2200,),
    "O-052": (3000,),
    "O-053": (3000,),
    "O-059": (2200,),
    "O-060": (2200,),
    "O-062": (2400,),
    "O-063": (2400,),
    "O-065": (1800,),
    "O-066": (1800,),
    "O-073": (1900,),
    "O-074": (1900,),
    "O-077": (2100,),
    "O-078": (2100,),
    "O-082": (2100,),
    "O-083": (500,),
    "O-084": (1000,),
    "O-085": (1000,),
    "O-089": (2000,),
    "O-090": (2000,),
    "P-002": (350,),
    "P-014": (500,),
    "P-015": (500,),
    "Pb-002": (700,),
    "Pb-009": (1400,),
    "Rb-002": (350,),
    "S-002": (400,),
    "S-013": (1400,),
    "S-014": (1400,),
    "Si-002": (1700,),
    "Sr-002": (900,),
    "Sr-003": (1100,),
    "Ta-002": (3300,),
    "Ti-002": (1200,),
    "Ti-003": (2000,),
    "V-002": (2200,),
    "W-002": (3700,),
    "Zn-002": (700,),
    "Zr-002": (1200,),
    "Zr-003": (2200,),
}

EXPECTED_UNREPAIRED_TAILS = (
    ("B-123", 1100, "printed Gibbs/log Kf pair misses allowed tolerance"),
    *(
        (table_id, temperature, "formation enthalpy neighbors span a transition marker")
        for table_id, temperatures in EXPECTED_TRANSITION_REFUSALS.items()
        for temperature in temperatures
    ),
)

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


@pytest.mark.parametrize(
    ("table_id", "temperature", "expected"),
    (
        (
            "K-003",
            1100,
            {
                "formation_enthalpy": -78.951,
                "formation_gibbs_energy": 4.610,
                "log10_formation_equilibrium_constant": -0.219,
            },
        ),
        (
            "Al-003",
            1000,
            {
                "formation_enthalpy": 0.0,
                "formation_gibbs_energy": 0.0,
                "log10_formation_equilibrium_constant": 0.0,
            },
        ),
    ),
)
def test_transition_following_rows_restore_signed_janaf_tail(
    table_id: str, temperature: int, expected: dict[str, float]
) -> None:
    table = load_table_document(TABLES_DIR / f"{table_id}.yaml")["table"]
    rows = [
        row
        for row in table["values"]
        if row["temperature"]["value"] == temperature
    ]
    assert len(rows) == 1, f"{table_id}: missing {temperature} K row"
    row = rows[0]
    for key, value in expected.items():
        assert row[key]["value"] == value


@pytest.mark.parametrize(
    ("table_id", "temperature"),
    (("O-038", 1700), ("O-037", 1700), ("Fe-018", 1700), ("Na-012", 1500)),
)
def test_transition_following_enthalpy_bracket_is_named_refusal(
    table_id: str, temperature: int
) -> None:
    table = load_table_document(TABLES_DIR / f"{table_id}.yaml")["table"]
    assert not any(
        row["temperature"]["value"] == temperature for row in table["values"]
    )
    ambiguity = next(
        item
        for item in table["parse_ambiguities"]
        if item.get("raw_line", "").startswith(f"{temperature}\t")
    )
    assert ambiguity["kind"] == "nist_tail_whitespace_signs_unresolved"
    assert "formation enthalpy neighbors span a transition marker" in ambiguity["reason"]


def test_refused_janaf_row_keeps_unsigned_source_line() -> None:
    table = load_table_document(TABLES_DIR / "O-038.yaml")["table"]
    raw_line = (
        "1700\t85.772\t158.857\t102.619\t95.604\t"
        "941.610  609.059 18.714"
    )
    ambiguity = next(
        item for item in table["parse_ambiguities"] if item.get("raw_line", "").startswith("1700\t")
    )
    assert ambiguity["raw_line"] == raw_line
    assert "formation enthalpy neighbors span a transition marker" in ambiguity["reason"]


def test_unresolved_janaf_tail_identity_stays_in_parse_ambiguities() -> None:
    table = load_table_document(TABLES_DIR / "B-123.yaml")["table"]
    assert not any(row["temperature"]["value"] == 1100 for row in table["values"])
    ambiguity = next(
        item
        for item in table["parse_ambiguities"]
        if item.get("raw_line", "").startswith("1100\t")
    )
    assert ambiguity["kind"] == "nist_tail_whitespace_signs_unresolved"
    assert "identity misses by 1001.634 printed log Kf units" in ambiguity["reason"]


def test_unrepaired_janaf_tail_coordinates_match_the_named_exception_list() -> None:
    from decimal import Decimal

    actual = []
    for path in iter_table_paths():
        table = load_table_document(path)["table"]
        for ambiguity in table.get("parse_ambiguities", []):
            if ambiguity.get("kind") != "nist_tail_whitespace_signs_unresolved":
                continue
            raw_line = ambiguity.get("raw_line", "")
            temperature = Decimal(raw_line.split("\t", 1)[0])
            reason = (
                "printed Gibbs/log Kf pair misses allowed tolerance"
                if "identity misses by" in ambiguity.get("reason", "")
                else "formation enthalpy neighbors span a transition marker"
            )
            actual.append((table["table_id"], temperature, reason))
    assert sorted(actual) == sorted(EXPECTED_UNREPAIRED_TAILS)


@pytest.mark.parametrize(
    ("table_id", "temperature"),
    (
        ("Al-050", 1100),
        ("Al-052", 900),
        ("Al-053", 900),
        ("Al-106", 1100),
        ("Al-107", 1100),
        ("B-116", 1100),
        ("B-132", 1200),
        ("B-133", 1200),
        ("F-133", 400),
        ("F-134", 400),
        ("H-085", 400),
        ("H-086", 400),
        ("Na-022", 500),
        ("O-083", 500),
    ),
)
def test_large_logk_rounding_miss_refuses_transition_crossing_enthalpy(
    table_id: str, temperature: int
) -> None:
    table = load_table_document(TABLES_DIR / f"{table_id}.yaml")["table"]
    assert not any(
        row["temperature"]["value"] == temperature for row in table["values"]
    )
    ambiguity = next(
        item
        for item in table["parse_ambiguities"]
        if item.get("raw_line", "").startswith(f"{temperature}\t")
    )
    assert "formation enthalpy neighbors span a transition marker" in ambiguity["reason"]


def test_scale_aware_identity_tolerance_accepts_more_than_two_printed_units() -> None:
    source = (
        "Synthetic scale-aware identity tail\n"
        "T(K)\tCp\tS\t-[G-H(Tr)]/T\tH-H(Tr)\tΔfH\tΔfG\tlog Kf\n"
        "900\t10\t10\t10\t0\t-100.000\t1000.000\t-58.038\n"
        "1000\t10\t10\t10\t0\t100.000 1000.000 52.236\n"
        "1100\t10\t10\t10\t0\t-100.000\t1000.000\t-47.485\n"
    )
    parsed = parse_janaf_txt(
        source, table_id="synthetic-scale-aware-tail", url="u", download_url="d"
    )
    row = next(row for row in parsed.values if row["temperature"]["value"] == 1000)
    assert row["formation_enthalpy"]["value"] == -100.0
    assert row["formation_gibbs_energy"]["value"] == 1000.0
    assert row["log10_formation_equilibrium_constant"]["value"] == -52.236


def test_malformed_tail_refuses_enthalpy_bracket_crossing_transition() -> None:
    source = (
        "Synthetic transition-adjacent formation tail\n"
        "T(K)\tCp\tS\t-[G-H(Tr)]/T\tH-H(Tr)\tΔfH\tΔfG\tlog Kf\n"
        "900\t10\t10\t10\t0\t0.\t0.\t0.\n"
        "950\t10\t10\t10\t0\tALPHA <--> BETA\n"
        "1000\t10\t10\t10\t0\t1.000 0.500 0.026\n"
        "1100\t10\t10\t10\t0\t-1.200\t0.600\t-0.031\n"
    )
    parsed = parse_janaf_txt(
        source, table_id="synthetic-transition-tail", url="u", download_url="d"
    )
    assert not any(row["temperature"]["value"] == 1000 for row in parsed.values)
    ambiguity = next(
        item
        for item in parsed.parse_ambiguities
        if item.get("raw_line", "").startswith("1000\t")
    )
    assert ambiguity["kind"] == "nist_tail_whitespace_signs_unresolved"
    assert "formation enthalpy neighbors span a transition" in ambiguity["reason"]


def test_identity_consistent_tail_needs_no_same_sign_orientation() -> None:
    source = (
        "Synthetic identity-consistent formation tail\n"
        "T(K)\tCp\tS\t-[G-H(Tr)]/T\tH-H(Tr)\tΔfH\tΔfG\tlog Kf\n"
        "900\t10\t10\t10\t0\t-1.000\t0.038\t-0.002\n"
        "1000\t10\t10\t10\t0\t1.100 0.038 0.001\n"
        "1100\t10\t10\t10\t0\t-1.000\t0.038\t-0.002\n"
    )
    parsed = parse_janaf_txt(
        source, table_id="synthetic-identity-tail", url="u", download_url="d"
    )
    row = next(row for row in parsed.values if row["temperature"]["value"] == 1000)
    assert row["formation_enthalpy"]["value"] == -1.1
    assert row["formation_gibbs_energy"]["value"] == 0.038
    assert row["log10_formation_equilibrium_constant"]["value"] == -0.001


def test_every_absent_100k_node_is_explained_by_the_printed_grid() -> None:
    """Derived sparse ladders plus named unresolved rows are the only skipped nodes."""

    from decimal import Decimal

    unexplained: list[tuple[str, int, str]] = []
    for path in iter_table_paths():
        table = load_table_document(path)["table"]
        temperatures = sorted(
            Decimal(str(row["temperature"]["value"]))
            for row in table["values"]
            if row["temperature"]["value"] is not None
        )
        present = set(temperatures)
        if not temperatures:
            continue
        first = int(temperatures[0] // 100) * 100
        last = int(temperatures[-1] // 100) * 100
        for candidate in range(first, last + 1, 100):
            node = Decimal(candidate)
            if node in present or node < temperatures[0] or node > temperatures[-1]:
                continue
            left = max(value for value in temperatures if value < node)
            right = min(value for value in temperatures if value > node)
            gaps = [b - a for a, b in zip(temperatures, temperatures[1:])]
            left_index = temperatures.index(left)
            run_start = left_index
            run_end = left_index + 1
            while run_start > 0 and gaps[run_start - 1] in (
                Decimal("200"),
                Decimal("400"),
            ):
                run_start -= 1
            while run_end < len(gaps) and gaps[run_end] in (
                Decimal("200"),
                Decimal("400"),
            ):
                run_end += 1
            sparse_ladder = (
                left >= Decimal("3000")
                and right - left in (Decimal("200"), Decimal("400"))
                and run_end - run_start >= 2
            )
            reference_interval = (
                left == Decimal("298.15") and right == Decimal("500")
            )
            if not sparse_ladder and not reference_interval:
                ambiguity = next(
                    item
                    for item in table.get("parse_ambiguities", [])
                    if item.get("kind") == "nist_tail_whitespace_signs_unresolved"
                    and item.get("raw_line", "").startswith(f"{candidate}\t")
                )
                reason = (
                    "printed Gibbs/log Kf pair misses allowed tolerance"
                    if "identity misses by" in ambiguity.get("reason", "")
                    else "formation enthalpy neighbors span a transition marker"
                )
                unexplained.append((table["table_id"], candidate, reason))
    expected_grid_gaps = [
        row for row in EXPECTED_UNREPAIRED_TAILS if row[1] % 100 == 0
    ]
    assert sorted(unexplained) == sorted(expected_grid_gaps)


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
    zero_row = "0\t0.\t0.\tINFINITE\t-4.539\t0.\t0.\t0.\n"
    source = LIVE_TXT_SAMPLE.replace(
        zero_row,
        zero_row + "200\t20.786\t141.651\t151.852\t-2.040\t\t\t\t\t\t\n",
        1,
    )
    parsed = parse_janaf_txt(source, table_id="Al-006", url="u", download_url="d")
    row = parsed.values[1]
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
    payload = load_cached_safe_yaml(EXTRACT.read_text(encoding="utf-8"))
    assert payload["source_id"] == "janaf-4th"
    assert payload["schema_version"] == "literature_extract.v1"


def test_b133_300k_log_kf_is_a_compilation_finding_not_a_correction() -> None:
    """Printed log Kf at 300 K disagrees with printed ΔfG; do not rewrite either."""

    document = load_table_document(TABLES_DIR / "B-133.yaml")
    row = next(
        item
        for item in document["table"]["values"]
        if item["temperature"]["as_published"] == "300"
    )
    assert row["formation_gibbs_energy"]["as_published"] == "-5516.922"
    assert row["formation_gibbs_energy"]["value"] == -5516.922
    assert row["log10_formation_equilibrium_constant"]["as_published"] == "966.926"
    assert row["log10_formation_equilibrium_constant"]["value"] == 966.926
    findings = document["table"]["compilation_findings"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["kind"] == "compilation_finding"
    assert finding["action"] == "none"
    assert finding["old"] == finding["new"] == "966.926"
    assert finding["temperature_K"] == 300.0
    assert "300\t324.511" in finding["verbatim_quote"]
    assert "-5516.922" in finding["verbatim_quote"]
    assert "966.926" in finding["verbatim_quote"]


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
