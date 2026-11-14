"""Shared helpers for source assets, engine metadata, and ancestry ids."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache


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
    """Identify the generated fallback asset for an unmatched INDEX layer."""

    return asset_id.startswith("unknown:")


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


@lru_cache(maxsize=1)
def _default_openimcc_pack_source_refs() -> tuple[str, str, tuple[str, ...]]:
    """Read source refs declared by the packaged default melt pack once."""

    try:
        import openimcc

        pack = openimcc.load_datapack()
    except Exception:  # noqa: BLE001 - an unavailable pack remains an unknown alias
        return "", "", ()
    raw_refs = getattr(pack, "_row_source_refs", None)
    if not isinstance(raw_refs, Mapping):
        return "", "", ()
    refs = tuple(
        sorted(
            {
                ref
                for row_refs in raw_refs.values()
                if isinstance(row_refs, Sequence) and not isinstance(row_refs, str)
                for ref in row_refs
                if isinstance(ref, str) and ref
            }
        )
    )
    return (
        str(getattr(pack, "version", "")),
        str(getattr(pack, "binding_digest", "") or ""),
        refs,
    )


def openimcc_pack_coefficient_sources(
    pack_version: object, pack_digest: object = None
) -> tuple[str, ...]:
    """Resolve ancestry from the loaded pack's declared source refs."""

    if not isinstance(pack_version, str) or not pack_version:
        return ()
    default_version, default_digest, source_refs = _default_openimcc_pack_source_refs()
    if (
        default_version == pack_version
        and source_refs
        and (pack_digest is None or pack_digest == default_digest)
    ):
        return tuple(f"openimcc-source-ref:{source_ref}" for source_ref in source_refs)
    # Legacy packs do not expose row source refs. Their version alias is kept
    # explicit in the scorer's source map; an unknown alias becomes a typed
    # lineage refusal there.
    return (f"openimcc-v{pack_version}",)


def coefficient_lineage_sources(sources: Sequence[str]) -> tuple[str, ...]:
    """Return store-resolvable coefficient ancestors, excluding run metadata."""

    return tuple(
        source
        for source in sources
        if not any(source.startswith(prefix) for prefix in OPENIMCC_PROVENANCE_PREFIXES)
    )
