"""Typed failures for malformed recipe inputs crossing engine boundaries."""

from collections.abc import Iterable


class MalformedRecipeError(ValueError):
    """Recipe data is malformed and should be scored as an invalid recipe."""


def c6_oxygen_control_refusal(fields: Iterable[str]) -> str | None:
    """One refusal rule for recipe, catalog and runtime override boundaries."""
    removed = {'pO2_mbar', 'pO2_mbar_default', 'pO2_bar', 'o2_bubbler_kg_per_hr'}
    for field in sorted(set(fields) & removed):
        return (
            f'c6_oxygen_control_removed_d203: d-203 requires an oxygen-free Ar '
            f'cover; C6.{field} is no longer a commanded control')
    return None
