#!/usr/bin/env python3
"""Measure attribution gaps by isolated extract migration for b-1068."""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from simulator.battery.migrate import REPO_ROOT, discover_extracts, iter_extract_observations
from tests.battery.factories import migrate_one_extract


BASE = "f69c0da13"
NOTES = Path("/private/tmp/claude-501/main-review/b1068-attrib-sweep")
TARGET_CLASSES = {"quoted_attributed", "compilation_assessed"}
EXPLICIT_CITATION_KEYS = {
    "primary_sources_as_printed",
    "reference_as_printed",
    "references_as_printed",
    "reference_as_published",
    "references_as_published",
    "citation",
    "citation_as_printed",
    "citation_as_published",
    "source_citation",
    "attribution",
    "source_attribution",
    "quoted_from",
    "quote",
}
CITATION_SIGNAL = re.compile(
    r"\b[A-Z][A-Za-zÀ-ÿ'-]+(?:\s+[A-Z]\.?)?(?:\s+et\s+al\.?)?\s*,?\s*\((?:18|19|20)\d{2}[a-z]?\)|\b[A-Z][A-Za-zÀ-ÿ'-]+\s+(?:18|19|20)\d{2}\b|\bref(?:erence)?\s*[\[(#:]?\s*\d+|\[\d+\]|\bet\s+al\.?\b|\bcited\s+(?:by|as)\b",
    re.IGNORECASE,
)


def strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def citation_candidates(observation: Mapping[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def visit(value: Any, prefix: str = "") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key)
                field = f"{prefix}.{key_text}" if prefix else key_text
                normalized = key_text.casefold().replace("-", "_").replace(" ", "_")
                if normalized in EXPLICIT_CITATION_KEYS:
                    for text in strings(child):
                        if text.strip():
                            found.append((field, text.strip()))
                elif normalized in {"note", "notes", "derived_from", "reference", "references"}:
                    for text in strings(child):
                        if text.strip() and CITATION_SIGNAL.search(text):
                            found.append((field, text.strip()))
                visit(child, field)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{prefix}[{index}]")

    visit(observation)
    # Nested objects can be seen twice when an explicit field contains a mapping.
    return list(dict.fromkeys(found))


def raw_rows(doc: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["observation_id"]): row
        for _formula, row in iter_extract_observations(doc)
        if row.get("observation_id")
    }


def raw_id_for(migrated_id: str, source_id: str, ids: list[str]) -> str | None:
    prefix = f"{source_id}::"
    if not migrated_id.startswith(prefix):
        return None
    remainder = migrated_id[len(prefix) :]
    for raw_id in sorted(ids, key=len, reverse=True):
        if remainder == raw_id or remainder.startswith(raw_id + "::"):
            return raw_id
    return None


def inspect_extract(
    path: Path,
    *,
    scope: str,
    displayed_path: str,
) -> list[dict[str, str]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, Mapping):
        return []
    source_id = str(doc.get("source_id") or path.stem)
    raw = raw_rows(doc)
    ids = list(raw)
    migrator = migrate_one_extract(REPO_ROOT, path, index=None, aliases=None)
    records: list[dict[str, str]] = []
    for migrated in migrator.result.observations.values():
        if migrated.source_id != source_id:
            continue
        raw_id = raw_id_for(migrated.observation_id, source_id, ids)
        if raw_id is None:
            continue
        original = raw[raw_id]
        values = original.get("values")
        method = str(values.get("method_class") or "").strip().casefold() if isinstance(values, Mapping) else ""
        if method not in TARGET_CLASSES or migrated.evidence.attribution is not None:
            continue
        candidates = citation_candidates(original)
        evidence_class = migrated.evidence.class_.value
        records.append(
            {
                "scope": scope,
                "path": displayed_path,
                "source_id": source_id,
                "raw_observation_id": raw_id,
                "migrated_observation_id": migrated.observation_id,
                "method_class": method,
                "evidence_class": str(evidence_class.value if hasattr(evidence_class, "value") else evidence_class),
                "citation_locations": " | ".join(field for field, _ in candidates) or "NONE",
                "citation_texts": " | ".join(f"{field}={text}" for field, text in candidates) or "NONE",
            }
        )
    return records


def changed_extract_paths(ref: str, branch_base: str) -> list[str]:
    output = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            branch_base,
            ref,
            "--",
            "data/literature/extracts",
        ],
        cwd=REPO_ROOT,
        text=True,
    )
    prefix = "data/literature/extracts/"
    paths = []
    for line in output.splitlines():
        path = line.strip()
        relative = path.removeprefix(prefix)
        if (
            path.startswith(prefix)
            and "/" not in relative
            and relative.endswith(".yaml")
            and not relative.startswith("_")
            and not relative.upper().startswith("SCHEMA")
        ):
            paths.append(path)
    return paths


