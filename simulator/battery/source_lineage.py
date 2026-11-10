"""Shared helpers for source assets, engine metadata, and ancestry ids."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


OPENIMCC_PROVENANCE_PREFIXES: tuple[str, ...] = (
    "openimcc-pack-version:",
    "openimcc-pack-digest:",
    "openimcc-engine-binding:",
    "openimcc-melt-binding:",
    "openimcc-condensate-table:",
    "openimcc-gas-table:",
    "openimcc-package-version:",
    "openimcc-commit:",
    "openimcc-pack-file:",
    "openimcc-feo-redox-rows:",
    "openimcc-feo-condensate-row:",
    "openimcc-feo-published-condensate-row:",
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


def openimcc_pack_coefficient_sources(pack_version: object) -> tuple[str, ...]:
    """Resolve coefficient ancestry from the version of the loaded pack."""

    if not isinstance(pack_version, str) or not pack_version:
        return ()
    return (f"openimcc-v{pack_version}",)


def openimcc_pack_identity_sources(
    identity: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """Format the selected package's package and raw default-pack identities."""

    if not isinstance(identity, Mapping):
        return ()
    sources: list[str] = []
    if isinstance(identity.get("package_version"), str):
        sources.append(f"openimcc-package-version:{identity['package_version']}")
    if isinstance(identity.get("commit"), str):
        sources.append(f"openimcc-commit:{identity['commit']}")
    packs = identity.get("packs")
    if isinstance(packs, Mapping):
        for name in ("melt", "redox", "gas", "condensate"):
            pack = packs.get(name)
            if not isinstance(pack, Mapping):
                continue
            filename, digest = pack.get("file_name"), pack.get("sha256")
            if isinstance(filename, str) and isinstance(digest, str):
                sources.append(
                    f"openimcc-pack-file:{name}:{filename}:sha256:{digest}"
                )
    guard = identity.get("feo_guard")
    if isinstance(guard, Mapping):
        redox_digest = guard.get("redox_rows_sha256")
        condensate_digest = guard.get("condensate_feo_row_sha256")
        published_condensate_digest = guard.get(
            "published_condensate_feo_row_sha256"
        )
        if isinstance(redox_digest, str):
            sources.append(f"openimcc-feo-redox-rows:sha256:{redox_digest}")
        if isinstance(condensate_digest, str):
            sources.append(
                f"openimcc-feo-condensate-row:sha256:{condensate_digest}"
            )
        if isinstance(published_condensate_digest, str):
            sources.append(
                "openimcc-feo-published-condensate-row:sha256:"
                f"{published_condensate_digest}"
            )
    return tuple(sources)


def coefficient_lineage_sources(sources: Sequence[str]) -> tuple[str, ...]:
    """Return store-resolvable coefficient ancestors, excluding run metadata."""

    return tuple(
        source
        for source in sources
        if not any(source.startswith(prefix) for prefix in OPENIMCC_PROVENANCE_PREFIXES)
    )
