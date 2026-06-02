from __future__ import annotations

from dataclasses import replace
import subprocess
import sys
from typing import Any, Mapping

import pytest

from simulator.optimize import study
from simulator.optimize.evalspec import EvalSpec, PrefixEvalSpec, cache_key
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult, _build_eval_inputs
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.optimize.results_store import ResultStore
from simulator.optimize.strategy import (
    Candidate,
    StagedBeamStateError,
    StagedDuplicateCacheKey,
    StagedStrategy,
    StagedStrategyError,
    assert_prefix_replay_equal,
    make_prefix_eval_spec,
)

FEEDSTOCK = "lunar_mare_low_ti"
PROFILE = {
    "profile_id": "staged-test",
    "profile_schema_version": "profile-schema-v1",
    "objectives": [
        {"metric": "oxygen_kg", "sense": "maximize", "units": "kg"},
        {"metric": "energy_kWh", "sense": "minimize", "units": "kWh"},
    ],
    "run": {"campaign": "C0", "hours": 1, "mass_kg": 1000.0, "backend_name": "stub"},
    "fidelities": {"stub": {"backend_name": "stub", "hours": 1}},
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
        staged_prefix_result: ScoredResult | None = None,
        staged_stage_patch: RecipePatch | None = None,
        **_: Any,
    ) -> ScoredResult:
        if candidate_id is not None and candidate_id.startswith("staged-prefix-"):
            self.prefix_calls += 1
            self.prefix_patch = patch
            assert staged_prefix_result is None
            assert staged_stage_patch is None
        elif "-01-" in str(candidate_id):
            assert staged_prefix_result is not None
            assert staged_stage_patch is not None
            assert patch == staged_stage_patch
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
) -> ScoredResult:
    spec = eval_spec or _spec(patch, feedstock, fidelity, profile)
    key = cache_key_value or cache_key(spec)
    oxygen = 100.0 - float(str(candidate_id or "").count("1"))
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=key,
        feasible=True,
        objectives=_objectives(oxygen=oxygen, energy=2.0),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            trace={"heavy": "trace"} if trace is None else trace,
            product_summary={"oxygen_kg": oxygen},
        ),
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
    assert any(record.candidate_id.startswith("staged-7-01-") for record in result.records)

    prefix_spec = next(spec for spec in prefix_specs if spec.prefix_stage_ids == ("C0",))
    cached = store.lookup(prefix_spec)
    assert cached is not None
    assert evaluator.prefix_patch is not None
    fresh = evaluator(
        evaluator.prefix_patch,
        FEEDSTOCK,
        "stub",
        profile=PROFILE,
        candidate_id="staged-prefix-independent",
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


def test_base_evalspec_and_prefix_evalspec_keys_do_not_collide(tmp_path) -> None:
    base = _spec(RecipePatch({}))
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

    assert store.lookup(base).cache_key == cache_key(base)
    assert store.lookup(prefix).cache_key == cache_key(prefix)


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


def test_staged_out_of_scope_contracts_fail_loud() -> None:
    for options in (
        {"backward_passes": 1},
        {"joint_refine": True},
        {"topology": "C6"},
    ):
        with pytest.raises(StagedStrategyError):
            StagedStrategy(SCHEMA, seed=9, objective_profile={**PROFILE, "staged": options})

    strategy = StagedStrategy(SCHEMA, seed=9, objective_profile=PROFILE)
    with pytest.raises(StagedStrategyError):
        strategy.run_backward_pass()
    with pytest.raises(StagedStrategyError):
        strategy.joint_refine()
    with pytest.raises(StagedStrategyError):
        strategy.enumerate_c6_topology()


def test_staged_result_honesty_and_light_results(tmp_path) -> None:
    def engine_bug_evaluator(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
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
