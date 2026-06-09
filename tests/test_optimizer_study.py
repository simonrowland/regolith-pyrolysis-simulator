from __future__ import annotations

import csv
from dataclasses import is_dataclass, fields, replace
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

import pytest
import yaml

from simulator.optimize import cli as optimizer_cli
from simulator.optimize import study
from simulator.optimize.evalspec import EvalSpec, cache_key
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult, _build_eval_inputs
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, PhysicsConstraintSet, ThresholdSpec
from simulator.optimize.physics import physics_constraints_digest
from simulator.optimize.profiles import physics_constraints_from_profile
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.optimize.results_store import ResultStore


PROFILE = {
    "profile_id": "study-test",
    "profile_schema_version": "profile-schema-v1",
    "feedstock": "lunar_mare_low_ti",
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
            "id": "study-c0-seed",
            "source_campaign": "C0",
            "patch": {"campaigns": {"C0": {"temp_range_C": [900, 950]}}},
        }
    ],
}
FEEDSTOCK = "lunar_mare_low_ti"


def _threshold() -> ThresholdSpec:
    return ThresholdSpec(
        id="test_gate_min",
        value=0.0,
        units="unit",
        source="engineering_envelope",
        source_ref="test",
    )


def _margin(
    *,
    feasible: bool = True,
    margin: float | None = None,
    observed: float | None = None,
) -> GateMargin:
    return GateMargin(
        gate="delivered_stream_purity",
        feasible=feasible,
        margin=margin if margin is not None else (1.0 if feasible else -1.0),
        threshold=_threshold(),
        observed=observed if observed is not None else (1.0 if feasible else 0.0),
        detail="test",
    )


def _sequence(candidate_id: str | None) -> int:
    assert candidate_id is not None
    return int(candidate_id.rsplit("-", 1)[1])


def _spec(
    patch: RecipePatch,
    feedstock: str,
    fidelity: str,
    profile: Mapping[str, Any],
    constraints: Any | None = None,
) -> EvalSpec:
    spec, _ = _build_eval_inputs(
        patch.validated(RecipeSchema()),
        feedstock,
        fidelity,
        profile,
        RecipeSchema(),
        constraints=constraints,
    )
    return spec


def _scope_spec() -> EvalSpec:
    return _spec(
        RecipePatch({}),
        FEEDSTOCK,
        "stub",
        PROFILE,
        physics_constraints_from_profile(PROFILE),
    )


def _stored_rows(out_dir: Path) -> list[ScoredResult]:
    spec = _scope_spec()
    store = ResultStore(
        out_dir / "cache.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    return store.query(FEEDSTOCK, profile_id=spec.profile_id, fidelity=spec.fidelity)


def _read_provenance(out_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (out_dir / "provenance.jsonl").read_text().splitlines()]


def _evaluator(*, infeasible: set[int] | None = None, engine_bug: set[int] | None = None):
    bad = infeasible or set()
    aborts = engine_bug or set()

    def evaluate_patch(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        **kwargs: Any,
    ) -> ScoredResult:
        index = _sequence(candidate_id)
        spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
        if index in aborts:
            return ScoredResult(
                candidate_id=candidate_id,
                eval_spec=spec,
                cache_key=cache_key(spec),
                feasible=False,
                failure_category=FailureCategory.ENGINE_BUG,
                feasibility_margins={"delivered_stream_purity": _margin(feasible=False)},
                failing_gates=("delivered_stream_purity",),
                run_reference=RunReference(
                    status="failed",
                    trace={"backend_status": "diagnostic_stub", "snapshots": ["heavy"]},
                ),
            )
        if index in bad:
            return ScoredResult(
                candidate_id=candidate_id,
                eval_spec=spec,
                cache_key=cache_key(spec),
                feasible=False,
                failure_category=FailureCategory.INFEASIBLE_RECIPE,
                feasibility_margins={"delivered_stream_purity": _margin(feasible=False)},
                failing_gates=("delivered_stream_purity",),
                run_reference=RunReference(
                    status="ok",
                    trace={"backend_status": "diagnostic_stub", "snapshots": ["heavy"]},
                ),
            )
        objectives = ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", 10.0 + index, "kg", ordinal=0),
                ObjectiveValue("energy_kWh", "minimize", 5.0 + index, "kWh", ordinal=1),
            )
        )
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key=cache_key(spec),
            feasible=True,
            objectives=objectives,
            feasibility_margins={"delivered_stream_purity": _margin()},
            run_reference=RunReference(
                status="ok",
                trace={"backend_status": "diagnostic_stub", "snapshots": ["heavy"]},
            ),
        )

    return evaluate_patch


