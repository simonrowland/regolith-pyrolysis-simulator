"""Shared rules for records kept as context or reference, not observations."""

from __future__ import annotations

from collections.abc import Mapping

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


def declares_non_scoring_compilation_reference(role: Mapping[str, object]) -> bool:
    """Require the complete positive role declaration before filtering work."""

    return (
        role.get("engine_reference_input") is True
        and role.get("validation_measurement") is False
        and role.get("scoring_eligible") is False
    )


def is_non_observation(observation: Observation, origin: str | None = None) -> bool:
    """Return whether positive source metadata marks a record as context.

    An unknown quantity or unavailable value never establishes that status by
    itself. Extract context rows are carried separately from observations; the
    migrated records that remain here are filtered only by their internal
    ledger origin or an explicit non-scoring compilation role.
    """

    if is_internal_consistency(origin):
        return True
    provenance = observation.provenance
    role = (
        provenance.get("compilation_role")
        if isinstance(provenance, Mapping)
        else None
    )
    if isinstance(role, Mapping) and declares_non_scoring_compilation_reference(
        role
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
