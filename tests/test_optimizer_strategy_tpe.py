from __future__ import annotations

import importlib
import math
import re
import subprocess
import sys
from dataclasses import replace
from numbers import Real
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.optimize import (
    Candidate,
    GateMargin,
    OptunaNSGA2Strategy,
    OptunaTPEStrategy,
    Strategy,
    ThresholdSpec,
)
from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.optimize.evaluate import (
    FailureCategory,
    RunReference,
    ScoredResult,
    evaluate,
)
from simulator.optimize.objective import (
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    LEGACY_ENERGY_KWH_METRIC,
    ObjectiveComputationError,
    ObjectiveValue,
    ObjectiveVector,
)
from simulator.optimize.profiles import load_profile
from simulator.optimize.recipe import (
    KnobSpec,
    RecipePatch,
    RecipeSchema,
    RecipeValidationError,
    _default_setpoint_value,
)
from simulator.optimize.strategy.bayesian import (
    _BAD_MAXIMIZE_VALUE,
    _BAD_MINIMIZE_VALUE,
    _CONSTRAINT_NAMES_ATTR,
    _CANDIDATE_ID_ATTR,
    _CONSTRAINT_VALUES_ATTR,
    _NONFINITE_INFEASIBLE_CONSTRAINT_VIOLATION,
    _UNSCOREABLE_OBJECTIVES_ATTR,
    _constraints_for_trial,
    _constraint_values,
    OPTUNA_REQUIRED_MESSAGE,
    OptunaUnavailableError,
)
from simulator.optimize.strategy.protocol import WarmStartSeed
from simulator.optimize.study import _profile_warm_start_seeds


ROOT = Path(__file__).resolve().parents[1]
PROFILE = {
    "objectives": [
        {"metric": "yield", "sense": "maximize", "units": "kg"},
        {"metric": "energy", "sense": "minimize", "units": "kWh"},
    ]
}
PATH = ("campaigns", "C0", "dT_dt_C_per_hr")


def _available_run_reference(
    furnace_status: str | None = "available",
    furnace_penalty: float = 0.0,
) -> RunReference:
    product_summary = {}
    if furnace_status is not None:
        product_summary = {
            "furnace_amortization_status": furnace_status,
            "furnace_amortization_batch_cost_equivalents": furnace_penalty,
        }
    return RunReference(
        status="ok",
        product_summary=product_summary,
    )


def _simple_schema() -> RecipeSchema:
    return RecipeSchema(
        allowlist=(
            KnobSpec(
                path=PATH,
                kind="float",
                low=0.0,
                high=1.0,
                bounds_source="test",
            ),
        )
    )


def _value(candidate: Candidate) -> float:
    return float(candidate.patch.values[PATH])


def _canonical_candidates(candidates: list[Candidate]) -> tuple[tuple[str, str], ...]:
    return tuple((candidate.id, candidate.patch.canonical_json()) for candidate in candidates)


def _gate_margin(*, margin: float, tolerance: float, feasible: bool) -> GateMargin:
    return GateMargin(
        gate="gate",
        feasible=feasible,
        margin=margin,
        threshold=ThresholdSpec(
            id="gate",
            value=0.0,
            units="unit",
            source="profile",
            source_ref="test",
            tolerance=tolerance,
        ),
        observed=0.0,
        detail="test",
    )


def _gate_margin_result(candidate: Candidate, gate_margin: GateMargin) -> ScoredResult:
    if gate_margin.feasible:
        return ScoredResult(
            candidate_id=candidate.id,
            eval_spec=None,
            cache_key=None,
            feasible=True,
            objectives=ObjectiveVector(
                (
                    ObjectiveValue(metric="yield", sense="maximize", value=1.0),
                    ObjectiveValue(metric="energy", sense="minimize", value=1.0),
                )
            ),
            feasibility_margins={"gate": gate_margin},
            run_reference=_available_run_reference(),
        )
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=None,
        cache_key=None,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
        feasibility_margins={"gate": gate_margin},
    )


def _null_objective_result(candidate: Candidate) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=None,
        cache_key=None,
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue(metric="yield", sense="maximize", value=None),
                ObjectiveValue(metric="energy", sense="minimize", value=1.0),
            )
        ),
        feasibility_margins={"gate": 0.25},
        run_reference=_available_run_reference(),
    )


def _trials_by_candidate(strategy: OptunaTPEStrategy) -> dict[str, object]:
    return {
        trial.user_attrs[_CANDIDATE_ID_ATTR]: trial
        for trial in strategy.study.trials
        if _CANDIDATE_ID_ATTR in trial.user_attrs
    }


