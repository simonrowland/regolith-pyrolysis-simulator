from __future__ import annotations

import csv
from dataclasses import is_dataclass, fields
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

import pytest
import yaml

from simulator.optimize import study
from simulator.optimize.evalspec import EvalSpec, cache_key
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult, _build_eval_inputs
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.optimize.results_store import ResultStore


PROFILE = {
    "profile_id": "study-test",
    "profile_schema_version": "profile-schema-v1",
    "feedstock": "lunar_mare_low_ti",
    "objectives": [
        {"metric": "oxygen_kg", "sense": "maximize", "units": "kg", "weight": 0.6},
        {"metric": "energy_kWh", "sense": "minimize", "units": "kWh", "weight": 0.4},
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
) -> EvalSpec:
    spec, _ = _build_eval_inputs(
        patch.validated(RecipeSchema()),
        feedstock,
        fidelity,
        profile,
        RecipeSchema(),
    )
    return spec


def _scope_spec() -> EvalSpec:
    return _spec(RecipePatch({}), FEEDSTOCK, "stub", PROFILE)


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
        **_: Any,
    ) -> ScoredResult:
        index = _sequence(candidate_id)
        spec = _spec(patch, feedstock, fidelity, profile)
        if index in aborts:
            return ScoredResult(
                candidate_id=candidate_id,
                eval_spec=spec,
                cache_key=cache_key(spec),
                feasible=False,
                failure_category=FailureCategory.ENGINE_BUG,
                feasibility_margins={"delivered_stream_purity": _margin(feasible=False)},
                failing_gates=("delivered_stream_purity",),
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
                run_reference=RunReference(status="ok", trace={"snapshots": ["heavy"]}),
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
            run_reference=RunReference(status="ok", trace={"snapshots": ["heavy"]}),
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

    with pytest.raises(study.StudyAbort, match="margin.*non-finite"):
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
                "delivered_stream_purity": _margin(feasible=False, observed=math.inf),
            },
            failing_gates=("delivered_stream_purity",),
        )

    with pytest.raises(study.StudyAbort, match="observed.*non-finite"):
        study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 1, tmp_path, evaluator=bad_margin)

    assert not (tmp_path / "pareto.json").exists()
    assert not (tmp_path / "winner.recipe.yaml").exists()


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