def test_budget_three_stub_e2e_writes_artifacts_and_round_trips_winner(tmp_path) -> None:
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        3,
        tmp_path,
        seed=7,
        evaluator=_evaluator(),
    )

    assert (tmp_path / "pareto.json").exists()
    assert (tmp_path / "leaderboard.csv").exists()
    assert (tmp_path / "winner.recipe.yaml").exists()
    assert (tmp_path / "provenance.jsonl").exists()
    assert (tmp_path / "cache.sqlite").exists()
    assert result.winner.candidate_id == "random-7-000002"

    provenance = _read_provenance(tmp_path)
    assert len(provenance) == 3

    pareto_payload = json.loads((tmp_path / "pareto.json").read_text())
    assert {row["candidate_id"] for row in pareto_payload["pareto"]} == {
        "random-7-000000",
        "random-7-000001",
        "random-7-000002",
    }
    assert all(row["feasible"] is True for row in pareto_payload["pareto"])

    leaderboard = list(csv.DictReader((tmp_path / "leaderboard.csv").open()))
    assert [row["rank"] for row in leaderboard] == ["1", "2", "3"]
    assert [row["candidate_id"] for row in leaderboard] == [
        "random-7-000002",
        "random-7-000001",
        "random-7-000000",
    ]
    assert pareto_payload["winner_candidate_id"] in {row["candidate_id"] for row in leaderboard}
    assert result.winner.candidate_id in {row["candidate_id"] for row in pareto_payload["pareto"]}
    assert [float(row["oxygen_kg"]) for row in leaderboard] == [12.0, 11.0, 10.0]
    assert [float(row["energy_kWh"]) for row in leaderboard] == [7.0, 6.0, 5.0]
    assert [float(row["margin_delivered_stream_purity"]) for row in leaderboard] == [1.0, 1.0, 1.0]
    assert len(_stored_rows(tmp_path)) == 3

    loaded = yaml.safe_load((tmp_path / "winner.recipe.yaml").read_text())
    assert RecipePatch.from_nested(loaded).validated(RecipeSchema())


def test_backend_status_field_survives_strip_and_store_for_real_backend(tmp_path) -> None:
    spec = replace(_scope_spec(), backend_name="alphamelts")
    scored = ScoredResult(
        candidate_id="real-backend-field",
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", 1.0, "kg", ordinal=0),
                ObjectiveValue("energy_kWh", "minimize", 1.0, "kWh", ordinal=1),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            trace={},
            backend_status="ok",
            backend_authoritative=True,
        ),
    )

    study._assert_result_artifact_floor(scored)
    light = study._strip_heavy_result(scored)

    assert light.run_reference is not None
    assert light.run_reference.backend_status == "ok"
    assert light.run_reference.backend_authoritative is True
    ResultStore(tmp_path / "cache.sqlite").store(spec, light, created_at="t1")


def test_clean_zero_wall_deposit_infinite_margin_optimizes_and_ranks_best(tmp_path) -> None:
    def clean_evaluator(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        **kwargs: Any,
    ) -> ScoredResult:
        index = _sequence(candidate_id)
        spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
        coating_margin = math.inf if index == 1 else 0.5
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key=cache_key(spec),
            feasible=True,
            objectives=ObjectiveVector(
                (
                    ObjectiveValue("oxygen_kg", "maximize", 10.0 + index, "kg", ordinal=0),
                    ObjectiveValue("energy_kWh", "minimize", 5.0 + index, "kWh", ordinal=1),
                )
            ),
            feasibility_margins={
                "delivered_stream_purity": _margin(),
                "coating": GateMargin(
                    gate="coating",
                    feasible=True,
                    margin=coating_margin,
                    threshold=ThresholdSpec(
                        id="coating_min_campaigns_to_resinter",
                        value=10.0,
                        units="campaigns",
                        source="code_default",
                        source_ref="clean zero-wall-deposit test",
                    ),
                    observed=math.inf if index == 1 else 10.5,
                    detail="no wall deposit" if index == 1 else "finite deposit",
                ),
            },
            run_reference=RunReference(
                status="ok",
                trace={"backend_status": "diagnostic_stub", "snapshots": [index]},
            ),
        )

    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        2,
        tmp_path,
        seed=7,
        evaluator=clean_evaluator,
    )

    assert result.winner.candidate_id == "random-7-000001"
    provenance = _read_provenance(tmp_path)
    winner_row = next(row for row in provenance if row["candidate_id"] == result.winner.candidate_id)
    assert winner_row["feasibility_margins"]["coating"]["margin"] == "+inf"
    assert len(_stored_rows(tmp_path)) == 2


