from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
import subprocess
import sys
from typing import Any, Mapping

import pytest

from simulator.optimize import study
from simulator.optimize.evalspec import EvalSpec, PrefixEvalSpec, cache_key
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult, _build_eval_inputs
from simulator.optimize.objective import (
    ObjectiveValue,
    ObjectiveVector,
    objective_definitions,
    pareto_front,
)
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.optimize.results_store import ResultStore
from simulator.optimize.strategy.staged import (
    StagedAllowlistError,
    TopologyChoice,
    enumerate_topologies,
)
from simulator.optimize.strategy import (
    Candidate,
    StagedBeamStateError,
    StagedDuplicateCacheKey,
    StagedReplayViolation,
    StagedStrategy,
    StagedStrategyError,
    assert_prefix_replay_equal,
    make_prefix_eval_spec,
)

FEEDSTOCK = "lunar_mare_low_ti"
PROFILE = {
    "profile_id": "staged-test",
    "profile_schema_version": "profile-schema-v1",
    "feedstock": FEEDSTOCK,
    "objectives": [
        {"metric": "oxygen_kg", "sense": "maximize", "units": "kg", "weight": 0.6},
        {"metric": "energy_kWh", "sense": "minimize", "units": "kWh", "weight": 0.4},
    ],
    "constraints": {"gates": ["delivered_stream_purity"]},
    "run": {"campaign": "C0", "hours": 1, "mass_kg": 1000.0, "backend_name": "stub"},
    "fidelities": {"stub": {"backend_name": "stub", "hours": 1}},
    "seed_recipes": [
        {
            "id": "staged-c0-seed",
            "source_campaign": "C0",
            "patch": {"campaigns": {"C0": {"temp_range_C": [900, 950]}}},
        }
    ],
    "staged": {
        "beam_width": 1,
        "children_per_parent": 2,
        "allowlist": ("C0", "C0b_p_cleanup"),
    },
}
SCHEMA = RecipeSchema()


class SpyStore(ResultStore):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lookup_specs: list[EvalSpec] = []
        self.prefix_hits = 0

    def lookup(self, eval_spec: EvalSpec) -> ScoredResult | None:
        self.lookup_specs.append(eval_spec)
        result = super().lookup(eval_spec)
        if isinstance(eval_spec, PrefixEvalSpec) and result is not None:
            self.prefix_hits += 1
        return result


class SpyEvaluator:
    def __init__(self) -> None:
        self.prefix_calls = 0
        self.prefix_patch: RecipePatch | None = None

    def __call__(
        self,
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        staged_replay: study.StagedReplay | None = None,
        **_: Any,
    ) -> ScoredResult:
        if candidate_id is not None and candidate_id.startswith("staged-prefix-"):
            self.prefix_calls += 1
            self.prefix_patch = patch
            assert staged_replay is None
        elif "-01-" in str(candidate_id):
            assert staged_replay is not None
            assert patch == staged_replay.stage_patch
        return _scored(patch, feedstock, fidelity, profile, candidate_id=candidate_id)


def _threshold() -> ThresholdSpec:
    return ThresholdSpec(
        id="delivered-stream-purity",
        value=1.0,
        units="fraction",
        source="test",
        source_ref="test",
    )


def _margin() -> GateMargin:
    return GateMargin(
        gate="delivered_stream_purity",
        feasible=True,
        margin=1.0,
        threshold=_threshold(),
        observed=1.0,
        detail="ok",
    )


def _objectives(oxygen: float = 10.0, energy: float = 2.0) -> ObjectiveVector:
    return ObjectiveVector(
        (
            ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
            ObjectiveValue("energy_kWh", "minimize", energy, "kWh", ordinal=1),
        )
    )


def _spec(
    patch: RecipePatch,
    feedstock: str = FEEDSTOCK,
    fidelity: str = "stub",
    profile: Mapping[str, Any] = PROFILE,
) -> EvalSpec:
    spec, _ = _build_eval_inputs(patch.validated(SCHEMA), feedstock, fidelity, profile, SCHEMA)
    return spec


