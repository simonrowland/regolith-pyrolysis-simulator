from __future__ import annotations

import ast
from dataclasses import fields, replace
import inspect
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any, Mapping

import pytest

from simulator.optimize import doe as doe_module
from simulator.optimize import study
from simulator.optimize import evalspec as evalspec_module
from simulator.optimize.evalspec import EvalSpec, PrefixEvalSpec, cache_key
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult, _build_eval_inputs
from simulator.optimize.objective import (
    ObjectiveValue,
    ObjectiveVector,
    objective_definitions,
    pareto_front,
)
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.recipe import (
    C2A_STAGED_ORDER_PATH,
    KnobSpec,
    RecipePatch,
    RecipeSchema,
)
from simulator.optimize.results_store import ResultStore
from simulator.optimize.strategy import staged as staged_module
from simulator.optimize.strategy.staged import (
    StagedAllowlistError,
    TopologyChoice,
    enumerate_topologies,
    _patch_for_topology,
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
        {
            "metric": "oxygen_kg",
            "sense": "maximize",
            "units": "kg",
            "weight": 0.6,
            "rationale": "test oxygen objective evidence",
        },
        {
            "metric": "energy_kWh",
            "sense": "minimize",
            "units": "kWh",
            "weight": 0.4,
            "rationale": "test energy objective evidence",
        },
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
    def __init__(self, prefix_log: str | None = None) -> None:
        self.prefix_calls = 0
        self.prefix_patch: RecipePatch | None = None
        self.prefix_log = prefix_log

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
            if self.prefix_log is not None:
                Path(self.prefix_log).write_text(
                    json.dumps(
                        [[list(path), value] for path, value in patch.values.items()]
                    ),
                    encoding="utf-8",
                )
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


def _closed_mass_closure() -> dict[str, object]:
    return {"status": "closed", "mass_balance_error_pct": 0.0}


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
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "snapshots": [{"mass_balance_error_pct": 0.0}],
                "heavy": "trace",
            }
            if trace is None
            else trace,
            product_summary={
                "oxygen_kg": oxygen_value,
                "mass_closure": _closed_mass_closure(),
            },
            backend_status="ok",
            backend_authoritative=True,
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
    prefix_log = tmp_path / "prefix-patch.json"
    evaluator = SpyEvaluator(str(prefix_log))

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
    assert prefix_log.exists()
    assert store.prefix_hits >= 1
    assert any("-01-" in record.candidate_id for record in result.records)

    prefix_spec = next(spec for spec in prefix_specs if spec.prefix_stage_ids == ("C0",))
    cached = store.lookup(prefix_spec)
    assert cached is not None
    prefix_patch = RecipePatch({tuple(path): value for path, value in json.loads(prefix_log.read_text(encoding="utf-8"))})
    fresh = evaluator(
        prefix_patch,
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
                trace={"backend_status": "ok", "backend_authoritative": True},
                product_summary=fresh.run_reference.product_summary,
                backend_status="ok",
                backend_authoritative=True,
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


def test_make_prefix_eval_spec_handles_all_canonical_identity_fields() -> None:
    def spec_attribute_names(function: Any) -> set[str]:
        tree = ast.parse(inspect.getsource(function))
        return {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "spec"
        }

    canonical_identity_fields = spec_attribute_names(
        evalspec_module.canonical_evalspec_json
    ) | spec_attribute_names(evalspec_module.lab_overlay_scope_payload)
    handled_fields = staged_module._PREFIX_EVAL_SPEC_HANDLED_FIELD_NAMES

    assert set(staged_module._PREFIX_EVAL_SPEC_BASE_FIELD_NAMES) == {
        field.name for field in fields(EvalSpec)
    }
    assert canonical_identity_fields <= handled_fields


def test_base_evalspec_and_prefix_evalspec_keys_do_not_collide(tmp_path) -> None:
    profile = {
        **PROFILE,
        "run": {
            **PROFILE["run"],
            "c5_enabled": True,
            "mre_max_voltage_V": 1.45,
            "mre_target_species": "SiO2",
        },
    }
    base = _spec(RecipePatch({}), profile=profile)
    base = replace(
        base,
        vapor_pressure_provider_id="builtin-vapor-pressure",
        vapor_pressure_fallback_provider_id="builtin-vapor-pressure",
        allow_fallback_vapor=True,
        force_builtin_vapor_pressure=True,
        vapor_pressure_provider_code_fingerprint="provider-source-sha256:test",
    )
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
    assert loaded_prefix.eval_spec.mre_max_voltage_V == pytest.approx(1.45)
    assert loaded_prefix.eval_spec.mre_target_species == "SiO2"
    assert loaded_prefix.eval_spec.vapor_pressure_provider_id == "builtin-vapor-pressure"
    assert loaded_prefix.eval_spec.vapor_pressure_fallback_provider_id == (
        "builtin-vapor-pressure"
    )
    assert loaded_prefix.eval_spec.allow_fallback_vapor is True
    assert loaded_prefix.eval_spec.force_builtin_vapor_pressure is True
    assert loaded_prefix.eval_spec.vapor_pressure_provider_code_fingerprint == (
        "provider-source-sha256:test"
    )


def test_prefix_evalspec_copies_schema_identity_and_splits_bounds_digest() -> None:
    base = _spec(RecipePatch({}))

    def prefix_for(spec: EvalSpec) -> PrefixEvalSpec:
        return make_prefix_eval_spec(
            spec,
            prefix_stage_ids=("C0",),
            prefix_recipe_ids=(spec.recipe_id,),
            topology_id="PATH_AB",
        )

    old_base = replace(
        base,
        allowlist_version="allowlist-custom",
        bounds_digest="bounds-digest-old",
    )
    new_base = replace(old_base, bounds_digest="bounds-digest-new")
    bubbler_base = replace(
        old_base,
        o2_bubbler_settings={"C3": {"o2_bubbler_kg_per_hr": 0.125}},
    )
    stage0_exit_base = replace(old_base, stop_at_stage0_exit=True)

    old_prefix = prefix_for(old_base)
    new_prefix = prefix_for(new_base)
    bubbler_prefix = prefix_for(bubbler_base)
    stage0_exit_prefix = prefix_for(stage0_exit_base)

    assert old_base.recipe_id == new_base.recipe_id
    assert old_prefix.allowlist_version == "allowlist-custom"
    assert old_prefix.bounds_digest == "bounds-digest-old"
    assert new_prefix.allowlist_version == old_prefix.allowlist_version
    assert new_prefix.bounds_digest == "bounds-digest-new"
    assert bubbler_prefix.o2_bubbler_settings == {
        "C3": {"o2_bubbler_kg_per_hr": 0.125}
    }
    assert stage0_exit_prefix.stop_at_stage0_exit is True
    assert old_prefix.recipe_id == new_prefix.recipe_id
    assert old_prefix.prefix_recipe_ids == new_prefix.prefix_recipe_ids
    assert cache_key(old_prefix) != cache_key(new_prefix)
    assert cache_key(old_prefix) != cache_key(bubbler_prefix)
    assert cache_key(old_prefix) != cache_key(stage0_exit_prefix)


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

    assert len(enumerate_topologies()) == 32
    assert c5_no.stage_ids != c5_yes.stage_ids
    assert "C5" not in c5_no.stage_ids
    assert "C5" in c5_yes.stage_ids
    assert c6_no.stage_ids != c6_yes.stage_ids
    assert "C6" not in c6_no.stage_ids
    assert c6_yes.stage_ids[-1] == "C6"


def test_c2a_staged_topology_order_is_candidate_visible() -> None:
    topology = TopologyChoice(
        path_ab="A_staged",
        branch="two",
        c5=False,
        c6=True,
        c2a_staged_order="fe_then_sio",
    )
    profile = {**PROFILE, "staged": {"beam_width": 1, "children_per_parent": 1}}
    strategy = StagedStrategy(
        SCHEMA,
        seed=7,
        objective_profile=profile,
        topology=topology,
    )

    candidate = strategy.ask(1)[0]

    assert "__C2A_ORDER_FE_THEN_SIO" in topology.id
    assert topology.metadata()["c2a_staged_order"] == "fe_then_sio"
    assert candidate.patch.values[C2A_STAGED_ORDER_PATH] == "fe_then_sio"
    assert candidate.metadata["topology"]["c2a_staged_order"] == "fe_then_sio"
    assert "__C2A_ORDER_FE_THEN_SIO" in candidate.id
    with pytest.raises(ValueError, match="requires PATH_A_STAGED"):
        TopologyChoice(path_ab="A", c2a_staged_order="fe_then_sio")


def test_default_c2a_staged_topology_is_cache_neutral() -> None:
    topology = TopologyChoice(path_ab="A_staged", branch="two", c5=False, c6=True)
    topology_patch = _patch_for_topology(topology)
    default_patch = RecipePatch({})

    topology_spec = _spec(topology_patch)
    default_spec = _spec(default_patch)

    assert topology.id == "PATH_A_STAGED__BRANCH_TWO__C5_NO__C6_YES"
    assert "__C2A_ORDER" not in topology.id
    assert C2A_STAGED_ORDER_PATH not in topology_patch.values
    assert topology_patch.recipe_id(SCHEMA) == default_patch.recipe_id(SCHEMA)
    assert topology_spec.recipe_id == default_spec.recipe_id
    assert cache_key(topology_spec) == cache_key(default_spec)


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


def test_duplicate_topologies_raise_at_profile_and_strategy_construction(tmp_path) -> None:
    topology = TopologyChoice(path_ab="A", branch="two", c5=False, c6=True)
    duplicate_topologies = (topology.id, topology.id)
    with pytest.raises(ValueError, match="duplicate staged topology id"):
        enumerate_topologies(duplicate_topologies)

    profile = {**PROFILE, "staged": {"topologies": duplicate_topologies}}
    with pytest.raises(ValueError, match="duplicate staged topology id"):
        study.resolve_profile(profile, expected_feedstock=FEEDSTOCK, schema=SCHEMA)
    with pytest.raises(ValueError, match="duplicate staged topology id"):
        StagedStrategy(SCHEMA, seed=13, objective_profile=profile)
    with pytest.raises(ValueError, match="duplicate staged topology id"):
        study.run(
            profile,
            FEEDSTOCK,
            "staged",
            "stub",
            parallel=2,
            budget=2,
            out_dir=tmp_path,
            seed=13,
            evaluator=SpyEvaluator(),
        )


def test_multi_topology_staged_run_uses_topology_specific_sample_streams(tmp_path) -> None:
    topologies = (
        TopologyChoice(path_ab="A", branch="two", c5=False, c6=False),
        TopologyChoice(path_ab="A", branch="two", c5=False, c6=True),
    )
    result = study.run(
        {**PROFILE, "staged": {"beam_width": 1, "children_per_parent": 1}},
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=2,
        budget=2,
        out_dir=tmp_path,
        seed=17,
        evaluator=SpyEvaluator(),
        topologies=topologies,
    )

    assert {record.candidate_id.split("-")[2] for record in result.records} == {
        topology.id for topology in topologies
    }
    assert len({record.cache_key for record in result.records}) == len(result.records)
    assert result.records[0].patch.recipe_id(SCHEMA) != result.records[1].patch.recipe_id(SCHEMA)


def test_staged_rejects_sobol_sample_index_band_collisions() -> None:
    with pytest.raises(ValueError, match="forward band exceeded"):
        StagedStrategy(
            SCHEMA,
            seed=1,
            objective_profile={
                **PROFILE,
                "staged": {"beam_width": 1_000_001, "children_per_parent": 1},
            },
        )

    with pytest.raises(ValueError, match="backward target band exceeded"):
        StagedStrategy(
            SCHEMA,
            seed=1,
            objective_profile={
                **PROFILE,
                "staged": {
                    "beam_width": 100_000,
                    "children_per_parent": 2,
                    "max_backward_passes": 1,
                },
            },
        )


def _joint_refine_strategy_after_real_tell(
    *,
    trace: Mapping[str, Any] | None = None,
    margin: GateMargin | None = None,
) -> StagedStrategy:
    profile = {
        **PROFILE,
        "staged": {
            "beam_width": 1,
            "children_per_parent": 1,
            "allowlist": ("C0", "C2A_continuous", "C5", "C6"),
            "joint_refine": True,
            "max_backward_passes": 0,
        },
    }
    strategy = StagedStrategy(
        SCHEMA,
        seed=11,
        objective_profile=profile,
        topology=TopologyChoice(path_ab="A", branch="two", c5=True, c6=True),
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
                        profile=profile,
                        candidate_id=candidate.id,
                        trace=trace,
                        margin=margin or replace(_margin(), margin=1.0),
                    ),
                )
                for candidate in candidates
            ]
        )
    assert strategy._archive
    return strategy