def test_constraint_threshold_change_misses_cached_verdict(tmp_path) -> None:
    schema = RecipeSchema()
    patch = RecipePatch({})
    loose = PhysicsConstraintSet()
    tight = PhysicsConstraintSet(
        furnace_T_max_C=ThresholdSpec(
            id="furnace_T_max_C",
            value=900.0,
            units="degC",
            source="code_default",
            source_ref="test tightened furnace ceiling",
        )
    )
    spec_loose, _ = _build_eval_inputs(
        patch.validated(schema),
        FEEDSTOCK,
        "stub",
        PROFILE,
        schema,
        constraints=loose,
    )
    spec_tight, _ = _build_eval_inputs(
        patch.validated(schema),
        FEEDSTOCK,
        "stub",
        PROFILE,
        schema,
        constraints=tight,
    )
    store = ResultStore(tmp_path / "cache.sqlite")
    scored = ScoredResult(
        candidate_id="cached",
        eval_spec=spec_loose,
        cache_key=cache_key(spec_loose),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", 1.0, "kg", ordinal=0),
                ObjectiveValue("energy_kWh", "minimize", 1.0, "kWh", ordinal=1),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            trace={"backend_status": "diagnostic_stub"},
        ),
    )
    store.store(spec_loose, scored, created_at="t1")
    candidate = study.Candidate(id="random-7-000000", patch=patch)

    assert cache_key(spec_tight) != cache_key(spec_loose)
    assert study._lookup_cached(candidate, PROFILE, FEEDSTOCK, "stub", schema, store, loose)
    assert study._lookup_cached(candidate, PROFILE, FEEDSTOCK, "stub", schema, store, tight) is None


def test_profile_constraint_threshold_change_changes_cache_digest() -> None:
    schema = RecipeSchema()
    patch = RecipePatch({})
    loose_profile = dict(PROFILE)
    loose_profile["constraints"] = {
        **PROFILE["constraints"],
        "furnace_T_max_C": 1800.0,
    }
    tight_profile = dict(PROFILE)
    tight_profile["constraints"] = {
        **PROFILE["constraints"],
        "furnace_T_max_C": 1300.0,
    }
    loose = physics_constraints_from_profile(loose_profile)
    tight = physics_constraints_from_profile(tight_profile)
    spec_loose, _ = _build_eval_inputs(
        patch.validated(schema),
        FEEDSTOCK,
        "stub",
        loose_profile,
        schema,
        constraints=loose,
    )
    spec_tight, _ = _build_eval_inputs(
        patch.validated(schema),
        FEEDSTOCK,
        "stub",
        tight_profile,
        schema,
        constraints=tight,
    )

    assert physics_constraints_digest(loose) != physics_constraints_digest(tight)
    assert cache_key(spec_loose) != cache_key(spec_tight)


def test_stub_smoke_selector_ignores_profile_threshold_overrides() -> None:
    profile = dict(PROFILE)
    profile["study_constraints"] = "stub_smoke"
    profile["constraints"] = {
        **PROFILE["constraints"],
        "furnace_T_max_C": 1300.0,
    }

    constraints = study._constraints_for_profile(profile)

    assert isinstance(constraints, study.StubSmokeConstraintSet)


def test_fidelity_pilot_profile_resolves_stub_smoke_constraints() -> None:
    profile_path = Path("data/optimize_profiles/lunar_mare_low_ti.yaml")
    profile = yaml.safe_load(profile_path.read_text())

    constraints = study._constraints_for_profile(profile)

    assert profile["study_constraints"] == "stub_smoke"
    assert isinstance(constraints, study.StubSmokeConstraintSet)


