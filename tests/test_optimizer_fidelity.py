from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from simulator.optimize.doe import (
    DEPENDENCY_FREE_LHC_SAMPLER,
    DoeSpec,
    FidelityCorrelationResult,
)
from simulator.optimize.evaluate import EvaluationAbort, FailureCategory, ScoredResult
from simulator.optimize.fidelity import DEFAULT_THRESHOLD_PROFILE, run_fidelity_correlation
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.recipe import KnobSpec, RecipePatch, RecipeSchema

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


def _result(
    candidate_id: str | None,
    *,
    oxygen_kg: float,
    energy_kwh: float,
    feasible: bool = True,
) -> ScoredResult:
    if not feasible:
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=None,
            cache_key=None,
            feasible=False,
            failure_category=FailureCategory.INFEASIBLE_RECIPE,
        )
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=None,
        cache_key=None,
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", oxygen_kg, "kg", 0),
                ObjectiveValue("energy_kWh", "minimize", energy_kwh, "kWh", 1),
            )
        ),
    )


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


def test_perfect_correlation_is_trustworthy_and_writes_artifacts(tmp_path: Path) -> None:
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

    assert result.fast_screen_trustworthy is True
    assert result.confidence == "high"
    assert result.n_samples_compared == 8
    assert result.n_samples_dropped == 0
    assert result.feasible_infeasible_agreement == 1.0
    assert result.top_k_recall[3] == 1.0
    assert result.spearman_by_objective["oxygen_kg"] == pytest.approx(1.0)
    assert result.spearman_by_objective["energy_kWh"] == pytest.approx(1.0)

    payload = json.loads(Path(result.artifact_paths["json"]).read_text())
    assert payload["fast_screen_trustworthy"] is True
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
    assert result.spearman_by_objective["energy_kWh"] == pytest.approx(-1.0)
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