def run_base() -> tuple[list[dict[str, str]], list[tuple[str, str, str]]]:
    paths = discover_extracts(REPO_ROOT / "data" / "literature" / "extracts")
    records: list[dict[str, str]] = []
    sources: list[tuple[str, str, str]] = []
    for index, path in enumerate(paths, 1):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        source_id = str(doc.get("source_id") or path.stem) if isinstance(doc, Mapping) else path.stem
        display_path = path.relative_to(REPO_ROOT).as_posix()
        sources.append(("GREEN", display_path, source_id))
        records.extend(inspect_extract(path, scope="GREEN", displayed_path=display_path))
        if index % 50 == 0:
            print(f"migrated {index}/{len(paths)} green extracts", flush=True)
    return records, sources


def run_branches() -> tuple[list[dict[str, str]], list[tuple[str, str, str]]]:
    refs = subprocess.check_output(
        ["git", "for-each-ref", "--format=%(refname)", "refs/remotes/origin/review/x-*"],
        cwd=REPO_ROOT,
        text=True,
    ).splitlines()
    records: list[dict[str, str]] = []
    sources: list[tuple[str, str, str]] = []
    changed: list[tuple[str, str]] = []
    for index, ref in enumerate(refs, 1):
        branch_base = subprocess.check_output(
            ["git", "merge-base", BASE, ref], cwd=REPO_ROOT, text=True
        ).strip()
        changed.extend(
            (ref, path) for path in changed_extract_paths(ref, branch_base)
        )
        if index % 10 == 0:
            print(f"compared {index}/{len(refs)} review refs", flush=True)
    print(f"isolating {len(changed)} extract revisions from {len(refs)} review refs", flush=True)
    temp_root = Path(tempfile.mkdtemp(prefix="b1068-branch-extracts-", dir="/private/tmp/claude-501/main-review/b1068-attrib-sweep/tmp"))
    try:
        for index, (ref, extract_path) in enumerate(changed, 1):
            target = temp_root / f"extract-{index}.yaml"
            payload = subprocess.check_output(
                ["git", "show", f"{ref}:{extract_path}"], cwd=REPO_ROOT
            )
            target.write_bytes(payload)
            doc = yaml.safe_load(payload)
            source_id = str(doc.get("source_id") or target.stem) if isinstance(doc, Mapping) else target.stem
            scope = f"PENDING-BRANCH {ref}"
            display_path = f"{ref}:{extract_path}"
            sources.append((scope, display_path, source_id))
            records.extend(
                inspect_extract(
                    target,
                    scope=scope,
                    displayed_path=display_path,
                )
            )
            if index % 20 == 0:
                print(f"migrated {index}/{len(changed)} branch extract revisions", flush=True)
    finally:
        for target in temp_root.iterdir():
            target.unlink()
        temp_root.rmdir()
    return records, sources


def write_outputs(
    name: str,
    records: list[dict[str, str]],
    sources: list[tuple[str, str, str]],
) -> None:
    detail_path = NOTES / f"{name}-details.tsv"
    fields = [
        "scope",
        "path",
        "source_id",
        "raw_observation_id",
        "migrated_observation_id",
        "method_class",
        "evidence_class",
        "citation_locations",
        "citation_texts",
    ]
    with detail_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(records)

    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in records:
        grouped[(row["scope"], row["path"], row["source_id"])].append(row)
    for source in sources:
        grouped.setdefault(source, [])
    table_path = NOTES / f"{name}-table.tsv"
    with table_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(("scope", "path", "source_id", "n_affected", "printed_citation_lives"))
        for (scope, path, source_id), rows in sorted(grouped.items()):
            locations = Counter()
            for row in rows:
                row_fields = {
                    re.sub(r"\[\d+\]", "[]", field)
                    for field in row["citation_locations"].split(" | ")
                    if field not in {"NONE", "values.attribution"}
                }
                for field in row_fields:
                    locations[field] += 1
            summary = "; ".join(f"{field} ({count})" for field, count in sorted(locations.items()))
            writer.writerow((scope, path, source_id, len(rows), summary or "NONE"))
    print(f"wrote {len(records)} affected observations; {len(grouped)} source rows", flush=True)
    print(f"table={table_path}", flush=True)
    print(f"details={detail_path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("green", "branches"))
    parser.add_argument("--name", default=None)
    args = parser.parse_args()
    name = args.name or ("baseline" if args.mode == "green" else "pending-branches")
    records, sources = run_base() if args.mode == "green" else run_branches()
    write_outputs(name, records, sources)


if __name__ == "__main__":
    main()
