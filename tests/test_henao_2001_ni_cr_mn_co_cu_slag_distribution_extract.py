"""Regression pins for Henao et al. (2001) table and deferred figure context."""

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


def _table_rows(document: dict, table: str) -> list[list[str]]:
    record = next(
        row
        for row in document["species"]["FeO"]["context"]
        if row["locator"].get("table") == table
    )
    return list(csv.reader(StringIO(record["values"]["printed_rows_csv"])))


def _assert_render_pins(document: dict) -> None:
    """Table 1 cells below are independently read from the 600 dpi PDF p. 2 render."""
    rows = _table_rows(document, "1")
    assert len(rows) == 18  # header plus all 17 printed A-series rows
    assert rows[1][1:5] == ["A-1", "-6.8", "42.5", "4.50"]
    assert rows[1][7:10] == ["21.8", "11.3", "2.2"]
    assert rows[1][12:15] == ["0.78", "11.2", "91.7"]
    assert len(_table_rows(document, "2")) == 11  # header plus 10 B-series rows
    assert len(_table_rows(document, "3")) == 29  # header plus 28 C-series rows


def test_henao_tables_and_deferred_figures_migrate_as_context() -> None:
    document = _document()
    _assert_render_pins(document)

    contexts = [
        row
        for species in document["species"].values()
        for row in species["context"]
    ]
    tables = [row for row in contexts if row["locator"].get("table")]
    figures = [row for row in contexts if row["locator"].get("figure")]
    assert len(tables) == 4
    assert len(figures) == 10
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("numeric_points" not in row["values"] for row in figures)
    assert tables[0]["values"]["temperature_K"] == 1773
    assert "derivation pending a corpus table asset (controller, t-1382)" in tables[0]["values"]["derivation_status"]

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not [observation for observation in result.observations.values() if observation.source_id == EXTRACT.stem]
    migrated_contexts = [
        context
        for rows in result.context_by_work.values()
        for context in rows
        if context["context_id"].startswith(f"{EXTRACT.stem}::context::")
    ]
    assert len(migrated_contexts) == 14
    assert not result.validation.hard_issues


def test_table_cell_mutation_is_detected_by_independent_render_pins() -> None:
    mutated = deepcopy(_document())
    rows = _table_rows(mutated, "1")
    rows[1][3] = "42.6"
    table = next(
        row
        for row in mutated["species"]["FeO"]["context"]
        if row["locator"].get("table") == "1"
    )
    table["values"]["printed_rows_csv"] = "\n".join(",".join(row) for row in rows)
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)

