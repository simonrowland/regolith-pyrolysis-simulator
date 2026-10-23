from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass
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
EXPECTED_LN_BETA_FITS = {
    "baud_2003_norton_al_beta_1500k": "0.989 − 8009/T",
    "baud_2003_carborundum_al_beta_1500k": "1.633 − 9341/T",
    "baud_2003_norton_carbon_al_beta_1500k": "1.915 − 9416/T",
    "baud_2003_norton_silicon_al_beta_1500k": "−7.601 + 6042/T",
    "baud_2003_norton_al2o_beta_1500k": "5.663 − 17 027/T",
    "baud_2003_carborundum_al2o_beta_1500k": "3.375 − 13656/T",
    "baud_2003_norton_carbon_al2o_beta_1500k": "7.412 − 19 403/T",
    "baud_2003_norton_silicon_al2o_beta_1500k": "−10.414 + 9888/T",
    "baud_2003_norton_sio_beta_1500k": "−4.733 + 274/T",
    "baud_2003_carborundum_sio_beta_1500k": "1.631 − 7095/T",
    "baud_2003_norton_carbon_sio_beta_1500k": "−13.122 + 13961/T",
    "baud_2003_norton_silicon_sio_beta_1500k": "−21.64 + 26 039/T",
    "baud_2003_norton_co_beta_1500k": "−6.032 + 3844/T",
    "baud_2003_carborundum_co_beta_1500k": "−16.118 + 18 635/T",
    "baud_2003_norton_carbon_co_beta_1500k": "−7.824 + 5949/T",
    "baud_2003_norton_silicon_co_beta_1500k": "−8.112 + 8900/T",
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


def test_all_beta_points_use_their_printed_fit_as_measured_reduced():
    doc = yaml.safe_load(EXTRACT.read_text())
    rows = {
        row["observation_id"]: row
        for block in doc["species"].values()
        for row in block["observations"]
    }

    assert set(rows) == set(EXPECTED_LN_BETA_FITS)
    for observation_id, fit in EXPECTED_LN_BETA_FITS.items():
        row = rows[observation_id]
        assert row["values"]["method_class"] == "measured_reduced"
        derivation = row["values"]["derivation"]
        assert fit in derivation["relation"]
        assert "T = 1500 K" in derivation["relation"]
        assert derivation["raw_inputs"]["state"] == {
            "tag": "value",
            "value": "not_printed",
        }
        assert derivation["raw_inputs"]["locator"]["table"] == "5"
        assert derivation["raw_inputs"]["locator"]["section"] == "3.3"


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
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.is_value
        assert float(observation.identity.temperature_K.value) == 1500.0
        pressure = observation.identity.total_pressure_Pa
        assert pressure is None or not pressure.is_value
