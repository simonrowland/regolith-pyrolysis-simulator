from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pytest

from simulator.optimize import Candidate, MorrisScreenStrategy, Strategy
from simulator.optimize.evaluate import FailureCategory, ScoredResult
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.recipe import KnobSpec, RecipeSchema


def _canonical_candidates(candidates: list[Candidate]) -> tuple[tuple[str, str], ...]:
    return tuple((candidate.id, candidate.patch.canonical_json()) for candidate in candidates)


def _feasible_result(
    candidate: Candidate, value: float, *, metric: str = "known"
) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=None,
        cache_key=None,
        feasible=True,
        objectives=ObjectiveVector(
            (ObjectiveValue(metric=metric, sense="maximize", value=value),)
        ),
    )


def _feasible_result_many(candidate: Candidate, metrics: dict[str, float]) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=None,
        cache_key=None,
        feasible=True,
        objectives=ObjectiveVector(
            tuple(
                ObjectiveValue(metric=metric, sense="maximize", value=value)
                for metric, value in sorted(metrics.items())
            )
        ),
    )


def _infeasible_result(candidate: Candidate, *, candidate_id: str | None = None) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate.id if candidate_id is None else candidate_id,
        eval_spec=None,
        cache_key=None,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
    )


def _all_candidates(strategy: MorrisScreenStrategy) -> list[Candidate]:
    return strategy.ask(strategy.plan_length + 10)


def _group_by_name(strategy: MorrisScreenStrategy) -> dict[str, object]:
    return {group.name: group for group in strategy.groups}


def _screen_signature(strategy: MorrisScreenStrategy) -> tuple[tuple[object, ...], ...]:
    result = strategy.screen_result()
    return tuple(
        (
            group.name,
            group.aggregate_mu_star,
            group.aggregate_sigma,
            group.recommendation,
            tuple(
                (metric, row.mu_star, row.sigma, row.effects)
                for metric, row in sorted(group.objectives.items())
            ),
        )
        for group in result.groups
    )


def _unit_value(schema: RecipeSchema, candidate: Candidate, path: tuple[str, ...]) -> float:
    spec = schema.spec_for(path)
    if spec.low is None or spec.high is None:
        raise AssertionError(f"{path!r} lacks numeric bounds")
    return (float(candidate.patch.values[path]) - float(spec.low)) / (
        float(spec.high) - float(spec.low)
    )


def _mean_group_unit(
    schema: RecipeSchema, candidate: Candidate, paths: tuple[tuple[str, ...], ...]
) -> float:
    return sum(_unit_value(schema, candidate, path) for path in paths) / len(paths)


def _changed_paths(previous: Candidate, current: Candidate) -> set[tuple[str, ...]]:
    return {
        path
        for path in previous.patch.values
        if _meaningfully_changed(previous.patch.values[path], current.patch.values[path])
    }


def _meaningfully_changed(previous: object, current: object) -> bool:
    if isinstance(previous, float) and isinstance(current, float):
        return not math.isclose(previous, current, rel_tol=1e-12, abs_tol=1e-12)
    return previous != current


class _BadObjectives:
    def __init__(self, value: object) -> None:
        self._value = value

    def as_mapping(self) -> dict[str, object]:
        if isinstance(self._value, BaseException):
            raise self._value
        return {"bad": self._value}


def test_morris_strategy_implements_protocol_and_round_trips() -> None:
    strategy = MorrisScreenStrategy(RecipeSchema(), seed=5, num_trajectories=1)

    assert isinstance(strategy, Strategy)
    candidates = strategy.ask(2)
    strategy.tell([(candidate, _feasible_result(candidate, 1.0)) for candidate in candidates])

    assert strategy.tell_count == 2


def test_morris_ask_returns_valid_unique_deterministic_candidates() -> None:
    schema = RecipeSchema()
    first = _all_candidates(MorrisScreenStrategy(schema, seed=13, num_trajectories=2))
    second = _all_candidates(MorrisScreenStrategy(schema, seed=13, num_trajectories=2))
    different = _all_candidates(MorrisScreenStrategy(schema, seed=14, num_trajectories=2))

    assert len(first) == 2 * (len(MorrisScreenStrategy(schema, seed=13).groups) + 1)
    assert len({candidate.id for candidate in first}) == len(first)
    assert _canonical_candidates(first) == _canonical_candidates(second)
    assert _canonical_candidates(first) != _canonical_candidates(different)
    for candidate in first:
        assert candidate.patch.validated(schema).canonical_json() == candidate.patch.canonical_json()
        assert all(not schema.is_forbidden(path) for path in candidate.patch.values)


