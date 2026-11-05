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
    assert sum(len(points) for points in points_by_id.values()) == 84
    assert sum(len(points) for key, points in points_by_id.items() if "tablei_" in key) == 49
    assert sum(len(points) for key, points in points_by_id.items() if "tableii_" in key) == 35

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
    refused_rows = [
        observation
        for observation in extract["species"]["CaFe2O4"]["observations"]
        if observation["phase"] == "unknown"
    ]
    assert len(refused_rows) == 1
    assert "incongruent-melting interval" in refused_rows[0]["values"]["phase_refusal_reason"]
    assert all(point["phase_reason"] == "Incongruent melting interval; no per-row phase printed." for point in refused_rows[0]["values"]["points"])
    assert not [row for row in rows if row.identity.quantity.is_unknown]
