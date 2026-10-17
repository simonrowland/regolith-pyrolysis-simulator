"""Regression pins and isolated migration coverage for Liu et al. (2023)."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/liu-2023-chromium-oxide-activity.yaml"
SLAG_NUMBER_BY_TABLE_ROW = [1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 4, 4]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_render_pins(document: dict) -> None:
    """Values below were read from the page renders: PDF pp. 2, 3 and 5."""
    table1 = document["species"]["Cr"]["context"][0]["values"]["rows"]
    assert len(table1) == 4
    assert table1[0]["composition_mass_percent_as_published"]["CaF2"] == "4.98"

    table2 = document["species"]["Cr"]["observations"][0]["values"]["points"]
    assert len(table2) == 24
    assert table2[0]["composition_wt_pct"]["MoO2"] == "32.39"

    cro15 = document["species"]["CrO1.5"]["observations"]
    assert len(cro15) == 12
    row1 = cro15[0]["values"]["points"][0]
    row4 = cro15[3]["values"]["points"][0]
    assert row1["activity"] == 1.5519
    assert row1["value_as_printed"] == "1.5519"
    assert row4["activity"] == 3.261
    assert row4["value_as_printed"] == "3.2610"


def test_table_rows_and_render_pins():
    document = _document()
    _assert_render_pins(document)

    table2 = document["species"]["Cr"]["observations"][0]["values"]["points"]
    assert Counter(point["row"] for point in table2) == {
        str(row): 2 for row in range(1, 13)
    }
    for index, point in enumerate(table2):
        assert int(point["slag_number_as_published"]) == SLAG_NUMBER_BY_TABLE_ROW[index // 2]

    for species in ("Cr", "CrO1.5", "CrO"):
        observations = [
            item
            for item in document["species"][species]["observations"]
            if item["observation_id"].startswith("liu23_t3_a_")
        ]
        assert len(observations) == 12
        assert [int(item["values"]["points"][0]["row"]) for item in observations] == list(
            range(1, 13)
        )
        for index, item in enumerate(observations):
            point = item["values"]["points"][0]
            assert int(point["slag_number_as_published"]) == SLAG_NUMBER_BY_TABLE_ROW[index]
            assert point["value"] == point["activity"]

    assert len(document["species"]["CrOx"]["observations"][0]["values"]["points"]) == 12


def test_render_pin_detects_a_mutated_cell():
    mutated = deepcopy(_document())
    point = mutated["species"]["CrO1.5"]["observations"][3]["values"]["points"][0]
    point["activity"] = 3.262
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_real_migrator_receives_each_rows_conditions_and_lineage():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = tuple(
        observation
        for observation in result.observations.values()
        if observation.source_id == EXTRACT.stem
    )

    assert len(observations) == 72
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert sum(o.value.kind.value == "unavailable" for o in observations) == 36
    assert Counter(o.value.kind.value for o in observations) == {
        "point": 36,
        "unavailable": 36,
    }

    for observation in observations:
        assert observation.point_conditions
        assert all(
            condition.locator is not None
            for condition in observation.point_conditions.values()
        )
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1823")
        assert temperature.locator is not None

    table3 = [o for o in observations if o.locator is not None and o.locator.table == "3"]
    assert len(table3) == 48
    admitted = [o for o in table3 if o.admission.status.value == "admitted"]
    assert len(admitted) == 36

    representatives = {
        "Cr": next(o for o in admitted if o.identity.species.formula == "Cr"),
        "CrO1.5": next(o for o in admitted if o.identity.species.formula == "CrO1.5"),
        "CrO": next(o for o in admitted if o.identity.species.formula == "CrO"),
    }
    for observation in representatives.values():
        identity = observation.identity
        assert identity.temperature_K.is_value
        assert identity.temperature_K.value == Decimal("1823")
        assert identity.fO2_Pa.is_value
        assert identity.fO2_Pa.value > 0
        assert identity.total_pressure_Pa.is_unknown
    assert representatives["Cr"].identity.composition.is_unknown
    assert representatives["Cr"].point_conditions["composition"].state.is_unknown
    assert (
        "Cu-Cr alloy is not tabulated"
        in representatives["Cr"].point_conditions["composition"].state.reason
    )
    for species in ("CrO1.5", "CrO"):
        composition = representatives[species].identity.composition
        assert composition.is_unknown
        assert "unsupported numeric component(s)" in composition.reason

    for species in ("CrO1.5", "CrO"):
        rows = [
            o for o in admitted
            if o.identity.species.formula == species
        ]
        assert len(rows) == 12
        for observation in rows:
            assert len(observation.derived_from) == 1
            parent = result.observations[observation.derived_from[0]]
            assert parent.identity.species.formula == "Cr"
            assert parent.locator is not None
            parent_row = parent.observation_id.split("::")[1].rsplit("row", 1)[1]
            child_row = observation.observation_id.split("::")[1].rsplit("row", 1)[1]
            assert parent_row == child_row
            assert observation.derivation is not None
            assert observation.derivation.inputs == observation.derived_from
