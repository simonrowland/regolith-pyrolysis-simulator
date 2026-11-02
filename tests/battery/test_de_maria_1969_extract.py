from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, Phase, Quantity, ValueKind
from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract


SOURCE_ID = "de-maria-1969-rend-lincei"
EXTRACT_NAME = f"{SOURCE_ID}.yaml"
EXTRACT_PATH = REPO_ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME


def test_de_maria_1969_extraction_renders_and_migration(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    species = document["species"]

    source_observations = [
        row
        for block in species.values()
        for row in block.get("observations", [])
    ]
    for row in source_observations:
        conditions = [row.get("point_conditions", {})]
        conditions.extend(
            point.get("point_conditions", {})
            for point in row.get("values", {}).get("points", [])
        )
        for point_conditions in conditions:
            for axis in ("total_pressure_Pa", "fO2_Pa", "composition"):
                assert point_conditions[axis]["state"]["tag"] == "unknown"
    table_i = [
        row for row in source_observations
        if row["observation_id"].startswith("de_maria_1969_table1_mq_")
    ]
    table_i_series = [
        row
        for row in table_i
        if row["observation_id"].removesuffix("_upper_limit_typed_refusal")
        == row["observation_id"].split("_row_")[0]
    ]
    table_i_limits = [row for row in table_i if row not in table_i_series]
    table_i_points = [point for row in table_i_series for point in row["values"]["points"]]
    table_i_temperatures = {
        int(point["T_K"]) for point in table_i_points
    } | {int(row["T_range_K"][0]) for row in table_i_limits}

    assert len(table_i_series) == 15
    assert len(table_i_points) == 125
    assert len(table_i_limits) == 8
    assert len(table_i_points) + len(table_i_limits) == 133
    assert table_i_temperatures == {
        1210, 1280, 1350, 1420, 1500, 1560, 1600, 1660, 1710,
        1780, 1830, 1880, 1900, 1980, 2150, 2250, 2330,
    }

    expected_render_cells = {
        ("de_maria_1969_table1_mq_23", "ion_intensity_as_printed", "1210", "2 · 10^-13", 7, "I"),
        ("de_maria_1969_table1_mq_39_row_1880_upper_limit_typed_refusal", "ion_intensity_as_printed", None, "< 0.5 · 10^-13", 7, "I"),
        ("de_maria_1969_table1_mq_44", "ion_intensity_as_printed", "1880", "10 · 10^-12", 7, "I"),
        ("de_maria_1969_table1_mq_56", "ion_intensity_as_printed", "1980", "12.6 · 10^-12", 8, "I"),
    }
    for observation_id, field, temperature, expected, page, table in expected_render_cells:
        row = next(
            row for block in species.values() for row in block.get("observations", [])
            if row["observation_id"] == observation_id
            or row["observation_id"].startswith(observation_id + "_row_")
        )
        if temperature is None:
            actual = row["values"][field]
        else:
            point = next(
                point for point in row["values"]["points"]
                if point["T_K"] == temperature
            )
            actual = point[field]
        assert actual == expected
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table
        assert row["locator"]["published_page"] == (529 if page == 7 else 530)

    all_table_ii = [
        row for block in species.values() for row in block.get("observations", [])
        if row["observation_id"].startswith("de_maria_1969_table2_mq_")
    ]
    assert len(all_table_ii) == 11
    table_ii = [
        row for row in all_table_ii
        if row["observation_id"].endswith("mq_23_appearance_potential_typed_refusal")
    ]
    assert len(table_ii) == 1
    assert table_ii[0]["values"]["appearance_potential_as_printed"] == "5.1 ± 0.3 eV"
    assert table_ii[0]["locator"]["page"] == 12
    assert table_ii[0]["locator"]["published_page"] == 534
    assert table_ii[0]["locator"]["table"] == "II"

    prose_ap = species["SiO"]["observations"][-1]
    assert prose_ap["values"]["appearance_potential_as_printed"] == "12.0 ± 0.8 eV"
    assert prose_ap["locator"]["page"] == 12
    assert prose_ap["locator"]["published_page"] == 534

    pressure_rows = [
        row for block in species.values() for row in block.get("observations", [])
        if row["type"] == "partial_pressure"
    ]
    assert len(pressure_rows) == 3
    assert {
        (row["values"]["formula"], row["values"]["points"][0]["pressure_as_printed"])
        for row in pressure_rows
    } == {
        ("Na", "of the order 10^-5 atm"),
        ("K", "of the order 10^-4 atm"),
        ("Fe", "5 · 10^-5 atm"),
    }

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    unavailable_count = sum(
        row.value.kind is ValueKind.UNAVAILABLE for row in result.observations.values()
    )
    print(
        f"isolated migration: observations={len(result.observations)} "
        f"hard={len(result.validation.hard_issues)} unavailable={unavailable_count}"
    )
    assert len(result.observations) == 149
    known_temperature_ids = {
        "de_maria_1969_results_na_partial_pressure",
        "de_maria_1969_results_k_partial_pressure",
        "de_maria_1969_results_fe_partial_pressure",
    }
    for row in result.observations.values():
        assert row.point_conditions is not None
        for axis in ("total_pressure_Pa", "fO2_Pa", "composition"):
            state = getattr(row.identity, axis)
            assert state is None or not state.is_value
        temperature = row.point_conditions.get("temperature_K")
        assert temperature is not None and temperature.locator is not None
        has_known_temperature = (
            "de_maria_1969_table1_mq_" in row.observation_id
            or any(local_id in row.observation_id for local_id in known_temperature_ids)
        )
        if has_known_temperature:
            assert temperature.state.is_value
            assert row.identity.temperature_K is not None
            assert row.identity.temperature_K.is_value
            assert temperature.state.value == row.identity.temperature_K.value
        else:
            assert temperature.state.is_unknown
            state = row.identity.temperature_K
            assert state is None or not state.is_value

    unknown_quantity_ids = {
        row.observation_id.split("::")[-1]
        for row in result.observations.values()
        if row.identity.quantity.is_unknown
    }
    expected_unknown_quantity_ids = {
        *(
            f"de_maria_1969_table2_mq_{mass}_appearance_potential_typed_refusal"
            for mass in (23, 24, 25, 26, 39, 44, 50, 51, 52, 56, 78)
        ),
        "de_maria_1969_results_sio_appearance_potential_prose_typed_refusal",
    }
    assert unknown_quantity_ids == expected_unknown_quantity_ids
    assert all(row.identity.species.phase.is_value for row in result.observations.values())
    assert all(row.identity.species.phase.value is Phase.G for row in result.observations.values())

    table_i_migrated = [
        row for row in result.observations.values()
        if "de_maria_1969_table1_mq_" in row.observation_id
    ]
    assert len(table_i_migrated) == 133
    for row in table_i_migrated:
        temperature = row.identity.temperature_K
        condition = (row.point_conditions or {}).get("temperature_K")
        assert temperature is not None and temperature.is_value
        assert condition is not None and condition.state.is_value
        assert condition.state.value == temperature.value
        assert condition.locator is not None
        assert not row.identity.total_pressure_Pa.is_value
        assert not row.identity.fO2_Pa.is_value
        assert not row.identity.composition.is_value

    limits = {
        row.observation_id.split("::")[-1]: row
        for row in table_i_migrated
        if row.observation_id.endswith("_upper_limit_typed_refusal")
    }
    expected_limits = {
        f"de_maria_1969_table1_mq_{mass}_row_{temperature}_upper_limit_typed_refusal"
        for mass, temperature in (
            (23, 1780), (24, 1420), (24, 1780), (25, 1830),
            (26, 1980), (39, 1880), (50, 1900), (56, 2150),
        )
    }
    assert set(limits) == expected_limits
    assert all(row.identity.quantity.value is Quantity.ION_INTENSITY for row in limits.values())
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in limits.values())
    assert all(row.admission.status is AdmissionStatus.REJECTED for row in limits.values())

    identity_rows = [
        row for row in table_i_migrated
        if any(token in row.observation_id for token in (
            "de_maria_1969_table1_mq_23", "de_maria_1969_table1_mq_44", "de_maria_1969_table1_mq_56"
        )) and row.identity.temperature_K.is_value
    ]
    chosen = []
    for row in identity_rows:
        if row.identity.temperature_K.value in {Decimal("1210"), Decimal("1880"), Decimal("1980")}:
            chosen.append(row)
    chosen_by_temperature = {row.identity.temperature_K.value: row for row in chosen}
    assert set(chosen_by_temperature) == {Decimal("1210"), Decimal("1880"), Decimal("1980")}
    for temperature, row in chosen_by_temperature.items():
        print(
            f"identity T={temperature}: composition={row.identity.composition} "
            f"P={row.identity.total_pressure_Pa} fO2={row.identity.fO2_Pa}"
        )
        assert not row.identity.composition.is_value
        assert not row.identity.total_pressure_Pa.is_value
        assert not row.identity.fO2_Pa.is_value

    migrated_pressures = {
        row.identity.species.formula: row
        for row in result.observations.values()
        if "de_maria_1969_results_" in row.observation_id
        and "_partial_pressure" in row.observation_id
    }
    assert set(migrated_pressures) == {"Na", "K", "Fe"}
    assert migrated_pressures["Na"].value.point == Decimal("1.01325")
    assert migrated_pressures["K"].value.point == Decimal("10.1325")
    assert migrated_pressures["Fe"].value.point == Decimal("5.06625")
    assert migrated_pressures["Na"].value.approximate is True
    assert migrated_pressures["K"].value.approximate is True
    assert migrated_pressures["Fe"].value.approximate is False
    assert all(not row.identity.total_pressure_Pa.is_value for row in migrated_pressures.values())
    assert all(not row.identity.fO2_Pa.is_value for row in migrated_pressures.values())
    assert all(not row.identity.composition.is_value for row in migrated_pressures.values())
