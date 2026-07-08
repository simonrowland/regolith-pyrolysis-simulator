from __future__ import annotations

import pytest

from simulator.fidelity_vocabulary import EvidenceClass, may_certify
from simulator.optimize import study
from simulator.optimize.evaluate import RunReference, ScoredResult
from simulator.optimize.honesty import optimizer_tier_label
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.strategy import Candidate
from web.routes import _optimizer_tier_label


def _real_path_record(*, proof_grade: str | None) -> study.StudyRecord:
    trace = {
        "backend_name": "alphamelts",
        "backend_status": "ok",
        "backend_authoritative": True,
        "evidence_class": EvidenceClass.MELTS.value,
        "cache_state": "live_fill",
        "reduced_real_cache_state": "live_fill",
    }
    if proof_grade is not None:
        trace["proof_grade"] = proof_grade
    reference = RunReference(
        status="ok",
        trace=trace,
        backend_name="alphamelts",
        backend_status="ok",
        backend_authoritative=True,
        evidence_class=EvidenceClass.MELTS.value,
    )
    candidate = Candidate(
        id="candidate-proof-grade",
        patch=RecipePatch({}),
        metadata={"proposal_source": "sobol", "strategy": "test"},
    )
    scored = ScoredResult(
        candidate_id=candidate.id,
        eval_spec=None,
        cache_key="cache-proof-grade",
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue(
                    metric="oxygen_kg",
                    sense="maximize",
                    value=1.0,
                    units="kg",
                ),
            )
        ),
        run_reference=reference,
    )
    return study._to_record(candidate, scored, cache_hit=False)


@pytest.mark.parametrize(
    ("cache_state", "evidence_class", "expected_ux", "expected_tier"),
    [
        ("cached_exact", EvidenceClass.MELTS.value, "CERTIFIED", "cached_exact"),
        ("live_fill", EvidenceClass.MELTS.value, "CERTIFIED", "live_fill"),
        ("cached_physics_bucket", EvidenceClass.MELTS.value, "ESTIMATED", "cached_physics_bucket"),
        ("cached_interpolated", EvidenceClass.MELTS.value, "ESTIMATED", "cached_interpolated"),
        ("live_fill", EvidenceClass.INTERNAL_ANALYTICAL.value, "UNVERIFIED", "live_fill"),
        ("cached_exact", "stub", "UNVERIFIED", "cached_exact"),
        ("live_fill", "diagnostic_stub", "UNVERIFIED", "live_fill"),
    ],
)
def test_optimizer_tier_label_mapping_matrix(
    cache_state: str,
    evidence_class: str,
    expected_ux: str,
    expected_tier: str,
) -> None:
    run_reference = {
        "cache_state": cache_state,
        "backend_status": "ok",
    }
    if evidence_class in {"stub", "diagnostic_stub"}:
        run_reference["backend_name"] = evidence_class
        run_reference["backend_authoritative"] = False
    elif evidence_class == EvidenceClass.INTERNAL_ANALYTICAL.value:
        run_reference["backend_name"] = "stub"
        run_reference["evidence_class"] = evidence_class
        run_reference["backend_authoritative"] = False
    else:
        run_reference["backend_name"] = "alphamelts"
        run_reference["evidence_class"] = evidence_class
        run_reference["backend_authoritative"] = True
    label = _optimizer_tier_label(run_reference, {})

    assert label["ux_label"] == expected_ux
    assert label["tier"] == expected_tier
    if evidence_class in {"stub", "diagnostic_stub"}:
        assert label["evidence_class"] == EvidenceClass.INTERNAL_ANALYTICAL.value
        assert label["certification_allowed"] is False
    else:
        assert label["evidence_class"] == evidence_class
        assert label["certification_allowed"] is may_certify(evidence_class)
    if expected_ux == "CERTIFIED":
        assert label["certification_allowed"] is True
    if evidence_class in {EvidenceClass.INTERNAL_ANALYTICAL.value, "stub", "diagnostic_stub"}:
        assert label["ux_label"] != "CERTIFIED"


