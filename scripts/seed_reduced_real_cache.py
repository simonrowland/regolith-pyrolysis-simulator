#!/usr/bin/env python3
"""Seed a study reduced-real cache DB from one or more PT-1 cache DBs."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simulator.reduced_real_determinism import (  # noqa: E402
    PT1_EQUILIBRIUM_TABLE,
    PT1_METADATA_TABLE,
    PT1_STORE_SCHEMA_VERSION,
    PT1_SUPPORTED_STORE_SCHEMA_VERSIONS,
    PT1PersistentStoreCorrupt,
    _PT1CacheMergeCollision,
    _admit_stored_row,
    _copy_pt1_equilibrium_rows,
)


PAYLOAD_TABLE = PT1_EQUILIBRIUM_TABLE


class CacheSourceSchemaMismatch(ValueError):
    """Source DB is not a PT-1 reduced-real cache with the expected schema."""


class CacheSourceRowInvalid(ValueError):
    """Source row bytes, hashes, or provenance do not match its canonical key."""


CacheMergeCollision = _PT1CacheMergeCollision


def _format_schema_mismatch(source: Path, field: str, found: Any) -> str:
    return (
        "PT-1 cache source schema mismatch: "
        f"source={source} field={field} "
        f"found={found!r} expected_one_of={PT1_SUPPORTED_STORE_SCHEMA_VERSIONS!r}"
    )


def open_cache_source_readonly(source: Path) -> sqlite3.Connection:
    source = source.expanduser()
    if not source.exists():
        raise FileNotFoundError(f"cache source does not exist: {source}")
    con = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def validate_source_schema(source: Path) -> None:
    con = open_cache_source_readonly(source)
    try:
        metadata_table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (PT1_METADATA_TABLE,),
        ).fetchone()
        found_schema = None
        if metadata_table is not None:
            row = con.execute(
                f"SELECT value FROM {PT1_METADATA_TABLE} WHERE key=?",
                ("store_schema_version",),
            ).fetchone()
            if row is not None:
                found_schema = str(row[0])
        if found_schema not in PT1_SUPPORTED_STORE_SCHEMA_VERSIONS:
            raise CacheSourceSchemaMismatch(
                _format_schema_mismatch(
                    source,
                    f"{PT1_METADATA_TABLE}.store_schema_version",
                    found_schema,
                )
            )

        payload_table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (PAYLOAD_TABLE,),
        ).fetchone()
        if payload_table is None:
            return
        versions = [
            str(row[0])
            for row in con.execute(
                f"SELECT DISTINCT store_schema_version FROM {PAYLOAD_TABLE}"
            )
        ]
        mismatched = sorted(
            version
            for version in versions
            if version not in PT1_SUPPORTED_STORE_SCHEMA_VERSIONS
        )
        if mismatched:
            raise CacheSourceSchemaMismatch(
                _format_schema_mismatch(
                    source,
                    f"{PAYLOAD_TABLE}.store_schema_version",
                    mismatched,
                )
            )
    finally:
        con.close()


def payload_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    con = open_cache_source_readonly(db_path)
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (PAYLOAD_TABLE,),
        ).fetchone()
        if exists is None:
            return 0
        return int(con.execute(f"SELECT count(*) FROM {PAYLOAD_TABLE}").fetchone()[0])
    finally:
        con.close()


def _source_payload_rows(source: Path) -> list[sqlite3.Row]:
    con = open_cache_source_readonly(source)
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (PAYLOAD_TABLE,),
        ).fetchone()
        if exists is None:
            return []
        return list(con.execute(f"SELECT * FROM {PAYLOAD_TABLE}"))
    finally:
        con.close()


def validate_cache_source_rows(source: Path) -> list[dict[str, Any]]:
    validate_source_schema(source)
    rows = _source_payload_rows(source)
    validated_rows: list[dict[str, Any]] = []
    for raw_row in rows:
        try:
            admitted = _admit_stored_row(
                raw_row,
                store_kind="pt1",
                strict_source_validation=True,
                validate_source_provenance=True,
            )
        except PT1PersistentStoreCorrupt as exc:
            message = str(exc)
            if (
                isinstance(exc.__cause__, RuntimeError)
                and str(exc.__cause__).startswith("PT-1 reduced-real ")
            ):
                raise
            field = (
                "payload_sha256"
                if "payload hash mismatch" in message
                else "repair_notices_json"
                if "repair_notices_json" in message
                else "key_hash"
                if "key bytes hash mismatch" in message
                else "key_sha256"
                if "key hash mismatch" in message
                else "stored row"
            )
            raise CacheSourceRowInvalid(
                "PT-1 cache source row invalid stored fields: "
                f"{source}:{field}: {message}"
            ) from exc
        assert admitted is not None and admitted.stored_columns is not None
        row = admitted.stored_columns
        row.setdefault("corpus_version", None)
        row.setdefault("repair_notices_json", None)
        row.setdefault("payload_codec_version", None)
        if admitted.stale_payload_codec:
            row["store_schema_version"] = PT1_STORE_SCHEMA_VERSION
            validated_rows.append(row)
            continue
        key = admitted.entry["key"]
        payload = admitted.entry["payload"]
        row["store_schema_version"] = PT1_STORE_SCHEMA_VERSION
        row["validated_key"] = key
        row["validated_payload"] = payload
        validated_rows.append(row)
    return validated_rows


def merge_cache_source(
    source: Path,
    target: Path,
    *,
    validated_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = (
        validated_rows
        if validated_rows is not None
        else validate_cache_source_rows(source)
    )
    if source.resolve() == target.resolve():
        return {
            "merged": True,
            "source": str(source),
            "rows": len(rows),
            "inserted_rows": 0,
        }
    result = _copy_pt1_equilibrium_rows(
        [source],
        target,
        require_capture_epoch_match=True,
        strict_source_validation=True,
    )
    source_result = result["sources"][0]
    return {
        "merged": True,
        "source": str(source),
        "rows": len(rows),
        "inserted_rows": int(source_result["inserted_rows"]),
    }


def discover_sources(paths: Iterable[Path]) -> list[Path]:
    sources: list[Path] = []
    for raw in paths:
        path = raw.expanduser()
        if not path.exists():
            raise FileNotFoundError(f"cache source does not exist: {path}")
        if path.is_file():
            sources.append(path)
            continue
        for candidate in sorted(path.rglob("*")):
            if candidate.is_file() and (
                candidate.name == "cache.sqlite"
                or candidate.suffix in {".db", ".sqlite"}
            ):
                sources.append(candidate)
    unique: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        resolved = source.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(source)
    return unique


def seed_cache(target: Path, sources: Iterable[Path]) -> dict[str, Any]:
    target = target.expanduser()
    discovered = discover_sources(sources)
    if not discovered:
        raise ValueError("no cache source DBs found")

    target_resolved = target.resolve()
    validated_sources: list[tuple[Path, list[dict[str, Any]]]] = []
    for source in discovered:
        if source.resolve() != target_resolved:
            validated_sources.append((source, validate_cache_source_rows(source)))
    validated_by_source = {
        source.resolve(): rows for source, rows in validated_sources
    }
    before = payload_count(target)
    source_summaries: list[dict[str, Any]] = []
    merged = _copy_pt1_equilibrium_rows(
        [source for source, _rows in validated_sources],
        target,
        require_capture_epoch_match=True,
        strict_source_validation=True,
    )
    merge_by_source = {
        Path(summary["source"]).resolve(): summary
        for summary in merged["sources"]
    }
    for source in discovered:
        if source.resolve() == target_resolved:
            source_summaries.append(
                {
                    "source": str(source),
                    "source_rows": payload_count(source),
                    "inserted_rows": 0,
                    "skipped": "target",
                }
            )
            continue
        rows = validated_by_source[source.resolve()]
        source_rows = len(rows)
        result = merge_by_source[source.resolve()]
        source_summaries.append(
            {
                "source": str(source),
                "source_rows": source_rows,
                "inserted_rows": int(result["inserted_rows"]),
            }
        )

    after = payload_count(target)
    return {
        "target": str(target),
        "rows_before": before,
        "rows_after": after,
        "inserted_rows": int(merged["inserted_rows"]),
        "sources": source_summaries,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed a study reduced-real cache DB from consolidated or recipe-db "
            "PT-1 cache DBs. Existing targets are merged idempotently."
        )
    )
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        type=Path,
        help="source DB or directory; repeatable",
    )
    parser.add_argument("sources", nargs="*", type=Path)
    parser.add_argument("--json", action="store_true", help="emit machine JSON only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = seed_cache(args.target, [*args.source, *args.sources])
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(summary, sort_keys=True))
        return 0
    for source in summary["sources"]:
        skipped = f" skipped={source['skipped']}" if "skipped" in source else ""
        print(
            f"source {source['source']}: "
            f"rows={source['source_rows']} inserted={source['inserted_rows']}"
            f"{skipped}"
        )
    print(
        f"target {summary['target']}: rows_before={summary['rows_before']} "
        f"rows_after={summary['rows_after']} inserted={summary['inserted_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
