from __future__ import annotations

import csv
from dataclasses import is_dataclass, fields, replace
import json
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
import yaml

import scripts.make_recipe_db_profile as generator
from simulator.optimize import cli as optimizer_cli
from simulator.optimize import study
from simulator.optimize.evalspec import EvalSpec, cache_key
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult, _build_eval_inputs
from simulator.optimize.evaluate import evaluate
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


def _evaluator(
    *,
    infeasible: set[int] | None = None,
    engine_bug: set[int] | None = None,
    out_of_domain: set[int] | None = None,
    non_finite_payload: set[int] | None = None,
    invalid_recipe: set[int] | None = None,
):
    bad = infeasible or set()
    aborts = engine_bug or set()
    domain_rejects = out_of_domain or set()
    non_finite = non_finite_payload or set()
    invalid = invalid_recipe or set()

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
        if index in domain_rejects:
            crash_point = {
                "temperature_C": 865.0,
                "pressure_bar": 1.0e-6,
                "fO2_log": -9.0,
                "composition_wt_pct": {"SiO2": 55.0, "CaO": 45.0},
                "composition_mol": {"SiO2": 1.0, "CaO": 1.0},
            }
            return ScoredResult(
                candidate_id=candidate_id,
                eval_spec=spec,
                cache_key=cache_key(spec),
                feasible=False,
                failure_category=FailureCategory.OUT_OF_DOMAIN,
                feasibility_margins={"backend_domain": GateMargin(
                    gate="backend_domain",
                    feasible=False,
                    margin=-1.0,
                    threshold=_threshold(),
                    observed=0.0,
                    detail="test alphamelts domain rejection",
                )},
                failing_gates=("backend_domain",),
                run_reference=RunReference(
                    status="ok",
                    trace={
                        "backend_status": "out_of_domain",
                        "backend_diagnostics": {
                            "backend_status": "out_of_domain",
                            "out_of_domain_crash_point": crash_point,
                        },
                        "out_of_domain_crash_point": crash_point,
                        "rump_terminal": {
                            "status": "not_earned",
                            "reason": "kernel_liquidus_disagree",
                            "liquid_fraction": 0.5,
                            "solidus_T_C": 900.0,
                            "T_crash_C": 865.0,
                        },
                        "terminal_rump_by_species_kg": {"CaO": 2.0},
                        "snapshots": ["heavy"],
                    },
                    backend_status="out_of_domain",
                ),
            )
        if index in non_finite:
            return ScoredResult(
                candidate_id=candidate_id,
                eval_spec=spec,
                cache_key=cache_key(spec),
                feasible=False,
                failure_category=FailureCategory.NON_FINITE_PAYLOAD,
                feasibility_margins={
                    "non_finite_payload": GateMargin(
                        gate="non_finite_payload",
                        feasible=False,
                        margin=-1.0,
                        threshold=_threshold(),
                        observed=1.0,
                        detail="test PT-0 non-finite payload",
                    )
                },
                failing_gates=("non_finite_payload",),
                run_reference=RunReference(
                    status="failed",
                    error_message="PT0NonFinitePayload: $.SCSS_ppm inf",
                    trace={"backend_status": "ok", "snapshots": ["heavy"]},
                    backend_status="ok",
                ),
                notes=("CALC_BUG: PT-0 payload contained a non-finite derived value",),
            )
        if index in invalid:
            return ScoredResult(
                candidate_id=candidate_id,
                eval_spec=spec,
                cache_key=cache_key(spec),
                feasible=False,
                failure_category=FailureCategory.INVALID_RECIPE,
                feasibility_margins={
                    "inventory_overdraw": GateMargin(
                        gate="inventory_overdraw",
                        feasible=False,
                        margin=-0.125,
                        threshold=_threshold(),
                        observed=0.125,
                        detail="test inventory overdraw",
                    )
                },
                failing_gates=("inventory_overdraw",),
                run_reference=RunReference(
                    status="failed",
                    error_message="ProposalRejected: balance would be -0.125 kg",
                    trace={"backend_status": "ok", "snapshots": ["heavy"]},
                    backend_status="ok",
                ),
                notes=("overdraw_kg=0.125",),
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


class _SingleCandidateStrategy:
    name = "single"
    seed = 0

    def __init__(self, patch: RecipePatch | None = None) -> None:
        self._pending = [study.Candidate(id="single-000000", patch=patch or RecipePatch({}))]

    def ask(self, n: int) -> list[study.Candidate]:
        batch = self._pending[:n]
        self._pending = self._pending[n:]
        return batch

    def tell(self, results) -> None:
        return None


class _ClosedLoopLedger:
    registry = {}

    def __init__(self, cleaned_melt: Mapping[str, float]) -> None:
        self._balances = {"process.cleaned_melt": dict(cleaned_melt)}

    def mol_by_account(self, account: str | None = None):
        if account is None:
            return {key: dict(value) for key, value in self._balances.items()}
        return dict(self._balances.get(account, {}))

    def kg_by_account(self, account: str | None = None):
        return {}


class _ClosedLoopSim:
    def __init__(self, snapshots: tuple[object, ...], configured_hours: int) -> None:
        self.atom_ledger = _ClosedLoopLedger({"SiO2": 1.0, "CaO": 1.0})
        self.train = SimpleNamespace(
            stages=(
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
            )
        )
        self.record = SimpleNamespace(
            feedstock_key=FEEDSTOCK,
            batch_mass_kg=1000.0,
            additives_kg={},
            snapshots=snapshots,
            total_hours=configured_hours,
        )
        self.melt = SimpleNamespace(hour=configured_hours)
        self.energy_cumulative_kWh = 1.0

    def product_ledger(self) -> dict[str, float]:
        return {}

    def _terminal_rump_by_species(self) -> dict[str, float]:
        return {"SiO2": 50.0, "CaO": 50.0}

    def _oxygen_terminal_partition_kg(self) -> dict[str, float]:
        return {
            "stored": 0.0,
            "vented": 0.0,
            "total": 0.0,
            "mre_anode_stored": 0.0,
        }


class _ClosedLoopTapExecutor:
    def __init__(self) -> None:
        self.durations: list[int] = []

    def execute(self, config: object) -> object:
        configured_hours = int(getattr(config, "hours", 3))
        duration = self._duration_from_config(config, configured_hours)
        self.durations.append(duration)
        snapshots = _closed_loop_snapshots()[:duration]
        return _closed_loop_run(snapshots, configured_hours=configured_hours)

    @staticmethod
    def _duration_from_config(config: object, fallback: int) -> int:
        setpoints = getattr(config, "setpoints", {})
        if isinstance(setpoints, Mapping):
            campaigns = setpoints.get("campaigns", {})
            if isinstance(campaigns, Mapping):
                for campaign in ("C0b_p_cleanup", "C2A_continuous"):
                    values = campaigns.get(campaign, {})
                    if not isinstance(values, Mapping):
                        continue
                    raw = values.get("duration_h")
                    if isinstance(raw, int | float) and math.isfinite(float(raw)):
                        return max(1, int(float(raw)))
        return fallback


def _closed_loop_snapshots() -> tuple[object, ...]:
    return (
        _closed_loop_snapshot(1, {"SiO2": 52.0, "CaO": 48.0}),
        _closed_loop_snapshot(2, {"SiO2": 50.0, "CaO": 50.0}),
        _closed_loop_snapshot(3, {"SiO2": 80.0, "CaO": 20.0}),
    )


def _closed_loop_snapshot(hour: int, composition_wt_pct: Mapping[str, float]) -> object:
    return SimpleNamespace(
        hour=hour,
        campaign=SimpleNamespace(name="C0B"),
        temperature_C=1200.0 + hour,
        melt_mass_kg=100.0,
        composition_wt_pct=dict(composition_wt_pct),
        inventory=SimpleNamespace(melt_oxide_kg={}),
        overhead=SimpleNamespace(composition={"O2": 0.25, "N2": 10.0}),
        condensed_by_stage_species_delta={},
        wall_deposit_by_segment_species_delta={},
        mass_in_kg=1000.0,
        mass_out_kg=1000.0,
        mass_balance_error_pct=0.0,
    )


def _closed_loop_run(
    snapshots: tuple[object, ...],
    *,
    configured_hours: int,
) -> object:
    return SimpleNamespace(
        simulator=_ClosedLoopSim(snapshots, configured_hours),
        snapshots=snapshots,
        trace=SimpleNamespace(
            snapshots=snapshots,
            wall_deposit_by_segment_species_kg={},
            wall_zone_by_segment={"stage_1_to_stage_2": "Hot"},
        ),
        per_hour=tuple(
            {"hour": snapshot.hour, "backend_status": "diagnostic_stub"}
            for snapshot in snapshots
        ),
        backend_status="diagnostic_stub",
        status="ok",
        error_message="",
        reason="",
    )


def _closed_loop_best_tap_profile() -> dict[str, Any]:
    return {
        **PROFILE,
        "profile_id": "closed-loop-best-tap",
        "constraints": {"gates": ["furnace_temperature"]},
        "run": {
            "campaign": "C0b_p_cleanup",
            "hours": 3,
            "mass_kg": 1000.0,
            "backend_name": "stub",
        },
        "fidelities": {"stub": {"backend_name": "stub", "hours": 3}},
        "objectives": [
            {
                "type": "composition_target",
                "id": "closed-loop-glass",
                "metric": "composition_target:closed-loop-glass",
                "sense": "maximize",
                "units": "score_0_1",
                "weight": 1.0,
                "rationale": "closed-loop best-tap materialization test",
                "target": {
                    "pool": "residual_rump_at_stop",
                    "require_coating_gate": False,
                    "species_vector": {"Si": "retain", "Ca": "retain"},
                    "composition_window": {
                        "pool": "residual_rump_at_stop",
                        "basis": "oxide_wt_pct",
                        "mode": "hard_window",
                        "oxides": {
                            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
                            "CaO": {
                                "min": 49.0,
                                "max": 51.0,
                                "strict": False,
                                "weight": 1.0,
                            },
                        },
                    },
                    "maturity": {"best_tap": {"enabled": True}},
                    "score_weights": {"extraction": 0.0, "composition": 1.0},
                },
            }
        ],
    }


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


def test_best_tap_winner_recipe_replays_tap_claim_through_eval_path(tmp_path) -> None:
    profile = _closed_loop_best_tap_profile()
    executor = _ClosedLoopTapExecutor()

    result = study.run(
        profile,
        FEEDSTOCK,
        _SingleCandidateStrategy(),
        "stub",
        1,
        1,
        tmp_path,
        evaluator=lambda patch, feedstock, fidelity, **kwargs: evaluate(
            patch,
            feedstock,
            fidelity,
            profile=kwargs["profile"],
            executor=executor,
            candidate_id=kwargs.get("candidate_id"),
        ),
    )

    leaderboard = list(csv.DictReader((tmp_path / "leaderboard.csv").open()))
    assert len(leaderboard) == 1
    assert result.winner.candidate_id == "single-000000"
    tap_claim = result.winner.trace_summary["composition_target"]
    assert tap_claim["tap_hour"] == 2

    emitted_recipe = yaml.safe_load((tmp_path / "winner.recipe.yaml").read_text())
    assert emitted_recipe["campaigns"]["C0b_p_cleanup"]["duration_h"] == pytest.approx(2.0)
    emitted_patch = RecipePatch.from_nested(emitted_recipe).validated(RecipeSchema())
    replay_executor = _ClosedLoopTapExecutor()
    replay = evaluate(
        emitted_patch,
        FEEDSTOCK,
        "stub",
        profile=profile,
        executor=replay_executor,
        candidate_id="replay",
    )
    assert replay.feasible
    assert replay_executor.durations == [2]
    assert replay.run_reference is not None
    replay_claim = replay.run_reference.trace["composition_target"]

    assert replay_claim["pool_snapshot_hour"] == tap_claim["pool_snapshot_hour"]
    assert replay_claim["resolved_composition"]["oxide_wt_pct"] == pytest.approx(
        tap_claim["resolved_composition"]["oxide_wt_pct"]
    )
    assert replay_claim["resolved_composition"]["ratios"] == pytest.approx(
        tap_claim["resolved_composition"]["ratios"]
    )
    assert [
        (row["id"], row["pool"], row["pass"])
        for row in replay_claim["rows"]
        if row.get("strict", True)
    ] == [
        (row["id"], row["pool"], row["pass"])
        for row in tap_claim["rows"]
        if row.get("strict", True)
    ]


def test_parallel_composition_target_stub_study_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(generator, "_runtime_engine_identity", lambda: ("stub-engine", "test"))
    profile_path = tmp_path / "lunar_mare_low_ti__pc-glass-retain-na-k-c3.real.yaml"
    assert (
        generator.main(
            [
                "lunar_mare_low_ti",
                "--target",
                "pc-glass-retain-na-k-c3",
                "--campaign",
                "C3",
                "--hours",
                "24",
                "--gate",
                "stub_smoke",
                "--db",
                str(tmp_path / "profile.db"),
                "--out",
                str(profile_path),
            ]
        )
        == 0
    )

    result = study.run(
        yaml.safe_load(profile_path.read_text()),
        "lunar_mare_low_ti",
        "screen",
        "stub",
        2,
        4,
        tmp_path / "study",
        seed=0,
    )

    assert result.winner is not None
    assert (tmp_path / "study" / "pareto.json").exists()


def test_leaderboard_separates_certified_envelope_from_preference_score(tmp_path) -> None:
    record = study.StudyRecord(
        candidate_id="targeted",
        patch=RecipePatch({}),
        feasible=True,
        status="ok",
        objectives={"composition_target:pc-glass-clear": 0.75},
        feasibility_margins={},
        cache_key="cache",
        trace_summary={
            "composition_target": {
                "certification_tier": "certified",
                "certified_envelope": [{"id": "FeO_total", "strict": True, "pass": True}],
                "preference_score": 0.75,
                "target_spec_digest": "digest",
            }
        },
    )

    study._write_leaderboard(
        tmp_path / "leaderboard.csv",
        [record],
        [record],
        record,
        [study.ObjectiveDefinition("composition_target:pc-glass-clear", "maximize", "score_0_1")],
        RecipeSchema(),
    )

    row = next(csv.DictReader((tmp_path / "leaderboard.csv").open()))
    assert row["certification_tier"] == "certified"
    assert json.loads(row["certified_envelope_json"]) == [
        {"id": "FeO_total", "pass": True, "strict": True}
    ]


def test_tap_truncated_winner_materializes_recipe_and_sidecar(tmp_path) -> None:
    profile = {
        **PROFILE,
        "run": {
            "campaign": "C0b_p_cleanup",
            "hours": 2,
            "mass_kg": 1000.0,
            "backend_name": "stub",
        },
    }
    record = study.StudyRecord(
        candidate_id="tap-winner",
        patch=RecipePatch({}),
        feasible=True,
        status="ok",
        objectives={"composition_target:pc-glass-clear": 1.0},
        feasibility_margins={},
        cache_key="cache",
        trace_summary={
            "composition_target": {
                "best_tap_enabled": True,
                "tap_hour": 1,
                "configured_hours": 2,
                "tap_provenance": "tap_truncated",
                "operator_instruction": {
                    "tap_hour": 1,
                    "configured_hours": 2,
                    "configured_campaign": "C0b_p_cleanup",
                    "phase_at_tap": "C0B",
                    "T_C": 950.0,
                    "pO2_mbar": 0.1,
                    "provenance": "tap_truncated",
                },
                "tap_grade_report": {
                    "melt_tap": {"oxide_wt_pct": {"SiO2": 50.0, "CaO": 50.0}},
                    "distillation_train_taps": {
                        "3": {
                            "dominant_species": "SiO2",
                            "dominant_species_purity_pct": 100.0,
                            "species_wt_pct": {"SiO2": 100.0},
                        }
                    },
                },
                "tap_score_curve": [{"hour": 1, "score": 1.0, "certified": True}],
            }
        },
    )

    artifacts = study._write_artifacts(
        tmp_path,
        profile=profile,
        feedstock=FEEDSTOCK,
        fidelity="stub",
        definitions=[
            study.ObjectiveDefinition(
                "composition_target:pc-glass-clear",
                "maximize",
                "score_0_1",
            )
        ],
        pareto=[record],
        leaderboard=[record],
        winner=record,
        schema=RecipeSchema(),
        failure_counts={},
    )

    recipe = yaml.safe_load((tmp_path / "winner.recipe.yaml").read_text())
    assert recipe["campaigns"]["C0b_p_cleanup"]["duration_h"] == pytest.approx(1.0)
    assert RecipePatch.from_nested(recipe).validated(RecipeSchema())
    pareto = json.loads((tmp_path / "pareto.json").read_text())
    pareto_row = pareto["pareto"][0]
    assert pareto_row["patch"]["campaigns"]["C0b_p_cleanup"]["duration_h"] == pytest.approx(1.0)
    assert pareto_row["materialized_patch"]["campaigns"]["C0b_p_cleanup"]["duration_h"] == pytest.approx(1.0)
    assert pareto_row["parent_trajectory_patch"] == {}
    leaderboard_row = next(csv.DictReader((tmp_path / "leaderboard.csv").open()))
    assert json.loads(leaderboard_row["patch_json"])["campaigns"]["C0b_p_cleanup"]["duration_h"] == pytest.approx(1.0)
    assert json.loads(leaderboard_row["materialized_patch_json"])["campaigns"]["C0b_p_cleanup"]["duration_h"] == pytest.approx(1.0)
    assert json.loads(leaderboard_row["parent_trajectory_patch_json"]) == {}
    sidecar = json.loads((tmp_path / "winner.tap-truncated.json").read_text())
    assert artifacts["winner_tap_truncated"] == tmp_path / "winner.tap-truncated.json"
    assert sidecar["materialized_patch"]["campaigns"]["C0b_p_cleanup"]["duration_h"] == pytest.approx(1.0)
    assert sidecar["parent_trajectory_patch"] == {}
    assert sidecar["operator_instruction"]["tap_hour"] == 1
    assert sidecar["tap_grade_report"]["melt_tap"]["oxide_wt_pct"] == {
        "CaO": 50.0,
        "SiO2": 50.0,
    }


def test_tap_truncated_c3_materialization_fails_loud_for_dosing_schedule(tmp_path) -> None:
    profile = {
        **PROFILE,
        "run": {
            "campaign": "C3",
            "hours": 6,
            "mass_kg": 1000.0,
            "backend_name": "stub",
        },
    }
    record = study.StudyRecord(
        candidate_id="tap-c3",
        patch=RecipePatch({}),
        feasible=True,
        status="ok",
        objectives={"composition_target:pc-glass-clear": 1.0},
        feasibility_margins={},
        cache_key="cache",
        trace_summary={
            "composition_target": {
                "best_tap_enabled": True,
                "tap_hour": 2,
                "configured_hours": 6,
                "tap_provenance": "tap_truncated",
                "operator_instruction": {
                    "tap_hour": 2,
                    "configured_hours": 6,
                    "configured_campaign": "C3",
                    "phase_at_tap": "C3_NA",
                    "provenance": "tap_truncated",
                },
            }
        },
    )

    with pytest.raises(study.StudyAbort, match="cannot be faithfully materialized"):
        study._write_artifacts(
            tmp_path,
            profile=profile,
            feedstock=FEEDSTOCK,
            fidelity="stub",
            definitions=[
                study.ObjectiveDefinition(
                    "composition_target:pc-glass-clear",
                    "maximize",
                    "score_0_1",
                )
            ],
            pareto=[record],
            leaderboard=[record],
            winner=record,
            schema=RecipeSchema(),
            failure_counts={},
        )


def test_tap_truncated_leaderboard_uses_tap_hour_coating_summary(tmp_path) -> None:
    spec = _scope_spec()
    scored = ScoredResult(
        candidate_id="tap-row",
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue(
                    "composition_target:pc-glass-clear",
                    "maximize",
                    1.0,
                    "score_0_1",
                    ordinal=0,
                ),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            product_summary={
                "campaigns_to_resinter": "resinter_threshold_kg / 100",
                "wall_deposit_kg_by_segment_species": {
                    "stage_1_to_stage_2": {"SiO": 100.0}
                },
                "wall_deposit_kg_by_zone_species": {"Hot": {"SiO": 100.0}},
            },
            trace={
                "backend_status": "diagnostic_stub",
                "composition_target": {
                    "tap_provenance": "tap_truncated",
                    "tap_hour": 1,
                    "configured_hours": 2,
                    "operator_instruction": {
                        "tap_hour": 1,
                        "configured_hours": 2,
                        "configured_campaign": "C0b_p_cleanup",
                        "phase_at_tap": "C0B",
                    },
                    "tap_coating_product_summary": {
                        "campaigns_to_resinter": "resinter_threshold_kg / 0.001",
                        "wall_deposit_kg_by_segment_species": {
                            "stage_1_to_stage_2": {"SiO": 0.001}
                        },
                        "wall_deposit_kg_by_zone_species": {"Hot": {"SiO": 0.001}},
                    },
                },
            },
            backend_status="diagnostic_stub",
        ),
    )
    record = study._to_record(
        study.Candidate(id="tap-row", patch=RecipePatch({})),
        scored,
        cache_hit=False,
    )
    assert record.product_summary["campaigns_to_resinter"] == "resinter_threshold_kg / 0.001"
    assert record.product_summary["wall_deposit_kg_by_segment_species"] == {
        "stage_1_to_stage_2": {"SiO": 0.001}
    }
    assert record.product_summary["wall_deposit_kg_by_zone_species"] == {
        "Hot": {"SiO": 0.001}
    }

    study._write_leaderboard(
        tmp_path / "leaderboard.csv",
        [record],
        [record],
        record,
        [study.ObjectiveDefinition("composition_target:pc-glass-clear", "maximize", "score_0_1")],
        RecipeSchema(),
        profile=PROFILE,
    )

    row = next(csv.DictReader((tmp_path / "leaderboard.csv").open()))
    assert row["campaigns_to_resinter"] == "resinter_threshold_kg / 0.001"
    assert json.loads(row["wall_deposit_kg_by_segment_species_json"]) == {
        "stage_1_to_stage_2": {"SiO": 0.001}
    }
    assert json.loads(row["wall_deposit_kg_by_zone_species_json"]) == {
        "Hot": {"SiO": 0.001}
    }
    assert "100" not in row["wall_deposit_kg_by_segment_species_json"]
    pareto = study._pareto_payload(
        PROFILE,
        FEEDSTOCK,
        "stub",
        [study.ObjectiveDefinition("composition_target:pc-glass-clear", "maximize", "score_0_1")],
        [record],
        record,
        RecipeSchema(),
    )
    assert pareto["pareto"][0]["product_summary"]["campaigns_to_resinter"] == (
        "resinter_threshold_kg / 0.001"
    )
    assert pareto["pareto"][0]["product_summary"]["wall_deposit_kg_by_zone_species"] == {
        "Hot": {"SiO": 0.001}
    }