def test_morris_groups_partition_allowlist_and_residual_group_when_needed() -> None:
    schema = RecipeSchema()
    strategy = MorrisScreenStrategy(schema, seed=17, num_trajectories=1)
    allowlist = {spec.path for spec in schema.search_allowlist}
    seen: set[tuple[str, ...]] = set()

    for group in strategy.groups:
        assert seen.isdisjoint(group.paths)
        seen.update(group.paths)
    assert seen == allowlist

    groups = _group_by_name(strategy)
    assert ("campaigns", "C4", "temp_range_C") in groups["thermo"].paths
    assert (
        "campaigns",
        "C2A_continuous",
        "dT_dt_C_per_hr",
        "early_ramp_1050_1320C",
    ) in groups["schedule"].paths
    assert ("campaigns", "C3", "K_phase", "pO2_bakeout_mbar") in groups["chemistry"].paths

    residual_spec = KnobSpec(
        path=("campaigns", "custom", "stir_factor"),
        kind="float",
        low=0.0,
        high=1.0,
        bounds_source="test",
    )
    residual_schema = RecipeSchema(allowlist=schema.allowlist + (residual_spec,))
    residual_groups = _group_by_name(
        MorrisScreenStrategy(residual_schema, seed=17, num_trajectories=1)
    )

    assert residual_spec.path in residual_groups["residual"].paths


def test_morris_all_never_prune_groups_keep_at_zero_effect() -> None:
    strategy = MorrisScreenStrategy(
        RecipeSchema(), seed=19, num_trajectories=2, prune_threshold=1e-9
    )
    candidates = _all_candidates(strategy)
    strategy.tell([(candidate, _feasible_result(candidate, 0.0)) for candidate in candidates])

    groups = {group.name: group for group in strategy.screen_result().groups}

    for planned_group in strategy.groups:
        if not planned_group.never_prune:
            continue
        assert groups[planned_group.name].never_prune
        assert groups[planned_group.name].aggregate_mu_star == 0.0
        assert groups[planned_group.name].recommendation == "keep"
    assert groups["chemistry"].recommendation == "prune"


def test_morris_elementary_effect_math_ranks_known_linear_group() -> None:
    coefficient = 7.5
    target_group = "chemistry"
    schema = RecipeSchema()
    strategy = MorrisScreenStrategy(schema, seed=23, num_trajectories=4)
    candidates = _all_candidates(strategy)
    target_paths = _group_by_name(strategy)[target_group].paths

    strategy.tell(
        [
            (
                candidate,
                _feasible_result(
                    candidate,
                    2.0
                    + coefficient * _mean_group_unit(schema, candidate, target_paths),
                ),
            )
            for candidate in candidates
        ]
    )

    result = strategy.screen_result()
    by_name = {group.name: group for group in result.groups}
    expected_delta = strategy.num_levels / (2 * (strategy.num_levels - 1))
    target_effects = []
    for previous, current in zip(candidates, candidates[1:]):
        if current.metadata["trajectory"] != previous.metadata["trajectory"]:
            continue
        if current.metadata["moved_group"] != target_group:
            continue
        actual_delta = _mean_group_unit(schema, current, target_paths) - _mean_group_unit(
            schema, previous, target_paths
        )
        target_effects.append(coefficient * actual_delta / actual_delta)
        assert abs(actual_delta) == pytest.approx(expected_delta)

    assert result.groups[0].name == target_group
    assert by_name[target_group].objectives["known"].mu_star == pytest.approx(coefficient)
    assert tuple(abs(effect) for effect in target_effects) == pytest.approx(
        tuple(abs(effect) for effect in by_name[target_group].objectives["known"].effects)
    )
    assert by_name["thermo"].objectives["known"].mu_star == pytest.approx(0.0)
    assert by_name["schedule"].objectives["known"].mu_star == pytest.approx(0.0)


