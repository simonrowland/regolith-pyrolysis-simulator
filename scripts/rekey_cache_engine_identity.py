#!/usr/bin/env python3
"""Re-stamp reduced-real cache rows from legacy path identity to config identity."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.engine_local_config import (  # noqa: E402
    cache_version_for,
    is_legacy_cache_version,
    load_config,
)
from simulator.reduced_real_determinism import (  # noqa: E402
    PT1_EQUILIBRIUM_TABLE,
    canonical_json_bytes,
)


def _json_loads(raw: bytes) -> dict[str, Any]:
    loaded = json.loads(raw.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("cache key root must be a JSON object")
    return loaded


def _replace_engine_version(key: dict[str, Any], new_version: str) -> bool:
    changed = False
    if key.get("engine_version") != new_version:
        key["engine_version"] = new_version
        changed = True

    backend = key.get("backend")
    if isinstance(backend, dict) and backend.get("backend_version") != new_version:
        backend["backend_version"] = new_version
        changed = True

    provider = key.get("provider")
    if isinstance(provider, dict) and provider.get("engine_version") != new_version:
        provider["engine_version"] = new_version
        changed = True
    return changed


def _count_legacy_rows(conn: sqlite3.Connection, engine: str) -> int:
    target = cache_version_for(engine)
    if target is None:
        return 0
    count = 0
    for (key_bytes,) in conn.execute(
        f"SELECT key_bytes FROM {PT1_EQUILIBRIUM_TABLE}"
    ):
        key = _json_loads(bytes(key_bytes))
        version = str(key.get("engine_version") or "")
        if is_legacy_cache_version(version):
            count += 1
    return count


def rekey_cache(db_path: Path, *, engine: str = "alphamelts") -> tuple[int, int]:
    config = load_config(required=True)
    new_version = cache_version_for(engine)
    if new_version is None:
        raise SystemExit(
            f"engines.local.toml has no identity for {engine!r}; "
            "run install-engines.py first"
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    before = _count_legacy_rows(conn, engine)
    updated = 0

    for row in conn.execute(
        f"""
        SELECT key_hash, key_bytes, engine_version
        FROM {PT1_EQUILIBRIUM_TABLE}
        """
    ):
        key = _json_loads(bytes(row["key_bytes"]))
        current = str(key.get("engine_version") or "")
        if not is_legacy_cache_version(current):
            continue
        if not _replace_engine_version(key, new_version):
            continue
        key_bytes = canonical_json_bytes(key)
        key_hash = __import__("hashlib").sha256(key_bytes).hexdigest()
        conn.execute(
            f"""
            UPDATE {PT1_EQUILIBRIUM_TABLE}
            SET key_bytes = ?,
                key_sha256 = ?,
                key_hash = ?,
                engine_version = ?
            WHERE key_hash = ?
            """,
            (
                key_bytes,
                key_hash,
                key_hash,
                new_version,
                row["key_hash"],
            ),
        )
        updated += 1

    conn.commit()
    after = _count_legacy_rows(conn, engine)
    conn.close()
    return before, updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-stamp reduced-real cache engine_version fields from legacy "
            "path-based identity to engines.local.toml digest identity."
        )
    )
    parser.add_argument("cache_sqlite", type=Path, help="Path to cache SQLite DB")
    parser.add_argument(
        "--engine",
        default="alphamelts",
        help="Engine identity block to apply (default: alphamelts)",
    )
    args = parser.parse_args(argv)

    if not args.cache_sqlite.is_file():
        raise SystemExit(f"cache database not found: {args.cache_sqlite}")

    before, updated = rekey_cache(args.cache_sqlite, engine=args.engine)
    after = before - updated
    print(f"legacy_rows_before={before}")
    print(f"rows_updated={updated}")
    print(f"legacy_rows_after={after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())