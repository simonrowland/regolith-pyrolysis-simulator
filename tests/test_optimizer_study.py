from __future__ import annotations

import copy
import csv
from dataclasses import is_dataclass, fields, replace
import json
import logging
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
import yaml

import scripts.make_recipe_db_profile as generator
from simulator.optimize import cli as optimizer_cli
from simulator.optimize import physics as physics_module
from simulator.optimize import study
from simulator.optimize.evalspec import EvalSpec, cache_key
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult, _build_eval_inputs
from simulator.optimize.evaluate import evaluate
from simulator.optimize.objective import (
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    LEGACY_ENERGY_KWH_METRIC,
    ObjectiveValue,
    ObjectiveVector,
    compute_objectives,
)
from simulator.optimize.physics import GateMargin, PhysicsConstraintSet, ThresholdSpec
from simulator.optimize.physics import physics_constraints_digest
from simulator.optimize.profiles import constrained_max_profile, physics_constraints_from_profile
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.optimize.results_store import ResultStore
from simulator.optimize.strategy import (
    Candidate,
    MorrisScreenStrategy,
    OptunaNSGA2Strategy,
    OptunaTPEStrategy,
    RandomStrategy,
    StagedStrategy,
)


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
            "metric": ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
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


def _write_cli_physics_smoke_profile(tmp_path: Path) -> Path:
    profile = copy.deepcopy(PROFILE)
    profile["profile_id"] = "cli-physics-smoke"
    profile["constraints"] = {
        "gates": ["furnace_temperature"],
        "furnace_T_max_C": 1800.0,
    }
    path = tmp_path / "cli-physics-smoke.yaml"
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    return path


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


def _assert_candidate_pressure_pairs_valid(candidates: list[Any]) -> None:
    schema = RecipeSchema()
    c2a_pairs = schema.C2A_STAGED_STAGE_PRESSURE_TOTAL_BY_PO2
    pressure_pairs = tuple(schema.PRESSURE_COUPLED_DEFAULT_PAIRS) + tuple(
        c2a_pairs.items()
    )
    for candidate in candidates:
        for po2_path, total_path in pressure_pairs:
            if po2_path not in candidate.patch.values or total_path not in candidate.patch.values:
                continue
            po2 = candidate.patch.values[po2_path]
            total = candidate.patch.values[total_path]
            if po2_path in c2a_pairs:
                mode_path = po2_path[:-1] + ("gas_cover_mode",)
                mode = candidate.patch.values.get(mode_path, "pn2_sweep")
                if mode == "pn2_sweep":
                    assert po2 < total, (
                        candidate.id,
                        ".".join(po2_path),
                        po2,
                        ".".join(total_path),
                        total,
                        ".".join(mode_path),
                        mode,
                    )
                    continue
            assert po2 <= total, (
                candidate.id,
                ".".join(po2_path),
                po2,
                ".".join(total_path),
                total,
            )


def _pressure_feasible_scored(candidate: Any) -> ScoredResult:
    spec = _spec(candidate.patch, FEEDSTOCK, "stub", PROFILE)
    objectives = ObjectiveVector(
        (
            ObjectiveValue("oxygen_kg", "maximize", 10.0, "kg", ordinal=0),
            ObjectiveValue("energy_kWh", "minimize", 2.0, "kWh", ordinal=1),
        )
    )
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=objectives,
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(status="ok", trace={"backend_status": "ok"}),
    )


def _joint_refine_pressure_strategy(schema: RecipeSchema) -> StagedStrategy:
    profile = {
        **PROFILE,
        "staged": {
            "beam_width": 1,
            "children_per_parent": 4,
            "allowlist": ("C2A_staged",),
            "joint_refine": True,
            "max_backward_passes": 0,
            "topology": {"path_ab": "A_staged", "branch": "two", "c6": False},
        },
    }
    strategy = StagedStrategy(schema, seed=0, objective_profile=profile)
    while True:
        candidates = strategy.ask(64)
        if not candidates:
            break
        strategy.tell([(candidate, _pressure_feasible_scored(candidate)) for candidate in candidates])
    assert strategy.joint_refine() is True
    return strategy


@pytest.mark.parametrize(
    ("name", "factory", "draws"),
    (
        ("random", lambda schema: RandomStrategy(schema, seed=23), 128),
        (
            "morris",
            lambda schema: MorrisScreenStrategy(schema, seed=23, num_trajectories=16),
            10_000,
        ),
        (
            "tpe",
            lambda schema: OptunaTPEStrategy(
                schema,
                seed=23,
                objective_profile=PROFILE,
            ),
            64,
        ),
        (
            "nsga2",
            lambda schema: OptunaNSGA2Strategy(
                schema,
                seed=23,
                objective_profile=PROFILE,
            ),
            64,
        ),
        ("staged_joint_refine", _joint_refine_pressure_strategy, 64),
    ),
)
def test_strategy_ask_paths_emit_pressure_feasible_candidates(
    name: str,
    factory: Any,
    draws: int,
) -> None:
    schema = RecipeSchema()
    try:
        strategy = factory(schema)
    except ImportError as exc:
        pytest.skip(str(exc))
    count = min(draws, getattr(strategy, "plan_length", draws))

    candidates = strategy.ask(count)

    assert candidates, name
    _assert_candidate_pressure_pairs_valid(candidates)


def _stale_melt_target_profile() -> dict[str, Any]:
    return {
        **PROFILE,
        "profile_id": "stale-melt-target-profile",
        "constraints": {"gates": ["delivered_stream_purity"]},
        "objectives": [
            {
                "type": "composition_target",
                "id": "stale-profile-target",
                "metric": "composition_target:stale-profile-target",
                "sense": "maximize",
                "units": "score_0_1",
                "weight": 1.0,
                "rationale": "test stale-profile refusal",
                "target": {
                    "pool": "residual_rump_at_stop",
                    "species_vector": {"Ca": "retain"},
                    "composition_window": {
                        "pool": "residual_rump_at_stop",
                        "basis": "oxide_wt_pct",
                        "mode": "hard_window",
                        "oxides": {"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
                    },
                    "maturity": {"mode": "campaign_hours", "campaign": "C2B", "hours": 24},
                    "constraints": {"furnace_T_max_C": "profile_or_study_constraint"},
                    "score_weights": {"extraction": 0.0, "composition": 1.0},
                },
            }
        ],
    }


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


def _sso2_objective_profile(profile_id: str) -> dict[str, Any]:
    return {
        **PROFILE,
        "profile_id": profile_id,
        "objectives": [
            {
                "metric": "sso2_pn2_fe_drain_silica",
                "sense": "maximize",
                "units": "score_0_1",
                "weight": 1.0,
                "rationale": "test SSO-2 evidence projection",
            }
        ],
    }


def test_sso2_objective_evidence_projects_reader_failure_without_field_collision(tmp_path) -> None:
    metric = "sso2_pn2_fe_drain_silica"
    profile = _sso2_objective_profile("sso2-study-test")
    spec = _spec(RecipePatch({}), FEEDSTOCK, "stub", profile)
    evidence = {
        "reader": metric,
        "status": "wall_coating_failed",
        "status_reason": "wall coating failed",
        "consumed_fields": (
            "delivered_stream_purity.margin",
            "fe_tap.Fe_kg",
        ),
        "score": 0.0,
        "evidence": {
            "status": "available",
            "status_reason": "",
            "certified_sso_r_surface": {
                "dose_species": "Na",
                "declared_pN2_mbar": 99.99,
                "source": "quoted \"surface\", line\nnext",
            },
            "fe_tap": {
                "status": "available",
                "status_reason": "",
            },
            "wall_coating": {
                "status": "wall_coating_failed",
                "status_reason": "liner failed",
            },
        },
    }
    scored = ScoredResult(
        candidate_id="sso2-candidate",
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (ObjectiveValue(metric, "maximize", 0.0, "score_0_1", ordinal=0),),
            evidence={metric: evidence},
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        failing_gates=(),
        run_reference=RunReference(
            status="ok",
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "snapshots": [{"mass_balance_error_pct": 0.0}],
            },
        ),
    )

    record = study._to_record(
        Candidate("sso2-candidate", RecipePatch({})),
        scored,
        cache_hit=True,
    )
    summary = record.trace_summary["sso2_objective_evidence"]
    assert summary["status"] == "wall_coating_failed"
    assert summary["status_reason"] == "wall coating failed"
    assert summary["evidence_status"] == "available"
    assert summary["consumed_fields"] == [
        "delivered_stream_purity.margin",
        "fe_tap.Fe_kg",
    ]
    assert summary["certified_sso_r_surface"]["dose_species"] == "Na"

    leaderboard_path = tmp_path / "leaderboard.csv"
    study._write_leaderboard(
        leaderboard_path,
        (record,),
        (record,),
        record,
        study.objective_definitions(profile),
        RecipeSchema(),
        profile=profile,
    )
    row = next(csv.DictReader(leaderboard_path.open(encoding="utf-8")))

    assert row["sso2_reader_status"] == "wall_coating_failed"
    assert row["sso2_reader_status_reason"] == "wall coating failed"
    assert json.loads(row["sso2_consumed_fields_json"]) == [
        "delivered_stream_purity.margin",
        "fe_tap.Fe_kg",
    ]
    certified_surface = json.loads(row["sso2_certified_surface_json"])
    assert certified_surface["dose_species"] == "Na"
    assert certified_surface["source"] == "quoted \"surface\", line\nnext"


