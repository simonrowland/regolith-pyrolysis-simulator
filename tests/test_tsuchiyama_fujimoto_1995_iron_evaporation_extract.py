from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/tsuchiyama-fujimoto-1995-iron-evaporation.yaml"
SOURCE_ID = "tsuchiyama-fujimoto-1995-iron-evaporation"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[dict]:
    return document["species"]["Fe"]["observations"]


def test_printed_tables_pin_render_cells_and_reject_mutation() -> None:
    document = _document()
    rows = _observations(document)
    table1 = [row for row in rows if row["locator"]["table"] == "1"]
    table2 = [row for row in rows if row["locator"]["table"] == "2"]
    assert len(table1) == 20
    assert len(table2) == 6

    run_1101 = next(row for row in table1 if row["values"]["run_as_printed"] == "1101")
    assert run_1101["locator"]["page"] == 2
    assert run_1101["locator"]["published_page"] == 206
    assert run_1101["locator"]["table_row_number"] == 2
    assert run_1101["locator"]["table_column_number"] == 8
    assert run_1101["values"]["value"] == "0.010"
    assert run_1101["values"]["T_C"] == "1075"
    assert run_1101["values"]["duration_h_as_printed"] == "72"
    assert run_1101["values"]["mass_loss_mg_as_printed"] == "0.94"
    assert run_1101["values"]["starting_material_as_printed"] == (
        "99.99% pure polycrystalline metallic iron"
    )
    assert run_1101["values"]["pressure_as_printed"] == (
        "The pressure ranged from 10^-5 to 10^-6 Torr."
    )
    pressure = run_1101["point_conditions"]["total_pressure_Pa"]["state"]["value"]
    assert pressure["interval_low"] == "0.00013332236842105263"
    assert pressure["interval_high"] == "0.0013332236842105263"

    run_1301 = next(row for row in table1 if row["values"]["run_as_printed"] == "1301")
    assert run_1301["locator"]["page"] == 2
    assert run_1301["locator"]["table_row_number"] == 18
    assert run_1301["locator"]["table_column_number"] == 8
    assert run_1301["values"]["surface_area_mm2_as_printed"] == "96.625"
    assert run_1301["values"]["mass_loss_mg_as_printed"] == "107.33"

    rate_1265 = next(row for row in table2 if row["values"]["T_C"] == "1265")
    assert rate_1265["locator"]["page"] == 6
    assert rate_1265["locator"]["published_page"] == 210
    assert rate_1265["locator"]["table_row_number"] == 6
    assert rate_1265["locator"]["table_column_number"] == 2
    assert rate_1265["values"]["evaporation_rate_as_printed"] == "2.307 × 10^-4"
    assert rate_1265["values"]["evaporation_rate"] == "2.307e-4"
    assert rate_1265["values"]["standard_deviation_as_printed"] == "1.99 × 10^-6"

    rate_1312 = next(row for row in table2 if row["values"]["T_C"] == "1312")
    assert rate_1312["locator"]["table_row_number"] == 7
    assert rate_1312["locator"]["table_column_number"] == 2
    assert rate_1312["values"]["standard_deviation_as_printed"] == "3.49 × 10^-4"

    assert all(row["phase"] == "cr" for row in rows)
    assert all(row["condensed_form"]["polymorph"] == "gamma" for row in rows)
    assert all(row["values"]["rail"] == "vapour" for row in rows)
    assert all(row["values"]["reservoir_as_printed"] == "pure metallic Fe(s)" for row in rows)

    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    mutated = deepcopy(document)
    mutated_row = next(
        row for row in _observations(mutated)
        if row["observation_id"] == run_1101["observation_id"]
    )
    mutated_row["values"]["value"] = "0.011"
    assert check_all_fidelity_samples_match(mutated, label="mutated Tsuchiyama Table 1")


def test_conditions_survive_isolated_migration_and_schema_gaps_are_refused() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert len(migrator.result.observations) == 26
    assert len(migrator.result.validation.hard_issues) == 0
    assert migrator.result.registry_issues == []
    assert sum(
        observation.value.kind.value == "unavailable"
        for observation in migrator.result.observations.values()
    ) == 20

    for observation in migrator.result.observations.values():
        assert observation.identity.temperature_K.is_value
        assert observation.point_conditions is not None
        assert set(observation.point_conditions) >= {
            "temperature_K",
            "total_pressure_Pa",
            "fO2_Pa",
            "composition",
        }
        assert observation.point_conditions["temperature_K"].state.is_value
        assert observation.point_conditions["total_pressure_Pa"].state.value.kind.value == "interval"
        assert observation.point_conditions["fO2_Pa"].state.is_unknown
        assert observation.point_conditions["composition"].state.is_unknown
        assert all(
            condition.locator is not None
            for condition in observation.point_conditions.values()
        )

    first_rate = next(
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.endswith("table2_1075C")
    )
    assert first_rate.identity.quantity.value is Quantity.EVAPORATION_RATE
    assert first_rate.value.point == Decimal("2.344e-6")
    assert first_rate.admission.status.value == "rejected"
    assert "does not represent" in first_rate.admission.reason
    assert first_rate.identity.total_pressure_Pa.is_unknown
    assert first_rate.identity.reservoir.is_unknown

    for obs in _observations(document):
        assert obs["values"]["T_C"]
        for condition in obs["point_conditions"].values():
            assert "state" in condition
            assert "locator" in condition
