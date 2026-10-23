"""Regression pins for Henao et al. (2001) typed tables and deferred figures."""

from __future__ import annotations

import csv
from copy import deepcopy
from io import StringIO
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/henao-2001-ni-cr-mn-co-cu-slag-distribution.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[dict]:
    return [
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    ]


def _table_rows(document: dict, table: str) -> list[list[str]]:
    record = next(
        row
        for row in document["species"]["FeO"]["context"]
        if row["locator"].get("table") == table
    )
    return list(csv.reader(StringIO(record["values"]["printed_rows_csv"])))


def _assert_render_pins(document: dict) -> None:
    """Pin printed cells independently read from the PDF page renders."""
    observations = {row["observation_id"]: row for row in _observations(document)}
    assert len(observations) == 431
    assert observations["henao_2001_t1_A-1_slag_Fe"]["values"]["component_amount"] == "42.5"
    assert observations["henao_2001_t1_A-1_slag_Ni"]["values"]["component_amount"] == "4.50"
    assert observations["henao_2001_t1_A-1_slag_MgO"]["values"]["component_amount"] == "21.8"
    assert observations["henao_2001_t1_A-1_slag_SiO2"]["values"]["component_amount"] == "11.3"
    assert observations["henao_2001_t1_A-1_alloy_Fe"]["values"]["component_amount"] == "11.2"
    assert observations["henao_2001_t1_A-1_alloy_Ni"]["values"]["component_amount"] == "91.7"
    assert observations["henao_2001_t3_C-1_alloy_Co"]["values"]["component_amount"] == "91.2"
    assert observations["henao_2001_t1_A-1_alloy_Fe"]["values"]["phase_label"] == "metal"
    first = observations["henao_2001_t1_A-1_slag_Fe"]["values"]
    assert first["log_fO2"]["value"] == "-6.8"
    assert first["log_fO2"]["units"] == "log10(atm)"
    assert "fO2_Pa" not in first
    assert len(_table_rows(document, "1")) == 18
    assert len(_table_rows(document, "2")) == 11
    assert len(_table_rows(document, "3")) == 29
    assert _table_rows(document, "1")[1][1:5] == ["A-1", "", "", ""]
    assert _table_rows(document, "1")[1][7:10] == ["", "", "2.2"]
    assert _table_rows(document, "1")[1][12:15] == ["0.78", "", ""]
    component_columns = {
        "1": (2, 3, 4, 5, 6, 7, 8, 13, 14, 15, 16),
        "2": (2, 3, 4, 5, 6, 7, 8, 9, 14, 15, 16, 17),
        "3": (2, 3, 4, 5, 6, 7, 8, 9, 10, 16, 17, 18, 19, 20),
    }
    for table, columns in component_columns.items():
        assert all(
            row[column] == "" or row[column].replace(" ", "") == "<0.03"
            for row in _table_rows(document, table)[1:]
            for column in columns
        )
    bounds = [
        cell
        for table in ("1", "2", "3")
        for row in _table_rows(document, table)
        for cell in row
        if cell.replace(" ", "") == "<0.03"
    ]
    assert len(bounds) == 4


def test_henao_tables_migrate_as_typed_compositions_with_conditions() -> None:
    document = _document()
    _assert_render_pins(document)

    contexts = [
        row
        for species in document["species"].values()
        for row in species.get("context", [])
    ]
    figures = [row for row in contexts if row["locator"].get("figure")]
    assert len(figures) == 10
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("numeric_points" not in row["values"] for row in figures)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = [row for row in result.observations.values() if row.source_id == EXTRACT.stem]
    assert len(observations) == 431
    assert all(row.admission.status.value == "admitted" for row in observations)
    assert not [entry for entry in result.queue if entry.source.endswith(EXTRACT.name)]
    assert all(row.identity.temperature_K.value == 1773 for row in observations)
    assert all(row.point_conditions and "fO2_log" in row.point_conditions for row in observations)
    assert all(row.identity.fO2_Pa.tag.value == "value" for row in observations)
    assert all(row.locator and row.locator.page and row.locator.published_page for row in observations)
    migrated_contexts = [
        context
        for rows in result.context_by_work.values()
        for context in rows
        if context["context_id"].startswith(f"{EXTRACT.stem}::context::")
    ]
    assert len(migrated_contexts) == 14
    assert not result.validation.hard_issues


def test_typed_cell_mutation_is_detected_by_independent_pdf_pins() -> None:
    for observation_id, amount in (
        ("henao_2001_t1_A-1_slag_Fe", "42.6"),
        ("henao_2001_t1_A-1_slag_MgO", "21.9"),
    ):
        mutated = deepcopy(_document())
        row = next(
            observation
            for observation in _observations(mutated)
            if observation["observation_id"] == observation_id
        )
        row["values"]["component_amount"] = amount
        with pytest.raises(AssertionError):
            _assert_render_pins(mutated)

    mutated = deepcopy(_document())
    rows = _table_rows(mutated, "1")
    rows[1][9] = "2.3"
    table = next(
        row
        for row in mutated["species"]["FeO"]["context"]
        if row["locator"].get("table") == "1"
    )
    table["values"]["printed_rows_csv"] = "\n".join(",".join(value for value in row) for row in rows)
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)