def _assert_pressure_trial_params_match_patches(
    strategy: OptunaTPEStrategy,
    candidates: list[Candidate],
) -> None:
    schema = strategy.schema
    pressure_pairs = tuple(schema.PRESSURE_COUPLED_DEFAULT_PAIRS) + tuple(
        schema.C2A_STAGED_STAGE_PRESSURE_TOTAL_BY_PO2.items()
    )
    trials_by_number = {trial.number: trial for trial in strategy.study.trials}
    checked = 0
    for candidate in candidates:
        trial = trials_by_number[candidate.metadata["trial_number"]]
        for po2_path, total_path in pressure_pairs:
            for path in (po2_path, total_path):
                if path not in candidate.patch.values:
                    continue
                name = ".".join(path)
                assert name in trial.params
                assert float(trial.params[name]) == pytest.approx(
                    float(candidate.patch.values[path])
                )
                checked += 1
    assert checked > 0


def _feasible_result(
    candidate: Candidate,
    yield_value: float,
    energy: float,
    *,
    furnace_status: str | None = "available",
    furnace_penalty: float = 0.0,
) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=None,
        cache_key=None,
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue(metric="yield", sense="maximize", value=yield_value),
                ObjectiveValue(metric="energy", sense="minimize", value=energy),
            )
        ),
        feasibility_margins={"gate": 0.25},
        run_reference=_available_run_reference(furnace_status, furnace_penalty),
    )


def _single_objective_result(candidate: Candidate, metric: str, value: float) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=None,
        cache_key=None,
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue(metric=metric, sense="minimize", value=value),
            )
        ),
        feasibility_margins={"gate": 0.25},
        run_reference=_available_run_reference(),
    )


def _legacy_energy_cache_result(
    candidate: Candidate,
    *,
    yield_value: float,
    energy: float,
) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=None,
        cache_key="legacy-cache-key",
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue(metric="yield", sense="maximize", value=yield_value),
                ObjectiveValue(
                    metric=LEGACY_ENERGY_KWH_METRIC,
                    sense="minimize",
                    value=energy,
                    units="kWh",
                ),
            )
        ),
        feasibility_margins={"gate": 0.25},
        run_reference=_available_run_reference(),
    )


def _no_objective_infeasible_result(candidate: Candidate) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=None,
        cache_key=None,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
    )


def _infeasible_result(
    candidate: Candidate,
    *,
    candidate_id: str | None = None,
    margin: float = -0.5,
) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate.id if candidate_id is None else candidate_id,
        eval_spec=None,
        cache_key=None,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
        feasibility_margins={"gate": margin},
    )


def test_tpe_strategy_implements_protocol_and_round_trips() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=11, objective_profile=PROFILE)

    assert isinstance(strategy, Strategy)
    candidates = strategy.ask(2)
    strategy.tell(
        [
            (candidates[0], _feasible_result(candidates[0], 1.0, 2.0)),
            (candidates[1], _infeasible_result(candidates[1])),
        ]
    )

    assert strategy.tell_count == 2
    assert all(scored.candidate_id == candidate.id for candidate, scored in strategy.results)


def test_tpe_ask_returns_schema_valid_unique_deterministic_candidates() -> None:
    schema = RecipeSchema()

    first = OptunaTPEStrategy(schema, seed=13, objective_profile=PROFILE).ask(8)
    second = OptunaTPEStrategy(schema, seed=13, objective_profile=PROFILE).ask(8)
    different = OptunaTPEStrategy(schema, seed=14, objective_profile=PROFILE).ask(8)

    assert len(first) == 8
    assert len({candidate.id for candidate in first}) == 8
    assert _canonical_candidates(first) == _canonical_candidates(second)
    assert _canonical_candidates(first) != _canonical_candidates(different)
    for candidate in first:
        assert candidate.patch.validated(schema).canonical_json() == candidate.patch.canonical_json()
        assert all(not schema.is_forbidden(path) for path in candidate.patch.values)
        for spec in schema.search_allowlist:
            value = candidate.patch.values[spec.path]
            if spec.low is not None:
                assert float(value) >= float(spec.low)
            if spec.high is not None:
                assert float(value) <= float(spec.high)


def test_tpe_ask_batch_after_startup_does_not_duplicate_parameters() -> None:
    schema = RecipeSchema(
        allowlist=(
            KnobSpec(
                path=PATH,
                kind="categorical",
                choices=("low", "high"),
                bounds_source="test",
            ),
        )
    )
    strategy = OptunaTPEStrategy(
        schema,
        seed=0,
        objective_profile=PROFILE,
        n_startup_trials=10,
    )
    startup_candidates = strategy.ask(10)
    strategy.tell(
        [
            (
                candidate,
                _feasible_result(
                    candidate,
                    yield_value=float(index),
                    energy=float(10 - index),
                ),
            )
            for index, candidate in enumerate(startup_candidates)
        ]
    )

    batch = strategy.ask(2)

    assert len({candidate.patch.canonical_json() for candidate in batch}) == 2