class _Sso2AbsenceLedger:
    registry = {}
    transitions = (SimpleNamespace(name="native_fe_saturation_split"),)

    def kg_by_account(self, account: str | None = None) -> dict[str, dict[str, float]] | dict[str, float]:
        balances = {
            "terminal.drain_tap_material": {},
            "process.metal_phase": {"Fe": 1.0},
        }
        if account is None:
            return {key: dict(value) for key, value in balances.items()}
        return dict(balances.get(account, {}))


class _Sso2AbsenceSim:
    def __init__(self, snapshots: tuple[Any, ...]) -> None:
        self.atom_ledger = _Sso2AbsenceLedger()
        self.species_formula_registry = {}
        self.train = SimpleNamespace(
            stages=(
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={"SiO": 1.0}),
            )
        )
        self.record = SimpleNamespace(
            feedstock_key=FEEDSTOCK,
            batch_mass_kg=1000.0,
            additives_kg={},
            snapshots=snapshots,
            total_hours=1,
        )
        self.melt = SimpleNamespace(hour=1)
        self.energy_electrical_plus_evaporation_cumulative_kWh = 1.0

    def product_ledger(self) -> dict[str, float]:
        return {}

    def _terminal_rump_by_species(self) -> dict[str, float]:
        return {}

    def _oxygen_terminal_partition_kg(self) -> dict[str, float]:
        return {"stored": 0.0, "vented": 0.0, "total": 0.0}


def _sso2_absent_tap_run_execution() -> SimpleNamespace:
    native_partition = {
        "native_fe_pool_mol": 1.0,
        "native_fe_tap_mol": 1.0,
        "native_fe_vapor_mol": 0.0,
    }
    snapshot = SimpleNamespace(
        hour=1,
        mass_balance_error_pct=0.0,
        fe_redox_split={"native_fe_partition": native_partition},
    )
    snapshots = (snapshot,)
    trace = SimpleNamespace(
        snapshots=snapshots,
        condensed_by_stage_species_delta=({(3, "SiO"): 1.0},),
        wall_deposit_by_segment_species_delta=({},),
        wall_zone_by_segment={},
    )
    return SimpleNamespace(
        simulator=_Sso2AbsenceSim(snapshots),
        snapshots=snapshots,
        trace=trace,
    )


def _stored_sso2_record(
    tmp_path: Path,
    *,
    profile_id: str,
    objectives: ObjectiveVector,
) -> study.StudyRecord:
    profile = _sso2_objective_profile(profile_id)
    spec = _spec(RecipePatch({}), FEEDSTOCK, "stub", profile)
    scored = ScoredResult(
        candidate_id=f"{profile_id}-candidate",
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=objectives,
        feasibility_margins={"delivered_stream_purity": _margin()},
        failing_gates=(),
        run_reference=RunReference(
            status="ok",
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "snapshots": [{"mass_balance_error_pct": 0.0}],
            },
        ),
    )
    store = ResultStore(
        tmp_path / f"{profile_id}.sqlite",
        current_code_version=spec.code_version,
        current_data_digests=spec.data_digests,
    )
    store.store(spec, scored, created_at="2026-07-06T00:00:00Z")
    loaded = store.fetch(cache_key(spec))
    assert loaded is not None
    return study._to_record(
        Candidate(f"{profile_id}-candidate", RecipePatch({})),
        loaded,
        cache_hit=True,
    )


def test_sso2_missing_tap_evidence_chains_through_store_to_leaderboard(tmp_path) -> None:
    metric = "sso2_pn2_fe_drain_silica"
    profile = _sso2_objective_profile("sso2-missing-tap")
    objectives = compute_objectives(profile, _sso2_absent_tap_run_execution())
    assert objectives.evidence[metric]["status"] == "missing_fe_tap_evidence"

    record = _stored_sso2_record(
        tmp_path,
        profile_id="sso2-missing-tap",
        objectives=objectives,
    )
    summary = record.trace_summary["sso2_objective_evidence"]
    assert summary["status"] == "missing_fe_tap_evidence"
    assert "terminal.drain_tap_material kg evidence is absent" in summary["status_reason"]
    assert summary["fe_tap"]["Fe_kg"] is None

    leaderboard_path = tmp_path / "missing-tap-leaderboard.csv"
    study._write_leaderboard(
        leaderboard_path,
        (record,),
        (record,),
        record,
        study.objective_definitions(profile),
        RecipeSchema(),
        profile=profile,
    )
    row = next(csv.DictReader(leaderboard_path.open(encoding="utf-8")))
    assert row["sso2_reader_status"] == "missing_fe_tap_evidence"
    assert "terminal.drain_tap_material kg evidence is absent" in row[
        "sso2_reader_status_reason"
    ]


def test_sso2_legacy_row_without_evidence_reads_as_evidence_absent(tmp_path) -> None:
    metric = "sso2_pn2_fe_drain_silica"
    profile = _sso2_objective_profile("sso2-legacy-no-evidence")
    record = _stored_sso2_record(
        tmp_path,
        profile_id="sso2-legacy-no-evidence",
        objectives=ObjectiveVector(
            (ObjectiveValue(metric, "maximize", 0.25, "score_0_1", ordinal=0),)
        ),
    )
    summary = record.trace_summary["sso2_objective_evidence"]
    assert summary == {
        "reader": metric,
        "status": "evidence_absent",
        "status_reason": "stored objective row has no SSO-2 evidence payload",
        "consumed_fields": [],
    }

    leaderboard_path = tmp_path / "legacy-leaderboard.csv"
    study._write_leaderboard(
        leaderboard_path,
        (record,),
        (record,),
        record,
        study.objective_definitions(profile),
        RecipeSchema(),
        profile=profile,
    )
    row = next(csv.DictReader(leaderboard_path.open(encoding="utf-8")))
    assert row["sso2_reader_status"] == "evidence_absent"
    assert row["sso2_certified_surface_json"] == "{}"


def _evaluator(
    *,
    infeasible: set[int] | None = None,
    engine_bug: set[int] | None = None,
    out_of_domain: set[int] | None = None,
    earned_rump_ood: set[int] | None = None,
    non_finite_payload: set[int] | None = None,
    invalid_recipe: set[int] | None = None,
):
    bad = infeasible or set()
    aborts = engine_bug or set()
    domain_rejects = out_of_domain or set()
    earned_rump_domain = earned_rump_ood or set()
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
        if index in earned_rump_domain:
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
                    trace={
                        "backend_status": "out_of_domain",
                        "backend_authoritative": True,
                        "backend_status_reason": "earned_terminal_rump_out_of_domain",
                        "rump_terminal": {"status": "earned"},
                        "terminal_rump_by_species_kg": {"CaO": 2.0},
                        "snapshots": [{"mass_balance_error_pct": 0.0}],
                    },
                    product_summary={
                        "oxygen_kg": 10.0 + index,
                        "mass_closure": {
                            "status": "closed",
                            "mass_balance_error_pct": 0.0,
                        },
                    },
                    backend_status="out_of_domain",
                    backend_authoritative=True,
                    backend_status_reason="earned_terminal_rump_out_of_domain",
                ),
                notes=("backend_status=out_of_domain",),
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
                trace={
                    "backend_status": "ok",
                    "backend_authoritative": True,
                    "snapshots": [{"mass_balance_error_pct": 0.0}],
                },
                product_summary={
                    "oxygen_kg": 10.0 + index,
                    "mass_closure": {
                        "status": "closed",
                        "mass_balance_error_pct": 0.0,
                    },
                },
                backend_status="ok",
                backend_authoritative=True,
            ),
        )

    return evaluate_patch


def _seed_safe_certified_evaluator(
    patch: RecipePatch,
    feedstock: str,
    fidelity: str,
    *,
    profile: Mapping[str, Any],
    candidate_id: str | None = None,
    **kwargs: Any,
) -> ScoredResult:
    spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
    seed_bonus = 100.0 if candidate_id and "seed" in candidate_id else 0.0
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", 10.0 + seed_bonus, "kg", ordinal=0),
                ObjectiveValue("energy_kWh", "minimize", 5.0, "kWh", ordinal=1),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "per_hour_summary": [{"reduced_real_cache_state": "cached_exact"}],
                "snapshots": [{"mass_balance_error_pct": 0.0}],
            },
            product_summary={
                "oxygen_kg": 10.0 + seed_bonus,
                "mass_closure": {
                    "status": "closed",
                    "mass_balance_error_pct": 0.0,
                },
            },
            backend_status="ok",
            backend_authoritative=True,
        ),
    )


def _write_prior_warm_start_run(
    out_dir: Path,
    patch: RecipePatch,
    *,
    profile_payload: Mapping[str, Any] = PROFILE,
    candidate_id: str = "prior-winner",
) -> ScoredResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    schema = RecipeSchema()
    profile = study.resolve_profile(
        profile_payload,
        expected_feedstock=FEEDSTOCK,
        schema=schema,
    )
    scored = _seed_safe_certified_evaluator(
        patch.validated(schema),
        FEEDSTOCK,
        "stub",
        profile=profile,
        candidate_id=candidate_id,
        constraints=study._constraints_for_profile(profile),
    )
    ResultStore(out_dir / "cache.sqlite").store(
        scored.eval_spec,
        scored,
        created_at="2026-07-07T00:00:00Z",
    )
    record = study._to_record(
        Candidate(
            id=candidate_id,
            patch=patch,
            metadata={"proposal_source": "sobol", "strategy": "test"},
        ),
        scored,
        cache_hit=False,
    )
    study._write_artifacts(
        out_dir,
        profile=profile,
        feedstock=FEEDSTOCK,
        fidelity="stub",
        definitions=study.objective_definitions(PROFILE),
        pareto=(record,),
        leaderboard=(record,),
        winner=record,
        schema=schema,
        failure_counts={},
        search_space_identity=study._search_space_identity(
            schema,
            profile_pinned_paths=(),
            cli_pinned_paths=(),
        ),
    )
    return scored


