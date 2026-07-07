from __future__ import annotations

import inspect
import json
import os
import time
from pathlib import Path

import pytest

import simulator.backends as backend_module
import simulator.optimize.fidelity as fidelity_module
import simulator.optimize.worker_runtime as worker_runtime_module
from simulator.optimize.doe import (
    DEPENDENCY_FREE_LHC_SAMPLER,
    DoeSpec,
    FidelityCorrelationResult,
)
from simulator.optimize.evaluate import EvaluationAbort, FailureCategory, RunReference, ScoredResult
from simulator.optimize.evalspec import EvalSpec
from simulator.optimize.fidelity import DEFAULT_THRESHOLD_PROFILE, run_fidelity_correlation
from simulator.optimize.objective import (
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    ObjectiveValue,
    ObjectiveVector,
)
from simulator.optimize.recipe import KnobSpec, RecipePatch, RecipeSchema
from simulator.optimize.worker_runtime import WARM_WORKERS_ENV

FEEDSTOCK_ID = "lunar_mare_low_ti"


def _schema() -> RecipeSchema:
    return RecipeSchema(
        allowlist=(
            KnobSpec(
                path=("campaigns", "C0", "temp_range_C"),
                kind="float",
                low=20,
                high=950,
            ),
        )
    )


def _doe(n_samples: int = 8) -> DoeSpec:
    return DoeSpec(
        schema=_schema(),
        n_samples=n_samples,
        seed=42,
        sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
    )


def _index(candidate_id: str | None) -> int:
    assert candidate_id is not None
    return int(candidate_id.split("-")[-2])


def _candidate_fidelity(candidate_id: str | None) -> str:
    return str(candidate_id).rsplit("-", 1)[-1] if candidate_id else "test"


def _eval_spec(candidate_id: str | None, backend_name: str) -> EvalSpec:
    return EvalSpec(
        recipe_id=f"recipe-{candidate_id or 'test'}",
        feedstock_recipe_digest="feedstock-digest",
        feedstock_id=FEEDSTOCK_ID,
        profile_id="test-profile",
        fidelity=_candidate_fidelity(candidate_id),
        code_version="test",
        data_digests={
            "feedstocks": "feedstocks-digest",
            "foulant_thermo": "foulant-thermo-digest",
            "materials": "materials-digest",
            "profile": "profile-digest",
            "setpoints": "setpoints-digest",
            "species_catalog": "species-catalog-digest",
            "vapor_pressures": "vapor-pressures-digest",
        },
        backend_name=backend_name,
    )


def _run_reference(backend_status: str | None) -> RunReference | None:
    if backend_status is None:
        return None
    return RunReference(status="ok", trace={"backend_status": backend_status})


def _result(
    candidate_id: str | None,
    *,
    oxygen_kg: float,
    energy_kwh: float,
    feasible: bool = True,
    backend_name: str | None = None,
    backend_status: str | None = None,
) -> ScoredResult:
    eval_spec = _eval_spec(candidate_id, backend_name) if backend_name is not None else None
    cache_key = f"cache-{candidate_id}" if eval_spec is not None else None
    run_reference = _run_reference(backend_status)
    if not feasible:
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=eval_spec,
            cache_key=cache_key,
            feasible=False,
            failure_category=FailureCategory.INFEASIBLE_RECIPE,
            run_reference=run_reference,
        )
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=eval_spec,
        cache_key=cache_key,
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", oxygen_kg, "kg", 0),
                ObjectiveValue("energy_kWh", "minimize", energy_kwh, "kWh", 1),
            )
        ),
        run_reference=run_reference,
    )


def _warm_task(*, feedstock_id: str = FEEDSTOCK_ID) -> fidelity_module._FidelityTask:
    return fidelity_module._FidelityTask(
        index=0,
        tier="high",
        fn=_perfect_high,
        patch=RecipePatch({}),
        feedstock_id=feedstock_id,
        fidelity="high",
        profile={
            "run": {"backend_name": "alphamelts"},
            "fidelities": {"high": {"backend_name": "alphamelts"}},
        },
        candidate_id="fidelity-doe-000000-high",
        kwargs={},
    )