def test_tpe_pressure_conditioning_updates_recorded_trial_params() -> None:
    strategy = OptunaTPEStrategy(RecipeSchema(), seed=17, objective_profile=PROFILE)
    candidates = strategy.ask(4)

    _assert_pressure_trial_params_match_patches(strategy, candidates)


def test_tpe_learns_toward_favored_region_after_tell_history() -> None:
    target = 0.85
    warmup = 40
    followup = 24
    strategy = OptunaTPEStrategy(
        _simple_schema(),
        seed=21,
        objective_profile=PROFILE,
        n_startup_trials=0,
        n_ei_candidates=64,
    )
    control = OptunaTPEStrategy(
        _simple_schema(),
        seed=21,
        objective_profile=PROFILE,
        n_startup_trials=0,
        n_ei_candidates=64,
    )
    warm_candidates = strategy.ask(warmup)
    control.ask(warmup)

    strategy.tell(
        [
            (
                candidate,
                _feasible_result(
                    candidate,
                    yield_value=1.0 - abs(_value(candidate) - target),
                    energy=abs(_value(candidate) - target),
                ),
            )
            for candidate in warm_candidates
        ]
    )

    learned = strategy.ask(followup)
    no_tell = control.ask(followup)
    learned_distance = sum(abs(_value(candidate) - target) for candidate in learned) / followup
    control_distance = sum(abs(_value(candidate) - target) for candidate in no_tell) / followup

    assert learned_distance < control_distance * 0.70
    assert sum(_value(candidate) for candidate in learned) / followup > target - 0.12


def test_tpe_same_seed_same_tells_same_followup() -> None:
    def canonical_followup() -> tuple[tuple[str, str], ...]:
        strategy = OptunaTPEStrategy(
            _simple_schema(),
            seed=24,
            objective_profile=PROFILE,
            n_startup_trials=0,
            n_ei_candidates=64,
        )
        warm_candidates = strategy.ask(32)
        strategy.tell(
            [
                (
                    candidate,
                    _feasible_result(
                        candidate,
                        yield_value=1.0 - abs(_value(candidate) - 0.7),
                        energy=abs(_value(candidate) - 0.7),
                    ),
                )
                for candidate in warm_candidates
            ]
        )
        return _canonical_candidates(strategy.ask(16))

    assert canonical_followup() == canonical_followup()


def test_tpe_multi_objective_directions_and_pareto_front_are_derived_from_profile() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=31, objective_profile=PROFILE)
    candidates = strategy.ask(3)

    strategy.tell(
        [
            (candidates[0], _feasible_result(candidates[0], 10.0, 10.0)),
            (candidates[1], _feasible_result(candidates[1], 5.0, 5.0)),
            (candidates[2], _feasible_result(candidates[2], 3.0, 12.0)),
        ]
    )

    assert strategy.directions == ("maximize", "minimize")
    assert len(strategy.study.directions) == len(PROFILE["objectives"])
    assert {trial.user_attrs["regolith_candidate_id"] for trial in strategy.best_trials} == {
        candidates[0].id,
        candidates[1].id,
    }
    assert strategy.pareto_front == strategy.best_trials


def test_tpe_directions_change_with_objective_profile() -> None:
    profile = {"objectives": [{"metric": "energy", "sense": "minimize"}]}
    strategy = OptunaTPEStrategy(_simple_schema(), seed=35, objective_profile=profile)

    assert strategy.directions == ("minimize",)
    assert len(strategy.study.directions) == 1


def test_tpe_single_objective_profile_round_trips() -> None:
    profile = {"objectives": [{"metric": "energy", "sense": "minimize"}]}
    strategy = OptunaTPEStrategy(_simple_schema(), seed=36, objective_profile=profile)
    candidate = strategy.ask(1)[0]

    strategy.tell([(candidate, _single_objective_result(candidate, "energy", 3.5))])

    trial = _trials_by_candidate(strategy)[candidate.id]
    assert trial.values == [3.5]
    assert strategy.best_trials[0].user_attrs[_CANDIDATE_ID_ATTR] == candidate.id


