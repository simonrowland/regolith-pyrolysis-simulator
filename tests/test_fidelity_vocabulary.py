from __future__ import annotations

import pytest

import simulator.backend_names as backend_names
from simulator.backend_names import (
    RATIFIED_VAPOUR_ANALYTICAL_EVIDENCE_CLASSES,
    VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
    VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
    canonical_backend_name,
)
from simulator.fidelity_vocabulary import (
    CANONICAL_DIMENSIONS,
    CERTIFICATION_CEILING_NEVER,
    CERTIFICATION_DENYLIST,
    DESIGN_LEGACY_MAPPING_ROW_COUNT,
    LEGACY_INTERNAL_ANALYTICAL_VOCABULARY_TOKEN,
    LEGACY_VOCABULARY_TOKENS,
    STATUS_BEARING_NON_AUTHORITATIVE,
    EvidenceClass,
    FidelityVocabularyTranslationError,
    UnknownFidelityVocabularyTokenError,
    backend_evidence_authority_rejection,
    backend_name_denies_authority,
    canonicalize_fidelity_emission,
    is_ratified_vapour_analytical_evidence_class,
    legacy_backend_alias_for_evidence_class,
    may_certify,
    translate_legacy_token,
    vapour_analytical_flux_verdict,
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
        "thermoengine",
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
                "label_source": "diagnostic_internal_analytical",
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
    assert DESIGN_LEGACY_MAPPING_ROW_COUNT == 26


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

    with pytest.raises(FidelityVocabularyTranslationError, match="never-certify"):
        translate_legacy_token(
            "backend/status alias",
            "cached-real",
            inherited_evidence_class=EvidenceClass.INTERNAL_ANALYTICAL,
        )


@pytest.mark.parametrize(
    "denied_class",
    sorted(CERTIFICATION_DENYLIST),
)
def test_cached_real_refuses_every_never_certify_evidence_class(
    denied_class: str,
) -> None:
    """O1 ceiling: cached-real must not dress any denylisted class as real.

    Null-hypothesis: a name-only guard for internal-analytical would leave
    both ratified vapour classes green here — denylist membership is required.
    """

    with pytest.raises(
        FidelityVocabularyTranslationError,
        match="never-certify",
    ):
        translate_legacy_token(
            "backend/status alias",
            "cached-real",
            inherited_evidence_class=denied_class,
        )
    # Enum form must refuse the same way (not only string tokens).
    enum_member = EvidenceClass(denied_class)
    with pytest.raises(
        FidelityVocabularyTranslationError,
        match="never-certify",
    ):
        translate_legacy_token(
            "backend/status alias",
            "cached-real",
            inherited_evidence_class=enum_member,
        )


@pytest.mark.parametrize(
    "real_class",
    [
        EvidenceClass.MELTS,
        EvidenceClass.MAGEMIN,
        EvidenceClass.INTERNAL_DATATABLES,
        "melts",
        "magemin",
        "internal-datatables",
    ],
)
def test_cached_real_still_dresses_genuinely_real_evidence_classes(
    real_class: str | EvidenceClass,
) -> None:
    mapping = translate_legacy_token(
        "backend/status alias",
        "cached-real",
        inherited_evidence_class=real_class,
    )
    expected = (
        real_class.value if isinstance(real_class, EvidenceClass) else real_class
    )
    assert mapping.cache_state == "cached_real"
    assert mapping.evidence_class == expected
    assert mapping.requires_inherited_evidence_class is False
    assert expected not in CERTIFICATION_DENYLIST


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
        "diagnostic_internal_analytical",
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
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED: 999,
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED: 999,
    }

    assert CERTIFICATION_DENYLIST == frozenset(
        {
            "internal-analytical",
            "diagnostic-shadow",
            "C-henrian-screen",
            "B-dilute-screen",
            "EXT-SP",
            VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
            VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        }
    )
    assert not may_certify(
        EvidenceClass.INTERNAL_ANALYTICAL,
        hostile_ordering,
        ordering={"internal-analytical": "first"},
    )
    assert not may_certify(
        EvidenceClass.ANALYTICAL_VAPOROCK_CALIBRATED,
        hostile_ordering,
    )
    assert not may_certify(
        EvidenceClass.ANALYTICAL_EXTERNAL_GROUNDED,
        hostile_ordering,
    )
    assert may_certify(EvidenceClass.MELTS, hostile_ordering)


