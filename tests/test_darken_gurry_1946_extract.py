from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase
from simulator.battery.migrate import Migrator


EXTRACT = Path(
    "data/literature/extracts/darken-gurry-1946-iron-oxygen-ii.yaml"
)
PINNED_CELLS = (
    ("dg46_1946_table_i_liquid_composition", "A1", "r", "1.096", 3),
    ("dg46_1946_table_i_liquid_composition", "L3", "T_C", "1596", 3),
    ("dg46_1946_table_iii_a_iron_boundary", "1", "r", "1.081", 6),
    ("dg46_1946_table_iv_a_wustite_liquid", "6", "log_pO2_atm", "-6.14", 8),
    ("dg46_1946_table_vi_oxygen_potential", "1500-1", "N2_over_N1", "1.0225", 11),
    ("dg46_1946_table_vi_activities", "1400-1", "neg_log_a_FeO", "-0.0255", 11),
)


def _assert_render_pins(extract):
    observations = {
        observation["observation_id"]: observation
        for species in extract["species"].values()
        for observation in species["observations"]
    }
    for observation_id, row, column, value, page in PINNED_CELLS:
        point = next(
            point
            for point in observations[observation_id]["values"]["points"]
            if point["locator"]["row"] == row
        )
        assert point[column] == value
        assert point["locator"]["page"] == page


def test_darken_gurry_1946_render_pins_and_migration_conditions():
    extract = yaml.safe_load(EXTRACT.read_text())
    _assert_render_pins(extract)

    table_i_points = extract["species"]["FeO"]["observations"][0]["values"][
        "points"
    ]
    assert [
        point["time_at_temperature_as_printed"]
        for point in table_i_points
        if point.get("time_at_temperature_as_printed")
    ] == ["5½", "5½", "3¼", "3¼", "4½", "4½"]

    migrator = Migrator(Path.cwd(), index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues

    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == EXTRACT.stem
    ]
    assert len(observations) == 181
    assert all(observation.point_conditions for observation in observations)
    assert all(
        observation.identity.species.phase.value is Phase.L
        for observation in observations
    )
    assert {observation.identity.species.formula for observation in observations} == {
        "FeO"
    }
    assert all(
        condition.locator.published_page == observation.locator.published_page
        for observation in observations
        for condition in observation.point_conditions.values()
    )

    unknown_temperatures = 0
    for observation in observations:
        temperature = observation.point_conditions["temperature_K"]
        if observation.identity.temperature_K is None:
            unknown_temperatures += 1
            assert temperature.state.tag.value == "unknown"
            continue
        assert temperature.state.value == observation.identity.temperature_K.value
    assert unknown_temperatures == 6

    composition_rows = [
        observation
        for observation in observations
        if observation.locator.table in {"I", "III A"}
    ]
    assert len(composition_rows) == 89
    assert all(
        observation.point_conditions["composition"].state.is_value
        and observation.point_conditions["composition"].locator.page
        == observation.locator.page
        and observation.point_conditions["composition"].locator.table
        == observation.locator.table
        for observation in composition_rows
    )
    assert all(
        observation.identity.composition is None for observation in composition_rows
    )
    for table in ("V", "VI"):
        derived = [
            observation
            for observation in observations
            if observation.locator.table == table
            and observation.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        ]
        assert derived
        assert all(observation.derived_from for observation in derived)
        assert all(observation.derivation is not None for observation in derived)


@pytest.mark.parametrize(
    ("original_cell", "mutated_cell"),
    [
        (
            "T_C: '1596', starting_material: Fe2O3, time_at_temperature_h: '1', r: '4.067'",
            "T_C: '1590', starting_material: Fe2O3, time_at_temperature_h: '1', r: '4.067'",
        ),
        ("neg_log_a_FeO: '-0.0255'", "neg_log_a_FeO: '-0.0256'"),
    ],
)
def test_darken_gurry_1946_render_pin_detects_cell_mutation(
    tmp_path, original_cell, mutated_cell
):
    original = EXTRACT.read_text()
    mutated = original.replace(original_cell, mutated_cell, 1)
    assert mutated != original
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(mutated)

    with pytest.raises(AssertionError):
        _assert_render_pins(yaml.safe_load(mutated_extract.read_text()))
