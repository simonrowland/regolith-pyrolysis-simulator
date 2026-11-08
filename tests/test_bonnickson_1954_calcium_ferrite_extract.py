from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/bonnickson-1954-calcium-magnesium-ferrite-heat-contents.yaml"
)
SOURCE_ID = "bonnickson-1954-calcium-magnesium-ferrite-heat-contents"


def test_bonnickson_calcium_ferrite_rows_match_render_and_migrate() -> None:
    """Pin printed table cells and the source-ruled phase refusals."""
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    points_by_id = {
        observation["observation_id"]: observation["values"]["points"]
        for species in extract["species"].values()
        for observation in species["observations"]
        if observation["values"].get("points")
    }
    observations_by_id = {
        observation["observation_id"]: observation
        for species in extract["species"].values()
        for observation in species["observations"]
    }
    assert sum(len(points) for points in points_by_id.values()) == 84
    assert sum(len(points) for key, points in points_by_id.items() if "tablei_" in key) == 49
    assert sum(len(points) for key, points in points_by_id.items() if "tableii_" in key) == 35

    ca2_crystal = points_by_id["bonnickson_1954_tableii_ca2fe2o5_cr_heat_content"]
    ca2_liquid = points_by_id["bonnickson_1954_tableii_ca2fe2o5_l_heat_content"]
    crystal_1750 = next(point for point in ca2_crystal if point["T_K"] == 1750.0)
    liquid_1750 = next(point for point in ca2_liquid if point["T_K"] == 1750.0)
    assert (crystal_1750["value"], crystal_1750["mark_as_printed"]) == (82760, "c")
    assert (liquid_1750["value"], liquid_1750["mark_as_printed"]) == (118870, "l")
    assert observations_by_id["bonnickson_1954_tableii_ca2fe2o5_cr_heat_content"]["phase"] == "cr"
    assert observations_by_id["bonnickson_1954_tableii_ca2fe2o5_l_heat_content"]["phase"] == "l"
    assert crystal_1750["locator"]["page"] == liquid_1750["locator"]["page"] == 2
    assert crystal_1750["locator"]["table"] == liquid_1750["locator"]["table"] == "II"

    render_pins = {
        ("bonnickson_1954_tablei_cafe2o4_cr_heat_content", 411.3): 4220,
        ("bonnickson_1954_tablei_ca2fe2o5_cr_heat_content", 376.5): 3695,
        ("bonnickson_1954_tableii_ca2fe2o5_cr_heat_content", 400.0): 4870,
    }
    for (observation_id, temperature), expected_cal_mol in render_pins.items():
        point = next(
            point
            for point in points_by_id[observation_id]
            if point["T_K"] == temperature
        )
        assert point["value"] == expected_cal_mol
        assert point["locator"]["page"] == 2
        assert point["locator"]["table"] in {"I", "II"}

    migrator = Migrator(REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    rows = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == SOURCE_ID
    ]
    assert len(rows) == 86
    heat_rows = [row for row in rows if "heat_content" in row.observation_id]
    fusion_rows = [row for row in rows if "heat_of_fusion" in row.observation_id]
    assert len(heat_rows) == 84
    assert len(fusion_rows) == 2
    ca2_liquid_1750_rows = [
        row
        for row in rows
        if "bonnickson_1954_tableii_ca2fe2o5_l_heat_content" in row.observation_id
        and row.identity.temperature_K.value == Decimal("1750.0")
    ]
    assert len(ca2_liquid_1750_rows) == 1
    assert ca2_liquid_1750_rows[0].value.point == Decimal("497.35208")
    assert ca2_liquid_1750_rows[0].identity.species.phase.value == "l"
    ca2_crystal_1750_rows = [
        row
        for row in rows
        if "bonnickson_1954_tableii_ca2fe2o5_cr_heat_content" in row.observation_id
        and row.identity.temperature_K.value == Decimal("1750.0")
    ]
    assert len(ca2_crystal_1750_rows) == 1
    assert ca2_crystal_1750_rows[0].value.point == Decimal("346.26784")
    assert ca2_crystal_1750_rows[0].identity.species.phase.value == "cr"
    assert all(
        observation.identity.temperature_K.is_value
        and observation.identity.total_pressure_Pa.is_not_applicable
        for observation in rows
    )
    assert all(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.H_MINUS_H298
        for observation in heat_rows
    )
    assert all(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ENTHALPY_OF_FUSION
        for observation in fusion_rows
    )

    migrated_pins = {
        ("bonnickson_1954_tablei_cafe2o4_cr_heat_content", "T=411.3"): Decimal("17.65648"),
        ("bonnickson_1954_tablei_ca2fe2o5_cr_heat_content", "T=376.5"): Decimal("15.45988"),
        ("bonnickson_1954_tableii_ca2fe2o5_cr_heat_content", "T=400.0"): Decimal("20.37608"),
    }
    for (source_observation, temperature_fragment), expected_kj_mol in migrated_pins.items():
        observation = next(
            row
            for row in rows
            if source_observation in row.observation_id
            and temperature_fragment in row.observation_id
        )
        assert observation.value.point == expected_kj_mol
        assert observation.identity.temperature_K.value == Decimal(
            temperature_fragment.removeprefix("T=")
        )

    phase_unknown = {
        row.observation_id
        for row in rows
        if row.identity.species.phase.is_unknown
    }
    assert len(phase_unknown) == 2
    assert all("tablei_cafe2o4_unknown_a_heat_content" in row for row in phase_unknown)
    refused_migrated = [row for row in rows if row.observation_id in phase_unknown]
    refused_by_temperature = {row.identity.temperature_K.value: row for row in refused_migrated}
    assert set(refused_by_temperature) == {Decimal("1504.6"), Decimal("1529.7")}
    assert {
        temperature: row.value.point
        for temperature, row in refused_by_temperature.items()
    } == {
        Decimal("1504.6"): Decimal("269.11488"),
        Decimal("1529.7"): Decimal("313.67448"),
    }
    incongruent_reason = "The printed measurement lies in the source-stated incongruent-melting interval"
    assert all(incongruent_reason in row.admission.reason for row in refused_migrated)
    assert all(row.admission.status.value == "rejected" for row in refused_migrated)
    refused_rows = [
        observation
        for observation in extract["species"]["CaFe2O4"]["observations"]
        if observation["phase"] == "unknown"
    ]
    assert len(refused_rows) == 1
    assert "incongruent-melting interval" in refused_rows[0]["values"]["refusal_reason"]
    assert all(point["phase_reason"] == "Incongruent melting interval; no per-row phase printed." for point in refused_rows[0]["values"]["points"])
    assert not [row for row in rows if row.identity.quantity.is_unknown]
