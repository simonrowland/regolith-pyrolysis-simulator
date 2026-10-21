"""Quoted-review context and deferred figure manifest for Holappa & Xiao."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/holappa-xiao-2004-ferroalloy-slag-review.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_capture(document):
    chromium = document["species"]["CrOx"]["context"]
    manganese = document["species"]["MnO"]["context"]
    assert [row["locator"]["figure"] for row in chromium if row["type"] == "figure_only"] == ["1", "2", "3", "4", "5", "6", "10"]
    assert [row["locator"]["figure"] for row in manganese if row["type"] == "figure_only"] == ["7", "8", "9"]
    assert "10^-14 atm at 1600 °C" in manganese[0]["values"]["quoted_values"][0]["value_as_printed"]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in chromium + manganese if row["type"] == "figure_only")


def test_review_quotes_mutation_and_context_only_migration():
    document = _document()
    _assert_capture(document)
    mutated = deepcopy(document)
    mutated["species"]["MnO"]["context"][0]["values"]["quoted_values"][0]["value_as_printed"] = "10^-13 atm at 1600 °C"
    with pytest.raises(AssertionError):
        _assert_capture(mutated)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert not migrator.result.observations