def test_feasibility_filter_excludes_infeasible_from_pareto_but_logs_provenance(tmp_path) -> None:
    study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        3,
        tmp_path,
        seed=7,
        evaluator=_evaluator(infeasible={1}),
    )

    pareto = json.loads((tmp_path / "pareto.json").read_text())["pareto"]
    provenance = [
        json.loads(line)
        for line in (tmp_path / "provenance.jsonl").read_text().splitlines()
    ]

    assert "random-7-000001" not in {row["candidate_id"] for row in pareto}
    logged = {row["candidate_id"]: row for row in provenance}
    assert logged["random-7-000001"]["status"] == "infeasible_recipe"
    assert logged["random-7-000001"]["feasibility_margins"]["delivered_stream_purity"]["margin"] < 0


def test_all_infeasible_writes_empty_pareto_and_no_winner(tmp_path) -> None:
    with pytest.raises(study.StudyNoFeasibleError):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "random",
            "stub",
            1,
            3,
            tmp_path,
            seed=7,
            evaluator=_evaluator(infeasible={0, 1, 2}),
        )

    pareto_payload = json.loads((tmp_path / "pareto.json").read_text())
    assert pareto_payload["pareto"] == []
    assert pareto_payload["winner_candidate_id"] is None
    assert (tmp_path / "leaderboard.csv").read_text().splitlines() == [
        "rank,candidate_id,cache_key,is_pareto,is_winner,oxygen_kg,energy_kWh,patch_json"
    ]
    assert len(_read_provenance(tmp_path)) == 3
    assert len(_stored_rows(tmp_path)) == 3
    assert not (tmp_path / "winner.recipe.yaml").exists()


def test_single_feasible_point_is_winner(tmp_path) -> None:
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        3,
        tmp_path,
        seed=7,
        evaluator=_evaluator(infeasible={0, 2}),
    )

    assert [record.candidate_id for record in result.pareto] == ["random-7-000001"]
    assert result.winner.candidate_id == "random-7-000001"
    pareto_payload = json.loads((tmp_path / "pareto.json").read_text())
    assert [row["candidate_id"] for row in pareto_payload["pareto"]] == ["random-7-000001"]


def test_winner_tie_determinism_uses_cache_key_then_candidate_id(tmp_path) -> None:
    def tied(
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
            feasible=True,
            objectives=ObjectiveVector(
                (
                    ObjectiveValue("oxygen_kg", "maximize", 10.0, "kg", ordinal=0),
                    ObjectiveValue("energy_kWh", "minimize", 5.0, "kWh", ordinal=1),
                )
            ),
            feasibility_margins={"delivered_stream_purity": _margin()},
            run_reference=RunReference(
                status="ok",
                trace={"backend_status": "diagnostic_stub"},
            ),
        )

    first = study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 3, tmp_path / "first", seed=7, evaluator=tied)
    second = study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 3, tmp_path / "second", seed=7, evaluator=tied)

    expected = min(first.pareto, key=lambda record: (record.cache_key or "", record.candidate_id))
    assert first.winner.candidate_id == expected.candidate_id
    assert second.winner.candidate_id == expected.candidate_id
    assert [record.candidate_id for record in first.pareto] == [
        record.candidate_id for record in second.pareto
    ]


def test_rerun_hits_cache_without_duplicating_rows(tmp_path) -> None:
    study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 3, tmp_path, seed=7, evaluator=_evaluator())
    study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 3, tmp_path, seed=7, evaluator=_evaluator())

    provenance = _read_provenance(tmp_path)
    assert len(provenance) == 3
    assert all(row["cache_hit"] is True for row in provenance)
    assert len(_stored_rows(tmp_path)) == 3


def test_engine_bug_result_aborts_without_pareto(tmp_path) -> None:
    with pytest.raises(study.StudyAbort):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "random",
            "stub",
            1,
            1,
            tmp_path,
            seed=7,
            evaluator=_evaluator(engine_bug={0}),
        )

    assert not (tmp_path / "pareto.json").exists()


def test_feasible_nonfinite_margin_aborts_without_pareto(tmp_path) -> None:
    def bad_margin(
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
            feasible=True,
            objectives=ObjectiveVector(
                (
                    ObjectiveValue("oxygen_kg", "maximize", 1.0, "kg", ordinal=0),
                    ObjectiveValue("energy_kWh", "minimize", 1.0, "kWh", ordinal=1),
                )
            ),
            feasibility_margins={
                "delivered_stream_purity": _margin(margin=math.nan),
            },
        )

    with pytest.raises(study.StudyAbort, match="margin.*NaN"):
        study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 1, tmp_path, evaluator=bad_margin)

    assert not (tmp_path / "pareto.json").exists()
    assert not (tmp_path / "winner.recipe.yaml").exists()