def test_tap_truncated_partial_coating_projection_fails_loud() -> None:
    spec = _scope_spec()
    scored = ScoredResult(
        candidate_id="tap-partial",
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue(
                    "composition_target:pc-glass-clear",
                    "maximize",
                    1.0,
                    "score_0_1",
                    ordinal=0,
                ),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            product_summary={
                "campaigns_to_resinter": "resinter_threshold_kg / 100",
                "wall_deposit_kg_by_segment_species": {
                    "stage_1_to_stage_2": {"SiO": 100.0}
                },
                "wall_deposit_kg_by_zone_species": {"Hot": {"SiO": 100.0}},
            },
            trace={
                "backend_status": "diagnostic_stub",
                "composition_target": {
                    "tap_provenance": "tap_truncated",
                    "tap_hour": 1,
                    "configured_hours": 2,
                    "tap_coating_product_summary": {
                        "campaigns_to_resinter": "resinter_threshold_kg / 0.001",
                    },
                },
            },
            backend_status="diagnostic_stub",
        ),
    )

    with pytest.raises(study.StudyAbort, match="wall_deposit_kg_by_segment_species"):
        study._to_record(
            study.Candidate(id="tap-partial", patch=RecipePatch({})),
            scored,
            cache_hit=False,
        )


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


