"""Shared helpers for source assets, engine metadata, and ancestry ids."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
import json
from pathlib import Path
import subprocess

from importlib import resources


OPENIMCC_PROVENANCE_PREFIXES: tuple[str, ...] = (
    "openimcc-pack-version:",
    "openimcc-pack-digest:",
    "openimcc-redox-pack:",
    "openimcc-engine-binding:",
    "openimcc-melt-binding:",
    "openimcc-condensate-table:",
    "openimcc-gas-table:",
    "openimcc-index-vocabulary:",
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
def _openimcc_coefficient_ancestry() -> tuple[Mapping[str, object], ...]:
    """Load the installed pin's coefficient-ancestry artifacts once."""

    try:
        import openimcc
    except ImportError:
        return ()
    reader = getattr(openimcc, "coefficient_ancestry", None)
    if not callable(reader):
        return ()
    artifacts = reader()
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, str):
        return ()
    return tuple(item for item in artifacts if isinstance(item, Mapping))


@lru_cache(maxsize=1)
def _openimcc_index_vocabulary_flag() -> str | None:
    """Flag when the manifest's INDEX snapshot differs from this checkout."""

    try:
        manifest = json.loads(
            resources.files("openimcc")
            .joinpath("data", "coefficient-ancestry.json")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ImportError, ModuleNotFoundError, OSError, ValueError):
        return "openimcc-index-vocabulary:manifest-unavailable"
    snapshot = manifest.get("vocabulary_snapshot")
    pinned_commit = snapshot.get("commit") if isinstance(snapshot, Mapping) else None
    if not isinstance(pinned_commit, str) or not pinned_commit:
        return "openimcc-index-vocabulary:manifest-commit-missing"

    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", "data/literature/INDEX.yaml"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return f"openimcc-index-vocabulary:current-commit-unavailable;manifest={pinned_commit}"
    current_commit = result.stdout.strip()
    if result.returncode != 0 or not current_commit:
        return f"openimcc-index-vocabulary:current-commit-unavailable;manifest={pinned_commit}"
    if current_commit.startswith(pinned_commit):
        return None
    return (
        "openimcc-index-vocabulary:mismatch;"
        f"manifest={pinned_commit};current={current_commit}"
    )


def _work_ids_from_rows(rows: object) -> tuple[str, ...]:
    if not isinstance(rows, Sequence) or isinstance(rows, str):
        return ()
    return tuple(sorted({
        work_id
        for row in rows
        if isinstance(row, Mapping)
        for source in row.get("sources", ())
        if isinstance(source, Mapping)
        if isinstance((work_id := source.get("work_id")), str) and work_id
    }))


def _species_fragment_sources(
    artifacts: Sequence[Mapping[str, object]], species_symbol: str | None
) -> tuple[str, ...]:
    if not isinstance(species_symbol, str) or not species_symbol:
        return ()
    wanted = species_symbol.casefold()
    sources: set[str] = set()
    for artifact in artifacts:
        identity = artifact.get("identity")
        if not isinstance(identity, str):
            continue
        if not identity.startswith("openimcc") or ".v1:" not in identity:
            continue
        if identity.rsplit(":", 1)[-1].casefold() != wanted:
            continue
        sources.update(_work_ids_from_rows(artifact.get("rows")))
    return tuple(sorted(sources))


def openimcc_pack_coefficient_sources(
    pack_version: object | None,
    pack_digest: object = None,
    *,
    species_symbol: str | None = None,
) -> tuple[str, ...]:
    """Resolve manifest work IDs, retaining a version alias only for legacy packs.

    The active OpenIMCC evaluator does not expose the D066 interaction rows
    used by an output species, so that artifact's work-ID union is the
    pack-level fallback. Species-specific fragments remain row-scoped.
    """

    version = pack_version if isinstance(pack_version, str) and pack_version else None
    if version is None and not isinstance(pack_digest, str):
        return ()
    artifacts = _openimcc_coefficient_ancestry()
    matching: Mapping[str, object] | None = None
    digest_match = False
    manifest_versions: set[str] = set()
    for artifact in artifacts:
        identity = artifact.get("identity")
        if not isinstance(identity, str):
            continue
        artifact_version = identity.rsplit("@", 1)[-1]
        manifest_versions.add(artifact_version)
        artifact_digest = artifact.get("runtime_digest") or artifact.get("digest")
        if pack_digest is not None and artifact_digest == pack_digest:
            digest_match = True
        if version is not None and artifact_version != version:
            continue
        if pack_digest is not None and artifact_digest != pack_digest:
            continue
        matching = artifact
        break
    if matching is None:
        # Keep the legacy alias only for versions absent from the installed
        # manifest. A known version with a mismatched digest is unresolved.
        if (
            version is None
            or version in manifest_versions
            or digest_match
        ):
            return ()
        return (f"openimcc-v{version}",)

    sources = set(_work_ids_from_rows(matching.get("rows")))
    sources.update(_species_fragment_sources(artifacts, species_symbol))
    if not sources:
        sources.update(
            item
            for item in matching.get("work_ids", ())
            if isinstance(item, str) and item
        )
    flag = _openimcc_index_vocabulary_flag()
    if flag is not None:
        sources.add(flag)
    return tuple(sorted(sources))


def openimcc_redox_coefficient_sources(
    redox_pack_digest: object,
    melt_pack_binding_digest: object,
    *,
    species_symbol: str | None,
) -> tuple[str, ...]:
    """Resolve OpenIMCC redox and melt-pack ancestry with digest metadata."""

    sources: list[str] = []
    if isinstance(redox_pack_digest, str) and redox_pack_digest:
        sources.extend(
            openimcc_pack_coefficient_sources(
                None, redox_pack_digest, species_symbol=species_symbol
            )
        )
        sources.append(f"openimcc-redox-pack:sha256:{redox_pack_digest}")
    if isinstance(melt_pack_binding_digest, str) and melt_pack_binding_digest:
        sources.append(
            f"openimcc-melt-binding:sha256:{melt_pack_binding_digest}"
        )
        sources.extend(
            openimcc_pack_coefficient_sources(
                None, melt_pack_binding_digest, species_symbol=species_symbol
            )
        )
    return tuple(dict.fromkeys(sources))


def coefficient_lineage_sources(sources: Sequence[str]) -> tuple[str, ...]:
    """Return store-resolvable coefficient ancestors, excluding run metadata."""

    return tuple(
        source
        for source in sources
        if not any(source.startswith(prefix) for prefix in OPENIMCC_PROVENANCE_PREFIXES)
    )
