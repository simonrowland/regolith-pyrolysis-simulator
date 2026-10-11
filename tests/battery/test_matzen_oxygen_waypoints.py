from pathlib import Path

from simulator.battery.migrate import Migrator
from simulator.battery.waypoints import WaypointAuthority, oxygen_condition


def test_matzen_2022_table_2_oxygen_values_migrate_to_all_observation_waypoints():
    extract = Path(__file__).resolve().parents[2] / "data/literature/extracts/matzen-2022-martian-basalt-redox.yaml"
    migrator = Migrator()
    migrator._migrate_extract(extract)
    migrator.finalize()
    result = migrator.result

    expected_by_observation = {
        "matzen_2022_run_7_method_a": "-0.68",
        "matzen_2022_run_7_method_b": "-0.68",
        "matzen_2022_run_11_method_a": "-5.88",
        "matzen_2022_run_11_method_b": "-5.88",
        "matzen_2022_run_13_method_a": "-5.85",
        "matzen_2022_run_13_method_b": "-5.85",
        "matzen_2022_run_15_method_a": "-5.87",
        "matzen_2022_run_15_method_b": "-5.87",
        "matzen_2022_run_20_method_a": "-4.76",
        "matzen_2022_run_20_method_b": "-4.76",
        "matzen_2022_run_21_method_a": "-2.48",
        "matzen_2022_run_21_method_b": "-2.48",
        "matzen_2022_run_27_method_a": "-8.0",
        "matzen_2022_run_27_method_b": "-8.0",
        "matzen_2022_run_30_method_a": "-10.0",
        "matzen_2022_run_30_method_b": "-10.0",
        "matzen_2022_run_31_method_a": "-4.9",
        "matzen_2022_run_31_method_b": "-4.9",
        "matzen_2022_run_57_method_a": "-10.03",
        "matzen_2022_run_57_method_b": "-10.03",
        "matzen_2022_run_60_method_a": "-8.20",
        "matzen_2022_run_60_method_b": "-8.20",
        "matzen_2022_run_61_method_a": "-7.20",
        "matzen_2022_run_61_method_b": "-7.20",
    }
    expected_by_observation = {
        f"matzen-2022-martian-basalt-redox::{observation_id}": value
        for observation_id, value in expected_by_observation.items()
    }
    assert len(result.observations) == 24
    selected_by_observation = {}
    for observation in result.observations.values():
        experiment = result.experiments[observation.experiment_id]
        bench = result.benches[experiment.bench_id]
        waypoint = oxygen_condition(experiment, bench, observation)
        assert waypoint.selected is not None, observation.observation_id
        assert waypoint.selected.authority is WaypointAuthority.PRINTED
        assert observation.point_conditions["fO2_log"].locator.table == 2
        selected_by_observation[observation.observation_id] = str(waypoint.selected.value.point)
    assert selected_by_observation == expected_by_observation
