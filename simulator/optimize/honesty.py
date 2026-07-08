"""Shared optimizer honesty/tier label producer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simulator.fidelity_vocabulary import canonicalize_fidelity_emission

_CERTIFIED_CACHE_TIERS = frozenset({"cached_exact", "live_fill"})
_ESTIMATED_CACHE_TIERS = frozenset({"cached_physics_bucket", "cached_interpolated"})
_LEGACY_EVIDENCE_BACKEND_ALIASES = frozenset({"stub", "diagnostic_stub"})


def optimizer_tier_label(
    run_reference: Mapping[str, Any],
    result_blob: Mapping[str, Any],
    *,
    backend_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce the optimizer honesty/tier label used by web and artifacts."""

    stored_state = _stored_reduced_real_cache_state(run_reference, result_blob)
    evidence_class = _stored_evidence_class(run_reference, result_blob)
    proof_rank = _stored_proof_rank(run_reference, result_blob)
    backend_name = _mapping_value(run_reference).get("backend_name")
    if backend_name is None:
        backend_name = _mapping_value(result_blob).get("backend_name")
    backend_status = _mapping_value(run_reference).get("backend_status")
    if backend_status is None:
        backend_status = _mapping_value(result_blob).get("backend_status")
    backend_authoritative = _optional_bool(
        _mapping_value(run_reference).get("backend_authoritative")
    )
    if backend_authoritative is None:
        backend_authoritative = _optional_bool(
            _mapping_value(result_blob).get("backend_authoritative")
        )
    if isinstance(backend_payload, Mapping):
        backend_status = backend_payload.get("backend_status") or backend_status
        backend_authoritative = _optional_bool(
            backend_payload.get("backend_authoritative")
        )
        if backend_authoritative is None:
            backend_authoritative = _optional_bool(
                backend_payload.get("backend_real_active")
            )
        if evidence_class is None:
            evidence_class = backend_payload.get("evidence_class")
        if backend_name is None:
            backend_name = backend_payload.get("backend_name")

    canonical_evidence_class = evidence_class
    canonical_backend_name = backend_name
    if (
        isinstance(canonical_evidence_class, str)
        and canonical_evidence_class in _LEGACY_EVIDENCE_BACKEND_ALIASES
    ):
        canonical_backend_name = canonical_backend_name or canonical_evidence_class
        canonical_evidence_class = None

    if stored_state is not None:
        canonical = canonicalize_fidelity_emission(
            reduced_real_cache_state=stored_state,
            evidence_class=canonical_evidence_class,
            backend_name=canonical_backend_name if canonical_evidence_class is None else None,
            backend_status=backend_status if canonical_evidence_class is None else None,
        )
    else:
        canonical = canonicalize_fidelity_emission(
            evidence_class=canonical_evidence_class,
            backend_name=canonical_backend_name,
            backend_status=backend_status,
            backend_authoritative=backend_authoritative,
        )
    certification_allowed = bool(canonical.get("certification_allowed", False))
    tier = stored_state or "unknown"
    if (
        tier in _CERTIFIED_CACHE_TIERS
        and certification_allowed
        and bool(backend_authoritative)
    ):
        ux_label = "CERTIFIED"
    elif tier in _ESTIMATED_CACHE_TIERS:
        ux_label = "ESTIMATED"
    else:
        ux_label = "UNVERIFIED"

    label = {
        "tier": tier,
        "evidence_class": canonical.get("evidence_class") or evidence_class,
        "ux_label": ux_label,
        "certification_allowed": certification_allowed,
        "title": _tier_label_title(run_reference, result_blob, tier=tier),
        "canonical": canonical,
    }
    if proof_rank is not None:
        label["evidence_rank"] = proof_rank
        label["proof_rank"] = proof_rank
        label["proof_grade"] = proof_rank
    return label


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
        return False
    return False


def _stored_reduced_real_cache_state(
    run_reference: Mapping[str, Any],
    result_blob: Mapping[str, Any],
) -> str | None:
    for carrier in (run_reference, result_blob):
        if not isinstance(carrier, Mapping):
            continue
        for key in ("cache_state", "reduced_real_cache_state"):
            raw = carrier.get(key)
            if raw is not None and str(raw).strip():
                return str(raw)
        per_hour = carrier.get("per_hour_summary")
        if isinstance(per_hour, list) and per_hour:
            last = per_hour[-1]
            if isinstance(last, Mapping):
                for key in ("reduced_real_cache_state", "cache_state"):
                    raw = last.get(key)
                    if raw is not None and str(raw).strip():
                        return str(raw)
    return None


def _stored_evidence_class(
    run_reference: Mapping[str, Any],
    result_blob: Mapping[str, Any],
) -> str | None:
    for carrier in (run_reference, result_blob):
        if not isinstance(carrier, Mapping):
            continue
        raw = carrier.get("evidence_class")
        if raw is not None and str(raw).strip():
            return str(raw)
    return None


def _stored_proof_rank(
    run_reference: Mapping[str, Any],
    result_blob: Mapping[str, Any],
) -> str | None:
    for carrier in (run_reference, result_blob):
        if not isinstance(carrier, Mapping):
            continue
        for key in ("evidence_rank", "proof_rank", "proof_grade"):
            raw = carrier.get(key)
            if raw is not None and str(raw).strip():
                return str(raw)
    return None


def _tier_label_title(
    run_reference: Mapping[str, Any],
    result_blob: Mapping[str, Any],
    *,
    tier: str | None,
) -> str:
    parts: list[str] = []
    if tier:
        parts.append(f"tier={tier}")
    for carrier in (run_reference, result_blob):
        if not isinstance(carrier, Mapping):
            continue
        for key in ("cache_rung", "physics_rung", "sig_fig_rung", "rung"):
            raw = carrier.get(key)
            if raw is not None:
                parts.append(f"rung={raw}")
                break
        disagreement = carrier.get("neighbor_disagreement")
        if isinstance(disagreement, Mapping):
            if disagreement.get("max") is not None:
                parts.append(f"neighbor_disagreement_max={disagreement['max']}")
            elif disagreement.get("p95") is not None:
                parts.append(f"neighbor_disagreement_p95={disagreement['p95']}")
        reduced_real = carrier.get("reduced_real_cache")
        if isinstance(reduced_real, Mapping):
            err = reduced_real.get("interpolation_error_estimate")
            if isinstance(err, Mapping) and err.get("max") is not None:
                parts.append(f"interpolation_error_max={err['max']}")
    return "; ".join(parts) if parts else "cache tier from stored artifact"
