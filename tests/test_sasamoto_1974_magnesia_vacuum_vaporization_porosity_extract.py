"""Source pins for the Sasamoto et al. 1974 MgO vaporization extract."""

from __future__ import annotations

import copy
from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery import migrate as battery_migrate
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import check_fidelity_sample_matches


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_PATH = (
    ROOT
    / "data"
    / "literature"
    / "extracts"
    / "sasamoto-1974-magnesia-vacuum-vaporization-porosity.yaml"
)
SOURCE_ID = "sasamoto-1974-magnesia-vacuum-vaporization-porosity"


def _by_id(extract: dict, observation_id: str) -> dict:
    return next(
        observation
        for block in extract["species"].values()
        for observation in block.get("observations", [])
        if observation["observation_id"] == observation_id
    )


def _migrated_by_id(migrator, observation_id: str):
    return next(
        observation
        for key, observation in migrator.result.observations.items()
        if key == observation_id or key.endswith(f"::{observation_id}")
    )


def test_sasamoto_1974_table_cells_conditions_and_migration_are_pinned() -> None:
    extract = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    assert extract["review_status"] == "draft"
    mg_rows = extract["species"]["Mg"]["observations"]
    mgo_rows = extract["species"]["MgO"]["observations"]
    table3_pressure = [
        row for row in mg_rows if row["observation_id"].endswith("langmuir_mg_partial_pressure")
    ]
    table3_alpha = [
        row for row in mg_rows if "table3_" in row["observation_id"] and row["type"] == "alpha"
    ]
    table3_rate = [
        row for row in mgo_rows if row["observation_id"].endswith("areal_rate_typed_refusal")
    ]
    table4_fit = [
        row for row in mg_rows if row["observation_id"].endswith("overall_alpha_fit")
    ]
    table4_parameters = [
        row for row in mgo_rows if row["observation_id"].endswith("activation_parameters_typed_refusal")
    ]

    assert len(table3_pressure) == len(table3_alpha) == len(table3_rate) == 32
    assert len(table4_fit) == len(table4_parameters) == 6
    table2 = next(
        row for row in extract["species"]["MgO"]["context"]
        if row["observation_id"].endswith("table2_specimen_porosity_density_and_molding_pressure")
    )
    assert len(table2["values"]["rows"]) == 6
    table1 = next(
        row for row in extract["species"]["MgO"]["context"]
        if row["observation_id"].endswith("table1_impurity_contents_typed_refusal")
    )
    assert len(table1["values"]["rows"]) == 17

    rendered_pins = (
        ("sasamoto_1974_table3_1_1_langmuir_mg_partial_pressure", "pressure_atm", "5.98e-10", 4, "1-1"),
        ("sasamoto_1974_table3_6_5_areal_rate_typed_refusal", "mass_loss_rate_per_area_as_printed", "146", 4, "6-5"),
        ("sasamoto_1974_table3_1_1_mg_vaporization_coefficient", "alpha", "0.104", 4, "1-1"),
        ("sasamoto_1974_table4_specimen_6_overall_alpha_fit", "overall_coefficient_as_printed", "α̅_MgO = 12.3 exp(-9,130/RT)", 7, "6"),
    )
    for observation_id, field, expected, page, row_label in rendered_pins:
        observation = _by_id(extract, observation_id)
        assert observation["values"][field] == expected
        assert observation["locator"]["page"] == page
        assert observation["locator"]["row_label"] == row_label

    for observation in [*table3_pressure, *table3_alpha, *table3_rate]:
        values = observation["values"]
        conditions = observation["point_conditions"]
        assert values["T_C"]
        assert conditions["T_C"]["state"]["tag"] == "value"
        assert conditions["T_C"]["locator"]["page"] == 4
        assert conditions["apparent_porosity_pct"]["state"]["tag"] == "value"
        assert conditions["bulk_density_g_cm3"]["state"]["tag"] == "value"
        assert conditions["composition"]["state"] == {
            "tag": "unknown",
            "reason": "not_published",
        }
        for absent_condition in (
            "total_pressure_Pa",
            "fO2_Pa",
            "operation_duration_min",
            "total_mass_loss_g",
            "surface_area_m2",
        ):
            assert conditions[absent_condition]["state"] == {
                "tag": "unknown",
                "reason": "not_published",
            }

    assert "99.9%" not in table3_pressure[0]["point_conditions"]["sample_material_as_printed"]["state"]["value"]
    assert "99.9%" in table3_pressure[8]["point_conditions"]["sample_material_as_printed"]["state"]["value"]
    assert "10^-5 torr" not in str(table3_pressure[0]["point_conditions"]["total_pressure_Pa"]["state"])
    assert "101325" not in EXTRACT_PATH.read_text(encoding="utf-8")

    migrator = migrate_one_extract(
        ROOT,
        EXTRACT_PATH,
        index={SOURCE_ID: dict(extract["source"], source_id=SOURCE_ID)},
        aliases={},
    )
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 108
    assert result.validation.hard_issues == ()
    assert Counter(row.value.kind.value for row in result.observations.values()) == {
        "point": 64,
        "unavailable": 44,
    }

    for observation in [*table3_pressure, *table3_alpha, *table3_rate]:
        migrated = _migrated_by_id(migrator, observation["observation_id"])
        temperature = migrated.identity.temperature_K
        assert temperature.is_value
        assert migrated.point_conditions["temperature_K"].state.is_value
        assert migrated.point_conditions["temperature_K"].locator.page == 4
        assert migrated.point_conditions["apparent_porosity_pct"].state.is_value
        assert migrated.point_conditions["apparent_porosity_pct"].locator.page == 4

    identity_pins = (
        ("sasamoto_1974_table3_1_1_langmuir_mg_partial_pressure", Decimal("1673.15")),
        ("sasamoto_1974_table3_3_2_langmuir_mg_partial_pressure", Decimal("1773.15")),
        ("sasamoto_1974_table3_6_5_langmuir_mg_partial_pressure", Decimal("1913.15")),
    )
    for observation_id, expected_temperature in identity_pins:
        identity = _migrated_by_id(migrator, observation_id).identity
        assert identity.quantity.is_value
        assert identity.quantity.value is battery_migrate.Quantity.P_PARTIAL
        assert identity.temperature_K.is_value
        assert identity.temperature_K.value == expected_temperature
        assert identity.composition.is_unknown
        assert identity.total_pressure_Pa.is_unknown
        assert identity.fO2_Pa.is_unknown

    assert all(check_fidelity_sample_matches(extract, sample) == [] for sample in extract["fidelity_samples"])
    mutated = copy.deepcopy(extract)
    _by_id(mutated, "sasamoto_1974_table3_1_1_langmuir_mg_partial_pressure")["values"]["pressure_atm"] = "5.99e-10"
    first_sample = next(
        sample for sample in extract["fidelity_samples"]
        if sample["observation_id"] == "sasamoto_1974_table3_1_1_langmuir_mg_partial_pressure"
    )
    assert check_fidelity_sample_matches(mutated, first_sample)
