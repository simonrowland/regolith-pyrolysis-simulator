"""Shared runtime disposition for compiled evaluator temperature domains."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


TYPED_REFUSAL_DISPOSITION = "typed_refusal"
REFUSAL_OUTSIDE_DECLARED_DOMAIN = "outside_declared_evaluator_domain"


class DomainPolicyError(ValueError):
    """A compiled row declares an unsupported runtime domain policy."""


@dataclass(frozen=True)
class DomainTransition:
    """One caller-independent decision at the declared evaluator boundary."""

    outside_declared_domain: bool
    disposition: str | None
    refusal_code: str | None = None
    detail: str | None = None

    @property
    def refuses(self) -> bool:
        return self.refusal_code is not None


def declared_domain_transition(
    compiled_species: Any,
    temperature_K: float,
) -> DomainTransition:
    """Resolve one generic OOD transition from compiled metadata and bounds.

    Calibration misses retain the catalog-owned continuation, including legacy
    rows declaring ``typed_refusal``. Missing/invalid evaluator inputs still
    refuse at their owning boundary.
    """

    raw = getattr(getattr(compiled_species, "code_metadata", None), "raw", {})
    disposition = raw.get("out_of_domain_disposition")
    if disposition not in {None, TYPED_REFUSAL_DISPOSITION}:
        raise DomainPolicyError(
            "unsupported code_metadata.out_of_domain_disposition "
            f"{disposition!r}"
        )
    if disposition is None:
        # Existing compiled rows retain their evaluator-owned continuation.
        # Do not require test doubles or legacy callers to expose bounds that
        # this policy does not consume.
        return DomainTransition(False, None)

    temperature = float(temperature_K)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise DomainPolicyError("temperature_K must be finite and positive")
    low, high = compiled_species.valid_temperature_K
    outside = temperature < float(low) or temperature > float(high)
    if not outside:
        return DomainTransition(outside, disposition)

    detail = (
        f"reason={REFUSAL_OUTSIDE_DECLARED_DOMAIN}; authority_level=extrapolated; "
        f"certified_band=[{float(low)}, {float(high)}] K; "
        f"temperature_K={temperature}; using evaluator continuation"
    )
    return DomainTransition(
        outside_declared_domain=True,
        disposition="out_of_range_conservative_continuation",
        detail=detail,
    )
