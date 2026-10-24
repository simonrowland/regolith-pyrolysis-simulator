"""Source-grid and migration pins for the gaseous cerates SI tables."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SID = "shugurov-2024-gaseous-cerates-si"
ROOT = Path(__file__).resolve().parents[2]
CORPUS = regolith_corpus_root()
SOURCE = CORPUS / "raw" / SID / f"{SID}.docx"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

# Independent transcription of the three measured-pressure columns, followed
# by the two calculated reaction-enthalpy columns, from the publisher DOCX.
TABLES = {
    "S1": [
        ("2226", "10.2", "1.90", "9.39", "381.4", "327.0"),
        ("2229", "11.4", "2.00", "8.85", "377.6", "323.2"),
        ("2235", "11.2", "1.87", "9.98", "382.5", "327.9"),
        ("2232", "11.8", "2.22", "9.42", "376.7", "322.3"),
        ("2236", "10.0", "1.91", "7.21", "378.3", "323.7"),
        ("2237", "9.55", "1.91", "5.00", "372.5", "317.9"),
        ("2236", "8.84", "1.38", "4.99", "379.8", "325.3"),
        ("2223", "14.3", "3.81", "15.0", "370.3", "316.1"),
        ("2230", "15.2", "3.82", "15.0", "370.2", "315.8"),
        ("2229", "14.6", "3.68", "12.9", "368.7", "314.3"),
        ("2227", "14.5", "3.40", "13.6", "371.0", "316.6"),
        ("2230", "13.5", "3.14", "9.29", "367.3", "312.9"),
        ("2224", "13.4", "3.40", "9.27", "364.9", "310.7"),
        ("2218", "12.9", "3.12", "9.24", "366.1", "312.0"),
        ("2240", "16.4", "3.56", "10.8", "365.5", "310.9"),
        ("2240", "16.0", "3.56", "10.8", "366.0", "311.3"),
        ("2247", "15.6", "3.30", "10.1", "367.9", "313.0"),
        ("2244", "14.5", "3.29", "9.35", "367.4", "312.6"),
        ("2249", "14.2", "2.61", "8.65", "371.3", "316.4"),
        ("2245", "12.5", "2.47", "6.48", "368.8", "314.0"),
    ],
    "S2": [
        ("2167", "11.1", "3.71", "5.60", "436.5", "383.1"),
        ("2158", "10.7", "3.69", "6.14", "437.1", "383.9"),
        ("2154", "10.3", "3.69", "5.57", "435.2", "382.1"),
        ("2155", "9.14", "3.69", "5.01", "435.7", "382.6"),
        ("2157", "9.05", "4.00", "5.02", "434.8", "381.7"),
        ("2165", "8.64", "4.32", "5.04", "435.9", "382.5"),
        ("2159", "9.27", "4.00", "5.02", "434.8", "381.6"),
        ("2164", "8.97", "4.01", "5.03", "436.4", "383.0"),
        ("2166", "9.52", "4.33", "5.04", "434.4", "381.0"),
        ("2159", "8.62", "4.00", "4.46", "434.0", "380.8"),
        ("2158", "9.27", "4.31", "3.90", "428.8", "375.6"),
        ("2164", "8.40", "4.01", "4.47", "435.4", "382.1"),
        ("2162", "9.17", "4.01", "3.91", "431.1", "377.8"),
        ("2198", "10.6", "5.33", "5.68", "436.9", "382.7"),
        ("2192", "13.0", "6.57", "5.10", "426.3", "372.3"),
        ("2200", "10.7", "6.90", "5.69", "432.6", "378.3"),
        ("2208", "12.5", "8.19", "5.14", "426.2", "371.8"),
        ("2199", "12.0", "7.21", "5.68", "429.4", "375.2"),
        ("2207", "12.6", "8.50", "4.56", "423.0", "368.6"),
        ("2207", "12.4", "7.24", "4.56", "426.2", "371.7"),
        ("2207", "9.70", "9.13", "3.99", "424.0", "369.6"),
        ("2207", "5.62", "9.44", "2.28", "423.2", "368.7"),
    ],
}
SERIES = {
    ("S1", "CeO2"): (1, -4),
    ("S1", "SrO"): (2, -5),
    ("S1", "SrCeO3"): (3, -8),
    ("S2", "CeO2"): (1, -5),
    ("S2", "CaO"): (2, -7),
    ("S2", "CaCeO3"): (3, -9),
}


def _read_docx_tables(path: Path) -> list[list[list[str]]]:
    with ZipFile(path) as source:
        document = ET.fromstring(source.read("word/document.xml"))
    tables = []
    for table in document.findall(".//w:tbl", WORD_NS):
        rows = []
        for row in table.findall("./w:tr", WORD_NS):
            cells = []
            for cell in row.findall("./w:tc", WORD_NS):
                paragraphs = []
                for paragraph in cell.findall("./w:p", WORD_NS):
                    paragraphs.append(
                        "".join(
                            text.text or ""
                            for text in paragraph.findall(".//w:t", WORD_NS)
                        )
                    )
                cells.append("\n".join(part for part in paragraphs if part).strip())
            rows.append(cells)
        tables.append(rows)
    return tables


def test_s1_s2_docx_cells_and_all_migrated_pressure_points() -> None:
    if not SOURCE.is_file():
        pytest.skip(f"publisher SI DOCX is absent: {SOURCE}")

    source_tables = _read_docx_tables(SOURCE)
    assert len(source_tables) == 13
    for table_id, expected_rows in TABLES.items():
        table_index = int(table_id[1:]) - 1
        numeric_rows = source_tables[table_index][2:-1]
        assert [tuple(row[:6]) for row in numeric_rows] == expected_rows
    assert source_tables[0][-1][:3] == [
        "Average value",
        "371.7±4.7",
        "317.2±4.7",
    ]
    assert source_tables[1][-1][:3] == [
        "Average value",
        "431.7±4.9",
        "378.0±5.3",
    ]

    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    extracted_points = Counter()
    for formula, species_data in extract["species"].items():
        for observation in species_data.get("observations", []):
            assert observation["values"]["formula"] == formula
            table_id = observation["locator"]["table"]
            assert (table_id, formula) in SERIES
            column, exponent = SERIES[(table_id, formula)]
            for row_index, point in enumerate(observation["values"]["points"], 1):
                source_row = TABLES[table_id][row_index - 1]
                printed_pressure = source_row[column]
                expected_atm = Decimal(printed_pressure) * (Decimal(10) ** exponent)
                assert point["T_K_as_printed"] == source_row[0]
                assert point["T_K"] == int(source_row[0])
                assert Decimal(point["P_atm"]) == expected_atm
                assert point["P_atm_as_printed"] == printed_pressure
                assert point["pressure_scale_as_printed"] == f"10^{exponent} atm"
                assert point["value_as_published"] == (
                    f"{printed_pressure} × 10^{exponent} atm"
                )
                conditions = point["point_conditions"]
                temperature = conditions["temperature_K"]
                composition = conditions["composition"]
                assert temperature["state"]["value"]["point"] == source_row[0]
                assert temperature["locator"]["table"] == table_id
                assert composition["state"]["tag"] == "unknown"
                assert composition["locator"]["table"] == table_id
                extracted_points[
                    (table_id, formula, row_index, source_row[0], printed_pressure)
                ] += 1
    expected_points = Counter(
        (table_id, formula, row_index, row[0], row[column])
        for (table_id, formula), (column, _exponent) in SERIES.items()
        for row_index, row in enumerate(TABLES[table_id], 1)
    )
    assert extracted_points == expected_points
    assert sum(extracted_points.values()) == 126

    reaction_enthalpy = {
        record["observation_id"]: record
        for species_data in extract["species"].values()
        for record in species_data.get("context", [])
        if record["observation_id"].endswith("reaction_enthalpy_calculations")
    }
    for table_id, formula, mean_c3v, mean_cs in (
        ("S1", "SrCeO3", "371.7 ± 4.7", "317.2 ± 4.7"),
        ("S2", "CaCeO3", "431.7 ± 4.9", "378.0 ± 5.3"),
    ):
        record = reaction_enthalpy[
            f"shugurov_2024_{table_id.lower()}_{'sr' if table_id == 'S1' else 'ca'}_reaction_enthalpy_calculations"
        ]
        values = record["values"]
        assert values["method_class"] == "model_derived"
        assert values["average_values_as_printed"] == {
            "C3v": mean_c3v,
            "Cs": mean_cs,
        }
        expected_enthalpies = [
            {"C3v": row[4], "Cs": row[5]} for row in TABLES[table_id]
        ]
        assert [
            run["negative_reaction_enthalpy_at_0K_kJ_mol"]
            for run in values["runs"]
        ] == expected_enthalpies

    figures = [
        record
        for species_data in extract["species"].values()
        for record in species_data.get("context", [])
        if record.get("type") == "figure_record"
    ]
    assert len(figures) == 1
    assert figures[0]["locator"]["figure"] == "S1"
    assert figures[0]["values"]["digitisation"] == "deferred to a figure pass"
    assert "points" not in figures[0]["values"]

    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    migrated = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == SID
    ]
    assert len(migrated) == 126
    assert not [
        (observation.observation_id, notice)
        for observation in migrated
        for notice in observation.notices
    ]

    migrated_points = Counter()
    for observation in migrated:
        identity = observation.identity
        assert observation.value.kind is ValueKind.POINT
        assert identity.quantity.value is Quantity.P_PARTIAL
        assert identity.temperature_K.is_value
        temperature = int(identity.temperature_K.value)
        formula = identity.species.formula
        assert formula in {"CeO2", "SrO", "SrCeO3", "CaO", "CaCeO3"}
        assert identity.species.phase.is_value
        assert identity.species.phase.value is Phase.G
        conditions = observation.point_conditions or {}
        assert "temperature_K" in conditions
        assert "composition" in conditions
        assert conditions["temperature_K"].state.is_value
        assert conditions["temperature_K"].locator is not None
        assert not conditions["composition"].state.is_value
        assert conditions["composition"].locator is not None
        table_id = conditions["temperature_K"].locator.table
        row_note = conditions["temperature_K"].locator.note
        row_index = int(row_note.rsplit(" ", 1)[-1])
        source_row = TABLES[table_id][row_index - 1]
        column, exponent = SERIES[(table_id, formula)]
        expected_pa = (
            Decimal(source_row[column])
            * (Decimal(10) ** exponent)
            * Decimal("101325")
        )
        assert observation.value.point == expected_pa
        migrated_points[
            (table_id, formula, row_index, temperature, observation.value.point)
        ] += 1
    expected_migrated = Counter()
    for (table_id, formula), (column, exponent) in SERIES.items():
        for row_index, source_row in enumerate(TABLES[table_id], 1):
            expected_pa = (
                Decimal(source_row[column])
                * (Decimal(10) ** exponent)
                * Decimal("101325")
            )
            expected_migrated[
                (table_id, formula, row_index, int(source_row[0]), expected_pa)
            ] += 1
    assert migrated_points == expected_migrated
