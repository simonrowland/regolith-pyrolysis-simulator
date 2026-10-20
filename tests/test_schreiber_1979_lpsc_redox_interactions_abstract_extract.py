from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schreiber-1979-lpsc-redox-interactions-abstract.yaml"


def _assert_context_only(document: dict) -> None:
    rows = document["species"]["Ti"]["context"]
    figures = [row for row in rows if row["type"] == "figure_record"]
    assert len(figures) == 1
    figure = figures[0]
    assert figure["locator"] == {
        "page": 2,
        "pdf_page_index": 1,
        "published_page": 1068,
        "figure": "1",
    }
    assert figure["values"]["numeric_points"] == "none"
    assert figure["values"]["digitisation"] == "deferred to a figure pass"
    estimates = next(
        row for row in rows
        if row["observation_id"] == "schreiber_1979_prior_lunar_redox_estimates"
    )
    assert estimates["values"]["estimates_as_printed"] == {
        "Cr(II)": "90% of total Cr",
        "Eu(II)": "70% of total Eu",
        "Ti(III)": "4% of total Ti",
    }


def test_abstract_keeps_figure_points_deferred_and_estimates_in_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_context_only(document)


def test_context_pin_fails_after_estimate_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    estimates = next(
        row for row in document["species"]["Ti"]["context"]
        if row["observation_id"] == "schreiber_1979_prior_lunar_redox_estimates"
    )
    estimates["values"]["estimates_as_printed"]["Cr(II)"] = "91% of total Cr"
    with pytest.raises(AssertionError):
        _assert_context_only(document)


def test_isolated_migration_has_no_scored_observations_or_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert len(migrator.result.observations) == 0

