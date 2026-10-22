from __future__ import annotations

import copy
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))
from literature_extract_test_helpers import iter_locators

SID = "stolyarova-2021-ti-al-kems-ru"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
PDF = CORPUS_ROOT / "raw" / SID / f"{SID}.pdf"

# Independent transcription of every Table 3 cell from the 600-dpi render of
# physical page 6. The time field is the printed row label; None is a dash or
# blank, never an inferred value. This grid is separate from the extractor.
TABLE3_RENDER = (
    ("0", "0.50", "0.50", "2349", "1.34", "0.27", "0.02", "0.05", "0.03", "0.5", "0.52"),
    ("5", "0.49", "0.51", "2349", "1.15", "0.23", "0.03", "0.09", "0.06", "0.49", "0.52"),
    ("10", "0.49", "0.51", "2345", "1.08", "0.40", "0.04", "0.11", "0.07", "0.48", "0.51"),
    ("15", "0.48", "0.52", "2346", "1.00", "0.42", "0.05", "0.11", "0.07", "0.44", "0.52"),
    ("20", "0.48", "0.52", "2347", "0.96", "0.40", "0.05", "0.11", "0.08", "0.42", "0.52"),
    ("25", "0.47", "0.53", "2346", "0.93", "0.39", "0.05", "0.11", "0.09", "0.41", "0.52"),
    ("30", "0.47", "0.53", "2345", "0.85", "0.37", "0.05", "0.11", "0.10", "0.38", "0.52"),
    ("40", "0.46", "0.54", "2352", "0.86", "0.38", "0.05", "0.11", "0.12", "0.35", "0.52"),
    ("50", "0.44", "0.56", "2353", "0.78", "0.34", "0.05", "0.15", "0.15", "0.32", "0.52"),
    ("60", "0.43", "0.57", "2353", "0.86", "0.42", "0.05", "0.15", "0.12", "0.35", "0.52"),
    ("70", "0.42", "0.8", "2359", "0.86", "0.41", "0.05", "0.15", "0.12", "0.33", "0.53"),
    ("80", "0.40", "0.60", "2359", "0.82", "0.36", "0.05", "0.15", "0.12", "0.31", "0.58"),
    ("100", "0.37", "0.63", "2370", "0.94", "0.42", "0.07", "0.15", "0.17", "0.32", "0.63"),
    ("120", "0.33", "0.67", "2367", "0.86", "0.42", "0.07", "0.15", "0.18", "0.30", "0.67"),
    ("a", "0.33", "0.67", "2355", "0.76", "0.28", "0.19", None, None, "0.36", None),
    ("150", "0.27", "0.73", "2368", "0.71", "0.35", "0.07", "0.15", "0.19", "0.25", "0.72"),
    ("a", "0.25", "0.75", "2376", "0.45", "0.12", "0.25", None, None, "0.28", None),
    ("180", "0.21", "0.79", "2372", "0.53", "0.24", "0.07", "0.15", "0.20", "0.18", "0.77"),
    ("200", "0.17", "0.83", "2371", "0.41", "0.20", "0.07", "0.19", "0.20", "0.14", "0.82"),
    ("220", "0.14", "0.87", "2362", "0.34", "0.17", "0.07", "0.18", "0.20", "0.12", "0.86"),
    ("240", "0.10", "0.90", "2375", "0.30", "0.15", "0.07", "0.15", "0.20", "0.10", "0.88"),
    ("240", "0.10", "0.90", "2477", "0.78", "0.43", "0.27", "0.93", "1.03", "0.09", "0.91"),
    ("245", "0.08", "0.92", "2491", "0.63", "0.36", "0.29", "0.94", "1.12", "0.07", "0.93"),
    ("250", "0.04", "0.96", "2499", "0.51", "0.29", "0.27", "0.94", "1.13", "0.05", "0.94"),
    ("260", "0.04", "0.96", "2500", "0.28", "0.16", "0.25", "0.94", "1.13", "0.03", "0.96"),
    ("270", "0.02", "0.98", "2489", "0.16", "0.08", "0.26", "0.94", "1.12", "0.02", "0.98"),
    ("285", "0.01", "0.99", "2489", "0.04", "0.03", "0.25", "0.94", "1.12", "0.01", "1.00"),
    ("300", "0", "1.00", "2491", "0", "0", "0.21", "0.81", "1.04", "0", "1.00"),
    ("300", "0", "1.00", "2711", "0", "0", "2.06", "8.77", "12.21", None, None),
    ("305", "0", "1.00", "2705", "0", "0", "2.44", "8.31", "9.37", None, None),
    ("310", "0", "1.00", "2701", "0", "0", "0", "0", "0", None, None),
)
PRESSURE_COLUMNS = {
    "TiO2": (4, Decimal("1")),
    "TiO": (5, Decimal("1")),
    "Al": (6, Decimal("1")),
    "AlO": (7, Decimal("10")),
    "Al2O": (8, Decimal("10")),
}
SAMPLE_BY_ROW = {15: "sample2", 17: "sample1"}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _actual_table3_grid(document: dict) -> tuple[tuple[str | None, ...], ...]:
    rows: list[list[str | None]] = [[None] * 11 for _ in TABLE3_RENDER]
    for formula, block in document["species"].items():
        for observation in block.get("observations", []):
            local_id = observation["observation_id"]
            if not local_id.startswith("stolyarova_2021_table3_"):
                continue
            row_number = int(observation["locator"]["row"])
            row = rows[row_number - 1]
            values = observation["values"]
            composition = values["composition_mol"]
            row[0] = str(values["time_evaporation_min_as_printed"])
            row[1] = str(composition["TiO2"])
            row[2] = str(composition["Al2O3"])
            row[3] = str(values["T_K"])
            if observation["type"] == "partial_pressure":
                column, scale = PRESSURE_COLUMNS[formula]
                row[column] = str(values["table_cell_as_printed"])
                assert Decimal(str(values["pressure_Pa"])) == Decimal(row[column]) / scale
            else:
                column = 9 if formula == "TiO2" else 10
                row[column] = str(values["table_cell_as_printed"])
                assert str(values["activity"]) == row[column]
    return tuple(tuple(row) for row in rows)


