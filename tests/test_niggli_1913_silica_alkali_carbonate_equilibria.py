"""Migration pins for Niggli (1913) equilibrium data."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery import factories as battery_factories


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/niggli-1913-silica-alkali-carbonate-equilibria.yaml"
)
OBSERVATION_ID = "niggli_1913_table_ii_potassium_co2_loss"
SOLID_OBSERVATION_ID = "niggli_1913_table_ii_potassium_solid_reaction_endmember"


def _migrate():
    migrator = battery_factories.migrate_one_extract(
        ROOT, EXTRACT, index={}, aliases={}
    )
    migrator.finalize()
    return migrator


def _table_ii_rows(result):
    return [
        row
        for row in result.observations.values()
        if OBSERVATION_ID in row.observation_id or SOLID_OBSERVATION_ID in row.observation_id
    ]


def test_table_ii_render_pins_and_migrates_every_measured_co2_loss_point():
    """Pin rendered Table II values, row count, composition, and printed conditions."""
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    raw_observations = document["species"]["K2CO3"]["observations"]
    raw_points = raw_observations[0]["values"]["points"]
    solid_points = raw_observations[1]["values"]["points"]
    assert len(raw_points) + len(solid_points) == 30  # 18 charges, 30 numeric (a) cells.
    assert len(raw_points) == 29 and len(solid_points) == 1

    # PDF page 23 render: Table VI's soda-system displacement column is retained
    # as typed context because its printed molar basis has no exact quantity token.
    table_vi = document["species"]["Na2CO3"]["context"][0]["values"]["points"]
    assert len(table_vi) == 15  # 14 printed mixture rows; 15 measured cells.
    assert (table_vi[0]["row"], table_vi[0]["T_C"], table_vi[0]["co2_displaced_mol_per_100_mol_sio2_as_printed"]) == (
        "I",
        "898",
        "174",
    )
    assert (table_vi[2]["row"], table_vi[2]["T_C"], table_vi[2]["co2_displaced_mol_per_100_mol_sio2_as_printed"]) == (
        "II",
        "956",
        "182",
    )
    assert (table_vi[-1]["row"], table_vi[-1]["T_C"], table_vi[-1]["co2_displaced_mol_per_100_mol_sio2_as_printed"]) == (
        "XII",
        "956",
        "134",
    )
    assert all(point["locator"]["page"] == 23 for point in table_vi)
    assert all(point["locator"]["table"] == "VI" for point in table_vi)
    assert table_vi[0]["SiO2_mol_percent_as_printed"] == "7.1"

    # PDF page 8 render: Table II column (a), three different rows/temperatures.
    assert raw_points[0]["locator"]["page"] == 8
    assert (raw_points[0]["row"], raw_points[0]["T_C"], raw_points[0]["mass_loss_pct"]) == (
        "I",
        "898",
        "6.6",
    )
    assert (raw_points[10]["row"], raw_points[10]["T_C"], raw_points[10]["mass_loss_pct"]) == (
        "VII",
        "998",
        "28.6",
    )
    assert (raw_points[26]["row"], raw_points[26]["T_C"], raw_points[26]["mass_loss_pct"]) == (
        "XIII",
        "956",
        "79.3",
    )

    result = _migrate().result
    rows = _table_ii_rows(result)
    assert len(rows) == 30
    assert not result.validation.hard_issues
    assert all(row.value.kind.value != "unavailable" for row in rows)
    assert all(row.identity.quantity.is_value for row in rows)
    assert all(row.identity.quantity.value.value == "mass_loss_fraction" for row in rows)
    assert all(row.identity.species.phase.is_value for row in rows)

    expected = {
        (Decimal("0.069"), Decimal("1171.15")): Decimal("0.066"),
        (Decimal("0.238"), Decimal("1271.15")): Decimal("0.286"),
        (Decimal("0.526"), Decimal("1229.15")): Decimal("0.793"),
    }
    actual = {}
    for row in rows:
        assert row.identity.temperature_K.is_value
        assert row.identity.composition is not None and row.identity.composition.is_value
        composition = dict(row.identity.composition.value.components)
        assert "K2CO3" in composition and "SiO2" in composition
        key = (composition["SiO2"], row.identity.temperature_K.value)
        actual[key] = row.value.point

        conditions = row.point_conditions
        assert conditions["temperature_K"].state.is_value
        assert conditions["temperature_K"].locator.page == 8
        assert conditions["temperature_K"].locator.table == "II"
        assert conditions["composition"].state.is_value
        assert conditions["composition"].locator.page == 8
        assert conditions["CO2_partial_pressure_Pa"].state.value == Decimal("101325")
        assert conditions["CO2_partial_pressure_Pa"].locator.table == "II"
        assert conditions["fO2_Pa"].state.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown

    assert {key: actual[key] for key in expected} == expected
    assert not any(row.identity.quantity.is_unknown for row in result.observations.values())
    assert not any(row.identity.species.phase.is_unknown for row in result.observations.values())

    final_solid = next(
        row
        for row in rows
        if row.identity.temperature_K.value == Decimal("1271.15")
        and dict(row.identity.composition.value.components)["SiO2"] == Decimal("0.667")
    )
    assert final_solid.identity.species.phase.value.value == "cr"