def test_tpe_constraints_rank_completed_trials_but_do_not_block_asks() -> None:
    strategy = OptunaTPEStrategy(
        _simple_schema(),
        seed=41,
        objective_profile=PROFILE,
        n_startup_trials=0,
    )
    candidates = strategy.ask(12)

    assert any(_value(candidate) < 0.35 for candidate in candidates)
    assert any(_value(candidate) > 0.65 for candidate in candidates)

    strategy.tell(
        [
            (candidates[0], _infeasible_result(candidates[0], margin=-1.0)),
            (candidates[1], _feasible_result(candidates[1], 0.1, 10.0)),
        ]
    )

    trials_by_candidate = {
        trial.user_attrs["regolith_candidate_id"]: trial
        for trial in strategy.study.trials
        if "regolith_candidate_id" in trial.user_attrs
    }
    assert trials_by_candidate[candidates[0].id].user_attrs["regolith_constraint_values"] == (1.0,)
    assert trials_by_candidate[candidates[1].id].user_attrs["regolith_constraint_values"] == (0.0,)
    assert strategy.ask(1)[0].patch.validated(strategy.schema)


def test_tpe_constraint_feasible_margin_not_violated() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=42, objective_profile=PROFILE)
    candidates = strategy.ask(2)
    feasible_margin = _gate_margin(margin=-0.05, tolerance=0.1, feasible=True)
    infeasible_margin = _gate_margin(margin=-0.05, tolerance=0.0, feasible=False)

    strategy.tell(
        [
            (
                candidates[0],
                ScoredResult(
                    candidate_id=candidates[0].id,
                    eval_spec=None,
                    cache_key=None,
                    feasible=True,
                    objectives=ObjectiveVector(
                        (
                            ObjectiveValue(metric="yield", sense="maximize", value=1.0),
                            ObjectiveValue(metric="energy", sense="minimize", value=1.0),
                        )
                    ),
                    feasibility_margins={"gate": feasible_margin},
                    run_reference=_available_run_reference(),
                ),
            ),
            (
                candidates[1],
                ScoredResult(
                    candidate_id=candidates[1].id,
                    eval_spec=None,
                    cache_key=None,
                    feasible=False,
                    failure_category=FailureCategory.INFEASIBLE_RECIPE,
                    feasibility_margins={"gate": infeasible_margin},
                ),
            ),
        ]
    )

    trials_by_candidate = _trials_by_candidate(strategy)
    assert trials_by_candidate[candidates[0].id].user_attrs[_CONSTRAINT_VALUES_ATTR] == (0.0,)
    assert trials_by_candidate[candidates[1].id].user_attrs[_CONSTRAINT_VALUES_ATTR] == (0.05,)


def test_tpe_qualified_continuous_margin_is_priced_not_constrained() -> None:
    candidate = Candidate(id="qualified-fouling", patch=RecipePatch({}))
    margin = _gate_margin(margin=-9.8, tolerance=0.0, feasible=True)
    margin = GateMargin(
        **{
            **margin.__dict__,
            "status_payload": {"constraint_mode": "continuous"},
        }
    )
    names, values = _constraint_values(_gate_margin_result(candidate, margin))

    assert names == ("gate",)
    assert values == (0.0,)


def test_tpe_nonfinite_gate_margins_map_to_finite_constraint_values() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=421, objective_profile=PROFILE)
    candidates = strategy.ask(3)
    feasible_inf = _gate_margin(margin=math.inf, tolerance=0.0, feasible=True)
    infeasible_neg_inf = _gate_margin(margin=-math.inf, tolerance=0.0, feasible=False)
    na_pass = _gate_margin(margin=-math.inf, tolerance=0.0, feasible=True)

    strategy.tell(
        [
            (candidates[0], _gate_margin_result(candidates[0], feasible_inf)),
            (candidates[1], _gate_margin_result(candidates[1], infeasible_neg_inf)),
            (candidates[2], _gate_margin_result(candidates[2], na_pass)),
        ]
    )

    trials_by_candidate = _trials_by_candidate(strategy)
    assert trials_by_candidate[candidates[0].id].user_attrs[_CONSTRAINT_VALUES_ATTR] == (0.0,)
    assert trials_by_candidate[candidates[1].id].user_attrs[_CONSTRAINT_VALUES_ATTR] == (
        _NONFINITE_INFEASIBLE_CONSTRAINT_VIOLATION,
    )
    assert trials_by_candidate[candidates[2].id].user_attrs[_CONSTRAINT_VALUES_ATTR] == (0.0,)


def test_tpe_nan_gate_margin_fails_loud() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=422, objective_profile=PROFILE)
    candidate = strategy.ask(1)[0]
    nan_margin = _gate_margin(margin=math.nan, tolerance=0.0, feasible=True)

    with pytest.raises(ValueError, match="constraint margin"):
        strategy.tell([(candidate, _gate_margin_result(candidate, nan_margin))])

    assert strategy.tell_count == 0
    assert strategy.study.trials[0].state.name == "RUNNING"


