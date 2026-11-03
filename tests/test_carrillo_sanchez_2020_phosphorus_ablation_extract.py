from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, StateTag, ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/carrillo-sanchez-2020-phosphorus-ablation.yaml"
SOURCE_ID = "carrillo-sanchez-2020-phosphorus-ablation"
EFFICIENCIES_ID = "carrillo_sanchez_2020_table3_phosphorus_ablation_efficiencies"
BULK_DUST_ID = "carrillo_sanchez_2020_table3_bulk_cosmic_dust_mass_rates"


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
    }


def _contexts(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("context", [])
    }


def _assert_render_pins(document: dict) -> None:
    rows = _observations(document)
    pins = (
        (
            "carrillo_sanchez_2020_table3_earth_p_mass_influx",
            0,
            "0.000028",
            2,
            3,
        ),
        (
            "carrillo_sanchez_2020_table3_mars_total_p_mass_influx",
            3,
            "0.0012",
            10,
            6,
        ),
        (
            "carrillo_sanchez_2020_table3_venus_total_p_mass_influx",
            3,
            "0.024",
            15,
            6,
        ),
    )
    for observation_id, index, value, table_row, table_column in pins:
        point = rows[observation_id]["values"]["points"][index]
        assert point["value"] == value
        assert point["locator"]["page"] == 13
        assert point["locator"]["table"] == "3"
        assert point["table_row_number"] == table_row
        assert point["table_column_number"] == table_column

    measured = rows[
        "carrillo_sanchez_2020_allende_po_ablation_peak_temperature_radius_gt19um"
    ]
    assert measured["locator"]["page"] == 9
    assert measured["values"]["T_K"] == {
        "kind": "interval",
        "interval_low": "2100",
        "interval_high": "2200",
    }
    assert measured["values"]["particle_radius_as_printed"] == ">19 μm"


def _assert_model_efficiency_context(document: dict) -> None:
    context = _contexts(document)[EFFICIENCIES_ID]
    values = context["values"]
    assert values["method_class"] == "model_derived"
    assert values["evidence_class"] == "model_derived"
    assert values["method"]["name"] == "CABMOD-ZoDy"
    assert values["method"]["method_locator"]["page"] == 11
    assert values["method"]["method_locator"]["section"] == 3.4
    assert values["method"]["result_locator"] == {"page": 13, "table": "3"}
    assert values["admission_status"] == "context_only"
    assert "not a furnace pyrolysis yield" in values["reason"]

    expected = (
        ("Earth", "JFC-Planck", "(25%)", 6, 3),
        ("Earth", "AST", "(57%)", 6, 4),
        ("Earth", "HTC", "(96%)", 6, 5),
        ("Earth", "Total P", "(41%)", 5, 6),
        ("Mars", "JFC-Planck", "(11%)", 11, 3),
        ("Mars", "AST", "(3%)", 11, 4),
        ("Mars", "HTC", "(93%)", 11, 5),
        ("Mars", "Total P", "(38%)", 11, 6),
        ("Venus", "JFC-Planck", "(36%)", 15, 3),
        ("Venus", "AST", "(67%)", 16, 4),
        ("Venus", "HTC", "(98%)", 15, 5),
        ("Venus", "Total P", "(53%)", 15, 6),
    )
    percentages = values["percentages"]
    assert len(percentages) == 12
    for cell, (planet, source, value, table_row, table_column) in zip(
        percentages, expected, strict=True
    ):
        assert cell["planet_as_printed"] == planet
        assert cell["dust_source_as_printed"] == source
        assert cell["value_as_printed"] == value
        assert cell["table_row_number"] == table_row
        assert cell["table_column_number"] == table_column
        assert cell["locator"]["page"] == 13
        assert cell["locator"]["table"] == "3"


