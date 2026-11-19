"""Shared helpers for source assets, engine metadata, and ancestry ids."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess

from importlib import resources


OPENIMCC_INDEX_VOCABULARY_MISMATCH_PREFIX = (
    "openimcc-index-vocabulary:mismatch;"
)
OPENIMCC_INDEX_VOCABULARY_UNVERIFIED_PREFIX = (
    "openimcc-index-vocabulary:unverified;"
)
OPENIMCC_LINEAGE_GAP_PREFIX = "openimcc-lineage-gap:"
OPENIMCC_REVIEW_GAMMA_ROW_WITHHELD_PREFIX = (
    "openimcc-review-gamma-row-withheld;"
)
OPENIMCC_REVIEW_GAMMA_SOURCE_ROW_PREFIX = "openimcc-review-gamma-source-row:"
OPENIMCC_REVIEW_GAMMA_WITHHELD_SCOPE = "withheld"
OPENIMCC_COEFFICIENT_LINEAGE_UNMATCHED_PREFIX = (
    "openimcc-coefficient-lineage-unmatched;"
)
OPENIMCC_LINEAGE_CAVEAT_PREFIXES = (
    OPENIMCC_INDEX_VOCABULARY_MISMATCH_PREFIX,
    OPENIMCC_INDEX_VOCABULARY_UNVERIFIED_PREFIX,
    OPENIMCC_LINEAGE_GAP_PREFIX,
    OPENIMCC_REVIEW_GAMMA_ROW_WITHHELD_PREFIX,
    OPENIMCC_COEFFICIENT_LINEAGE_UNMATCHED_PREFIX,
)
OPENIMCC_PROVENANCE_PREFIXES: tuple[str, ...] = (
    "openimcc-pack-version:",
    "openimcc-pack-digest:",
    "openimcc-redox-pack:",
    "openimcc-engine-binding:",
    "openimcc-melt-binding:",
    "openimcc-condensate-table:",
    "openimcc-gas-table:",
    "openimcc-index-vocabulary:",
    OPENIMCC_LINEAGE_GAP_PREFIX,
    OPENIMCC_REVIEW_GAMMA_ROW_WITHHELD_PREFIX,
    OPENIMCC_REVIEW_GAMMA_SOURCE_ROW_PREFIX,
    OPENIMCC_COEFFICIENT_LINEAGE_UNMATCHED_PREFIX,
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
        return (
            OPENIMCC_INDEX_VOCABULARY_UNVERIFIED_PREFIX
            + "reason=manifest_unavailable"
        )
    snapshot = manifest.get("vocabulary_snapshot")
    pinned_commit = snapshot.get("commit") if isinstance(snapshot, Mapping) else None
    if not isinstance(pinned_commit, str) or not pinned_commit:
        return (
            OPENIMCC_INDEX_VOCABULARY_UNVERIFIED_PREFIX
            + "reason=manifest_commit_missing"
        )

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
        return (
            OPENIMCC_INDEX_VOCABULARY_UNVERIFIED_PREFIX
            + f"reason=current_commit_unavailable;manifest={pinned_commit}"
        )
    current_commit = result.stdout.strip()
    if result.returncode != 0 or not current_commit:
        return (
            OPENIMCC_INDEX_VOCABULARY_UNVERIFIED_PREFIX
            + f"reason=current_commit_unavailable;manifest={pinned_commit}"
        )
    if current_commit.startswith(pinned_commit):
        return None
    return (
        OPENIMCC_INDEX_VOCABULARY_MISMATCH_PREFIX
        + f"manifest={pinned_commit};current={current_commit}"
    )


def openimcc_index_vocabulary_caveat_flag() -> str | None:
    """Return mismatch or failed-verification flags as caveats, not ancestry."""

    flag = _openimcc_index_vocabulary_flag()
    if flag is not None and flag.startswith(
        (
            OPENIMCC_INDEX_VOCABULARY_MISMATCH_PREFIX,
            OPENIMCC_INDEX_VOCABULARY_UNVERIFIED_PREFIX,
        )
    ):
        return flag
    return None


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


def _fragment_row_by_id(
    artifact: Mapping[str, object] | None, source_row_id: str
) -> Mapping[str, object] | None:
    rows = artifact.get("rows") if artifact is not None else None
    if not isinstance(rows, Sequence) or isinstance(rows, str):
        return None
    return next(
        (
            row
            for row in rows
            if isinstance(row, Mapping) and row.get("id") == source_row_id
        ),
        None,
    )


@lru_cache(maxsize=128)
def _single_cation_element(species_formula: str | None) -> str | None:
    if not isinstance(species_formula, str) or not species_formula:
        return None
    from simulator.accounting.exceptions import AccountingError
    from simulator.accounting.formulas import parse_formula

    try:
        elements = parse_formula(species_formula, species=species_formula).elements
    except AccountingError:
        return None
    cations = tuple(element for element in elements if element != "O")
    return cations[0] if len(cations) == 1 else None


@lru_cache(maxsize=1)
def _openimcc_trace_fragment_by_cation() -> Mapping[str, str]:
    """Map each registered one-cation trace oxide to its fragment identity."""

    try:
        from openimcc.trace_components import (
            supported_trace_oxides,
            trace_component_rule,
        )
    except (ImportError, ModuleNotFoundError):
        return {}
    fragments_by_cation: dict[str, set[str]] = {}
    for supported in supported_trace_oxides():
        element = _single_cation_element(supported.oxide)
        rule = trace_component_rule(supported.oxide)
        if element is None or rule is None:
            continue
        for field in ("review_gamma_element", "activity_fragment"):
            fragment = getattr(rule, field, None)
            if isinstance(fragment, str) and fragment:
                fragments_by_cation.setdefault(element, set()).add(
                    fragment.casefold()
                )
    return {
        element: next(iter(fragments))
        for element, fragments in fragments_by_cation.items()
        if len(fragments) == 1
    }


def _species_fragment_artifact(
    artifacts: Sequence[Mapping[str, object]], species_formula: str | None
) -> Mapping[str, object] | None:
    cation = _single_cation_element(species_formula)
    fragment = _openimcc_trace_fragment_by_cation().get(cation or "")
    if fragment is None:
        return None
    for artifact in artifacts:
        identity = artifact.get("identity")
        if not isinstance(identity, str):
            continue
        if not identity.startswith("openimcc-review-gamma-activity.v1:"):
            continue
        if identity.rsplit(":", 1)[-1].casefold() != fragment:
            continue
        return artifact
    return None


def _fragment_row_scopes(
    rows: object,
) -> dict[str, tuple[str, ...]]:
    """Collect locator scopes and mark work IDs missing from candidate rows."""

    if not isinstance(rows, Sequence) or isinstance(rows, str) or not rows:
        return {}
    row_scopes = [
        _row_source_scopes(row)
        for row in rows
        if isinstance(row, Mapping)
    ]
    if len(row_scopes) != len(rows):
        return {}
    combined: dict[str, tuple[str, ...]] = {}
    all_work_ids = set().union(*(set(scopes) for scopes in row_scopes))
    for work_id in sorted(all_work_ids):
        scopes_for_work: set[str] = set()
        missing_from_row = False
        for scopes in row_scopes:
            if work_id not in scopes:
                missing_from_row = True
                continue
            scope = scopes[work_id]
            if scope is None:
                scopes_for_work.add("unparsed")
            else:
                scopes_for_work.update(scope)
        if missing_from_row:
            scopes_for_work.add(OPENIMCC_REVIEW_GAMMA_WITHHELD_SCOPE)
        combined[work_id] = tuple(sorted(scopes_for_work))
    return combined


def _species_fragment_sources(
    artifacts: Sequence[Mapping[str, object]], species_formula: str | None
) -> tuple[str, ...]:
    artifact = _species_fragment_artifact(artifacts, species_formula)
    rows = artifact.get("rows") if artifact is not None else None
    return tuple(
        sorted(
            work_id
            for work_id, scopes in _fragment_row_scopes(rows).items()
            if OPENIMCC_REVIEW_GAMMA_WITHHELD_SCOPE not in scopes
        )
    )


def _withheld_fragment_flag(
    artifact: Mapping[str, object], rows: object
) -> str | None:
    if not isinstance(rows, Sequence) or isinstance(rows, str) or len(rows) <= 1:
        return None
    identity = str(artifact.get("identity") or "unknown")
    fragment = identity.rsplit(":", 1)[-1]
    candidate_rows = sorted(
        str(row.get("id") or "unknown")
        for row in rows
        if isinstance(row, Mapping)
    )
    withheld_work_ids = sorted(
        work_id
        for work_id, scopes in _fragment_row_scopes(rows).items()
        if OPENIMCC_REVIEW_GAMMA_WITHHELD_SCOPE in scopes
    )
    return (
        f"{OPENIMCC_REVIEW_GAMMA_ROW_WITHHELD_PREFIX}"
        f"fragment={fragment};candidate_rows={','.join(candidate_rows)};"
        f"withheld_work_ids={','.join(withheld_work_ids)}"
    )


def _table_ids(text: str) -> set[str]:
    table_number = r"[a-z]?-?\d+(?:\.\d+)?[a-z]?"
    separator = r"(?:,|/|&|\band\b)"
    table_lists = re.findall(
        rf"\btables?\s+({table_number}(?:\s*{separator}\s*{table_number})*)",
        text,
        re.I,
    )
    if table_lists:
        return {
            match.casefold()
            for table_list in table_lists
            for match in re.findall(table_number, table_list, re.I)
        }
    if re.fullmatch(
        rf"\s*{table_number}(?:\s*{separator}\s*{table_number})*\s*",
        text,
        re.I,
    ):
        return {
            match.casefold()
            for match in re.findall(table_number, text, re.I)
        }
    return set()


def _page_ranges(
    text: str, *, citation_locator: bool = False
) -> set[tuple[int, int]]:
    page_pattern = (
        r"\bpp?\.?\s*(\d+)(?:\s*[\u2013\u2014-]\s*(\d+))?\b"
        if citation_locator
        else r"(?<!\d)(\d+)(?:\s*[\u2013\u2014-]\s*(\d+))?(?!\d)"
    )
    return {
        (int(first), int(last or first))
        for first, last in re.findall(page_pattern, text, re.I)
    }


def _summary_work_id(
    citation: str,
    source_work_ids: Sequence[str],
) -> str | None:
    """Match a summarized-in citation to a source work in the same row."""

    _head, marker, locator = citation.partition("locator:")
    if not marker:
        return None
    match = re.search(
        r"\bsummari[sz]ed\s+in\s+(.+?)(?=,\s*(?:tables?|pp?\.?)(?:\s|$)|$)",
        locator,
        re.I,
    )
    if match is None:
        return None
    cited_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z]+|\d{4}", match.group(1))
        if token.casefold() not in {"et", "al"}
    }
    if not cited_tokens:
        return None
    candidates = [
        work_id
        for work_id in source_work_ids
        if cited_tokens.issubset(
            {token.casefold() for token in re.findall(r"[A-Za-z]+|\d{4}", work_id)}
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


def _locator_scope_from_citation(
    citation: object,
    *,
    row_id: object = None,
    source_work_id: str | None = None,
    summary_work_id: str | None = None,
) -> tuple[str, ...] | None:
    """Parse locator tokens, treating citations without them as work-wide."""

    if not isinstance(citation, str):
        return None
    _head, marker, locator = citation.partition("locator:")
    if not marker:
        locator = citation
    elif re.search(r"\bsummari[sz]ed\s+in\b", locator, re.I):
        if summary_work_id is None:
            return ("unparsed",)
        if source_work_id != summary_work_id:
            return ("work",)
    locator_segments = (locator,)
    row_table = (
        re.search(
            r"(?:^|[-_\s])table[-_\s]?([a-z]?-?\d+(?:\.\d+)?[a-z]?)",
            row_id,
            re.I,
        )
        if isinstance(row_id, str)
        else None
    )
    if row_table is not None and re.search(
        r"\bcomposition\s+law\b", locator, re.I
    ):
        # Keep the row's coefficient table and the table supplying its fitted
        # composition law; closure inputs and standard-state tables are context.
        coefficient_table = row_table.group(1).casefold()
        relevant_segments = tuple(
            segment
            for segment in locator.split(";")
            if coefficient_table in _table_ids(segment)
            or re.search(r"\bcomposition\s+law\b", segment, re.I)
        )
        if relevant_segments:
            locator_segments = relevant_segments
    scopes: set[str] = set()
    for segment in locator_segments:
        scopes.update(f"table:{table}" for table in _table_ids(segment))
        scopes.update(
            f"page:{first}-{last}"
            for first, last in _page_ranges(segment, citation_locator=True)
        )
    if scopes:
        return tuple(sorted(scopes))
    if re.search(r"\b(?:tables?|pages?)\b|\bpp?\.", locator, re.I):
        return ("unparsed",)
    return ("work",)


def _row_source_scopes(row: Mapping[str, object]) -> dict[str, tuple[str, ...] | None]:
    scopes: dict[str, tuple[str, ...] | None] = {}
    sources = row.get("sources", ())
    if not isinstance(sources, Sequence) or isinstance(sources, str):
        return scopes
    source_entries = tuple(source for source in sources if isinstance(source, Mapping))
    source_work_ids = tuple(
        sorted(
            {
                work_id
                for source in source_entries
                if isinstance((work_id := source.get("work_id")), str) and work_id
            }
        )
    )
    for source in source_entries:
        work_id = source.get("work_id")
        if not isinstance(work_id, str) or not work_id:
            continue
        citation = source.get("citation")
        summary_work_id = (
            _summary_work_id(citation, source_work_ids)
            if isinstance(citation, str)
            else None
        )
        scope = _locator_scope_from_citation(
            citation,
            row_id=row.get("id"),
            source_work_id=work_id,
            summary_work_id=summary_work_id,
        )
        if work_id not in scopes:
            scopes[work_id] = scope
        elif scopes[work_id] is None or scope is None:
            scopes[work_id] = ("unparsed",)
        else:
            scopes[work_id] = tuple(sorted(set(scopes[work_id]) | set(scope)))
    return scopes


@lru_cache(maxsize=32)
def openimcc_species_source_locator_scopes(
    species_formula: str | None,
    source_row_id: str | None = None,
) -> Mapping[str, tuple[str, ...]]:
    """Return selected-row scopes or candidate scopes with withheld work IDs."""

    artifacts = _openimcc_coefficient_ancestry()
    artifact = _species_fragment_artifact(artifacts, species_formula)
    if source_row_id is not None:
        row = _fragment_row_by_id(artifact, source_row_id)
        if row is None:
            return {}
        return {
            work_id: scopes if scopes is not None else ("unparsed",)
            for work_id, scopes in _row_source_scopes(row).items()
        }
    rows = artifact.get("rows") if artifact is not None else None
    return {
        work_id: scopes
        for work_id, scopes in _fragment_row_scopes(rows).items()
    }


def openimcc_source_locator_matches(
    scopes: Sequence[str], locator: object | None
) -> bool | None:
    """Match a manifest citation scope to a record locator.

    ``None`` means the manifest scope or observation locator is insufficient
    to decide. A known different table/page is an independent source row.
    """

    if "unparsed" in scopes:
        return None
    if "work" in scopes:
        return True
    if locator is None:
        return None
    scopes = tuple(
        scope
        for scope in scopes
        if scope != OPENIMCC_REVIEW_GAMMA_WITHHELD_SCOPE
    )
    locator_table_text = str(getattr(locator, "table", "") or "")
    locator_tables = _table_ids(locator_table_text)
    scope_tables = {
        scope.partition(":")[2]
        for scope in scopes
        if scope.startswith("table:")
    }
    scope_pages = {
        tuple(int(value) for value in scope.partition(":")[2].split("-", 1))
        for scope in scopes
        if scope.startswith("page:")
    }
    if locator_table_text.strip():
        if not locator_tables:
            return None
        return bool(locator_tables & scope_tables) if scope_tables else None
    locator_page_ranges = {
        page_range
        for value in (
            getattr(locator, "page", None),
            getattr(locator, "published_page", None),
        )
        if value is not None
        for page_range in _page_ranges(str(value))
    }
    if locator_page_ranges and scope_pages:
        return any(
            max(scope_first, locator_first) <= min(scope_last, locator_last)
            for scope_first, scope_last in scope_pages
            for locator_first, locator_last in locator_page_ranges
        )
    return None


def openimcc_pack_coefficient_sources(
    pack_version: object | None,
    pack_digest: object = None,
    *,
    species_formula: str | None = None,
    source_row_id: str | None = None,
) -> tuple[str, ...]:
    """Resolve manifest work IDs, retaining a version alias only for legacy packs.

    The active OpenIMCC evaluator does not expose the D066 interaction rows
    used by a major-oxide output, so that artifact's work-ID union is the
    pack-level fallback for its major oxides. Trace fragments use a selected
    row when supplied and use the work-ID intersection only as a fallback.
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
    selected_fragment = _species_fragment_artifact(artifacts, species_formula)
    fragment = selected_fragment
    fragment_matches = fragment is not None and (
        not is_fragment or fragment.get("identity") == identity
    )
    fragment_rows = fragment.get("rows") if fragment is not None else None
    fragment_sources = (
        set(_species_fragment_sources(artifacts, species_formula))
        if fragment_matches
        else set()
    )
    is_major_oxide = False
    if is_d066 and species_formula:
        try:
            from openimcc.gas_data import IMCC_PARENT_OXIDES as parent_oxides
        except (ImportError, ModuleNotFoundError):
            parent_oxides = ()
        is_major_oxide = species_formula in parent_oxides

    flags: set[str] = set()
    if source_row_id is not None:
        row = _fragment_row_by_id(fragment, source_row_id)
        sources = set(_work_ids_from_rows((row,))) if row is not None else set()
        if not sources:
            flags.add(
                f"{OPENIMCC_COEFFICIENT_LINEAGE_UNMATCHED_PREFIX}"
                f"formula={species_formula or 'missing'};row={source_row_id}"
            )
        sources.add(f"{OPENIMCC_REVIEW_GAMMA_SOURCE_ROW_PREFIX}{source_row_id}")
        return tuple(sorted(sources | flags))
    if fragment_matches and fragment is not None:
        withheld_flag = _withheld_fragment_flag(fragment, fragment_rows)
        if withheld_flag is not None:
            flags.add(withheld_flag)
    if is_fragment and not fragment_matches:
        flags.add(
            f"{OPENIMCC_COEFFICIENT_LINEAGE_UNMATCHED_PREFIX}"
            f"formula={species_formula or 'missing'}"
        )

    if is_d066 and is_major_oxide:
        sources = {
            item
            for item in matching.get("work_ids", ())
            if isinstance(item, str) and item
        }
        if not sources:
            sources.update(_work_ids_from_rows(matching.get("rows")))
    elif is_d066:
        sources = fragment_sources
        if not sources and not flags:
            flags.add(
                f"{OPENIMCC_COEFFICIENT_LINEAGE_UNMATCHED_PREFIX}"
                f"formula={species_formula or 'missing'}"
            )
    elif is_fragment:
        sources = fragment_sources if fragment is not None else set()
    else:
        sources = set(_work_ids_from_rows(matching.get("rows")))
    if not is_d066 and not is_fragment and not sources:
        sources.update(
            item
            for item in matching.get("work_ids", ())
            if isinstance(item, str) and item
        )
    if not is_d066 and not is_fragment:
        sources.update(fragment_sources)
        if species_formula and not sources and not fragment_matches:
            flags.add(
                f"{OPENIMCC_COEFFICIENT_LINEAGE_UNMATCHED_PREFIX}"
                f"formula={species_formula}"
            )
    sources.update(flags)
    return tuple(sorted(sources))