def test_table3_render_grid_and_both_cell_mutations_are_detected() -> None:
    document = _document()
    actual = _actual_table3_grid(document)
    assert actual == TABLE3_RENDER
    assert len(actual) == 31
    assert sum(value is not None for row in actual for value in row) == 329

    changed_render = [list(row) for row in TABLE3_RENDER]
    changed_render[0][4] = "1.35"
    with pytest.raises(AssertionError):
        assert actual == tuple(tuple(row) for row in changed_render)

    changed_extract = copy.deepcopy(document)
    changed_cell = next(
        observation
        for observation in changed_extract["species"]["TiO2"]["observations"]
        if observation["observation_id"].endswith("partial_pressure_row_01")
    )
    changed_cell["values"]["table_cell_as_printed"] = "1.35"
    with pytest.raises(AssertionError):
        assert _actual_table3_grid(changed_extract) == TABLE3_RENDER


def test_table3_rows_bind_composition_sample_and_conditions() -> None:
    document = _document()
    records = next(
        record
        for record in document["species"]["TiO2"]["context"]
        if record["observation_id"] == "stolyarova_2021_table3_calculated_residue_composition_context"
    )
    for row_number, row in enumerate(TABLE3_RENDER, start=1):
        context_row = records["values"]["rows"][row_number - 1]
        assert context_row == {
            "row": row_number,
            "time_min_as_printed": row[0],
            "TiO2_mole_fraction_as_printed": row[1],
            "Al2O3_mole_fraction_as_printed": row[2],
            "temperature_K_as_printed": row[3],
            "sample_id": SAMPLE_BY_ROW.get(row_number, "sample3"),
        }

    source_observations = {
        item["observation_id"]: (formula, item)
        for formula, block in document["species"].items()
        for item in block.get("observations", [])
    }
    assert len(source_observations) == 205
    for row_number, row in enumerate(TABLE3_RENDER, start=1):
        for formula, (column, scale) in PRESSURE_COLUMNS.items():
            observation_id = f"stolyarova_2021_table3_{formula}_partial_pressure_row_{row_number:02d}"
            raw = row[column]
            if raw is None:
                assert observation_id not in source_observations
                continue
            actual_formula, observation = source_observations[observation_id]
            assert actual_formula == formula
            assert observation["values"]["table_cell_as_printed"] == raw
            assert Decimal(observation["values"]["pressure_Pa"]) == Decimal(raw) / scale
            assert observation["values"]["sample_id"] == SAMPLE_BY_ROW.get(row_number, "sample3")
            assert observation["point_conditions"]["temperature_K"]["locator"]["row"] == str(row_number)

        for formula, column in (("TiO2", 9), ("Al2O3", 10)):
            observation_id = f"stolyarova_2021_table3_{formula}_activity_row_{row_number:02d}"
            raw = row[column]
            if raw is None:
                assert observation_id not in source_observations
                continue
            _, observation = source_observations[observation_id]
            assert observation["values"]["activity"] == raw
            assert observation["values"]["sample_id"] == SAMPLE_BY_ROW.get(row_number, "sample3")


