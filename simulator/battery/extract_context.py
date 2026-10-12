"""Shared location rule for context containers in literature extracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

SUPPORTED_EXTRACT_CONTEXT_PATH = "species.<formula>.context"


def is_supported_context_path(path: tuple[str, ...]) -> bool:
    """Whether a context container path is lifted from an extract."""
    return len(path) == 3 and path[0] == "species" and path[2] == "context"


def iter_extract_context_containers(
    doc: Mapping[str, Any],
) -> Iterator[tuple[str, Any]]:
    """Yield formula and context rows from the supported extract location."""
    species = doc.get("species")
    if not isinstance(species, Mapping):
        return
    for formula, body in species.items():
        path = ("species", str(formula), "context")
        if (
            is_supported_context_path(path)
            and isinstance(body, Mapping)
            and "context" in body
        ):
            yield str(formula), body["context"]


def iter_context_container_paths(
    value: Any, path: tuple[str, ...] = ()
) -> Iterator[tuple[str, ...]]:
    """Yield paths to context values shaped like containers, not descriptions."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = (*path, str(key))
            if str(key) == "context" and isinstance(child, (Mapping, list)):
                yield child_path
            yield from iter_context_container_paths(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_context_container_paths(child, (*path, f"[{index}]"))


def iter_unsupported_context_container_paths(
    value: Any, path: tuple[str, ...] = ()
) -> Iterator[tuple[str, ...]]:
    """Yield context container paths that the migrator does not lift."""
    for key_path in iter_context_container_paths(value, path):
        if not is_supported_context_path(key_path):
            yield key_path