def openimcc_sources_with_activity_row_identity(
    coefficient_sources: Sequence[str],
    pack_version: object | None,
    pack_digest: object,
    *,
    species_formula: str | None,
    activity_coefficient_details: object,
) -> tuple[str, ...]:
    """Replace fallback fragment lineage with the evaluated row's works."""

    if not isinstance(activity_coefficient_details, Mapping):
        return tuple(coefficient_sources)
    element = _single_cation_element(species_formula)
    if element is None:
        return tuple(coefficient_sources)
    row_ids = {
        str(detail["source_row_id"])
        for component, detail in activity_coefficient_details.items()
        if isinstance(detail, Mapping)
        and isinstance(detail.get("source_row_id"), str)
        and detail.get("source_row_id")
        and _single_cation_element(str(component)) == element
    }
    if not row_ids:
        return tuple(coefficient_sources)

    base_sources = openimcc_pack_coefficient_sources(
        pack_version,
        pack_digest,
        species_formula=species_formula,
    )
    preserved_sources = tuple(
        source for source in coefficient_sources if source not in base_sources
    )
    if len(row_ids) == 1:
        row_sources = openimcc_pack_coefficient_sources(
            pack_version,
            pack_digest,
            species_formula=species_formula,
            source_row_id=next(iter(row_ids)),
        )
    else:
        row_sources = (
            f"{OPENIMCC_COEFFICIENT_LINEAGE_UNMATCHED_PREFIX}"
            f"formula={species_formula};ambiguous_source_rows="
            f"{','.join(sorted(row_ids))}",
            *(
                f"{OPENIMCC_REVIEW_GAMMA_SOURCE_ROW_PREFIX}{row_id}"
                for row_id in sorted(row_ids)
            ),
        )
    return tuple(dict.fromkeys((*preserved_sources, *row_sources)))


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


def openimcc_missing_index_work_ids(
    sources: Sequence[str],
    indexed_works: Mapping[str, object],
) -> tuple[str, ...]:
    """Find manifest work sources absent from the INDEX used for scoring."""

    candidate_sources = set(coefficient_lineage_sources(sources))
    if not candidate_sources:
        return ()
    manifest_work_ids: set[str] = set()
    for artifact in _openimcc_coefficient_ancestry():
        work_ids = artifact.get("work_ids")
        if isinstance(work_ids, Sequence) and not isinstance(work_ids, str):
            manifest_work_ids.update(
                work_id for work_id in work_ids
                if isinstance(work_id, str) and work_id
            )
        manifest_work_ids.update(_work_ids_from_rows(artifact.get("rows")))
    return tuple(
        sorted(
            work_id
            for work_id in candidate_sources & manifest_work_ids
            if work_id not in indexed_works
        )
    )
