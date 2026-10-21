"""Printed soda-flux measurements and deferred sensor figures."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kawashima-1984-soda-flux-chemical-sensors.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_printed_values(document):
    context = document["species"]["Na2O"]["context"]
    points = context[0]["values"]["printed_points"]
    assert len(points) == 8
    assert points[0]["activity_as_printed"] == "2 × 10^-5"
    assert points[4]["activity_as_printed"] == "2 × 10^-6"
    assert points[4]["fO2_Pa_as_printed"] == "1e-13"
    figures = [row for row in context if row["type"] == "figure_only"]
    assert [row["locator"]["figure"] for row in figures] == [str(n) for n in range(1, 8)]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert document["species"]["Na"]["context"][0]["values"]["values_as_printed"] == "6 × 10^4 Pa (0.6 atm)"


def test_printed_values_mutation_and_context_only_migration():
    document = _document()
    _assert_printed_values(document)
    changed = deepcopy(document)
    changed["species"]["Na2O"]["context"][0]["values"]["printed_points"][4]["activity_as_printed"] = "2 × 10^-5"
    with pytest.raises(AssertionError):
        _assert_printed_values(changed)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert not migrator.result.observations
