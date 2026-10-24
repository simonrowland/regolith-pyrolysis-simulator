"""Independent checks for Albertsson (2013) at.% phase tables and context migration."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/albertsson-2013-cr-slag-thesis.yaml"

TABLE10 = (
    ("S1", "glass", ("0.1", "0.7", "0.1", "0.2")),
    ("S2", "pseudo-wollastonite", ("0.6", "—", "1.1", "0.2")),
    ("S2", "merwinite", ("0.3", "0.2", "0.4", "0.2")),
    ("S3", "Ca2SiO4", ("1.5", "—", "0.2", "0.1")),
    ("S3", "merwinite", ("0.3", "—", "0.1", "0.1")),
    ("S3", "pseudo-wollastonite", ("0.2", "1.3", "—", "0.1")),
    ("S4", "merwinite", ("0.2", "0.3", "0.2", "0.1")),
    ("S4", "Ca2SiO4", ("0.5", "0.6", "0.5", "0.1")),
    ("S5", "merwinite", ("0.9", "0.6", "0.9", "—")),
    ("S5", "Ca2SiO4", ("1.5", "1.1", "1.9", "—")),
    ("S6", "Ca2SiO4", ("—", "1.4", "1.1", "—")),
)

# Independent element-by-element transcription from the 600 dpi PDF renders.
TABLES13_TO20 = {
    "13": (
        ("EAF-slag1", "untreated", "larnite", ("0.0", "0.1", "0.2", "11.6", "21.0", "0.0", "0.2", "0.0", "0.0", "66.8")),
        ("EAF-slag1", "untreated", "brownmillerite", ("0.0", "0.0", "18.0", "0.3", "15.3", "0.1", "0.6", "0.0", "0.0", "65.7")),
        ("EAF-slag1", "untreated", "wüstite", ("1.9", "19.7", "0.3", "0.0", "0.4", "2.8", "15.5", "0.0", "0.0", "59.2")),
        ("EAF-slag1", "A", "spinel", ("14.1", "8.7", "3.0", "0.0", "0.6", "2.4", "6.2", "0.0", "0.0", "65.0")),
        ("EAF-slag1", "A", "larnite", ("0.0", "0.6", "0.1", "13.2", "24.4", "0.2", "0.3", "0.1", "0.1", "61.0")),
        ("EAF-slag1", "A", "wüstite", ("1.1", "19.5", "0.5", "0.0", "1.3", "5.5", "18.0", "0.0", "0.0", "54.1")),
        ("EAF-slag1", "A", "brownmillerite", ("0.1", "0.6", "7.5", "2.5", "17.6", "1.0", "9.6", "0.4", "0.1", "60.5")),
        ("EAF-slag1", "B", "larnite", ("0.0", "0.6", "0.1", "12.9", "23.2", "0.2", "0.3", "0.0", "0.0", "62.7")),
        ("EAF-slag1", "B", "wüstite", ("0.2", "8.9", "2.1", "0.4", "3.3", "4.9", "22.4", "0.1", "0.0", "57.8")),
        ("EAF-slag1", "B", "brownmillerite", ("0.0", "0.9", "8.0", "4.0", "16.4", "1.2", "8.1", "0.3", "0.1", "61.0")),
    ),
    "14": (
        ("EAF-slag2", "untreated", "akermanite", ("0.18", "5.5", "0.0", "12.7", "17.0", "0.5", "0.0", "0.0", "0.0", "64.0")),
        ("EAF-slag2", "untreated", "pseudo-wollastonite", ("0.6", "1.9", "2.7", "10.6", "14.8", "0.7", "0.0", "4.1", "0.0", "66.4")),
        ("EAF-slag2", "untreated", "spinel", ("17.2", "5.0", "3.1", "1.6", "1.6", "6.1", "0.3", "1.2", "0.0", "65.6")),
        ("EAF-slag2", "A", "spinel", ("15.2", "11.1", "6.6", "0.0", "0.4", "3.0", "0.4", "0.2", "0.0", "63.2")),
        ("EAF-slag2", "A", "pseudo-wollastonite", ("0.1", "1.7", "5.9", "13.1", "13.9", "0.8", "0.1", "1.4", "0.0", "62.9")),
        ("EAF-slag2", "A", "larnite", ("0.0", "1.0", "0.1", "14.1", "23.6", "0.5", "0.0", "0.1", "0.0", "60.6")),
        ("EAF-slag2", "B", "spinel", ("15.0", "10.4", "5.8", "0.0", "0.4", "3.1", "1.1", "0.2", "0.1", "64.1")),
        ("EAF-slag2", "B", "pseudo-wollastonite", ("0.3", "1.6", "5.0", "12.5", "14.9", "0.9", "0.4", "1.3", "0.1", "63.2")),
        ("EAF-slag2", "B", "larnite", ("0.1", "2.5", "1.3", "13.8", "21.2", "0.5", "0.2", "0.4", "0.0", "60.0")),
    ),
    "15": (
        ("EAF-slag3", "untreated", "spinel", ("17.6", "11.8", "4.1", "0.0", "0.0", "1.0", "0.3", "0.0", "0.3", "65.0", "0.0")),
        ("EAF-slag3", "untreated", "forsterite", ("0.3", "7.2", "5.1", "0.0", "12.8", "1.5", "14.3", "0.0", "1.5", "64.3", "0.0")),
        ("EAF-slag3", "untreated", "pseudo-wollastonite", ("0.1", "6.6", "6.0", "14.4", "7.4", "0.6", "0.8", "0.0", "0.0", "66.5", "0.0")),
        ("EAF-slag3", "A", "spinel", ("15.2", "11.2", "1.9", "0.0", "0.2", "0.8", "1.7", "0.0", "0.2", "67.8", "0.0")),
        ("EAF-slag3", "A", "merwinite", ("0.0", "9.6", "2.3", "11.7", "9.5", "0.5", "0.3", "0.1", "0.1", "59.7", "6.4")),
        ("EAF-slag3", "B", "spinel", ("11.9", "15.7", "17.1", "0.1", "0.3", "0.7", "0.7", "0.1", "0.1", "53.3", "0.0")),
        ("EAF-slag3", "B", "merwinite", ("0.1", "9.7", "2.6", "15.3", "10.9", "0.6", "0.3", "0.1", "0.1", "56.0", "4.4")),
    ),
    "16": (
        ("SY1", "A", "spinel", ("26.5", "11.2", "6.9", "0.1", "0.9", "1.6", "2.9", "49.9")),
        ("SY1", "A", "larnite", ("0.1", "2.8", "0.2", "14.1", "33.3", "0.5", "0.4", "48.7")),
        ("SY1", "A", "glass", ("0.1", "1.8", "14.1", "10.1", "22.3", "0.3", "1.0", "50.6")),
        ("SY1", "A", "merwinite", ("0.2", "6.7", "0.3", "13.8", "28.7", "0.3", "0.4", "49.8")),
        ("SY1", "B", "spinel", ("25.0", "12.3", "8.8", "0.8", "1.6", "0.7", "1.6", "49.3")),
        ("SY1", "B", "glass", ("0.4", "4.9", "7.6", "13.3", "22.9", "0.1", "0.3", "50.8")),
        ("SY1", "B", "merwinite", ("0.3", "5.1", "0.2", "13.4", "30.6", "0.2", "0.3", "50.2")),
    ),
    "17": (
        ("SY2", "A", "larnite", ("0.2", "1.0", "0.3", "14.2", "36.5", "0.1", "0.3", "47.6")),
        ("SY2", "A", "spinel", ("30.9", "12.0", "3.1", "0.1", "1.5", "1.4", "3.5", "47.5")),
        ("SY2", "A", "wüstite", ("9.6", "34.6", "2.0", "0.0", "0.8", "1.8", "8.3", "43.0")),
        ("SY2", "A", "periclase", ("2.0", "45.2", "0.1", "0.0", "0.4", "1.8", "9.7", "40.9")),
        ("SY2", "B", "spinel", ("19.8", "18.8", "3.5", "0.0", "0.4", "1.1", "3.5", "52.9")),
        ("SY2", "B", "larnite", ("0.2", "0.2", "0.3", "13.2", "32.7", "0.1", "0.2", "53.1")),
        ("SY2", "B", "periclase", ("2.3", "41.8", "0.1", "0.1", "0.3", "1.0", "8.0", "46.6")),
    ),
    "18": (
        ("SY3", "A", "larnite", ("0.1", "0.1", "0.9", "13.2", "35.2", "0.1", "0.1", "50.3")),
        ("SY3", "A", "periclase", ("0.3", "49.5", "0.1", "0.0", "0.7", "4.5", "0.8", "44.1")),
        ("SY3", "A", "Ca3SiO5", ("0.2", "0.1", "1.2", "12.5", "35.2", "0.2", "0.2", "50.5")),
        ("SY3", "A", "spinel", ("27.4", "6.9", "3.0", "0.1", "3.3", "4.9", "2.6", "51.8")),
        ("SY3", "A", "brownmillerite", ("0.1", "2.6", "23.1", "1.1", "21.9", "0.8", "0.7", "50.0")),
        ("SY3", "B", "larnite", ("0.2", "0.3", "0.3", "13.8", "36.9", "0.2", "0.3", "48.1")),
        ("SY3", "B", "spinel", ("29.1", "11.3", "1.3", "0.4", "4.0", "2.0", "5.9", "46.1")),
        ("SY3", "B", "wüstite", ("1.5", "5.5", "3.1", "5.5", "15.6", "1.8", "11.4", "55.6")),
        ("SY3", "B", "periclase", ("1.3", "49.6", "0.0", "0.1", "1.1", "2.3", "6.1", "39.4")),
    ),
    "19": (
        ("SY4", "A", "Ca3SiO5", ("0.2", "0.2", "0.4", "12.5", "37.7", "0.1", "0.3", "48.5")),
        ("SY4", "A", "wüstite", ("6.9", "2.4", "1.4", "1.2", "25.9", "0.5", "14.3", "47.4")),
        ("SY4", "A", "periclase", ("1.6", "49.9", "0.0", "0.1", "0.5", "1.2", "5.2", "41.6")),
        ("SY4", "A", "CaCr2O4", ("26.4", "1.9", "1.1", "0.2", "20.1", "0.6", "2.1", "47.9")),
        ("SY4", "A", "spinel", ("19.1", "26.9", "1.1", "0.1", "2.6", "1.1", "3.2", "45.9")),
        ("SY4", "B", "Ca3SiO5", ("0.4", "0.5", "0.4", "10.8", "38.2", "0.2", "0.5", "49.1")),
        ("SY4", "B", "periclase", ("1.7", "46.9", "0.2", "0.1", "1.5", "1.4", "4.4", "43.8")),
        ("SY4", "B", "CaCr2O4", ("26.4", "1.7", "0.0", "0.1", "19.8", "0.0", "2.5", "49.5")),
        ("SY4", "B", "spinel", ("16.2", "18.9", "0.8", "1.2", "9.3", "1.2", "4.4", "48.5")),
    ),
    "20": (
        ("", "oxygen", "spinel", ("61.82", "8.36", "29.65", "0.10", "0.04", "0.03")),
        ("", "oxygen", "matrix", ("66.59", "3.07", "17.84", "8.03", "4.47", "0.00")),
    ),
}


def _context_record(extract: dict, observation_id: str) -> dict:
    return next(
        row
        for payload in extract["species"].values()
        for row in payload.get("context", [])
        if row["observation_id"] == observation_id
    )


def _table10_grid(extract: dict) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    record = _context_record(extract, "albertsson2013_table10_matrix_chromium")
    return tuple(
        (row["sample"], row["phase"], tuple(row["at_percent_Cr"]))
        for row in record["values"]["rows"]
    )


def _assert_table10_matches(extract: dict, expected=TABLE10) -> None:
    assert _table10_grid(extract) == expected


def _assert_phase_tables_match(extract: dict) -> None:
    for table, expected_rows in TABLES13_TO20.items():
        record = next(
            row
            for payload in extract["species"].values()
            for row in payload.get("context", [])
            if row["observation_id"].startswith(f"albertsson2013_table{table}_")
        )
        actual = tuple(
            (
                row.get("sample", ""),
                row.get("treatment", "oxygen"),
                row["phase"],
                tuple(row["values"]),
            )
            for row in record["values"]["rows"]
        )
        assert actual == expected_rows


def test_albertsson_at_percent_grids_match_independent_render_transcription() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table10_matches(extract)
    _assert_phase_tables_match(extract)


def test_albertsson_grid_pins_reject_grid_and_extract_mutations() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))

    changed_grid = list(TABLE10)
    changed_grid[0] = ("S1", "glass", ("0.11", "0.7", "0.1", "0.2"))
    with pytest.raises(AssertionError):
        _assert_table10_matches(extract, tuple(changed_grid))

    changed_extract = deepcopy(extract)
    table10 = _context_record(changed_extract, "albertsson2013_table10_matrix_chromium")
    table10["values"]["rows"][0]["at_percent_Cr"][0] = "0.11"
    with pytest.raises(AssertionError):
        _assert_table10_matches(changed_extract)

    changed_extract = deepcopy(extract)
    table16 = _context_record(changed_extract, "albertsson2013_table16_sy1_phase_compositions")
    table16["values"]["rows"][0]["values"][0] = "26.51"
    with pytest.raises(AssertionError):
        _assert_phase_tables_match(changed_extract)


def test_albertsson_context_only_extract_migrates_without_scored_rows() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.observations

    migrated_context = {
        row["observation_id"]
        for rows in migrator.result.context_by_work.values()
        for row in rows
    }
    assert len(migrated_context) == 36
    assert "albertsson2013_table10_matrix_chromium" in migrated_context
    assert "albertsson2013_table20_wds_phase_compositions" in migrated_context
    assert "albertsson2013_figure32_calculated_crO3_pressure" in migrated_context


def test_albertsson_printed_prose_numbers_remain_in_context() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    pt_partition = _context_record(
        extract, "albertsson2013_context_pt_cr_partition_72h"
    )["values"]
    assert pt_partition["temperature_K"] == "1673"
    assert pt_partition["oxygen_partial_pressure_Pa"] == "1e-4"
    assert pt_partition["observations"][1]["chromium_at_percent"] == "4.5"
    assert pt_partition["observations"][2]["chromium_at_percent"] == "0.5"

    noise_level = _context_record(
        extract, "albertsson2013_context_pt_cr_wds_noise"
    )["values"]
    assert noise_level["chromium_at_percent_as_published"] == "around 0.02"