def test_tpe_constraints_for_trial_rejects_malformed_or_nonfinite_attrs() -> None:
    assert _constraints_for_trial(SimpleNamespace(user_attrs={})) == (0.0,)

    bad_attrs = [
        (),
        "bad",
        ("bad",),
        ("nan",),
        (float("nan"),),
        (float("inf"),),
    ]
    for raw in bad_attrs:
        trial = SimpleNamespace(user_attrs={_CONSTRAINT_VALUES_ATTR: raw})
        with pytest.raises(ValueError, match="constraint values"):
            _constraints_for_trial(trial)


def test_tpe_constraints_do_not_prefilter_infeasible_region() -> None:
    strategy = OptunaTPEStrategy(
        _simple_schema(),
        seed=43,
        objective_profile=PROFILE,
        n_startup_trials=0,
        n_ei_candidates=64,
    )
    warm_candidates = strategy.ask(80)
    strategy.tell(
        [
            (
                candidate,
                _infeasible_result(candidate, margin=_value(candidate) - 0.5)
                if _value(candidate) < 0.5
                else _feasible_result(candidate, 0.0, 0.0),
            )
            for candidate in warm_candidates
        ]
    )

    followup = strategy.ask(80)

    assert any(_value(candidate) < 0.5 for candidate in followup)


def test_tpe_infeasible_result_uses_directional_worst_values_not_zero() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=44, objective_profile=PROFILE)
    candidate = strategy.ask(1)[0]

    strategy.tell([(candidate, _no_objective_infeasible_result(candidate))])

    trial = _trials_by_candidate(strategy)[candidate.id]
    assert trial.values == [_BAD_MAXIMIZE_VALUE, _BAD_MINIMIZE_VALUE]
    assert trial.user_attrs[_CONSTRAINT_VALUES_ATTR] == (1.0,)


def _lunar_profile_for_hours(hours: int, *, gates: tuple[str, ...] | None = None):
    profile = dict(load_profile("lunar_mare_low_ti"))
    fidelities = {
        name: dict(options)
        for name, options in profile["fidelities"].items()
    }
    selected = dict(fidelities[ANALYTICAL_BACKEND_SERIALIZATION_TOKEN])
    selected["hours"] = hours
    fidelities[ANALYTICAL_BACKEND_SERIALIZATION_TOKEN] = selected
    profile["fidelities"] = fidelities
    if gates is not None:
        constraints = dict(profile["constraints"])
        constraints["gates"] = list(gates)
        profile["constraints"] = constraints
    return profile


def test_tpe_preserves_infeasible_yield_signal_for_gated_sixty_hour_pair() -> None:
    furnace_path = ("furnace_max_T_C",)
    schema = RecipeSchema(
        allowlist=(
            KnobSpec(
                path=furnace_path,
                kind="float",
                low=1200.0,
                high=2200.0,
                bounds_source="C5 acceptance",
            ),
        )
    )
    profile = _lunar_profile_for_hours(60)
    gates = set(profile["constraints"]["gates"])
    assert {"delivered_stream_purity", "extraction_completeness"} <= gates

    strategy = OptunaTPEStrategy(
        schema,
        seed=5505,
        objective_profile=profile,
        n_startup_trials=0,
        warm_start_seeds=(
            WarmStartSeed(
                id="nonbinding-1800-cap",
                patch=RecipePatch({furnace_path: 1800.0}),
                proposal_source="seed_recipe",
            ),
            WarmStartSeed(
                id="furnace-cap-1200",
                patch=RecipePatch({furnace_path: 1200.0}),
                proposal_source="seed_recipe",
            ),
        ),
    )
    candidates = strategy.ask(2)
    results = [
        evaluate(
            candidate.patch,
            "lunar_mare_low_ti",
            ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
            profile=profile,
            candidate_id=candidate.id,
        )
        for candidate in candidates
    ]

    for result in results:
        assert result.eval_spec is not None and result.eval_spec.hours == 60
        assert not result.feasible
        assert result.objectives is not None
        assert {"delivered_stream_purity", "extraction_completeness"} & set(
            result.failing_gates
        )

    strategy.tell(list(zip(candidates, results, strict=True)))
    trials = _trials_by_candidate(strategy)
    metals_index = strategy.objective_metrics.index("metals_total_kg")
    stored_metals = []
    for candidate, result in zip(candidates, results, strict=True):
        trial = trials[candidate.id]
        assert trial.state.name == "COMPLETE"
        assert trial.values is not None
        value = trial.values[metals_index]
        assert math.isfinite(value)
        assert value not in (_BAD_MAXIMIZE_VALUE, _BAD_MINIMIZE_VALUE)
        assert set(result.failing_gates) <= set(
            trial.user_attrs[_CONSTRAINT_NAMES_ATTR]
        )
        assert trial.user_attrs[_CONSTRAINT_VALUES_ATTR]
        stored_metals.append(value)
    # Identical deterministic patches have zero repeat spread at one cache key.
    assert stored_metals[0] != stored_metals[1]

    short_profile = _lunar_profile_for_hours(
        1,
        gates=("knudsen_viscous", "furnace_temperature"),
    )
    short_results = [
        evaluate(
            candidate.patch,
            "lunar_mare_low_ti",
            ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
            profile=short_profile,
            candidate_id=f"short-{candidate.id}",
        )
        for candidate in candidates
    ]
    assert all(result.feasible and result.objectives is not None for result in short_results)
    assert all(
        result.run_reference is not None
        and result.run_reference.product_summary.get("furnace_amortization_status")
        == "available"
        for result in short_results
    )
    short_metals = [
        result.objectives.as_mapping()["metals_total_kg"]
        for result in short_results
    ]
    short_oxygen = [
        result.objectives.as_mapping()["oxygen_kg"]
        for result in short_results
    ]
    assert short_metals[0] == short_metals[1]
    assert short_oxygen[0] == short_oxygen[1]
    assert all(
        result.objectives.as_mapping()["duration_h"] == result.eval_spec.hours
        for result in short_results
    )


