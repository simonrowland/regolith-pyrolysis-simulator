from __future__ import annotations

import pytest

import simulator.backend_names as backend_names
from simulator.fidelity_vocabulary import (
    CANONICAL_DIMENSIONS,
    CERTIFICATION_DENYLIST,
    DESIGN_LEGACY_MAPPING_ROW_COUNT,
    LEGACY_INTERNAL_ANALYTICAL_VOCABULARY_TOKEN,
    LEGACY_VOCABULARY_TOKENS,
    EvidenceClass,
    FidelityVocabularyTranslationError,
    UnknownFidelityVocabularyTokenError,
    canonicalize_fidelity_emission,
    legacy_backend_alias_for_evidence_class,
    may_certify,
    translate_legacy_token,
)


EXPECTED_LEGACY_TOKENS = {
    "curve_source": {
        "liquidus_solidus:kernel",
        "liquidus_solidus:kernel:composition_derived",
        "composition_derived",
        "proof_inputs",
    },
    "terminal_rump_source": {"earned_crash", "completed_run", "tap_truncated"},
    "reduced_real_cache_state": {
        "live_fill",
        "cached_exact",
        "cached_physics_bucket",
        "cached_interpolated",
    },
    "backend/status alias": {
        "stub",
        "diagnostic_stub",
        "alphamelts",
        "auto",
        "cached-real",
        "mixed:*",
        "mixed_backend",
        "missing",
        "ok",
        "unavailable",
        "out_of_domain",
        "not_run",
        "no_compared_results",
    },
    "legacy runtime field": {"backend_authoritative"},
}


@pytest.mark.parametrize(
    ("family", "token", "expected"),
    [
        (
            "curve_source",
            "liquidus_solidus:kernel",
            {"label_source": "liquidus_solidus:kernel"},
        ),
        (
            "curve_source",
            "liquidus_solidus:kernel:composition_derived",
            {"label_source": "liquidus_solidus:kernel:composition_derived"},
        ),
        (
            "curve_source / emitted provenance",
            "composition_derived",
            {"label_source": "composition_derived"},
        ),
        (
            "curve_source / emitted provenance",
            "proof_inputs",
            {"label_source": "proof_inputs"},
        ),
        (
            "terminal_rump_source",
            "earned_crash",
            {"label_source": "terminal_rump:earned_crash"},
        ),
        (
            "terminal_rump_source",
            "completed_run",
            {"label_source": "terminal_rump:completed_run"},
        ),
        (
            "terminal_rump_source",
            "tap_truncated",
            {
                "label_source": "terminal_rump:tap_truncated",
                "degradation_reason": "tap_truncated",
            },
        ),
        (
            "reduced_real_cache_state",
            "live_fill",
            {"cache_state": "live_fill"},
        ),
        (
            "reduced_real_cache_state",
            "cached_exact",
            {"cache_state": "cached_exact"},
        ),
        (
            "reduced_real_cache_state",
            "cached_interpolated",
            {
                "cache_state": "served_neighbor",
                "degradation_reason": "legacy_cached_interpolated",
            },
        ),
        (
            "backend/status alias",
            "stub",
            {
                "evidence_class": "internal-analytical",
                "label_source": "legacy_backend_alias:stub",
            },
        ),
        (
            "backend/status alias",
            "diagnostic_stub",
            {
                "evidence_class": "internal-analytical",
                "label_source": "diagnostic_stub",
                "degradation_reason": "diagnostic_only",
            },
        ),
        (
            "backend/status alias",
            "alphamelts",
            {
                "evidence_class": "melts",
                "label_source": "backend_alias:alphamelts",
            },
        ),
        (
            "backend/status alias",
            "missing",
            {
                "runtime_status": "missing",
                "degradation_reason": "missing",
            },
        ),
        (
            "backend/status alias",
            "ok",
            {"runtime_status": "ok"},
        ),
        (
            "backend/status alias",
            "unavailable",
            {
                "runtime_status": "unavailable",
                "degradation_reason": "unavailable",
            },
        ),
        (
            "backend/status alias",
            "out_of_domain",
            {
                "runtime_status": "out_of_domain",
                "degradation_reason": "out_of_domain",
            },
        ),
        (
            "backend/status alias",
            "not_run",
            {
                "runtime_status": "not_run",
                "degradation_reason": "not_run",
            },
        ),
        (
            "backend/status alias",
            "no_compared_results",
            {
                "runtime_status": "not_run",
                "degradation_reason": "not_run",
            },
        ),
    ],
)
def test_design_table_simple_rows_translate(family: str, token: str, expected: dict[str, str]) -> None:
    assert translate_legacy_token(family, token).as_dict() == expected


