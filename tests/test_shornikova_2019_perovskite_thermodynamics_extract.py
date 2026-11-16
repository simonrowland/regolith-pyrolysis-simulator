from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "shornikova-2019-perovskite-thermodynamics"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
SOURCE_PATH = (
    "tables/shornikova-2019-perovskite-thermodynamics/"
    "shornikova-2019-perovskite-thermodynamics-audit.md"
)
EXPECTED = {
    "shornikova_2019_fig3b_TiO2_t1973_5": ("1973.5", "0.1556"),
    "shornikova_2019_fig3b_TiO2_t2035_8": ("2035.8", "0.1732"),
    "shornikova_2019_fig3b_TiO2_t2253_9": ("2253.9", "0.2430"),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _migrate(document: dict, path: Path):
    migrator = migrate_one_extract(
        ROOT,
        path,
        index={SOURCE_ID: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    return migrator


def _source_observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row.get("observation_id") in EXPECTED
    }


def test_recovered_activity_rows_migrate_with_located_conditions():
    document = _document()
    source_rows = _source_observations(document)
    assert set(source_rows) == set(EXPECTED)
    for observation_id, (temperature, activity) in EXPECTED.items():
        values = source_rows[observation_id]["values"]
        assert str(values["T_K"]) == temperature
        assert str(values["activity"]) == activity
        assert source_rows[observation_id]["uncertainty"]["confidence_level"] == "not_stated"

    migrator = _migrate(document, EXTRACT)
    rows = {
        row.observation_id.split("::", 2)[1]: row
        for row in migrator.result.observations.values()
        if row.source_id == SOURCE_ID
        and row.observation_id.split("::", 2)[1] in EXPECTED
    }
    assert set(rows) == set(EXPECTED)
    for observation_id, (temperature, activity) in EXPECTED.items():
        row = rows[observation_id]
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.value.point == Decimal(activity)
        condition = row.point_conditions["temperature_K"]
        actual = getattr(condition.state.value, "point", condition.state.value)
        assert condition.state.is_value
        assert actual == Decimal(temperature)
        assert condition.locator is not None
        assert condition.locator.page == 5
        assert condition.locator.published_page == 1432
        assert row.locator.source_path == SOURCE_PATH


def test_mutating_figure_temperature_changes_the_migrated_condition(tmp_path: Path):
    document = _document()
    source = _source_observations(document)["shornikova_2019_fig3b_TiO2_t1973_5"]
    source["T_range_K"] = ["1974.5", "1974.5"]
    source["values"]["T_K"] = "1974.5"
    source["values"]["temperature_K"] = 1974.5
    source["point_conditions"]["temperature_K"]["state"]["value"]["point"] = "1974.5"
    path = tmp_path / EXTRACT.name
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    migrator = _migrate(document, path)
    row = next(
        item
        for item in migrator.result.observations.values()
        if item.source_id == SOURCE_ID
        and item.observation_id.endswith("::shornikova_2019_fig3b_TiO2_t1973_5")
    )
    assert row.identity.temperature_K.value == Decimal("1974.5")
    condition = row.point_conditions["temperature_K"]
    actual = getattr(condition.state.value, "point", condition.state.value)
    assert condition.state.is_value
    assert actual == Decimal("1974.5")
