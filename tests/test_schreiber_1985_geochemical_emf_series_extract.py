from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schreiber-1985-geochemical-emf-series.yaml"
RENDER_GRID = [
    ("b", "Ni", "3-2", "1.7", None, "1.5"),
    ("a", "Co", "3-2", "1.4", None, "1.1"),
    ("e", "Au", "3-0", "> .8", None, None),
    ("c", "Mn", "3-2", ".8", None, ".7"),
    ("i", "Ag", "1-0", ".5", None, None),
    ("f", "O", "O2-2-", "0", None, None),
    ("d", "Ce", "4-3", "-.1", ".8", "-.5"),
    ("g", "Cr", "6-3", "-.3", "1.1", None),
    ("k", "Sb", "5-3", "-.3", None, "-.9"),
    ("q", "Cu", "2-1", "-.8", None, None),
    ("s", "U", "6-5", "-1.5", ".2", None),
    ("j", "Fe", "3-2", "-1.7", "-.4", "-2.1"),
    ("m", "As", "5-3", "-1.7", None, None),
    ("h", "V", "5-4", "-1.9", "-.8", "-2.7"),
    ("l", "U", "5-4", "-2.2", "-1.3", None),
    ("n", "Cu", "1-0", "-3.3", None, None),
    ("x", "Cr", "3-2", "-3.4", "-1.6", None),
    ("o", "Mo", "6-5", "-3.8", None, None),
    ("p", "V", "4-3", "-4.0", None, "-3.8"),
    ("w", "Eu", "3-2", "-4.3", "-1.8", None),
    ("t", "Ti", "4-3", "-5.0", "-3.7", "-5.7"),
    ("u", "Ni", "2-0", "-5.3", None, None),
    ("r", "Sn", "4-2", "-5.5", None, "-4.4"),
    ("v", "Co", "2-0", "-6.0", None, None),
    ("y", "Fe", "2-0", "-6.3", None, None),
    ("-", "Mo", "5-0", "-15.8", None, None),
]


def _assert_table_and_figures(document: dict, render_grid=RENDER_GRID) -> None:
    context = document["species"]["SiO2"]["context"]
    table = next(row for row in context if row["observation_id"] == "schreiber_1985_table1_reference_reduction_potentials")
    rows = table["values"]["table_rows"]
    assert len(rows) == table["values"]["table_row_count"] == 26
    extract_grid = [
        (
            row["index"],
            row["element"],
            row["couple"],
            row["SRL-131_1150_C"],
            row["FAS_1500_C"],
            row["Na2Si2O5_1085_C"],
        )
        for row in rows
    ]
    assert extract_grid == render_grid
    by_index = {row["index"]: row for row in rows}
    assert by_index["b"]["SRL-131_1150_C"] == "1.7"
    assert by_index["e"]["SRL-131_1150_C"] == "> .8"
    assert by_index["j"]["Na2Si2O5_1085_C"] == "-2.1"
    assert by_index["-"]["SRL-131_1150_C"] == "-15.8"
    assert by_index["a"]["FAS_1500_C"] is None
    assert table["values"]["candidate_tokens"] == ["valence_ratio", "interaction_parameter"]
    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 2
    assert all(row["values"]["numeric_points"] == "none" for row in figures)
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    counts = [[series["marker_count"] for series in row["values"]["series"]] for row in figures]
    assert counts == [[26, 0], [9, 10, 0]]


def test_table1_values_and_deferred_figures_match_render_audit() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table_and_figures(document)


def test_table_cell_mutation_breaks_render_pin() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table = document["species"]["SiO2"]["context"][0]
    table["values"]["table_rows"][0]["SRL-131_1150_C"] = "1.8"
    with pytest.raises(AssertionError):
        _assert_table_and_figures(document)


def test_independent_render_grid_mutation_breaks_extract_comparison() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed_render_grid = list(RENDER_GRID)
    changed_render_grid[0] = ("b", "Ni", "3-2", "1.8", None, "1.5")
    with pytest.raises(AssertionError):
        _assert_table_and_figures(document, changed_render_grid)


def test_isolated_migration_has_no_scored_potential_or_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert len(migrator.result.observations) == 0
