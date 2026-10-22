from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/piacente-matousek-1973-sodium-disilicate-kems.yaml"
SOURCE_ID = "piacente-matousek-1973-sodium-disilicate-kems"


def _migrated_at(migrated: dict, source_observation_id: str, temperature: str):
    prefix = f"{SOURCE_ID}::{source_observation_id}::T={temperature}:"
    matches = [row for key, row in migrated.items() if key.startswith(prefix)]
    assert len(matches) == 1
    return matches[0]


def test_piacente_matousek_1973_table_rows_and_render_pins() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = [
        (observation, point)
        for observation in extract["species"]["Na"]["observations"]
        if observation["type"] == "partial_pressure"
        for point in observation["values"]["points"]
    ]
    assert len(source_rows) == 80  # Tables I–IV print 41 + 24 + 7 + 8 rows.
    expected_cell_materials = {
        "piacente_1973_run_1_0_partial_pressure": "Mo",
        "piacente_1973_run_2_1_partial_pressure": "Mo",
        "piacente_1973_run_2_2_partial_pressure": "Mo",
        "piacente_1973_run_2_3_partial_pressure": "Mo",
        "piacente_1973_run_3_0_partial_pressure": "MgO",
        "piacente_1973_run_3_1_partial_pressure": "MgO",
        "piacente_1973_run_4_0_partial_pressure": "Re",
        "piacente_1973_run_5_0_partial_pressure": "Ta",
    }
    pressure_series = {
        row["observation_id"]: row
        for row in extract["species"]["Na"]["observations"]
        if row["type"] == "partial_pressure"
    }
    assert {
        observation_id: row["equipment"]["cell_material_and_liner"]["value"]
        for observation_id, row in pressure_series.items()
    } == expected_cell_materials

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 86
    assert not result.validation.hard_issues

    pressure_rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.value is Quantity.P_PARTIAL
    ]
    assert len(pressure_rows) == 80
    assert all(row.value.kind.value == "point" for row in pressure_rows)
    assert all(row.identity.species.formula == "Na" for row in pressure_rows)
    assert all(row.point_conditions["temperature_K"].state.is_value for row in pressure_rows)
    assert all(row.point_conditions["composition"].state.is_value for row in pressure_rows)
    assert all(row.identity.temperature_K.is_value for row in pressure_rows)
    assert all(row.identity.composition.is_value for row in pressure_rows)

    # The page-4 and page-7 render pins are the printed coefficients in Tables I, II, IV.
    pins = (
        ("piacente_1973_run_2_1_partial_pressure", "1490", Decimal("0.91901775")),
        ("piacente_1973_run_3_1_partial_pressure", "1430", Decimal("0.332042025")),
        ("piacente_1973_run_5_0_partial_pressure", "1523", Decimal("0.123413850")),
        ("piacente_1973_run_5_0_partial_pressure", "1546", Decimal("0.229703775")),
    )
    for observation_id, temperature, expected_pa in pins:
        row = _migrated_at(result.observations, observation_id, temperature)
        assert row.value.point == expected_pa

    for observation_id, temperature in (
        ("piacente_1973_run_2_1_partial_pressure", "1490"),
        ("piacente_1973_run_3_1_partial_pressure", "1430"),
        ("piacente_1973_run_5_0_partial_pressure", "1523"),
    ):
        identity = _migrated_at(result.observations, observation_id, temperature).identity
        assert identity.temperature_K.value == Decimal(temperature)
        assert identity.composition.is_value
        assert identity.total_pressure_Pa.is_unknown
        assert identity.fO2_Pa.is_unknown

    # The fitted values are retained in the source records; the current v2 migrator
    # recognizes their quantity but has no selected source field for its value.
    fits = [row for row in result.observations.values() if row.identity.quantity.value is Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW]
    assert len(fits) == 6
    assert all(row.value.kind.value == "unavailable" for row in fits)
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 6

    source_fits = {
        row["observation_id"]: row["values"]
        for row in extract["species"]["Na"]["observations"]
        if row["values"].get("quantity") == "second_law_enthalpy_of_vaporization"
    }
    assert source_fits["piacente_1973_mo_enthalpy_fit"]["derived_from"] == [
        "piacente_1973_run_1_0_partial_pressure",
        "piacente_1973_run_2_1_partial_pressure",
        "piacente_1973_run_2_2_partial_pressure",
        "piacente_1973_run_2_3_partial_pressure",
    ]
    assert source_fits["piacente_1973_mgo_enthalpy_fit"]["derived_from"] == [
        "piacente_1973_run_3_0_partial_pressure",
        "piacente_1973_run_3_1_partial_pressure",
    ]
    assert source_fits["piacente_1973_re_enthalpy_fit"]["derived_from"] == [
        "piacente_1973_run_4_0_partial_pressure"
    ]
    assert source_fits["piacente_1973_ta_enthalpy_fit"]["derived_from"] == [
        "piacente_1973_run_5_0_partial_pressure"
    ]
    assert all(
        fit["derived_from"] == list(source_fits)[:4]
        for fit_id, fit in source_fits.items()
        if fit_id.startswith("piacente_1973_four_cell_mean_")
    )