def test_canonical_dimension_set_is_exact() -> None:
    assert CANONICAL_DIMENSIONS == (
        "evidence_class",
        "cache_state",
        "runtime_status",
        "label_source",
        "degradation_reason",
    )


def test_design_token_inventory_is_pinned_to_spec_table() -> None:
    assert {
        family: set(tokens) for family, tokens in LEGACY_VOCABULARY_TOKENS.items()
    } == EXPECTED_LEGACY_TOKENS
    assert DESIGN_LEGACY_MAPPING_ROW_COUNT == 25


def test_legacy_stub_vocabulary_survives_backend_identity_hinge_flip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backend_names,
        "ANALYTICAL_BACKEND_SERIALIZATION_TOKEN",
        "internal-analytical",
    )

    assert LEGACY_INTERNAL_ANALYTICAL_VOCABULARY_TOKEN == "stub"
    assert translate_legacy_token(
        "backend/status alias",
        "stub",
    ).as_dict() == {
        "evidence_class": "internal-analytical",
        "label_source": "legacy_backend_alias:stub",
    }


def test_auto_requires_and_decomposes_selected_backend() -> None:
    with pytest.raises(FidelityVocabularyTranslationError):
        translate_legacy_token("backend/status alias", "auto")

    result = translate_legacy_token(
        "backend/status alias",
        "auto",
        selected_token="alphamelts",
    )

    assert result.label_source == "backend_selection:auto"
    assert len(result.contributors) == 1
    assert result.contributors[0].evidence_class == "melts"


def test_cached_real_is_cache_state_and_cannot_certify_analytical_rows() -> None:
    result = translate_legacy_token("backend/status alias", "cached-real")

    assert result.cache_state == "cached_real"
    assert result.label_source == "cached-real"
    assert result.evidence_class is None
    assert result.requires_inherited_evidence_class is True

    inherited = translate_legacy_token(
        "backend/status alias",
        "cached-real",
        inherited_evidence_class=EvidenceClass.MELTS,
    )
    assert inherited.evidence_class == "melts"
    assert inherited.requires_inherited_evidence_class is False

    with pytest.raises(FidelityVocabularyTranslationError):
        translate_legacy_token(
            "backend/status alias",
            "cached-real",
            inherited_evidence_class=EvidenceClass.INTERNAL_ANALYTICAL,
        )


def test_mixed_suffix_decomposes_contributors() -> None:
    result = translate_legacy_token(
        "backend/status alias",
        "mixed:stub+alphamelts|diagnostic_stub",
    )

    assert result.label_source == "mixed"
    assert [item.evidence_class for item in result.contributors] == [
        "internal-analytical",
        "melts",
        "internal-analytical",
    ]
    assert [item.label_source for item in result.contributors] == [
        "legacy_backend_alias:stub",
        "backend_alias:alphamelts",
        "diagnostic_stub",
    ]


def test_mixed_suffix_fails_when_undecomposable_or_unknown() -> None:
    with pytest.raises(FidelityVocabularyTranslationError):
        translate_legacy_token("backend/status alias", "mixed:")

    with pytest.raises(FidelityVocabularyTranslationError):
        translate_legacy_token("backend/status alias", "mixed:stub:alphamelts")

    with pytest.raises(UnknownFidelityVocabularyTokenError):
        translate_legacy_token("backend/status alias", "mixed:stub+not_a_backend")


def test_mixed_backend_requires_explicit_contributor_list() -> None:
    with pytest.raises(FidelityVocabularyTranslationError):
        translate_legacy_token("backend/status alias", "mixed_backend")

    result = translate_legacy_token(
        "backend/status alias",
        "mixed_backend",
        contributors=("stub", "alphamelts"),
    )

    assert result.label_source == "mixed_backend"
    assert [item.evidence_class for item in result.contributors] == [
        "internal-analytical",
        "melts",
    ]


