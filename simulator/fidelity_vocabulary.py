"""Canonical trust-vocabulary translation for fidelity surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from simulator.backend_names import canonical_backend_name


class CanonicalDimension(str, Enum):
    EVIDENCE_CLASS = "evidence_class"
    CACHE_STATE = "cache_state"
    RUNTIME_STATUS = "runtime_status"
    LABEL_SOURCE = "label_source"
    DEGRADATION_REASON = "degradation_reason"


CANONICAL_DIMENSIONS: tuple[str, ...] = tuple(item.value for item in CanonicalDimension)


class EvidenceClass(str, Enum):
    MELTS = "melts"
    MAGEMIN = "magemin"
    INTERNAL_DATATABLES = "internal-datatables"
    INTERNAL_ANALYTICAL = "internal-analytical"


CANONICAL_EVIDENCE_CLASSES: frozenset[str] = frozenset(
    item.value for item in EvidenceClass
)


class CacheState(str, Enum):
    LIVE_FILL = "live_fill"
    CACHED_EXACT = "cached_exact"
    CACHED_PHYSICS_BUCKET = "cached_physics_bucket"
    SERVED_NEIGHBOR = "served_neighbor"
    CACHED_REAL = "cached_real"


class RuntimeStatus(str, Enum):
    MISSING = "missing"
    OK = "ok"
    UNAVAILABLE = "unavailable"
    OUT_OF_DOMAIN = "out_of_domain"
    NOT_RUN = "not_run"


class LabelSource(str, Enum):
    LIQUIDUS_SOLIDUS_KERNEL = "liquidus_solidus:kernel"
    LIQUIDUS_SOLIDUS_KERNEL_COMPOSITION_DERIVED = (
        "liquidus_solidus:kernel:composition_derived"
    )
    COMPOSITION_DERIVED = "composition_derived"
    PROOF_INPUTS = "proof_inputs"
    TERMINAL_RUMP_EARNED_CRASH = "terminal_rump:earned_crash"
    TERMINAL_RUMP_COMPLETED_RUN = "terminal_rump:completed_run"
    TERMINAL_RUMP_TAP_TRUNCATED = "terminal_rump:tap_truncated"
    LEGACY_BACKEND_ALIAS_STUB = "legacy_backend_alias:stub"
    DIAGNOSTIC_STUB = "diagnostic_stub"
    BACKEND_ALIAS_ALPHAMELTS = "backend_alias:alphamelts"
    BACKEND_SELECTION_AUTO = "backend_selection:auto"
    CACHED_REAL = "cached-real"
    MIXED = "mixed"
    MIXED_BACKEND = "mixed_backend"
    LEGACY_BACKEND_AUTHORITATIVE = "legacy_backend_authoritative"


class DegradationReason(str, Enum):
    TAP_TRUNCATED = "tap_truncated"
    LEGACY_CACHED_INTERPOLATED = "legacy_cached_interpolated"
    DIAGNOSTIC_ONLY = "diagnostic_only"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    OUT_OF_DOMAIN = "out_of_domain"
    NOT_RUN = "not_run"


CERTIFICATION_DENYLIST: frozenset[str] = frozenset(
    {EvidenceClass.INTERNAL_ANALYTICAL.value}
)

LEGACY_EVIDENCE_CLASS_SERIALIZATION_ALIASES: Mapping[str, str] = MappingProxyType(
    {EvidenceClass.INTERNAL_ANALYTICAL.value: "stub"}
)

LEGACY_VOCABULARY_TOKENS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "curve_source": frozenset(
            {
                "liquidus_solidus:kernel",
                "liquidus_solidus:kernel:composition_derived",
                "composition_derived",
                "proof_inputs",
            }
        ),
        "terminal_rump_source": frozenset(
            {"earned_crash", "completed_run", "tap_truncated"}
        ),
        "reduced_real_cache_state": frozenset(
            {
                "live_fill",
                "cached_exact",
                "cached_physics_bucket",
                "cached_interpolated",
            }
        ),
        "backend/status alias": frozenset(
            {
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
            }
        ),
        "legacy runtime field": frozenset({"backend_authoritative"}),
    }
)

DESIGN_LEGACY_MAPPING_ROW_COUNT = sum(
    len(tokens) for tokens in LEGACY_VOCABULARY_TOKENS.values()
)


class FidelityVocabularyTranslationError(ValueError):
    """Raised when a known token cannot be safely translated without context."""


class UnknownFidelityVocabularyTokenError(FidelityVocabularyTranslationError):
    """Raised when legacy fidelity vocabulary would otherwise pass through opaque."""

    def __init__(
        self,
        legacy_field: str,
        token: object,
        *,
        artifact_digest: str | None = None,
        migration_chunk: str = "chunk-1",
        hint: str | None = None,
    ) -> None:
        self.legacy_field = legacy_field
        self.token = token
        self.artifact_digest = artifact_digest
        self.migration_chunk = migration_chunk
        self.hint = hint
        message = (
            "unknown fidelity vocabulary token "
            f"legacy_field={legacy_field!r} token={token!r} "
            f"artifact_digest={artifact_digest!r} migration_chunk={migration_chunk!r}"
        )
        if hint is not None:
            message = f"{message}; {hint}"
        super().__init__(message)


@dataclass(frozen=True)
class CanonicalFidelityMapping:
    evidence_class: str | None = None
    cache_state: str | None = None
    runtime_status: str | None = None
    label_source: str | None = None
    degradation_reason: str | None = None
    backend_real_active: bool | None = None
    contributors: tuple["CanonicalFidelityMapping", ...] = field(default_factory=tuple)
    requires_inherited_evidence_class: bool = False

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key in CANONICAL_DIMENSIONS:
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        if self.backend_real_active is not None:
            data["backend_real_active"] = self.backend_real_active
        if self.contributors:
            data["contributors"] = [contributor.as_dict() for contributor in self.contributors]
        if self.requires_inherited_evidence_class:
            data["requires_inherited_evidence_class"] = True
        return data


_FAMILY_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "curve_source": "curve_source",
        "curve_source / emitted provenance": "curve_source",
        "emitted provenance": "curve_source",
        "terminal_rump_source": "terminal_rump_source",
        "reduced_real_cache_state": "reduced_real_cache_state",
        "backend/status alias": "backend/status alias",
        "backend alias": "backend/status alias",
        "status alias": "backend/status alias",
        "backend": "backend/status alias",
        "legacy runtime field": "legacy runtime field",
    }
)

_SIMPLE_TRANSLATIONS: Mapping[tuple[str, str], CanonicalFidelityMapping] = MappingProxyType(
    {
        (
            "curve_source",
            "liquidus_solidus:kernel",
        ): CanonicalFidelityMapping(
            label_source=LabelSource.LIQUIDUS_SOLIDUS_KERNEL.value
        ),
        (
            "curve_source",
            "liquidus_solidus:kernel:composition_derived",
        ): CanonicalFidelityMapping(
            label_source=LabelSource.LIQUIDUS_SOLIDUS_KERNEL_COMPOSITION_DERIVED.value
        ),
        ("curve_source", "composition_derived"): CanonicalFidelityMapping(
            label_source=LabelSource.COMPOSITION_DERIVED.value
        ),
        ("curve_source", "proof_inputs"): CanonicalFidelityMapping(
            label_source=LabelSource.PROOF_INPUTS.value
        ),
        ("terminal_rump_source", "earned_crash"): CanonicalFidelityMapping(
            label_source=LabelSource.TERMINAL_RUMP_EARNED_CRASH.value
        ),
        ("terminal_rump_source", "completed_run"): CanonicalFidelityMapping(
            label_source=LabelSource.TERMINAL_RUMP_COMPLETED_RUN.value
        ),
        ("terminal_rump_source", "tap_truncated"): CanonicalFidelityMapping(
            label_source=LabelSource.TERMINAL_RUMP_TAP_TRUNCATED.value,
            degradation_reason=DegradationReason.TAP_TRUNCATED.value,
        ),
        ("reduced_real_cache_state", "live_fill"): CanonicalFidelityMapping(
            cache_state=CacheState.LIVE_FILL.value
        ),
        ("reduced_real_cache_state", "cached_exact"): CanonicalFidelityMapping(
            cache_state=CacheState.CACHED_EXACT.value
        ),
        (
            "reduced_real_cache_state",
            "cached_physics_bucket",
        ): CanonicalFidelityMapping(
            cache_state=CacheState.CACHED_PHYSICS_BUCKET.value
        ),
        ("reduced_real_cache_state", "cached_interpolated"): CanonicalFidelityMapping(
            cache_state=CacheState.SERVED_NEIGHBOR.value,
            degradation_reason=DegradationReason.LEGACY_CACHED_INTERPOLATED.value,
        ),
        ("backend/status alias", "stub"): CanonicalFidelityMapping(
            evidence_class=EvidenceClass.INTERNAL_ANALYTICAL.value,
            label_source=LabelSource.LEGACY_BACKEND_ALIAS_STUB.value,
        ),
        ("backend/status alias", "diagnostic_stub"): CanonicalFidelityMapping(
            evidence_class=EvidenceClass.INTERNAL_ANALYTICAL.value,
            label_source=LabelSource.DIAGNOSTIC_STUB.value,
            degradation_reason=DegradationReason.DIAGNOSTIC_ONLY.value,
        ),
        ("backend/status alias", "alphamelts"): CanonicalFidelityMapping(
            evidence_class=EvidenceClass.MELTS.value,
            label_source=LabelSource.BACKEND_ALIAS_ALPHAMELTS.value,
        ),
        ("backend/status alias", "missing"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.MISSING.value,
            degradation_reason=DegradationReason.MISSING.value,
        ),
        ("backend/status alias", "ok"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.OK.value
        ),
        ("backend/status alias", "unavailable"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.UNAVAILABLE.value,
            degradation_reason=DegradationReason.UNAVAILABLE.value,
        ),
        ("backend/status alias", "out_of_domain"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.OUT_OF_DOMAIN.value,
            degradation_reason=DegradationReason.OUT_OF_DOMAIN.value,
        ),
        ("backend/status alias", "not_run"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.NOT_RUN.value,
            degradation_reason=DegradationReason.NOT_RUN.value,
        ),
        ("backend/status alias", "no_compared_results"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.NOT_RUN.value,
            degradation_reason=DegradationReason.NOT_RUN.value,
        ),
    }
)


def translate_legacy_token(
    family: str,
    token: object,
    *,
    artifact_digest: str | None = None,
    migration_chunk: str = "chunk-1",
    value: object = None,
    selected_token: str | None = None,
    contributors: Sequence[str] | None = None,
    inherited_evidence_class: str | EvidenceClass | None = None,
) -> CanonicalFidelityMapping:
    canonical_family = _normalize_family(family, token, artifact_digest, migration_chunk)
    token_text = _normalize_token_text(token, family, artifact_digest, migration_chunk)

    if canonical_family == "backend/status alias":
        if token_text == "auto":
            return _translate_auto(
                selected_token=selected_token,
                artifact_digest=artifact_digest,
                migration_chunk=migration_chunk,
            )
        if token_text == "cached-real":
            return _translate_cached_real(inherited_evidence_class)
        if token_text.startswith("mixed:"):
            return _translate_mixed_suffix(
                token_text,
                artifact_digest=artifact_digest,
                migration_chunk=migration_chunk,
            )
        if token_text == "mixed_backend":
            return _translate_mixed_backend(
                contributors,
                artifact_digest=artifact_digest,
                migration_chunk=migration_chunk,
            )

    if canonical_family == "legacy runtime field" and token_text == "backend_authoritative":
        return _translate_backend_authoritative(value)

    result = _SIMPLE_TRANSLATIONS.get((canonical_family, token_text))
    if result is not None:
        return result

    raise UnknownFidelityVocabularyTokenError(
        family,
        token,
        artifact_digest=artifact_digest,
        migration_chunk=migration_chunk,
    )


def may_certify(
    evidence_class: str | EvidenceClass | None,
    *ordering_inputs: object,
    **ordering_kwargs: object,
) -> bool:
    del ordering_inputs, ordering_kwargs
    if evidence_class is None:
        return False
    return _evidence_class_value(evidence_class) not in CERTIFICATION_DENYLIST


def backend_name_denies_authority(backend_name: str | None) -> bool:
    """Return True when backend identity independently forbids authoritative admission."""

    if backend_name is None:
        return False
    normalized = canonical_backend_name(str(backend_name).strip())
    if not normalized:
        return False
    if normalized.startswith("mixed:"):
        suffix = normalized[len("mixed:") :]
        for delimiter in ("+", "|"):
            suffix = suffix.replace(delimiter, ",")
        return any(
            backend_name_denies_authority(token.strip())
            for token in suffix.split(",")
            if token.strip()
        )
    try:
        mapping = translate_legacy_token("backend/status alias", normalized)
    except (UnknownFidelityVocabularyTokenError, FidelityVocabularyTranslationError):
        return False
    if mapping.evidence_class is None:
        return False
    return _evidence_class_value(mapping.evidence_class) in CERTIFICATION_DENYLIST


def canonicalize_fidelity_emission(
    *,
    backend_name: object | None = None,
    backend_status: object | None = None,
    backend_authoritative: object | None = None,
    reduced_real_cache_state: object | None = None,
    evidence_class: object | None = None,
    inherited_evidence_class: str | EvidenceClass | None = None,
    contributors: Sequence[str] | None = None,
    artifact_digest: str | None = None,
    migration_chunk: str = "chunk-1b",
    certification_shape: bool = False,
) -> dict[str, Any]:
    """Return additive canonical trust fields for an emitted payload."""

    data: dict[str, Any] = {}
    label_sources: list[str] = []
    degraded_from: list[str] = []
    contributor_payloads: list[dict[str, Any]] = []

    def merge(mapping: CanonicalFidelityMapping) -> None:
        _merge_scalar(data, CanonicalDimension.EVIDENCE_CLASS.value, mapping.evidence_class)
        _merge_scalar(data, CanonicalDimension.CACHE_STATE.value, mapping.cache_state)
        _merge_scalar(data, CanonicalDimension.RUNTIME_STATUS.value, mapping.runtime_status)
        if mapping.label_source is not None:
            label_sources.append(mapping.label_source)
        if mapping.degradation_reason is not None:
            degraded_from.append(mapping.degradation_reason)
            data.setdefault(
                CanonicalDimension.DEGRADATION_REASON.value,
                mapping.degradation_reason,
            )
        if mapping.backend_real_active is not None:
            _merge_scalar(data, "backend_real_active", mapping.backend_real_active)
        if mapping.requires_inherited_evidence_class:
            data["requires_inherited_evidence_class"] = True
        if mapping.contributors:
            contributor_payloads.extend(
                contributor.as_dict() for contributor in mapping.contributors
            )

    if backend_name is not None:
        merge(
            translate_legacy_token(
                "backend/status alias",
                backend_name,
                artifact_digest=artifact_digest,
                migration_chunk=migration_chunk,
                inherited_evidence_class=inherited_evidence_class,
                contributors=contributors,
            )
        )
    if backend_status is not None:
        merge(
            translate_legacy_token(
                "backend/status alias",
                backend_status,
                artifact_digest=artifact_digest,
                migration_chunk=migration_chunk,
                inherited_evidence_class=inherited_evidence_class,
                contributors=contributors,
            )
        )
    if backend_authoritative is not None:
        merge(
            translate_legacy_token(
                "legacy runtime field",
                "backend_authoritative",
                artifact_digest=artifact_digest,
                migration_chunk=migration_chunk,
                value=backend_authoritative,
            )
        )
    if reduced_real_cache_state is not None:
        merge(
            translate_legacy_token(
                "reduced_real_cache_state",
                reduced_real_cache_state,
                artifact_digest=artifact_digest,
                migration_chunk=migration_chunk,
            )
        )
    if evidence_class is not None:
        _merge_scalar(
            data,
            CanonicalDimension.EVIDENCE_CLASS.value,
            _evidence_class_value(evidence_class),
        )

    if label_sources:
        data[CanonicalDimension.LABEL_SOURCE.value] = label_sources[0]
        if len(label_sources) > 1:
            data["label_sources"] = list(label_sources)
    if degraded_from:
        data["degraded_from"] = list(dict.fromkeys(degraded_from))
    if contributor_payloads:
        data["contributors"] = list(contributor_payloads)

    emitted_evidence_class = data.get(CanonicalDimension.EVIDENCE_CLASS.value)
    if emitted_evidence_class is not None:
        allowed = may_certify(str(emitted_evidence_class))
        data["certification_allowed"] = allowed
        if certification_shape and not allowed:
            raise FidelityVocabularyTranslationError(
                "certification emission refused for denylisted evidence_class="
                f"{emitted_evidence_class!r}"
            )
    elif certification_shape:
        raise FidelityVocabularyTranslationError(
            "certification emission requires canonical evidence_class"
        )
    return data


def legacy_backend_alias_for_evidence_class(
    evidence_class: str | EvidenceClass,
) -> str | None:
    return LEGACY_EVIDENCE_CLASS_SERIALIZATION_ALIASES.get(
        _evidence_class_value(evidence_class)
    )


def _merge_scalar(data: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    existing = data.get(key)
    if existing is None:
        data[key] = value
        return
    if existing != value:
        raise FidelityVocabularyTranslationError(
            f"conflicting canonical fidelity field {key}: {existing!r} vs {value!r}"
        )


def _normalize_family(
    family: str,
    token: object,
    artifact_digest: str | None,
    migration_chunk: str,
) -> str:
    try:
        return _FAMILY_ALIASES[family]
    except KeyError as exc:
        raise UnknownFidelityVocabularyTokenError(
            family,
            token,
            artifact_digest=artifact_digest,
            migration_chunk=migration_chunk,
        ) from exc


def _normalize_token_text(
    token: object,
    family: str,
    artifact_digest: str | None,
    migration_chunk: str,
) -> str:
    if isinstance(token, Enum):
        value = str(token.value)
    else:
        value = str(token)
    if not value:
        raise UnknownFidelityVocabularyTokenError(
            family,
            token,
            artifact_digest=artifact_digest,
            migration_chunk=migration_chunk,
        )
    return value


def _translate_auto(
    *,
    selected_token: str | None,
    artifact_digest: str | None,
    migration_chunk: str,
) -> CanonicalFidelityMapping:
    if not selected_token:
        raise FidelityVocabularyTranslationError(
            "backend/status alias token 'auto' requires selected_token before proof"
        )
    return CanonicalFidelityMapping(
        label_source=LabelSource.BACKEND_SELECTION_AUTO.value,
        contributors=(
            translate_legacy_token(
                "backend/status alias",
                selected_token,
                artifact_digest=artifact_digest,
                migration_chunk=migration_chunk,
            ),
        ),
    )


def _translate_cached_real(
    inherited_evidence_class: str | EvidenceClass | None,
) -> CanonicalFidelityMapping:
    evidence_class = (
        None
        if inherited_evidence_class is None
        else _evidence_class_value(inherited_evidence_class)
    )
    if evidence_class == EvidenceClass.INTERNAL_ANALYTICAL.value:
        raise FidelityVocabularyTranslationError(
            "cached-real cannot dress internal-analytical output as real"
        )
    return CanonicalFidelityMapping(
        cache_state=CacheState.CACHED_REAL.value,
        evidence_class=evidence_class,
        label_source=LabelSource.CACHED_REAL.value,
        requires_inherited_evidence_class=evidence_class is None,
    )


def _translate_mixed_suffix(
    token_text: str,
    *,
    artifact_digest: str | None,
    migration_chunk: str,
) -> CanonicalFidelityMapping:
    suffix = token_text.removeprefix("mixed:")
    contributor_tokens = _split_contributor_suffix(suffix)
    return CanonicalFidelityMapping(
        label_source=LabelSource.MIXED.value,
        contributors=tuple(
            translate_legacy_token(
                "backend/status alias",
                contributor_token,
                artifact_digest=artifact_digest,
                migration_chunk=migration_chunk,
            )
            for contributor_token in contributor_tokens
        ),
    )


def _translate_mixed_backend(
    contributors: Sequence[str] | None,
    *,
    artifact_digest: str | None,
    migration_chunk: str,
) -> CanonicalFidelityMapping:
    if not contributors:
        raise FidelityVocabularyTranslationError(
            "backend/status alias token 'mixed_backend' requires contributor list"
        )
    return CanonicalFidelityMapping(
        label_source=LabelSource.MIXED_BACKEND.value,
        contributors=tuple(
            translate_legacy_token(
                "backend/status alias",
                contributor,
                artifact_digest=artifact_digest,
                migration_chunk=migration_chunk,
            )
            for contributor in contributors
        ),
    )


def _split_contributor_suffix(suffix: str) -> tuple[str, ...]:
    if not suffix:
        raise FidelityVocabularyTranslationError(
            "backend/status alias token 'mixed:*' requires decomposable suffix"
        )
    normalized = suffix
    for delimiter in ("+", "|"):
        normalized = normalized.replace(delimiter, ",")
    tokens = tuple(item.strip() for item in normalized.split(",") if item.strip())
    if not tokens or any(":" in item for item in tokens):
        raise FidelityVocabularyTranslationError(
            "backend/status alias token 'mixed:*' has undecomposable suffix"
        )
    return tokens


def _translate_backend_authoritative(value: object) -> CanonicalFidelityMapping:
    if not isinstance(value, bool):
        raise FidelityVocabularyTranslationError(
            "legacy runtime field 'backend_authoritative' requires boolean value"
        )
    return CanonicalFidelityMapping(
        label_source=LabelSource.LEGACY_BACKEND_AUTHORITATIVE.value,
        backend_real_active=value,
    )


def _evidence_class_value(evidence_class: str | EvidenceClass) -> str:
    if isinstance(evidence_class, EvidenceClass):
        return evidence_class.value
    value = str(evidence_class)
    if not value:
        raise FidelityVocabularyTranslationError("evidence_class may not be empty")
    if value not in CANONICAL_EVIDENCE_CLASSES:
        raise UnknownFidelityVocabularyTokenError(
            "evidence_class",
            evidence_class,
            hint=(
                "certification gates accept one canonical evidence_class; "
                "decompose legacy or mixed tokens with translate_legacy_token first"
            ),
        )
    return value