@pytest.mark.parametrize(
    ("evidence_class", "expected"),
    [
        ("melts", True),
        ("magemin", True),
        ("internal-datatables", True),
        ("internal-analytical", False),
        ("diagnostic-shadow", False),
        ("C-henrian-screen", False),
        ("B-dilute-screen", False),
        ("EXT-SP", False),
        (VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED, False),
        (VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED, False),
    ],
)
def test_may_certify_registered_canonical_classes(
    evidence_class: str, expected: bool
) -> None:
    assert may_certify(evidence_class) is expected


def test_may_certify_accepts_evidence_class_enum() -> None:
    assert may_certify(EvidenceClass.MELTS) is True
    assert may_certify(EvidenceClass.INTERNAL_ANALYTICAL) is False
    assert may_certify(EvidenceClass.ANALYTICAL_VAPOROCK_CALIBRATED) is False
    assert may_certify(EvidenceClass.ANALYTICAL_EXTERNAL_GROUNDED) is False


@pytest.mark.parametrize(
    "token",
    sorted(RATIFIED_VAPOUR_ANALYTICAL_EVIDENCE_CLASSES),
)
def test_ratified_vapour_analytical_tokens_canonicalize_to_self(token: str) -> None:
    assert canonical_backend_name(token) == token
    assert canonical_backend_name(token.upper()) == token
    assert canonical_backend_name(f"  {token}  ") == token
    # Never fold into the melt-backend analytical identity.
    assert canonical_backend_name(token) != "internal-analytical"
    assert is_ratified_vapour_analytical_evidence_class(token)
    assert backend_name_denies_authority(token) is True


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "totally-made-up-xyz",
        "mixed:",
        "mixed:alphamelts,   ",
        "mixed:   ,alphamelts",
        "mixed:mixed:alphamelts",
        "mixed:alphamelts,totally-made-up-xyz",
        "ok",
        "missing",
        "unavailable",
        "mixed:ok",
        "diagnostic-shadow",
        "C-henrian-screen",
        "B-dilute-screen",
        "EXT-SP",
    ],
)
def test_backend_name_authority_fails_closed_for_missing_unknown_and_diagnostic_tokens(
    token: str | None,
) -> None:
    assert backend_name_denies_authority(token) is True


@pytest.mark.parametrize(
    "token",
    [
        "alphamelts",
        "thermoengine",
        "cached-real",
        EvidenceClass.MELTS,
        EvidenceClass.MAGEMIN,
        EvidenceClass.INTERNAL_DATATABLES,
    ],
)
def test_backend_name_authority_preserves_registered_non_denied_tokens(
    token: str | EvidenceClass,
) -> None:
    assert backend_name_denies_authority(token) is False


@pytest.mark.parametrize(
    (
        "backend_name",
        "evidence_class",
        "requires_inherited_evidence_class",
        "expected",
    ),
    (
        ("alphamelts", None, False, None),
        ("cached-real", "melts", False, None),
        (None, "melts", False, "backend_name_non_authoritative"),
        ("cached-real", None, False, "inherited_evidence_class_required"),
        (
            "cached-real",
            "diagnostic-shadow",
            False,
            "evidence_class_non_authoritative",
        ),
        (
            "alphamelts",
            "diagnostic-shadow",
            False,
            "evidence_class_non_authoritative",
        ),
        ("alphamelts", None, True, "inherited_evidence_class_required"),
        ("alphamelts", "melts", True, "inherited_evidence_class_required"),
    ),
)
def test_backend_evidence_authority_rejection_is_contextual_and_fail_closed(
    backend_name: str | None,
    evidence_class: str | None,
    requires_inherited_evidence_class: bool,
    expected: str | None,
) -> None:
    assert (
        backend_evidence_authority_rejection(
            backend_name,
            evidence_class,
            requires_inherited_evidence_class=requires_inherited_evidence_class,
        )
        == expected
    )


