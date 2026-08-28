"""Canonical trust-vocabulary translation for fidelity surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from simulator.backend_names import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    LEGACY_ANALYTICAL_BACKEND_DIAGNOSTIC_TOKEN,
    LEGACY_ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    RATIFIED_VAPOUR_ANALYTICAL_EVIDENCE_CLASSES,
    VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
    VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
    canonical_backend_name,
)


LEGACY_INTERNAL_ANALYTICAL_VOCABULARY_TOKEN = (
    LEGACY_ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
)


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
    DIAGNOSTIC_SHADOW = "diagnostic-shadow"
    C_HENRIAN_SCREEN = "C-henrian-screen"
    B_DILUTE_SCREEN = "B-dilute-screen"
    EXT_SP = "EXT-SP"
    # O1-ratified vapour-pressure analytical classes (version 1). Future
    # classes registered in design-fidelity-surface; may drive HKL flux only
    # with status_bearing_non_authoritative verdicts; may never certify.
    ANALYTICAL_VAPOROCK_CALIBRATED = VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED
    ANALYTICAL_EXTERNAL_GROUNDED = VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED


CANONICAL_EVIDENCE_CLASSES: frozenset[str] = frozenset(
    item.value for item in EvidenceClass
)

# Flux-only ceiling for O1-ratified vapour analytical classes (never certify).
STATUS_BEARING_NON_AUTHORITATIVE = "status_bearing_non_authoritative"
CERTIFICATION_CEILING_NEVER = "never"


class CacheState(str, Enum):
    LIVE_FILL = "live_fill"
    CACHED_EXACT = "cached_exact"
    CACHED_PHYSICS_BUCKET = "cached_physics_bucket"
    SERVED_NEIGHBOR = "served_neighbor"
    CACHED_REAL = "cached_real"


class RuntimeStatus(str, Enum):
    """Runtime dispositions the fidelity layer can read.

    ★ THE FIVE MEMBERS BELOW THE ORIGINAL LINE EXIST BECAUSE THE INTENT
    VOCABULARY EMITS THEM AND THIS LAYER COULD NOT READ THEM (d-003).
    INTENT_RESULT_STATUSES has eight members; this enum had five, and the five
    that were absent -- not_converged, refused, not_attempted, unsupported,
    non_authoritative -- made canonicalize_fidelity_emission RAISE. The
    optimizer writes three of them onto a run reference and the web layer
    reads that field straight back into canonicalisation, so one layer emitted
    what the other could not read.

    THEY ARE ADDED RATHER THAN MAPPED ONTO EXISTING MEMBERS because the
    mapping is not cosmetic. Per the derivation in
    simulator/chemistry/kernel/dto.py, reporting out_of_domain makes the
    optimizer PRUNE a candidate as physically infeasible -- a permanent
    verdict about the recipe -- while unavailable routes to RETRY, a verdict
    about the tooling. Folding a token onto the wrong one either discards a
    good recipe on evidence never gathered, or retries forever against a real
    infeasibility. Each new member is derived below by the same question the
    dto header asks: WHAT DID WE LEARN ABOUT THE PHYSICS?
    """

    MISSING = "missing"
    OK = "ok"
    UNAVAILABLE = "unavailable"
    OUT_OF_DOMAIN = "out_of_domain"
    NOT_RUN = "not_run"

    #: The engine was present, attempted the solve, and failed to reach
    #: tolerance. That IS a claim about this operating point, but a weak one --
    #: a different seed, bracket or budget may converge. Distinct from
    #: out_of_domain, which asserts the point lies outside calibration and is
    #: therefore permanent. Must not prune.
    NOT_CONVERGED = "not_converged"

    #: The engine was present and DECLINED to compute -- a policy refusal,
    #: e.g. an absolute fO2 imposed on an Fe-free melt. We learned NOTHING
    #: about the physics of this point, so this sits with unavailable rather
    #: than out_of_domain despite the engine being there. Treating a refusal
    #: as a physics claim is exactly the laundering this branch has been
    #: removing. Must not prune.
    REFUSED = "refused"

    #: The probe never ran, typically because a prior step closed the
    #: transport. Nothing was learned. Distinct from not_run, which describes a
    #: class that was never scheduled, where this describes one that was
    #: scheduled and skipped.
    NOT_ATTEMPTED = "not_attempted"

    #: The engine does not implement this operation at all. A statement about
    #: tooling capability, not about the composition. Nothing learned about the
    #: physics; retrying the same engine cannot help, but the recipe is
    #: unjudged. Must not prune.
    UNSUPPORTED = "unsupported"

    #: A number EXISTS but its provenance does not meet the certification bar.
    #: This is the one member on a different axis from the rest: the others
    #: answer whether a result exists, this answers whether an existing result
    #: may be trusted. It must not be collapsed into missing -- a value is
    #: present and may legitimately be used where authority is not required.
    NON_AUTHORITATIVE = "non_authoritative"


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
    LEGACY_BACKEND_ALIAS_INTERNAL_ANALYTICAL = "legacy_backend_alias:stub"
    BACKEND_INTERNAL_ANALYTICAL = "backend_alias:internal-analytical"
    DIAGNOSTIC_INTERNAL_ANALYTICAL = "diagnostic_internal_analytical"
    BACKEND_ALIAS_ALPHAMELTS = "backend_alias:alphamelts"
    BACKEND_ALIAS_THERMOENGINE = "backend_alias:thermoengine"
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
    # Paired with the RuntimeStatus members added for d-003. Each keeps its own
    # reason rather than reusing a near neighbour, because the reason is what a
    # reader sees when asking WHY a result degraded, and collapsing two causes
    # into one label is how the distinctions above got lost in the first place.
    NOT_CONVERGED = "not_converged"
    REFUSED = "refused"
    NOT_ATTEMPTED = "not_attempted"
    UNSUPPORTED = "unsupported"
    NON_AUTHORITATIVE = "non_authoritative"


CERTIFICATION_DENYLIST: frozenset[str] = frozenset(
    {
        EvidenceClass.INTERNAL_ANALYTICAL.value,
        EvidenceClass.DIAGNOSTIC_SHADOW.value,
        EvidenceClass.C_HENRIAN_SCREEN.value,
        EvidenceClass.B_DILUTE_SCREEN.value,
        EvidenceClass.EXT_SP.value,
        EvidenceClass.ANALYTICAL_VAPOROCK_CALIBRATED.value,
        EvidenceClass.ANALYTICAL_EXTERNAL_GROUNDED.value,
    }
)

LEGACY_EVIDENCE_CLASS_SERIALIZATION_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        EvidenceClass.INTERNAL_ANALYTICAL.value:
            ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    }
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
                LEGACY_INTERNAL_ANALYTICAL_VOCABULARY_TOKEN,
                LEGACY_ANALYTICAL_BACKEND_DIAGNOSTIC_TOKEN,
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
        (
            "backend/status alias",
            LEGACY_INTERNAL_ANALYTICAL_VOCABULARY_TOKEN,
        ): CanonicalFidelityMapping(
            evidence_class=EvidenceClass.INTERNAL_ANALYTICAL.value,
            label_source=LabelSource.LEGACY_BACKEND_ALIAS_INTERNAL_ANALYTICAL.value,
        ),
        (
            "backend/status alias",
            ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        ): CanonicalFidelityMapping(
            evidence_class=EvidenceClass.INTERNAL_ANALYTICAL.value,
            label_source=LabelSource.BACKEND_INTERNAL_ANALYTICAL.value,
        ),
        (
            "backend/status alias",
            LEGACY_ANALYTICAL_BACKEND_DIAGNOSTIC_TOKEN,
        ): CanonicalFidelityMapping(
            evidence_class=EvidenceClass.INTERNAL_ANALYTICAL.value,
            label_source=LabelSource.DIAGNOSTIC_INTERNAL_ANALYTICAL.value,
            degradation_reason=DegradationReason.DIAGNOSTIC_ONLY.value,
        ),
        ("backend/status alias", "alphamelts"): CanonicalFidelityMapping(
            evidence_class=EvidenceClass.MELTS.value,
            label_source=LabelSource.BACKEND_ALIAS_ALPHAMELTS.value,
        ),
        ("backend/status alias", "thermoengine"): CanonicalFidelityMapping(
            evidence_class=EvidenceClass.MELTS.value,
            label_source=LabelSource.BACKEND_ALIAS_THERMOENGINE.value,
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
        # d-003: the five intent tokens this layer previously could not read.
        # Adding the enum members alone was INERT -- canonicalisation resolves
        # through this alias table, so without these entries it kept raising
        # UnknownFidelityVocabularyTokenError on exactly the tokens the
        # optimizer writes onto a run reference. Verified by execution before
        # and after.
        ("backend/status alias", "not_converged"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.NOT_CONVERGED.value,
            degradation_reason=DegradationReason.NOT_CONVERGED.value,
        ),
        ("backend/status alias", "refused"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.REFUSED.value,
            degradation_reason=DegradationReason.REFUSED.value,
        ),
        ("backend/status alias", "not_attempted"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.NOT_ATTEMPTED.value,
            degradation_reason=DegradationReason.NOT_ATTEMPTED.value,
        ),
        ("backend/status alias", "unsupported"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.UNSUPPORTED.value,
            degradation_reason=DegradationReason.UNSUPPORTED.value,
        ),
        ("backend/status alias", "non_authoritative"): CanonicalFidelityMapping(
            runtime_status=RuntimeStatus.NON_AUTHORITATIVE.value,
            degradation_reason=DegradationReason.NON_AUTHORITATIVE.value,
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
    if (
        canonical_family == "backend/status alias"
        and token_text in CANONICAL_EVIDENCE_CLASSES
    ):
        return CanonicalFidelityMapping(evidence_class=token_text)

    raise UnknownFidelityVocabularyTokenError(
        family,
        token,
        artifact_digest=artifact_digest,
        migration_chunk=migration_chunk,
    )


def may_certify(evidence_class: str | EvidenceClass | None) -> bool:
    """Can this KIND of computation EVER be certified? A CLASS property.

    ★ THIS IS NOT A PER-RESULT VERDICT. It answers whether the evidence class
    is certifiable in principle, and knows nothing about how any particular
    run went. For "may THIS result be trusted", use
    result_certification_allowed below (b-270).

    The signature previously accepted *ordering_inputs and **ordering_kwargs
    and immediately deleted them, which is the conflation made literal: it
    ACCEPTED per-result context and discarded it, so every caller that wanted
    a per-result answer silently received a class capability. Those
    parameters are now gone, which makes "certification cannot be swayed by
    ordering context" a STRUCTURAL guarantee rather than a behavioural one --
    a function that cannot receive the inputs cannot be influenced by them.
    """
    if evidence_class is None:
        return False
    return _evidence_class_value(evidence_class) not in CERTIFICATION_DENYLIST


#: The only runtime disposition under which a RESULT may be certified.
#: Everything else in RuntimeStatus is a caveat of some kind, and a caveated
#: result has not earned certification whatever its evidence class permits in
#: principle. Kept as a set derived from the enum rather than a hand-listed
#: literal so a member added to RuntimeStatus is covered the day it is added
#: -- the same partition discipline as the degrading/flattering split in the
#: kernel DTO.
CERTIFYING_RUNTIME_STATUSES: frozenset[str] = frozenset({RuntimeStatus.OK.value})
NON_CERTIFYING_RUNTIME_STATUSES: frozenset[str] = (
    frozenset(member.value for member in RuntimeStatus)
    - CERTIFYING_RUNTIME_STATUSES
)


def result_certification_allowed(
    evidence_class: str | EvidenceClass | None,
    runtime_status: str | RuntimeStatus | None,
) -> bool:
    """May THIS RESULT be certified? A PER-RESULT verdict (b-270).

    Both axes must permit: the evidence class must be certifiable in
    principle AND this particular run must have completed without a caveat.

    ★ ABSENCE IS NOT PERMISSION. A missing runtime status returns False
    rather than deferring to the class, because a result that cannot say how
    it went has not earned a certificate. That is the same rule the store
    already applies on admission, stated once here so callers stop
    re-deriving it inconsistently.

    Not yet wired into canonicalisation: this lands as a pure helper so the
    split is pinned by tests before any publisher changes meaning.
    """
    if not may_certify(evidence_class):
        return False
    if runtime_status is None:
        return False
    value = (
        runtime_status.value
        if isinstance(runtime_status, RuntimeStatus)
        else str(runtime_status)
    )
    return value in CERTIFYING_RUNTIME_STATUSES


def is_ratified_vapour_analytical_evidence_class(
    evidence_class: str | EvidenceClass | None,
) -> bool:
    """True for O1-ratified vapour analytical evidence classes (version 1)."""

    if evidence_class is None:
        return False
    try:
        value = _evidence_class_value(evidence_class)
    except (UnknownFidelityVocabularyTokenError, FidelityVocabularyTranslationError):
        # Fold case/alias through the name-keyed boundary first.
        normalized = canonical_backend_name(str(evidence_class).strip())
        return (
            normalized is not None
            and normalized in RATIFIED_VAPOUR_ANALYTICAL_EVIDENCE_CLASSES
        )
    return value in RATIFIED_VAPOUR_ANALYTICAL_EVIDENCE_CLASSES


def vapour_analytical_flux_verdict(
    evidence_class: str | EvidenceClass,
) -> dict[str, Any]:
    """Return the only allowed flux verdict for an O1-ratified vapour class.

    Ceiling (owner O1, 2026-07-31): may drive HKL flux only with
    ``status_bearing_non_authoritative`` verdicts; may never certify, earn a
    certification vote, satisfy an authority gate, or be serialized as
    authoritative. Validation status is orthogonal and never lifts this.
    """

    # Unwrap EvidenceClass first: str(Enum member) is "EvidenceClass.NAME",
    # not the token value, so the advertised enum input would always fail.
    try:
        raw = _evidence_class_value(evidence_class)
    except (UnknownFidelityVocabularyTokenError, FidelityVocabularyTranslationError):
        raw = str(evidence_class).strip()
    normalized = canonical_backend_name(raw)
    if normalized not in RATIFIED_VAPOUR_ANALYTICAL_EVIDENCE_CLASSES:
        raise FidelityVocabularyTranslationError(
            "vapour_analytical_flux_verdict requires a ratified O1 vapour "
            f"analytical evidence class; got {evidence_class!r}"
        )
    value = _evidence_class_value(normalized)
    return {
        "evidence_class": value,
        "verdict_status": STATUS_BEARING_NON_AUTHORITATIVE,
        "certification_ceiling": CERTIFICATION_CEILING_NEVER,
        "certification_allowed": False,
        "authoritative": False,
        # Typed fields remain distinct from provider/backend and label_source.
        "provider_field_note": (
            "provider remains builtin|vaporock; this token is evidence_class only"
        ),
    }


def backend_name_denies_authority(backend_name: str | None) -> bool:
    """Return True when backend identity independently forbids authoritative admission."""

    # This is a trust predicate: a missing or unknown identity cannot prove
    # authority. Denial is the safe default; permitting would make typos and
    # future unregistered evidence classes authoritative.
    if backend_name is None:
        return True
    raw_name = (
        backend_name.value
        if isinstance(backend_name, EvidenceClass)
        else str(backend_name)
    )
    normalized = canonical_backend_name(raw_name.strip())
    if not normalized:
        return True
    if normalized in CANONICAL_EVIDENCE_CLASSES:
        return normalized in CERTIFICATION_DENYLIST
    if normalized.startswith("mixed:"):
        suffix = normalized[len("mixed:") :]
        for delimiter in ("+", "|"):
            suffix = suffix.replace(delimiter, ",")
        tokens = tuple(token.strip() for token in suffix.split(","))
        if not tokens or any(not token or token.startswith("mixed:") for token in tokens):
            return True
        return any(backend_name_denies_authority(token) for token in tokens)
    try:
        mapping = translate_legacy_token("backend/status alias", normalized)
    except (UnknownFidelityVocabularyTokenError, FidelityVocabularyTranslationError):
        return True
    if mapping.evidence_class is None:
        # A translated status is still not an authority-bearing identity.
        # ``cached-real`` is the one contextual wrapper admitted here; its
        # inherited evidence is checked by the fidelity/cache callers.
        return normalized != "cached-real"
    return _evidence_class_value(mapping.evidence_class) in CERTIFICATION_DENYLIST


def backend_evidence_authority_rejection(
    backend_name: str | None,
    inherited_evidence_class: str | EvidenceClass | None = None,
    *,
    requires_inherited_evidence_class: bool = False,
) -> str | None:
    """Return the shared backend/evidence authority rejection code, if any."""

    if requires_inherited_evidence_class:
        return "inherited_evidence_class_required"
    if backend_name_denies_authority(backend_name):
        return "backend_name_non_authoritative"
    raw_name = (
        backend_name.value
        if isinstance(backend_name, EvidenceClass)
        else str(backend_name)
    )
    normalized = canonical_backend_name(raw_name.strip())
    if inherited_evidence_class is None:
        if normalized == "cached-real":
            return "inherited_evidence_class_required"
        return None
    try:
        if not may_certify(inherited_evidence_class):
            return "evidence_class_non_authoritative"
    except (UnknownFidelityVocabularyTokenError, FidelityVocabularyTranslationError):
        return "evidence_class_non_authoritative"
    return None


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
    explicit_evidence_class = (
        _evidence_class_value(evidence_class)
        if evidence_class is not None
        else None
    )
    explicit_evidence_overrides_inference = (
        explicit_evidence_class is not None and backend_authoritative is False
    )
    label_sources: list[str] = []
    degraded_from: list[str] = []
    contributor_payloads: list[dict[str, Any]] = []

    def merge(mapping: CanonicalFidelityMapping) -> None:
        if not explicit_evidence_overrides_inference:
            _merge_scalar(
                data,
                CanonicalDimension.EVIDENCE_CLASS.value,
                mapping.evidence_class,
            )
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
    if explicit_evidence_class is not None:
        if explicit_evidence_overrides_inference:
            data[CanonicalDimension.EVIDENCE_CLASS.value] = explicit_evidence_class
        else:
            _merge_scalar(
                data,
                CanonicalDimension.EVIDENCE_CLASS.value,
                explicit_evidence_class,
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
    # Refuse every never-certify class via CERTIFICATION_DENYLIST, not a
    # single-name check. Failure prevented: a newly ratified denylisted
    # class (e.g. O1 vapour analytical tokens) would otherwise pass the
    # cached-real earn/dress path and be serialized with real standing —
    # an O1 ceiling bypass that name-enumeration would reintroduce on
    # every future class addition.
    if evidence_class is not None and evidence_class in CERTIFICATION_DENYLIST:
        raise FidelityVocabularyTranslationError(
            f"cached-real cannot dress never-certify evidence_class "
            f"{evidence_class!r} as real"
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
