from __future__ import annotations

import pytest

from simulator.fidelity_vocabulary import EvidenceClass, may_certify
from web.routes import _optimizer_tier_label


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
    run_reference = {"cache_state": "cached_exact", "evidence_class": EvidenceClass.MELTS.value}
    result_blob = {"cache_state": "cached_interpolated"}

    label = _optimizer_tier_label(run_reference, result_blob)

    assert label["tier"] == "cached_exact"
    assert label["ux_label"] == "CERTIFIED"


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