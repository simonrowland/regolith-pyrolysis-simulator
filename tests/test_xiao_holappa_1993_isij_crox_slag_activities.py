from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/xiao-holappa-1993-isij-crox-slag-activities.yaml"
)


def _assert_figure_inventory(document: dict) -> None:
    contexts = [
        row
        for species in document["species"].values()
        for row in species["context"]
    ]
    assert len(contexts) == 22
    assert {row["locator"]["figure"] for row in contexts} == {
        str(index) for index in range(1, 23)
    }
    assert all(
        row["values"]["digitisation"] == "deferred to a figure pass"
        for row in contexts
    )
    assert all(
        "marker_count" in series
        for row in contexts
        for series in row["values"]["series"]
    )
    assert all(not species["observations"] for species in document["species"].values())


def test_xiao_holappa_figures_are_deferred_and_unscored() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_figure_inventory(document)

    mutated = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated["species"]["CrO"]["context"].pop()
    with pytest.raises(AssertionError):
        _assert_figure_inventory(mutated)

    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert migrator.result.observations == {}
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 22
