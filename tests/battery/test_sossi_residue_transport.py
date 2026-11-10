from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.residue import _sossi_transport_conditions


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"


def _source_experiment(run_id: str):
    source = yaml.safe_load(SOURCE.read_text())
    experiment = next(
        row
        for row in source["experiments"]
        if row["experiment_id"] == f"sossi-2019-run-{run_id}"
    )
    bench = next(row for row in source["benches"] if row["id"] == experiment["bench_id"])
    dimensions = next(
        fact["value"]["state"]["value"]["categorical"]
        for fact in bench["other_facts"]
        if fact["name"] == "furnace_tube_dimensions"
    )
    return {**experiment, "_bench_furnace_tube_dimensions": dimensions}


def test_sossi_series_conditions_read_primary_table_and_shared_geometry():
    experiment = _source_experiment("2m-15-07-16g")

    conditions = _sossi_transport_conditions(experiment, 101325.0)

    assert experiment["locator"]["page"] == 54
    assert experiment["locator"]["table"] == "2"
    assert conditions == {
        "total_pressure_pa": 101325.0,
        "carrier_gas_mixture": {"CO": 1.8, "CO2": 200.0},
        "flow_rate_sccm": 201.8,
        "pipe_diameter_m": 0.042,
        "transport_length_m": 0.03,
    }

    mutated = {
        **experiment,
        "pressure_environment": {
            **experiment["pressure_environment"],
            "sweep_gas_mixture": {
                "state": {
                    "tag": "value",
                    "value": {"CO": "1.8", "CO2": "400"},
                }
            },
            "flow_rate_sccm": {
                "state": {"tag": "value", "value": {"kind": "point", "point": "401.8"}}
            },
        },
    }
    mutated_conditions = _sossi_transport_conditions(mutated, 101325.0)
    assert mutated_conditions["carrier_gas_mixture"]["CO2"] == 400.0
    assert mutated_conditions["flow_rate_sccm"] == 401.8
    assert mutated_conditions != conditions


def test_sossi_unprinted_flow_remains_unknown_for_series_defaults():
    experiment = _source_experiment("2m-15-07-16c")

    conditions = _sossi_transport_conditions(experiment, 101325.0)

    assert experiment["pressure_environment"]["flow_rate_sccm"]["state"]["tag"] == "unknown"
    assert conditions["carrier_gas_mixture"] is None
    assert conditions["flow_rate_sccm"] is None
    assert conditions["pipe_diameter_m"] == pytest.approx(0.042)
    assert conditions["transport_length_m"] == pytest.approx(0.03)
