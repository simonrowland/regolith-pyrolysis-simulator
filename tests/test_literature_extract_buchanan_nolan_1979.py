from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from tests.battery import factories as battery_factories
from tools import validate_literature_extracts as vle


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "buchanan-nolan-1979-sulfur-sulfide-immiscibility-tholeiitic"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _migrated():
    migrator = battery_factories.migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    return migrator.result


def test_full_table_render_pins_and_run_reconciliation() -> None:
    document = _document()
    observations = document["species"]["S"]["observations"]
    assert len(observations) == 27
    by_table = {
        table: [row for row in observations if row["locator"]["table"] == table]
        for table in ("2a", "2b", "2c")
    }
    assert [len(by_table[table]) for table in ("2a", "2b", "2c")] == [9, 9, 9]
    assert len(
        {
            row["values"]["tabulated_values_as_published"]["run_id"]
            for row in observations
        }
    ) == 21
    table_audit = yaml.safe_load(
        (ROOT / f"data/literature/extracts/audits/{SOURCE_ID}.mapping.yaml").read_text()
    )["tables"]
    assert [
        table.get("printed_data_rows", table.get("primary_run_columns"))
        for table in table_audit
    ] == [3, 9, 7, 5]

    by_id = {row["observation_id"]: row for row in observations}
    # Independent reads from the page renders; the sulfur range remains in uncertainty.
    for observation_id, printed, page, table in (
        ("bn79_t2a_run43_db3_glass_s", "0.027", 7, "2a"),
        ("bn79_t2b_run69_db3_glass_s", "0.136", 8, "2b"),
        ("bn79_t2b_run69_pal685_glass_s", "0.103", 8, "2b"),
        ("bn79_t2c_run45_db3_glass_s", "0.145", 9, "2c"),
        ("bn79_t2c_run45_db8_glass_s", "0.317", 9, "2c"),
    ):
        row = by_id[observation_id]
        assert row["values"]["value"] == printed
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table
        assert row["uncertainty"]["locator"]["page"] == page
        assert row["uncertainty"]["range_as_published"]
    assert by_id["bn79_t2c_run45_db8_glass_s"]["uncertainty"]["range_as_published"] == "0.314-0.322"

    assert vle.validate_extract_document(
        document, expected_source_id=SOURCE_ID
    ) == []
    assert vle.check_all_fidelity_samples_match(document, label="buchanan-nolan-1979") == []
    mutated = yaml.safe_load(yaml.safe_dump(document))
    mutated_row = next(
        row
        for row in mutated["species"]["S"]["observations"]
        if row["observation_id"] == "bn79_t2c_run45_db8_glass_s"
    )
    mutated_row["values"]["value"] = "0.318"
    assert any(
        "mismatch" in error
        for error in vle.check_all_fidelity_samples_match(
            mutated, label="mutated-buchanan-nolan-1979"
        )
    )


def test_isolated_migration_maps_each_printed_condition() -> None:
    result = _migrated()
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 27
    assert all(row.value.kind is ValueKind.POINT for row in result.observations.values())
    assert all(row.identity.quantity.is_value for row in result.observations.values())
    assert all(row.identity.species.phase.is_value for row in result.observations.values())
    assert all(row.identity.species.phase.value.value == "glass" for row in result.observations.values())

    for row in result.observations.values():
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["fO2_log"].state.is_value
        assert row.point_conditions["composition"].state.is_value
        assert row.point_conditions["total_pressure_Pa"].state.is_value
        assert row.point_conditions["quench_state"].state.is_value
        assert row.identity.composition is not None and row.identity.composition.is_value
        assert row.identity.temperature_K is not None and row.identity.temperature_K.is_value
        assert row.identity.total_pressure_Pa is not None and row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is not None and row.identity.fO2_Pa.is_value

    # Pin the required identity axes on three distinct printed table/page pairs.
    for observation_id, page, table, log_fo2 in (
        ("bn79_t2a_run43_db3_glass_s", 7, "2a", "-8.50"),
        ("bn79_t2b_run69_db3_glass_s", 8, "2b", "-10.50"),
        ("bn79_t2c_run45_db3_glass_s", 9, "2c", "-11.50"),
    ):
        row = result.observations[f"{SOURCE_ID}::{observation_id}"]
        assert row.identity.composition.is_value
        assert row.identity.temperature_K.value == Decimal("1473.15")
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        assert row.identity.fO2_Pa.is_value
        assert row.point_conditions["fO2_log"].state.value == Decimal(log_fo2)
        assert row.point_conditions["temperature_K"].locator.page == page
        assert row.point_conditions["temperature_K"].locator.table == table
        assert row.point_conditions["fO2_log"].locator.page == page
        assert row.point_conditions["composition"].locator.table == "1"
        assert row.point_conditions["total_pressure_Pa"].locator.page == 1
        assert row.point_conditions["quench_state"].state.value == "not_stated"
        assert row.point_conditions["quench_state"].locator.page == 4

    sulfur = result.observations[f"{SOURCE_ID}::bn79_t2a_run43_db3_glass_s"]
    assert sulfur.value.point == Decimal("0.027")
    assert sulfur.identity.subtype.value == "wt_percent"