def _slow_first_then_ok_evaluator(
    patch: RecipePatch,
    feedstock: str,
    fidelity: str,
    *,
    profile: Mapping[str, Any],
    candidate_id: str | None = None,
    **kwargs: Any,
) -> ScoredResult:
    if _sequence(candidate_id) == 0:
        time.sleep(20.0)
    return _evaluator()(patch, feedstock, fidelity, profile=profile, candidate_id=candidate_id, **kwargs)


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


class _FixedCandidateStrategy:
    name = "fixed"
    seed = 0

    def __init__(self, candidates: tuple[Candidate, ...]) -> None:
        self._pending = list(candidates)

    def ask(self, n: int) -> list[Candidate]:
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
        self.energy_electrical_plus_evaporation_cumulative_kWh = 1.0

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
    assert [float(row[ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC]) for row in leaderboard] == [
        7.0,
        6.0,
        5.0,
    ]
    assert [float(row["margin_delivered_stream_purity"]) for row in leaderboard] == [1.0, 1.0, 1.0]
    assert len(_stored_rows(tmp_path)) == 3

    loaded = yaml.safe_load((tmp_path / "winner.recipe.yaml").read_text())
    assert RecipePatch.from_nested(loaded).validated(RecipeSchema())


def test_default_profile_leaderboard_uses_scoped_energy_metric_and_legacy_alias(
    tmp_path,
) -> None:
    profile = study.resolve_profile(
        study.DEFAULT_PROFILE_NAME,
        expected_feedstock=FEEDSTOCK,
    )
    definitions = study.objective_definitions(profile)
    assert [definition.metric for definition in definitions] == [
        "oxygen_kg",
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
        "duration_h",
    ]
    record = study.StudyRecord(
        candidate_id="legacy-cache-row",
        patch=RecipePatch({}),
        feasible=True,
        status="ok",
        objectives={
            "oxygen_kg": 1.0,
            LEGACY_ENERGY_KWH_METRIC: 2.0,
            "duration_h": 3.0,
        },
        feasibility_margins={},
        cache_key="legacy-cache-key",
    )

    leaderboard_path = tmp_path / "leaderboard.csv"
    study._write_leaderboard(
        leaderboard_path,
        [record],
        [record],
        record,
        definitions,
        RecipeSchema(),
        profile=profile,
    )

    with leaderboard_path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        row = next(reader)
        assert reader.fieldnames is not None
        assert ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC in reader.fieldnames
        assert LEGACY_ENERGY_KWH_METRIC not in reader.fieldnames
    assert float(row[ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC]) == pytest.approx(2.0)


def _strategy_for_legacy_cache_hit(
    name: str,
    schema: RecipeSchema,
    profile: Mapping[str, Any],
) -> Any:
    if name == "bayes":
        return OptunaTPEStrategy(schema, seed=916, objective_profile=profile)
    if name == "nsga2":
        return OptunaNSGA2Strategy(schema, seed=916, objective_profile=profile)
    if name == "screen":
        return MorrisScreenStrategy(schema, seed=916, num_trajectories=1)
    raise AssertionError(f"unknown strategy {name!r}")


def _legacy_energy_cached_scored(
    candidate: Candidate,
    profile: Mapping[str, Any],
    constraints: Any,
    *,
    index: int,
    energy_metric: str,
) -> ScoredResult:
    spec = _spec(candidate.patch, FEEDSTOCK, "stub", profile, constraints)
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", 10.0 + index, "kg", ordinal=0),
                ObjectiveValue(
                    energy_metric,
                    "minimize",
                    2.0 + index,
                    "kWh",
                    ordinal=1,
                ),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "snapshots": [{"mass_balance_error_pct": 0.0}],
            },
            backend_status="ok",
            backend_authoritative=True,
            product_summary={
                "mass_closure": {
                    "status": "closed",
                    "mass_balance_error_pct": 0.0,
                }
            },
        ),
    )


@pytest.mark.parametrize("strategy_name", ["bayes", "nsga2", "screen"])
def test_strategy_cache_hit_legacy_energy_scores_against_canonical_profile(
    tmp_path,
    strategy_name: str,
) -> None:
    profile = copy.deepcopy(PROFILE)
    schema = RecipeSchema()
    constraints = physics_constraints_from_profile(profile)
    preview_strategy = _strategy_for_legacy_cache_hit(strategy_name, schema, profile)
    budget = (
        preview_strategy.plan_length
        if isinstance(preview_strategy, MorrisScreenStrategy)
        else 1
    )
    candidates = preview_strategy.ask(budget)
    store = ResultStore(tmp_path / f"{strategy_name}-cache.sqlite")
    for index, candidate in enumerate(candidates):
        energy_metric = (
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC
            if strategy_name == "screen" and index % 2 == 0
            else LEGACY_ENERGY_KWH_METRIC
        )
        scored = _legacy_energy_cached_scored(
            candidate,
            profile,
            constraints,
            index=index,
            energy_metric=energy_metric,
        )
        store.store(
            scored.eval_spec,
            scored,
            created_at="2026-07-07T00:00:00+00:00",
        )

    def cache_miss_evaluator(*args: Any, **kwargs: Any) -> ScoredResult:
        raise AssertionError("expected cached optimizer result")

    active_strategy = _strategy_for_legacy_cache_hit(strategy_name, schema, profile)
    result = study.run(
        profile,
        FEEDSTOCK,
        active_strategy,
        "stub",
        parallel=budget,
        budget=budget,
        out_dir=tmp_path / f"{strategy_name}-out",
        evaluator=cache_miss_evaluator,
        schema=schema,
        result_store=store,
        constraints=constraints,
    )

    assert len(result.records) == budget
    assert all(record.cache_hit for record in result.records)
    if strategy_name == "screen":
        assert active_strategy.screen_result().completed_trajectories == 1
    else:
        trial = active_strategy.study.trials[0]
        assert trial.state.name == "COMPLETE"
        assert trial.values == [10.0, 2.0]


def test_study_applies_profile_and_cli_pins_to_strategy_search(tmp_path) -> None:
    profile = copy.deepcopy(PROFILE)
    profile["pinned_paths"] = ["C2A_staged.stages.alkali_early_fe.target_C"]
    profile_pin = (
        "campaigns",
        "C2A_staged",
        "stages",
        "alkali_early_fe",
        "target_C",
    )
    cli_pin = (
        "campaigns",
        "C2A_staged",
        "stages",
        "sio_window",
        "target_C",
    )
    searched_pressure = ("campaigns", "C2A_staged", "p_total_mbar")
    searched_ramp = (
        "campaigns",
        "C2A_staged",
        "stages",
        "sio_window",
        "ramp_rate_C_per_hr",
    )
    observed_log = tmp_path / "observed-paths.jsonl"
    base_evaluator = _evaluator()

    def evaluator(patch: RecipePatch, *args: Any, **kwargs: Any) -> ScoredResult:
        with observed_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps([list(path) for path in patch.values]) + "\n")
        return base_evaluator(patch, *args, **kwargs)

    study.run(
        profile,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        1,
        tmp_path,
        seed=7,
        evaluator=evaluator,
        pinned_paths=["C2A_staged.stages.sio_window.target_C"],
    )

    observed_paths = [
        {tuple(path) for path in json.loads(line)}
        for line in observed_log.read_text(encoding="utf-8").splitlines()
    ]
    assert observed_paths
    assert profile_pin not in observed_paths[0]
    assert cli_pin not in observed_paths[0]
    assert searched_pressure in observed_paths[0]
    assert searched_ramp in observed_paths[0]
    pareto = json.loads((tmp_path / "pareto.json").read_text(encoding="utf-8"))
    identity = pareto["search_space_identity"]
    assert identity["bounds_digest"] == RecipeSchema().bounds_digest
    assert identity["profile_pinned_paths"] == [
        "C2A_staged.stages.alkali_early_fe.target_C"
    ]
    assert identity["cli_pinned_paths"] == ["C2A_staged.stages.sio_window.target_C"]
    assert "campaigns.C2A_staged.stages.sio_window.target_C" in identity[
        "resolved_pinned_paths"
    ]
    provenance = _read_provenance(tmp_path)
    assert provenance[0]["search_space_identity"] == identity


def test_study_search_space_identity_tracks_schema_bounds_digest(tmp_path) -> None:
    base_schema = RecipeSchema()
    same_bounds_schema = RecipeSchema(allowlist=base_schema.allowlist)
    target = next(spec for spec in base_schema.allowlist if spec.high is not None)
    shifted_bounds_schema = RecipeSchema(
        allowlist=tuple(
            replace(spec, high=float(spec.high) + 1.0)
            if spec.path == target.path
            else spec
            for spec in base_schema.allowlist
        )
    )

    def persisted_identity(schema: RecipeSchema, out_dir: Path) -> Mapping[str, Any]:
        study.run(
            PROFILE,
            FEEDSTOCK,
            _SingleCandidateStrategy(),
            "stub",
            1,
            1,
            out_dir,
            seed=7,
            evaluator=_evaluator(),
            schema=schema,
        )
        pareto = json.loads((out_dir / "pareto.json").read_text(encoding="utf-8"))
        identity = pareto["search_space_identity"]
        assert _read_provenance(out_dir)[0]["search_space_identity"] == identity
        return identity

    base_identity = persisted_identity(base_schema, tmp_path / "base")
    same_identity = persisted_identity(same_bounds_schema, tmp_path / "same")
    shifted_identity = persisted_identity(shifted_bounds_schema, tmp_path / "shifted")

    assert base_identity["bounds_digest"] == base_schema.bounds_digest
    assert same_identity["bounds_digest"] == same_bounds_schema.bounds_digest
    assert shifted_identity["bounds_digest"] == shifted_bounds_schema.bounds_digest
    assert same_identity == base_identity
    assert shifted_identity["bounds_digest"] != base_identity["bounds_digest"]
    assert shifted_identity != base_identity


