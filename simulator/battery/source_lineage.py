"""Shared helpers for source assets, engine metadata, and ancestry ids."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


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


def _is_unknown_source_asset(asset_id: str) -> bool:
    """The generated unknown asset is a read_from fallback, not source lineage."""

    return asset_id.startswith("unknown:")


def _registered_lineage_asset_ids(asset_ids: Iterable[str]) -> frozenset[str]:
    """Exclude the generated read_from fallback from source asset lineage."""

    return frozenset(
        asset_id for asset_id in asset_ids if not _is_unknown_source_asset(asset_id)
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


def openimcc_pack_coefficient_sources(pack_version: object) -> tuple[str, ...]:
    """Resolve coefficient ancestry from the version of the loaded pack."""

    if not isinstance(pack_version, str) or not pack_version:
        return ()
    return (f"openimcc-v{pack_version}",)


def coefficient_lineage_sources(sources: Sequence[str]) -> tuple[str, ...]:
    """Return store-resolvable coefficient ancestors, excluding run metadata."""

    return tuple(
        source
        for source in sources
        if not any(source.startswith(prefix) for prefix in OPENIMCC_PROVENANCE_PREFIXES)
    )