def _scored(
    patch: RecipePatch,
    feedstock: str = FEEDSTOCK,
    fidelity: str = "stub",
    profile: Mapping[str, Any] = PROFILE,
    *,
    candidate_id: str | None = "candidate",
    cache_key_value: str | None = None,
    eval_spec: EvalSpec | None = None,
    trace: Any = None,
    oxygen: float | None = None,
    energy: float = 2.0,
    margin: GateMargin | None = None,
) -> ScoredResult:
    spec = eval_spec or _spec(patch, feedstock, fidelity, profile)
    key = cache_key_value or cache_key(spec)
    oxygen_value = (
        100.0 - float(str(candidate_id or "").count("1"))
        if oxygen is None
        else oxygen
    )
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=key,
        feasible=True,
        objectives=_objectives(oxygen=oxygen_value, energy=energy),
        feasibility_margins={"delivered_stream_purity": margin or _margin()},
        run_reference=RunReference(
            status="ok",
            trace={"heavy": "trace"} if trace is None else trace,
            product_summary={"oxygen_kg": oxygen_value},
        ),
    )


def _record_signature(record: study.StudyRecord) -> tuple[Any, ...]:
    return (
        record.candidate_id,
        record.cache_key,
        record.feasible,
        record.status,
        tuple(sorted(record.objectives.items())),
        tuple(sorted((key, tuple(sorted(value.items()))) for key, value in record.feasibility_margins.items())),
    )


def test_staged_prefix_replay_hits_cache_and_matches_fresh_prefix(tmp_path) -> None:
    store = SpyStore(tmp_path / "cache.sqlite")
    evaluator = SpyEvaluator()

    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=1,
        budget=4,
        out_dir=tmp_path,
        seed=7,
        evaluator=evaluator,
        result_store=store,
    )

    prefix_specs = [spec for spec in store.lookup_specs if isinstance(spec, PrefixEvalSpec)]
    assert any(spec.prefix_stage_ids == ("C0",) for spec in prefix_specs)
    assert evaluator.prefix_calls == 1
    assert store.prefix_hits >= 1
    assert any("-01-" in record.candidate_id for record in result.records)

    prefix_spec = next(spec for spec in prefix_specs if spec.prefix_stage_ids == ("C0",))
    cached = store.lookup(prefix_spec)
    assert cached is not None
    assert evaluator.prefix_patch is not None
    fresh = evaluator(
        evaluator.prefix_patch,
        FEEDSTOCK,
        "stub",
        profile=PROFILE,
        candidate_id=cached.candidate_id,
    )
    fresh = replace(fresh, eval_spec=prefix_spec, cache_key=cache_key(prefix_spec))
    fresh = replace(
        fresh,
        run_reference=RunReference(
            status=fresh.run_reference.status,
            error_message=fresh.run_reference.error_message,
            reason=fresh.run_reference.reason,
            trace=None,
            product_summary=fresh.run_reference.product_summary,
        ),
    )
    assert_prefix_replay_equal(cached, fresh)


def test_staged_runtime_rejects_tampered_prefix_cache(tmp_path) -> None:
    class TamperingStore(SpyStore):
        def store(
            self,
            eval_spec: EvalSpec,
            scored: ScoredResult,
            *,
            created_at: str,
        ) -> None:
            if isinstance(eval_spec, PrefixEvalSpec):
                scored = replace(scored, notes=(*scored.notes, "tampered-prefix-cache"))
            super().store(eval_spec, scored, created_at=created_at)

    with pytest.raises(StagedReplayViolation):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "staged",
            "stub",
            parallel=1,
            budget=4,
            out_dir=tmp_path,
            seed=11,
            evaluator=SpyEvaluator(),
            result_store=TamperingStore(tmp_path / "cache.sqlite"),
        )


def test_staged_default_evaluator_fails_loud_without_replay(tmp_path) -> None:
    def no_replay_evaluator(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        **_: Any,
    ) -> ScoredResult:
        return _scored(patch, feedstock, fidelity, profile, candidate_id=candidate_id)

    with pytest.raises(StagedReplayViolation, match="explicit staged replay"):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "staged",
            "stub",
            parallel=1,
            budget=4,
            out_dir=tmp_path,
            seed=12,
            evaluator=no_replay_evaluator,
        )


