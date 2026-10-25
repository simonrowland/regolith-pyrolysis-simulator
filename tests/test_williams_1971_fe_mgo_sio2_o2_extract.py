from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AdmissionStatus, ValueKind
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


def _reaction_rows(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (species, row)
        for species, block in document["species"].items()
        for row in block.get("observations", [])
        if row["observation_id"].startswith("williams71_t")
    }


def _printed_data_row_count(document: dict) -> int:
    contexts = _context_rows(document)
    run_rows = [
        row
        for row in contexts
        if row.get("values", {}).get("quantity") == "printed_experimental_run_row"
    ]
    structural_rows = []
    table_data_rows = 0
    for row in contexts:
        table_rows = row.get("values", {}).get("rows")
        if not isinstance(table_rows, list):
            continue
        for table_row in table_rows:
            if isinstance(table_row, dict) and table_row.get("row_type") == "structural_context":
                structural_rows.append(table_row)
            else:
                table_data_rows += 1

    assert len(structural_rows) == 2
    assert {row["label"] for row in structural_rows} == {
        "Derived with QFM by Wones and Gilbert (1969):",
        "Derived Reactions:",
    }
    data_row_count = len(run_rows) + table_data_rows
    assert document["extraction"]["printed_data_rows"] == data_row_count
    return data_row_count


def _assert_render_reaction_pins(rows: dict[str, tuple[str, dict]]) -> None:
    expected = {
        "williams71_t4_opsi_3c": (
            "-0.09",
            "MgSi1/2O2 + FeSiO3 = MgSiO3 + FeSi1/2O2",
            14,
            "4",
        ),
        "williams71_t6_opsi_3c": (
            "5.740",
            "Fe + 1/2 SiO2 + 1/2 O2 = FeSi1/2O2",
            15,
            "6",
        ),
        "williams71_t5_owi_2c": (
            "5.560",
            "(1 - x)Fe + 1/2 O2 = Fe(1-x)O",
            15,
            "5",
        ),
    }
    for observation_id, (value, reaction, page, table) in expected.items():
        _species, row = rows[observation_id]
        assert row["values"]["value_as_published"].strip("'\"") == value
        assert row["values"]["reaction"] == reaction
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table


def _assert_reaction_refusal_rows(document: dict) -> None:
    rows = _reaction_rows(document)
    assert len(rows) == 46
    for row in rows.values():
        values = row[1]["values"]
        assert values["quantity"] == "reaction_equilibrium_constant"
        assert values["status"] == values["admission_status"] == "typed_refusal"
        assert values["reason"] == values["unavailable_reason"] == (
            "schema_gap_reaction_equilibrium_constant"
        )
        assert values["reaction"]
        assert values["value_as_published"]
        assert values["T_C"]
        assert values["point_conditions"]["T_C"]["state"]["value"] == values["T_C"]

    extrema = {
        "williams71_t4_opsi_3c": "Min.",
        "williams71_t5_owi_2c": "max",
        "williams71_t6_opsi_3c": "min",
        "williams71_t6_osi_3c": "max",
        "williams71_t6_osi_1c": "min",
        "williams71_t7_psi_2c": "max",
        "williams71_t7_psi_4c": "min",
    }
    assert {
        observation_id: rows[observation_id][1]["values"]["comment_as_published"]
        for observation_id in extrema
    } == extrema
    assert rows["williams71_t6_opsi_3c"][0] == "FeSi1/2O2"
    assert rows["williams71_t6_opsi_3c"][1]["values"]["formula"] == "FeSi1/2O2"


