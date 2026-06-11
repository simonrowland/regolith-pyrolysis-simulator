#!/usr/bin/env python3
"""Seed a study reduced-real cache DB from one or more PT-1 cache DBs."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.populate_reduced_real_cache import _merge_cache_shard  # noqa: E402
from simulator.reduced_real_determinism import (  # noqa: E402
    PT1_EQUILIBRIUM_TABLE,
    PT1_METADATA_TABLE,
    PT1_STORE_SCHEMA_VERSION,
    PT1PersistentEquilibriumStore,
)


PAYLOAD_TABLE = PT1_EQUILIBRIUM_TABLE


class CacheSourceSchemaMismatch(ValueError):
    """Source DB is not a PT-1 reduced-real cache with the expected schema."""


def _format_schema_mismatch(source: Path, field: str, found: Any) -> str:
    return (
        "PT-1 cache source schema mismatch: "
        f"source={source} field={field} "
        f"found={found!r} expected={PT1_STORE_SCHEMA_VERSION!r}"
    )


def validate_source_schema(source: Path) -> None:
    con = sqlite3.connect(source)
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
    con = sqlite3.connect(db_path)
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

    PT1PersistentEquilibriumStore(target)
    target_resolved = target.resolve()
    before = payload_count(target)
    source_summaries: list[dict[str, Any]] = []
    total_inserted = 0
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
        validate_source_schema(source)
        source_rows = payload_count(source)
        result = _merge_cache_shard(source, target)
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
