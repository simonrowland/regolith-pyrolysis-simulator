from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.oxygen_identity import (
    SOSSI_GAS_EQUILIBRIUM_BAND_DEX,
    check_sossi_table2_gas_equilibrium,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"


def _source_parts():
    source = yaml.safe_load(SOURCE.read_text())
    experiments = source["experiments"]
    table2 = next(
        observation
        for observation in source["species"]["Mn"]["observations"]
        if observation.get("observation_id")
        == "sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906"
    )
    series = table2["values"]["series"]
    return experiments, series


def _checks_by_run(experiments, series):
    checks = check_sossi_table2_gas_equilibrium(experiments, series)
    return {check.run_id: check for check in checks.values()}


def test_all_43_sossi_table2_gas_equilibria_match_the_printed_logs() -> None:
    experiments, series = _source_parts()
    checks = _checks_by_run(experiments, series)

    assert len(checks) == 43
    assert Counter(check.method for check in checks.values()) == {
        "imposed_fo2": 24,
        "co2_dissociation_partials_bar": 10,
        "printed_air": 9,
    }
    assert all(check.refusal_reason is None for check in checks.values())
    assert all(
        check.checked_log10_fO2_bar == check.printed_log10_fO2_bar
        for check in checks.values()
    )
    assert all(
        check.delta_log10_fO2_dex is None
        or abs(check.delta_log10_fO2_dex) <= SOSSI_GAS_EQUILIBRIUM_BAND_DEX
        for check in checks.values()
    )

    for run_id in ("P09/06/18a", "P09/06/18b"):
        check = checks[run_id]
        assert check.printed_log10_fO2_bar == Decimal("-9.23")
        assert float(check.computed_log10_fO2_bar) == pytest.approx(
            -9.2055, abs=0.00005
        )
        assert float(check.delta_log10_fO2_dex) == pytest.approx(
            0.0245, abs=0.00005
        )

    assert float(checks["2M-15-07-16d"].computed_log10_fO2_bar) == pytest.approx(
        -3.4351, abs=0.00005
    )
    assert float(checks["C18/12/15"].computed_log10_fO2_bar) == pytest.approx(
        -3.0652, abs=0.00005
    )
    assert all(
        checks[run_id].printed_log10_fO2_bar == Decimal("-0.68")
        and checks[run_id].checked_log10_fO2_bar == Decimal("-0.68")
        for run_id, check in checks.items()
        if check.method == "printed_air"
    )


def test_breaking_the_printed_co_co2_ratio_refuses_the_run() -> None:
    experiments, series = _source_parts()
    mutated = deepcopy(series)
    row = next(
        item for item in mutated if item["run_id"].rstrip("*") == "P09/06/18a"
    )
    quote = row["locator"]["quote"]
    row["locator"]["quote"], count = re.subn(
        r"(P09/06/18a\*?\s+1400\s+15\s+)130(\s+65\s+-9\.23)",
        r"\g<1>100\g<2>",
        quote,
        count=1,
    )
    assert count == 1

    check = _checks_by_run(experiments, mutated)["P09/06/18a"]

    assert check.refusal_reason == "gas_equilibrium_fo2_disagrees_with_printed"
    assert check.checked_log10_fO2_bar is None
    assert abs(check.delta_log10_fO2_dex) > SOSSI_GAS_EQUILIBRIUM_BAND_DEX


@pytest.mark.parametrize("cover", ["Argon", "air", "CO + CO2 + Kr"])
def test_swapping_a_printed_co_co2_cover_refuses_the_run(cover: str) -> None:
    experiments, series = _source_parts()
    mutated = deepcopy(experiments)
    experiment = next(
        item
        for item in mutated
        if "P09/06/18a" in item["locator"]["note"]
    )
    experiment["pressure_environment"]["sweep_gas"]["state"] = {
        "tag": "value",
        "value": cover,
    }

    check = _checks_by_run(mutated, series)["P09/06/18a"]

    assert check.refusal_reason == "cover_not_printed_mixture"
    assert check.checked_log10_fO2_bar is None


def test_non_value_pressure_state_is_a_typed_gas_equilibrium_refusal() -> None:
    experiments, series = _source_parts()
    mutated = deepcopy(experiments)
    experiment = next(
        item
        for item in mutated
        if "P09/06/18a" in item["locator"]["note"]
    )
    experiment["pressure_environment"]["total_pressure_Pa"]["state"] = {
        "tag": "unknown",
        "reason": "mutated for refusal regression",
    }

    check = _checks_by_run(mutated, series)["P09/06/18a"]

    assert check.refusal_reason == "gas_equilibrium_fo2_unavailable"
    assert check.checked_log10_fO2_bar is None


def test_empty_table2_quote_is_a_typed_parser_refusal() -> None:
    experiments, series = _source_parts()
    mutated = deepcopy(series)
    row = next(
        item for item in mutated if item["run_id"].rstrip("*") == "P09/06/18a"
    )
    row["locator"]["quote"] = ""

    with pytest.raises(
        ValueError, match="sossi_table2_gas_row_quote_invalid:P09/06/18a"
    ):
        check_sossi_table2_gas_equilibrium(experiments, mutated)


def test_real_sossi_extract_flows_reach_the_lab_schedule_boundary() -> None:
    from simulator.battery.consumer_inputs import (
        _sossi_table2_flow_observation,
        collect_consumer_inputs,
    )
    from simulator.battery.generators.bench import (
        _document,
        vacuum_pyrolysis_preset,
    )
    from simulator.battery.migrate import load_migrated_benches, load_migrated_store
    from simulator.lab_schedule import _normalize_gas_boundary

    source_id = "kems-012-sossi-2019"
    _works, experiments, observations = load_migrated_store(sources=(source_id,))
    benches = load_migrated_benches()

    def real_boundary(run_slug: str):
        experiment = next(
            item
            for item in experiments.values()
            if item.experiment_id.endswith(
                f"::experiment::sossi-2019-run-{run_slug}"
            )
        )
        observation = _sossi_table2_flow_observation(
            experiment, observations.values()
        )
        assert observation is not None
        bench = benches[experiment.bench_id]
        inputs = collect_consumer_inputs(experiment, bench, observation)
        pressure = inputs.waypoints["run_pressure_boundary"].selected.value.point
        gas_boundary = inputs.waypoints["gas_boundary"].selected
        assert gas_boundary is not None
        assert gas_boundary.route == (
            f"observation[{observation.observation_id}]"
            ".point_conditions.temperature_K"
        )
        generated = vacuum_pyrolysis_preset(inputs)
        assert not any(
            gap.waypoint == "gas_boundary" for gap in generated.readiness.gaps
        )
        boundary = _normalize_gas_boundary(_document(inputs, "gas_boundary"))
        return pressure, boundary, experiment, observation, bench

    pressure, mixed, experiment, observation, bench = real_boundary("p09-06-18a")
    assert pressure == Decimal("101325")
    assert mixed["component_flows"]["CO"]["value"] == 130.0
    assert mixed["component_flows"]["CO2"]["value"] == 65.0
    assert mixed["reported_total_flow"]["value"] == 200.0
    assert mixed["reported_total_flow"]["qualifier"] == "approximate"
    assert mixed["flow_reference_state"]["reason"] == (
        "unresolved_sccm_standard_state"
    )
    assert mixed["flow_reference_state"]["conversion_role"] == "diagnostic_only"
    assert mixed["pressure_control"]["pressure_driver"] == "imposed"
    assert "not load-bearing" in mixed["flow_reference_state"]["conversion_note"]
    assert "knob_defaulted" not in mixed["flow_reference_state"]

    temperature_condition = observation.point_conditions["temperature_K"]
    mutated_note, count = re.subn(
        r"(P09/06/18a\*\s+1400\s+15\s+)130(\s+65\s+-9\.23)",
        r"\g<1>100\g<2>",
        temperature_condition.locator.note,
        count=1,
    )
    assert count == 1
    mutated_temperature_condition = replace(
        temperature_condition,
        locator=replace(temperature_condition.locator, note=mutated_note),
    )
    mutated_observation = replace(
        observation,
        point_conditions={
            **observation.point_conditions,
            "temperature_K": mutated_temperature_condition,
        },
    )
    mutated_inputs = collect_consumer_inputs(
        experiment, bench, mutated_observation
    )
    mutated_boundary = _normalize_gas_boundary(
        _document(mutated_inputs, "gas_boundary")
    )
    assert mutated_boundary["component_flows"]["CO"]["value"] == 100.0

    pressure, pure_co2, *_ = real_boundary("c18-12-15")
    assert pressure == Decimal("101325")
    assert pure_co2["component_flows"]["CO"]["value"] == 0.0
    assert pure_co2["component_flows"]["CO2"]["value"] == 27.0
    assert "reported_total_flow" not in pure_co2
    assert pure_co2["pressure_control"]["pressure_driver"] == "imposed"


def test_rps_cli_passes_the_real_sossi_run_record(tmp_path, monkeypatch) -> None:
    import json

    import scripts.bench_generate as cli
    from simulator.battery.consumer_inputs import _sossi_table2_flow_observation
    from simulator.battery.migrate import load_migrated_benches, load_migrated_store

    source_id = "kems-012-sossi-2019"
    works, experiments, observations = load_migrated_store(sources=(source_id,))
    experiment = next(
        item
        for item in experiments.values()
        if item.experiment_id.endswith(
            "::experiment::sossi-2019-run-p09-06-18a"
        )
    )
    observation = _sossi_table2_flow_observation(experiment, observations.values())
    assert observation is not None
    bench = load_migrated_benches()[experiment.bench_id]
    source_observations = {
        item.observation_id: item
        for item in observations.values()
        if item.experiment_id == experiment.experiment_id
    }
    monkeypatch.setattr(
        cli, "load_migrated_benches", lambda _root: {bench.id: bench}
    )
    monkeypatch.setattr(cli, "_apparatus_references", lambda _root, _works: {})

    def generate(output, run_observations):
        monkeypatch.setattr(
            cli,
            "load_migrated_store",
            lambda _root: (
                works,
                {experiment.experiment_id: experiment},
                run_observations,
            ),
        )
        assert cli.main(
            [
                "--consumer",
                "rps",
                "--source",
                source_id,
                "--output",
                str(output),
            ]
        ) == 0
        return json.loads((output / "summary.json").read_text())["records"][0]

    record = generate(tmp_path / "with-flow-record", source_observations)
    assert record["observation_id"] == observation.observation_id
    assert "gas_boundary" not in {
        gap["waypoint"] for gap in record["readiness"]["gaps"]
    }

    without_flow_record = {
        key: item
        for key, item in source_observations.items()
        if item.observation_id != observation.observation_id
    }
    assert _sossi_table2_flow_observation(
        experiment, without_flow_record.values()
    ) is None
    missing_flow_record = generate(
        tmp_path / "without-flow-record", without_flow_record
    )
    assert missing_flow_record["observation_id"] is None
    assert "gas_boundary" in {
        gap["waypoint"] for gap in missing_flow_record["readiness"]["gaps"]
    }