def test_backend_authoritative_translates_only_runtime_flag() -> None:
    result = translate_legacy_token(
        "legacy runtime field",
        "backend_authoritative",
        value=True,
    )

    assert result.label_source == "legacy_backend_authoritative"
    assert result.backend_real_active is True
    assert result.evidence_class is None
    assert result.runtime_status is None

    assert (
        translate_legacy_token(
            "legacy runtime field",
            "backend_authoritative",
            value=False,
        ).backend_real_active
        is False
    )

    with pytest.raises(FidelityVocabularyTranslationError):
        translate_legacy_token("legacy runtime field", "backend_authoritative")


def test_unknown_token_fails_loud_with_required_context() -> None:
    with pytest.raises(UnknownFidelityVocabularyTokenError) as exc_info:
        translate_legacy_token(
            "curve_source",
            "opaque_passthrough",
            artifact_digest="sha256:test",
            migration_chunk="chunk-1a",
        )

    message = str(exc_info.value)
    assert "curve_source" in message
    assert "opaque_passthrough" in message
    assert "sha256:test" in message
    assert "chunk-1a" in message


def test_certification_denylist_ignores_hostile_ordering_inputs() -> None:
    hostile_ordering = {
        "internal-analytical": 999,
        "melts": -1,
    }

    assert CERTIFICATION_DENYLIST == frozenset(
        {"internal-analytical"}
    )
    assert not may_certify(
        EvidenceClass.INTERNAL_ANALYTICAL,
        hostile_ordering,
        ordering={"internal-analytical": "first"},
    )
    assert may_certify(EvidenceClass.MELTS, hostile_ordering)


@pytest.mark.parametrize(
    ("evidence_class", "expected"),
    [
        ("melts", True),
        ("magemin", True),
        ("internal-datatables", True),
        ("internal-analytical", False),
    ],
)
def test_may_certify_registered_canonical_classes(
    evidence_class: str, expected: bool
) -> None:
    assert may_certify(evidence_class) is expected


def test_may_certify_accepts_evidence_class_enum() -> None:
    assert may_certify(EvidenceClass.MELTS) is True
    assert may_certify(EvidenceClass.INTERNAL_ANALYTICAL) is False


@pytest.mark.parametrize(
    "evidence_class",
    [
        "unknown",
        "mixed:internal-analytical+melts",
    ],
)
def test_may_certify_rejects_noncanonical_strings(evidence_class: str) -> None:
    with pytest.raises(UnknownFidelityVocabularyTokenError) as exc_info:
        may_certify(evidence_class)

    message = str(exc_info.value)
    assert "evidence_class" in message
    assert evidence_class in message
    if evidence_class.startswith("mixed:"):
        assert "translate_legacy_token" in message


def test_stub_alias_round_trips_to_internal_analytical() -> None:
    result = translate_legacy_token("backend/status alias", "stub")

    assert result.evidence_class == "internal-analytical"
    assert (
        legacy_backend_alias_for_evidence_class(result.evidence_class)
        == "stub"
    )


def test_canonical_emission_combines_backend_status_and_runtime_flag() -> None:
    payload = canonicalize_fidelity_emission(
        backend_name="alphamelts",
        backend_status="ok",
        backend_authoritative=True,
    )

    assert payload["evidence_class"] == "melts"
    assert payload["runtime_status"] == "ok"
    assert payload["backend_real_active"] is True
    assert payload["certification_allowed"] is True
    assert payload["label_source"] == "backend_alias:alphamelts"


def test_canonical_emission_preserves_not_run_honesty() -> None:
    payload = canonicalize_fidelity_emission(
        backend_status="not_run",
        backend_authoritative=False,
    )

    assert payload["runtime_status"] == "not_run"
    assert payload["backend_real_active"] is False
    assert payload["degradation_reason"] == "not_run"
    assert payload["degraded_from"] == ["not_run"]


def test_canonical_emission_refuses_denylisted_certification_shape() -> None:
    with pytest.raises(FidelityVocabularyTranslationError):
        canonicalize_fidelity_emission(
            backend_name="stub",
            backend_status="ok",
            backend_authoritative=True,
            certification_shape=True,
        )
