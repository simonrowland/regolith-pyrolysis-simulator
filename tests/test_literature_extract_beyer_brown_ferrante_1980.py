from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/beyer-brown-ferrante-1980-kalo2-thermodynamics.yaml"
)


def _source_observations():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = document["species"]["KAlO2"]["observations"]
    return document, {row["observation_id"]: row for row in rows}


def _point_at(observation, temperature):
    return next(
        point
        for point in observation["values"]["points"]
        if Decimal(str(point["T_K"])) == Decimal(temperature)
    )


def test_beyer_1980_full_cell_transcription_and_isolated_migration():
    assert validate_extract_file(EXTRACT) == []
    document, source = _source_observations()
    direct_cp = source["beyer80_table1_experimental_cp"]
    enthalpy = source["beyer80_table2_enthalpy_increments"]
    smooth_cp = source["beyer80_table3_smoothed_cp"]
    smooth_entropy = source["beyer80_table3_entropy_increment_from_zero"]
    smooth_g = source["beyer80_table3_negative_gibbs_function_over_T"]
    smooth_h = source["beyer80_table3_enthalpy_increment_from_zero"]

    # Independent pins from the rendered pages: Table 1 and 2 on PDF p. 4;
    # Table 3 on PDF pp. 4–5. Table 1 row layout was checked across all four
    # interleaved column pairs; Tables 2 and 3 were checked across all rows.
    table1 = _point_at(direct_cp, "51.58")
    assert table1["value"] == "16.209"
    assert table1["locator"] == {
        "page": 4,
        "published_page": 988,
        "table": "1",
        "row": 3,
        "column": 2,
    }
    assert table1["series_as_printed"] == "Series I"
    table2 = _point_at(enthalpy, "406.1")
    assert table2["value"] == "8850"
    assert table2["locator"]["page"] == 4
    assert table2["locator"]["table"] == "2"
    assert table2["locator"]["column"] == 2
    assert enthalpy["values"]["reference_temperature_K"] == "298.15"
    row_298 = _point_at(smooth_cp, "298.15")
    assert row_298["value"] == "76.12"
    assert row_298["locator"]["page"] == 5
    assert row_298["locator"]["published_page"] == 989
    assert _point_at(smooth_entropy, "298.15")["value"] == "85.55"
    assert _point_at(smooth_g, "298.15")["value"] == "40.76"
    assert _point_at(smooth_h, "298.15")["value"] == "13355"

    assert len(direct_cp["values"]["points"]) == 54
    assert len(enthalpy["values"]["points"]) == 16
    assert all(
        len(source[key]["values"]["points"]) == 59
        for key in (
            "beyer80_table3_smoothed_cp",
            "beyer80_table3_entropy_increment_from_zero",
            "beyer80_table3_negative_gibbs_function_over_T",
            "beyer80_table3_enthalpy_increment_from_zero",
        )
    )

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = [
        row
        for row in migrator.result.observations.values()
        if row.source_id == document["source_id"]
    ]

    assert len(migrated) == 308
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert sum(row.value.kind.value == "unavailable" for row in migrated) == 178
    expected_schema_gaps = {
        "beyer80_table3_entropy_increment_from_zero",
        "beyer80_table3_negative_gibbs_function_over_T",
        "beyer80_table3_enthalpy_increment_from_zero",
        "beyer80_transition_enthalpy_schema_gap",
    }
    unknown_quantity_ids = {
        row.observation_id.split("::")[1]
        for row in migrated
        if row.identity.quantity.value is None
    }
    assert unknown_quantity_ids == expected_schema_gaps
    assert all(row.identity.species.phase.value is not None for row in migrated)
    assert all("temperature_K" in row.point_conditions for row in migrated)
    assert all(
        row.identity.temperature_K.value is not None
        or row.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
        for row in migrated
    )

    # Each direct row keeps its printed experiment pressure and row temperature.
    for table_id, pressure in (
        ("beyer80_table1_experimental_cp", Decimal("6700")),
        ("beyer80_table2_enthalpy_increments", Decimal("101000")),
    ):
        rows = [row for row in migrated if f"::{table_id}::" in row.observation_id]
        assert len(rows) == (54 if table_id.endswith("cp") else 16)
        for row in rows:
            conditions = row.point_conditions
            assert conditions["temperature_K"].state.value == row.identity.temperature_K.value
            assert conditions["total_pressure_Pa"].state.value == pressure

    measured_cp = next(
        row
        for row in migrated
        if "::beyer80_table3_smoothed_cp::" in row.observation_id
        and row.identity.temperature_K.value == Decimal("298.15")
    )
    assert measured_cp.identity.quantity.value is Quantity.CP
    assert measured_cp.value.point == Decimal("76.12")
    measured_increment = next(
        row
        for row in migrated
        if "::beyer80_table2_enthalpy_increments::" in row.observation_id
        and row.identity.temperature_K.value == Decimal("406.1")
    )
    assert measured_increment.identity.quantity.value is Quantity.H_MINUS_H298
    assert measured_increment.value.point == Decimal("8.85")
