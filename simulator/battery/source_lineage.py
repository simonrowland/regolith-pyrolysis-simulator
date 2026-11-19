"""Shared helpers for source assets, engine metadata, and ancestry ids."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
import json
from pathlib import Path
import re
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

OPENIMCC_INDEX_VOCABULARY_MISMATCH_PREFIX = (
    "openimcc-index-vocabulary:mismatch;"
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


@lru_cache(maxsize=32)
def _openimcc_trace_component_rule(species_formula: str | None) -> object | None:
    if not isinstance(species_formula, str) or not species_formula:
        return None
    try:
        from openimcc.trace_components import trace_component_rule
    except (ImportError, ModuleNotFoundError):
        return None
    return trace_component_rule(species_formula)


def _species_fragment_artifact(
    artifacts: Sequence[Mapping[str, object]], species_formula: str | None
) -> Mapping[str, object] | None:
    rule = _openimcc_trace_component_rule(species_formula)
    element = getattr(rule, "review_gamma_element", None)
    if not isinstance(element, str) or not element:
        return None
    wanted = element.casefold()
    for artifact in artifacts:
        identity = artifact.get("identity")
        if not isinstance(identity, str):
            continue
        if not identity.startswith("openimcc-review-gamma-activity.v1:"):
            continue
        if identity.rsplit(":", 1)[-1].casefold() != wanted:
            continue
        return artifact
    return None


def _species_fragment_sources(
    artifacts: Sequence[Mapping[str, object]], species_formula: str | None
) -> tuple[str, ...]:
    artifact = _species_fragment_artifact(artifacts, species_formula)
    rows = artifact.get("rows") if artifact is not None else None
    if not isinstance(rows, Sequence) or isinstance(rows, str) or len(rows) != 1:
        # The pinned evaluator does not expose the selected review-gamma row.
        # Only one-row fragments are unambiguous until that provenance exists.
        return ()
    return _work_ids_from_rows(rows)


def _locator_scope_from_citation(citation: object) -> tuple[str, ...] | None:
    if not isinstance(citation, str):
        return None
    _head, marker, locator = citation.partition("locator:")
    if not marker:
        return None
    scopes = {
        f"table:{match.casefold()}"
        for match in re.findall(r"\btable\s+([0-9]+[a-z]?)\b", locator, re.I)
    }
    for first, last in re.findall(
        r"\bpp?\.?\s*([0-9]+)(?:\s*[\u2013\u2014-]\s*([0-9]+))?\b",
        locator,
        re.I,
    ):
        scopes.add(f"page:{first}")
        if last:
            scopes.add(f"page:{last}")
    return tuple(sorted(scopes)) if scopes else ("unparsed",)


def _row_source_scopes(row: Mapping[str, object]) -> dict[str, tuple[str, ...] | None]:
    scopes: dict[str, tuple[str, ...] | None] = {}
    sources = row.get("sources", ())
    if not isinstance(sources, Sequence) or isinstance(sources, str):
        return scopes
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        work_id = source.get("work_id")
        if not isinstance(work_id, str) or not work_id:
            continue
        scope = _locator_scope_from_citation(source.get("citation"))
        if work_id not in scopes:
            scopes[work_id] = scope
        elif scopes[work_id] is None or scope is None:
            scopes[work_id] = None
        else:
            scopes[work_id] = tuple(sorted(set(scopes[work_id]) | set(scope)))
    return scopes


@lru_cache(maxsize=32)
def openimcc_species_source_locator_scopes(
    species_formula: str | None,
) -> Mapping[str, tuple[str, ...]]:
    """Return locator scopes for a one-row review-gamma fragment.

    Multi-row fragments have no selected-row result at the current OpenIMCC
    pin, so they deliberately contribute no source scopes.
    """

    artifacts = _openimcc_coefficient_ancestry()
    artifact = _species_fragment_artifact(artifacts, species_formula)
    rows = artifact.get("rows") if artifact is not None else None
    if not isinstance(rows, Sequence) or isinstance(rows, str) or len(rows) != 1:
        return {}
    return {
        work_id: scopes
        for work_id, scopes in _row_source_scopes(rows[0]).items()
        if scopes is not None
    }


def openimcc_source_locator_matches(
    scopes: Sequence[str], locator: object | None
) -> bool | None:
    """Match a manifest citation scope to a record locator.

    ``None`` means the manifest scope or observation locator is insufficient
    to decide. A known different table/page is an independent source row.
    """

    if "unparsed" in scopes or locator is None:
        return None
    locator_tables = {
        match.casefold()
        for match in re.findall(
            r"[0-9]+[a-z]?", str(getattr(locator, "table", "") or ""), re.I
        )
    }
    locator_pages = {
        match
        for value in (
            getattr(locator, "page", None),
            getattr(locator, "published_page", None),
        )
        if value is not None
        for match in re.findall(r"[0-9]+", str(value))
    }
    scope_tables = {
        scope.partition(":")[2]
        for scope in scopes
        if scope.startswith("table:")
    }
    scope_pages = {
        scope.partition(":")[2]
        for scope in scopes
        if scope.startswith("page:")
    }
    comparable = False
    if locator_tables and scope_tables:
        comparable = True
        if locator_tables & scope_tables:
            return True
    if locator_pages and scope_pages:
        comparable = True
        if locator_pages & scope_pages:
            return True
    if comparable:
        return False
    return None


def openimcc_pack_coefficient_sources(
    pack_version: object | None,
    pack_digest: object = None,
    *,
    species_formula: str | None = None,
) -> tuple[str, ...]:
    """Resolve manifest work IDs, retaining a version alias only for legacy packs.

    The active OpenIMCC evaluator does not expose the D066 interaction rows
    used by a major-oxide output, so that artifact's work-ID union is the
    pack-level fallback. Trace activities exclude D066 ancestry. A
    review-gamma fragment is usable only when it has one manifest row.
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

    identity = matching.get("identity")
    is_d066 = isinstance(identity, str) and identity.startswith("IMCC-SF04-D066@")
    is_fragment = (
        isinstance(identity, str)
        and identity.startswith("openimcc-review-gamma-activity.v1:")
    )
    trace_rule = _openimcc_trace_component_rule(species_formula)
    is_trace_species = trace_rule is not None
    if is_d066 and (not species_formula or is_trace_species):
        sources: set[str] = set()
    elif is_fragment:
        sources = set(_species_fragment_sources(artifacts, species_formula))
    else:
        sources = set(_work_ids_from_rows(matching.get("rows")))
    if is_d066 and species_formula and not is_trace_species and not sources:
        sources.update(
            item
            for item in matching.get("work_ids", ())
            if isinstance(item, str) and item
        )
    elif not is_d066 and not is_fragment and not sources:
        sources.update(
            item
            for item in matching.get("work_ids", ())
            if isinstance(item, str) and item
        )
    if not is_fragment:
        sources.update(_species_fragment_sources(artifacts, species_formula))
    flag = _openimcc_index_vocabulary_flag()
    if flag is not None:
        sources.add(flag)
    return tuple(sorted(sources))


def openimcc_redox_coefficient_sources(
    redox_pack_digest: object,
    melt_pack_binding_digest: object,
    *,
    species_formula: str | None,
) -> tuple[str, ...]:
    """Resolve OpenIMCC redox and melt-pack ancestry with digest metadata."""

    sources: list[str] = []
    if isinstance(redox_pack_digest, str) and redox_pack_digest:
        sources.extend(
            openimcc_pack_coefficient_sources(
                None, redox_pack_digest, species_formula=species_formula
            )
        )
        sources.append(f"openimcc-redox-pack:sha256:{redox_pack_digest}")
    if isinstance(melt_pack_binding_digest, str) and melt_pack_binding_digest:
        sources.append(
            f"openimcc-melt-binding:sha256:{melt_pack_binding_digest}"
        )
        sources.extend(
            openimcc_pack_coefficient_sources(
                None, melt_pack_binding_digest, species_formula=species_formula
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