def test_base_evalspec_and_prefix_evalspec_keys_do_not_collide(tmp_path) -> None:
    profile = {
        **PROFILE,
        "run": {
            **PROFILE["run"],
            "c5_enabled": True,
            "mre_max_voltage_V": 1.4,
            "mre_target_species": "SiO2",
        },
    }
    base = _spec(RecipePatch({}), profile=profile)
    prefix = make_prefix_eval_spec(
        base,
        prefix_stage_ids=("C0",),
        prefix_recipe_ids=(base.recipe_id,),
        topology_id="PATH_AB",
    )
    assert cache_key(base) != cache_key(prefix)

    store = ResultStore(tmp_path / "cache.sqlite")
    store.store(base, _scored(RecipePatch({}), eval_spec=base), created_at="t1")
    store.store(
        prefix,
        _scored(RecipePatch({}), eval_spec=prefix, candidate_id="prefix"),
        created_at="t2",
    )

    loaded_base = store.lookup(base)
    loaded_prefix = store.lookup(prefix)
    assert loaded_base.cache_key == cache_key(base)
    assert loaded_prefix.cache_key == cache_key(prefix)
    assert isinstance(loaded_prefix.eval_spec, PrefixEvalSpec)
    assert loaded_prefix.eval_spec.prefix_stage_ids == ("C0",)
    assert loaded_prefix.eval_spec.prefix_recipe_ids == (base.recipe_id,)
    assert loaded_prefix.eval_spec.topology_id == "PATH_AB"
    assert loaded_prefix.eval_spec.c5_enabled is True
    assert loaded_prefix.eval_spec.mre_max_voltage_V == pytest.approx(1.4)
    assert loaded_prefix.eval_spec.mre_target_species == "SiO2"