def test_fidelity_warm_runtime_spec_carries_feedstock_subprocess_route() -> None:
    spec = fidelity_module._warm_runtime_spec((_warm_task(),))

    assert spec == fidelity_module._FidelityWarmRuntimeSpec(
        backend_name="alphamelts",
        feedstock_id=FEEDSTOCK_ID,
        stage0_subprocess_required=True,
    )


def test_fidelity_warm_runtime_spec_keeps_benign_feedstock_inprocess() -> None:
    feedstock_id = "m_type_metallic_phase"
    spec = fidelity_module._warm_runtime_spec(
        (_warm_task(feedstock_id=feedstock_id),)
    )

    assert spec == fidelity_module._FidelityWarmRuntimeSpec(
        backend_name="alphamelts",
        feedstock_id=feedstock_id,
        stage0_subprocess_required=False,
    )


def test_fidelity_subprocess_routing_uses_shared_backend_predicate() -> None:
    fidelity_source = Path(fidelity_module.__file__).read_text()
    task_route_source = inspect.getsource(
        fidelity_module._task_stage0_subprocess_required
    )
    init_route_source = inspect.getsource(fidelity_module._initialize_fidelity_worker)

    assert (
        fidelity_module.requires_stage0_subprocess
        is backend_module.requires_stage0_subprocess
    )
    assert "requires_stage0_subprocess" in task_route_source
    assert "requires_stage0_subprocess" in init_route_source
    assert "STAGE0_SUBPROCESS_FEEDSTOCK_IDS" not in fidelity_source
    assert "is_spinel_rich_stage0_subprocess_feedstock" not in fidelity_source


def test_fidelity_warm_worker_initialization_rechecks_feedstock_subprocess_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feedstock_id = "ci_carbonaceous_chondrite"
    calls: list[dict[str, object]] = []
    backend = object()

    def fake_resolve_backend(
        backend_name: str,
        *_args: object,
        **kwargs: object,
    ) -> object:
        feedstocks = kwargs.get("feedstocks")
        calls.append(
            {
                "backend_name": backend_name,
                "feedstock_id": kwargs.get("feedstock_id"),
                "feedstock_present": feedstocks is not None
                and feedstock_id in feedstocks,
                "stage0_subprocess_required": kwargs.get(
                    "stage0_subprocess_required"
                ),
            }
        )
        return backend

    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)
    monkeypatch.setattr(worker_runtime_module, "resolve_backend", fake_resolve_backend)
    worker_runtime_module.clear_worker_runtime()
    try:
        fidelity_module._initialize_fidelity_worker(
            fidelity_module._FidelityWarmRuntimeSpec(
                backend_name="alphamelts",
                feedstock_id=feedstock_id,
                stage0_subprocess_required=False,
            )
        )

        assert calls == [
            {
                "backend_name": "alphamelts",
                "feedstock_id": feedstock_id,
                "feedstock_present": True,
                "stage0_subprocess_required": True,
            }
        ]
        assert (
            worker_runtime_module.get_worker_runtime(
                feedstock_id=feedstock_id,
                stage0_subprocess_required=True,
            )
            is not None
        )
        assert (
            worker_runtime_module.get_worker_runtime(
                feedstock_id=feedstock_id,
                stage0_subprocess_required=False,
            )
            is None
        )
    finally:
        worker_runtime_module.clear_worker_runtime()


def test_fidelity_task_requests_matching_feedstock_worker_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    runtime = object()

    def fake_get_worker_runtime(**kwargs: object) -> object:
        calls.append(dict(kwargs))
        return runtime

    def fake_eval(
        patch: RecipePatch,
        feedstock_id: str,
        fidelity: str,
        *,
        profile: dict[str, object],
        candidate_id: str | None = None,
        worker_runtime: object | None = None,
    ) -> ScoredResult:
        del patch, feedstock_id, fidelity, profile
        assert worker_runtime is runtime
        return _result(candidate_id, oxygen_kg=1.0, energy_kwh=2.0)

    task = _warm_task()
    task = fidelity_module._FidelityTask(
        **{**task.__dict__, "fn": fake_eval}
    )
    monkeypatch.setattr(worker_runtime_module, "get_worker_runtime", fake_get_worker_runtime)

    fidelity_module._call_evaluate_fn(task)

    assert calls == [
        {
            "feedstock_id": FEEDSTOCK_ID,
            "stage0_subprocess_required": True,
        }
    ]