def test_out_of_domain_candidate_is_stored_and_study_continues(tmp_path) -> None:
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        3,
        tmp_path,
        seed=7,
        evaluator=_evaluator(out_of_domain={1}),
    )

    assert result.winner.candidate_id == "random-7-000002"
    pareto = json.loads((tmp_path / "pareto.json").read_text())
    provenance = _read_provenance(tmp_path)
    logged = {row["candidate_id"]: row for row in provenance}
    stored = {row.candidate_id: row for row in _stored_rows(tmp_path)}

    assert pareto["failure_counts"] == {"out_of_domain": 1}
    assert logged["random-7-000001"]["status"] == "out_of_domain"
    assert logged["random-7-000001"]["failure_category"] == "out_of_domain"
    assert logged["random-7-000001"]["objectives"] == {}
    assert logged["random-7-000001"]["feasibility_margins"]["backend_domain"]["observed"] == 0.0
    trace_summary = logged["random-7-000001"]["trace_summary"]
    assert trace_summary["out_of_domain_crash_point"]["temperature_C"] == pytest.approx(865.0)
    assert trace_summary["out_of_domain_crash_point"]["composition_mol"]["CaO"] == pytest.approx(1.0)
    assert trace_summary["rump_terminal"]["liquid_fraction"] == pytest.approx(0.5)
    assert trace_summary["terminal_rump_by_species_kg"] == {"CaO": 2.0}
    assert "snapshots" not in trace_summary
    assert stored["random-7-000001"].failure_category is FailureCategory.OUT_OF_DOMAIN
    assert stored["random-7-000001"].run_reference is not None
    stored_trace = stored["random-7-000001"].run_reference.trace
    assert stored_trace["out_of_domain_crash_point"]["fO2_log"] == pytest.approx(-9.0)
    assert "snapshots" not in stored_trace


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