def test_legacy_stub_aliases_still_fold_to_internal_analytical() -> None:
    for alias in ("stub", "diagnostic_stub", "internal_analytical", "internal-analytical"):
        assert canonical_backend_name(alias) == "internal-analytical"


@pytest.mark.parametrize(
    "token",
    sorted(RATIFIED_VAPOUR_ANALYTICAL_EVIDENCE_CLASSES),
)
def test_ratified_vapour_classes_flux_verdict_is_status_bearing_only(
    token: str,
) -> None:
    verdict = vapour_analytical_flux_verdict(token)
    assert verdict["evidence_class"] == token
    assert verdict["verdict_status"] == STATUS_BEARING_NON_AUTHORITATIVE
    assert verdict["certification_ceiling"] == CERTIFICATION_CEILING_NEVER
    assert verdict["certification_allowed"] is False
    assert verdict["authoritative"] is False
    # Only one allowed flux status; never authoritative / certifying.
    assert verdict["verdict_status"] != "authoritative"
    assert may_certify(token) is False

    with pytest.raises(
        FidelityVocabularyTranslationError,
        match="certification emission refused for denylisted",
    ):
        canonicalize_fidelity_emission(
            evidence_class=token,
            backend_status="ok",
            certification_shape=True,
        )


def test_vapour_analytical_flux_verdict_rejects_non_ratified_classes() -> None:
    with pytest.raises(FidelityVocabularyTranslationError, match="ratified O1"):
        vapour_analytical_flux_verdict("internal-analytical")
    with pytest.raises(FidelityVocabularyTranslationError, match="ratified O1"):
        vapour_analytical_flux_verdict("melts")


@pytest.mark.parametrize(
    "token",
    sorted(RATIFIED_VAPOUR_ANALYTICAL_EVIDENCE_CLASSES),
)
def test_vapour_analytical_flux_verdict_accepts_evidence_class_enum(
    token: str,
) -> None:
    """Enum input must unwrap to the token value (not str(Enum) name form)."""

    member = EvidenceClass(token)
    verdict = vapour_analytical_flux_verdict(member)
    assert verdict["evidence_class"] == token
    assert verdict["verdict_status"] == STATUS_BEARING_NON_AUTHORITATIVE
    assert verdict["certification_allowed"] is False
    assert verdict["authoritative"] is False


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
        == "internal-analytical"
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


def test_canonical_emission_preserves_explicit_denied_evidence_over_backend_inference() -> None:
    payload = canonicalize_fidelity_emission(
        backend_name="alphamelts",
        backend_status="ok",
        backend_authoritative=False,
        evidence_class="diagnostic-shadow",
    )

    assert payload["evidence_class"] == "diagnostic-shadow"
    assert payload["certification_allowed"] is False


def test_canonical_emission_refuses_authoritative_backend_evidence_conflict() -> None:
    with pytest.raises(
        FidelityVocabularyTranslationError,
        match="conflicting canonical fidelity field evidence_class",
    ):
        canonicalize_fidelity_emission(
            backend_name="alphamelts",
            backend_status="ok",
            backend_authoritative=True,
            evidence_class="diagnostic-shadow",
        )


def test_canonical_emission_preserves_not_run_honesty() -> None:
    payload = canonicalize_fidelity_emission(
        backend_status="not_run",
        backend_authoritative=False,
    )

    assert payload["runtime_status"] == "not_run"
    assert payload["backend_real_active"] is False
    assert payload["degradation_reason"] == "not_run"
    assert payload["degraded_from"] == ["not_run"]


@pytest.mark.parametrize("backend_name", ["internal-analytical", "stub"])
def test_canonical_emission_refuses_analytical_certification_shape(
    backend_name: str,
) -> None:
    with pytest.raises(
        FidelityVocabularyTranslationError,
        match=(
            "certification emission refused for denylisted "
            "evidence_class='internal-analytical'"
        ),
    ):
        canonicalize_fidelity_emission(
            backend_name=backend_name,
            backend_status="ok",
            backend_authoritative=True,
            certification_shape=True,
        )