def test_warm_start_rejects_stale_search_space_identity(tmp_path) -> None:
    prior = tmp_path / "prior"
    current = tmp_path / "current"
    prior.mkdir()
    ResultStore(prior / "cache.sqlite")
    (prior / "pareto.json").write_text(
        json.dumps(
            {
                "search_space_identity": {"bounds_digest": "stale"},
                "pareto": [
                    {
                        "candidate_id": "old-winner",
                        "cache_key": "missing",
                        "patch": {"campaigns": {"C0": {"temp_range_C": [900, 950]}}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(study.StudyError, match="search_space_identity mismatch"):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "staged",
            "stub",
            parallel=1,
            budget=1,
            out_dir=current,
            evaluator=_evaluator(),
            warm_start_from=prior,
        )


def test_warm_start_from_prior_run_store_admits_real_seed(tmp_path) -> None:
    prior = tmp_path / "prior"
    current = tmp_path / "current"
    _write_prior_warm_start_run(
        prior,
        RecipePatch.from_nested(
            {"campaigns": {"C0": {"temp_range_C": [900.0, 940.0]}}}
        ),
    )

    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=1,
        budget=2,
        out_dir=current,
        seed=7,
        evaluator=_seed_safe_certified_evaluator,
        warm_start_from=prior,
    )
    search_provenance = json.loads(result.artifacts["search_provenance"].read_text())

    store_records = [
        record for record in result.records if record.proposal_source == "store_warm_start"
    ]
    assert store_records
    assert all(record.seed_lineage for record in store_records)
    assert search_provenance["proposal_source_counts"]["store_warm_start"] >= 1


def test_warm_start_rejects_corrupt_patch_recipe_identity(tmp_path) -> None:
    prior = tmp_path / "prior"
    current = tmp_path / "current"
    _write_prior_warm_start_run(
        prior,
        RecipePatch.from_nested(
            {"campaigns": {"C0": {"temp_range_C": [900.0, 940.0]}}}
        ),
    )
    pareto_path = prior / "pareto.json"
    payload = json.loads(pareto_path.read_text(encoding="utf-8"))
    payload["pareto"][0]["optimizer_patch"] = {
        "campaigns": {"C0": {"temp_range_C": [900.0, 950.0]}}
    }
    pareto_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(study.StudyError, match="recipe_id mismatch|cache_key mismatch"):
        study.run(
            PROFILE,
            FEEDSTOCK,
            "staged",
            "stub",
            parallel=1,
            budget=1,
            out_dir=current,
            seed=7,
            evaluator=_seed_safe_certified_evaluator,
            warm_start_from=prior,
        )


def test_profile_seed_epoch_stamp_mismatch_warns_but_reevaluates(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    profile = copy.deepcopy(PROFILE)
    profile["code_version"] = "stale-code"
    profile["corpus_version"] = "stale-corpus"

    with caplog.at_level(logging.WARNING, logger=study.__name__):
        result = study.run(
            profile,
            FEEDSTOCK,
            "staged",
            "stub",
            parallel=1,
            budget=1,
            out_dir=tmp_path,
            seed=7,
            evaluator=_seed_safe_certified_evaluator,
        )

    assert any(record.proposal_source == "seed_recipe" for record in result.records)
    messages = [record.getMessage() for record in caplog.records]
    assert any("profile_seed_epoch_warning" in message for message in messages)
    assert any("stale advisory seed_recipes" in message for message in messages)


def test_optuna_incomplete_warm_start_drop_counted_in_search_provenance(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("optuna")

    with caplog.at_level(logging.WARNING):
        result = study.run(
            PROFILE,
            FEEDSTOCK,
            "bayes",
            "stub",
            parallel=1,
            budget=1,
            out_dir=tmp_path,
            seed=7,
            evaluator=_evaluator(),
        )
    payload = json.loads(result.artifacts["search_provenance"].read_text())
    strategy_provenance = payload["strategy_provenance"]

    assert strategy_provenance["optuna_incomplete_seed_dropped_count"] == 1
    assert strategy_provenance["optuna_incomplete_seed_dropped_ids"] == [
        "study-c0-seed"
    ]
    assert any(
        "optuna_warm_start_seed_dropped" in record.getMessage()
        for record in caplog.records
    )


def test_evalspec_seed_identity_tracks_canonical_evalspec_fields() -> None:
    schema = RecipeSchema()
    patch = RecipePatch.from_nested(
        {"campaigns": {"C0": {"temp_range_C": [900.0, 950.0]}}}
    ).validated(schema)
    spec, _ = _build_eval_inputs(
        patch,
        FEEDSTOCK,
        "stub",
        PROFILE,
        schema,
        constraints=physics_constraints_from_profile(PROFILE),
    )
    rich_spec = replace(
        spec,
        stop_at_stage0_exit=True,
        stage0_redox_oxidant_kg=1.0,
        stage0_carbon_reductant_kg=1.0,
        o2_bubbler_settings={"O2": {"flow": 1.0}},
        allow_fallback_vapor=True,
        vapor_pressure_provider_code_fingerprint="fingerprint",
        lab_schedule={"campaigns": {"C0": {"operator": "test"}}},
        target_spec_id="target",
        target_spec_digest="target-digest",
        target_maturity={"level": "test"},
    )
    canonical_payload = json.loads(
        study.canonical_evalspec_json(rich_spec).decode("utf-8")
    )
    seed_identity = study._evalspec_seed_identity(rich_spec)
    intentionally_excluded: set[str] = set()

    assert set(seed_identity) == set(canonical_payload) - intentionally_excluded
    assert "recipe_id" in seed_identity


def test_study_records_seed_provenance_and_dual_winner_artifacts(tmp_path) -> None:
    def staged_id_evaluator(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        **kwargs: Any,
    ) -> ScoredResult:
        spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
        oxygen = 50.0 if candidate_id and "seed" in candidate_id else 10.0
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key=cache_key(spec),
            feasible=True,
            objectives=ObjectiveVector(
                (
                    ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
                    ObjectiveValue("energy_kWh", "minimize", 2.0, "kWh", ordinal=1),
                )
            ),
            feasibility_margins={"delivered_stream_purity": _margin()},
            run_reference=RunReference(
                status="ok",
                trace={"backend_status": "ok"},
                backend_status="ok",
                backend_authoritative=True,
            ),
        )

    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "staged",
        "stub",
        parallel=2,
        budget=2,
        out_dir=tmp_path / "run",
        evaluator=staged_id_evaluator,
    )

    assert any(record.proposal_source == "seed_recipe" for record in result.records)
    pareto = json.loads(result.artifacts["pareto"].read_text())
    search_provenance = json.loads(result.artifacts["search_provenance"].read_text())
    with result.artifacts["leaderboard"].open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))

    assert "best_overall_candidate_id" in pareto
    assert "best_non_seeded_lineage_candidate_id" in pareto
    assert search_provenance["proposal_source_counts"]["seed_recipe"] >= 1
    assert row["proposal_source"]
    assert row["seed_lineage"] in {"True", "False"}


def test_study_surfaces_knob_saturation_in_pareto_and_provenance(tmp_path) -> None:
    diagnostic = {
        "schema_version": "knob-saturation-v1",
        "tolerance_fraction": 0.01,
        "pinned_count": 1,
        "no_opposing_cost_pinned_count": 1,
        "red_flag": True,
        "knobs": [
            {
                "key": "campaigns.C0.temp_range_C[1]",
                "value": 950.0,
                "low": 20,
                "high": 950,
                "pinned": "high",
                "frac_of_range": 1.0,
                "kind": "float",
                "units": "C",
                "has_opposing_cost": False,
                "opposing_cost_metrics": [],
            }
        ],
    }
    base_evaluator = _evaluator()

    def evaluator(*args: Any, **kwargs: Any) -> ScoredResult:
        scored = base_evaluator(*args, **kwargs)
        assert scored.run_reference is not None
        trace = dict(scored.run_reference.trace or {})
        trace["knob_saturation"] = diagnostic
        return replace(
            scored,
            run_reference=replace(scored.run_reference, trace=trace),
        )

    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        3,
        tmp_path,
        seed=7,
        evaluator=evaluator,
    )

    pareto_payload = json.loads((tmp_path / "pareto.json").read_text())
    assert pareto_payload["winner_knob_saturation"] == diagnostic
    pareto_rows = {row["candidate_id"]: row for row in pareto_payload["pareto"]}
    assert (
        pareto_rows[result.winner.candidate_id]["trace_summary"]["knob_saturation"]
        == diagnostic
    )
    provenance_rows = {row["candidate_id"]: row for row in _read_provenance(tmp_path)}
    assert (
        provenance_rows[result.winner.candidate_id]["trace_summary"]["knob_saturation"]
        == diagnostic
    )

    header = (tmp_path / "leaderboard.csv").read_text().splitlines()[0]
    assert "knob_saturation" not in header


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
                "physics",
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


def _nullable_position_profile() -> dict[str, Any]:
    return {
        **PROFILE,
        "profile_id": "nullable-position-ranking",
        "objectives": [
            {
                "metric": "energy_kWh",
                "sense": "minimize",
                "units": "kWh",
                "weight": 0.4,
                "rationale": "primary nullable-position regression metric",
            },
            {
                "metric": "oxygen_kg",
                "sense": "maximize",
                "units": "kg",
                "weight": 0.3,
                "rationale": "middle nullable-position regression metric",
            },
            {
                "metric": "metals_total_kg",
                "sense": "maximize",
                "units": "kg",
                "weight": 0.3,
                "rationale": "last nullable-position regression metric",
            },
        ],
    }


def _nullable_position_values(candidate_id: str) -> tuple[float | None, float | None, float | None]:
    return {
        "nullable-baseline": (1.0, 1.0, 1.0),
        "nullable-primary": (None, 100.0, 0.0),
        "nullable-middle": (1.0, None, 100.0),
        "nullable-last": (1.0, 1.0, None),
    }[candidate_id]


def _nullable_position_candidates() -> tuple[Candidate, ...]:
    candidates: list[Candidate] = []
    for idx, candidate_id in enumerate(
        (
            "nullable-baseline",
            "nullable-primary",
            "nullable-middle",
            "nullable-last",
        )
    ):
        candidates.append(
            Candidate(
                id=candidate_id,
                patch=RecipePatch.from_nested(
                    {"campaigns": {"C0": {"temp_range_C": [900.0 + idx, 920.0 + idx]}}}
                ),
            )
        )
    return tuple(candidates)


def _nullable_position_objectives(
    candidate_id: str,
    definitions: tuple[study.ObjectiveDefinition, ...],
) -> ObjectiveVector:
    values = _nullable_position_values(candidate_id)
    return ObjectiveVector(
        tuple(
            ObjectiveValue(
                definition.metric,
                definition.sense,
                values[idx],
                definition.units,
                ordinal=definition.ordinal,
            )
            for idx, definition in enumerate(definitions)
        )
    )


def _nullable_position_record(candidate_id: str) -> study.StudyRecord:
    profile = _nullable_position_profile()
    definitions = study.objective_definitions(profile)
    values = _nullable_position_values(candidate_id)
    return study.StudyRecord(
        candidate_id=candidate_id,
        patch=RecipePatch({}),
        feasible=True,
        status="ok",
        objectives={
            definition.metric: values[idx]
            for idx, definition in enumerate(definitions)
        },
        feasibility_margins={},
        cache_key=f"cache-{candidate_id}",
    )


def _nullable_position_evaluator(
    patch: RecipePatch,
    feedstock: str,
    fidelity: str,
    *,
    profile: Mapping[str, Any],
    candidate_id: str | None = None,
    **kwargs: Any,
) -> ScoredResult:
    assert candidate_id is not None
    definitions = study.objective_definitions(profile)
    spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=_nullable_position_objectives(candidate_id, definitions),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "snapshots": [{"mass_balance_error_pct": 0.0}],
            },
            product_summary={
                "mass_closure": {
                    "status": "closed",
                    "mass_balance_error_pct": 0.0,
                }
            },
            backend_status="ok",
            backend_authoritative=True,
        ),
    )