def test_morris_patch_steps_move_all_and_only_moved_group_paths() -> None:
    strategy = MorrisScreenStrategy(RecipeSchema(), seed=24, num_trajectories=1)
    candidates = _all_candidates(strategy)
    paths_by_group = {group.name: set(group.paths) for group in strategy.groups}
    saw_multi_knob_group = False

    for previous, current in zip(candidates, candidates[1:]):
        moved_group = current.metadata["moved_group"]
        expected_paths = paths_by_group[moved_group]
        changed_paths = _changed_paths(previous, current)

        assert changed_paths == expected_paths
        saw_multi_knob_group = saw_multi_knob_group or len(expected_paths) > 1

    assert saw_multi_knob_group


def test_morris_multi_objective_recommendation_is_per_objective_aware() -> None:
    schema = RecipeSchema()
    strategy = MorrisScreenStrategy(
        schema, seed=25, num_trajectories=2, prune_threshold=1.0
    )
    candidates = _all_candidates(strategy)
    chemistry_paths = _group_by_name(strategy)["chemistry"].paths

    strategy.tell(
        [
            (
                candidate,
                _feasible_result_many(
                    candidate,
                    {
                        "zero": 0.0,
                        "small_scale": 1.5
                        * _mean_group_unit(schema, candidate, chemistry_paths),
                    },
                ),
            )
            for candidate in candidates
        ]
    )

    groups = {group.name: group for group in strategy.screen_result().groups}

    assert groups["chemistry"].objectives["small_scale"].mu_star == pytest.approx(1.5)
    assert groups["chemistry"].objectives["zero"].mu_star == pytest.approx(0.0)
    assert groups["chemistry"].recommendation == "keep"


def test_morris_deterministic_screen_result_and_seed_changes_plan() -> None:
    first = MorrisScreenStrategy(RecipeSchema(), seed=29, num_trajectories=3)
    second = MorrisScreenStrategy(RecipeSchema(), seed=29, num_trajectories=3)
    different = MorrisScreenStrategy(RecipeSchema(), seed=30, num_trajectories=3)
    first_candidates = _all_candidates(first)
    second_candidates = _all_candidates(second)

    assert _canonical_candidates(first_candidates) == _canonical_candidates(second_candidates)
    assert _canonical_candidates(first_candidates) != _canonical_candidates(
        _all_candidates(different)
    )

    for strategy, candidates in ((first, first_candidates), (second, second_candidates)):
        strategy.tell(
            [
                (
                    candidate,
                    _feasible_result(
                        candidate,
                        3.0 * candidate.metadata["group_units"]["chemistry"]
                        + candidate.metadata["group_units"]["schedule"],
                    ),
                )
                for candidate in candidates
            ]
        )

    assert _screen_signature(first) == _screen_signature(second)


def test_morris_effect_order_independent_of_tell_order() -> None:
    first = MorrisScreenStrategy(RecipeSchema(), seed=30, num_trajectories=3)
    second = MorrisScreenStrategy(RecipeSchema(), seed=30, num_trajectories=3)
    first_candidates = _all_candidates(first)
    second_candidates = _all_candidates(second)

    def value(candidate: Candidate) -> float:
        return (
            3.0 * candidate.metadata["group_units"]["chemistry"]
            + candidate.metadata["group_units"]["schedule"]
        )

    by_trajectory: dict[int, list[Candidate]] = {}
    for candidate in first_candidates:
        by_trajectory.setdefault(candidate.metadata["trajectory"], []).append(candidate)

    for trajectory in [0, 1, 2]:
        first.tell(
            [
                (candidate, _feasible_result(candidate, value(candidate)))
                for candidate in by_trajectory[trajectory]
            ]
        )

    by_trajectory = {}
    for candidate in second_candidates:
        by_trajectory.setdefault(candidate.metadata["trajectory"], []).append(candidate)
    for trajectory in [2, 0, 1]:
        second.tell(
            [
                (candidate, _feasible_result(candidate, value(candidate)))
                for candidate in by_trajectory[trajectory]
            ]
        )

    assert _screen_signature(first) == _screen_signature(second)


def test_morris_tell_rejects_spoofed_candidate_metadata() -> None:
    strategy = MorrisScreenStrategy(RecipeSchema(), seed=31, num_trajectories=1)
    candidate = strategy.ask(1)[0]
    metadata = dict(candidate.metadata)
    metadata["trajectory"] = 999
    spoofed = Candidate(id=candidate.id, patch=candidate.patch, metadata=metadata)

    with pytest.raises(ValueError, match="metadata"):
        strategy.tell([(spoofed, _feasible_result(spoofed, 1.0))])

    assert strategy.tell_count == 0
    assert strategy.results == ()