def _joint_refine_targets_after_real_tell(
    *,
    trace: Mapping[str, Any] | None = None,
    margin: GateMargin | None = None,
) -> tuple[int, ...]:
    strategy = _joint_refine_strategy_after_real_tell(trace=trace, margin=margin)
    return staged_module._joint_refine_target_indices(
        strategy._archive,
        strategy._stages,
    )


def test_joint_refine_targets_threshold_straddle_trace_from_real_tell_sc15() -> None:
    trace = {
        "reduced_real_cache": {
            "interpolation_uncertainty_ranked_table_drain": {
                "selected": [
                    {
                        "point_id": "C2A_continuous_solidus_probe",
                        "ranked_table": "threshold_straddle",
                        "uncertainty": {
                            "components": {
                                "threshold_straddle": {
                                    "status": "non_interpolable",
                                    "surfaces": [
                                        {"surface": "solidus_liquid_fraction_proxy"}
                                    ],
                                }
                            }
                        },
                    }
                ]
            }
        }
    }

    strategy = _joint_refine_strategy_after_real_tell(trace=trace)
    targets = staged_module._joint_refine_target_indices(
        strategy._archive,
        strategy._stages,
    )

    member = strategy._archive[0]
    assert member.scored.run_reference.trace == {"backend_status": "ok"}
    assert "C2A_continuous_solidus_probe" in member.joint_refine_trace_signals
    assert targets == (0, 1)
    assert strategy.joint_refine() is True
    joint_candidates = strategy.ask(10)
    assert joint_candidates
    assert {
        candidate.metadata["joint_refine_target_stage_ids"]
        for candidate in joint_candidates
    } == {("C0", "C2A_continuous")}


