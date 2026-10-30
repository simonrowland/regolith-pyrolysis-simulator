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

    Rows without an explicit disposition retain the catalog's normal
    status-bearing continuation. Rows declaring ``typed_refusal`` refuse before
    any evaluator is called, so a caller cannot widen a source polynomial's
    domain by reaching an outer continuation path.
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
    if not math.isfinite(temperature):
        raise DomainPolicyError("temperature_K must be finite")
    low, high = compiled_species.valid_temperature_K
    outside = temperature < float(low) or temperature > float(high)
    if not outside:
        return DomainTransition(outside, disposition)

    detail = (
        f"temperature {temperature} K is outside declared evaluator domain "
        f"[{float(low)}, {float(high)}] K; conservative continuation is not "
        "admitted for this row"
    )
    return DomainTransition(
        outside_declared_domain=True,
        disposition=disposition,
        refusal_code=REFUSAL_OUTSIDE_DECLARED_DOMAIN,
        detail=detail,
    )
