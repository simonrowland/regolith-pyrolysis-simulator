from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "shornikov-2003-aluminosilicate-solids"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
SOURCE_PATH = (
    "tables/shornikov-2003-aluminosilicate-solids/"
    "shornikov-2003-aluminosilicate-solids-audit.md"
)
EXPECTED = {
    "shornikov_2003_fig2a_sio_symbol4": ("SiO", ["2063.5"]),
    "shornikov_2003_fig2e_sio_symbol7": ("SiO", ["1725.4", "1779.2"]),
    "shornikov_2003_fig2e_sio_symbol10": ("SiO", ["1718.4"]),
    "shornikov_2003_fig2f_al_symbol10": ("Al", ["2540.0"]),
    "shornikov_2003_fig2c_alo_symbol6": ("AlO", ["1839.7"]),
    "shornikov_2003_fig2h_o_symbol10": ("O", ["2020.3", "2484.7"]),
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


def _local_id(observation_id: str) -> str:
    return observation_id.split("::", 2)[1]


def test_recovered_figure_points_migrate_with_temperature_and_composition():
    document = _document()
    migrator = _migrate(document, EXTRACT)
    grouped = defaultdict(list)
    for row in migrator.result.observations.values():
        local_id = _local_id(row.observation_id)
        if row.source_id == SOURCE_ID and local_id in EXPECTED:
            grouped[local_id].append(row)

    assert set(grouped) == set(EXPECTED)
    assert sum(map(len, grouped.values())) == 8
    for observation_id, (formula, temperatures) in EXPECTED.items():
        rows = grouped[observation_id]
        assert sorted(str(row.identity.temperature_K.value) for row in rows) == temperatures
        assert all(row.identity.species.formula == formula for row in rows)
        assert all(row.locator.source_path == SOURCE_PATH for row in rows)
        for row in rows:
            condition = row.point_conditions["temperature_K"]
            temperature = getattr(condition.state.value, "point", condition.state.value)
            assert condition.state.is_value
            assert temperature == row.identity.temperature_K.value
            assert condition.locator is not None
            assert condition.locator.published_page == 1040

            composition = row.point_conditions["composition"]
            assert composition.state.is_value
            assert composition.locator is not None
            assert composition.locator.published_page == 1040


def test_mutating_nested_temperature_changes_the_migrated_point(tmp_path: Path):
    document = _document()
    source = next(
        row
        for row in document["species"]["SiO"]["observations"]
        if row["observation_id"] == "shornikov_2003_fig2a_sio_symbol4"
    )
    source["T_range_K"] = ["2064.5", "2064.5"]
    source["values"]["points"][0]["temperature_K"] = "2064.5"
    path = tmp_path / EXTRACT.name
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    migrator = _migrate(document, path)
    row = next(
        item
        for item in migrator.result.observations.values()
        if item.source_id == SOURCE_ID
        and _local_id(item.observation_id) == source["observation_id"]
    )
    assert row.identity.temperature_K.value == Decimal("2064.5")
    condition = row.point_conditions["temperature_K"]
    temperature = getattr(condition.state.value, "point", condition.state.value)
    assert condition.state.is_value
    assert temperature == Decimal("2064.5")