def test_joint_refine_targets_knob_saturation_trace_from_real_tell_sc50() -> None:
    trace = {
        "knob_saturation": {
            "red_flag": True,
            "knobs": [
                {
                    "key": "campaigns.C2A_continuous.temperature_C",
                    "pinned": "high",
                    "has_opposing_cost": False,
                }
            ],
        }
    }

    strategy = _joint_refine_strategy_after_real_tell(trace=trace)
    targets = staged_module._joint_refine_target_indices(
        strategy._archive,
        strategy._stages,
    )

    assert "campaigns.C2A_continuous.temperature_C" in (
        strategy._archive[0].joint_refine_trace_signals
    )
    assert targets == (0, 1)


def test_joint_refine_targets_indeterminate_gate_verdict_trace_from_real_tell() -> None:
    trace = {
        "interpolation_feasibility_verdict": {
            "verdict": "indeterminate",
            "reason": "gate_margin_inside_interpolation_error",
            "closest_gate": "C2A_continuous_pressure",
        }
    }

    targets = _joint_refine_targets_after_real_tell(trace=trace)

    assert targets == (0, 1)


def test_joint_refine_targets_not_attempted_gate_verdict_margin_from_real_tell() -> None:
    targets = _joint_refine_targets_after_real_tell(
        margin=replace(
            _margin(),
            gate="C2A_continuous_extraction",
            margin=1.0,
            status="not-attempted",
            output_status="not_attempted",
            status_reason="no_volatilization",
        )
    )

    assert targets == (0, 1)