def test_tpe_infeasible_objectives_without_furnace_cost_use_raw_metrics() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=5506, objective_profile=PROFILE)
    candidate = strategy.ask(1)[0]
    raw = _no_objective_infeasible_result(candidate)
    scored = replace(
        raw,
        objectives=ObjectiveVector(
            (
                ObjectiveValue(metric="yield", sense="maximize", value=2.5),
                ObjectiveValue(metric="energy", sense="minimize", value=8.0),
            )
        ),
        feasibility_margins={
            "gate": _gate_margin(margin=-0.5, tolerance=0.0, feasible=False)
        },
        run_reference=_available_run_reference(furnace_status=None),
    )

    strategy.tell([(candidate, scored)])

    trial = _trials_by_candidate(strategy)[candidate.id]
    assert trial.state.name == "COMPLETE"
    assert trial.values == [
        scored.objectives.as_mapping()["yield"],
        scored.objectives.as_mapping()["energy"],
    ]
    assert any(value > 0.0 for value in trial.user_attrs[_CONSTRAINT_VALUES_ATTR])


def test_tpe_feasible_unscoreable_result_fails_trial_without_bad_objective_values() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=45, objective_profile=PROFILE)
    candidate = strategy.ask(1)[0]

    strategy.tell([(candidate, _null_objective_result(candidate))])

    trial = _trials_by_candidate(strategy)[candidate.id]
    assert trial.state.name == "FAIL"
    assert trial.values is None
    assert trial.user_attrs[_CONSTRAINT_VALUES_ATTR] == (0.0,)
    assert trial.user_attrs[_UNSCOREABLE_OBJECTIVES_ATTR] is True
    assert strategy.tell_count == 1


@pytest.mark.parametrize("furnace_status", [None, "batch_cost_unavailable"])
def test_tpe_missing_or_degraded_furnace_evidence_is_unscoreable(
    furnace_status: str | None,
) -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=451, objective_profile=PROFILE)
    candidate = strategy.ask(1)[0]

    strategy.tell(
        [
            (
                candidate,
                _feasible_result(
                    candidate,
                    1.0,
                    2.0,
                    furnace_status=furnace_status,
                ),
            )
        ]
    )

    trial = _trials_by_candidate(strategy)[candidate.id]
    assert trial.state.name == "FAIL"
    assert trial.values is None
    assert trial.user_attrs[_UNSCOREABLE_OBJECTIVES_ATTR] is True
    assert strategy.tell_count == 1


def test_tpe_corrupted_available_furnace_evidence_propagates() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=452, objective_profile=PROFILE)
    candidate = strategy.ask(1)[0]

    with pytest.raises(
        ObjectiveComputationError,
        match="furnace amortization penalty must be non-negative",
    ):
        strategy.tell(
            [
                (
                    candidate,
                    _feasible_result(
                        candidate,
                        1.0,
                        2.0,
                        furnace_penalty=-1.0,
                    ),
                )
            ]
        )


def test_tpe_scores_legacy_energy_cache_objective_against_canonical_profile() -> None:
    profile = {
        "objectives": [
            {"metric": "yield", "sense": "maximize", "units": "kg"},
            {
                "metric": ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
                "sense": "minimize",
                "units": "kWh",
            },
        ]
    }
    strategy = OptunaTPEStrategy(_simple_schema(), seed=46, objective_profile=profile)
    candidate = strategy.ask(1)[0]

    strategy.tell(
        [
            (
                candidate,
                _legacy_energy_cache_result(
                    candidate,
                    yield_value=1.25,
                    energy=2.5,
                ),
            )
        ]
    )

    trial = _trials_by_candidate(strategy)[candidate.id]
    assert trial.state.name == "COMPLETE"
    assert trial.values == [1.25, 2.5]
    assert strategy.tell_count == 1


