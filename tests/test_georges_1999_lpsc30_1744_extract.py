"""Source pins for Georges et al. (1999), LPSC abstract 1744."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/georges-1999-lpsc30-1744.yaml"


def test_printed_results_and_deferred_figures_are_retained() -> None:
    data = yaml.safe_load(EXTRACT.read_text())
    context = data["species"]["K2O"]["context"]
    results = context[0]["values"]["printed_values"]
    assert "6.0 ± 0.5 wt%" in results["experiment_1_result"]
    assert "11.2 wt%" in results["experiment_2_result"]
    assert len([row for row in context if row["type"] == "figure_only"]) == 2
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in context[1:])
    assert [s["marker_count"] for s in context[1]["values"]["series"]] == [7, 5, 0]
    assert [s["marker_count"] for s in context[2]["values"]["series"]] == [5, 10]


def test_isolated_migration_has_no_hard_issues_or_unsupported_points() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.observations