def test_joint_refine_without_w3_signals_from_real_tell_preserves_legacy_behavior() -> None:
    near_margin = replace(
        _margin(),
        gate="C2A_continuous_pressure",
        margin=0.01,
    )

    assert _joint_refine_targets_after_real_tell(margin=near_margin) == (0, 1)
    assert _joint_refine_targets_after_real_tell() == (2, 3)


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


def test_joint_refine_conditions_c2a_staged_pressure_pair_at_boundary() -> None:
    profile = {
        **PROFILE,
        "staged": {
            "beam_width": 1,
            "children_per_parent": 4,
            "allowlist": ("C2A_staged",),
            "joint_refine": True,
            "max_backward_passes": 0,
        },
    }
    strategy = StagedStrategy(
        SCHEMA,
        seed=0,
        objective_profile=profile,
        topology=TopologyChoice(path_ab="A_staged", branch="two", c6=False),
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
                        profile=profile,
                        candidate_id=candidate.id,
                        margin=replace(
                            _margin(),
                            gate="C2A_staged_pressure",
                            margin=0.01,
                            detail="near C2A staged pressure boundary",
                        ),
                    ),
                )
                for candidate in candidates
            ]
        )

    po2_path = tuple("campaigns.C2A_staged.stages.sio_window.pO2_mbar".split("."))
    total_path = tuple(
        "campaigns.C2A_staged.stages.sio_window.p_total_mbar".split(".")
    )
    boundary_patch = RecipePatch(
        {po2_path: math.nextafter(15.0, -math.inf), total_path: 15.0}
    ).validated(SCHEMA)
    member = strategy._archive[0]
    boundary_node = replace(
        member.node,
        patch=boundary_patch,
        recipe_ids=(boundary_patch.recipe_id(SCHEMA),),
    )
    strategy._archive = (replace(member, node=boundary_node),)

    assert strategy.joint_refine() is True
    joint_candidates = strategy.ask(20)

    assert joint_candidates
    for candidate in joint_candidates:
        assert candidate.patch.values[po2_path] < candidate.patch.values[total_path]
        candidate.patch.validated(SCHEMA)