def _pareto_certification_candidates() -> tuple[Candidate, ...]:
    candidates: list[Candidate] = []
    for idx, candidate_id in enumerate(("pareto-a", "pareto-b", "scalar-c")):
        candidates.append(
            Candidate(
                id=candidate_id,
                patch=RecipePatch.from_nested(
                    {"campaigns": {"C0": {"temp_range_C": [910.0 + idx, 930.0 + idx]}}}
                ),
            )
        )
    return tuple(candidates)


def _pareto_certification_values(candidate_id: str) -> tuple[float, float]:
    return {
        "pareto-a": (10.0, 10.0),
        "pareto-b": (8.0, 0.0),
        "scalar-c": (9.0, 9.0),
    }[candidate_id]


def _pareto_certification_evaluator(
    patch: RecipePatch,
    feedstock: str,
    fidelity: str,
    *,
    profile: Mapping[str, Any],
    candidate_id: str | None = None,
    **kwargs: Any,
) -> ScoredResult:
    assert candidate_id is not None
    oxygen, energy = _pareto_certification_values(candidate_id)
    spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
                ObjectiveValue("energy_kWh", "minimize", energy, "kWh", ordinal=1),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "snapshots": [{"mass_balance_error_pct": 0.0}],
            },
            product_summary={
                "mass_closure": {
                    "status": "closed",
                    "mass_balance_error_pct": 0.0,
                }
            },
            backend_status="ok",
            backend_authoritative=True,
        ),
    )


def _single_objective_tie_profile() -> dict[str, Any]:
    profile = copy.deepcopy(PROFILE)
    profile["objectives"] = [copy.deepcopy(PROFILE["objectives"][0])]
    return profile


def _single_objective_tie_candidates() -> tuple[Candidate, ...]:
    candidates: list[Candidate] = []
    for idx, candidate_id in enumerate(("tie-a", "tie-b", "lower-c")):
        candidates.append(
            Candidate(
                id=candidate_id,
                patch=RecipePatch.from_nested(
                    {"campaigns": {"C0": {"temp_range_C": [920.0 + idx, 940.0 + idx]}}}
                ),
            )
        )
    return tuple(candidates)


def _single_objective_tie_evaluator(
    patch: RecipePatch,
    feedstock: str,
    fidelity: str,
    *,
    profile: Mapping[str, Any],
    candidate_id: str | None = None,
    **kwargs: Any,
) -> ScoredResult:
    assert candidate_id is not None
    oxygen = {"tie-a": 10.0, "tie-b": 10.0, "lower-c": 9.0}[candidate_id]
    spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),)
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=RunReference(
            status="ok",
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "snapshots": [{"mass_balance_error_pct": 0.0}],
            },
            backend_status="ok",
            backend_authoritative=True,
        ),
    )


def test_two_phase_certification_pool_includes_pareto_beyond_scalar_top_k(
    tmp_path,
) -> None:
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        _FixedCandidateStrategy(_pareto_certification_candidates()),
        "stub",
        1,
        3,
        tmp_path / "two-phase-pareto-pool",
        evaluator=_pareto_certification_evaluator,
        two_phase_certify={"enabled": True, "top_k": 2},
    )
    certification = json.loads(
        (tmp_path / "two-phase-pareto-pool" / "two_phase_certification.json").read_text()
    )

    certified_ids = [row["candidate_id"] for row in certification["candidates"]]
    assert certified_ids == ["pareto-a", "scalar-c", "pareto-b"]
    assert "pareto-b" in {record.candidate_id for record in result.pareto}
    assert certification["top_k"] == 2
    assert certification["certification_pool_size"] == 3


def test_two_phase_certification_pool_preserves_single_objective_scalar_top_k_ties(
    tmp_path,
) -> None:
    profile = _single_objective_tie_profile()
    result = study.run(
        profile,
        FEEDSTOCK,
        _FixedCandidateStrategy(_single_objective_tie_candidates()),
        "stub",
        1,
        3,
        tmp_path / "two-phase-single-objective-tie",
        evaluator=_single_objective_tie_evaluator,
        two_phase_certify={"enabled": True, "top_k": 1},
    )
    certification = json.loads(
        (
            tmp_path
            / "two-phase-single-objective-tie"
            / "two_phase_certification.json"
        ).read_text()
    )
    definitions = study.objective_definitions(profile)
    scalar_top_id = sorted(result.records, key=lambda row: study._rank_key(row, definitions))[
        0
    ].candidate_id
    explore_pareto_ids = {
        row.candidate_id
        for row in study.pareto_front(
            result.records,
            definitions,
            objective_getter=lambda row: row.objectives,
        )
    }

    assert {"tie-a", "tie-b"} <= explore_pareto_ids
    assert [row["candidate_id"] for row in certification["candidates"]] == [scalar_top_id]
    assert certification["top_k"] == 1
    assert certification["certification_pool_size"] == 1
    assert certification["certification_pool_limit"] == 1


def test_two_phase_certification_preserves_seed_lineage_for_dual_winner(
    tmp_path,
) -> None:
    def seed_winner_evaluator(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        **kwargs: Any,
    ) -> ScoredResult:
        spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
        oxygen = 100.0 if candidate_id and "seed" in candidate_id else 10.0
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key=cache_key(spec),
            feasible=True,
            objectives=ObjectiveVector(
                (
                    ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
                    ObjectiveValue("energy_kWh", "minimize", 1.0, "kWh", ordinal=1),
                )
            ),
            feasibility_margins={"delivered_stream_purity": _margin()},
            run_reference=RunReference(
                status="ok",
                trace={"backend_status": "ok"},
                backend_status="ok",
                backend_authoritative=True,
            ),
        )

    out = tmp_path / "two-phase-seed-lineage"
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "staged",
        "stub",
        1,
        2,
        out,
        seed=7,
        evaluator=seed_winner_evaluator,
        two_phase_certify={"enabled": True, "top_k": 2},
    )
    pareto = json.loads((out / "pareto.json").read_text())
    search_provenance = json.loads((out / "search_provenance.json").read_text())

    assert result.winner is not None
    assert result.winner.proposal_source == "seed_recipe"
    assert result.winner.seed_lineage is True
    assert pareto["best_overall_candidate_id"] == result.winner.candidate_id
    assert pareto["best_non_seeded_lineage_candidate_id"] != result.winner.candidate_id
    assert result.winner.candidate_id in search_provenance["seeded_candidate_ids"]