def test_isolated_migration_types_each_printed_value_and_identity() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={SID: document["source"]}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 205
    assert not any(
        issue.reason.value == "source_value_outside_physical_range"
        for issue in result.validation.issues
    )
    source_observations = {
        f"{SID}::{observation['observation_id']}": (formula, observation)
        for formula, block in document["species"].items()
        for observation in block.get("observations", [])
    }
    assert set(result.observations) == set(source_observations)
    for source_id, migrated in result.observations.items():
        formula, source = source_observations[source_id]
        row_number = int(source["locator"]["row"])
        row = TABLE3_RENDER[row_number - 1]
        values = source["values"]
        assert migrated.identity.species.formula == formula
        assert migrated.value.kind is ValueKind.POINT
        assert migrated.identity.temperature_K.is_value
        assert migrated.identity.temperature_K.value == Decimal(row[3])
        assert migrated.identity.composition.is_value
        composition = dict(migrated.identity.composition.value.components)
        assert composition == {"TiO2": Decimal(row[1]), "Al2O3": Decimal(row[2])}
        assert migrated.point_conditions is not None
        temperature = migrated.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal(row[3])
        assert temperature.locator is not None
        assert temperature.locator.page == 6
        assert temperature.locator.pdf_page_index == 5
        assert temperature.locator.table == "3"
        assert source["values"]["sample_id"] == SAMPLE_BY_ROW.get(row_number, "sample3")

        if source["type"] == "partial_pressure":
            assert migrated.identity.quantity.value is Quantity.P_PARTIAL
            column, scale = PRESSURE_COLUMNS[formula]
            expected = Decimal(row[column]) / scale
            assert migrated.value.point == expected
            assert source["units"] == "Pa"
            assert migrated.identity.species.phase.is_value
            assert migrated.identity.species.phase.value is Phase.G
        else:
            column = 9 if formula == "TiO2" else 10
            assert migrated.identity.quantity.value is Quantity.ACTIVITY
            assert migrated.value.point == Decimal(row[column])
            assert source["units"] == "dimensionless"
            assert migrated.identity.species.phase.is_value
            assert migrated.identity.species.phase.value is Phase.L
            assert migrated.identity.reference_state is not None
            if formula == "TiO2":
                reference = migrated.identity.reference_state.value
                assert reference.endmember.formula == "TiO2"
                assert reference.endmember.phase.is_value
                assert reference.endmember.phase.value is Phase.L
                assert source["standard_state"]["inferred"] is True
                assert "2130 K" in source["standard_state"]["basis"]
            else:
                assert migrated.identity.reference_state.is_unknown

        assert not any(
            notice.kind.value == "source_value_outside_physical_range"
            for notice in migrated.notices
        )


def test_figure_records_are_deferred_and_point_free() -> None:
    document = _document()
    figures = [
        record
        for record in document["species"]["TiO2"]["context"]
        if record.get("what") == "figure_record"
    ]
    assert len(figures) == 5
    for figure in figures:
        assert "points" not in figure.get("values", {})
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert all(
            values.get(field)
            for field in (
                "species",
                "phase",
                "temperature_K",
                "axes_as_printed",
                "caption_as_printed",
                "attribution",
                "series",
            )
        )
        assert all("marker_count" in series for series in values["series"])
        assert figure["locator"]["page"] == figure["locator"]["pdf_page_index"] + 1


@pytest.mark.skipif(not PDF.is_file(), reason="source corpus PDF is absent")
def test_cited_physical_pages_contain_their_printed_table_or_figure_labels() -> None:
    document = _document()
    pages = {
        int(locator["page"])
        for locator in iter_locators(document)
        if locator.get("table") or locator.get("figure") or locator.get("equation")
    }
    assert pages == {2, 3, 4, 5, 6, 7, 8}
    page_text = {}
    for page in pages:
        completed = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert completed.stdout.strip()
        page_text[page] = completed.stdout
    assert "Рис. 1" in page_text[2]
    assert "Таблица 1" in page_text[3] and "Таблица 2" in page_text[3]
    assert "Рис. 2" in page_text[4]
    assert "Рис. 3" in page_text[5]
    assert "Таблица 3" in page_text[6]
    assert "Рис. 4" in page_text[7] and "Рис. 5" in page_text[7]
    assert "(17)" in page_text[8]
