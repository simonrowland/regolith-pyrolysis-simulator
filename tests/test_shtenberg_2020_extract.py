from decimal import Decimal
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shtenberg-2020-alkaline-oxide-thermodynamic-regression.yaml"
SOURCE_ID = "shtenberg-2020-alkaline-oxide-thermodynamic-regression"


def _migrate():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def _assert_render_pin(document, oid, page, table, expected):
    source_rows = {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
    }
    point = source_rows[oid]["values"]["points"][0]
    assert str(point["value"]) == expected
    assert point["locator"]["page"] == page
    assert point["locator"]["table"] == table


def test_shtenberg_tables_and_render_pins_survive_isolated_migration():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert len(document["species"]["K2B4O7"]["context"]) == 2
    assert sum(len(row.get("observations", [])) for row in document["species"].values()) == 47

    table_rows = {}
    context_rows = []
    for block in document["species"].values():
        for row in block.get("observations", []):
            table_rows[row["observation_id"]] = row
        context_rows.extend(block.get("context", []))
    assert sum(row["locator"]["table"] == "1" for row in table_rows.values()) == 24
    assert sum(row["locator"]["table"] == "2" for row in table_rows.values()) == 21
    assert sum(row["locator"]["table"] == "4" for row in table_rows.values()) == 2
    assert sum(row["locator"]["table"] == "1" for row in context_rows) == 24
    assert sum(row["locator"]["table"] == "2" for row in context_rows) == 24
    assert sum(row["locator"]["table"] == "3" for row in context_rows) == 16
    assert sum(row["locator"]["table"] == "4" for row in context_rows) == 2
    render_pins = (
        ("shtenberg20_table1_K2B4O7_reference_cp", 3, "1", "170.5"),
        ("shtenberg20_table1_Li2B8O13_reference_cp", 3, "1", "325.5"),
        ("shtenberg20_table2_K2Si2O5_reference_delta_fH", 4, "2", "-2496.0"),
        ("shtenberg20_table4_K2B2Ge3O10_reference_delta_fH", 6, "4", "-3937.1"),
    )
    for oid, page, table, expected in render_pins:
        _assert_render_pin(document, oid, page, table, expected)

    result = _migrate().result
    assert result.validation is not None
    assert not result.validation.hard_issues
    migrated = [row for row in result.observations.values() if row.source_id == SOURCE_ID]
    assert len(migrated) == 47
    assert all(row.value.kind is ValueKind.POINT for row in migrated)
    assert all(row.identity.quantity.value in {Quantity.CP, Quantity.DELTA_FH} for row in migrated)
    assert all(row.identity.species.phase.value is Phase.CR for row in migrated)
    quantity_unknown = {
        row.observation_id for row in migrated if not row.identity.quantity.is_value
    }
    assert quantity_unknown == set()
    phase_unknown = {
        row.observation_id
        for row in migrated
        if not row.identity.species.phase.is_value
    }
    assert phase_unknown == set()
    assert all(row.identity.temperature_K.value == Decimal("298.15") for row in migrated)
    assert not any(row.value.kind is ValueKind.UNAVAILABLE for row in migrated)

    by_id = {
        next(part for part in row.observation_id.split("::") if part.startswith("shtenberg20_")): row
        for row in migrated
    }
    assert by_id[render_pins[0][0]].value.point == Decimal("170.5")
    assert by_id[render_pins[2][0]].value.point == Decimal("-2496.0")
    assert by_id[render_pins[3][0]].value.point == Decimal("-3937.1")
    assert by_id[render_pins[0][0]].uncertainty.verbatim["value"] == 1.7
    assert by_id[render_pins[0][0]].uncertainty.verbatim["confidence_level"] == "not_stated"
    assert all(
        axis is None or not axis.is_value
        for row in migrated
        for axis in (
            row.identity.composition,
            row.identity.total_pressure_Pa,
            row.identity.fO2_Pa,
        )
    )


def test_shtenberg_render_pin_fails_when_a_transcribed_cell_is_mutated():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed = deepcopy(document)
    row = next(
        row
        for row in changed["species"]["K2B4O7"]["observations"]
        if row["observation_id"] == "shtenberg20_table1_K2B4O7_reference_cp"
    )
    row["values"]["points"][0]["value"] = 170.6
    with pytest.raises(AssertionError):
        _assert_render_pin(
            changed,
            "shtenberg20_table1_K2B4O7_reference_cp",
            3,
            "1",
            "170.5",
        )