def test_leaderboard_ranking_preserves_nullable_objective_positions(tmp_path) -> None:
    profile = _nullable_position_profile()
    definitions = study.objective_definitions(profile)
    records = tuple(
        _nullable_position_record(candidate.id)
        for candidate in _nullable_position_candidates()
    )
    leaderboard = tuple(sorted(records, key=lambda row: study._rank_key(row, definitions)))

    study._write_leaderboard(
        tmp_path / "leaderboard.csv",
        leaderboard,
        leaderboard,
        leaderboard[0],
        definitions,
        RecipeSchema(),
    )
    rows = list(csv.DictReader((tmp_path / "leaderboard.csv").open()))

    assert [record.candidate_id for record in leaderboard] == [
        "nullable-baseline",
        "nullable-last",
        "nullable-middle",
        "nullable-primary",
    ]
    assert [row["candidate_id"] for row in rows] == [
        "nullable-baseline",
        "nullable-last",
        "nullable-middle",
        "nullable-primary",
    ]


def test_two_phase_ranking_preserves_nullable_objective_positions(tmp_path) -> None:
    profile = _nullable_position_profile()
    result = study.run(
        profile,
        FEEDSTOCK,
        _FixedCandidateStrategy(_nullable_position_candidates()),
        "stub",
        1,
        4,
        tmp_path / "two-phase-nullable-position",
        evaluator=_nullable_position_evaluator,
        two_phase_certify={"enabled": True, "top_k": 4},
    )
    certification = json.loads(
        (tmp_path / "two-phase-nullable-position" / "two_phase_certification.json").read_text()
    )

    assert [record.candidate_id for record in result.leaderboard] == [
        "nullable-baseline",
        "nullable-last",
        "nullable-middle",
        "nullable-primary",
    ]
    assert [row["candidate_id"] for row in certification["candidates"]] == [
        "nullable-baseline",
        "nullable-last",
        "nullable-middle",
        "nullable-primary",
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
            trace={"snapshots": [{"mass_balance_error_pct": 0.0}]},
            product_summary={
                "mass_closure": {
                    "status": "closed",
                    "mass_balance_error_pct": 0.0,
                }
            },
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
                trace={
                    "backend_status": "ok",
                    "backend_authoritative": True,
                    "snapshots": [{"mass_balance_error_pct": 0.0}],
                },
                product_summary={
                    "mass_closure": {
                        "status": "closed",
                        "mass_balance_error_pct": 0.0,
                    }
                },
                backend_status="ok",
                backend_authoritative=True,
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
            value=1300.0,
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
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "snapshots": [{"mass_balance_error_pct": 0.0}],
            },
            product_summary={
                "mass_closure": {
                    "status": "closed",
                    "mass_balance_error_pct": 0.0,
                }
            },
            backend_status="ok",
            backend_authoritative=True,
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


def test_physics_policy_version_change_invalidates_eval_cache_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = RecipeSchema()
    patch = RecipePatch({})
    profile = dict(PROFILE)
    profile["constraints"] = {
        **PROFILE["constraints"],
        "furnace_T_max_C": 1800.0,
    }
    constraints = physics_constraints_from_profile(profile)
    validated = patch.validated(schema)
    current_version = physics_module.PHYSICS_GATE_VERSION

    def build_for_version(version: str) -> tuple[str, str, str]:
        monkeypatch.setattr(physics_module, "PHYSICS_GATE_VERSION", version)
        spec, _ = _build_eval_inputs(
            validated,
            FEEDSTOCK,
            "stub",
            profile,
            schema,
            constraints=constraints,
        )
        return physics_constraints_digest(constraints), spec.recipe_id, cache_key(spec)

    old_digest, old_recipe_id, old_cache_key = build_for_version(
        "physics-feasibility-v1"
    )
    new_digest, new_recipe_id, new_cache_key = build_for_version(current_version)

    # v3 2026-07-03: bumped when the extraction_completeness gate flipped to
    # the S2c provenance-aware trace surface (milestone-3 L2-P2 — pre-S2c
    # cached feasibility verdicts must not be served under the same digest).
    assert current_version == "physics-feasibility-v3-provenance-completeness"
    assert old_digest != new_digest
    assert old_cache_key != new_cache_key
    assert old_recipe_id == new_recipe_id


def test_stub_smoke_selector_is_retired_from_live_profiles() -> None:
    profile = dict(PROFILE)
    profile["study_constraints"] = "stub_smoke"
    profile["constraints"] = {
        **PROFILE["constraints"],
        "furnace_T_max_C": 1300.0,
    }

    with pytest.raises(ValueError) as excinfo:
        study._constraints_for_profile(profile)
    message = str(excinfo.value)
    assert "stub_smoke" in message
    assert "retired" in message
    assert "FORCE_PROFILES=1" in message


def test_fidelity_pilot_profile_defaults_to_physics_constraints() -> None:
    profile_path = Path("data/optimize_profiles/lunar_mare_low_ti.yaml")
    profile = yaml.safe_load(profile_path.read_text())

    constraints = study._constraints_for_profile(profile)

    assert "study_constraints" not in profile
    assert isinstance(constraints, PhysicsConstraintSet)


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


def test_feasible_earned_rump_ood_cache_rejection_warns_and_study_continues(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger="simulator.optimize.study")

    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        3,
        tmp_path,
        seed=7,
        evaluator=_evaluator(earned_rump_ood={1}),
    )

    assert result.winner.candidate_id == "random-7-000002"
    provenance = _read_provenance(tmp_path)
    logged = {row["candidate_id"]: row for row in provenance}
    stored = {row.candidate_id for row in _stored_rows(tmp_path)}

    assert logged["random-7-000001"]["feasible"] is True
    assert logged["random-7-000001"]["status"] == "ok"
    assert "random-7-000001" not in stored
    assert {"random-7-000000", "random-7-000002"} <= stored
    assert "result_store_write_rejected" in caplog.text
    assert "out_of_domain_provenance" in caplog.text


@pytest.mark.timeout(25)
def test_parallel_one_timeout_records_failure_and_continues(tmp_path) -> None:
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        2,
        tmp_path,
        seed=7,
        evaluator=_slow_first_then_ok_evaluator,
        per_eval_timeout_seconds=2.0,
    )

    provenance = _read_provenance(tmp_path)
    assert result.winner.candidate_id == "random-7-000001"
    assert [row["candidate_id"] for row in provenance] == [
        "random-7-000000",
        "random-7-000001",
    ]
    assert provenance[0]["failure_category"] == "timeout"
    assert provenance[0]["status"] == "timeout"
    assert provenance[1]["status"] == "ok"


