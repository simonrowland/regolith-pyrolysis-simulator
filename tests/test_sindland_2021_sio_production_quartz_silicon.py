from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.factories import migrate_one_extract

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sindland-2021-sio-production-quartz-silicon.yaml"


def test_sindland_table_v_rows_match_page_6_and_migrate_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = document["species"]["SiO2"]["observations"]
    table_v = [row for row in rows if row["type"] == "mass_loss"]
    assert len(table_v) == 30
    by_id = {row["observation_id"]: row for row in table_v}
    expected_values = {
        "sindland_2021_experiment_2": "3.25",
        "sindland_2021_experiment_16": "46.63",
        "sindland_2021_experiment_17": "71.80",
    }
    for observation_id, value in expected_values.items():
        assert by_id[observation_id]["values"]["points"][0]["mass_loss_wt_pct"] == value
        assert by_id[observation_id]["locator"]["page"] == 6
    assert all(row["values"]["quantity"] == "mass_loss_fraction" for row in table_v)
    assert all("SiO formation" in row["values"]["attribution_as_printed"] for row in table_v)
    assert all("SiO" not in row["values"].get("gas_species", "") for row in table_v)

    migrator = migrate_one_extract(root=ROOT, path=EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert sum(
        obs.value.kind is ValueKind.UNAVAILABLE
        for obs in migrator.result.observations.values()
    ) == 0
    observations = [
        obs for obs in migrator.result.observations.values()
        if "sindland_2021_experiment_" in obs.observation_id
    ]
    assert len(observations) == 30
    assert all(obs.identity.quantity.value is Quantity.MASS_LOSS_FRACTION for obs in observations)
    assert all(obs.identity.temperature_K.is_value for obs in observations)
    assert all(obs.point_conditions["temperature_K"].state.is_value for obs in observations)
    assert all(obs.point_conditions["temperature_K"].locator.table == "V" for obs in observations)
    assert all(obs.point_conditions["T_C"].state.is_value for obs in observations)
    assert all(obs.point_conditions["hold_time_min"].state.is_value for obs in observations)
    assert all(obs.point_conditions["total_pressure_Pa"].state.is_value for obs in observations)
    assert all(obs.point_conditions["composition"].state.is_value for obs in observations)
    assert all(obs.point_conditions["sweep_gas"].state.is_value for obs in observations)
    for obs in observations:
        for key in ("T_C", "hold_time_min", "total_pressure_Pa", "composition", "sweep_gas"):
            assert obs.point_conditions[key].locator is not None
    assert all(obs.identity.total_pressure_Pa.is_value for obs in observations)
    assert all(obs.identity.composition.is_value for obs in observations)
    assert all(
        obs.identity.composition.value.components == (
            ("Si", Decimal("0.5")),
            ("SiO2", Decimal("0.5")),
        )
        for obs in observations
    )
    assert all(obs.identity.reservoir.is_unknown for obs in observations)
    assert all(obs.identity.fO2_Pa.is_unknown for obs in observations)

    expected_migrated = {
        "sindland_2021_experiment_2": Decimal("0.0325"),
        "sindland_2021_experiment_16": Decimal("0.4663"),
        "sindland_2021_experiment_17": Decimal("0.7180"),
    }
    migrated = {
        observation_id: next(
            obs for obs in observations if f"::{observation_id}" in obs.observation_id
        )
        for observation_id in expected_migrated
    }
    assert {key: migrated[key].value.point for key in expected_migrated} == expected_migrated
