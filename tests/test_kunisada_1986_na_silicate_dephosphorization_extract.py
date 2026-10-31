"""Render-grounded extraction pins and isolated migration for Kunisada & Iwai (1986)."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, PhaseRole, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kunisada-1986-na-silicate-dephosphorization"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
CELL_AUDIT = (
    ROOT
    / "data/literature/extracts/audits"
    / f"{SOURCE_ID}.cell-audit.csv"
)


RENDER_PINS = {
    "kunisada_1986_t1ii_e_equilibrium_P_in_liquid_iron": (
        "P",
        "0.020",
        2,
        "1(II)",
        "E",
        "[%P]f",
        PhaseRole.METAL,
        Decimal("0.020"),
    ),
    "kunisada_1986_t1ii_e_equilibrium_Na2O_in_slag": (
        "Na2O",
        "40.9",
        2,
        "1(II)",
        "E",
        "Na2O",
        PhaseRole.SLAG,
        Decimal("40.9"),
    ),
    "kunisada_1986_table2_flux_i_experimental_total_FeO_content": (
        "FeO",
        "28.8",
        6,
        "2",
        "I",
        "(%T.FeO)exp.",
        PhaseRole.SLAG,
        Decimal("28.8"),
    ),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (formula, row)
        for formula, group in document["species"].items()
        for row in group.get("observations", [])
    }


def test_tables_match_render_samples_and_complete_cell_audit() -> None:
    document = _document()
    rows = _rows(document)

    with CELL_AUDIT.open(encoding="utf-8", newline="") as stream:
        cells = list(csv.DictReader(stream))
    assert len(cells) == 298
    assert all(
        cell["confirmation"].startswith("PDF page render verified") for cell in cells
    )
    assert {
        table: len(
            {
                cell["printed_data_row"]
                for cell in cells
                if cell["table"] == table
            }
        )
        for table in ("1(I)", "1(II)", "2")
    } == {"1(I)": 27, "1(II)": 5, "2": 5}

    fidelity = {sample["observation_id"]: sample for sample in document["fidelity_samples"]}
    for observation_id, (
        formula,
        printed,
        page,
        table,
        row_label,
        column,
        _role,
        _migrated_value,
    ) in RENDER_PINS.items():
        source_formula, source_row = rows[observation_id]
        assert source_formula == formula
        assert source_row["values"]["value"] == printed
        assert source_row["units"] == "%"
        assert source_row["values"]["value_as_published"]["unit"] == "%"
        assert source_row["locator"]["page"] == page
        assert source_row["locator"]["table"] == table
        assert source_row["locator"]["row_label"] == row_label
        assert source_row["locator"]["column"] == column
        assert fidelity[observation_id]["value"] == printed
        assert fidelity[observation_id]["locator"]["page"] == page
        assert fidelity[observation_id]["locator"]["table"] == table


def test_isolated_migration_binds_rows_conditions_and_exact_schema_gaps() -> None:
    document = _document()
    source_rows = _rows(document)
    migrator = migrate_one_extract(ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    migrated = migrator.result.observations

    expected_ids = {f"{SOURCE_ID}::{observation_id}" for observation_id in source_rows}
    assert set(migrated) == expected_ids
    assert len(migrated) == 278
    assert len(migrator.result.validation.hard_issues) == 0
    assert len(migrator.result.validation.issues) == 116
    assert {issue.reason.value for issue in migrator.result.validation.issues} == {
        "identity_incomplete"
    }
    assert not migrator.result.registry_issues
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 234

    expected_quantity_unknown = {
        f"{SOURCE_ID}::{observation_id}"
        for observation_id, (_formula, row) in source_rows.items()
        if row.get("values", {}).get("admission_status") == "typed_refusal"
    }
    actual_quantity_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.is_unknown
    }
    assert actual_quantity_unknown == expected_quantity_unknown
    assert len(actual_quantity_unknown) == 234
    assert all(
        row.identity.species is not None
        and row.identity.species.phase.is_value
        and row.identity.species.phase.value is Phase.L
        for row in migrated.values()
    )

    for observation_id, (_formula, source_row) in source_rows.items():
        source_conditions = source_row["point_conditions"]
        migrated_row = migrated[f"{SOURCE_ID}::{observation_id}"]
        assert source_conditions["temperature_C"]["state"]
        assert source_conditions["temperature_C"]["locator"]
        assert migrated_row.point_conditions is not None
        temperature = migrated_row.point_conditions["temperature_K"]
        assert temperature.locator is not None
        source_temperature = source_conditions["temperature_C"]["state"]
        assert temperature.state.tag.value == source_temperature["tag"]
        if source_temperature["tag"] == "value":
            assert temperature.state.value.is_finite()
        for condition_name in ("fO2_Pa", "total_pressure_Pa"):
            condition = source_conditions[condition_name]
            assert condition["state"]["tag"] == "unknown"
            assert condition["locator"]
            migrated_condition = migrated_row.point_conditions.get(condition_name)
            if migrated_condition is not None:
                assert migrated_condition.state.is_unknown
                assert migrated_condition.locator is not None
            identity_axis = getattr(migrated_row.identity, condition_name)
            if identity_axis is not None:
                assert not identity_axis.is_value

    phase_rows = [
        row
        for row in migrated.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(phase_rows) == 40
    assert all(row.identity.composition.is_value for row in phase_rows)
    assert all(row.point_conditions["composition"].state.is_value for row in phase_rows)

    for observation_id, (
        formula,
        _printed,
        page,
        table,
        row_label,
        _column,
        phase_role,
        migrated_value,
    ) in RENDER_PINS.items():
        row = migrated[f"{SOURCE_ID}::{observation_id}"]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == migrated_value
        assert row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert row.identity.species.formula == formula
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.phase_role.value is phase_role
        assert row.identity.temperature_K.is_value
        assert row.identity.composition.is_value
        assert row.locator.page == page
        assert row.locator.table == table
        assert row.locator.note is not None or row.locator.table == "2"
        for axis in (row.identity.total_pressure_Pa, row.identity.fO2_Pa):
            assert axis.is_unknown
        assert row.point_conditions["total_pressure_Pa"].state.is_unknown
        assert row.point_conditions["fO2_Pa"].state.is_unknown
        condition_locator = row.point_conditions["composition"].locator
        assert condition_locator is not None
        assert condition_locator.page == 2
        assert condition_locator.table == "1(II)"
        assert condition_locator.note is not None
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].state.value == Decimal("1873.15")
