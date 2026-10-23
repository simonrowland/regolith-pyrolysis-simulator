"""Source-cell and migration pins for the SrO-Al2O3 KEMS supplement."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
import re
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SID = "stolyarova-vorozhtcov-lopatin-2023-sro-al2o3-kems-supplement"
ROOT = Path(__file__).resolve().parents[2]
CORPUS = regolith_corpus_root()
SOURCE = CORPUS / "raw" / SID / f"{SID}.docx"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
ACTIVITY_EXPONENT = {1: -7, 2: -8, 3: -7, 4: -8, 5: -9, 6: -9}
SAMPLE = {1: "1", 2: "2", 3: "1", 4: "2", 5: "3", 6: "3", 7: "1", 8: "2", 9: "3"}
SAMPLE_COMPOSITION = {"1": "33 mol% SrO", "2": "20 mol% SrO", "3": "10 mol% SrO"}
PRESSURE_COLUMNS = (("Sr", 3), ("Al", 4), ("AlO", 5), ("Al2O", 6), ("O", 7))
IONS = (("Sr", "Sr+", 1), ("Al", "Al+", 2), ("AlO", "AlO+", 3), ("Al2O", "Al2O+", 4))
PressureCell = tuple[str, int, str, Decimal, Decimal | None, Decimal | None, Decimal]
ActivityCell = tuple[int, str, Decimal, Decimal, Decimal, Decimal]
IonCell = tuple[int, str, str, Decimal, Decimal]
ModelCell = tuple[int, str, str, str, Decimal, Decimal, Decimal, str, Decimal]
PrintedCell = tuple[object, str]


def source_decimal(text: str) -> Decimal:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:×)?10(-\d+)", text)
    if match:
        return Decimal(match.group(1)) * Decimal(10) ** int(match.group(2))
    return Decimal(text)


def table_cells(source: Path) -> list[list[list[str]]]:
    with ZipFile(source) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    return [
        [
            [
                "".join(text.text or "" for text in cell.findall(".//w:t", NS)).strip()
                for cell in row.findall("./w:tc", NS)
            ]
            for row in table.findall("./w:tr", NS)
        ]
        for table in document.findall(".//w:tbl", NS)
    ]


def test_printed_table_cells_migrate_with_their_row_conditions() -> None:
    if not SOURCE.is_file():
        pytest.skip(f"supporting-information DOCX is absent: {SOURCE}")

    tables = table_cells(SOURCE)
    assert [len(table) for table in tables] == [28, 21, 29, 20, 18, 16, 13, 15, 17]
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))

    expected_pressure: Counter[PressureCell] = Counter()
    expected_pressure_printed: Counter[PrintedCell] = Counter()
    expected_activity: Counter[ActivityCell] = Counter()
    expected_activity_printed: Counter[PrintedCell] = Counter()
    expected_ions: Counter[IonCell] = Counter()
    expected_ions_printed: Counter[PrintedCell] = Counter()
    expected_models: Counter[ModelCell] = Counter()

    for table_number in range(1, 7):
        sample = SAMPLE[table_number]
        for row in tables[table_number - 1][2:]:
            temperature = Decimal(row[0])
            srO, al2o3 = Decimal(row[1]), Decimal(row[2])
            for formula, column in PRESSURE_COLUMNS:
                raw = row[column]
                if raw and raw != "-":
                    cell = (
                        formula,
                        table_number,
                        sample,
                        temperature,
                        srO,
                        al2o3,
                        source_decimal(raw),
                    )
                    expected_pressure[cell] += 1
                    expected_pressure_printed[(cell, raw)] += 1
            raw_activity = row[8]
            if raw_activity and raw_activity != "-":
                cell = (
                    table_number,
                    sample,
                    temperature,
                    srO,
                    al2o3,
                    source_decimal(raw_activity)
                    * Decimal(10) ** ACTIVITY_EXPONENT[table_number],
                )
                expected_activity[cell] += 1
                expected_activity_printed[(cell, raw_activity)] += 1
            for component, columns in (("SrO", (9, 10)), ("Al2O3", (11, 12))):
                for model_index, column in enumerate(columns):
                    raw = row[column]
                    if not raw or raw == "-":
                        continue
                    exponent = ACTIVITY_EXPONENT[table_number] if component == "SrO" else 0
                    model = (
                        "Redlich-Kister polynomial (10)"
                        if model_index == 0
                        else "Wilson polynomial (12)"
                    )
                    expected_models[
                        (
                            table_number,
                            component,
                            model,
                            sample,
                            temperature,
                            srO,
                            al2o3,
                            raw,
                            source_decimal(raw) * Decimal(10) ** exponent,
                        )
                    ] += 1

    for table_number in range(7, 10):
        sample = SAMPLE[table_number]
        for row in tables[table_number - 1][2:]:
            temperature = Decimal(row[0])
            for formula, column in PRESSURE_COLUMNS:
                raw = row[5 + PRESSURE_COLUMNS.index((formula, column))]
                if raw and raw != "-":
                    cell = (
                        formula,
                        table_number,
                        sample,
                        temperature,
                        None,
                        None,
                        source_decimal(raw),
                    )
                    expected_pressure[cell] += 1
                    expected_pressure_printed[(cell, raw)] += 1
            for formula, _ion, column in IONS:
                raw = row[column]
                if raw and raw != "-":
                    cell = (table_number, formula, sample, temperature, source_decimal(raw))
                    expected_ions[cell] += 1
                    expected_ions_printed[(cell, raw)] += 1

    assert sum(expected_pressure.values()) == 778
    assert sum(expected_activity.values()) == 120
    assert sum(expected_ions.values()) == 139
    assert sum(expected_models.values()) == 480

    extract_pressure: Counter[PressureCell] = Counter()
    extract_pressure_printed: Counter[PrintedCell] = Counter()
    extract_activity: Counter[ActivityCell] = Counter()
    extract_activity_printed: Counter[PrintedCell] = Counter()
    extract_ions: Counter[IonCell] = Counter()
    extract_ions_printed: Counter[PrintedCell] = Counter()
    extract_models: Counter[ModelCell] = Counter()

    for formula, container in extract["species"].items():
        for observation in container.get("observations", []):
            values = observation["values"]
            table_number = int(observation["locator"]["table"][1:])
            sample = str(observation["sample_id_as_printed"])
            if values.get("quantity") == "partial_pressure":
                assert values["gas_species"] == formula
                for point in values["points"]:
                    composition = point.get("composition_as_printed")
                    if table_number <= 6:
                        parts = re.fullmatch(r"x\(SrO\)=([^;]+); x\(Al2O3\)=([^;]+)", composition)
                        assert parts
                        srO, al2o3 = Decimal(parts.group(1)), Decimal(parts.group(2))
                    else:
                        assert composition == SAMPLE_COMPOSITION[sample]
                        srO = al2o3 = None
                    cell = (
                        formula,
                        table_number,
                        sample,
                        Decimal(str(point["T_K"])),
                        srO,
                        al2o3,
                        Decimal(str(point["P_Pa"])),
                    )
                    extract_pressure[cell] += 1
                    extract_pressure_printed[
                        (cell, str(point["value_as_published"]))
                    ] += 1
            elif values.get("quantity") == "activity":
                assert formula == "SrO"
                for point in values["points"]:
                    printed_components = point["composition_mol"]
                    cell = (
                        table_number,
                        sample,
                        Decimal(str(point["T_K"])),
                        Decimal(printed_components["SrO"]),
                        Decimal(printed_components["Al2O3"]),
                        Decimal(str(point["activity"])),
                    )
                    extract_activity[cell] += 1
                    extract_activity_printed[
                        (cell, str(point["value_as_published"]))
                    ] += 1
            elif values.get("quantity") == "ion_intensity":
                assert values["gas_species"] == formula
                for point in values["points"]:
                    assert point["composition_as_printed"] == SAMPLE_COMPOSITION[sample]
                    cell = (
                        table_number,
                        formula,
                        sample,
                        Decimal(str(point["T_K"])),
                        Decimal(str(point["ion_intensity"])),
                    )
                    extract_ions[cell] += 1
                    extract_ions_printed[
                        (cell, str(point["value_as_published"]))
                    ] += 1

        for record in container.get("context", []):
            values = record.get("values", {})
            if values.get("method_class") != "model_derived":
                continue
            table_number = int(record["locator"]["table"][1:])
            component = values["component"]
            sample = str(values["sample_id_as_printed"])
            for row in values["data"]:
                extract_models[
                    (
                        table_number,
                        component,
                        values["model_as_printed"],
                        sample,
                        Decimal(str(row["T_K"])),
                        Decimal(str(row["composition_as_printed"]["SrO"])),
                        Decimal(str(row["composition_as_printed"]["Al2O3"])),
                        row["value_as_published"],
                        Decimal(str(row["activity_dimensionless_after_header_scale"])),
                    )
                ] += 1

    assert extract_pressure == expected_pressure
    assert extract_pressure_printed == expected_pressure_printed
    assert extract_activity == expected_activity
    assert extract_activity_printed == expected_activity_printed
    assert extract_ions == expected_ions
    assert extract_ions_printed == expected_ions_printed
    assert extract_models == expected_models

    figures = [
        record
        for container in extract["species"].values()
        for record in container.get("context", [])
        if record.get("type") == "figure_record"
    ]
    assert len(figures) == 12
    assert {record["locator"]["figure"] for record in figures} == {
        f"S{n}" for n in range(1, 13)
    }
    assert all(
        record["values"]["digitisation"] == "deferred to a figure pass"
        for record in figures
    )
    assert all("points" not in record["values"] for record in figures)

    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    migrated = [
        observation
        for observation in result.observations.values()
        if observation.source_id == SID
    ]
    migrated_pressure: Counter[PressureCell] = Counter()
    migrated_activity: Counter[ActivityCell] = Counter()
    migrated_ions: Counter[IonCell] = Counter()

    for observation in migrated:
        identity = observation.identity
        formula = identity.species.formula
        assert observation.value.kind is ValueKind.POINT
        assert not observation.notices
        assert identity.temperature_K.is_value
        temperature = identity.temperature_K.value
        table_number = int(observation.locator.table[1:])
        conditions = observation.point_conditions or {}
        assert "composition" in conditions, observation.observation_id
        composition = conditions["composition"]
        assert composition.locator is not None
        assert composition.locator.table == f"S{table_number}"
        assert composition.locator.note.startswith("supporting-information data row ")
        if table_number <= 6:
            assert composition.state.is_value, observation.observation_id
            if identity.quantity.value is Quantity.P_PARTIAL:
                candidates = [
                    (srO, al2o3)
                    for (
                        candidate_formula,
                        candidate_table,
                        _sample,
                        candidate_T,
                        srO,
                        al2o3,
                        candidate_value,
                    ), count in expected_pressure.items()
                    if candidate_formula == formula
                    and candidate_table == table_number
                    and candidate_T == temperature
                    and candidate_value == observation.value.point
                    and srO is not None
                    and al2o3 is not None
                    and count
                ]
            elif identity.quantity.value is Quantity.ACTIVITY:
                candidates = [
                    (srO, al2o3)
                    for (
                        candidate_table,
                        _sample,
                        candidate_T,
                        srO,
                        al2o3,
                        candidate_value,
                    ), count in expected_activity.items()
                    if candidate_table == table_number
                    and candidate_T == temperature
                    and candidate_value == observation.value.point
                    and count
                ]
            else:
                candidates = []
            actual = dict(composition.state.value.components)
            assert (actual["SrO"], actual["Al2O3"]) in candidates, (
                observation.observation_id,
                candidates,
                actual,
            )
            assert dict(identity.composition.value.components) == actual
            srO, al2o3 = actual["SrO"], actual["Al2O3"]
        else:
            assert composition.state.is_unknown, observation.observation_id
            assert SAMPLE_COMPOSITION[SAMPLE[table_number]] in composition.state.reason
            srO = al2o3 = None

        if identity.quantity.value is Quantity.P_PARTIAL:
            assert identity.species.phase.is_value and identity.species.phase.value is Phase.G
            migrated_pressure[
                (
                    formula,
                    table_number,
                    SAMPLE[table_number],
                    temperature,
                    srO,
                    al2o3,
                    observation.value.point,
                )
            ] += 1
        elif identity.quantity.value is Quantity.ACTIVITY:
            assert formula == "SrO"
            assert identity.reference_state.is_unknown
            assert identity.species.phase.is_value and identity.species.phase.value is Phase.L
            migrated_activity[
                (
                    table_number,
                    SAMPLE[table_number],
                    temperature,
                    srO,
                    al2o3,
                    observation.value.point,
                )
            ] += 1
        elif identity.quantity.value is Quantity.ION_INTENSITY:
            assert identity.species.phase.is_value and identity.species.phase.value is Phase.G
            migrated_ions[
                (
                    table_number,
                    formula,
                    SAMPLE[table_number],
                    temperature,
                    observation.value.point,
                )
            ] += 1
        else:
            pytest.fail(f"unexpected migrated quantity: {identity.quantity.value}")

    assert migrated_pressure == expected_pressure
    assert migrated_activity == expected_activity
    assert migrated_ions == expected_ions
