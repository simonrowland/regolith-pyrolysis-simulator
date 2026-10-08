"""Shared classification of engine source metadata versus ancestry ids."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


OPENIMCC_PROVENANCE_PREFIXES: tuple[str, ...] = (
    "openimcc-pack-version:",
    "openimcc-pack-digest:",
    "openimcc-engine-binding:",
    "openimcc-melt-binding:",
    "openimcc-condensate-table:",
    "openimcc-gas-table:",
)

_OPENIMCC_ENGINE_IDENTITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("openimcc-engine-binding", "engine_binding_digest"),
    ("openimcc-melt-binding", "melt_binding_digest"),
    ("openimcc-condensate-table", "condensate_table_digest"),
    ("openimcc-gas-table", "gas_table_digest"),
)


def openimcc_engine_identity_sources(
    identity: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """Format engine binding digests as scorer metadata, not ancestry IDs."""

    if not isinstance(identity, Mapping):
        return ()
    return tuple(
        f"{label}:sha256:{digest}"
        for label, field in _OPENIMCC_ENGINE_IDENTITY_FIELDS
        if isinstance((digest := identity.get(field)), str) and digest
    )


def coefficient_lineage_sources(sources: Sequence[str]) -> tuple[str, ...]:
    """Return store-resolvable coefficient ancestors, excluding run metadata."""

    return tuple(
        source
        for source in sources
        if not any(source.startswith(prefix) for prefix in OPENIMCC_PROVENANCE_PREFIXES)
    )