def test_joint_refine_conditions_pinned_c2a_pressure_pair_after_parent_fallback() -> None:
    if not doe_module._scipy_sobol_available():
        pytest.skip("scipy-sobol unavailable")

    schema = RecipeSchema(
        pinned_paths=["C2A_staged.stages.sio_window.p_total_mbar"]
    )
    po2_path = tuple("campaigns.C2A_staged.stages.sio_window.pO2_mbar".split("."))
    total_path = tuple(
        "campaigns.C2A_staged.stages.sio_window.p_total_mbar".split(".")
    )
    mode_path = tuple(
        "campaigns.C2A_staged.stages.sio_window.gas_cover_mode".split(".")
    )
    specs = (schema.spec_for(po2_path),)
    search_paths = {spec.path for spec in schema.search_allowlist}
    parent = RecipePatch(
        {po2_path: 5.0, total_path: 5.0, mode_path: "po2_hold"}
    ).validated(schema)

    assert po2_path in search_paths
    assert total_path not in search_paths
    raw_numeric_patch = staged_module._anchored_numeric_refine_patch(
        parent,
        specs,
        schema=schema,
        seed=41,
        sampler_name=doe_module.SCIPY_SOBOL_SAMPLER,
        round_index=0,
        parent_index=0,
        child_index=0,
        children_per_parent=6,
    )
    assert raw_numeric_patch.values[po2_path] == pytest.approx(6.700232018716632)

    refined_patch = staged_module._refine_patch_near_parent(
        parent,
        specs,
        schema=schema,
        seed=41,
        sampler_name=doe_module.SCIPY_SOBOL_SAMPLER,
        round_index=0,
        parent_index=0,
        child_index=0,
        children_per_parent=6,
    )
    child_patch = RecipePatch({**parent.values, **refined_patch.values})

    assert refined_patch.values[po2_path] <= parent.values[total_path]
    child_patch.validated(schema)


def test_joint_refine_numeric_trust_region_shrinks_and_remaps_near_rail() -> None:
    if not doe_module._scipy_sobol_available():
        pytest.skip("scipy-sobol unavailable")

    path = ("test_float",)
    spec = KnobSpec(path=path, kind="float", low=0.0, high=100.0)
    schema = RecipeSchema(allowlist=(spec,))
    parent = RecipePatch({path: 2.0}).validated(schema)

    def values_for_round(round_index: int) -> tuple[float, ...]:
        return tuple(
            staged_module._anchored_numeric_refine_patch(
                parent,
                (spec,),
                schema=schema,
                seed=31,
                sampler_name=doe_module.SCIPY_SOBOL_SAMPLER,
                round_index=round_index,
                parent_index=0,
                child_index=child_index,
                children_per_parent=8,
            ).values[path]
            for child_index in range(8)
        )

    round0 = values_for_round(0)
    round1 = values_for_round(1)
    round2 = values_for_round(2)
    round3 = values_for_round(3)

    assert staged_module._joint_refine_delta_fraction(0) == 0.15
    assert staged_module._joint_refine_delta_fraction(1) == 0.05
    assert staged_module._joint_refine_delta_fraction(2) == 0.02
    assert staged_module._joint_refine_delta_fraction(3) == 0.02
    assert all(0.0 < value <= 17.0 for value in round0)
    assert all(0.0 <= value <= 7.0 for value in round1)
    assert all(0.0 <= value <= 4.0 for value in round2)
    assert all(0.0 <= value <= 4.0 for value in round3)
    assert len({round(value, 12) for value in round0}) > 1


def test_joint_refine_keeps_categorical_adjacent_neighbor_regression() -> None:
    path = ("mode",)
    spec = KnobSpec(
        path=path,
        kind="categorical",
        choices=("low", "nominal", "high"),
    )
    parent = RecipePatch({path: "nominal"})

    for child_index in range(4):
        patch = staged_module._refine_patch_near_parent(
            parent,
            (spec,),
            schema=RecipeSchema(allowlist=(spec,)),
            seed=19,
            sampler_name=doe_module.SCIPY_SOBOL_SAMPLER,
            round_index=1,
            parent_index=0,
            child_index=child_index,
            children_per_parent=4,
        )
        direction = -1 if (19 + 1 + child_index) % 2 else 1
        expected = spec.choices[(spec.choices.index("nominal") + direction) % 3]
        assert patch.values[path] == expected