def test_all_out_of_domain_raises_no_feasible_and_logs_count(tmp_path) -> None:
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
            evaluator=_evaluator(out_of_domain={0, 1, 2}),
        )

    pareto_payload = json.loads((tmp_path / "pareto.json").read_text())
    assert pareto_payload["pareto"] == []
    assert pareto_payload["winner_candidate_id"] is None
    assert pareto_payload["failure_counts"] == {"out_of_domain": 3}
    assert len(_read_provenance(tmp_path)) == 3
    stored = _stored_rows(tmp_path)
    assert len(stored) == 3
    assert {row.failure_category for row in stored} == {FailureCategory.OUT_OF_DOMAIN}
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


def test_nonfinite_payload_result_continues_and_counts_failure(tmp_path) -> None:
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        3,
        tmp_path,
        seed=7,
        evaluator=_evaluator(non_finite_payload={0}),
    )

    assert result.winner is not None
    records = _read_provenance(tmp_path)
    assert len(records) == 3
    assert records[0]["failure_category"] == "non_finite_payload"
    summary = json.loads((tmp_path / "pareto.json").read_text())
    assert summary["failure_counts"] == {"non_finite_payload": 1}
    stored = _stored_rows(tmp_path)
    assert any(
        row.failure_category is FailureCategory.NON_FINITE_PAYLOAD
        for row in stored
    )


