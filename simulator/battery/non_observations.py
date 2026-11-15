"""Shared rules for records kept as context or reference, not observations."""

from __future__ import annotations

from simulator.battery.enums import ValueKind
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import Observation


INTERNAL_CONSISTENCY_STEMS: frozenset[str] = frozenset(
    {
        "gibbs_battery_residual_ledger.yaml",
        "species_rail_differential_ledger.yaml",
    }
)


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

    Unknown-quantity rows count only when they carry no value. Known-quantity
    typed absences remain observations because they name an observable.
    """

    if is_internal_consistency(origin):
        return True
    identity = observation.identity
    if (
        isinstance(identity, Identity)
        and quantity_token(identity) is None
        and observation.value.kind is ValueKind.UNAVAILABLE
    ):
        return True
    derivation = observation.derivation
    if derivation is None:
        return False
    role = _compilation_role_flags(derivation.relation)
    return (
        role["engine_reference_input"] is True
        and role["scoring_eligible"] is False
    )
