"""Printed soda-flux measurements and deferred sensor figures."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from simulator.battery.records import ValueKind

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kawashima-1984-soda-flux-chemical-sensors.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_printed_values(document):
    species = document["species"]["Na2O"]
    observation = species["observations"][0]
    points = observation["values"]["points"]
    assert len(points) == 8
    assert points[0]["activity_as_printed"] == "2 × 10^-5"
    assert points[4]["activity_as_printed"] == "2 × 10^-6"
    assert points[4]["point_conditions"]["fO2_Pa"]["state"]["value"] == "1e-13"
    assert observation["values"]["quantity"] == "activity"
    assert observation["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
    context = species["context"]
    figures = [row for row in context if row["type"] == "figure_only"]
    assert [row["locator"]["figure"] for row in figures] == [str(n) for n in range(1, 8)]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert "pNa denotes sodium partial pressure" in document["extraction"]["method"]
    assert document["species"]["Na"]["context"][0]["values"]["values_as_printed"] == "6 × 10^4 Pa (0.6 atm)"


def test_printed_values_mutation_and_migration():
    document = _document()
    _assert_printed_values(document)
    changed = deepcopy(document)
    changed["species"]["Na2O"]["observations"][0]["values"]["points"][4]["activity_as_printed"] = "2 × 10^-5"
    with pytest.raises(AssertionError):
        _assert_printed_values(changed)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    observations = list(migrator.result.observations.values())
    assert len(observations) == 8
    assert [row.value.point for row in observations] == [
        Decimal(value) for value in ("2e-5", "2e-5", "3e-4", "2e-4", "2e-6", "3e-5", "3e-4", "1e-7")
    ]
    assert all(row.value.kind is ValueKind.POINT for row in observations)
    assert all(row.identity.species.formula == "Na2O" for row in observations)
    assert all(row.identity.temperature_K.value == 1573 for row in observations)
    assert observations[0].point_conditions["fO2_Pa"].state.value == Decimal("3e-10")
