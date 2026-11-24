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


def test_checked_logs_preserve_the_prior_sossi_engine_fO2_map() -> None:
    from simulator.battery.enums import Quantity
    from simulator.battery.identity import quantity_token
    from simulator.battery.migrate import load_migrated_store

    experiments, series = _source_parts()
    checks = check_sossi_table2_gas_equilibrium(experiments, series)
    _works, _experiments, observations = load_migrated_store(
        sources=("kems-012-sossi-2019",)
    )
    prior_engine_inputs = {}
    for row in observations.values():
        if (
            row.source_id != "kems-012-sossi-2019"
            or row.admission.status.value != "admitted"
            or quantity_token(row.identity)
            is not Quantity.RESIDUE_COMPONENT_COMPOSITION
        ):
            continue
        fO2 = (row.point_conditions or {}).get("fO2_log")
        if fO2 is not None and fO2.state.is_value:
            run_id = row.experiment_id.rsplit("::", 1)[-1]
            prior_engine_inputs[run_id] = fO2.state.value

    assert len(prior_engine_inputs) == 43
    assert prior_engine_inputs == {
        check.experiment_id: check.checked_log10_fO2_bar
        for check in checks.values()
    }


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


def test_ascii_hyphenated_co_co2_cover_matches_the_printed_mixture() -> None:
    experiments, series = _source_parts()
    mutated = deepcopy(experiments)
    experiment = next(
        item
        for item in mutated
        if "P09/06/18a" in item["locator"]["note"]
    )
    experiment["pressure_environment"]["sweep_gas"]["state"] = {
        "tag": "value",
        "value": "CO-CO2",
    }

    check = _checks_by_run(mutated, series)["P09/06/18a"]

    assert check.refusal_reason is None
    assert check.checked_log10_fO2_bar is not None


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

    checks = _checks_by_run(experiments, mutated)

    assert checks["P09/06/18a"].refusal_reason == (
        "sossi_table2_gas_row_quote_invalid"
    )
    assert checks["P09/06/18a"].checked_log10_fO2_bar is None
    assert checks["P09/06/18b"].checked_log10_fO2_bar == Decimal("-9.23")


