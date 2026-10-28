"""Render-grounded regression pins for Pak, Ito, and Fruehan (1989)."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/"
    / "pak-ito-fruehan-1989-isij-na2o-activity-cao-slags.yaml"
)


def test_table1_activities_are_reduced_from_rendered_log_values():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = source["species"]["Na2O"]["observations"]
    activity_rows = [
        row
        for row in source_rows
        if row["observation_id"].startswith("pak_1989_table1_row_")
        and not row["observation_id"].endswith("_printed_log_input")
    ]
    input_rows = [
        row for row in source_rows if row["observation_id"].endswith("_printed_log_input")
    ]
    assert len(activity_rows) == 22
    assert len(input_rows) == 22

    expected = {
        "pak_1989_table1_row_01_a0_1400": ("3.241", Decimal("0.0005741164622073274")),
        "pak_1989_table1_row_03_b0_1400": ("4.771", Decimal("0.000016943378004473287")),
        "pak_1989_table1_row_09_a1_1400": ("2.458", Decimal("0.0034833731503601164")),
    }
    rows_by_id = {row["observation_id"]: row for row in activity_rows}
    for observation_id, (printed, value) in expected.items():
        row = rows_by_id[observation_id]
        assert row["values"]["value_as_printed"] == printed
        assert Decimal(str(row["values"]["activity"])) == value
        assert row["locator"]["page"] == 3
        assert row["locator"]["table"] == "1"
        assert row["derivation"]["relation"] == "a_Na2O = 10^(-(-log a_Na2O))"

    migrator = migrate_one_extract(ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    migrated = migrator.result.observations
    activity = {
        observation_id.rsplit("::", 1)[-1]: observation
        for observation_id, observation in migrated.items()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
        and observation_id.rsplit("::", 1)[-1].startswith("pak_1989_table1_row_")
    }
    assert len(activity) == 22
    assert not migrator.result.validation.hard_issues
    assert len(migrator.result.validation.issues) == 24
    assert {
        issue.path.rsplit(".", 1)[-1]
        for issue in migrator.result.validation.issues
    } == {"reference_state"}

    for source_row in activity_rows:
        observation = activity[source_row["observation_id"]]
        assert observation.identity.temperature_K.is_value
        assert observation.identity.composition.is_value
        assert observation.point_conditions["T_C"].state.is_value
        assert observation.point_conditions["T_C"].locator.page == 3
        assert observation.point_conditions["composition"].state.is_value
        assert observation.point_conditions["composition"].locator.table == "1"
        assert "fO2_Pa" not in observation.point_conditions
        assert "total_pressure_Pa" not in observation.point_conditions
        assert observation.identity.reference_state.is_unknown

    for observation_id, (printed, value) in expected.items():
        observation = activity[observation_id]
        assert observation.value.point == value
        parent_id = observation.derived_from[0]
        parent = migrated[parent_id]
        assert parent.identity.quantity.is_unknown
        assert parent_id.rsplit("::", 1)[-1].endswith("_printed_log_input")
        assert parent.locator.page == 3
        assert parent.locator.table == "1"
        assert parent_id.rsplit("::", 1)[-1].replace("_printed_log_input", "") == observation_id