def test_morris_tell_contract_is_atomic_and_deduped() -> None:
    strategy = MorrisScreenStrategy(RecipeSchema(), seed=31, num_trajectories=1)
    candidates = strategy.ask(2)
    bad_result = _infeasible_result(candidates[1], candidate_id="other")

    with pytest.raises(ValueError, match="candidate_id"):
        strategy.tell(
            [(candidates[0], _infeasible_result(candidates[0])), (candidates[1], bad_result)]
        )

    assert strategy.tell_count == 0
    assert strategy.results == ()

    with pytest.raises(ValueError, match="duplicate candidate_id"):
        strategy.tell(
            [
                (candidates[0], _infeasible_result(candidates[0])),
                (candidates[0], _infeasible_result(candidates[0])),
            ]
        )

    assert strategy.tell_count == 0
    assert strategy.results == ()

    strategy.tell([(candidates[0], _infeasible_result(candidates[0]))])
    before = strategy.results

    with pytest.raises(ValueError, match="already recorded"):
        strategy.tell([(candidates[0], _infeasible_result(candidates[0]))])

    assert strategy.tell_count == 1
    assert strategy.results == before


def test_morris_screen_result_requires_complete_trajectories() -> None:
    strategy = MorrisScreenStrategy(RecipeSchema(), seed=33, num_trajectories=1)

    with pytest.raises(ValueError, match="complete"):
        strategy.screen_result()

    candidates = _all_candidates(strategy)
    strategy.tell(
        [
            (candidate, _infeasible_result(candidate))
            if index == 0
            else (candidate, _feasible_result(candidate, float(index)))
            for index, candidate in enumerate(candidates)
        ]
    )

    with pytest.raises(ValueError, match="complete"):
        strategy.screen_result()


def test_morris_rejects_mismatched_objective_metric_sets() -> None:
    strategy = MorrisScreenStrategy(RecipeSchema(), seed=34, num_trajectories=1)
    candidates = _all_candidates(strategy)

    with pytest.raises(ValueError, match="objective metrics"):
        strategy.tell(
            [
                (
                    candidate,
                    _feasible_result(
                        candidate,
                        float(index),
                        metric="even" if index % 2 == 0 else "odd",
                    ),
                )
                for index, candidate in enumerate(candidates)
            ]
        )

    assert strategy.tell_count == 0
    assert strategy.results == ()


@pytest.mark.parametrize("bad_value", [object(), RuntimeError("boom")])
def test_morris_tell_is_atomic_when_objective_mapping_invalid(bad_value: object) -> None:
    strategy = MorrisScreenStrategy(RecipeSchema(), seed=35, num_trajectories=1)
    candidates = _all_candidates(strategy)
    bad_result = ScoredResult(
        candidate_id=candidates[0].id,
        eval_spec=None,
        cache_key=None,
        feasible=True,
        objectives=_BadObjectives(bad_value),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError):
        strategy.tell(
            [(candidates[0], bad_result)]
            + [
                (candidate, _feasible_result(candidate, float(index)))
                for index, candidate in enumerate(candidates[1:], start=1)
            ]
        )

    assert strategy.tell_count == 0
    assert strategy.results == ()


def test_morris_clean_import_does_not_load_evaluate_or_pool() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    code = """
import importlib
import sys

importlib.import_module("simulator.optimize.strategy.screen")
forbidden = {
    "simulator.optimize.evaluate",
    "simulator.optimize.pool",
}
loaded = sorted(name for name in forbidden if name in sys.modules)
if loaded:
    raise SystemExit(f"forbidden imports loaded: {loaded}")
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_morris_ask_edge_cases_and_exhaustion() -> None:
    strategy = MorrisScreenStrategy(RecipeSchema(), seed=37, num_trajectories=1)

    assert strategy.ask(0) == []
    for bad_n in (-1, True, "x"):
        with pytest.raises(ValueError, match="non-negative int"):
            strategy.ask(bad_n)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="even int"):
        MorrisScreenStrategy(RecipeSchema(), seed=37, num_levels=3)

    candidates = strategy.ask(strategy.plan_length + 5)

    assert len(candidates) == strategy.plan_length
    assert strategy.ask(1) == []
