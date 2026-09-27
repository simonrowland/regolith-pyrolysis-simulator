"""Shared classification of engine source metadata versus ancestry ids."""

from __future__ import annotations

from typing import Sequence


OPENIMCC_PROVENANCE_PREFIXES: tuple[str, ...] = (
    "openimcc-pack-version:",
    "openimcc-pack-digest:",
    "openimcc-gas-table:",
)


def coefficient_lineage_sources(sources: Sequence[str]) -> tuple[str, ...]:
    """Return store-resolvable coefficient ancestors, excluding run metadata."""

    return tuple(
        source
        for source in sources
        if not any(source.startswith(prefix) for prefix in OPENIMCC_PROVENANCE_PREFIXES)
    )