def test_joint_refine_composite_operator_is_deterministic_for_same_seed() -> None:
    if not doe_module._scipy_sobol_available():
        pytest.skip("scipy-sobol unavailable")

    float_path = ("test_float",)
    int_path = ("test_int",)
    mode_path = ("mode",)
    specs = (
        KnobSpec(path=float_path, kind="float", low=0.0, high=100.0),
        KnobSpec(path=int_path, kind="int", low=0, high=10),
        KnobSpec(
            path=mode_path,
            kind="categorical",
            choices=("low", "nominal", "high"),
        ),
    )
    schema = RecipeSchema(allowlist=specs)
    parent = RecipePatch(
        {float_path: 50.0, int_path: 5, mode_path: "nominal"}
    ).validated(schema)

    def composite_values() -> tuple[Mapping[tuple[str, ...], Any], ...]:
        return tuple(
            staged_module._refine_patch_near_parent(
                parent,
                specs,
                schema=schema,
                seed=41,
                sampler_name=doe_module.SCIPY_SOBOL_SAMPLER,
                round_index=0,
                parent_index=2,
                child_index=child_index,
                children_per_parent=6,
            ).values
            for child_index in range(6)
        )

    assert composite_values() == composite_values()


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
            failure_category=FailureCategory.INFEASIBLE_RECIPE,
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
    result = study.run(
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

    payload = json.loads((out / "pareto.json").read_text())
    leaderboard_lines = (out / "leaderboard.csv").read_text().strip().splitlines()
    assert result.status == "completed-no-feasible-winner"
    assert result.winner is None
    assert result.pareto == ()
    assert payload["pareto"] == []
    assert payload["status"] == "completed-no-feasible-winner"
    assert len(leaderboard_lines) == len(result.records) + 1
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
            run_reference=RunReference(
                status="failed",
                trace={"backend_status": "diagnostic_stub"},
            ),
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
    stored_reference = strategy.results[0][1].run_reference
    assert stored_reference.trace == {"backend_status": "ok"}
    assert stored_reference.backend_authoritative is True


def test_staged_strip_trace_preserves_real_backend_provenance(tmp_path) -> None:
    cache_config = {
        "db_path": str(tmp_path / "pt1-cache.db"),
        "miss_policy": "fail-loud",
        "authorized_backend_name": "alphamelts",
    }
    real_profile = {
        **PROFILE,
        "fidelities": {
            "high": {
                "backend_name": "cached-real",
                "hours": 1,
                "reduced_real_cache": cache_config,
            }
        },
    }
    strategy = StagedStrategy(
        RecipeSchema(
            allowlist=tuple(spec for spec in RecipeSchema.ALLOWLIST if spec.path[:2] == ("campaigns", "C0"))
        ),
        seed=23,
        objective_profile={**real_profile, "staged": {"beam_width": 1, "children_per_parent": 1}},
    )
    candidate = strategy.ask(1)[0]
    scored = _scored(
        candidate.patch,
        "lunar_mare_low_ti",
        "high",
        real_profile,
        candidate_id=candidate.id,
        trace={},
    )
    assert scored.run_reference.trace == {}
    scored = replace(
        scored,
        run_reference=RunReference(
            status="ok",
            trace={},
            product_summary=scored.run_reference.product_summary,
            backend_status="ok",
            backend_authoritative=True,
        ),
    )
    pre_fix_stripped = replace(
        scored,
        run_reference=RunReference(
            status=scored.run_reference.status,
            error_message=scored.run_reference.error_message,
            reason=scored.run_reference.reason,
            trace=None,
            product_summary=scored.run_reference.product_summary,
        ),
    )
    with pytest.raises(study.StudyAbort, match="missing backend_status"):
        study._assert_result_artifact_floor(pre_fix_stripped)

    strategy.tell([(candidate, scored)])

    stored = strategy.results[0][1]
    assert stored.run_reference.trace == {"backend_status": "ok"}
    assert stored.run_reference.backend_status == "ok"
    assert stored.run_reference.backend_authoritative is True
    study._assert_result_artifact_floor(stored)
