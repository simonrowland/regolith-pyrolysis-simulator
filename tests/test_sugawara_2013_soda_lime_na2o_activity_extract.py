"""Source-grounded guards for Sugawara et al. (2013) Table 1–4 extraction."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "sugawara-2013-soda-lime-melt-na2o-activity"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
T3_RENDER_PINS = {
    "sugawara_2013_t3_this_study_row_3_1473K": ("−9.01", Decimal("9.772372209558106826970760070E-10")),
    "sugawara_2013_t3_this_study_row_3_1573K": ("−8.46", Decimal("3.467368504525316394529113171E-9")),
    "sugawara_2013_t3_this_study_row_4_1473K": ("−8.94", Decimal("1.148153621496882751546224612E-9")),
}
T1_RENDER_PINS = {
    ("24", "sample_before_measurement_mol_percent", "SiO2"): "70.4",
    ("44", "sample_after_measurement_mol_percent", "CaO"): "16.3",
    ("57", "reference_after_measurement_mol_percent", "Na2O"): "28.6",
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_observation(document: dict, observation_id: str) -> dict:
    for body in document["species"].values():
        for observation in body.get("observations", []):
            if observation["observation_id"] == observation_id:
                return observation
    raise AssertionError(f"Missing source observation {observation_id}")


def _context(document: dict, observation_id: str) -> dict:
    for body in document["species"].values():
        for row in body.get("context", []):
            if row["observation_id"] == observation_id:
                return row
    raise AssertionError(f"Missing context row {observation_id}")


@pytest.fixture(scope="module")
def migrated():
    runner = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    runner.finalize()
    return runner.result


def test_tables_are_complete_and_render_pins_reach_migrated_fields(migrated) -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    table1 = _context(document, "sugawara_2013_table1_glass_compositions")
    table1_rows = table1["values"]["rows_as_printed"]
    assert len(table1_rows) == table1["values"]["row_count"] == 17
    assert table1["values"]["printed_numeric_cell_count"] == 376
    assert table1["values"]["EDX_numeric_cell_count"] == 272
    assert sum(
        cell["state"] == "value"
        for row in table1_rows
        for column in (
            "sample_before_measurement_mol_percent",
            "sample_after_measurement_mol_percent",
            "reference_before_measurement_mol_percent",
            "reference_after_measurement_mol_percent",
        )
        for cell in row[column].values()
    ) == 272

    table2 = _context(document, "sugawara_2013_table2_emf_measurements")
    assert len(table2["values"]["rows_as_printed"]) == table2["values"]["row_count"] == 24
    emf_cells = [
        cell
        for row in table2["values"]["rows_as_printed"]
        for cell in row["values"]["emf_series"]
    ]
    assert sum("value_as_printed" in cell for cell in emf_cells) == 82
    assert sum("value" in cell for cell in emf_cells) == 62
    assert {
        "tools/validate_literature_extracts.py::OBSERVATION_TYPES",
        "simulator/battery/enums.py::Quantity",
        "simulator/battery/migrate.py::QUANTITY_ALIASES",
        "simulator/battery/migrate.py::TYPE_QUANTITY",
    } <= set(table2["values"]["rows_as_printed"][0]["values"]["schema_gap_symbols"])

    table3 = _context(document, "sugawara_2013_table3_activity_rows")
    table3_rows = table3["values"]["rows_as_printed"]
    assert len(table3_rows) == table3["values"]["row_count"] == 5
    activity_cells = [
        row[key]
        for row in table3_rows
        for key in ("log10_activity_at_1473_K", "log10_activity_at_1573_K")
    ]
    assert sum(cell["state"] == "value" for cell in activity_cells) == 8
    assert sum(cell["state"] == "absent" for cell in activity_cells) == 2

    table4_sources = [
        observation
        for body in document["species"].values()
        for observation in body.get("observations", [])
        if observation.get("locator", {}).get("table") == "4"
        and observation.get("observation_id", "").startswith("sugawara_2013_t4_rtln_run_")
    ]
    table4_cells = [cell for row in table4_sources for cell in row["values"]["series"]]
    assert len(table4_sources) == 20
    assert all(
        row["point_conditions"][field]["state"]["tag"] == "unknown"
        for row in table4_sources
        for field in ("total_pressure_Pa", "fO2_Pa")
    )
    assert sum(cell.get("value") is not None for cell in table4_cells) == 77
    assert sum(cell.get("state", {}).get("state") == "absent" for cell in table4_cells) == 43
    assert sum(cell.get("value_as_printed") == "±4.4" for cell in table4_cells) == 1
    assert sum(
        cell.get("value") is None and cell.get("value_as_printed") != "±4.4"
        for cell in table4_cells
    ) == 42

    assert any(
        symbol.endswith("::PHASE_BASIS_TOKENS")
        for symbol in table1["values"]["schema_gap_symbols"]
    )
    table1_by_run = {row["source_row"]: row for row in table1_rows}
    for (run, field, formula), expected in T1_RENDER_PINS.items():
        row = table1_by_run[run]
        cell = row[field][formula]
        assert row["locator"]["page"] == 3
        assert row["locator"]["table"] == "1"
        assert cell["state"] == "value"
        assert cell["value_as_printed"] == expected

    for observation_id, (printed, migrated_value) in T3_RENDER_PINS.items():
        source = _source_observation(document, observation_id)
        assert source["locator"]["page"] == 4
        assert source["locator"]["table"] == "3"
        assert source["values"]["log10_activity_as_published"] == printed
        row = migrated.observations[f"{SOURCE_ID}::{observation_id}"]
        assert row.identity.quantity.value is Quantity.ACTIVITY
        assert row.identity.species.formula == "Na2O"
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal(source["values"]["T_K"])
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.identity.composition.is_value
        assert row.identity.composition.value.components == (
            ("SiO2", Decimal("0.8")),
            ("Na2O", Decimal("0.2")),
        )
        assert row.identity.reference_state.is_unknown
        if migrated_value is not None:
            assert row.value.point == migrated_value

    fit_id = "sugawara_2013_t4_partial_enthalpy_fit_78_12_10"
    h_source = _source_observation(document, fit_id)
    assert h_source["locator"]["page"] == 5
    assert h_source["locator"]["table"] == "4"
    assert h_source["values"]["value_as_printed"] == "−262 ± 4"
    h_row = migrated.observations[f"{SOURCE_ID}::{fit_id}"]
    assert h_row.value.point == Decimal("-262")
    assert h_row.identity.quantity.value is Quantity.PARTIAL_MOLAR_ENTHALPY
    assert h_row.identity.temperature_K.is_unknown
    assert h_row.identity.total_pressure_Pa.is_unknown

    expected_entropies = {
        f"{SOURCE_ID}::{row['observation_id']}"
        for body in document["species"].values()
        for row in body.get("observations", [])
        if row.get("table_column_number") == 12
        and row.get("values", {}).get("quantity")
        == "partial_molar_entropy_of_mixing_Na2O_as_printed"
    }
    unknown_quantity_rows = {
        observation_id
        for observation_id, row in migrated.observations.items()
        if not row.identity.quantity.is_value
    }
    table4_point_rows = {
        observation_id
        for observation_id, row in migrated.observations.items()
        if row.locator is not None
        and row.locator.table == "4"
        and "T=" in (row.locator.note or "")
        and not row.identity.quantity.is_value
    }
    assert len(table4_point_rows) == 120
    assert all(
        row.identity.temperature_K.is_value
        and row.identity.temperature_K.value in {
            Decimal("1273"), Decimal("1373"), Decimal("1423"),
            Decimal("1473"), Decimal("1523"), Decimal("1573"),
        }
        for observation_id, row in migrated.observations.items()
        if observation_id in table4_point_rows
    )
    assert unknown_quantity_rows == table4_point_rows | expected_entropies
    assert len(unknown_quantity_rows) == 135
    assert len(migrated.observations) == 158
    assert sum(row.value.kind is ValueKind.POINT for row in migrated.observations.values()) == 23
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.observations.values()) == 135
    assert sum(
        row.identity.quantity.value is Quantity.ACTIVITY
        for row in migrated.observations.values()
    ) == 8
    assert sum(
        row.identity.quantity.value is Quantity.PARTIAL_MOLAR_ENTHALPY
        for row in migrated.observations.values()
    ) == 15
    assert all(
        row.identity.species.phase.is_value
        for row in migrated.observations.values()
    )
    assert migrated.validation is not None
    assert not migrated.validation.hard_issues
    assert migrated.registry_issues == []


def test_fidelity_mutation_of_a_rendered_cell_is_rejected() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    mutated = deepcopy(document)
    target = _source_observation(
        mutated, "sugawara_2013_t3_this_study_row_3_1473K"
    )
    target["values"]["log10_activity_as_published"] = "−9.02"
    errors = validate_extract_document(
        mutated,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("fidelity" in error.casefold() for error in errors)
