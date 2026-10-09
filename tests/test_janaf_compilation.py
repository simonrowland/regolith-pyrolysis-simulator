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
import simulator.reference_data.janaf as janaf_reference

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
    _element_reference_gibbs_sign,
    _gibbs_sign_from_neighbors,
    harvest_era,
    is_nist_tail_signed_repair_cell,
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

EXPECTED_UNREPAIRED_TAILS = (
    ("B-123", 1100, "printed Gibbs/log Kf pair misses allowed tolerance"),
    ("C-008", 2800, "formation Gibbs neighbors do not structurally pin a sign"),
    ("C-009", 2800, "formation Gibbs neighbors do not structurally pin a sign"),
    ("Cl-032", 400, "formation Gibbs neighbors do not structurally pin a sign"),
    ("Cl-033", 400, "formation Gibbs neighbors do not structurally pin a sign"),
    ("H-014", 1000, "formation Gibbs neighbors do not structurally pin a sign"),
    ("H-015", 1000, "formation Gibbs neighbors do not structurally pin a sign"),
    ("Mo-019", 2100, "formation Gibbs neighbors do not structurally pin a sign"),
    ("N-014", 3300, "formation Gibbs neighbors do not structurally pin a sign"),
    ("N-015", 3300, "formation Gibbs neighbors do not structurally pin a sign"),
    ("Ni-012", 1300, "formation Gibbs neighbors do not structurally pin a sign"),
    ("Ni-013", 1300, "formation Gibbs neighbors do not structurally pin a sign"),
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
CU020_PAGE_READ_SAMPLE = (
    "Copper Oxide (Cu2O)\tCu2O1(l)\n"
    "T(K)\tCp\tS\t-[G-H(Tr)]/T\tH-H(Tr)\tdelta-f H\tdelta-f G\tlog Kf\n"
    "1400\t99.914\t250.822\t185.249\t91.802\t-127.026\t-61.360\t2.289\n"
    "1500\t99.914\t257.715\t189.853\t101.794\t-125.424\t-56.725\t1.975\n"
    "1516.700\t99.914\t258.821\t190.606\t103.462\tCRYSTAL <--> LIQUID\n"
    "1600\t99.914\t264.163\t194.298\t111.785\t123.836  52.197 1.704\n"
    "1700\t99.914\t270.220\t198.587\t121.776\t-122.259\t-47.768\t1.468\n"
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
                "formation_enthalpy": None,
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
    if table_id == "K-003":
        assert row["formation_enthalpy"]["locator"]["parse_repair"] == (
            "nist_tail_dfh_sign_undetermined"
        )


@pytest.mark.parametrize(
    ("table_id", "temperature"),
    (("O-038", 1700), ("O-037", 1700), ("Fe-018", 1700), ("Na-012", 1500)),
)
def test_transition_following_enthalpy_bracket_is_named_refusal(
    table_id: str, temperature: int
) -> None:
    table = load_table_document(TABLES_DIR / f"{table_id}.yaml")["table"]
    row = next(
        row for row in table["values"] if row["temperature"]["value"] == temperature
    )
    enthalpy = row["formation_enthalpy"]
    assert enthalpy["value"] is None
    assert enthalpy["locator"]["parse_repair"] == "nist_tail_dfh_sign_undetermined"
    assert "formation enthalpy neighbors span a transition marker" in enthalpy[
        "locator"
    ]["parse_repair_reason"]
    assert row["formation_gibbs_energy"]["value"] is not None
    assert row["log10_formation_equilibrium_constant"]["value"] is not None


def test_cu020_1600k_enthalpy_uses_the_page_read_sign() -> None:
    table = load_table_document(TABLES_DIR / "Cu-020.yaml")["table"]
    row = next(row for row in table["values"] if row["temperature"]["value"] == 1600)
    enthalpy = row["formation_enthalpy"]

    assert enthalpy["value"] == -123.836
    assert enthalpy["as_published"] == "-123.836"
    assert enthalpy["locator"]["page_read_sign"] == "-123.836"
    assert enthalpy["locator"]["page_locator"] == (
        "https://janaf.nist.gov/pdf/JANAF-FourthEd-1998-Copper.pdf#page=20; "
        "SHA-256 d161dc6535ffa6c579357f7489573f402610fbbf94018d703b6aa350c59d3773; "
        "printed p. 1024"
    )
    assert enthalpy["locator"]["crop_note"] == (
        "https://janaf.nist.gov/pdf/JANAF-FourthEd-1998-Copper.pdf#page=20; "
        "SHA-256 d161dc6535ffa6c579357f7489573f402610fbbf94018d703b6aa350c59d3773; "
        "printed p. 1024 / PDF p. 20, Table Cu-020, rows 1500, 1600, and 1700 K; "
        "crop shows the ΔfH° header and row signs."
    )

    parsed = parse_janaf_txt(
        CU020_PAGE_READ_SAMPLE,
        table_id="Cu-020",
        url="https://janaf.nist.gov/tables/Cu-020.html",
        download_url="https://janaf.nist.gov/tables/Cu-020.txt",
    )
    parsed_row = next(
        row for row in parsed.values if row["temperature"]["value"] == 1600
    )
    assert parsed_row["formation_enthalpy"]["value"] == -123.836
    assert parsed_row["formation_enthalpy"]["as_published"] == "-123.836"


def test_page_read_sign_provenance_has_no_machine_local_paths() -> None:
    forbidden = ("/private/", "/Users/", "/tmp/")
    for entry in janaf_reference.PAGE_READ_SIGNS:
        for field, value in entry.items():
            if isinstance(value, str):
                assert not any(path in value for path in forbidden), field


def test_page_read_sign_refuses_a_mismatched_text_layer_magnitude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        janaf_reference,
        "PAGE_READ_SIGNS",
        tuple(
            {**entry, "printed_token": "-123.837"}
            for entry in janaf_reference.PAGE_READ_SIGNS
        ),
    )
    parsed = parse_janaf_txt(
        CU020_PAGE_READ_SAMPLE,
        table_id="Cu-020",
        url="https://janaf.nist.gov/tables/Cu-020.html",
        download_url="https://janaf.nist.gov/tables/Cu-020.txt",
    )

    assert not any(row["temperature"]["value"] == 1600 for row in parsed.values)
    refusal = next(
        ambiguity
        for ambiguity in parsed.parse_ambiguities
        if ambiguity.get("kind")
        == janaf_reference.PAGE_READ_SIGN_MAGNITUDE_MISMATCH_KIND
    )
    assert refusal["reason"] == janaf_reference.PAGE_READ_SIGN_MAGNITUDE_MISMATCH_REASON


def test_refused_janaf_row_keeps_unsigned_source_line() -> None:
    table = load_table_document(TABLES_DIR / "O-038.yaml")["table"]
    raw_line = (
        "1700\t85.772\t158.857\t102.619\t95.604\t"
        "941.610  609.059 18.714"
    )
    row = next(row for row in table["values"] if row["temperature"]["value"] == 1700)
    assert row["formation_enthalpy"]["as_published"] == "941.610"
    assert row["formation_enthalpy"]["locator"]["raw_line"] == raw_line
    assert row["formation_enthalpy"]["locator"]["raw_tail_tokens"] == [
        "941.610",
        "609.059",
        "18.714",
    ]
    assert row["formation_gibbs_energy"]["value"] == -609.059
    assert row["log10_formation_equilibrium_constant"]["value"] == 18.714


def test_signed_tail_cell_validation_requires_marker_raw_line_and_magnitude() -> None:
    cell = {
        "as_published": "609.059",
        "value": -609.059,
        "locator": {
            "parse_repair": NIST_TAIL_PARSE_REPAIR,
            "raw_line": "1700\t...\t941.610  609.059 18.714",
        },
    }
    assert is_nist_tail_signed_repair_cell(cell)
    without_marker = deepcopy(cell)
    without_marker["locator"].pop("parse_repair")
    assert not is_nist_tail_signed_repair_cell(without_marker)
    without_raw_line = deepcopy(cell)
    without_raw_line["locator"].pop("raw_line")
    assert not is_nist_tail_signed_repair_cell(without_raw_line)
    wrong_magnitude = deepcopy(cell)
    wrong_magnitude["value"] = -609.060
    assert not is_nist_tail_signed_repair_cell(wrong_magnitude)


def test_gibbs_sign_continuity_refuses_zero_crossings_and_small_values() -> None:
    assert _gibbs_sign_from_neighbors(Decimal("10"), Decimal("9")) == 1
    assert _gibbs_sign_from_neighbors(Decimal("-10"), Decimal("-9")) == -1
    assert _gibbs_sign_from_neighbors(Decimal("-1"), Decimal("1")) is None
    # I-013@5000: the closest neighbour is only 0.625 kJ/mol from zero,
    # while the neighbours differ by 2.075 kJ/mol.
    assert _gibbs_sign_from_neighbors(Decimal("-0.625"), Decimal("-2.700")) is None


def test_element_reference_sign_requires_condensed_single_element_table() -> None:
    assert _element_reference_gibbs_sign(
        "O2", "cr", has_zero_reference_interval=True
    ) == 1
    assert _element_reference_gibbs_sign(
        "O2", "g", has_zero_reference_interval=True
    ) is None
    assert _element_reference_gibbs_sign(
        "MgO", "cr", has_zero_reference_interval=True
    ) is None
    assert _element_reference_gibbs_sign(
        "O2", "cr", has_zero_reference_interval=False
    ) is None


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
                else "formation Gibbs neighbors do not structurally pin a sign"
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
def test_large_logk_rows_restore_gibbs_and_mark_undecided_enthalpy(
    table_id: str, temperature: int
) -> None:
    table = load_table_document(TABLES_DIR / f"{table_id}.yaml")["table"]
    row = next(
        row for row in table["values"] if row["temperature"]["value"] == temperature
    )
    enthalpy = row["formation_enthalpy"]
    assert enthalpy["value"] is None
    assert enthalpy["locator"]["parse_repair"] == "nist_tail_dfh_sign_undetermined"
    assert row["formation_gibbs_energy"]["value"] is not None
    assert row["log10_formation_equilibrium_constant"]["value"] is not None
    assert not any(
        item.get("raw_line", "").startswith(f"{temperature}\t")
        for item in table.get("parse_ambiguities", [])
    )


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


def test_malformed_tail_restores_gibbs_when_enthalpy_spans_transition() -> None:
    source = (
        "Synthetic transition-adjacent formation tail\n"
        "T(K)\tCp\tS\t-[G-H(Tr)]/T\tH-H(Tr)\tΔfH\tΔfG\tlog Kf\n"
        "900\t10\t10\t10\t0\t-1.000\t1.000\t-0.052\n"
        "950\t10\t10\t10\t0\tALPHA <--> BETA\n"
        "1000\t10\t10\t10\t0\t1.000 0.500 0.026\n"
        "1100\t10\t10\t10\t0\t-1.200\t0.600\t-0.031\n"
    )
    parsed = parse_janaf_txt(
        source, table_id="synthetic-transition-tail", url="u", download_url="d"
    )
    row = next(row for row in parsed.values if row["temperature"]["value"] == 1000)
    assert row["formation_enthalpy"]["value"] is None
    assert row["formation_enthalpy"]["locator"]["parse_repair"] == (
        "nist_tail_dfh_sign_undetermined"
    )
    assert row["formation_gibbs_energy"]["value"] == 0.5
    assert row["log10_formation_equilibrium_constant"]["value"] == -0.026


def test_transition_enthalpy_refusal_prevents_the_fe004_wrong_sign() -> None:
    import simulator.reference_data.janaf as janaf

    source = (
        "Synthetic Fe-004 audit row\n"
        "T(K)\tCp\tS\t-[G-H(Tr)]/T\tH-H(Tr)\tΔfH\tΔfG\tlog Kf\n"
        "1100\t10\t10\t10\t0\t0.100\t0.052\t-0.002\n"
        "1184\t10\t10\t10\t0\tALPHA <--> GAMMA\n"
        "1300\t10\t10\t10\t0\t0.267 0.052 0.002\n"
        "1400\t10\t10\t10\t0\t0.044\t0.052\t-0.002\n"
    )
    guarded = janaf.parse_janaf_txt(
        source, table_id="synthetic-fe004-audit", url="u", download_url="d"
    )
    row = next(row for row in guarded.values if row["temperature"]["value"] == 1300)
    assert row["formation_enthalpy"]["value"] is None
    assert row["formation_enthalpy"]["locator"]["parse_repair"] == (
        "nist_tail_dfh_sign_undetermined"
    )


@pytest.mark.parametrize(
    ("table_id", "temperature"),
    (
        ("F-045", "800"),
        ("Fe-029", "350"),
        ("Cl-070", "3600"),
        ("I-018", "2000"),
        ("S-011", "1600"),
        ("Co-007", "3200"),
        ("Na-007", "1100"),
        ("S-020", "800"),
        ("O-009", "2000"),
        ("B-091", "2300"),
    ),
)
def test_opposite_sign_enthalpy_neighbors_do_not_restore_tail(
    table_id: str, temperature: str, janaf_source_dir: Path
) -> None:
    """Opposite-sign DfH brackets cannot establish a repaired sign."""

    source_path = janaf_source_dir / f"{table_id}.txt"
    lines = source_path.read_text(encoding="utf-8").splitlines()
    target_index = next(
        index
        for index, line in enumerate(lines)
        if line.split("\t", 1)[0] == temperature
    )
    fields = lines[target_index].split("\t")
    assert len(fields) >= 8
    # Hide the original signs and reproduce the malformed whitespace tail.
    lines[target_index] = "\t".join(
        [*fields[:5], " ".join(token.lstrip("+-") for token in fields[5:8])]
    )

    parsed = parse_janaf_txt(
        "\n".join(lines), table_id=table_id, url="u", download_url="d"
    )
    row = next(
        row for row in parsed.values
        if row["temperature"]["as_published"] == temperature
    )
    assert row["formation_enthalpy"]["value"] is None
    assert row["formation_enthalpy"]["locator"]["parse_repair"] == (
        "nist_tail_dfh_sign_undetermined"
    )


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
                    else "formation Gibbs neighbors do not structurally pin a sign"
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