def test_only_a_vetted_gas_result_can_be_the_fallback() -> None:
    from simulator.battery.oxygen_identity import (
        SossiGasEquilibriumCheck,
        resolve_sossi_run_oxygen,
    )

    disputed = SossiGasEquilibriumCheck(
        "run-a",
        "run-a",
        "co2_dissociation_partials_bar",
        None,
        Decimal("-8.0"),
        None,
        None,
        "gas_equilibrium_fo2_disagrees_with_printed",
        "computed value is outside the printed band",
        True,
    )
    decision = resolve_sossi_run_oxygen(
        disputed,
        recorded_log=None,
        ladder_log=Decimal("-7.5"),
        ladder_route="buffer_relation",
    )
    assert decision.log10_fO2_bar == Decimal("-7.5")
    assert decision.source == "oxygen_ladder_premise"

    unprinted = SossiGasEquilibriumCheck(
        "run-b",
        "run-b",
        "co2_dissociation_partials_bar",
        None,
        Decimal("-8.0"),
        None,
        None,
        "gas_equilibrium_fo2_unavailable",
        "The Table 2 run-log fO2 is not printed.",
        True,
    )
    decision = resolve_sossi_run_oxygen(
        unprinted,
        recorded_log=None,
        ladder_log=Decimal("-7.5"),
        ladder_route="buffer_relation",
    )
    assert decision.log10_fO2_bar == Decimal("-8.0")
    assert decision.source == "vetted_gas_derived_fO2"


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
    from simulator.battery.waypoints import WaypointAuthority
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
        expected_authority = (
            WaypointAuthority.EXTRAPOLATED
            if "reported_total_flow" in boundary
            else WaypointAuthority.PRINTED
        )
        assert gas_boundary.authority is expected_authority
        return pressure, boundary, experiment, observation, bench

    pressure, mixed, experiment, observation, bench = real_boundary("p09-06-18a")
    assert pressure == Decimal("101325")
    assert mixed["component_flows"]["CO"]["value"] == 130.0
    assert mixed["component_flows"]["CO2"]["value"] == 65.0
    assert mixed["component_flows"]["CO"]["source_class"] == "literature_primary"
    assert mixed["component_flows"]["CO2"]["source_class"] == "literature_primary"
    assert mixed["reported_total_flow"]["value"] == 200.0
    assert mixed["reported_total_flow"]["qualifier"] == "approximate"
    assert mixed["flow_reference_state"]["reason"] == (
        "sccm_reference_state_unreported"
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

    _, air, *_ = real_boundary("2m-15-07-16c")
    assert air["background_gas"]["species"] == "air"
    assert air["imposed_flow"]["reason"] == (
        "The source leaves both CO and CO2 flow columns blank for this run."
    )


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
    fallback_observation = _sossi_table2_flow_observation(
        experiment, without_flow_record.values()
    )
    assert fallback_observation is not None
    assert fallback_observation.observation_id != observation.observation_id
    missing_flow_record = generate(
        tmp_path / "without-flow-record", without_flow_record
    )
    assert missing_flow_record["observation_id"] == fallback_observation.observation_id
    gaps = {gap["waypoint"] for gap in missing_flow_record["readiness"]["gaps"]}
    assert "gas_boundary" not in gaps
    assert "thermal_path" not in gaps
    assert "run_pressure_boundary" not in gaps
    no_rows = generate(tmp_path / "without-any-observation", {})
    assert no_rows["observation_id"] is None
    no_row_gaps = {gap["waypoint"] for gap in no_rows["readiness"]["gaps"]}
    assert "gas_boundary" not in no_row_gaps
    assert "thermal_path" not in no_row_gaps
    assert "run_pressure_boundary" not in no_row_gaps


def test_rps_keeps_the_located_15_minute_sossi_thermal_series() -> None:
    from simulator.battery.consumer_inputs import (
        _sossi_table2_flow_observation,
        collect_consumer_inputs,
    )
    from simulator.battery.enums import ValueKind
    from simulator.battery.migrate import load_migrated_benches, load_migrated_store

    source_id = "kems-012-sossi-2019"
    _works, experiments, observations = load_migrated_store(sources=(source_id,))
    experiment = next(
        item
        for item in experiments.values()
        if item.experiment_id.endswith(
            "::experiment::sossi-2019-run-p09-06-18a"
        )
    )
    bench = load_migrated_benches()[experiment.bench_id]
    observation = _sossi_table2_flow_observation(experiment, observations.values())
    inputs = collect_consumer_inputs(experiment, bench, observation)

    thermal = inputs.waypoints["thermal_path"].selected.value
    assert thermal.kind is ValueKind.SERIES
    assert thermal.series == (
        (Decimal(0), Decimal("1673.15")),
        (Decimal(900), Decimal("1673.15")),
    )


def test_rps_keeps_a_mismatched_table2_gas_quote_out_of_control_inputs() -> None:
    from dataclasses import replace

    from simulator.battery.consumer_inputs import (
        _sossi_table2_gas_boundary,
        collect_consumer_inputs,
    )
    from simulator.battery.migrate import load_migrated_benches, load_migrated_store
    from simulator.battery.records import State, SweepGas, Value

    source_id = "kems-012-sossi-2019"
    _works, experiments, observations = load_migrated_store(sources=(source_id,))
    experiment = next(
        item
        for item in experiments.values()
        if item.experiment_id.endswith("::experiment::sossi-2019-run-p09-06-18a")
    )
    observation = next(
        item
        for item in observations.values()
        if item.source_id == source_id
        and item.experiment_id == experiment.experiment_id
        and item.identity.species.formula == "Mn"
        and item.point_conditions is not None
        and item.point_conditions.get("temperature_K") is not None
        and item.point_conditions["temperature_K"].locator is not None
        and item.point_conditions["temperature_K"].locator.table == "2"
        and "quote=" in (item.point_conditions["temperature_K"].locator.note or "")
    )
    row_locator = observation.point_conditions["temperature_K"].locator
    note = row_locator.note or ""
    prefix, quote = note.split("quote=", 1)
    mutated_quote, count = re.subn(
        r"P09/06/18a\*?", "P09/06/18b", quote, count=1
    )
    assert count == 1
    locator = replace(row_locator, note=prefix + "quote=" + mutated_quote)
    point_conditions = dict(observation.point_conditions or {})
    point_conditions["temperature_K"] = replace(
        point_conditions["temperature_K"], locator=locator
    )
    mutated = replace(
        observation, point_conditions=point_conditions
    )

    boundary = _sossi_table2_gas_boundary(
        experiment, mutated, expected_run_id="P09/06/18a"
    )
    assert boundary is not None
    assert boundary["background_gas"]["reason"].startswith(
        "sossi_table2_gas_row_quote_invalid"
    )
    assert "component_flows" not in boundary

    inputs = collect_consumer_inputs(
        experiment, load_migrated_benches()[experiment.bench_id], mutated
    )
    assert inputs.waypoints["gas_boundary"].selected.value == boundary

    other_run_observation = next(
        item
        for item in observations.values()
        if item.experiment_id.endswith(
            "::experiment::sossi-2019-run-p09-06-18b"
        )
        and item.identity.species.formula == "Mn"
    )
    mismatched_run_boundary = _sossi_table2_gas_boundary(
        experiment, other_run_observation
    )
    assert mismatched_run_boundary is not None
    assert mismatched_run_boundary["imposed_flow"]["reason"] == (
        "sossi_table2_gas_row_experiment_mismatch"
    )
    assert "component_flows" not in mismatched_run_boundary

    pressure = experiment.pressure_environment
    declared_cover = SweepGas(
        "CO + CO2 + Kr",
        State.unknown("unprinted"),
        State.unknown("unprinted"),
    )
    contaminated = replace(
        experiment,
        pressure_environment=replace(
            pressure,
            sweep_gas=replace(pressure.sweep_gas, state=State.of(declared_cover)),
        ),
    )
    contaminated_boundary = _sossi_table2_gas_boundary(contaminated, observation)
    assert contaminated_boundary is not None
    assert "component_flows" not in contaminated_boundary
    assert contaminated_boundary["imposed_flow"]["reason"] == (
        "cover_not_printed_mixture"
    )

    contradictory_pressure = replace(
        pressure.total_pressure_Pa,
        state=State.of(Value.point_of(50000)),
    )
    pressure_conflict = replace(
        experiment,
        pressure_environment=replace(
            pressure,
            total_pressure_Pa=contradictory_pressure,
        ),
    )
    pressure_boundary = _sossi_table2_gas_boundary(
        pressure_conflict, observation
    )
    assert pressure_boundary is not None
    assert "component_flows" not in pressure_boundary
    assert pressure_boundary["imposed_flow"]["reason"] == (
        "sossi_table2_pressure_conflicts_with_one_atmosphere_citation"
    )


def test_rps_cover_guard_fails_if_consumer_reverts_to_quote_only_rule(monkeypatch) -> None:
    from simulator.battery import consumer_inputs
    from simulator.battery.consumer_inputs import collect_consumer_inputs
    from simulator.battery.migrate import load_migrated_benches, load_migrated_store
    from simulator.battery.records import State, SweepGas

    source_id = "kems-012-sossi-2019"
    _works, experiments, observations = load_migrated_store(sources=(source_id,))
    experiment = next(
        item
        for item in experiments.values()
        if item.experiment_id.endswith(
            "::experiment::sossi-2019-run-p09-06-18a"
        )
    )
    observation = next(
        item
        for item in observations.values()
        if item.experiment_id == experiment.experiment_id
        and item.identity.species.formula == "Mn"
    )
    bench = load_migrated_benches()[experiment.bench_id]
    pressure = experiment.pressure_environment
    foreign_cover = replace(
        experiment,
        pressure_environment=replace(
            pressure,
            sweep_gas=replace(
                pressure.sweep_gas,
                state=State.of(
                    SweepGas(
                        "CO + CO2 + Kr",
                        State.unknown("unprinted"),
                        State.unknown("unprinted"),
                    )
                ),
            ),
        ),
    )
    safe_inputs = collect_consumer_inputs(foreign_cover, bench, observation)
    assert "component_flows" not in safe_inputs.waypoints["gas_boundary"].selected.value

    def quote_only_boundary(*_args, **_kwargs):
        return {"component_flows": {"CO": {"value": 130}, "CO2": {"value": 65}}}

    with monkeypatch.context() as mutation:
        mutation.setattr(
            consumer_inputs, "_sossi_table2_gas_boundary", quote_only_boundary
        )
        unsafe_inputs = collect_consumer_inputs(foreign_cover, bench, observation)
        with pytest.raises(AssertionError):
            assert "component_flows" not in unsafe_inputs.waypoints[
                "gas_boundary"
            ].selected.value


def test_sossi_unprinted_pressure_uses_flagged_dp002_method_premise() -> None:
    from dataclasses import replace

    from simulator.battery.consumer_inputs import collect_consumer_inputs
    from simulator.battery.migrate import load_migrated_benches, load_migrated_store
    from simulator.battery.records import State
    from simulator.battery.waypoints import WaypointAuthority, WaypointFlag

    source_id = "kems-012-sossi-2019"
    _works, experiments, _observations = load_migrated_store(sources=(source_id,))
    experiment = next(
        item
        for item in experiments.values()
        if item.experiment_id.endswith("::experiment::sossi-2019-run-p09-06-18a")
    )
    pressure = replace(
        experiment.pressure_environment.total_pressure_Pa,
        state=State.unknown("mutated unprinted pressure"),
    )
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment, total_pressure_Pa=pressure
        ),
    )
    bench = load_migrated_benches()[experiment.bench_id]
    inputs = collect_consumer_inputs(experiment, bench, None)

    waypoint = inputs.waypoints["run_pressure_boundary"].selected
    assert waypoint is not None
    assert waypoint.route == "DP-002_method_implied_Sossi_gas_mixing_furnace"
    assert waypoint.authority is WaypointAuthority.ASSUMED
    assert waypoint.flags == (WaypointFlag.ASSUMPTION,)
    assert waypoint.value.point == Decimal("101325")
    assert "Total pressure is unprinted" in waypoint.notice
