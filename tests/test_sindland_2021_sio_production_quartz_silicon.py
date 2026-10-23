from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AmountBasis, Quantity, ValueKind
from simulator.battery.waypoints import charge_moles_by_species
from simulator.state import MOLAR_MASS
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
    sample_composition = document["experiments"][0]["sample"]["initial_composition"]["state"]["value"]
    assert sample_composition["amount_basis"] == AmountBasis.MOLE_FRACTION.value
    assert sample_composition["components"] == [["Si", "0.5"], ["SiO2", "0.5"]]
    by_number = {
        int(row["observation_id"].rsplit("_", 1)[1]): row
        for row in table_v
    }
    assert by_number[5]["values"]["points"][0]["crucible_as_printed"] == "h-BN crucible"
    induction_experiments = {3, 4, 9, 10, 12, 15}
    assert {
        number for number, row in by_number.items()
        if row["values"].get("furnace") == "induction heat furnace"
    } == induction_experiments

    migrator = migrate_one_extract(root=ROOT, path=EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert sum(
        obs.value.kind is ValueKind.UNAVAILABLE
        for obs in migrator.result.observations.values()
    ) == 0
    experiment = next(iter(migrator.result.experiments.values()))
    initial_composition = experiment.sample.initial_composition
    assert initial_composition is not None and initial_composition.state.is_value
    assert initial_composition.state.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert initial_composition.state.value.components == (
        ("Si", Decimal("0.5")),
        ("SiO2", Decimal("0.5")),
    )
    assert experiment.sample.mass_kg is not None
    assert experiment.sample.mass_kg.state.value.point == Decimal("0.0200")
    charge = charge_moles_by_species(experiment, None)
    si = charge["Si"].selected
    sio2 = charge["SiO2"].selected
    assert si is not None and sio2 is not None
    assert si.route == sio2.route == "mass_times_mole_fraction"
    assert si.value.point == sio2.value.point
    charged_mass_g = sum(
        waypoint.value.point * Decimal(str(MOLAR_MASS[species]))
        for species, waypoint in (("Si", si), ("SiO2", sio2))
    )
    assert abs(charged_mass_g - Decimal("20.0")) <= Decimal("0.001")
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
