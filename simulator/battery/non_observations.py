"""Shared rules for records kept as context or reference, not observations."""

from __future__ import annotations

from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    MEASURED_EVIDENCE,
    ValueKind,
)
from simulator.battery.identity import Identity
from simulator.battery.records import Observation, State


INTERNAL_CONSISTENCY_STEMS: frozenset[str] = frozenset(
    {
        "gibbs_battery_residual_ledger.yaml",
        "species_rail_differential_ledger.yaml",
    }
)
# Unmapped tabulated rows stay context; other source-stated evidence can remain
# a candidate without a closed quantity token.
_SOURCE_STATED_EVIDENCE = (
    MEASURED_EVIDENCE - {EvidenceClass.MEASURED_TABULATED}
) | {
    EvidenceClass.QUOTED_ATTRIBUTED,
    EvidenceClass.QUOTED_UNATTRIBUTED,
    EvidenceClass.FIGURE_ONLY,
    EvidenceClass.COMPILATION_ASSESSED,
}


def _compilation_role_flags(relation: str) -> dict[str, bool | None]:
    """Parse compilation-role declarations carried in a relation string."""

    flags: dict[str, bool | None] = {
        "engine_reference_input": None,
        "scoring_eligible": None,
    }
    if not relation:
        return flags
    normalized = relation.replace(" ", "").lower()
    for key in flags:
        true_token = f"{key}=true"
        false_token = f"{key}=false"
        if true_token in normalized:
            flags[key] = True
        elif false_token in normalized:
            flags[key] = False
    return flags


def is_internal_consistency(origin: str | None) -> bool:
    if not origin:
        return False
    stem = origin.replace("\\", "/").rsplit("/", 1)[-1]
    return stem in INTERNAL_CONSISTENCY_STEMS


def is_non_observation(observation: Observation, origin: str | None = None) -> bool:
    """Return whether a stored record is context/reference, not an observation.

    An unknown quantity and unavailable value identify context only when the
    source did not also declare a candidate observation. Explicit refusals,
    schema-gap refusals, and unsupported quantities with source-stated
    candidate evidence remain observations for queueing or candidate refusal.
    """

    if is_internal_consistency(origin):
        return True
    identity = observation.identity
    if (
        isinstance(identity, Identity)
        and isinstance(identity.quantity, State)
        and identity.quantity.is_unknown
        and observation.value.kind is ValueKind.UNAVAILABLE
    ):
        if observation.admission.status is AdmissionStatus.REJECTED:
            return False
        quantity_reason = identity.quantity.reason
        if quantity_reason and quantity_reason == observation.value.unavailable_reason:
            return False
        evidence = observation.evidence
        if (
            quantity_reason
            and quantity_reason.startswith("unsupported quantity ")
            and evidence.class_.is_value
            and evidence.class_.value in _SOURCE_STATED_EVIDENCE
            and bool(evidence.original_method_class)
        ):
            return False
        return True
    derivation = observation.derivation
    if derivation is None:
        return False
    role = _compilation_role_flags(derivation.relation)
    return (
        role["engine_reference_input"] is True
        and role["scoring_eligible"] is False
    )