def test_infeasible_nonfinite_margin_aborts_without_pareto(tmp_path) -> None:
    def bad_margin(
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
            failure_category=FailureCategory.INFEASIBLE_RECIPE,
            feasibility_margins={
                "delivered_stream_purity": _margin(feasible=False, observed=math.nan),
            },
            failing_gates=("delivered_stream_purity",),
        )

    with pytest.raises(study.StudyAbort, match="observed.*NaN"):
        study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 1, tmp_path, evaluator=bad_margin)

    assert not (tmp_path / "pareto.json").exists()
    assert not (tmp_path / "winner.recipe.yaml").exists()


def test_infeasible_missing_metadata_aborts_before_ok_artifacts(tmp_path) -> None:
    produced: list[ScoredResult] = []

    def unmarked_infeasible(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        **_: Any,
    ) -> ScoredResult:
        result = ScoredResult(
            candidate_id=candidate_id,
            eval_spec=None,
            cache_key=None,
            feasible=False,
            failure_category=None,
            feasibility_margins={},
            run_reference=RunReference(status="ok"),
        )
        produced.append(result)
        return result

    with pytest.raises(study.StudyAbort, match="eval_spec/cache_key"):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "random",
            "stub",
            1,
            1,
            tmp_path,
            evaluator=unmarked_infeasible,
        )

    assert produced and study._status(produced[0]) == "ok"
    assert not (tmp_path / "pareto.json").exists()
    assert not _read_provenance(tmp_path)
    assert not _stored_rows(tmp_path)


def test_nonfinite_objective_aborts_without_pareto(tmp_path) -> None:
    def bad_objective(
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
            feasible=True,
            objectives={"oxygen_kg": math.nan, "energy_kWh": 1.0},
            feasibility_margins={"delivered_stream_purity": _margin()},
            run_reference=RunReference(
                status="ok",
                trace={"backend_status": "diagnostic_stub"},
            ),
        )

    with pytest.raises(study.StudyAbort, match="oxygen_kg is non-finite"):
        study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 1, tmp_path, evaluator=bad_objective)

    assert not (tmp_path / "pareto.json").exists()
    assert not (tmp_path / "winner.recipe.yaml").exists()


def test_feasible_nonfinite_or_unmarked_result_is_rejected(tmp_path) -> None:
    def unmarked(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        **_: Any,
    ) -> ScoredResult:
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=None,
            cache_key=None,
            feasible=True,
            objectives=ObjectiveVector(
                (
                    ObjectiveValue("oxygen_kg", "maximize", 1.0, "kg", ordinal=0),
                    ObjectiveValue("energy_kWh", "minimize", 1.0, "kWh", ordinal=1),
                )
            ),
        )

    with pytest.raises(study.StudyAbort, match="eval_spec/cache_key"):
        study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 1, tmp_path, evaluator=unmarked)


def test_study_result_records_are_light_no_trace_or_snapshots(tmp_path) -> None:
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        2,
        tmp_path,
        seed=7,
        evaluator=_evaluator(),
    )

    assert not hasattr(result.leaderboard[0], "run_reference")
    assert not hasattr(result.leaderboard[0], "snapshots")
    assert not _contains_key(result, "snapshots")