def _perfect_fast(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    del patch, feedstock_id, fidelity, profile
    value = float(_index(candidate_id) // 2)
    return _result(candidate_id, oxygen_kg=value, energy_kwh=100.0 - value)


def _perfect_high(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    return _perfect_fast(
        patch,
        feedstock_id,
        fidelity,
        profile=profile,
        candidate_id=candidate_id,
    )


def _mixed_perfect_fast(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    del patch, feedstock_id, fidelity, profile
    index = _index(candidate_id)
    value = float(index // 2)
    return _result(candidate_id, oxygen_kg=value, energy_kwh=100.0 - value, feasible=index < 6)


def _mixed_perfect_high(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    return _mixed_perfect_fast(
        patch,
        feedstock_id,
        fidelity,
        profile=profile,
        candidate_id=candidate_id,
    )


def _authoritative_mixed_perfect_high(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    del patch, feedstock_id, fidelity, profile
    index = _index(candidate_id)
    value = float(index // 2)
    return _result(
        candidate_id,
        oxygen_kg=value,
        energy_kwh=100.0 - value,
        feasible=index < 6,
        backend_name="alphamelts",
        backend_status="ok",
    )


def _authoritative_cached_real_fast(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    del patch, feedstock_id, fidelity, profile
    index = _index(candidate_id)
    value = float(index // 2)
    return _result(
        candidate_id,
        oxygen_kg=value,
        energy_kwh=100.0 - value,
        feasible=index < 6,
        backend_name="cached-real",
        backend_status="ok",
    )


def _authoritative_high_missing_energy_objective(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    del patch, feedstock_id, fidelity, profile
    value = float(_index(candidate_id) // 2)
    eval_spec = _eval_spec(candidate_id, "alphamelts")
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=eval_spec,
        cache_key=f"cache-{candidate_id}",
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", value, "kg", 0),
            )
        ),
        run_reference=_run_reference("ok"),
    )


def _anti_high(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    del patch, feedstock_id, fidelity, profile
    value = float(_index(candidate_id) // 2)
    return _result(candidate_id, oxygen_kg=-value, energy_kwh=100.0 + value)


def _all_infeasible(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    del patch, feedstock_id, fidelity, profile
    return _result(candidate_id, oxygen_kg=0.0, energy_kwh=0.0, feasible=False)


def _constant_mixed(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    del patch, feedstock_id, fidelity, profile
    return _result(
        candidate_id,
        oxygen_kg=1.0,
        energy_kwh=100.0,
        feasible=_index(candidate_id) < 6,
    )


def _always_error(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    del patch, feedstock_id, fidelity, profile, candidate_id
    raise RuntimeError("synthetic evaluator failure")


def _engine_abort(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    del feedstock_id, fidelity, profile
    raise EvaluationAbort(
        "synthetic engine bug",
        category=FailureCategory.ENGINE_BUG,
        patch=patch,
        candidate_id=candidate_id,
    )


def _flaky_high(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    index = _index(candidate_id)
    if index == 1:
        time.sleep(2.0)
    if index == 2:
        raise RuntimeError("synthetic evaluator failure")
    return _perfect_high(
        patch,
        feedstock_id,
        fidelity,
        profile=profile,
        candidate_id=candidate_id,
    )


def test_stub_high_self_parity_withholds_trust_verdict(tmp_path: Path) -> None:
    result = run_fidelity_correlation(
        _doe(),
        _mixed_perfect_fast,
        _mixed_perfect_high,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg", "energy_kWh"),
        artifact_dir=tmp_path,
    )

    assert result.feasible_infeasible_agreement == 1.0
    assert result.top_k_recall[3] == 1.0
    assert result.spearman_by_objective["oxygen_kg"] == pytest.approx(1.0)
    assert result.spearman_by_objective[
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC
    ] == pytest.approx(1.0)
    assert result.fast_screen_trustworthy is False
    assert result.confidence == "inconclusive"
    assert result.notes == ("stub-vs-stub diagnostic, not authoritative",)

    payload = json.loads(Path(result.artifact_paths["json"]).read_text())
    assert payload["fast_screen_trustworthy"] is False
    assert payload["reason"] == "stub-vs-stub diagnostic, not authoritative"
    assert {
        key: payload["backend_arms"]["fast"][key]
        for key in (
            "tier",
            "fidelity_name",
            "backend_name",
            "backend_status",
            "authoritative",
        )
    } == {
        "tier": "fast",
        "fidelity_name": "fast",
        "backend_name": "stub",
        "backend_status": "diagnostic_stub",
        "authoritative": False,
    }
    assert payload["backend_arms"]["fast"]["evidence_class"] == "internal-analytical"
    assert payload["backend_arms"]["fast"]["degradation_reason"] == "diagnostic_only"
    assert payload["backend_arms"]["fast"]["certification_allowed"] is False
    assert {
        key: payload["backend_arms"]["high"][key]
        for key in (
            "tier",
            "fidelity_name",
            "backend_name",
            "backend_status",
            "authoritative",
        )
    } == {
        "tier": "high",
        "fidelity_name": "high",
        "backend_name": "stub",
        "backend_status": "diagnostic_stub",
        "authoritative": False,
    }
    assert payload["backend_arms"]["high"]["evidence_class"] == "internal-analytical"
    assert payload["backend_arms"]["high"]["degradation_reason"] == "diagnostic_only"
    assert payload["backend_arms"]["high"]["certification_allowed"] is False


def test_authoritative_high_correlation_is_trustworthy_and_writes_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WARM_WORKERS_ENV, "0")

    result = run_fidelity_correlation(
        _doe(),
        _mixed_perfect_fast,
        _authoritative_mixed_perfect_high,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={"fidelities": {"fast": {"backend_name": "stub"}, "high": {"backend_name": "alphamelts"}}},
        objective_names=("oxygen_kg", "energy_kWh"),
        artifact_dir=tmp_path,
    )

    assert result.fast_screen_trustworthy is True
    assert result.confidence == "high"
    assert result.n_samples_compared == 8
    assert result.n_samples_dropped == 0
    assert result.feasible_infeasible_agreement == 1.0
    assert result.top_k_recall[3] == 1.0
    assert result.spearman_by_objective["oxygen_kg"] == pytest.approx(1.0)
    assert result.spearman_by_objective[
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC
    ] == pytest.approx(1.0)

    payload = json.loads(Path(result.artifact_paths["json"]).read_text())
    assert payload["fast_screen_trustworthy"] is True
    assert payload["reason"] == ""
    assert payload["backend_arms"]["high"]["backend_name"] == "alphamelts"
    assert payload["backend_arms"]["high"]["backend_status"] == "ok"
    assert payload["backend_arms"]["high"]["authoritative"] is True
    assert payload["backend_arms"]["high"]["evidence_class"] == "melts"
    assert payload["backend_arms"]["high"]["runtime_status"] == "ok"
    assert payload["backend_arms"]["high"]["certification_allowed"] is True
    assert payload["top_k_recall"]["3"] == 1.0
    assert Path(result.artifact_paths["markdown"]).read_text().startswith(
        "# Fidelity Correlation Report"
    )
    artifact_restored = FidelityCorrelationResult.from_dict(payload, schema=_schema())
    assert artifact_restored.to_dict() == result.to_dict()
    restored = FidelityCorrelationResult.from_dict(
        json.loads(json.dumps(result.to_dict())), schema=_schema()
    )
    assert restored.to_dict() == result.to_dict()


def test_cached_real_all_ok_arm_inherits_cache_authority_and_writes_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WARM_WORKERS_ENV, "0")
    cache_config = {
        "db_path": str(tmp_path / "pt1-cache.db"),
        "miss_policy": "fail-loud",
        "authorized_backend_name": "alphamelts",
    }

    result = run_fidelity_correlation(
        _doe(),
        _authoritative_cached_real_fast,
        _authoritative_mixed_perfect_high,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={
            "fidelities": {
                "fast": {
                    "backend_name": "cached-real",
                    "hours": 1,
                    "reduced_real_cache": cache_config,
                },
                "high": {"backend_name": "alphamelts", "hours": 1},
            }
        },
        objective_names=("oxygen_kg", "energy_kWh"),
        artifact_dir=tmp_path,
    )

    assert result.fast_screen_trustworthy is True
    payload = json.loads(Path(result.artifact_paths["json"]).read_text())
    fast = payload["backend_arms"]["fast"]
    assert fast["backend_name"] == "cached-real"
    assert fast["backend_status"] == "ok"
    assert fast["authoritative"] is True
    assert fast["cache_state"] == "cached_real"
    assert fast["evidence_class"] == "melts"
    assert fast["runtime_status"] == "ok"
    assert fast["label_source"] == "cached-real"
    assert fast["certification_allowed"] is True
    assert "requires_inherited_evidence_class" not in fast


def test_cached_real_all_ok_arm_without_inherited_evidence_degrades_without_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WARM_WORKERS_ENV, "0")

    result = run_fidelity_correlation(
        _doe(),
        _authoritative_cached_real_fast,
        _authoritative_mixed_perfect_high,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={
            "fidelities": {
                "fast": {"backend_name": "cached-real", "hours": 1},
                "high": {"backend_name": "alphamelts", "hours": 1},
            }
        },
        objective_names=("oxygen_kg", "energy_kWh"),
        artifact_dir=tmp_path,
    )

    payload = json.loads(Path(result.artifact_paths["json"]).read_text())
    fast = payload["backend_arms"]["fast"]
    assert fast["backend_name"] == "cached-real"
    assert fast["backend_status"] == "ok"
    assert fast["authoritative"] is False
    assert fast["cache_state"] == "cached_real"
    assert fast["runtime_status"] == "ok"
    assert fast["requires_inherited_evidence_class"] is True
    assert "evidence_class" not in fast
    assert "certification_allowed" not in fast


def test_missing_declared_objective_in_arm_withholds_inconclusive_and_names_metric(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WARM_WORKERS_ENV, "0")

    result = run_fidelity_correlation(
        _doe(),
        _perfect_fast,
        _authoritative_high_missing_energy_objective,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={"fidelities": {"fast": {"backend_name": "stub"}, "high": {"backend_name": "alphamelts"}}},
        objective_names=("oxygen_kg", "energy_kWh"),
        artifact_dir=tmp_path,
    )

    assert result.fast_screen_trustworthy is False
    assert result.confidence == "inconclusive"
    assert result.spearman_by_objective[ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC] is None
    assert any(
        (
            f"declared objective '{ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC}' "
            "missing in high arm"
        ) in note
        for note in result.notes
    )

    payload = json.loads(Path(result.artifact_paths["json"]).read_text())
    assert payload["fast_screen_trustworthy"] is False
    assert ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC in payload["reason"] or any(
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC in note for note in payload["notes"]
    )


def test_anti_correlated_scores_fail_verdict() -> None:
    result = run_fidelity_correlation(
        _doe(),
        _perfect_fast,
        _anti_high,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg", "energy_kWh"),
    )

    assert result.fast_screen_trustworthy is False
    assert result.spearman_by_objective["oxygen_kg"] == pytest.approx(-1.0)
    assert result.spearman_by_objective[
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC
    ] == pytest.approx(-1.0)
    assert result.top_k_recall[3] < 1.0


def test_thresholds_are_non_null_and_provenance_tagged() -> None:
    result = run_fidelity_correlation(
        _doe(),
        _mixed_perfect_fast,
        _mixed_perfect_high,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
    )

    assert result.thresholds
    for threshold in result.thresholds.values():
        assert threshold["value"] is not None
        assert threshold["source_type"] in {
            "literature",
            "engineering_envelope",
            "profile",
        }
        assert threshold["source"]
        if threshold["source_type"] == "literature":
            source = threshold["source"].lower()
            assert any(token in source for token in ("doi", "pmid", "http://", "https://"))

    broken = dict(DEFAULT_THRESHOLD_PROFILE)
    broken["spearman_min"] = {"value": None, "source_type": "profile", "source": "test"}
    with pytest.raises(ValueError, match="spearman_min threshold value is required"):
        run_fidelity_correlation(
            _doe(),
            _perfect_fast,
            _perfect_high,
            top_k=(3,),
            per_eval_timeout_s=2.0,
            feedstock_id=FEEDSTOCK_ID,
            profile={},
            thresholds=broken,
        )


def test_fake_literature_threshold_without_checkable_reference_fails() -> None:
    broken = dict(DEFAULT_THRESHOLD_PROFILE)
    broken["spearman_min"] = {
        "value": 0.8,
        "source_type": "literature",
        "source": "Akoglu 2018 says strong correlation",
    }

    with pytest.raises(ValueError, match="DOI, PMID, or URL"):
        run_fidelity_correlation(
            _doe(),
            _mixed_perfect_fast,
            _mixed_perfect_high,
            top_k=(3,),
            per_eval_timeout_s=2.0,
            feedstock_id=FEEDSTOCK_ID,
            profile={},
            objective_names=("oxygen_kg",),
            thresholds=broken,
        )


def test_timeout_and_errors_are_dropped_excluded_and_cap_respected(
    tmp_path: Path,
) -> None:
    result = run_fidelity_correlation(
        _doe(n_samples=6),
        _perfect_fast,
        _flaky_high,
        top_k=(2,),
        per_eval_timeout_s=1.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
        artifact_dir=tmp_path,
        max_samples=4,
    )

    assert result.fast_screen_trustworthy is False
    assert result.confidence == "low"
    assert result.n_samples_total == 4
    assert result.n_samples_compared == 2
    assert result.n_samples_dropped == 2
    assert {drop["reason"] for drop in result.dropped_evaluations} == {
        "timeout",
        "error",
    }
    assert all(drop["tier"] == "high" for drop in result.dropped_evaluations)
    assert any("min_compared_fraction" in note for note in result.notes)
    payload = json.loads(Path(result.artifact_paths["json"]).read_text())
    assert payload["n_samples_dropped"] == 2
    assert payload["dropped_evaluations"][0]["reason"] in {"timeout", "error"}


@pytest.mark.parametrize(
    "max_samples",
    [
        pytest.param(True, id="bool"),
        pytest.param(3.5, id="float"),
        pytest.param("3", id="string"),
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
    ],
)
def test_max_samples_rejects_non_positive_or_coerced_values(max_samples: object) -> None:
    with pytest.raises(ValueError, match="max_samples"):
        run_fidelity_correlation(
            _doe(n_samples=6),
            _perfect_fast,
            _perfect_high,
            top_k=(2,),
            per_eval_timeout_s=1.0,
            feedstock_id=FEEDSTOCK_ID,
            profile={},
            objective_names=("oxygen_kg",),
            max_samples=max_samples,  # type: ignore[arg-type]
        )


def test_all_evaluations_dropped_withholds_without_crash() -> None:
    result = run_fidelity_correlation(
        _doe(n_samples=3),
        _always_error,
        _always_error,
        top_k=(2,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
    )

    assert result.fast_screen_trustworthy is False
    assert result.confidence == "low"
    assert result.n_samples_compared == 0
    assert result.n_samples_dropped == 6


def test_single_compared_sample_withholds_when_spearman_undefined() -> None:
    result = run_fidelity_correlation(
        _doe(n_samples=1),
        _perfect_fast,
        _perfect_high,
        top_k=(1,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
    )

    assert result.fast_screen_trustworthy is False
    assert result.spearman_by_objective["oxygen_kg"] is None
    assert any("rank correlation undefined" in note for note in result.notes)


def test_constant_scores_make_spearman_unavailable_and_withhold() -> None:
    result = run_fidelity_correlation(
        _doe(),
        _constant_mixed,
        _constant_mixed,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
    )

    assert result.fast_screen_trustworthy is False
    assert result.feasible_infeasible_agreement == 1.0
    assert result.spearman_by_objective["oxygen_kg"] is None


def test_top_k_recall_unavailable_when_sample_count_below_k() -> None:
    result = run_fidelity_correlation(
        _doe(n_samples=2),
        _mixed_perfect_fast,
        _mixed_perfect_high,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
    )

    assert result.fast_screen_trustworthy is False
    assert result.top_k_recall[3] is None
    assert any("top-K recall unavailable" in note for note in result.notes)


@pytest.mark.parametrize(
    ("fast_fn", "high_fn"),
    (
        (_perfect_fast, _perfect_high),
        (_all_infeasible, _all_infeasible),
    ),
)
def test_single_feasibility_class_makes_agreement_unavailable_and_withholds(
    fast_fn, high_fn
) -> None:
    result = run_fidelity_correlation(
        _doe(),
        fast_fn,
        high_fn,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
    )

    assert result.fast_screen_trustworthy is False
    assert result.confidence == "low"
    assert result.feasible_infeasible_agreement is None
    assert any("feasibility agreement unavailable" in note for note in result.notes)


def test_evaluation_abort_reason_uses_failure_category_value() -> None:
    result = run_fidelity_correlation(
        _doe(n_samples=1),
        _perfect_fast,
        _engine_abort,
        top_k=(1,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
    )

    assert result.dropped_evaluations[0]["reason"] == "engine_bug"


# Env var the patch-recording spy reads to find its sink file. Each
# run_fidelity_correlation eval runs in a forked/spawned subprocess, so an
# in-memory sink would never reach the parent; the spy appends every received
# patch's knob values to this shared-filesystem JSONL file instead.
_PATCH_SINK_ENV = "PYROLYSIS_TEST_PATCH_SINK"
_RUNTIME_SINK_ENV = "PYROLYSIS_TEST_RUNTIME_SINK"


def _patch_recording_spy(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
) -> ScoredResult:
    """Spy evaluator: record the sampled knob values, no real chemistry.

    Module-level so it survives both fork and spawn process start. Writes the
    received patch's numeric knob values to the sink file named by
    ``_PATCH_SINK_ENV`` (one JSON object per eval), then returns a deterministic
    feasible ScoredResult keyed off the candidate index.
    """

    del feedstock_id, fidelity, profile
    sink = os.environ[_PATCH_SINK_ENV]
    record = {".".join(path): val for path, val in patch.values.items()}
    with open(sink, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    value = float(_index(candidate_id) // 2)
    return _result(candidate_id, oxygen_kg=value, energy_kwh=100.0 - value)


def _runtime_recording_spy(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
    worker_runtime: object | None = None,
) -> ScoredResult:
    del patch, feedstock_id, fidelity, profile
    sink = os.environ[_RUNTIME_SINK_ENV]
    record = {
        "candidate_id": candidate_id,
        "pid": os.getpid(),
        "has_runtime": worker_runtime is not None,
    }
    with open(sink, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    value = float(_index(candidate_id) // 2)
    return _result(candidate_id, oxygen_kg=value, energy_kwh=100.0 - value)


def test_fidelity_pool_reuses_warm_workers_and_cold_env_preserves_zero_drop_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warm_sink = tmp_path / "warm-runtime.jsonl"
    monkeypatch.setenv(_RUNTIME_SINK_ENV, str(warm_sink))
    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)

    warm_result = run_fidelity_correlation(
        _doe(n_samples=4),
        _runtime_recording_spy,
        _runtime_recording_spy,
        top_k=(2,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={"run": {"backend_name": "stub"}},
        objective_names=("oxygen_kg",),
    )

    warm_records = _recorded_values(warm_sink)
    assert warm_result.n_samples_total == 4
    assert warm_result.n_samples_compared == 4
    assert warm_result.n_samples_dropped == 0
    assert len(warm_records) == 8
    assert all(record["has_runtime"] for record in warm_records)
    assert len({record["pid"] for record in warm_records}) <= 4

    cold_sink = tmp_path / "cold-runtime.jsonl"
    monkeypatch.setenv(_RUNTIME_SINK_ENV, str(cold_sink))
    monkeypatch.setenv(WARM_WORKERS_ENV, "0")
    cold_result = run_fidelity_correlation(
        _doe(n_samples=4),
        _runtime_recording_spy,
        _runtime_recording_spy,
        top_k=(2,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={"run": {"backend_name": "stub"}},
        objective_names=("oxygen_kg",),
    )

    cold_records = _recorded_values(cold_sink)
    assert cold_result.to_dict() | {"artifact_paths": {}} == warm_result.to_dict() | {
        "artifact_paths": {}
    }
    assert len(cold_records) == 8
    assert not any(record["has_runtime"] for record in cold_records)


def _midpoint_anchor(schema: RecipeSchema) -> RecipePatch:
    """Build an anchor pinning every sampled numeric knob to its bounds midpoint."""

    values: dict[tuple[str, ...], object] = {}
    for spec in schema.search_allowlist:
        if spec.kind == "categorical":
            assert spec.choices
            values[spec.path] = spec.choices[0]
        elif spec.kind == "int":
            values[spec.path] = int(round((float(spec.low) + float(spec.high)) / 2.0))
        else:
            values[spec.path] = (float(spec.low) + float(spec.high)) / 2.0
    return RecipePatch(values)


def _numeric_bands(schema: RecipeSchema, df: float) -> dict[str, tuple[float, float]]:
    """Per-knob anchored band [center - df*(hi-lo), center + df*(hi-lo)] about the midpoint."""

    bands: dict[str, tuple[float, float]] = {}
    for spec in schema.search_allowlist:
        if spec.kind == "categorical":
            continue
        low, high = float(spec.low), float(spec.high)
        center = (low + high) / 2.0
        half = df * (high - low)
        bands[".".join(spec.path)] = (center - half, center + half)
    return bands


def _recorded_values(sink: Path) -> list[dict[str, float]]:
    return [json.loads(line) for line in sink.read_text().splitlines() if line.strip()]


def test_anchor_constrains_sampled_patches_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Proves DoeSpec(anchor=...) is honored all the way through
    # run_fidelity_correlation: every sampled numeric knob must land inside the
    # tight anchored band, and the un-anchored full-range run must escape it.
    # If anchor/delta_fraction are not forwarded into sample_recipe_patches the
    # harness full-range samples and the band assertion below fails loudly.
    schema = _schema()
    df = 0.05
    bands = _numeric_bands(schema, df)
    anchor = _midpoint_anchor(schema)

    anchored_sink = tmp_path / "anchored.jsonl"
    monkeypatch.setenv(_PATCH_SINK_ENV, str(anchored_sink))
    run_fidelity_correlation(
        DoeSpec(
            schema=schema,
            n_samples=8,
            seed=42,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=anchor,
            delta_fraction=df,
        ),
        _patch_recording_spy,
        _patch_recording_spy,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
    )

    anchored = _recorded_values(anchored_sink)
    # fast + high fns are the same spy, so each of 8 samples is recorded twice.
    assert len(anchored) == 16
    for record in anchored:
        for knob, (lo, hi) in bands.items():
            value = float(record[knob])
            assert lo <= value <= hi, f"{knob} value {value} escaped anchored band [{lo}, {hi}]"

    # Complementary decisive check: full-range (anchor=None) must produce at
    # least one numeric value OUTSIDE the tight anchored band, proving the band
    # assertion above is not vacuously satisfied (e.g. by full-range sampling
    # that happens to be ignored).
    full_sink = tmp_path / "full.jsonl"
    monkeypatch.setenv(_PATCH_SINK_ENV, str(full_sink))
    run_fidelity_correlation(
        _doe(n_samples=8),
        _patch_recording_spy,
        _patch_recording_spy,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
    )
    full = _recorded_values(full_sink)
    escaped = any(
        not (lo <= float(record[knob]) <= hi)
        for record in full
        for knob, (lo, hi) in bands.items()
    )
    assert escaped, "full-range sampling never escaped the anchored band; test is not decisive"


def test_anchor_with_max_samples_truncation_stays_in_band_and_records_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = _schema()
    df = 0.05
    bands = _numeric_bands(schema, df)
    anchor = _midpoint_anchor(schema)

    sink = tmp_path / "truncated.jsonl"
    monkeypatch.setenv(_PATCH_SINK_ENV, str(sink))
    result = run_fidelity_correlation(
        DoeSpec(
            schema=schema,
            n_samples=8,
            seed=42,
            sampler_name=DEPENDENCY_FREE_LHC_SAMPLER,
            anchor=anchor,
            delta_fraction=df,
        ),
        _patch_recording_spy,
        _patch_recording_spy,
        top_k=(3,),
        per_eval_timeout_s=2.0,
        feedstock_id=FEEDSTOCK_ID,
        profile={},
        objective_names=("oxygen_kg",),
        artifact_dir=tmp_path / "artifacts",
        max_samples=3,
    )

    recorded = _recorded_values(sink)
    assert result.n_samples_total == 3
    assert len(recorded) == 6
    for record in recorded:
        for knob, (lo, hi) in bands.items():
            value = float(record[knob])
            assert lo <= value <= hi, f"{knob} value {value} escaped anchored band [{lo}, {hi}]"

    payload = json.loads(Path(result.artifact_paths["json"]).read_text())
    doe_payload = payload["protocol"]["doe"]
    assert doe_payload["delta_fraction"] == df
    assert doe_payload["anchor"] == [
        {"path": ["campaigns", "C0", "temp_range_C"], "value": 485.0}
    ]
    artifact_restored = FidelityCorrelationResult.from_dict(payload, schema=schema)
    assert artifact_restored.protocol.doe.anchor is not None
    assert dict(artifact_restored.protocol.doe.anchor.values) == dict(anchor.values)
