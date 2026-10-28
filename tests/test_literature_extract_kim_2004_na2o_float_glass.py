from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kim-2004-na2o-float-glass-emf.yaml"

EXPECTED_ACTIVITY = {
    "kim_2004_table2_silicate_1268": ("6.745e-10", 3, "2"),
    "kim_2004_table2_silicate_1272": ("8.762e-10", 3, "2"),
    "kim_2004_table2_silicate_1300": ("1.028e-9", 3, "2"),
    "kim_2004_table2_silicate_1307": ("9.898e-10", 3, "2"),
    "kim_2004_table2_silicate_1325": ("1.286e-9", 3, "2"),
    "kim_2004_table2_silicate_1341": ("1.385e-9", 3, "2"),
    "kim_2004_table2_silicate_1348": ("1.548e-9", 3, "2"),
    "kim_2004_table2_silicate_1373": ("1.931e-9", 3, "2"),
    "kim_2004_table2_silicate_1375": ("1.834e-9", 3, "2"),
    "kim_2004_table3_glass_1296": ("1.76e-11", 4, "3"),
    "kim_2004_table3_glass_1296_lowering": ("1.81e-11", 4, "3"),
    "kim_2004_table3_glass_1302": ("1.81e-11", 4, "3"),
    "kim_2004_table3_glass_1325": ("2.21e-11", 4, "3"),
    "kim_2004_table3_glass_1325_lowering": ("2.27e-11", 4, "3"),
    "kim_2004_table3_glass_1333": ("2.38e-11", 4, "3"),
    "kim_2004_table3_glass_1348": ("2.69e-11", 4, "3"),
    "kim_2004_table3_glass_1348_lowering": ("2.86e-11", 4, "3"),
    "kim_2004_table3_glass_1354": ("2.80e-11", 4, "3"),
    "kim_2004_table3_glass_1365": ("3.10e-11", 4, "3"),
    "kim_2004_table3_glass_1365_lowering": ("3.14e-11", 4, "3"),
    "kim_2004_table3_glass_1370": ("3.19e-11", 4, "3"),
}


def _assert_kim_2004_pins(document: dict) -> None:
    rows = {
        row["observation_id"]: row
        for row in document["species"]["Na2O"]["observations"]
        if row["values"].get("quantity") == "activity"
    }
    assert len(rows) == len(EXPECTED_ACTIVITY) == 21
    for observation_id, (activity, page, table) in EXPECTED_ACTIVITY.items():
        row = rows[observation_id]
        point = row["values"]["points"][0]
        assert point["activity"] == activity
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table


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

    validation_errors = validate_extract_file(EXTRACT)
    assert len(validation_errors) == 21
    assert all("got 'electromotive_force'" in error for error in validation_errors)

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
        assert row.identity.reference_state.is_unknown
        assert row.point_conditions["fO2_Pa"].state.value == Decimal("101325")

    binary = by_id["kim_2004_table2_silicate_1268"]
    assert binary.identity.composition.is_value
    assert dict(binary.identity.composition.value.components) == {
        "SiO2": Decimal("0.657"), "Na2O": Decimal("0.343")
    }
    glass = by_id["kim_2004_table3_glass_1296"]
    assert glass.identity.composition.is_value
    assert dict(glass.identity.composition.value.components) == {
        "SiO2": Decimal("0.70175"),
        "Na2O": Decimal("0.13190"),
        "CaO": Decimal("0.10046"),
        "MgO": Decimal("0.05852"),
        "Al2O3": Decimal("0.00319"),
        "SO2": Decimal("0.00242"),
        "Fe2O3": Decimal("0.00035"),
        "Cl": Decimal("0.00106"),
        "TiO2": Decimal("0.00035"),
    }
    table1 = migrator.result.context_by_work
    rows = next(iter(table1.values()))[0]["values"]["rows_as_printed"]
    assert len(rows) == 9
    assert rows[5] == {"component": "SO2", "content_m_o": "0.242"}
    assert rows[7] == {"component": "Cl", "content_m_o": "0.106"}
