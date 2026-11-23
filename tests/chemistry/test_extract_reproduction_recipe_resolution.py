"""Regression checks for extract recipe precedence and figure admission."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

import pytest

from simulator.battery import migrate as battery_migrate
import simulator.diagnostic_helpers.extract_reproduction as extract_reproduction
from simulator.diagnostic_helpers.extract_reproduction import (
    _literature_pressure_points,
    _melt_recipe_mol,
    evaluate_observation,
    load_adopted_observations,
    observation_admission_reason,
    _pressure_standard_state_kind,
)


def _adopted_observation(source_id: str, species: str, observation_id: str):
    return next(
        observation
        for observation in load_adopted_observations()
        if observation.source_id == source_id
        and observation.species_id == species
        and observation.observation_id == observation_id
    )


def test_bischof_unknown_mixed_basis_point_keeps_parent_recipe() -> None:
    obs = _adopted_observation(
        "kems-137-bischof-2023",
        "Ga",
        "bischof_2023_ga_psat_s1_1low_polytherm",
    )
    parent, parent_source, parent_error = _melt_recipe_mol(obs)
    assert parent is not None and parent_error is None
    point = obs.values["points"][0]
    assert point["point_conditions"]["composition"]["state"]["tag"] == "unknown"

    recipe, source, error = _melt_recipe_mol(obs, point=point)

    assert recipe == pytest.approx(parent)
    assert error is None
    assert "initial_charge_only" in source
    assert "initial_charge_only" in parent_source


def test_bischof_value_point_assay_overrides_parent_and_partial_assay_refuses() -> None:
    obs = _adopted_observation(
        "kems-137-bischof-2023",
        "In",
        "bischof_2023_in_psat_s1_1low_polytherm",
    )
    point = deepcopy(obs.values["points"][0])
    wrapper = point["point_conditions"]["composition"]
    payload = {
        "basis": "oxide_weight_percent_as_published",
        "amount_basis": "mass_percent",
        "components": [["SiO2", "70.0"], ["Al2O3", "30.0"]],
    }
    wrapper["state"] = {"tag": "value", "value": payload}

    recipe, _source, error = _melt_recipe_mol(obs, point=point)
    expected = battery_migrate._composition_from_plain(payload).as_map()
    assert error is None
    assert recipe == pytest.approx(
        {name: float(amount) for name, amount in expected.items()}
    )

    point["point_conditions"]["composition"]["state"]["value"]["components"] = [
        ["Na2O", "0.23"]
    ]
    partial, _source, error = _melt_recipe_mol(obs, point=point)
    assert partial is None
    assert error is not None and "partial analysis" in error


def test_non_value_point_wrapper_recipe_census_has_no_parent_recipe_regressions() -> None:
    observations = load_adopted_observations()
    assert len(observations) == 2340
    regressions: list[str] = []
    bischof_unknown_points: dict[str, int] = {}
    point_recipe_keys = (
        "composition_mol",
        "melt_composition_mol",
        "composition_wt_pct",
        "melt_composition_wt_pct",
        "sample_oxide_composition_wt_pct",
    )

    for obs in observations:
        parent, _source, _error = _melt_recipe_mol(obs)
        if parent is None:
            continue
        for point in obs.values.get("points") or ():
            if not isinstance(point, Mapping):
                continue
            conditions = point.get("point_conditions")
            composition = (
                conditions.get("composition")
                if isinstance(conditions, Mapping)
                else None
            )
            state = (
                composition.get("state")
                if isinstance(composition, Mapping)
                else None
            )
            if not isinstance(state, Mapping) or state.get("tag") == "value":
                continue
            if point.get("composition_source_gap") or any(
                isinstance(point.get(key), dict) and point.get(key)
                for key in point_recipe_keys
            ) or any(
                point.get(key) is not None
                for key in ("sample_Na2O_wt_pct", "sample_K2O_wt_pct")
            ):
                continue

            recipe, _source, error = _melt_recipe_mol(obs, point=point)
            if error is not None or recipe != pytest.approx(parent):
                regressions.append(f"{obs.case_id}: {error or recipe}")
            if (
                obs.source_id == "kems-137-bischof-2023"
                and obs.observation_id
                in {
                    "bischof_2023_ga_psat_s1_1low_polytherm",
                    "bischof_2023_in_psat_s1_1low_polytherm",
                }
            ):
                bischof_unknown_points[obs.observation_id] = (
                    bischof_unknown_points.get(obs.observation_id, 0) + 1
                )

    assert regressions == []
    assert bischof_unknown_points == {
        "bischof_2023_ga_psat_s1_1low_polytherm": 11,
        "bischof_2023_in_psat_s1_1low_polytherm": 11,
    }


def test_figure_only_exception_requires_parsed_digitized_points() -> None:
    obs = _adopted_observation(
        "kems-022-demaria-1971",
        "Na",
        "demaria_1971_na_psat_figure1_sample_12022_digitized",
    )
    values = {
        **obs.values,
        "points": [],
        "runtime_form": "log10(P_Pa) = A_Pa - B/(T+C)",
        "runtime_coefficients": {"A_Pa": 10.4199, "B": 7696.0, "C": 0.0},
    }
    coefficient_only = replace(obs, values=values)
    parsed, _skip, _drops = _literature_pressure_points(coefficient_only)
    assert parsed
    assert observation_admission_reason(coefficient_only) == (
        "unsupported_observable:figure_only_not_digitized"
    )

    malformed = replace(
        coefficient_only,
        values={**values, "points": [{"T_K": "bad", "p_Pa": "bad"}]},
    )
    parsed, _skip, drops = _literature_pressure_points(malformed)
    assert parsed is None
    assert drops
    assert observation_admission_reason(malformed) == (
        "unsupported_observable:figure_only_not_digitized"
    )

    model_marked = replace(
        obs, values={**obs.values, "evidence_class": "model"}
    )
    assert observation_admission_reason(model_marked) == (
        "model_output_not_measurement"
    )
    unknown_marker = replace(
        obs, values={**obs.values, "evidence_class": "model_derived"}
    )
    assert observation_admission_reason(unknown_marker) == (
        "unknown_evidence_marker:evidence_class=model_derived"
    )


def test_demaria_k_reservoir_reason_selects_melt_standard_state() -> None:
    obs = _adopted_observation(
        "kems-022-demaria-1971",
        "K",
        "demaria_1971_k_psat_figure2_digitized",
    )

    assert obs.reservoir == {
        "tag": "not_applicable",
        "reason": "p_partial over multicomponent melt; no single pure-substance reservoir",
    }
    assert _pressure_standard_state_kind(obs) == "melt"


def test_demaria_k_composition_points_use_scorer_effusion_closure(
    monkeypatch,
) -> None:
    from simulator.battery.enums import Engine, ExecutionState, NoticeKind, Quantity
    from simulator.battery.records import Execution, Notice
    from simulator.battery.score import (
        ENGINE_CHANNELS,
        OXYGEN_BALANCE_NOTICE_PREFIX,
        EnginePrediction,
    )

    import simulator.battery.score as score

    obs = _adopted_observation(
        "kems-022-demaria-1971",
        "K",
        "demaria_1971_k_psat_figure2_digitized",
    )
    points, skip, drops = _literature_pressure_points(obs)
    assert skip is None
    assert not drops
    assert points is not None
    composition_points = [
        point for point in points if _melt_recipe_mol(obs, point=point)[0] is not None
    ]
    assert len(composition_points) == 2

    closure_calls: list[tuple[object, object]] = []

    def closure_stub(engine, observation, **kwargs):
        assert engine is Engine.INTERNAL_ANALYTICAL
        experiment = kwargs["experiment"]
        closure_calls.append((observation, experiment))
        return EnginePrediction(
            engine=engine,
            channel=ENGINE_CHANNELS[engine],
            execution=Execution(state=ExecutionState.PRODUCED),
            notices=(
                Notice(
                    kind=NoticeKind.SOURCE_DISAGREEMENT,
                    affected_quantities=(Quantity.P_PARTIAL,),
                    reason=OXYGEN_BALANCE_NOTICE_PREFIX
                    + '{"pO2_bar":5e-09}',
                    origin=f"engine:{engine.value}",
                ),
            ),
        )

    melt_calls: list[tuple[str, float, float, dict[str, float]]] = []

    def melt_stub(species, temperature_K, pO2_bar, _vp_data, *, account_mol):
        melt_calls.append(
            (str(species), float(temperature_K), float(pO2_bar), dict(account_mol))
        )
        return 1.0, None, {"provider_status": "ok"}

    runtimes: list[dict[str, object]] = []
    compare_point = extract_reproduction._compare_point

    def capture_runtime(**kwargs):
        runtimes.append(dict(kwargs["runtime"]))
        return compare_point(**kwargs)

    monkeypatch.setattr(score, "predict_with_engine", closure_stub)
    monkeypatch.setattr(extract_reproduction, "_engine_melt_psat_pa", melt_stub)
    monkeypatch.setattr(extract_reproduction, "_compare_point", capture_runtime)

    evaluation = evaluate_observation(obs, vapor_pressure_data={})

    assert len(evaluation.records) == 10
    assert len(closure_calls) == 2
    assert all(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
        and observation.identity.temperature_K.is_value
        and observation.identity.composition.is_value
        and len(observation.identity.composition.value.components) == 9
        and observation.identity.composition.value.proxy_flag == "initial_charge_only"
        and experiment.experiment_id.endswith("::experiment::demaria-1971-k-figure2")
        for observation, experiment in closure_calls
    )
    assert {
        float(observation.identity.temperature_K.value): float(observation.value.point)
        for observation, _experiment in closure_calls
    } == pytest.approx(
        {1366.12: 0.004526021577, 1314.06: 0.0025451684895}
    )
    assert len(melt_calls) == 2
    assert {call[1] for call in melt_calls} == {1366.12, 1314.06}
    assert {call[2] for call in melt_calls} == {5e-9}
    assert all(len(call[3]) == 9 for call in melt_calls)
    assert sum(record.status == "assumed-input" for record in evaluation.records) == 2
    assert sum(record.actual_value is None for record in evaluation.records) == 8
    assert all(
        runtime.get("oxygen_closure", {}).get("owner")
        == "simulator/battery/score.py::_borrow_internal_analytical_effusion"
        for runtime in runtimes
        if runtime.get("temperature_K") in {1366.12, 1314.06}
    )
    assert all(
        runtime["oxygen_closure"]["route"] == "effusion_oxygen_balance"
        for runtime in runtimes
        if runtime.get("temperature_K") in {1366.12, 1314.06}
    )
