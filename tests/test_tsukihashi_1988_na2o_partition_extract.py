"""Prose partition endpoints and deferred figure coverage for Tsukihashi et al. (1988)."""

import yaml
from simulator.battery.migrate import Migrator, REPO_ROOT

EXTRACT = REPO_ROOT / "data/literature/extracts/tsukihashi-1988-na2o-v-nb-mn-ti-partition.yaml"


def test_four_printed_partition_endpoint_pairs_are_preserved_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected = {"V": ("0.4", "5"), "Nb": ("6", "100"), "Ti": ("100", "300"), "Mn": ("2", "0.7")}
    for species, pair in expected.items():
        record = next(row for row in document["species"][species]["context"] if row["observation_id"].endswith("_partition_endpoints_text"))
        assert (record["values"]["start_value_as_printed"], record["values"]["end_value_as_printed"]) == pair
        assert record["locator"]["page"] == record["locator"]["pdf_page_index"] + 1 == 2


def test_isolated_migration_and_all_figures_are_deferred_without_points() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.observations

    figures = [row for body in document["species"].values() for row in body["context"] if row.get("type") == "figure_record"]
    assert {row["locator"]["figure"] for row in figures} == {str(n) for n in range(1, 11)}
    assert all(row["numeric_points"] == "none" and row["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["series"] and all(isinstance(series["marker_count"], int) for series in row["series"]) for row in figures)