def test_staged_clean_import_boundary_then_touch_loads_evaluate_only() -> None:
    code = """
import importlib
import sys

importlib.import_module("simulator.optimize")
importlib.import_module("simulator.optimize.strategy")
forbidden = {"simulator.optimize.evaluate", "simulator.optimize.pool"}
loaded = sorted(name for name in forbidden if name in sys.modules)
if loaded:
    raise SystemExit(f"forbidden imports loaded before staged touch: {loaded}")
from simulator.optimize.strategy import StagedStrategy
_ = StagedStrategy
if "simulator.optimize.evaluate" not in sys.modules:
    raise SystemExit("evaluate not loaded by staged touch")
if "simulator.optimize.pool" in sys.modules:
    raise SystemExit("pool loaded by staged touch")
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_staged_run_is_deterministic_for_same_seed_feedstock_fidelity(tmp_path) -> None:
    first = study.run(
        PROFILE,
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=1,
        budget=4,
        out_dir=tmp_path / "a",
        seed=13,
        evaluator=SpyEvaluator(),
    )
    second = study.run(
        PROFILE,
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=1,
        budget=4,
        out_dir=tmp_path / "b",
        seed=13,
        evaluator=SpyEvaluator(),
    )

    assert [record.cache_key for record in first.records] == [
        record.cache_key for record in second.records
    ]
    assert [_record_signature(record) for record in first.records] == [
        _record_signature(record) for record in second.records
    ]
    assert [_record_signature(record) for record in first.pareto] == [
        _record_signature(record) for record in second.pareto
    ]
    assert _record_signature(first.winner) == _record_signature(second.winner)


def test_c5_and_c6_topology_changes_stage_path() -> None:
    profile = {**PROFILE, "staged": {"beam_width": 1, "children_per_parent": 1}}
    c5_no = StagedStrategy(
        SCHEMA,
        seed=3,
        objective_profile=profile,
        topology=TopologyChoice(path_ab="A", branch="two", c5=False, c6=True),
    )
    c5_yes = StagedStrategy(
        SCHEMA,
        seed=3,
        objective_profile=profile,
        topology=TopologyChoice(path_ab="A", branch="two", c5=True, c6=True),
    )
    c6_no = StagedStrategy(
        SCHEMA,
        seed=3,
        objective_profile=profile,
        topology=TopologyChoice(path_ab="A", branch="two", c5=True, c6=False),
    )
    c6_yes = StagedStrategy(
        SCHEMA,
        seed=3,
        objective_profile=profile,
        topology=TopologyChoice(path_ab="A", branch="two", c5=True, c6=True),
    )

    assert len(enumerate_topologies()) == 24
    assert c5_no.stage_ids != c5_yes.stage_ids
    assert "C5" not in c5_no.stage_ids
    assert "C5" in c5_yes.stage_ids
    assert c6_no.stage_ids != c6_yes.stage_ids
    assert "C6" not in c6_no.stage_ids
    assert c6_yes.stage_ids[-1] == "C6"


def test_empty_topologies_raise(tmp_path) -> None:
    with pytest.raises(ValueError, match="at least one topology"):
        enumerate_topologies(())

    with pytest.raises(ValueError, match="at least one topology"):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "staged",
            "stub",
            parallel=1,
            budget=1,
            out_dir=tmp_path,
            seed=13,
            evaluator=SpyEvaluator(),
            topologies=(),
        )


def test_joint_refine_improves_or_refines() -> None:
    profile = {
        **PROFILE,
        "staged": {
            "beam_width": 1,
            "children_per_parent": 2,
            "allowlist": ("C0", "C0b_p_cleanup", "C2A_continuous"),
            "joint_refine": True,
            "max_backward_passes": 0,
        },
    }
    strategy = StagedStrategy(
        SCHEMA,
        seed=23,
        objective_profile=profile,
        topology=TopologyChoice(path_ab="A", branch="two", c6=True),
    )
    while True:
        candidates = strategy.ask(20)
        if not candidates:
            break
        strategy.tell(
            [
                (
                    candidate,
                    _scored(
                        candidate.patch,
                        candidate_id=candidate.id,
                        oxygen=20.0,
                        energy=5.0,
                        margin=replace(
                            _margin(),
                            gate="C2A_continuous_pressure",
                            margin=0.01,
                            detail="near C2A boundary",
                        ),
                    ),
                )
                for candidate in candidates
            ]
        )
    parent_patch = strategy._archive[0].node.patch
    parent_key = strategy._archive[0].scored.cache_key
    inactive_paths = {
        spec.path
        for stage_id, specs in strategy._stages
        if stage_id == "C0"
        for spec in specs
    }
    active_paths = {
        spec.path
        for stage_id, specs in strategy._stages
        if stage_id in {"C0b_p_cleanup", "C2A_continuous"}
        for spec in specs
    }

    assert strategy.joint_refine() is True
    joint_candidates = strategy.ask(10)
    assert joint_candidates
    assert all("-joint-" in candidate.id for candidate in joint_candidates)
    assert all(
        candidate.metadata["topology"]["id"] == strategy.topology.id
        for candidate in joint_candidates
    )
    assert all(candidate.metadata["prefix_depth"] > 0 for candidate in joint_candidates)
    assert any(candidate.patch.values != parent_patch.values for candidate in joint_candidates)
    assert all(
        candidate.patch.values.get(path) == parent_patch.values.get(path)
        for candidate in joint_candidates
        for path in inactive_paths
    )
    assert any(
        candidate.patch.values.get(path) != parent_patch.values.get(path)
        for candidate in joint_candidates
        for path in active_paths
    )

    strategy.tell(
        [
            (
                candidate,
                _scored(
                    candidate.patch,
                    candidate_id=candidate.id,
                    oxygen=200.0 if "c000001" in candidate.id else 50.0,
                    energy=1.0 if "c000001" in candidate.id else 4.0,
                ),
            )
            for candidate in joint_candidates
        ]
    )

    assert strategy.joint_refines_completed <= strategy.max_joint_refines
    assert strategy._archive[0].scored.cache_key != parent_key
    assert "joint-refine" in strategy._archive[0].candidate.metadata["stage_id"]


def test_backward_pass_reorders_pareto_with_improving_backward_candidate(tmp_path) -> None:
    def evaluator(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        staged_replay: study.StagedReplay | None = None,
        **_: Any,
    ) -> ScoredResult:
        oxygen = 20.0
        energy = 5.0
        if candidate_id and "-01-" in candidate_id:
            oxygen = 50.0
            energy = 4.0
        if candidate_id and "backward-" in candidate_id and "c000001" in candidate_id:
            oxygen = 200.0
            energy = 1.0
        scored = _scored(
            patch,
            feedstock,
            fidelity,
            profile,
            candidate_id=candidate_id,
            oxygen=oxygen,
            energy=energy,
        )
        return scored

    forward_profile = {
        **PROFILE,
        "staged": {**PROFILE["staged"], "max_backward_passes": 0},
    }
    backward_profile = {
        **PROFILE,
        "staged": {**PROFILE["staged"], "max_backward_passes": 1},
    }
    forward = study.run(
        forward_profile,
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=4,
        budget=4,
        out_dir=tmp_path / "forward",
        seed=29,
        evaluator=evaluator,
    )
    pre_backward_keys = {record.cache_key for record in forward.pareto}

    strategy = StagedStrategy(SCHEMA, seed=29, objective_profile=backward_profile)
    result = study.run(
        backward_profile,
        FEEDSTOCK,
        strategy,
        "stub",
        parallel=4,
        budget=6,
        out_dir=tmp_path / "backward",
        seed=29,
        evaluator=evaluator,
    )

    improving_record = next(
        record
        for record in result.records
        if "backward-" in record.candidate_id and "c000001" in record.candidate_id
    )
    assert {record.cache_key for record in result.pareto} != pre_backward_keys
    assert result.winner.cache_key == improving_record.cache_key
    assert any(
        any(str(stage_id).startswith("backward-") for stage_id in candidate.metadata["stage_ids"])
        for candidate, _ in strategy.results
    )
    assert strategy.backward_passes_completed <= strategy.max_backward_passes


def test_backward_pass_does_not_evict_nondominated(tmp_path) -> None:
    profile = {
        **PROFILE,
        "staged": {
            **PROFILE["staged"],
            "beam_width": 1,
            "children_per_parent": 2,
            "max_backward_passes": 1,
        },
    }

    def evaluator(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        staged_replay: study.StagedReplay | None = None,
        **_: Any,
    ) -> ScoredResult:
        oxygen = 10.0
        energy = 20.0
        if candidate_id and "-01-" in candidate_id and "c000000" in candidate_id:
            oxygen = 100.0
            energy = 10.0
        elif candidate_id and "-01-" in candidate_id and "c000001" in candidate_id:
            oxygen = 90.0
            energy = 1.0
        elif candidate_id and "backward-" in candidate_id:
            oxygen = 50.0
            energy = 30.0
        return _scored(
            patch,
            feedstock,
            fidelity,
            profile,
            candidate_id=candidate_id,
            oxygen=oxygen,
            energy=energy,
        )

    strategy = StagedStrategy(SCHEMA, seed=31, objective_profile=profile)
    study.run(
        profile,
        FEEDSTOCK,
        strategy,
        "stub",
        parallel=4,
        budget=6,
        out_dir=tmp_path,
        seed=31,
        evaluator=evaluator,
    )

    definitions = objective_definitions(profile)
    forward_final = tuple(
        (candidate, scored)
        for candidate, scored in strategy.results
        if tuple(candidate.metadata["stage_ids"]) == ("C0", "C0b_p_cleanup")
    )
    pre_front = pareto_front(
        forward_final,
        definitions,
        objective_getter=lambda item: item[1].objectives,
    )
    pre_front_keys = {scored.cache_key for _, scored in pre_front}
    frontier_keys = {node.cache_key for node in strategy._frontier}

    assert strategy.backward_passes_completed == 1
    assert len(pre_front_keys) == 2
    assert pre_front_keys <= frontier_keys
    assert not any(
        candidate.id.startswith("staged-31-backward") and scored.cache_key in frontier_keys
        for candidate, scored in strategy.results
    )


def test_backward_pass_is_bounded(tmp_path) -> None:
    profile = {
        **PROFILE,
        "staged": {**PROFILE["staged"], "max_backward_passes": 2},
    }

    def evaluator(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        staged_replay: study.StagedReplay | None = None,
        **_: Any,
    ) -> ScoredResult:
        oxygen = 25.0
        energy = 5.0
        if candidate_id and "backward-00" in candidate_id and "c000001" in candidate_id:
            oxygen = 100.0
            energy = 3.0
        elif candidate_id and "backward-01" in candidate_id and "c000001" in candidate_id:
            oxygen = 200.0
            energy = 1.0
        return _scored(
            patch,
            feedstock,
            fidelity,
            profile,
            candidate_id=candidate_id,
            oxygen=oxygen,
            energy=energy,
        )

    strategy = StagedStrategy(SCHEMA, seed=37, objective_profile=profile)
    study.run(
        profile,
        FEEDSTOCK,
        strategy,
        "stub",
        parallel=4,
        budget=12,
        out_dir=tmp_path,
        seed=37,
        evaluator=evaluator,
    )

    assert strategy.backward_passes_completed == 2
    assert strategy.backward_passes_completed <= strategy.max_backward_passes
    assert strategy.ask(1) == []


def test_staged_single_stage_terminates_and_empty_stage_fails_loud() -> None:
    c0_schema = RecipeSchema(
        allowlist=tuple(spec for spec in RecipeSchema.ALLOWLIST if spec.path[:2] == ("campaigns", "C0"))
    )
    strategy = StagedStrategy(
        c0_schema,
        seed=3,
        objective_profile={**PROFILE, "staged": {"beam_width": 1, "children_per_parent": 1}},
    )

    candidates = strategy.ask(5)
    assert len(candidates) == 1
    strategy.tell([(candidates[0], _scored(candidates[0].patch, candidate_id=candidates[0].id))])
    assert strategy.ask(1) == []

    with pytest.raises(StagedBeamStateError):
        StagedStrategy(RecipeSchema(allowlist=()), seed=3, objective_profile=PROFILE)


def test_staged_allowlist_unknown_stage_raises_named_error() -> None:
    profile = {
        **PROFILE,
        "staged": {**PROFILE["staged"], "allowlist": ("C0", "NOPE")},
    }

    with pytest.raises(StagedAllowlistError, match="unknown staged allowlist stage: NOPE"):
        StagedStrategy(RecipeSchema(), seed=3, objective_profile=profile)


def test_topology_mapping_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown topology key: 'brnach'"):
        enumerate_topologies(({"brnach": "one"},))


def test_staged_beam_ranker_raises_on_duplicate_cache_key() -> None:
    c0_schema = RecipeSchema(
        allowlist=tuple(spec for spec in RecipeSchema.ALLOWLIST if spec.path[:2] == ("campaigns", "C0"))
    )
    strategy = StagedStrategy(
        c0_schema,
        seed=5,
        objective_profile={**PROFILE, "staged": {"beam_width": 1, "children_per_parent": 2}},
    )
    candidates = strategy.ask(2)

    with pytest.raises(StagedDuplicateCacheKey):
        strategy.tell(
            [
                (candidate, _scored(candidate.patch, candidate_id=candidate.id, cache_key_value="dup"))
                for candidate in candidates
            ]
        )


def test_child_cache_key_duplicate_parent_raises() -> None:
    strategy = StagedStrategy(
        SCHEMA,
        seed=41,
        objective_profile={**PROFILE, "staged": {**PROFILE["staged"], "beam_width": 1}},
    )
    first_stage = strategy.ask(2)
    strategy.tell(
        [
            (candidate, _scored(candidate.patch, candidate_id=candidate.id))
            for candidate in first_stage
        ]
    )
    child = strategy.ask(1)[0]
    parent_key = child.metadata["parent_cache_key"]

    with pytest.raises(StagedDuplicateCacheKey):
        strategy.tell(
            [
                (
                    child,
                    _scored(
                        child.patch,
                        candidate_id=child.id,
                        cache_key_value=parent_key,
                    ),
                )
            ]
        )


def test_staged_rerun_hits_cache_without_duplicating_rows(tmp_path) -> None:
    store = SpyStore(tmp_path / "cache.sqlite")

    first = study.run(
        PROFILE,
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=1,
        budget=4,
        out_dir=tmp_path / "first",
        seed=43,
        evaluator=SpyEvaluator(),
        result_store=store,
    )
    first_rows = _store_row_count(store)
    first_prefix_hits = store.prefix_hits
    _delete_non_prefix_rows(store)

    second_evaluator = SpyEvaluator()
    second = study.run(
        PROFILE,
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=1,
        budget=4,
        out_dir=tmp_path / "second",
        seed=43,
        evaluator=second_evaluator,
        result_store=store,
    )

    assert _store_row_count(store) == first_rows
    assert store.prefix_hits > first_prefix_hits
    assert second_evaluator.prefix_calls == 0
    assert [record.cache_key for record in second.records] == [
        record.cache_key for record in first.records
    ]


def test_all_infeasible_beam_bounded(tmp_path) -> None:
    def infeasible_evaluator(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        staged_replay: study.StagedReplay | None = None,
        **_: Any,
    ) -> ScoredResult:
        return replace(
            _scored(patch, feedstock, fidelity, profile, candidate_id=candidate_id),
            feasible=False,
            objectives=None,
            feasibility_margins={
                "delivered_stream_purity": replace(
                    _margin(),
                    feasible=False,
                    margin=-0.1,
                    observed=0.9,
                    detail="finite miss",
                )
            },
        )

    out = tmp_path / "all-infeasible"
    with pytest.raises(study.StudyNoFeasibleError):
        study.run(
            {**PROFILE, "staged": {**PROFILE["staged"], "max_backward_passes": 1}},
            FEEDSTOCK,
            "staged",
            "stub",
            parallel=4,
            budget=8,
            out_dir=out,
            seed=47,
            evaluator=infeasible_evaluator,
        )

    assert json.loads((out / "pareto.json").read_text())["pareto"] == []
    assert (out / "leaderboard.csv").read_text().strip() == "rank,candidate_id,cache_key,is_pareto,is_winner,oxygen_kg,energy_kWh,patch_json"
    assert not (out / "winner.recipe.yaml").exists()


def test_beam_width_1_vs_k(tmp_path) -> None:
    def scorer(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        staged_replay: study.StagedReplay | None = None,
        **_: Any,
    ) -> ScoredResult:
        text = str(candidate_id)
        oxygen = 100.0
        energy = 2.0
        if "-00-C0-" in text:
            oxygen = 100.0 if "c000000" in text else 90.0
            energy = 1.0 if "c000000" in text else 2.0
        elif "-01-C0b_p_cleanup-" in text:
            if "-p001-" in text:
                oxygen = 300.0
                energy = 0.5
            else:
                oxygen = 110.0
                energy = 1.5
        return _scored(
            patch,
            feedstock,
            fidelity,
            profile,
            candidate_id=candidate_id,
            oxygen=oxygen,
            energy=energy,
        )

    base_staged = {
        **PROFILE["staged"],
        "allowlist": ("C0", "C0b_p_cleanup"),
        "children_per_parent": 2,
        "max_backward_passes": 0,
        "topology": {"path_ab": "A", "branch": "two", "c5": "yes", "c6": "yes"},
    }
    width_one = study.run(
        {**PROFILE, "staged": {**base_staged, "beam_width": 1}},
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=6,
        budget=4,
        out_dir=tmp_path / "width-one",
        seed=53,
        evaluator=scorer,
    )
    width_k = study.run(
        {**PROFILE, "staged": {**base_staged, "beam_width": 2}},
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=12,
        budget=6,
        out_dir=tmp_path / "width-k",
        seed=53,
        evaluator=scorer,
    )

    assert width_one.winner.cache_key != width_k.winner.cache_key
    assert "-p001-" not in width_one.winner.candidate_id
    assert "-p001-" in width_k.winner.candidate_id


def test_one_topology_vs_all_topologies_study(tmp_path) -> None:
    topologies = enumerate_topologies()
    store = SpyStore(tmp_path / "topologies.sqlite")
    one = study.run(
        PROFILE,
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=2,
        budget=2,
        out_dir=tmp_path / "one",
        seed=59,
        evaluator=SpyEvaluator(),
        result_store=store,
        topologies=topologies[:1],
    )
    all_result = study.run(
        PROFILE,
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=len(topologies),
        budget=len(topologies),
        out_dir=tmp_path / "all",
        seed=59,
        evaluator=SpyEvaluator(),
        result_store=store,
        topologies=topologies,
    )

    assert {_topology_id_from_candidate_id(record.candidate_id) for record in one.records} == {
        topologies[0].id
    }
    assert {_topology_id_from_candidate_id(record.candidate_id) for record in all_result.records} == {
        topology.id for topology in topologies
    }
    assert len({record.candidate_id for record in all_result.records}) == len(all_result.records)
    assert _duplicate_store_cache_keys(store) == []


def test_staged_strategy_has_no_op5b3_not_implemented_raises() -> None:
    import simulator.optimize.strategy.staged as staged_module

    source = staged_module.__loader__.get_source(staged_module.__name__)
    assert source is not None
    assert "not implemented until O-P5b3" not in source


def _store_row_count(store: ResultStore) -> int:
    with sqlite3.connect(store.path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM results").fetchone()[0])


def _delete_non_prefix_rows(store: ResultStore) -> None:
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute("SELECT cache_key, eval_spec FROM results").fetchall()
        full_keys = [
            row[0]
            for row in rows
            if json.loads(str(row[1])).get("eval_spec_type") != "prefix"
        ]
        conn.executemany("DELETE FROM results WHERE cache_key = ?", [(key,) for key in full_keys])


def _duplicate_store_cache_keys(store: ResultStore) -> list[str]:
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute(
            "SELECT cache_key FROM results GROUP BY cache_key HAVING COUNT(*) > 1"
        ).fetchall()
    return [str(row[0]) for row in rows]


def _topology_id_from_candidate_id(candidate_id: str) -> str:
    return candidate_id.split("-")[2]


def test_staged_out_of_scope_contracts_fail_loud() -> None:
    with pytest.raises(ValueError, match="enable_c6_topology is obsolete"):
        StagedStrategy(
            SCHEMA,
            seed=9,
            objective_profile={**PROFILE, "staged": {"enable_c6_topology": True}},
        )
    with pytest.raises(ValueError, match="enable_c6_topology is obsolete"):
        StagedStrategy(
            SCHEMA,
            seed=9,
            objective_profile={
                **PROFILE,
                "staged": {
                    "enable_c6_topology": True,
                    "topology": {"path_ab": "A", "branch": "two", "c5": "yes", "c6": "yes"},
                },
            },
        )

    strategy = StagedStrategy(
        SCHEMA,
        seed=9,
        objective_profile={**PROFILE, "staged": {"max_backward_passes": 1}},
    )
    assert strategy.run_backward_pass() is False
    assert strategy.joint_refine() is False
    c6_no, c6_yes = strategy.enumerate_c6_topology()
    assert c6_no.c5 == strategy.topology.c5
    assert c6_yes.c5 == strategy.topology.c5
    assert c6_no.c6 is False
    assert c6_yes.c6 is True


def test_staged_result_honesty_and_light_results(tmp_path) -> None:
    def engine_bug_evaluator(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        staged_replay: study.StagedReplay | None = None,
        **_: Any,
    ) -> ScoredResult:
        spec = _spec(patch, feedstock, fidelity, profile)
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key=cache_key(spec),
            feasible=False,
            failure_category=FailureCategory.ENGINE_BUG,
            feasibility_margins={"delivered_stream_purity": _margin()},
        )

    with pytest.raises(study.StudyAbort):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "staged",
            "stub",
            parallel=1,
            budget=1,
            out_dir=tmp_path,
            seed=17,
            evaluator=engine_bug_evaluator,
        )

    strategy = StagedStrategy(
        RecipeSchema(
            allowlist=tuple(spec for spec in RecipeSchema.ALLOWLIST if spec.path[:2] == ("campaigns", "C0"))
        ),
        seed=19,
        objective_profile={**PROFILE, "staged": {"beam_width": 1, "children_per_parent": 1}},
    )
    candidate = strategy.ask(1)[0]
    strategy.tell([(candidate, _scored(candidate.patch, candidate_id=candidate.id, trace={"large": "trace"}))])
    assert strategy.results[0][1].run_reference.trace is None
