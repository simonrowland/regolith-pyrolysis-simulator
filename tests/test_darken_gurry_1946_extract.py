from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


EXTRACT = Path(
    "data/literature/extracts/darken-gurry-1946-iron-oxygen-ii.yaml"
)
PINNED_CELLS = (
    ("dg46_1946_table_i_liquid_composition", 0, "r", "1.096", 3),
    ("dg46_1946_table_iii_a_iron_boundary", 0, "r", "1.081", 6),
    ("dg46_1946_table_iv_a_wustite_liquid", 5, "log_pO2_atm", "-6.14", 8),
    ("dg46_1946_table_vi_activities", 0, "neg_log_a_FeO", "-0.0255", 11),
)


def _assert_render_pins(extract):
    observations = {
        observation["observation_id"]: observation
        for species in extract["species"].values()
        for observation in species["observations"]
    }
    for observation_id, point_index, column, value, page in PINNED_CELLS:
        point = observations[observation_id]["values"]["points"][point_index]
        assert point[column] == value
        assert point["locator"]["page"] == page


def test_darken_gurry_1946_render_pins_and_migration_conditions():
    extract = yaml.safe_load(EXTRACT.read_text())
    _assert_render_pins(extract)

    migrator = Migrator(Path.cwd(), index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues

    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == EXTRACT.stem
    ]
    assert len(observations) == 159
    assert all(observation.point_conditions for observation in observations)
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


def test_darken_gurry_1946_render_pin_detects_cell_mutation(tmp_path):
    original = EXTRACT.read_text()
    mutated = original.replace(
        "neg_log_a_FeO: '-0.0255'",
        "neg_log_a_FeO: '-0.0256'",
        1,
    )
    assert mutated != original
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(mutated)

    with pytest.raises(AssertionError):
        _assert_render_pins(yaml.safe_load(mutated_extract.read_text()))
