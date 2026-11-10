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
        "transport_assumption_flags": (),
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


def test_sossi_single_dash_keeps_component_unknown_and_flags_carrier_approximation():
    experiment = _source_experiment("2m-15-07-16d")

    conditions = _sossi_transport_conditions(experiment, 101325.0)

    assert experiment["pressure_environment"]["sweep_gas_mixture"]["state"]["value"] == {
        "CO": "-",
        "CO2": "200",
    }
    assert conditions["carrier_gas_mixture"] == {"CO2": 200.0}
    assert conditions["flow_rate_sccm"] == 200.0
    assert conditions["transport_assumption_flags"] == (
        "sweep_gas_CO_unprinted_CO2_carrier_approximation",
    )

    # A literal printed zero is different from an unprinted dash.
    printed_zero = _sossi_transport_conditions(
        _source_experiment("c18-12-15"), 101325.0
    )
    assert printed_zero["carrier_gas_mixture"] == {"CO": 0.0, "CO2": 27.0}
    assert printed_zero["transport_assumption_flags"] == ()

    mutated = {
        **experiment,
        "pressure_environment": {
            **experiment["pressure_environment"],
            "sweep_gas_mixture": {
                "state": {
                    "tag": "value",
                    "value": {"CO": "0", "CO2": "200"},
                }
            },
        },
    }
    mutated_conditions = _sossi_transport_conditions(mutated, 101325.0)
    assert mutated_conditions["carrier_gas_mixture"] == {"CO": 0.0, "CO2": 200.0}
    assert mutated_conditions["transport_assumption_flags"] == ()
    assert mutated_conditions != conditions


def test_sossi_table_and_bead_area_locators_use_physical_pdf_pages():
    source = yaml.safe_load(SOURCE.read_text())
    experiments = [
        row
        for row in source["experiments"]
        if row["experiment_id"].startswith("sossi-2019-run-")
    ]
    pages = [row["locator"]["page"] for row in experiments]

    assert len(pages) == 43
    assert pages.count(54) == 26
    assert pages.count(55) == 17
    assert _source_experiment("c17-12-15b")["locator"]["page"] == 55

    area_record = next(
        observation["equipment"]["sample_surface_area"]
        for observation in source["species"]["K"]["observations"]
        if "sample_surface_area" in observation.get("equipment", {})
    )
    assert area_record["locator"]["page"] == 6
    assert area_record["locator"]["section"] == "2.1 Experimental Methods"
