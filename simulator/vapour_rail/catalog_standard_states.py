"""Selected source and K standards declared by vapor catalog routes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from simulator.vapour_rail.activity import StandardStateIdentity


class CatalogStandardStateError(ValueError):
    """A selected catalog reaction declares an invalid standard identity."""


def _catalog_vapor_standard_state_rows(
    catalog_payload: Mapping[str, Any] | None,
) -> Iterator[
    tuple[
        str,
        str,
        StandardStateIdentity | None,
        StandardStateIdentity | None,
    ]
]:
    if not isinstance(catalog_payload, Mapping):
        return
    families = catalog_payload.get("families")
    if not isinstance(families, Mapping):
        return
    for family in families.values():
        if not isinstance(family, Mapping):
            continue
        physical = family.get("physical_properties")
        if not isinstance(physical, Mapping):
            continue
        species_rows = physical.get("species")
        if not isinstance(species_rows, Mapping):
            continue
        for species_id, row in species_rows.items():
            if not isinstance(row, Mapping):
                continue
            parent = str(row.get("parent_oxide") or "").strip()
            pressure_models = row.get("pressure_models")
            reactions = row.get("source_reactions")
            if (
                not parent
                or not isinstance(pressure_models, list)
                or len(pressure_models) != 1
                or not isinstance(pressure_models[0], Mapping)
                or not isinstance(reactions, list)
            ):
                continue
            reaction_id = pressure_models[0].get("source_reaction_id")
            selected = [
                reaction
                for reaction in reactions
                if isinstance(reaction, Mapping)
                and reaction.get("id") == reaction_id
            ]
            if len(selected) != 1:
                continue
            reaction = selected[0]
            thermodynamic = reaction.get("thermodynamic_standard_state")
            activity_input = reaction.get("activity_input")
            producer = (
                activity_input.get("standard_state")
                if isinstance(activity_input, Mapping)
                else None
            )
            selected_k = (
                thermodynamic
                if isinstance(thermodynamic, Mapping)
                else producer
            )
            yield (
                parent,
                str(species_id),
                _standard_state_identity(
                    producer,
                    label=f"{reaction_id}.activity_input.standard_state",
                ),
                _standard_state_identity(
                    selected_k,
                    label=f"{reaction_id}.thermodynamic_standard_state",
                ),
            )


def catalog_vapor_species_standard_states(
    catalog_payload: Mapping[str, Any] | None,
) -> dict[
    str,
    tuple[StandardStateIdentity | None, StandardStateIdentity | None],
]:
    """Resolve source-activity and selected K identities by vapor species."""

    return {
        species_id: (source, selected_k)
        for _parent, species_id, source, selected_k
        in _catalog_vapor_standard_state_rows(catalog_payload)
    }


def catalog_vapor_parent_standard_states(
    catalog_payload: Mapping[str, Any] | None,
) -> dict[
    str,
    tuple[StandardStateIdentity | None, StandardStateIdentity | None],
]:
    """Aggregate selected route standards, clearing any conflicting side."""

    source_states_by_parent: dict[
        str, set[StandardStateIdentity | None]
    ] = {}
    k_states_by_parent: dict[str, set[StandardStateIdentity | None]] = {}
    for parent, _species_id, source, selected_k in (
        _catalog_vapor_standard_state_rows(catalog_payload)
    ):
        source_states_by_parent.setdefault(parent, set()).add(source)
        k_states_by_parent.setdefault(parent, set()).add(selected_k)

    def one_identity(
        states: set[StandardStateIdentity | None],
    ) -> StandardStateIdentity | None:
        return next(iter(states)) if len(states) == 1 else None

    return {
        parent: (
            one_identity(source_states_by_parent[parent]),
            one_identity(k_states_by_parent[parent]),
        )
        for parent in source_states_by_parent
    }


def _standard_state_identity(
    raw: Any,
    *,
    label: str,
) -> StandardStateIdentity | None:
    if not isinstance(raw, Mapping):
        return None
    try:
        return StandardStateIdentity.from_mapping(raw)
    except (TypeError, ValueError) as exc:
        raise CatalogStandardStateError(
            f"{label} is not a valid standard-state identity"
        ) from exc
