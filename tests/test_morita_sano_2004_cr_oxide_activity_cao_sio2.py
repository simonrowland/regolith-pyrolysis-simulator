"""Metadata and migration guard for Morita & Sano (2004)."""

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/morita-sano-2004-cr-oxide-activity-cao-sio2.yaml"


def _assert_figure_manifest(document):
    figures = document["species"]["CrOx"]["context"]
    assert [record["locator"]["figure"] for record in figures[:12]] == [str(n) for n in range(1, 13)]
    assert all(record["type"] == "figure_only" for record in figures[:12])
    assert all(record["values"]["digitisation"] == "deferred to a figure pass" for record in figures[:12])
    assert all("points" not in record["values"] for record in figures[:12])
    assert figures[4]["locator"]["page"] == 3


def test_figures_are_deferred_and_migration_has_no_scored_observations():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    figures = document["species"]["CrOx"]["context"]
    _assert_figure_manifest(document)
    assert figures[4]["values"]["caption_as_printed"].endswith("atm.")

    mutated = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated["species"]["CrOx"]["context"][4]["locator"]["page"] = 4
    with pytest.raises(AssertionError):
        _assert_figure_manifest(mutated)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert not migrator.result.observations