def test_all_infeasible_completes_with_no_feasible_winner(tmp_path) -> None:
    result = study.run(
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
    leaderboard_rows = list(csv.DictReader((tmp_path / "leaderboard.csv").open()))

    assert result.status == "completed-no-feasible-winner"
    assert result.reason == "completed-no-feasible-winner"
    assert result.winner is None
    assert result.pareto == ()
    assert len(result.records) == 3
    assert len(result.leaderboard) == 3
    assert pareto_payload["pareto"] == []
    assert pareto_payload["status"] == "completed-no-feasible-winner"
    assert pareto_payload["winner_candidate_id"] is None
    assert [row["candidate_id"] for row in leaderboard_rows] == [
        "random-7-000000",
        "random-7-000001",
        "random-7-000002",
    ]
    assert {row["is_winner"] for row in leaderboard_rows} == {"False"}
    assert all(row["margin_delivered_stream_purity"] for row in leaderboard_rows)
    assert len(_read_provenance(tmp_path)) == 3
    assert len(_stored_rows(tmp_path)) == 3
    assert not (tmp_path / "winner.recipe.yaml").exists()


def test_stale_profile_refusal_flows_through_study_as_named_failure(tmp_path) -> None:
    with pytest.raises(study.StudyNoFeasibleError, match="stale_profile"):
        study.run(
            _stale_melt_target_profile(),
            FEEDSTOCK,
            "random",
            "stub",
            1,
            1,
            tmp_path,
            seed=7,
        )

    pareto_payload = json.loads((tmp_path / "pareto.json").read_text())
    provenance = _read_provenance(tmp_path)

    assert pareto_payload["failure_counts"] == {"stale_profile": 1}
    assert provenance[0]["status"] == "stale_profile"
    assert provenance[0]["failure_category"] == "stale_profile"
    assert "delivered_stream_purity" in provenance[0]["notes"][0]
    assert "residual_rump_at_stop" in provenance[0]["notes"][0]
    assert "FORCE_PROFILES=1" in provenance[0]["notes"][0]
    assert not (tmp_path / "winner.recipe.yaml").exists()


def test_all_out_of_domain_completes_no_feasible_and_logs_count(tmp_path) -> None:
    result = study.run(
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
    assert result.status == "completed-no-feasible-winner"
    assert result.winner is None
    assert pareto_payload["pareto"] == []
    assert pareto_payload["winner_candidate_id"] is None
    assert pareto_payload["status"] == "completed-no-feasible-winner"
    assert pareto_payload["failure_counts"] == {"out_of_domain": 3}
    assert len(_read_provenance(tmp_path)) == 3
    assert len(list(csv.DictReader((tmp_path / "leaderboard.csv").open()))) == 3
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


def test_rerun_cache_hit_rederives_stale_false_feasible_from_margins(tmp_path) -> None:
    study.run(PROFILE, FEEDSTOCK, "random", "stub", 1, 3, tmp_path, seed=7, evaluator=_evaluator())
    with sqlite3.connect(tmp_path / "cache.sqlite") as conn:
        conn.execute(
            """
            UPDATE results
            SET feasible = 0, failing_gates = ?
            WHERE candidate_id = ?
            """,
            (json.dumps(["delivered_stream_purity"]), "random-7-000002"),
        )

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

    assert result.winner.candidate_id == "random-7-000002"
    by_id = {record.candidate_id: record for record in result.records}
    assert by_id["random-7-000002"].feasible is True
    assert all(row["cache_hit"] is True for row in _read_provenance(tmp_path))


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
    produced_status = tmp_path / "produced-status.txt"

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
        produced_status.write_text(study._status(result), encoding="utf-8")
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

    assert produced_status.read_text(encoding="utf-8") == "ok"
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

    bad_pin = subprocess.run(
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
            "--pin",
            "C2A_staged.stages.sio_window.not_a_knob",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad_pin.returncode != 0
    assert "pin path matches no optimizer knob" in bad_pin.stderr

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

    cli_profile = _write_cli_physics_smoke_profile(tmp_path)
    staged = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.optimize",
            "--feedstock",
            FEEDSTOCK,
            "--profile",
            str(cli_profile),
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
            str(cli_profile),
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


def test_cli_forwards_repeatable_pin_to_optimizer_run(tmp_path, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            out_dir=kwargs["out_dir"],
            winner=SimpleNamespace(candidate_id="winner"),
        )

    monkeypatch.setattr(optimizer_cli, "run", fake_run)

    result = optimizer_cli.main(
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
            str(tmp_path),
            "--warm-start-from",
            str(tmp_path / "prior-run"),
            "--pin",
            "C2A_staged.stages.alkali_early_fe.target_C",
            "--pin",
            "C2A_staged.stages.sio_window.target_C",
        ]
    )

    assert result == 0
    assert captured["pinned_paths"] == [
        "C2A_staged.stages.alkali_early_fe.target_C",
        "C2A_staged.stages.sio_window.target_C",
    ]
    assert captured["warm_start_from"] == str(tmp_path / "prior-run")


def test_cli_constrained_max_overlay_forwards_hardware_caps(tmp_path, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            out_dir=kwargs["out_dir"],
            winner=SimpleNamespace(candidate_id="winner"),
        )

    monkeypatch.setattr(optimizer_cli, "run", fake_run)

    result = optimizer_cli.main(
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
            str(tmp_path),
            "--constrained-max",
            "--furnace-temp-cap-C",
            "1300",
            "--cycle-time-cap-h",
            "12",
        ]
    )

    assert result == 0
    profile = captured["profile"]
    metrics = {objective["metric"] for objective in profile["objectives"]}
    assert profile["profile_id"].endswith("-constrained-max")
    assert "coating" not in profile["constraints"]["gates"]
    assert "furnace_temperature" in profile["constraints"]["gates"]
    assert "cycle_time" in profile["constraints"]["gates"]
    assert profile["constraints"]["furnace_T_max_C"] == pytest.approx(1300.0)
    assert profile["constraints"]["cycle_time_max_h"] == pytest.approx(12.0)
    assert "solar_thermal_flux_h" in metrics
    assert "furnace_lifespan_consumed_fraction" in metrics


@pytest.mark.parametrize(
    ("extra_args", "message"),
    [
        (["--constrained-max"], "--constrained-max requires at least one hardware cap"),
        (["--furnace-temp-cap-C", "1300"], "require --constrained-max"),
        (["--cycle-time-cap-h", "12"], "require --constrained-max"),
    ],
)
def test_cli_rejects_incoherent_constrained_max_args(
    tmp_path,
    capsys,
    extra_args: list[str],
    message: str,
) -> None:
    with pytest.raises(SystemExit) as exc:
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
                str(tmp_path),
                *extra_args,
            ]
        )

    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_cli_constrained_max_furnace_cap_e2e_writes_leaderboard(tmp_path) -> None:
    out_dir = tmp_path / "constrained-max"
    cli_profile = _write_cli_physics_smoke_profile(tmp_path)

    result = optimizer_cli.main(
        [
            "--feedstock",
            FEEDSTOCK,
            "--profile",
            str(cli_profile),
            "--strategy",
            "random",
            "--fidelity",
            "stub",
            "--budget",
            "1",
            "--out",
            str(out_dir),
            "--constrained-max",
            "--furnace-temp-cap-C",
            "1300",
        ]
    )

    assert result == 0
    leaderboard_path = out_dir / "leaderboard.csv"
    assert leaderboard_path.exists()
    rows = list(csv.DictReader(leaderboard_path.open()))
    assert rows
    assert rows[0]["rank"] == "1"
    assert "furnace_lifespan_consumed_fraction" in rows[0]
    assert rows[0]["furnace_lifespan_consumed_fraction"] == ""
    assert rows[0]["lifespan_cost_status"] == "threshold_unavailable"


def test_constrained_max_nullable_lifespan_persists_and_round_trips(
    tmp_path,
) -> None:
    metric = "furnace_lifespan_consumed_fraction"
    base_profile = copy.deepcopy(
        dict(study.resolve_profile("default", expected_feedstock=FEEDSTOCK))
    )
    profile = constrained_max_profile(
        base_profile,
        furnace_T_max_C=1300.0,
        include_throughput_cost=True,
    )

    def evaluate_lifespan_null(
        patch: RecipePatch,
        feedstock: str,
        fidelity: str,
        *,
        profile: Mapping[str, Any],
        candidate_id: str | None = None,
        **kwargs: Any,
    ) -> ScoredResult:
        spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
        values: list[ObjectiveValue] = []
        evidence: dict[str, Mapping[str, Any]] = {}
        for definition in study.objective_definitions(profile):
            if definition.metric == metric:
                value = None
                evidence[metric] = {
                    "reader": metric,
                    "lifespan_cost_status": "threshold_unavailable",
                    "lifespan_cost_status_reason": "resinter_threshold_kg is null",
                    "resinter_threshold_kg": None,
                    "wall_deposit_total_kg": 0.0,
                }
            elif definition.metric == "solar_thermal_flux_h":
                value = 12.0
            elif definition.sense == "maximize":
                value = 1.0
            else:
                value = 1.0
            values.append(
                ObjectiveValue(
                    definition.metric,
                    definition.sense,
                    value,
                    definition.units,
                    ordinal=definition.ordinal,
                )
            )
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key=cache_key(spec),
            feasible=True,
            objectives=ObjectiveVector(tuple(values), evidence=evidence),
            feasibility_margins={"delivered_stream_purity": _margin()},
            failing_gates=(),
            run_reference=RunReference(
                status="ok",
                trace={
                    "backend_status": "ok",
                    "backend_authoritative": True,
                    "snapshots": [{"mass_balance_error_pct": 0.0}],
                },
                product_summary={
                    "mass_closure": {
                        "status": "closed",
                        "mass_balance_error_pct": 0.0,
                    },
                    "lifespan_cost_status": "threshold_unavailable",
                    "lifespan_cost_status_reason": "resinter_threshold_kg is null",
                    "furnace_lifespan_consumed_fraction": None,
                    "wall_deposit_total_kg": 0.0,
                },
                backend_status="ok",
                backend_authoritative=True,
            ),
        )

    out_dir = tmp_path / "constrained-max-store"
    result = study.run(
        profile,
        FEEDSTOCK,
        _SingleCandidateStrategy(),
        "fast",
        parallel=1,
        budget=1,
        out_dir=out_dir,
        evaluator=evaluate_lifespan_null,
    )

    rows = list(csv.DictReader((out_dir / "leaderboard.csv").open()))
    with sqlite3.connect(result.store_path) as conn:
        stored = conn.execute(
            """
            SELECT r.cache_key, r.objectives, ov.value, ov.value_status
            FROM results r
            JOIN objective_values ov ON ov.cache_key = r.cache_key
            WHERE ov.metric = ?
            """,
            (metric,),
        ).fetchone()
    assert stored is not None
    payload = json.loads(stored[1])
    lifespan_payload = next(item for item in payload if item["metric"] == metric)
    loaded = ResultStore(result.store_path).fetch(stored[0])

    assert result.leaderboard[0].objectives[metric] is None
    assert rows[0]["rank"] == "1"
    assert rows[0][metric] == ""
    assert rows[0]["lifespan_cost_status"] == "threshold_unavailable"
    assert stored[2] is None
    assert stored[3] == "threshold_unavailable"
    assert lifespan_payload["value"] is None
    assert lifespan_payload["value_status"] == "threshold_unavailable"
    assert loaded is not None and loaded.objectives is not None
    assert loaded.objectives.as_mapping()[metric] is None
    assert loaded.objectives.evidence[metric]["lifespan_cost_status"] == "threshold_unavailable"


