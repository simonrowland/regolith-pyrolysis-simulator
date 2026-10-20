"""Printed Ichise 1982 Table 2 activities the migrator used to leave UNAVAILABLE (b-981).

Expected amounts are the MinerU decode of Table 2 (published p. 555), not a copy of the extract YAML.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import select_declared_source
from simulator.yaml_cache import load_cached_safe_yaml
from tests.battery.test_migrate import _write_min_tree
from tests.battery.test_printed_point_conditions import (
    _EXTRACTS,
    _migrate_real_extract,
)

# Ichise, Maruo, Sasho, Ueshima and Mori, Trans. ISIJ 22 (1982) 552.
# Table 2, "Result of activity measurements at 1 823 K."
# (x_Mo, a_Fe) as printed. The starred 0.528 is still a Table 2 cell.
_ICHISE_TABLE2 = (
    (Decimal("0.096"), Decimal("0.897")),
    (Decimal("0.154"), Decimal("0.810")),
    (Decimal("0.200"), Decimal("0.800")),
    (Decimal("0.201"), Decimal("0.767")),
    (Decimal("0.240"), Decimal("0.692")),
    (Decimal("0.251"), Decimal("0.737")),
    (Decimal("0.254"), Decimal("0.741")),
    (Decimal("0.300"), Decimal("0.690")),
    (Decimal("0.302"), Decimal("0.673")),
    (Decimal("0.333"), Decimal("0.677")),
    (Decimal("0.339"), Decimal("0.652")),
    (Decimal("0.348"), Decimal("0.652")),
    (Decimal("0.434"), Decimal("0.621")),
    (Decimal("0.434"), Decimal("0.656")),
    (Decimal("0.434"), Decimal("0.528")),
    (Decimal("0.556"), Decimal("0.585")),
    (Decimal("0.613"), Decimal("0.552")),
    (Decimal("0.666"), Decimal("0.526")),
    (Decimal("0.778"), Decimal("0.556")),
    (Decimal("0.793"), Decimal("0.538")),
    (Decimal("0.842"), Decimal("0.518")),
    (Decimal("0.912"), Decimal("0.360")),
    (Decimal("0.912"), Decimal("0.383")),
)


def _ichise_rows(result):
    prefix = "kems-111-ichise-1982::ichise_1982_table2_a_Fe_1823K::"
    return [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(prefix)
    ]


def _mo_amount(obs) -> Decimal:
    located = obs.point_conditions["composition"]
    assert located.state.is_value
    return dict(located.state.value.components)["Mo"]


def _pair(obs) -> tuple[Decimal, Decimal]:
    assert obs.value.kind is ValueKind.POINT
    return (_mo_amount(obs), obs.value.point)


def test_ichise_table2_activities_are_points_at_printed_values(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-111-ichise-1982")
    rows = _ichise_rows(result)
    assert len(rows) == 23
    assert sorted(_pair(row) for row in rows) == sorted(_ICHISE_TABLE2)
    for row in rows:
        assert row.identity.species.formula == "Fe"
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1823")
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1823")
        assert temperature.locator is not None
        assert temperature.locator.table == "2"
        composition = row.point_conditions["composition"]
        assert composition.locator is not None
        assert composition.locator.table == "2"
        assert row.point_conditions["fO2_Pa"].state.is_unknown
        assert row.point_conditions["total_pressure_Pa"].state.is_unknown
        assert row.value.approximate is False


def test_ichise_bare_a_fe_column_stays_unavailable_and_activity_is_the_point() -> None:
    """The reader takes `activity`. A bare a_Fe cell is not that field."""
    units = "dimensionless a_Fe"
    bare = select_declared_source(
        Quantity.ACTIVITY,
        units,
        {"no": "Fe-Mo 6", "x_Mo": "0.096", "a_Fe": "0.897"},
    )
    assert bare.value.kind is ValueKind.UNAVAILABLE
    taken = select_declared_source(
        Quantity.ACTIVITY,
        units,
        {"no": "Fe-Mo 6", "x_Mo": "0.096", "activity": "0.897"},
    )
    assert taken.value.kind is ValueKind.POINT
    assert taken.amount == Decimal("0.897")
    both = select_declared_source(
        Quantity.ACTIVITY,
        units,
        {"a_Fe": "0.897", "a_Mo": "0.2"},
    )
    assert both.value.kind is ValueKind.UNAVAILABLE
    quoted_shape = select_declared_source(
        Quantity.ACTIVITY,
        units,
        {"a_Fe": "0.865", "W_at_percent": "0.345"},
    )
    assert quoted_shape.value.kind is ValueKind.UNAVAILABLE


def test_renaming_ichise_activity_back_to_a_fe_drops_the_points(tmp_path: Path) -> None:
    path = _EXTRACTS / "kems-111-ichise-1982.yaml"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    table = next(
        obs
        for obs in doc["species"]["Fe"]["observations"]
        if obs["observation_id"] == "ichise_1982_table2_a_Fe_1823K"
    )
    for row in table["values"]["rows"]:
        row["a_Fe"] = row.pop("activity")
    result = _migrate_real_extract_doc(tmp_path, doc)
    rows = _ichise_rows(result)
    assert len(rows) == 23
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in rows)


def _migrate_real_extract_doc(tmp_path: Path, doc: dict):
    from simulator.battery.migrate import migrate

    root = _write_min_tree(tmp_path, doc)
    return migrate(root, write=False)
