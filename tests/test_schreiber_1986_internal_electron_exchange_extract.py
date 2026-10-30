from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schreiber-1986-internal-electron-exchange.yaml"


def _assert_context_and_figure(document: dict) -> None:
    context = document["species"]["SiO2"]["context"]
    repeated = next(row for row in context if row["observation_id"] == "schreiber_1986_table1_repeated_reference_potentials")
    assert repeated["values"]["table_row_count"] == 26
    assert repeated["values"]["repeated_from"] == {
        "source_id": "schreiber-1985-geochemical-emf-series",
        "observation_id": "schreiber_1985_table1_reference_reduction_potentials",
    }
    figure = next(row for row in context if row["type"] == "figure_record")
    assert figure["values"]["numeric_points"] == "none"
    assert figure["values"]["digitisation"] == "deferred to a figure pass"
    assert [series["marker_count"] for series in figure["values"]["series"]] == [12, 23]
    findings = next(row for row in context if row["observation_id"] == "schreiber_1986_sequential_oxidation_and_redox_buffering")
    assert len(findings["values"]["claims_as_printed"]) == 3


def test_repeated_table_is_linked_and_interaction_figure_deferred() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_context_and_figure(document)


def test_figure_value_mutation_breaks_deferred_record_pin() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    figure = next(row for row in document["species"]["SiO2"]["context"] if row["type"] == "figure_record")
    figure["values"]["numeric_points"] = ["0.75"]
    with pytest.raises(AssertionError):
        _assert_context_and_figure(document)


def test_isolated_migration_has_no_scored_duplicate_or_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert len(migrator.result.observations) == 0
