#!/usr/bin/env python3
"""Regenerate the executable cache identity schema/manifest golden."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cache_convert import (  # noqa: E402
    DDL,
    execute_cache_identity_migration_fixture,
)
from scripts.grid_pregrind_writer import (  # noqa: E402
    SCHEMA_SQL,
    cache_v2_identity_manifest,
)


GOLDEN = (
    ROOT
    / "docs-private/research/2026-07-19-cache-reissue"
    / "b-043-cache-contract.golden.json"
)


def _normalized_schema(sql: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(sql)
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "type": row[0],
            "name": row[1],
            "table": row[2],
            "sql": row[3],
        }
        for row in rows
    ]


def _migration_collision_outcomes() -> dict[str, Any]:
    return execute_cache_identity_migration_fixture()


def _logical_parity(
    grid_schema: list[dict[str, Any]],
    converter_schema: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, bool]:
    grid_sql = "\n".join(str(row["sql"] or "") for row in grid_schema)
    converter_sql = "\n".join(str(row["sql"] or "") for row in converter_schema)
    manifest_text = json.dumps(manifest, sort_keys=True)
    return {
        "grid_result_unique_without_epoch_or_version": (
            "UNIQUE(expedited_key)" in grid_sql
            and "UNIQUE(expedited_key, engine_epoch)" not in grid_sql
        ),
        "converter_result_unique_without_epoch_or_version": (
            "UNIQUE (\n        state_id, artifact_kind, consumer_id\n    )"
            in converter_sql
            and "state_id, engine_epoch, artifact_kind" not in converter_sql
        ),
        "engine_version_indexed_metadata": (
            "engine_version" in grid_sql and "engine_version_metadata" in converter_sql
        ),
        "manifest_has_no_corpus_or_cache_lever": (
            "corpus_version" not in manifest_text and "cache_lever" not in manifest_text
        ),
        "manifest_version_not_identity": (
            "engine_version" not in json.dumps(manifest["identity"], sort_keys=True)
            and "engine_version" not in json.dumps(manifest["key_hash"], sort_keys=True)
        ),
    }


def build_payload() -> dict[str, Any]:
    grid_schema = _normalized_schema(SCHEMA_SQL)
    converter_schema = _normalized_schema(DDL)
    manifest = cache_v2_identity_manifest()
    return {
        "cache_v2_identity_manifest": manifest,
        "converter_sqlite_schema": converter_schema,
        "embedded_sql_parity": _logical_parity(grid_schema, converter_schema, manifest),
        "grid_sqlite_schema": grid_schema,
        "migration_collision_outcomes": _migration_collision_outcomes(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(build_payload(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not GOLDEN.is_file() or GOLDEN.read_text(encoding="utf-8") != rendered:
            print(f"cache identity golden is stale: {GOLDEN}", file=sys.stderr)
            return 1
        return 0
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
