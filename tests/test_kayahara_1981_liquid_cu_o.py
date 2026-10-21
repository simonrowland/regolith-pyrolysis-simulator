"""Printed Cu-O facts and deferred figures for Kayahara (1981)."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kayahara-1981-liquid-cu-o.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_capture(document):
    context = document["species"]["Cu"]["context"]
    assert "monotectic temperature from emf: 1495 K" in context[0]["values"]["printed_values"]
    assert context[0]["locator"]["pdf_page_index"] == 2
    assert context[1]["locator"]["pdf_page_index"] == 6
    assert "comparison temperatures: 1503 K (Osterwald) and 1500 K (Sadat-Darbandi)" in context[0]["values"]["printed_values"]
    assert "oxygen concentration range: 3 to 34 at% O" in context[0]["values"]["printed_values"]
    assert context[1]["values"]["equations_as_printed"][0] == "ΔG°Cu2O(s) = -194.85 + 91.96 × 10^-3 T (kJ/mol)"
    figures = [row for row in context if row["type"] == "figure_only"]
    assert [row["locator"]["figure"] for row in figures] == [str(n) for n in range(1, 10)]
    assert [row["locator"]["pdf_page_index"] for row in figures] == [1, 2, 3, 3, 4, 5, 5, 6, 6]
    assert [row["locator"]["page"] for row in figures] == [2, 3, 4, 4, 5, 6, 6, 7, 7]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("points" not in row["values"] for row in figures)


def test_printed_facts_figures_and_migration_mutation():
    document = _document()
    _assert_capture(document)
    mutated = deepcopy(document)
    mutated["species"]["Cu"]["context"][0]["values"]["printed_values"][2] = "monotectic temperature: 1496 K"
    with pytest.raises(AssertionError):
        _assert_capture(mutated)
    mutated = deepcopy(document)
    mutated["species"]["Cu"]["context"][2]["locator"]["pdf_page_index"] = 2
    with pytest.raises(AssertionError):
        _assert_capture(mutated)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert not migrator.result.observations
