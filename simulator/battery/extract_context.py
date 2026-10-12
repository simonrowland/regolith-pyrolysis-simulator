"""Shared location rule for context containers in literature extracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

SUPPORTED_EXTRACT_CONTEXT_PATH = "species.<formula>.context"


def iter_extract_context_containers(
    doc: Mapping[str, Any],
) -> Iterator[tuple[str, Any]]:
    """Yield formula and context rows from the supported extract location."""
    species = doc.get("species")
    if not isinstance(species, Mapping):
        return
    for formula, body in species.items():
        if isinstance(body, Mapping) and "context" in body:
            yield str(formula), body["context"]


def iter_context_key_paths(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    """Yield every mapping path whose key is named ``context``."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = (*path, str(key))
            if str(key) == "context":
                yield child_path
            yield from iter_context_key_paths(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_context_key_paths(child, (*path, f"[{index}]"))
