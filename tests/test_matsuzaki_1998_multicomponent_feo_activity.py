from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/matsuzaki-1998-multicomponent-feo-activity.yaml"
SOURCE_ID = "matsuzaki-1998-multicomponent-feo-activity"


def _observations() -> list[dict]:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert document["source_id"] == SOURCE_ID
    return document["species"]["FeO"]["observations"]


def _sample_observation(observations: list[dict], table: str, sample: str, quantity: str) -> dict:
    return next(
        observation
        for observation in observations
        if observation["locator"]["table"] == table
        and observation["values"]["sample_id_as_printed"] == sample
        and observation["values"]["quantity"] == quantity
    )


def test_render_pins_cover_all_three_table_row_sets() -> None:
    observations = _observations()
    assert len(observations) == 128
    by_quantity = Counter(observation["values"]["quantity"] for observation in observations)
    assert by_quantity == {"activity": 64, "activity_coefficient": 64}

    row_sets = Counter(
        (observation["locator"]["page"], observation["locator"]["table"])
        for observation in observations
        if observation["values"]["quantity"] == "activity"
    )
    assert row_sets == {(1, "1"): 20, (1, "2"): 22, (2, "3"): 22}

    pins = (
        ("1", "26", "activity", "activity", "0.36", 1, "1147"),
        ("2", "56", "activity_coefficient", "gamma", "0.80", 1, "1147"),
        ("3", "89", "activity_coefficient", "gamma", "0.67", 2, "1148"),
    )
    for table, sample, quantity, value_field, expected, page, published_page in pins:
        observation = _sample_observation(observations, table, sample, quantity)
        locator = observation["locator"]
        assert observation["values"][value_field] == expected
        assert locator["page"] == page
        assert locator["published_page"] == int(published_page)
        assert locator["row_label"] == sample

    activities = {
        (observation["locator"]["table"], observation["values"]["sample_id_as_printed"]): observation
        for observation in observations
        if observation["values"]["quantity"] == "activity"
    }
    gammas = [
        observation
        for observation in observations
        if observation["values"]["quantity"] == "activity_coefficient"
    ]
    for gamma in gammas:
        key = (gamma["locator"]["table"], gamma["values"]["sample_id_as_printed"])
        assert gamma["derived_from"] == [activities[key]["observation_id"]]


def test_isolated_migration_binds_printed_conditions_and_keeps_pressure_unknown() -> None:
    observations = _observations()
    for observation in observations:
        conditions = observation["point_conditions"]
        assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert conditions["gas_mix"]["state"]["tag"] == "unknown"

    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [
        observation
        for observation in result.observations.values()
        if observation.source_id == SOURCE_ID
    ]
    assert len(migrated) == 128
    assert result.validation.hard_issues == ()
    assert sum(item.value.kind.value == "unavailable" for item in migrated) == 0

    composition_states = Counter()
    for observation in migrated:
        identity = observation.identity
        assert identity.temperature_K.is_value
        assert identity.temperature_K.value == 1673
        assert identity.fO2_Pa.is_value
        assert identity.total_pressure_Pa.is_unknown
        assert "temperature_K" in observation.point_conditions
        assert "fO2_log" in observation.point_conditions
        assert "composition" in observation.point_conditions
        composition_states["value" if identity.composition.is_value else "unknown"] += 1

    assert composition_states == {"value": 84, "unknown": 44}
    sample_26 = next(
        row for row in migrated if row.observation_id.endswith("_p001_t01_r03_c10")
    )
    assert sample_26.point_conditions["fO2_log"].state.value == Decimal("-10.71")
    sample_89 = next(
        row for row in migrated if row.observation_id.endswith("_p002_t03_r24_c11")
    )
    assert sample_89.identity.composition.is_unknown
    assert "AlO1.5" in sample_89.identity.composition.reason