def test_cli_writes_success_job_status_marker_for_completed_no_feasible_winner(
    tmp_path,
    monkeypatch,
) -> None:
    out_dir = tmp_path / "cli-no-winner"

    def complete_without_winner(**kwargs):
        out = Path(kwargs["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        store_path = out / "cache.sqlite"
        store_path.write_text("partial", encoding="utf-8")
        return study.StudyResult(
            out_dir=out,
            store_path=store_path,
            artifacts={"store": store_path},
            records=(),
            leaderboard=(),
            pareto=(),
            winner=None,
            status="completed-no-feasible-winner",
            reason="completed-no-feasible-winner",
        )

    monkeypatch.setattr(optimizer_cli, "run", complete_without_winner)

    exit_code = optimizer_cli.main(
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

    assert exit_code == 0
    status = json.loads((out_dir / "job_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed-no-feasible-winner"
    assert status["success"] is True
    assert status["reason"] == "completed-no-feasible-winner"
    assert status["study_status"] == "completed-no-feasible-winner"
    assert "winner_candidate_id" not in status


def test_cli_writes_failure_job_status_marker_for_no_feasible_config_error(
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


def _two_phase_evaluate_patch(
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
    fid_opts = profile.get("fidelities", {}).get(fidelity, {})
    tier_ceiling = fid_opts.get("cache_tier_ceiling", "cached_interpolated")
    if tier_ceiling == "cached_interpolated":
        oxygen = 100.0 + index
        cache_state = "cached_interpolated"
    else:
        oxygen = 10.0 + index
        cache_state = "cached_exact"
    objectives = ObjectiveVector(
        (
            ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
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
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "per_hour_summary": [{"reduced_real_cache_state": cache_state}],
                "snapshots": [{"mass_balance_error_pct": 0.0}],
            },
            product_summary={
                "mass_closure": {
                    "status": "closed",
                    "mass_balance_error_pct": 0.0,
                }
            },
            backend_status="ok",
            backend_authoritative=True,
        ),
    )


def _two_phase_exact_flip_evaluate_patch(
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
    fid_opts = profile.get("fidelities", {}).get(fidelity, {})
    tier_ceiling = fid_opts.get("cache_tier_ceiling", "cached_interpolated")
    if tier_ceiling != "cached_interpolated" and index == 4:
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
                trace={
                    "backend_status": "ok",
                    "backend_authoritative": True,
                    "per_hour_summary": [{"reduced_real_cache_state": "cached_exact"}],
                    "snapshots": [{"mass_balance_error_pct": 0.0}],
                },
                product_summary={
                    "mass_closure": {
                        "status": "closed",
                        "mass_balance_error_pct": 0.0,
                    }
                },
                backend_status="ok",
                backend_authoritative=True,
            ),
        )
    return _two_phase_evaluate_patch(
        patch,
        feedstock,
        fidelity,
        profile=profile,
        candidate_id=candidate_id,
        **kwargs,
    )


def _two_phase_exact_all_infeasible_evaluate_patch(
    patch: RecipePatch,
    feedstock: str,
    fidelity: str,
    *,
    profile: Mapping[str, Any],
    candidate_id: str | None = None,
    **kwargs: Any,
) -> ScoredResult:
    spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
    fid_opts = profile.get("fidelities", {}).get(fidelity, {})
    tier_ceiling = fid_opts.get("cache_tier_ceiling", "cached_interpolated")
    if tier_ceiling != "cached_interpolated":
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
                trace={
                    "backend_status": "ok",
                    "backend_authoritative": True,
                    "per_hour_summary": [{"reduced_real_cache_state": "cached_exact"}],
                    "snapshots": [{"mass_balance_error_pct": 0.0}],
                },
                product_summary={
                    "mass_closure": {
                        "status": "closed",
                        "mass_balance_error_pct": 0.0,
                    }
                },
                backend_status="ok",
                backend_authoritative=True,
            ),
        )
    return _two_phase_evaluate_patch(
        patch,
        feedstock,
        fidelity,
        profile=profile,
        candidate_id=candidate_id,
        **kwargs,
    )


def test_two_phase_loop_certifies_top_k_and_reports_certified_winner(tmp_path) -> None:
    out = tmp_path / "two-phase"
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        5,
        out,
        seed=7,
        evaluator=_two_phase_evaluate_patch,
        two_phase_certify={"enabled": True, "top_k": 3},
    )

    explore_states = {
        record.trace_summary.get("reduced_real_cache_state")
        for record in result.records
        if record.feasible
    }
    assert "cached_interpolated" in explore_states

    certification_path = out / "two_phase_certification.json"
    assert certification_path.exists()
    certification = json.loads(certification_path.read_text())
    assert certification["candidates"]
    assert "aggregate_disagreement" in certification
    assert certification["aggregate_disagreement"]["max"] > 0.0
    assert "replay_metadata" in certification
    metadata = certification["replay_metadata"]
    for key in (
        "seed",
        "strategy",
        "parallel",
        "sampler",
        "objective_names",
        "code_version",
        "data_digests",
    ):
        assert key in metadata
    assert metadata["seed"] == 7
    assert metadata["parallel"] == 1

    winner_index = int(result.winner.candidate_id.rsplit("-", 1)[1])
    assert result.winner.objectives["oxygen_kg"] == pytest.approx(10.0 + winner_index)
    explore_by_id = {record.candidate_id: record for record in result.records}
    explore_winner = explore_by_id[result.winner.candidate_id]
    assert explore_winner.objectives["oxygen_kg"] == pytest.approx(100.0 + winner_index)
    assert (
        certification["aggregate_disagreement"]["max"]
        == pytest.approx(abs(explore_winner.objectives["oxygen_kg"] - result.winner.objectives["oxygen_kg"]))
    )


def test_two_phase_certification_scores_legacy_energy_alias_as_primary_metric(
    tmp_path,
) -> None:
    profile = copy.deepcopy(PROFILE)
    profile["objectives"] = [copy.deepcopy(PROFILE["objectives"][1])]
    out = tmp_path / "two-phase-legacy-energy-primary"

    result = study.run(
        profile,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        5,
        out,
        seed=7,
        evaluator=_two_phase_evaluate_patch,
        two_phase_certify={"enabled": True, "top_k": 3},
    )

    assert result.winner is not None
    assert ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC in result.winner.objectives
    assert LEGACY_ENERGY_KWH_METRIC not in result.winner.objectives
    certification = json.loads((out / "two_phase_certification.json").read_text())
    assert certification["candidates"]
    assert all(row["certified_objective"] is not None for row in certification["candidates"])


def test_two_phase_certification_filters_exact_infeasible_before_objective(tmp_path) -> None:
    out = tmp_path / "two-phase-exact-flip"
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        5,
        out,
        seed=7,
        evaluator=_two_phase_exact_flip_evaluate_patch,
        two_phase_certify={"enabled": True, "top_k": 3},
    )

    assert result.winner.candidate_id == "random-7-000003"
    assert all(record.feasible for record in result.leaderboard)
    certification = json.loads((out / "two_phase_certification.json").read_text())
    flipped = next(
        row
        for row in certification["candidates"]
        if row["candidate_id"] == "random-7-000004"
    )
    assert flipped["certified_objective"] is None
    assert flipped["disagreement"] is None
    assert certification["aggregate_disagreement"]["max"] > 0.0


def test_two_phase_certification_all_infeasible_completes_no_winner(tmp_path) -> None:
    out = tmp_path / "two-phase-exact-all-infeasible"
    result = study.run(
        PROFILE,
        FEEDSTOCK,
        "random",
        "stub",
        1,
        5,
        out,
        seed=7,
        evaluator=_two_phase_exact_all_infeasible_evaluate_patch,
        two_phase_certify={"enabled": True, "top_k": 3},
    )

    pareto_payload = json.loads((out / "pareto.json").read_text())
    certification = json.loads((out / "two_phase_certification.json").read_text())
    leaderboard_rows = list(csv.DictReader((out / "leaderboard.csv").open()))

    assert result.status == "completed-no-feasible-winner"
    assert result.reason == "completed-no-feasible-winner"
    assert result.winner is None
    assert result.pareto == ()
    assert len(result.leaderboard) == certification["certification_pool_size"]
    assert {record.feasible for record in result.leaderboard} == {False}
    assert pareto_payload["status"] == "completed-no-feasible-winner"
    assert pareto_payload["pareto"] == []
    assert pareto_payload["winner_candidate_id"] is None
    assert len(certification["candidates"]) == certification["certification_pool_size"]
    assert all(row["certified_objective"] is None for row in certification["candidates"])
    assert [row["is_winner"] for row in leaderboard_rows] == [
        "False" for _ in range(certification["certification_pool_size"])
    ]
    assert not (out / "winner.recipe.yaml").exists()


def test_two_phase_disabled_matches_single_pass_output(tmp_path) -> None:
    single = tmp_path / "single"
    disabled = tmp_path / "disabled"
    kwargs = dict(
        profile=PROFILE,
        feedstock=FEEDSTOCK,
        strategy="random",
        fidelity="stub",
        parallel=1,
        budget=3,
        seed=7,
        evaluator=_evaluator(),
    )
    study.run(**kwargs, out_dir=single)
    study.run(**kwargs, out_dir=disabled, two_phase_certify={"enabled": False})

    assert (single / "pareto.json").read_text() == (disabled / "pareto.json").read_text()
    assert (single / "winner.recipe.yaml").read_text() == (
        disabled / "winner.recipe.yaml"
    ).read_text()
    assert not (disabled / "two_phase_certification.json").exists()


def test_two_phase_certification_records_parallel_for_adaptive_strategy(tmp_path) -> None:
    out = tmp_path / "adaptive-two-phase"
    study.run(
        PROFILE,
        FEEDSTOCK,
        "bayes",
        "stub",
        2,
        4,
        out,
        seed=3,
        evaluator=_two_phase_evaluate_patch,
        two_phase_certify={"enabled": True, "top_k": 2},
    )
    certification = json.loads((out / "two_phase_certification.json").read_text())
    assert certification["replay_metadata"]["parallel"] == 2


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