def test_invalid_recipe_result_continues_and_counts_failure(tmp_path) -> None:
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        3,
        tmp_path,
        seed=7,
        evaluator=_evaluator(invalid_recipe={0}),
    )

    assert result.winner is not None
    records = _read_provenance(tmp_path)
    assert len(records) == 3
    assert records[0]["failure_category"] == "invalid_recipe"
    summary = json.loads((tmp_path / "pareto.json").read_text())
    assert summary["failure_counts"] == {"invalid_recipe": 1}
    stored = _stored_rows(tmp_path)
    assert any(
        row.failure_category is FailureCategory.INVALID_RECIPE
        for row in stored
    )


def test_all_invalid_recipe_results_fail_loud_with_counts(tmp_path) -> None:
    with pytest.raises(
        study.StudyNoFeasibleError,
        match=r"failure_counts=.*invalid_recipe",
    ):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "random",
            "stub",
            1,
            2,
            tmp_path,
            seed=7,
            evaluator=_evaluator(invalid_recipe={0, 1}),
        )

    summary = json.loads((tmp_path / "pareto.json").read_text())
    assert summary["failure_counts"] == {"invalid_recipe": 2}
    assert not (tmp_path / "winner.recipe.yaml").exists()


def test_all_nonfinite_payload_results_fail_loud_with_counts(tmp_path) -> None:
    with pytest.raises(
        study.StudyNoFeasibleError,
        match=r"all candidates failed with non_finite_payload.*failure_counts",
    ):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "random",
            "stub",
            1,
            2,
            tmp_path,
            seed=7,
            evaluator=_evaluator(non_finite_payload={0, 1}),
        )

    summary = json.loads((tmp_path / "pareto.json").read_text())
    assert summary["failure_counts"] == {"non_finite_payload": 2}
    assert not (tmp_path / "winner.recipe.yaml").exists()


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
