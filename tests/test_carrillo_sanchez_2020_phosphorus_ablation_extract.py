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


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
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


def _table3_census(document: dict) -> tuple[int, int, set[int]]:
    flux_points = 0
    ablation_percentages = 0
    rows: set[int] = set()
    for row in _observations(document).values():
        values = row.get("values", {})
        if row.get("locator", {}).get("table") != "3":
            continue
        if row.get("type") == "gas_speciation":
            for point in values.get("points", []):
                flux_points += 1
                rows.add(int(point["table_row_number"]))
        elif values.get("quantity") == "yield_fraction":
            ablation_percentages += 1
            rows.add(int(values["table_row_number"]))
    return flux_points, ablation_percentages, rows


def _local_observation_id(observation) -> str:
    prefix = f"{SOURCE_ID}::"
    assert observation.observation_id.startswith(prefix)
    return observation.observation_id[len(prefix) :].split("::", 1)[0]


def _axis_is_unknown(axis) -> bool:
    return axis is None or axis.is_unknown


def test_table3_full_cell_count_and_render_pins_reject_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []
    _assert_render_pins(document)

    flux_points, percentages, table_rows = _table3_census(document)
    assert flux_points == 54
    assert percentages == 12
    assert table_rows == set(range(2, 17))

    mutated = deepcopy(document)
    earth_p = _observations(mutated)["carrillo_sanchez_2020_table3_earth_p_mass_influx"]
    earth_p["values"]["points"][0]["value"] = "0.000029"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_isolated_migration_preserves_conditions_schema_gaps_and_percent_units(
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    observations = list(result.observations.values())
    by_source_id: dict[str, list] = {}
    for observation in observations:
        by_source_id.setdefault(_local_observation_id(observation), []).append(observation)

    assert len(observations) == 67
    assert result.validation.hard_issues == ()
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 55
    assert all(row.identity.species.phase.is_value for row in observations)
    assert all(row.identity.species.phase.value is Phase.G for row in observations)

    table3_flux_rows = [
        row
        for row in observations
        if _local_observation_id(row).startswith("carrillo_sanchez_2020_table3_")
        and "ablation_fraction" not in _local_observation_id(row)
    ]
    percent_rows = [
        row
        for row in observations
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.YIELD_FRACTION
    ]
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
    assert len(actual_quantity_unknown) == 55
    assert len(table3_flux_rows) == 54
    assert len(percent_rows) == 12

    expected_yields = {
        "carrillo_sanchez_2020_table3_earth_jfc_p_ablation_fraction": Decimal("0.25"),
        "carrillo_sanchez_2020_table3_earth_ast_p_ablation_fraction": Decimal("0.57"),
        "carrillo_sanchez_2020_table3_earth_htc_p_ablation_fraction": Decimal("0.96"),
        "carrillo_sanchez_2020_table3_earth_total_p_ablation_fraction": Decimal("0.41"),
        "carrillo_sanchez_2020_table3_mars_jfc_p_ablation_fraction": Decimal("0.11"),
        "carrillo_sanchez_2020_table3_mars_ast_p_ablation_fraction": Decimal("0.03"),
        "carrillo_sanchez_2020_table3_mars_htc_p_ablation_fraction": Decimal("0.93"),
        "carrillo_sanchez_2020_table3_mars_total_p_ablation_fraction": Decimal("0.38"),
        "carrillo_sanchez_2020_table3_venus_jfc_p_ablation_fraction": Decimal("0.36"),
        "carrillo_sanchez_2020_table3_venus_ast_p_ablation_fraction": Decimal("0.67"),
        "carrillo_sanchez_2020_table3_venus_htc_p_ablation_fraction": Decimal("0.98"),
        "carrillo_sanchez_2020_table3_venus_total_p_ablation_fraction": Decimal("0.53"),
    }
    migrated_yields = {
        _local_observation_id(row): row for row in percent_rows
    }
    assert set(migrated_yields) == set(expected_yields)
    for observation_id, expected in expected_yields.items():
        observation = migrated_yields[observation_id]
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected
        assert observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert observation.identity.species.phase.value is Phase.G
        assert observation.locator.page == 13
        assert observation.locator.table == "3"

    for observation in table3_flux_rows:
        assert observation.identity.quantity.is_unknown
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

    for observation in percent_rows:
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
        "carrillo_sanchez_2020_table3_earth_p_mass_influx": 6,
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

    assert len(source_rows) == 25
