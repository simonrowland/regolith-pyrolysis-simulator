from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "shornikov-2019-perovskite-evaporation-thermodynamics"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
SOURCE_PATH = (
    "tables/shornikov-2019-perovskite-evaporation-thermodynamics/"
    "shornikov-2019-perovskite-evaporation-thermodynamics-audit.md"
)
EXPECTED = {
    "shornikov_2019_eg_fig3a_CaO_x34_21": ("CaO", "2250", "0.6155"),
    "shornikov_2019_eg_fig2a_TiO_1790_2": ("TiO", "1790.2", "-9.917"),
    "shornikov_2019_eg_fig2b_TiO_x100_00": ("TiO", "2300", "-5.483"),
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


def test_recovered_figure_rows_migrate_with_located_conditions():
    document = _document()
    source_rows = _source_observations(document)
    assert set(source_rows) == set(EXPECTED)
    for observation_id, (_formula, temperature, value) in EXPECTED.items():
        values = source_rows[observation_id]["values"]
        assert str(values.get("activity", values.get("log10_p"))) == value
        assert str(values["T_K"]) == temperature

    migrator = _migrate(document, EXTRACT)
    rows = {
        row.observation_id.split("::", 2)[1]: row
        for row in migrator.result.observations.values()
        if row.source_id == SOURCE_ID
        and row.observation_id.split("::", 2)[1] in EXPECTED
    }
    assert set(rows) == set(EXPECTED)
    for observation_id, (formula, temperature, _value) in EXPECTED.items():
        row = rows[observation_id]
        assert row.identity.species.formula == formula
        assert row.identity.temperature_K.value == Decimal(temperature)
        condition = row.point_conditions["temperature_K"]
        actual = getattr(condition.state.value, "point", condition.state.value)
        assert condition.state.is_value
        assert actual == Decimal(temperature)
        assert condition.locator is not None
        assert condition.locator.page == 2
        assert condition.locator.published_page == 39
        assert row.locator.source_path == SOURCE_PATH


def test_mutating_figure_temperature_changes_the_migrated_condition(tmp_path: Path):
    document = _document()
    source = _source_observations(document)["shornikov_2019_eg_fig3a_CaO_x34_21"]
    source["T_range_K"] = ["2251", "2251"]
    source["values"]["T_K"] = "2251"
    source["values"]["temperature_K"] = 2251
    source["point_conditions"]["temperature_K"]["state"]["value"]["point"] = "2251"
    path = tmp_path / EXTRACT.name
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    migrator = _migrate(document, path)
    row = next(
        item
        for item in migrator.result.observations.values()
        if item.source_id == SOURCE_ID
        and item.observation_id.endswith("::shornikov_2019_eg_fig3a_CaO_x34_21")
    )
    assert row.identity.temperature_K.value == Decimal("2251")
    condition = row.point_conditions["temperature_K"]
    actual = getattr(condition.state.value, "point", condition.state.value)
    assert condition.state.is_value
    assert actual == Decimal("2251")