def test_cli_help_unknowns_and_budget_one_stub_run(tmp_path) -> None:
    help_run = subprocess.run(
        [sys.executable, "-m", "simulator.optimize", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_run.returncode == 0
    assert "--strategy" in help_run.stdout

    bad_strategy = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.optimize",
            "--feedstock",
            FEEDSTOCK,
            "--profile",
            "default",
            "--strategy",
            "bogus",
            "--fidelity",
            "stub",
            "--budget",
            "1",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad_strategy.returncode != 0
    assert "invalid choice" in bad_strategy.stderr

    bad_feedstock = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.optimize",
            "--feedstock",
            "not_a_feedstock",
            "--profile",
            "default",
            "--strategy",
            "random",
            "--fidelity",
            "stub",
            "--budget",
            "1",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad_feedstock.returncode != 0
    assert "unknown feedstock" in bad_feedstock.stderr

    bad_profile = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.optimize",
            "--feedstock",
            FEEDSTOCK,
            "--profile",
            "not_a_profile",
            "--strategy",
            "random",
            "--fidelity",
            "stub",
            "--budget",
            "1",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad_profile.returncode != 0
    assert "invalid choice" in bad_profile.stderr

    bad_fidelity = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.optimize",
            "--feedstock",
            FEEDSTOCK,
            "--profile",
            "default",
            "--strategy",
            "random",
            "--fidelity",
            "bogus",
            "--budget",
            "1",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad_fidelity.returncode != 0
    assert "invalid choice" in bad_fidelity.stderr

    bad_budget = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.optimize",
            "--feedstock",
            FEEDSTOCK,
            "--profile",
            "default",
            "--strategy",
            "random",
            "--fidelity",
            "stub",
            "--budget",
            "0",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad_budget.returncode != 0
    assert "must be positive" in bad_budget.stderr

    existing_file = tmp_path / "not-a-directory"
    existing_file.write_text("occupied", encoding="utf-8")
    bad_out = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.optimize",
            "--feedstock",
            FEEDSTOCK,
            "--profile",
            "default",
            "--strategy",
            "random",
            "--fidelity",
            "stub",
            "--budget",
            "1",
            "--out",
            str(existing_file),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad_out.returncode != 0
    assert "error: output path exists and is not a directory" in bad_out.stderr
    assert "Traceback" not in bad_out.stderr

    staged = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.optimize",
            "--feedstock",
            FEEDSTOCK,
            "--profile",
            "default",
            "--strategy",
            "staged",
            "--fidelity",
            "stub",
            "--budget",
            "1",
            "--out",
            str(tmp_path / "staged"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert staged.returncode == 0, staged.stdout + staged.stderr
    assert "strategy: staged->StagedStrategy" in staged.stdout

    out_dir = tmp_path / "cli-run"
    good = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.optimize",
            "--feedstock",
            FEEDSTOCK,
            "--profile",
            "default",
            "--strategy",
            "random",
            "--fidelity",
            "stub",
            "--budget",
            "1",
            "--out",
            str(out_dir),
            "--seed",
            "7",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert good.returncode == 0, good.stderr
    assert (out_dir / "pareto.json").exists()
    assert (out_dir / "winner.recipe.yaml").exists()
    status = json.loads((out_dir / "job_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "SUCCEEDED"
    assert status["success"] is True
    assert status["winner_candidate_id"]


def test_cli_writes_failure_job_status_marker_for_no_feasible(
    tmp_path,
    monkeypatch,
) -> None:
    out_dir = tmp_path / "cli-failed"

    def fail_after_partial_artifacts(**kwargs):
        Path(kwargs["out_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(kwargs["out_dir"]) / "cache.sqlite").write_text("partial", encoding="utf-8")
        raise study.StudyNoFeasibleError(
            "no feasible candidates; winner.recipe.yaml not written"
        )

    monkeypatch.setattr(optimizer_cli, "run", fail_after_partial_artifacts)

    with pytest.raises(SystemExit) as exc_info:
        optimizer_cli.main(
            [
                "--feedstock",
                FEEDSTOCK,
                "--profile",
                "default",
                "--strategy",
                "random",
                "--fidelity",
                "stub",
                "--budget",
                "1",
                "--out",
                str(out_dir),
            ]
        )

    assert exc_info.value.code == 2
    status = json.loads((out_dir / "job_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "FAILED"
    assert status["success"] is False
    assert status["reason"] == "StudyNoFeasibleError"
    assert "no feasible candidates" in status["message"]


def test_determinism_same_seed_same_pareto_and_winner(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 3, first, seed=11, evaluator=_evaluator())
    study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 3, second, seed=11, evaluator=_evaluator())

    assert (first / "pareto.json").read_text() == (second / "pareto.json").read_text()
    assert (first / "winner.recipe.yaml").read_text() == (second / "winner.recipe.yaml").read_text()


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, Mapping):
        return any(item == key or _contains_key(child, key) for item, child in value.items())
    if isinstance(value, (str, bytes, Path)):
        return False
    if isinstance(value, tuple | list):
        return any(_contains_key(item, key) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return any(_contains_key(getattr(value, field.name), key) for field in fields(value))
    return False
