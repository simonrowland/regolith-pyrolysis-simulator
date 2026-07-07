from __future__ import annotations

import importlib
import math
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.optimize import (
    Candidate,
    GateMargin,
    OptunaTPEStrategy,
    Strategy,
    ThresholdSpec,
)
from simulator.optimize.evaluate import FailureCategory, ScoredResult
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.recipe import KnobSpec, RecipePatch, RecipeSchema
from simulator.optimize.strategy.bayesian import (
    _BAD_MAXIMIZE_VALUE,
    _BAD_MINIMIZE_VALUE,
    _CANDIDATE_ID_ATTR,
    _CONSTRAINT_VALUES_ATTR,
    _NONFINITE_INFEASIBLE_CONSTRAINT_VIOLATION,
    _UNSCOREABLE_OBJECTIVES_ATTR,
    _constraints_for_trial,
    OPTUNA_REQUIRED_MESSAGE,
    OptunaUnavailableError,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE = {
    "objectives": [
        {"metric": "yield", "sense": "maximize", "units": "kg"},
        {"metric": "energy", "sense": "minimize", "units": "kWh"},
    ]
}
PATH = ("campaigns", "C0", "dT_dt_C_per_hr")


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


def _feasible_result(candidate: Candidate, yield_value: float, energy: float) -> ScoredResult:
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
