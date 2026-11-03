from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator, to_plain


ROOT = Path(__file__).resolve().parents[1]
SID = "hayashi-2025-ca-fe-silicate-redox-activities"
SLUG = "hayashi_2025_ca_fe_silicate_redox_activities"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"


def _obs_id(row: int, column: int) -> str:
    return f"{SLUG}_p003_t01_r{row:02d}_c{column:02d}"


def _load_and_migrate() -> tuple[dict, Migrator, list]:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = [
        row
        for row in migrator.result.observations.values()
        if row.source_id == SID
    ]
    return document, migrator, observations


def test_hayashi_table_1_row_count_and_render_pins() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]
    observations = {
        (formula, row["observation_id"]): row
        for formula, block in species.items()
        for row in block.get("observations", [])
    }
    sample_count = len({
        row["values"]["sample_as_published"]
        for row in observations.values()
    })
    assert sample_count == 14

    render_pins = (
        ("Fe", _obs_id(4, 3), "78.3", "1(a)"),
        ("FeO", _obs_id(7, 12), ".101", "1(d)"),
        ("FeO1.5", _obs_id(17, 15), "5.42", "1(e)"),
        ("FeO", _obs_id(16, 12), ".564", "1(d)"),
    )
    for formula, observation_id, value, table in render_pins:
        row = observations[(formula, observation_id)]
        field = "value" if formula == "Fe" else "activity" if table == "1(d)" else "gamma"
        assert row["values"][field] == value
        assert row["locator"]["page"] == 3
        assert row["locator"]["table"] == table

    assert observations[("Fe", _obs_id(4, 3))]["values"]["oxygen_partial_pressure"] == {
        "value": "10^-9",
        "units": "atm",
        "as_printed": "10^-9 atm",
        "locator": observations[("Fe", _obs_id(4, 3))]["values"]["oxygen_partial_pressure"]["locator"],
    }
    e2_composition = observations[("FeO1.5", _obs_id(17, 15))]["values"]["composition_as_published"]["components"]
    assert sum(Decimal(value) for value in e2_composition.values()) == Decimal("100.5")


def test_hayashi_isolated_migration_binds_conditions_and_only_declared_schema_gaps() -> None:
    document, migrator, observations = _load_and_migrate()
    assert len(observations) == 65
    assert migrator.result.validation.hard_issues == ()

    unknown_quantity_ids = {
        row.observation_id.split("::", 1)[1]
        for row in observations
        if row.identity.quantity.is_unknown
    }
    expected_unknown_quantity_ids = {
        _obs_id(table_row, column)
        for table_row in (4, 5, 6, 7, 8, 11, 16)
        for column in (4, 5)
    }
    assert unknown_quantity_ids == expected_unknown_quantity_ids
    assert {
        row.observation_id.split("::", 1)[1]
        for row in observations
        if row.value.kind.value == "unavailable"
    } == expected_unknown_quantity_ids
    assert all(row.identity.species is not None for row in observations)
    assert all(not row.identity.species.phase.is_unknown for row in observations)

    source_rows = {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
    }
    for row in source_rows.values():
        conditions = row["point_conditions"]
        assert conditions["temperature_K"]["state"]["tag"] == "value"
        assert conditions["fO2_Pa"]["state"]["tag"] == "value"
        assert conditions["composition"]["state"]["tag"] == "value"
        assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"

    migrated_by_id = {
        row.observation_id.split("::", 1)[1]: row for row in observations
    }
    for observation_id in (_obs_id(4, 3), _obs_id(7, 12), _obs_id(17, 15)):
        row = migrated_by_id[observation_id]
        conditions = row.point_conditions
        assert conditions["temperature_K"].state.value == Decimal("1573")
        assert conditions["fO2_Pa"].state.is_value
        assert conditions["composition"].state.is_value
        assert row.identity.temperature_K.value == Decimal("1573")
        assert row.identity.fO2_Pa.is_value
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa.is_unknown

    fe2 = migrated_by_id[_obs_id(4, 3)]
    a_feo = migrated_by_id[_obs_id(7, 12)]
    gamma_feo = migrated_by_id[_obs_id(7, 14)]
    gamma_plain = to_plain(gamma_feo)
    assert to_plain(fe2)["value"]["point"] == "0.783"
    assert to_plain(a_feo)["value"]["point"] == "0.101"
    assert to_plain(gamma_feo)["value"]["point"] == "0.388"
    assert fe2.identity.fO2_Pa.value == Decimal("0.000101325")
    assert migrated_by_id[_obs_id(17, 15)].identity.fO2_Pa.value == Decimal("10.1325")

    contexts = {
        row["context_id"]
        for rows in migrator.result.context_by_work.values()
        for row in rows
    }
    assert a_feo.derived_from == (f"{SID}::context::{SLUG}_table1c_A4_cfe_pt",)
    assert gamma_plain["derived_from"] == [
        f"{SID}::{_obs_id(7, 12)}",
        f"{SID}::context::{SLUG}_table1b_A4_composition",
    ]
    assert a_feo.derived_from[0] in contexts
    assert gamma_plain["derived_from"][1] in contexts
