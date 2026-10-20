from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schreiber-1979-lpsc-fe-redox-abstract.yaml"
FIGURE_IDS = [f"schreiber_1979_figure{i}_" for i in range(1, 7)]


def _assert_context_only(document: dict) -> None:
    rows = document["species"]["Fe"]["context"]
    figures = [row for row in rows if row["type"] == "figure_record"]
    assert len(figures) == 6
    assert all(
        any(row["observation_id"].startswith(prefix) for row in figures)
        for prefix in FIGURE_IDS
    )
    assert all(row["values"]["numeric_points"] == "none" for row in figures)
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    fit = next(row for row in rows if row["observation_id"] == "schreiber_1979_table1_reduction_reaction_enthalpy")
    assert fit["values"]["method_class"] == "model_derived"
    assert fit["values"]["value_as_published"] == "28±17 kcal/mole"


def test_abstract_is_context_only_and_figures_are_deferred() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_context_only(document)


def test_figure_record_mutation_breaks_context_only_pin() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    document["species"]["Fe"]["context"][1]["values"]["numeric_points"] = ["0.7"]
    with pytest.raises(AssertionError):
        _assert_context_only(document)


def test_isolated_migration_has_no_scored_figure_values_or_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert len(migrator.result.observations) == 0