@pytest.mark.parametrize("strategy_class", [OptunaTPEStrategy, OptunaNSGA2Strategy])
def test_highland_profile_seed_enqueues_seed_values_and_loaded_defaults(strategy_class) -> None:
    pytest.importorskip("optuna")
    profile = load_profile("lunar_highland")
    schema = RecipeSchema()
    (seed,) = _profile_warm_start_seeds(profile, schema=schema)
    expected_params: dict[str, object] = {}
    no_default_paths: set[str] = set()
    missing_default_marker = object()

    for spec in schema.search_allowlist:
        name = ".".join(spec.path)
        if spec.path in seed.patch.values:
            expected_params[name] = seed.patch.values[spec.path]
            continue
        try:
            default = _default_setpoint_value(spec.path)
        except RecipeValidationError as exc:
            if not str(exc).startswith("recipe_pressure_total_default_missing:"):
                raise
            default = missing_default_marker
        if default is missing_default_marker:
            no_default_paths.add(name)
            continue
        if spec.kind == "float":
            has_unambiguous_default = isinstance(default, Real) and not isinstance(
                default, bool
            )
        elif spec.kind == "int":
            has_unambiguous_default = (
                isinstance(default, Real)
                and not isinstance(default, bool)
                and float(default).is_integer()
            )
        else:
            has_unambiguous_default = default in (spec.choices or ())
        if has_unambiguous_default:
            expected_params[name] = default
        else:
            no_default_paths.add(name)

    hold_path = ("campaigns", "C6", "default_hold_T_C")
    hold_spec = schema.spec_for(hold_path)
    assert hold_spec.high is not None
    bad_values = dict(seed.patch.values)
    # Use the next representable float above the schema maximum to avoid a copied bound.
    bad_values[hold_path] = math.nextafter(float(hold_spec.high), math.inf)
    bad_seed = replace(
        seed,
        id=f"{seed.id}-out-of-bounds",
        patch=RecipePatch(bad_values),
    )

    strategy = strategy_class(
        schema,
        seed=451,
        objective_profile=profile,
        warm_start_seeds=(seed, bad_seed),
    )
    assert strategy.warm_start_rejected_seed_ids == (bad_seed.id,)
    waiting = strategy._study.get_trials(deepcopy=False)
    assert len(waiting) == 1
    assert waiting[0].state.name == "WAITING"
    enqueued_params = waiting[0].system_attrs["fixed_params"]

    assert enqueued_params == expected_params
    search_paths = {spec.path for spec in schema.search_allowlist}
    expected_c6_seed_params = {
        ".".join(path): value
        for path, value in seed.patch.values.items()
        if path in search_paths and path[:2] == ("campaigns", "C6")
    }
    assert {
        name: enqueued_params[name] for name in expected_c6_seed_params
    } == expected_c6_seed_params
    assert {".".join(spec.path) for spec in schema.search_allowlist} - set(
        enqueued_params
    ) == no_default_paths


@pytest.mark.parametrize("strategy_class", [OptunaTPEStrategy, OptunaNSGA2Strategy])
def test_partial_seed_trial_samples_unset_knobs(strategy_class) -> None:
    pytest.importorskip("optuna")
    seeded_path = ("optimizer_test", "seeded")
    sampled_path = ("optimizer_test", "sampled")
    schema = RecipeSchema(
        allowlist=(
            KnobSpec(
                path=seeded_path,
                kind="float",
                low=0.0,
                high=1.0,
                bounds_source="C11 partial seed test",
            ),
            KnobSpec(
                path=sampled_path,
                kind="float",
                low=0.0,
                high=1.0,
                bounds_source="C11 partial seed test",
            ),
        )
    )
    seed = WarmStartSeed(
        id="partial-seed",
        patch=RecipePatch({seeded_path: 0.25}),
        proposal_source="seed_recipe",
    )
    strategy = strategy_class(
        schema,
        seed=452,
        objective_profile=PROFILE,
        warm_start_seeds=(seed,),
    )

    (candidate,) = strategy.ask(1)

    assert candidate.patch.values[seeded_path] == 0.25
    assert 0.0 <= candidate.patch.values[sampled_path] <= 1.0


