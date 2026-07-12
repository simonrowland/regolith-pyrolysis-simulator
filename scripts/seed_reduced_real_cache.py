#!/usr/bin/env python3
"""Seed a study reduced-real cache DB from one or more PT-1 cache DBs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simulator.grind_preflight import assert_strict_vapor_pt1_row  # noqa: E402
from simulator.reduced_real_determinism import (  # noqa: E402
    PT1_EQUILIBRIUM_TABLE,
    PT1_METADATA_TABLE,
    PT1_STORE_SCHEMA_VERSION,
    PT1PersistentEquilibriumStore,
    canonical_json_bytes,
    validate_reduced_real_equilibrium_record_key,
)


PAYLOAD_TABLE = PT1_EQUILIBRIUM_TABLE
_ROW_IDENTITY_FIELDS = (
    "artifact",
    "store_schema_version",
    "request_schema_version",
    "key_sha256",
    "payload_sha256",
    "key_bytes",
    "payload_bytes",
    "code_version",
    "corpus_version",
    "engine_version",
    "data_digests_json",
    "created_at",
    "git_dirty",
)
_REQUIRED_TEXT_FIELDS = (
    "key_hash",
    "artifact",
    "store_schema_version",
    "request_schema_version",
    "key_sha256",
    "payload_sha256",
    "code_version",
    "data_digests_json",
    "created_at",
)


class CacheSourceSchemaMismatch(ValueError):
    """Source DB is not a PT-1 reduced-real cache with the expected schema."""


class CacheSourceRowInvalid(ValueError):
    """Source row bytes, hashes, or provenance do not match its canonical key."""


class CacheMergeCollision(RuntimeError):
    """A cache key resolves to conflicting payload or provenance fields."""


def _format_schema_mismatch(source: Path, field: str, found: Any) -> str:
    return (
        "PT-1 cache source schema mismatch: "
        f"source={source} field={field} "
        f"found={found!r} expected={PT1_STORE_SCHEMA_VERSION!r}"
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
        if found_schema != PT1_STORE_SCHEMA_VERSION:
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
            version for version in versions if version != PT1_STORE_SCHEMA_VERSION
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


def _source_payload_rows(source: Path) -> list[dict[str, Any]]:
    con = open_cache_source_readonly(source)
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (PAYLOAD_TABLE,),
        ).fetchone()
        if exists is None:
            return []
        columns = {
            str(row["name"]) for row in con.execute(f"PRAGMA table_info({PAYLOAD_TABLE})")
        }
        corpus_column = (
            "corpus_version"
            if "corpus_version" in columns
            else "NULL AS corpus_version"
        )
        return [
            dict(row)
            for row in con.execute(
                f"""
                SELECT
                    key_hash,
                    artifact,
                    store_schema_version,
                    request_schema_version,
                    key_sha256,
                    payload_sha256,
                    key_bytes,
                    payload_bytes,
                    code_version,
                    {corpus_column},
                    engine_version,
                    data_digests_json,
                    created_at,
                    git_dirty
                FROM {PAYLOAD_TABLE}
                """
            )
        ]
    finally:
        con.close()


def _row_identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row[field] for field in _ROW_IDENTITY_FIELDS)


def validate_cache_source_rows(source: Path) -> list[dict[str, Any]]:
    validate_source_schema(source)
    rows = _source_payload_rows(source)
    for row in rows:
        invalid_types = [
            field for field in _REQUIRED_TEXT_FIELDS if not isinstance(row[field], str)
        ]
        if not isinstance(row["key_bytes"], bytes):
            invalid_types.append("key_bytes")
        if not isinstance(row["payload_bytes"], bytes):
            invalid_types.append("payload_bytes")
        for field in ("corpus_version", "engine_version"):
            if row[field] is not None and not isinstance(row[field], str):
                invalid_types.append(field)
        if type(row["git_dirty"]) is not int:
            invalid_types.append("git_dirty")
        if invalid_types:
            raise CacheSourceRowInvalid(
                "PT-1 cache source row invalid storage types: "
                f"{source} fields={','.join(invalid_types)}"
            )
        artifact = str(row["artifact"])
        key_hash = str(row["key_hash"])
        key_bytes = bytes(row["key_bytes"])
        payload_bytes = bytes(row["payload_bytes"])
        try:
            key = json.loads(key_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"PT-1 cache source row has invalid key bytes: {source}:{key_hash}"
            ) from exc
        if not isinstance(key, Mapping):
            raise RuntimeError(
                f"PT-1 cache source row key must be a mapping: {source}:{key_hash}"
            )
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"PT-1 cache source row has invalid payload bytes: {source}:{key_hash}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise CacheSourceRowInvalid(
                f"PT-1 cache source row payload must be a mapping: {source}:{key_hash}"
            )
        canonical_key_bytes = canonical_json_bytes(key)
        canonical_payload_bytes = canonical_json_bytes(payload)
        key_digest = hashlib.sha256(canonical_key_bytes).hexdigest()
        payload_digest = hashlib.sha256(canonical_payload_bytes).hexdigest()
        expected_metadata = {
            "request_schema_version": str(key.get("schema_version")),
            "corpus_version": str(key.get("corpus_version")),
            "data_digests_json": canonical_json_bytes(
                key.get("data_digests", {})
            ).decode("utf-8"),
        }
        mismatches: list[str] = []
        if "artifact" in key and key["artifact"] != artifact:
            mismatches.append("artifact")
        if key_bytes != canonical_key_bytes:
            mismatches.append("key_bytes")
        if payload_bytes != canonical_payload_bytes:
            mismatches.append("payload_bytes")
        if key_hash != key_digest:
            mismatches.append("key_hash")
        if str(row["key_sha256"]) != key_digest:
            mismatches.append("key_sha256")
        if str(row["payload_sha256"]) != payload_digest:
            mismatches.append("payload_sha256")
        for field, expected in expected_metadata.items():
            if str(row[field]) != expected:
                mismatches.append(field)
        try:
            datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        except ValueError:
            mismatches.append("created_at")
        if type(row["git_dirty"]) is not int or row["git_dirty"] not in (0, 1):
            mismatches.append("git_dirty")
        if mismatches:
            raise CacheSourceRowInvalid(
                "PT-1 cache source row canonical/provenance mismatch: "
                f"{source}:{key_hash} fields={','.join(mismatches)}"
            )
        validate_reduced_real_equilibrium_record_key(artifact, key)
        assert_strict_vapor_pt1_row(
            artifact=artifact,
            key=key,
            key_hash=key_hash,
            payload=payload,
            context=f"PT-1 cache source {source}:{artifact}:{key_hash}",
        )
        row["validated_key"] = key
        row["validated_payload"] = payload
    return rows


def validate_merge_plan(
    target: Path,
    sources: Iterable[tuple[Path, list[dict[str, Any]]]],
) -> None:
    identities: dict[str, tuple[Any, ...]] = {}
    planned = list(sources)
    if target.exists():
        planned.insert(0, (target, validate_cache_source_rows(target)))
    for source, rows in planned:
        for row in rows:
            key_hash = str(row["key_hash"])
            identity = _row_identity(row)
            existing = identities.setdefault(key_hash, identity)
            if existing != identity:
                raise CacheMergeCollision(
                    f"PT-1 cache collision while preflighting {source}:{key_hash}"
                )


def merge_cache_source(
    source: Path,
    target: Path,
    *,
    validated_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = validated_rows if validated_rows is not None else validate_cache_source_rows(source)
    target_store = PT1PersistentEquilibriumStore(target)
    inserted_rows = 0
    with target_store._connect() as conn:
        target_store._initialize(conn)
        for row in rows:
            artifact = str(row["artifact"])
            key_hash = str(row["key_hash"])
            key_bytes = bytes(row["key_bytes"])
            payload_bytes = bytes(row["payload_bytes"])
            payload_hash = str(row["payload_sha256"])
            existing = conn.execute(
                f"""
                SELECT artifact, store_schema_version, request_schema_version,
                       key_sha256, payload_sha256, key_bytes, payload_bytes,
                       code_version, corpus_version, engine_version,
                       data_digests_json, created_at, git_dirty
                FROM {PAYLOAD_TABLE}
                WHERE key_hash = ?
                """,
                (key_hash,),
            ).fetchone()
            if existing is not None:
                if _row_identity(existing) != _row_identity(row):
                    raise CacheMergeCollision(
                        f"PT-1 cache collision while merging {key_hash}"
                    )
                continue
            conn.execute(
                f"""
                INSERT INTO {PAYLOAD_TABLE} (
                    key_hash,
                    artifact,
                    store_schema_version,
                    request_schema_version,
                    key_sha256,
                    payload_sha256,
                    key_bytes,
                    payload_bytes,
                    code_version,
                    corpus_version,
                    engine_version,
                    data_digests_json,
                    created_at,
                    git_dirty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key_hash,
                    artifact,
                    str(row["store_schema_version"]),
                    str(row["request_schema_version"]),
                    str(row["key_sha256"]),
                    payload_hash,
                    sqlite3.Binary(key_bytes),
                    sqlite3.Binary(payload_bytes),
                    str(row["code_version"]),
                    row.get("corpus_version"),
                    row["engine_version"],
                    str(row["data_digests_json"]),
                    str(row["created_at"]),
                    int(row["git_dirty"]),
                ),
            )
            inserted_rows += 1
    return {
        "merged": True,
        "source": str(source),
        "rows": len(rows),
        "inserted_rows": inserted_rows,
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
    validate_merge_plan(target, validated_sources)

    PT1PersistentEquilibriumStore(target)
    before = payload_count(target)
    source_summaries: list[dict[str, Any]] = []
    total_inserted = 0
    validated_by_source = {source.resolve(): rows for source, rows in validated_sources}
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
        result = merge_cache_source(source, target, validated_rows=rows)
        inserted = int(result.get("inserted_rows", 0))
        total_inserted += inserted
        source_summaries.append(
            {
                "source": str(source),
                "source_rows": source_rows,
                "inserted_rows": inserted,
            }
        )

    after = payload_count(target)
    return {
        "target": str(target),
        "rows_before": before,
        "rows_after": after,
        "inserted_rows": total_inserted,
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