def _assert_migrated_reaction_pins(rows: dict[str, object]) -> None:
    expected = {
        "williams71_t4_opsi_3c": (
            "MgSi1/2O2 + FeSiO3 = MgSiO3 + FeSi1/2O2",
            "-0.09",
            "Min.",
            14,
            "4",
        ),
        "williams71_t6_opsi_3c": (
            "Fe + 1/2 SiO2 + 1/2 O2 = FeSi1/2O2",
            "5.740",
            "min",
            15,
            "6",
        ),
        "williams71_t5_owi_2c": (
            "(1 - x)Fe + 1/2 O2 = Fe(1-x)O",
            "5.560",
            "max",
            15,
            "5",
        ),
    }
    for observation_id, (reaction, value, qualifier, page, table) in expected.items():
        observation = rows[observation_id]
        assert observation.value.kind is ValueKind.UNAVAILABLE
        assert observation.admission.status is AdmissionStatus.REJECTED
        assert observation.admission.reason == "schema_gap_reaction_equilibrium_constant"
        assert observation.provenance == {
            "reaction_as_printed": reaction,
            "log_K_value_as_printed": value,
            "qualifier_as_published": qualifier,
        }
        assert observation.locator.page == page
        assert observation.locator.table == table


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
    assert _printed_data_row_count(document) == 172


def test_printed_rows_render_pins_and_mutations() -> None:
    document = _document()
    _assert_render_pins(document)
    reaction_rows = _reaction_rows(document)
    _assert_render_reaction_pins(reaction_rows)
    _assert_reaction_refusal_rows(document)

    mutated = deepcopy(document)
    _component_refusals(mutated)["williams71_opsi_3a_o_composition"]["values"]["component_amount"] = "26"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)

    mutated_reaction = deepcopy(document)
    _reaction_rows(mutated_reaction)["williams71_t4_opsi_3c"][1]["values"][
        "value_as_published"
    ] = "0.09"
    with pytest.raises(AssertionError):
        _assert_render_reaction_pins(_reaction_rows(mutated_reaction))


def test_table_9_headings_are_context_rows_not_data_rows() -> None:
    document = _document()
    table_9 = next(
        row
        for row in _context_rows(document)
        if row.get("observation_id") == "williams71_table_9_fit_coefficients"
    )
    table_9_values = table_9["values"]
    structural = [
        row
        for row in table_9_values["rows"]
        if isinstance(row, dict) and row.get("row_type") == "structural_context"
    ]
    assert len(table_9_values["rows"]) == 22
    assert len(structural) == 2
    assert sum(
        not (isinstance(row, dict) and row.get("row_type") == "structural_context")
        for row in table_9_values["rows"]
    ) == 20
    assert _printed_data_row_count(document) == 172

    structural[0].pop("row_type")
    with pytest.raises(AssertionError):
        _printed_data_row_count(document)


def test_isolated_migration_refuses_reaction_constants_without_schema_token() -> None:
    document = _document()
    _assert_reaction_refusal_rows(document)
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
    rows = {row.observation_id.split("::")[-1]: row for row in migrated}
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated) == 46
    assert sum(
        row.value.kind is ValueKind.UNAVAILABLE
        and row.admission.reason == "schema_gap_reaction_equilibrium_constant"
        for row in migrated
    ) == 46
    assert not any(
        row.identity.quantity.is_value
        and row.identity.quantity.value.value == "log10_Kf"
        and row.value.kind is ValueKind.POINT
        for row in migrated
    )
    for observation in migrated:
        assert observation.identity.temperature_K.is_value
        assert "temperature_K" in observation.point_conditions
        assert "fO2_Pa" not in observation.point_conditions
        assert "total_pressure_Pa" not in observation.point_conditions

    reaction_pins = {
        key: rows[key]
        for key in (
            "williams71_t4_opsi_3c",
            "williams71_t6_opsi_3c",
            "williams71_t5_owi_2c",
        )
    }
    _assert_migrated_reaction_pins(reaction_pins)

    mutated = dict(reaction_pins)
    source_row = reaction_pins["williams71_t4_opsi_3c"]
    mutated["williams71_t4_opsi_3c"] = replace(
        source_row,
        provenance={
            **source_row.provenance,
            "reaction_as_printed": "INVALID REACTION",
        },
    )
    with pytest.raises(AssertionError):
        _assert_migrated_reaction_pins(mutated)

    _assert_render_pins(document)
