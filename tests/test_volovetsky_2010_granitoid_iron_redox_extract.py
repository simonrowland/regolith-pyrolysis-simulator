from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/volovetsky-2010-granitoid-iron-redox.yaml"
COMPONENTS = ["SiO2", "TiO2", "Al2O3", "FeO", "MnO", "MgO", "CaO", "Na2O", "K2O", "Cl", "ZrO2", "Total"]

# Independent transcription of Table 1 from the 600 dpi PDF page 3 render.
TABLE1_GRID = [
    ("P9", "Initial", ["68.28", "0.36", "7.39", "7.82", "0.31", "0.07", "0.34", "6.61", "4.17", "0.81", "0.33", "96.48"]),
    ("P9-N2", "1102 °C, 46 h, -0.7", ["70.00", "0.36", "7.71", "7.60", "0.28", "0.09", "0.37", "6.05", "4.13", "0.70", "0.36", "97.64"]),
    ("P9-37", "1420 °C, 15 h, -5.7", ["63.69", "0.35", "20.77", "4.05", "0.36", "0.22", "0.35", "3.52", "3.85", "0.12", "0.31", "97.81"]),
    ("I2", "Initial", ["70.46", "0.06", "15.28", "3.41", None, "0.14", "2.06", "4.07", "3.95", None, None, "99.48"]),
    ("I2-N15", "1240 °C, 20 h, -7.0", ["73.53", "0.06", "14.15", "2.98", None, "0.14", "1.62", "3.76", "3.87", None, None, "100.13"]),
    ("I3R-29", "1420 °C, 7 h, -5.7", ["74.22", "0.06", "13.03", "2.84", None, "0.13", "1.55", "2.06", "3.77", None, None, "97.70"]),
]


def _assert_grid(actual, expected):
    assert actual == expected


def test_volovetsky_table1_components_migrate_with_sample_conditions_and_species():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    migrated = migrator.result.observations
    assert len(migrated) == 22
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues

    actual_grid = {sample: {column: None for column in COMPONENTS} for sample, _, _ in TABLE1_GRID}
    for species, block in source["species"].items():
        for item in block["observations"]:
            point = item["values"]["points"][0]
            sample = point["sample"]
            component = item["values"]["component_as_published"]
            actual_grid[sample][component] = point["value"]
            observation = next(
                record for record in migrated.values()
                if record.observation_id.split("::")[1] == item["observation_id"]
            )
            assert observation.value.kind is ValueKind.POINT
            assert observation.value.point == Decimal(point["value"])
            assert observation.identity.species.formula == species == component
            assert observation.locator.page == 3
            assert observation.locator.table == "1"
            assert observation.locator.note.startswith(f"Table 1 row {sample}, column {component}.")
            assert observation.point_conditions["T_C"].locator.note.startswith(f"Table 1 row {sample}, column {component}.")
            assert observation.point_conditions["fO2_log"].locator.note.startswith(f"Table 1 row {sample}, column {component}.")
            if point.get("T_C") is None:
                assert observation.identity.temperature_K.is_unknown
                assert observation.point_conditions["T_C"].state.is_unknown
                assert observation.point_conditions["fO2_log"].state.is_unknown
            else:
                assert observation.identity.temperature_K.is_value
                assert observation.point_conditions["T_C"].state.value == Decimal(point["T_C"])
                assert observation.point_conditions["fO2_log"].state.value == Decimal(point["point_conditions"]["fO2_log"]["state"]["value"])

    total_context = next(
        item for item in source["species"]["Fe"]["context"]
        if item["observation_id"] == "volovetsky_2010_table1_total_percent_context"
    )
    for row in total_context["values"]["rows"]:
        actual_grid[row["sample"]]["Total"] = row["total_percent"]

    repeated_context = {
        item["locator"]["row"]: item
        for item in source["species"]["Fe"]["context"]
        if item["observation_id"].endswith("composition_repeated_in_2012")
    }
    assert set(repeated_context) == {"P9", "I2-N15", "I2", "I3R-29"}
    for sample, expected_observation in {
        "P9": "volo12_p9_initial_fe_redox",
        "I2-N15": "volo12_i2_granite_table1",
        "I2": "volo12_i2_granite_table1",
        "I3R-29": "volo12_i2_granite_table1",
    }.items():
        item = repeated_context[sample]
        assert item["values"]["duplicate_of_observation_id"] == expected_observation
        for component, value in item["values"]["components_as_printed"].items():
            actual_grid[sample][component] = value

    actual = [
        (sample, condition, [actual_grid[sample][component] for component in COMPONENTS])
        for sample, condition, _ in TABLE1_GRID
    ]
    _assert_grid(actual, TABLE1_GRID)

    # Mutation proof: a changed render transcription and a changed extract cell both fail.
    changed_grid = deepcopy(TABLE1_GRID)
    changed_grid[0] = (changed_grid[0][0], changed_grid[0][1], ["68.29", *changed_grid[0][2][1:]])
    with pytest.raises(AssertionError):
        _assert_grid(actual, changed_grid)
    changed_extract = deepcopy(actual)
    changed_extract[2] = (changed_extract[2][0], changed_extract[2][1], [*changed_extract[2][2][:-1], "97.80"])
    with pytest.raises(AssertionError):
        _assert_grid(changed_extract, TABLE1_GRID)

    figures = [
        item for block in source["species"].values() for item in block.get("context", [])
        if item.get("type") == "figure_only"
    ]
    assert {figure["locator"]["figure"] for figure in figures} == {"1", "2", "3"}
    for figure in figures:
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
        assert "points" not in figure["values"]
        assert all(isinstance(series["marker_count"], int) for series in figure["values"]["series"])