def test_tpe_import_boundary_is_lazy_without_optuna() -> None:
    code = """
import builtins
import sys
real_import = builtins.__import__
def blocked_import(name, *args, **kwargs):
    if name == "optuna" or name.startswith("optuna."):
        raise ImportError("blocked optuna")
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked_import
import simulator.optimize
import simulator.optimize.strategy
print("OK", "optuna" in sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.stdout.strip() == "OK False"


def test_tpe_instantiation_fails_loud_when_optuna_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import_module = importlib.import_module

    def blocked_import_module(name: str, package: str | None = None) -> object:
        if name == "optuna" or name.startswith("optuna."):
            raise ImportError("blocked optuna")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", blocked_import_module)

    with pytest.raises(OptunaUnavailableError, match=re.escape(OPTUNA_REQUIRED_MESSAGE)):
        OptunaTPEStrategy(_simple_schema(), seed=51, objective_profile=PROFILE)


def test_tpe_tell_contract_is_atomic_for_mismatch_duplicate_and_unknown() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=61, objective_profile=PROFILE)
    candidates = strategy.ask(2)

    with pytest.raises(ValueError, match="candidate_id"):
        strategy.tell(
            [
                (candidates[0], _feasible_result(candidates[0], 1.0, 1.0)),
                (candidates[1], _infeasible_result(candidates[1], candidate_id="other")),
            ]
        )

    assert strategy.tell_count == 0
    assert strategy.results == ()
    assert all(trial.state.name == "RUNNING" for trial in strategy.study.trials)

    with pytest.raises(ValueError, match="duplicate candidate_id"):
        strategy.tell(
            [
                (candidates[0], _feasible_result(candidates[0], 1.0, 1.0)),
                (candidates[0], _feasible_result(candidates[0], 1.0, 1.0)),
            ]
        )

    unknown = Candidate(id="unknown", patch=RecipePatch({PATH: 0.5}).validated(_simple_schema()))
    with pytest.raises(ValueError, match="not planned"):
        strategy.tell([(unknown, _feasible_result(unknown, 1.0, 1.0))])

    strategy.tell([(candidates[0], _feasible_result(candidates[0], 1.0, 1.0))])
    before = strategy.results
    with pytest.raises(ValueError, match="already recorded"):
        strategy.tell([(candidates[0], _feasible_result(candidates[0], 1.0, 1.0))])

    assert strategy.tell_count == 1
    assert strategy.results == before


def test_tpe_tell_rejects_non_scored_result() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=62, objective_profile=PROFILE)
    candidate = strategy.ask(1)[0]
    fake = SimpleNamespace(
        candidate_id=candidate.id,
        feasible=True,
        objectives=SimpleNamespace(as_mapping=lambda: {"yield": 1.0, "energy": 1.0}),
        feasibility_margins={"gate": 0.25},
    )

    with pytest.raises(ValueError, match="ScoredResult"):
        strategy.tell([(candidate, fake)])  # type: ignore[list-item]

    assert strategy.tell_count == 0
    assert strategy.results == ()
    assert all(trial.state.name == "RUNNING" for trial in strategy.study.trials)


def test_tpe_tell_rejects_patch_or_metadata_mismatch() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=63, objective_profile=PROFILE)
    candidate = strategy.ask(1)[0]
    mutated_value = 0.0 if _value(candidate) != 0.0 else 1.0
    patch_mismatch = Candidate(
        id=candidate.id,
        patch=RecipePatch({PATH: mutated_value}).validated(_simple_schema()),
        metadata=candidate.metadata,
    )
    metadata_mismatch = Candidate(
        id=candidate.id,
        patch=candidate.patch,
        metadata={**dict(candidate.metadata), "extra": "value"},
    )

    with pytest.raises(ValueError, match="patch"):
        strategy.tell([(patch_mismatch, _feasible_result(patch_mismatch, 1.0, 1.0))])
    with pytest.raises(ValueError, match="metadata"):
        strategy.tell(
            [(metadata_mismatch, _feasible_result(metadata_mismatch, 1.0, 1.0))]
        )

    assert strategy.tell_count == 0
    assert strategy.results == ()
    assert all(trial.state.name == "RUNNING" for trial in strategy.study.trials)


def test_tpe_tell_empty_batch_is_documented_noop() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=64, objective_profile=PROFILE)
    strategy.ask(1)
    before = [
        (trial.number, trial.state.name, dict(trial.user_attrs))
        for trial in strategy.study.trials
    ]

    strategy.tell([])

    after = [
        (trial.number, trial.state.name, dict(trial.user_attrs))
        for trial in strategy.study.trials
    ]
    assert strategy.tell_count == 0
    assert strategy.results == ()
    assert after == before


def test_tpe_ask_edge_cases() -> None:
    strategy = OptunaTPEStrategy(_simple_schema(), seed=71, objective_profile=PROFILE)

    assert strategy.ask(0) == []
    for bad_n in (-1, True, "x"):
        with pytest.raises(ValueError, match="non-negative int"):
            strategy.ask(bad_n)  # type: ignore[arg-type]

    candidates = strategy.ask(1) + strategy.ask(2)

    assert [candidate.id for candidate in candidates] == [
        "tpe-71-000000",
        "tpe-71-000001",
        "tpe-71-000002",
    ]