def test_optimizer_tier_label_prefers_run_reference_cache_state() -> None:
    # backend_authoritative is REQUIRED for CERTIFIED since the 5786d1f R2
    # fold (tier-label authority): certification fails toward caution when
    # authority is absent. This fixture supplies it so the test exercises its
    # actual target — run_reference cache_state winning over result_blob.
    run_reference = {
        "cache_state": "cached_exact",
        "evidence_class": EvidenceClass.MELTS.value,
        "backend_authoritative": True,
    }
    result_blob = {"cache_state": "cached_interpolated"}

    label = _optimizer_tier_label(run_reference, result_blob)

    assert label["tier"] == "cached_exact"
    assert label["ux_label"] == "CERTIFIED"


def test_optimizer_tier_label_without_backend_authority_is_not_certified() -> None:
    # The pre-fold fixture shape (no backend_authoritative) must NOT certify.
    run_reference = {"cache_state": "cached_exact", "evidence_class": EvidenceClass.MELTS.value}
    result_blob = {"cache_state": "cached_interpolated"}

    label = _optimizer_tier_label(run_reference, result_blob)

    assert label["tier"] == "cached_exact"
    assert label["ux_label"] == "UNVERIFIED"


def test_optimizer_tier_label_reads_per_hour_summary_fallback() -> None:
    result_blob = {
        "per_hour_summary": [
            {"reduced_real_cache_state": "cached_physics_bucket"},
        ]
    }

    label = _optimizer_tier_label({}, result_blob)

    assert label["tier"] == "cached_physics_bucket"
    assert label["ux_label"] == "ESTIMATED"


def test_optimizer_tier_label_without_cache_state_is_unverified() -> None:
    backend_payload = {
        "backend_active": "cached-real",
        "backend_status": "ok",
        "backend_authoritative": True,
        "evidence_class": EvidenceClass.MELTS.value,
    }

    label = _optimizer_tier_label({}, {}, backend_payload=backend_payload)

    assert label["ux_label"] == "UNVERIFIED"
    assert label["tier"] == "unknown"


def test_optimizer_tier_label_title_includes_rung_and_disagreement() -> None:
    result_blob = {
        "cache_rung": 4.5,
        "neighbor_disagreement": {"max": 0.12, "p95": 0.08},
    }

    label = _optimizer_tier_label(
        {"cache_state": "cached_interpolated", "evidence_class": EvidenceClass.MELTS.value},
        result_blob,
    )

    assert "rung=4.5" in label["title"]
    assert "neighbor_disagreement_max=0.12" in label["title"]
    assert label["ux_label"] == "ESTIMATED"


def test_web_optimizer_tier_label_delegates_to_shared_producer() -> None:
    run_reference = {
        "cache_state": "live_fill",
        "backend_name": "alphamelts",
        "backend_status": "ok",
        "backend_authoritative": True,
        "evidence_class": EvidenceClass.MELTS.value,
    }
    result_blob = {"cache_rung": 3}

    assert _optimizer_tier_label(run_reference, result_blob) == optimizer_tier_label(
        run_reference,
        result_blob,
    )


def test_summary_honesty_real_path_matches_web_without_proof_grade() -> None:
    record = _real_path_record(proof_grade=None)

    summary_label = study._summary_honesty_payload("stub", record, records=(record,))
    web_label = _optimizer_tier_label(record.trace_summary, record.result_blob)

    assert record.result_blob["evidence_class"] == EvidenceClass.MELTS.value
    assert summary_label["tier"] == web_label["tier"] == "live_fill"
    assert summary_label["ux_label"] == web_label["ux_label"] == "CERTIFIED"
    assert summary_label["evidence_class"] == web_label["evidence_class"]
    assert "evidence_rank" not in summary_label
    assert "evidence_rank" not in web_label
    assert summary_label.get("evidence_rank") == web_label.get("evidence_rank")


@pytest.mark.parametrize("proof_grade", ["D", "B", "C"])
def test_summary_honesty_real_path_preserves_result_blob_proof_grade(
    proof_grade: str,
) -> None:
    record = _real_path_record(proof_grade=proof_grade)

    summary_label = study._summary_honesty_payload("stub", record, records=(record,))
    web_label = _optimizer_tier_label(record.trace_summary, record.result_blob)

    assert record.result_blob["evidence_class"] == EvidenceClass.MELTS.value
    assert record.result_blob["proof_grade"] == proof_grade
    assert web_label["evidence_rank"] == proof_grade
    assert web_label["proof_grade"] == proof_grade
    assert summary_label["evidence_class"] == EvidenceClass.MELTS.value
    assert summary_label["evidence_rank"] == web_label["evidence_rank"] == proof_grade
