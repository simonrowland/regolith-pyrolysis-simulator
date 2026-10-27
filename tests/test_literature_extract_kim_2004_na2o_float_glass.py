from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, StateTag, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kim-2004-na2o-float-glass-emf.yaml"


def _assert_kim_2004_pins(document: dict) -> None:
    rows = {
        row["observation_id"]: row["values"]["points"][0]
        for row in document["species"]["Na2O"]["observations"]
        if row["values"].get("quantity") == "activity"
    }
    assert len(rows) == 21
    assert rows["kim_2004_table2_silicate_1268"]["activity"] == "6.745e-10"
    assert rows["kim_2004_table3_glass_1296"]["activity"] == "1.76e-11"
    assert rows["kim_2004_table3_glass_1370"]["activity"] == "3.19e-11"


def test_kim_2004_tables_migrate_with_render_pins_and_bound_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    _assert_kim_2004_pins(document)
    mutated = deepcopy(document)
    mutated["species"]["Na2O"]["observations"][1]["values"]["points"][0]["activity"] = "9.99e-10"
    try:
        _assert_kim_2004_pins(mutated)
    except AssertionError:
        pass
    else:
        raise AssertionError("a changed render-pinned activity did not fail")

    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    observations = list(migrator.result.observations.values())
    activity_rows = [
        row for row in observations
        if row.identity.quantity.is_value and row.identity.quantity.value is Quantity.ACTIVITY
    ]
    emf_rows = [row for row in observations if row.value.kind is ValueKind.UNAVAILABLE]
    assert len(activity_rows) == 21
    assert len(emf_rows) == 21
    assert len(migrator.result.validation.hard_issues) == 0

    by_id = {
        row.observation_id.split("::", 1)[1].split("::T=", 1)[0]: row
        for row in activity_rows
    }
    expected = {
        "kim_2004_table2_silicate_1268": ("6.745e-10", 1268, "2"),
        "kim_2004_table3_glass_1296": ("1.76e-11", 1296, "3"),
        "kim_2004_table3_glass_1370": ("3.19e-11", 1370, "3"),
    }
    for observation_id, (value, temperature, table) in expected.items():
        row = by_id[observation_id]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(value)
        assert row.locator.page == (3 if table == "2" else 4)
        assert row.locator.table == table
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.point_conditions["temperature_K"].state.value == Decimal(temperature)
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        assert row.identity.fO2_Pa.is_value
        assert row.identity.fO2_Pa.value == Decimal("101325")
        assert row.point_conditions["fO2_Pa"].state.value == Decimal("101325")

    binary = by_id["kim_2004_table2_silicate_1268"]
    assert binary.identity.composition.is_value
    assert dict(binary.identity.composition.value.components) == {
        "SiO2": Decimal("0.657"), "Na2O": Decimal("0.343")
    }
    glass = by_id["kim_2004_table3_glass_1296"]
    assert glass.identity.composition.tag is StateTag.UNKNOWN
    table1 = migrator.result.context_by_work
    rows = next(iter(table1.values()))[0]["values"]["rows_as_printed"]
    assert len(rows) == 9
    assert rows[5] == {"component": "SO2", "content_m_o": "0.242"}
    assert rows[7] == {"component": "Cl", "content_m_o": "0.106"}