def _assert_bulk_dust_context_pins(document: dict) -> None:
    context = _contexts(document)[BULK_DUST_ID]
    assert context["units"] == "t d-1"
    values = context["values"]
    assert values["species_as_printed"] == "bulk cosmic dust, not elemental phosphorus"
    assert values["method_class"] == "model_derived"
    assert values["evidence_class"] == "model_derived"
    assert values["method"]["name"] == "CABMOD-ZoDy"
    assert values["method"]["method_locator"]["page"] == 11
    assert values["method"]["method_locator"]["section"] == 3.4

    expected = (
        ("Earth", 2, "8.2", "28.0 ± 8.1", 7, 8, "± 8.1"),
        ("Mars", 7, "0.65", "2.1 ± 1.2", 7, 8, "± 1.2"),
        ("Venus", 12, "12.7", "31.0 ± 17.7", 7, 8, "± 17.7"),
    )
    rates = values["rates"]
    assert len(rates) == 3
    for row, (planet, table_row, ablated, influx, ablated_column, influx_column, uncertainty) in zip(
        rates, expected, strict=True
    ):
        assert row["planet_as_printed"] == planet
        assert row["table_row_number"] == table_row
        assert row["total_ablated"]["value_as_printed"] == ablated
        assert row["total_influx"]["value_as_printed"] == influx
        assert row["total_influx"]["uncertainty_as_printed"] == uncertainty
        assert row["total_influx"]["confidence_level"] == "not stated"
        for field, column in (("total_ablated", ablated_column), ("total_influx", influx_column)):
            cell = row[field]
            assert cell["table_column_number"] == column
            assert cell["locator"]["page"] == 13
            assert cell["locator"]["table"] == "3"
            assert cell["locator"]["row"] == f"{planet} P"


def _table3_census(document: dict) -> tuple[int, int, int, set[int]]:
    flux_points = 0
    rows: set[int] = set()
    for row in _observations(document).values():
        values = row.get("values", {})
        if row.get("locator", {}).get("table") != "3":
            continue
        if row.get("type") == "gas_speciation":
            for point in values.get("points", []):
                flux_points += 1
                rows.add(int(point["table_row_number"]))

    contexts = _contexts(document)
    percentages = contexts[EFFICIENCIES_ID]["values"]["percentages"]
    for cell in percentages:
        rows.add(int(cell["table_row_number"]))
    bulk_rates = contexts[BULK_DUST_ID]["values"]["rates"]
    bulk_cells = 0
    for row in bulk_rates:
        rows.add(int(row["table_row_number"]))
        bulk_cells += 2
    return flux_points, len(percentages), bulk_cells, rows


def _local_observation_id(observation) -> str:
    prefix = f"{SOURCE_ID}::"
    assert observation.observation_id.startswith(prefix)
    return observation.observation_id[len(prefix) :].split("::", 1)[0]


def _axis_is_unknown(axis) -> bool:
    return axis is None or axis.is_unknown


def test_table3_context_bindings_and_render_pins_reject_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []
    _assert_render_pins(document)
    _assert_model_efficiency_context(document)
    _assert_bulk_dust_context_pins(document)

    flux_points, percentages, bulk_cells, table_rows = _table3_census(document)
    assert flux_points == 48
    assert percentages == 12
    assert bulk_cells == 6
    assert flux_points + percentages + bulk_cells == 66
    assert table_rows == set(range(2, 17))

    for row in _observations(document).values():
        if row.get("type") != "gas_speciation" or row.get("locator", {}).get("table") != "3":
            continue
        assert all(point["table_column_number"] in {3, 4, 5, 6} for point in row["values"]["points"])

    mutated = deepcopy(document)
    earth_p = _observations(mutated)["carrillo_sanchez_2020_table3_earth_p_mass_influx"]
    earth_p["values"]["points"][0]["value"] = "0.000029"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)

    mutated = deepcopy(document)
    bulk = _contexts(mutated)[BULK_DUST_ID]
    bulk["values"]["rates"][0]["total_influx"]["value_as_printed"] = "99.0 ± 8.1"
    with pytest.raises(AssertionError):
        _assert_bulk_dust_context_pins(mutated)

    mutated = deepcopy(document)
    efficiencies = _contexts(mutated)[EFFICIENCIES_ID]
    efficiencies["values"]["percentages"][10]["value_as_printed"] = "(99%)"
    with pytest.raises(AssertionError):
        _assert_model_efficiency_context(mutated)


