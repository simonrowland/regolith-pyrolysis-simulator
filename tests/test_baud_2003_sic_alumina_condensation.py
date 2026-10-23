from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/baud-2003-sic-alumina-vaporization-condensation.yaml"
)
EXPECTED = {
    "baud_2003_norton_al_beta_1500k": 0.0129,
    "baud_2003_carborundum_al2o_beta_1500k": 0.0033,
    "baud_2003_norton_carbon_sio_beta_1500k": 0.022,
    "baud_2003_norton_silicon_co_beta_1500k": 0.113,
}


def test_table_5_has_only_16_render_checked_1500_k_beta_points():
    doc = yaml.safe_load(EXTRACT.read_text())
    rows = [
        row
        for block in doc["species"].values()
        for row in block["observations"]
    ]

    assert len(rows) == 16
    assert all(row["locator"]["page"] == 6 for row in rows)
    assert all(row["T_K"] == 1500 for row in rows)
    assert all(row["values"]["quantity"] == "condensation_coefficient" for row in rows)
    assert all("pressure" not in row["values"] for row in rows)
    for observation_id, value in EXPECTED.items():
        row = next(row for row in rows if row["observation_id"] == observation_id)
        assert row["values"]["alpha"] == value


def test_real_migrator_keeps_temperature_and_unprinted_pressure_unknown():
    migrator = Migrator(ROOT)
    migrator._migrate_extract(EXTRACT)

    migrated = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "baud-2003-sic-alumina-vaporization-condensation"
    ]
    assert len(migrated) == 16
    for observation in migrated:
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.is_value
        assert float(observation.identity.temperature_K.value) == 1500.0
        pressure = observation.identity.total_pressure_Pa
        assert pressure is None or not pressure.is_value
