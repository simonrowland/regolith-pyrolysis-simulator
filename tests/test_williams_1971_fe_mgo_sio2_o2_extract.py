from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/williams-1971-fe-mgo-sio2-o2-reaction-constants.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context_rows(document: dict) -> list[dict]:
    return list(document["species"]["Fe"].get("context", []))


def _component_refusals(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in _context_rows(document)
        if row.get("values", {}).get("quantity") == "equilibrium_phase_composition"
    }


def _migrated_render_pins(migrated: list[object]) -> dict[str, tuple[str, str, str]]:
    return {
        row.observation_id.split("::")[-1]: (
            str(row.value.point), str(row.locator.page), str(row.locator.table)
        )
        for row in migrated
    }


def _assert_render_logk_pins(actual: dict[str, tuple[str, str, str]]) -> None:
    expected = {
        "williams71_t4_opsi_3c": ("-0.09", "14", "4"),
        "williams71_t5_owi_3c": ("5.443", "15", "5"),
        "williams71_t7_opsi_3c": ("5.610", "16", "7"),
    }
    for observation_id, value_and_locator in expected.items():
        assert actual[observation_id] == value_and_locator


def _assert_render_pins(document: dict) -> None:
    pins = {
        "williams71_opsi_3a_o_composition": ("25", 9, "1(a)", "OPSI-3a"),
        "williams71_opsi_3a_p_composition": ("20", 9, "1(a)", "OPSI-3a"),
        "williams71_owi_6d_w_composition": ("12", 10, "1(f)", "OWI-6d"),
    }
    refusals = _component_refusals(document)
    assert len(refusals) == 109
    for observation_id, (value, page, table, row) in pins.items():
        observation = refusals[observation_id]
        assert observation["values"]["component_amount"] == value
        assert observation["locator"]["page"] == page
        assert observation["locator"]["table"] == table
        assert observation["locator"]["row"] == row

    run_rows = [r for r in _context_rows(document) if r.get("values", {}).get("quantity") == "printed_experimental_run_row"]
    assert len(run_rows) == 82
    table_row_total = sum(
        int(r["values"]["printed_row_count"])
        for r in _context_rows(document)
        if r.get("values", {}).get("printed_row_count") is not None
    )
    assert len(run_rows) + table_row_total == 174


def test_printed_rows_render_pins_and_mutations() -> None:
    document = _document()
    _assert_render_pins(document)

    mutated = deepcopy(document)
    _component_refusals(mutated)["williams71_opsi_3a_o_composition"]["values"]["component_amount"] = "26"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_isolated_migration_preserves_only_printed_conditions_and_logk_points() -> None:
    document = _document()
    refusals = _component_refusals(document)
    assert len(refusals) == 109
    for row in refusals.values():
        values = row["values"]
        assert values["status"] == values["admission_status"] == "typed_refusal"
        assert values["point_conditions"]["T_C"]["state"]["value"] == values["T_C"]
        assert values["point_conditions"]["T_C"]["locator"]["page"] == row["locator"]["page"]
        assert values["point_conditions"]["fO2_log"]["locator"]["row"] == row["locator"]["row"]
        assert values["point_conditions"]["total_pressure_Pa"]["locator"]["table"] == row["locator"]["table"]

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    migrated = [
        row for row in migrator.result.observations.values()
        if row.source_id == document["source_id"]
    ]
    assert len(migrated) == 46
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.queue

    # Identity axes for this log10_Kf profile: only temperature is applicable.
    migrated_pins = _migrated_render_pins(migrated)
    _assert_render_logk_pins(migrated_pins)
    pinned = {
        row.observation_id.split("::")[-1]: row
        for row in migrated
        if row.observation_id.split("::")[-1] in {
            "williams71_t4_opsi_3c",
            "williams71_t5_owi_3c",
            "williams71_t7_opsi_3c",
        }
    }
    for observation_id, observation in pinned.items():
        temperature = "1573.15"
        assert str(observation.value.point) == migrated_pins[observation_id][0]
        assert str(observation.identity.temperature_K.value) == temperature
        assert observation.point_conditions["temperature_K"].locator.table == observation.locator.table
        assert observation.identity.composition.is_not_applicable
        assert observation.identity.total_pressure_Pa.is_not_applicable
        assert observation.identity.fO2_Pa.is_not_applicable
        assert "fO2_Pa" not in observation.point_conditions
        assert "total_pressure_Pa" not in observation.point_conditions
        assert "composition" not in observation.point_conditions

    mutated = dict(migrated_pins)
    mutated["williams71_t4_opsi_3c"] = ("0.09", "14", "4")
    with pytest.raises(AssertionError):
        _assert_render_logk_pins(mutated)

    _assert_render_pins(document)
