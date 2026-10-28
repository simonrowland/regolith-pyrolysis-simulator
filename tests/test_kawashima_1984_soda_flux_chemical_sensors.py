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
    assert len(points) == 10
    assert [point["activity_as_printed"] for point in points[:2]] == ["1 × 10^-5", "1 × 10^-5"]
    assert points[0]["initial_metal_P_mass_percent"] == "0.50"
    assert "time_s" not in points[0]
    assert "fO2_Pa" not in points[0]["point_conditions"]
    assert points[1]["point_conditions"]["fO2_Pa"]["state"]["value"] == "1e-7"
    assert points[6]["activity_as_printed"] == "2 × 10^-6"
    assert points[6]["point_conditions"]["fO2_Pa"]["state"]["value"] == "1e-13"
    assert points[9]["point_conditions"]["fO2_Pa"]["locator"]["figure"] == "6"
    assert points[9]["point_conditions"]["fO2_Pa"]["locator"] == {
        "page": 5, "published_page": 783, "pdf_page_index": 4, "figure": "6"
    }
    assert points[9]["point_conditions"]["composition"]["locator"] == {
        "page": 5, "published_page": 783, "pdf_page_index": 4, "figure": "6"
    }
    assert "equation (3)" in observation["derivation"]["relation"]
    assert "equation (5)" not in observation["derivation"]["relation"]
    assert "a pure-liquid standard is not printed" in observation["standard_state_as_printed"]
    assert observation["values"]["quantity"] == "activity"
    assert observation["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
    context = species["context"]
    figures = [row for row in context if row["type"] == "figure_only"]
    assert [row["locator"]["figure"] for row in figures] == [str(n) for n in range(1, 8)]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert "pNa denotes sodium partial pressure" in document["extraction"]["method"]
    pressures = document["species"]["Na"]["observations"]
    assert [row["values"]["points"][0]["pressure_as_printed"] for row in pressures] == ["6 × 10^4 Pa (0.6 atm)", "1 × 10^3 Pa"]
    assert [row["values"]["points"][0]["pressure_Pa"] for row in pressures] == ["6e4", "1e3"]
    assert all(row["values"]["method_class"] == "measured_reduced" for row in pressures)
    assert all(
        row["values"]["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
        for row in pressures
    )
    assert all(
        row["values"]["derivation"]["raw_inputs"]["locator"]["section"]
        == "Sodium vapour pressure measurement cell"
        for row in pressures
    )
    assert [row["locator"]["figure"] for row in pressures] == ["5", "7"]


def test_printed_values_mutation_and_migration():
    document = _document()
    _assert_printed_values(document)
    changed = deepcopy(document)
    changed["species"]["Na2O"]["observations"][0]["values"]["points"][6]["activity_as_printed"] = "2 × 10^-5"
    with pytest.raises(AssertionError):
        _assert_printed_values(changed)
    changed = deepcopy(document)
    changed["species"]["Na"]["observations"][0]["values"]["points"][0]["pressure_Pa"] = "6e3"
    with pytest.raises(AssertionError):
        _assert_printed_values(changed)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    observations = list(migrator.result.observations.values())
    activities = [row for row in observations if row.identity.quantity.value == "activity"]
    assert len(activities) == 10
    assert [row.value.point for row in activities] == [
        Decimal("1e-5"), Decimal("1e-5"),
        *[Decimal(value) for value in ("2e-5", "2e-5", "3e-4", "2e-4", "2e-6", "3e-5", "3e-4", "1e-7")],
    ]
    assert all(row.value.kind is ValueKind.POINT for row in observations)
    assert all(row.value.kind is not ValueKind.UNAVAILABLE for row in observations)
    assert all(row.identity.species.formula == "Na2O" for row in activities)
    assert all(row.point_conditions["temperature_K"].state.value == Decimal("1573") for row in observations)
    assert "fO2_Pa" not in activities[0].point_conditions
    assert activities[1].point_conditions["fO2_Pa"].state.value == Decimal("1e-7")
    assert activities[2].point_conditions["fO2_Pa"].state.value == Decimal("3e-10")
    pressures = [row for row in observations if row.identity.quantity.value == "p_partial"]
    assert [row.value.point for row in pressures] == [Decimal("6e4"), Decimal("1e3")]
    assert all(row.identity.species.formula == "Na" for row in pressures)
    assert all(row.evidence.original_method_class == "measured_reduced" for row in pressures)