def test_isolated_migration_keeps_only_refused_phosphorus_flux_and_measured_peak() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    observations = list(result.observations.values())
    by_source_id: dict[str, list] = {}
    for observation in observations:
        by_source_id.setdefault(_local_observation_id(observation), []).append(observation)

    assert len(observations) == 49
    assert result.validation.hard_issues == ()
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 49
    assert all(row.identity.species.phase.is_value for row in observations)
    assert all(row.identity.species.phase.value is Phase.G for row in observations)

    table3_flux_rows = [
        row
        for row in observations
        if _local_observation_id(row).startswith("carrillo_sanchez_2020_table3_")
    ]
    assert len(table3_flux_rows) == 48
    assert not any("ablation_fraction" in _local_observation_id(row) for row in observations)
    assert not any("bulk_cosmic_dust" in _local_observation_id(row) for row in observations)
    assert not any(
        row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.YIELD_FRACTION
        for row in observations
    )

    measured_id = (
        "carrillo_sanchez_2020_allende_po_ablation_peak_temperature_radius_gt19um"
    )
    source_rows = _observations(document)
    expected_unknown_source_ids = {
        source_id
        for source_id, row in source_rows.items()
        if row.get("type") == "gas_speciation"
        and row.get("locator", {}).get("table") == "3"
    } | {measured_id}
    expected_quantity_unknown = {
        row.observation_id
        for row in observations
        if _local_observation_id(row) in expected_unknown_source_ids
    }
    actual_quantity_unknown = {
        row.observation_id for row in observations if row.identity.quantity.is_unknown
    }
    assert actual_quantity_unknown == expected_quantity_unknown
    assert len(actual_quantity_unknown) == 49
    assert all(row.identity.quantity.is_unknown for row in table3_flux_rows)

    for observation in table3_flux_rows:
        assert observation.value.kind is ValueKind.UNAVAILABLE
        assert observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        conditions = observation.point_conditions
        assert conditions is not None
        for key in ("temperature_K", "fO2_log", "composition"):
            condition = conditions[key]
            assert condition.state.tag is StateTag.UNKNOWN
            assert condition.locator is not None
            assert condition.locator.page == 13
            assert condition.locator.table == "3"
        assert "total_pressure_Pa" not in conditions

    identity_pins = {
        "carrillo_sanchez_2020_table3_earth_p_mass_influx": 4,
        "carrillo_sanchez_2020_table3_mars_po2_mass_influx": 4,
        "carrillo_sanchez_2020_table3_venus_total_p_mass_influx": 4,
    }
    for source_id, expected_cells in identity_pins.items():
        rows = by_source_id[source_id]
        assert len(rows) == expected_cells
        for observation in rows:
            assert _axis_is_unknown(observation.identity.temperature_K)
            assert _axis_is_unknown(observation.identity.total_pressure_Pa)
            assert _axis_is_unknown(observation.identity.fO2_Pa)
            assert _axis_is_unknown(observation.identity.composition)

    measured_rows = by_source_id[measured_id]
    assert len(measured_rows) == 1
    measured = measured_rows[0]
    assert measured.identity.quantity.is_unknown
    assert measured.identity.species.phase.value is Phase.G
    assert measured.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
    assert _axis_is_unknown(measured.identity.total_pressure_Pa)
    assert _axis_is_unknown(measured.identity.fO2_Pa)
    conditions = measured.point_conditions
    assert conditions is not None
    temperature = conditions["temperature_K"]
    assert temperature.state.value.kind.value == "interval"
    assert temperature.state.value.interval_low == Decimal("2100")
    assert temperature.state.value.interval_high == Decimal("2200")
    assert temperature.locator.page == 9
    pressure = conditions["total_pressure_Pa"]
    assert pressure.state.value.point == Decimal("15")
    assert pressure.state.value.approximate is True
    assert pressure.locator.page == 3
    assert conditions["atmosphere"].state.value == "N2"
    assert conditions["fO2_control"].state.is_unknown
    assert conditions["fO2_control"].locator.page == 3
    assert conditions["heating_rate_K_s"].state.is_unknown
    assert conditions["heating_rate_K_s"].locator.page == 7
