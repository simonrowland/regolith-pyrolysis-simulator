#!/usr/bin/env python3
"""Re-stamp reduced-real cache rows to a deliberate corpus version."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.corpus_version import (  # noqa: E402
    current_corpus_version,
    interoperable_corpus_versions,
)
from simulator.engine_local_config import is_legacy_cache_version  # noqa: E402
from simulator.reduced_real_determinism import (  # noqa: E402
    PT1_EQUILIBRIUM_TABLE,
    _physics_ladder_values_from_replay_key,
    _replay_scope_hash,
    canonical_json_bytes,
    canonical_physics_bucket_key_from_replay_key,
)


@dataclass(frozen=True)
class RekeyResult:
    rows_before: int
    rows_updated: int
    backup_path: Path | None = None

    def __iter__(self):
        yield self.rows_before
        yield self.rows_updated


def _json_loads(raw: bytes) -> dict[str, Any]:
    loaded = json.loads(raw.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("cache key root must be a JSON object")
    return loaded


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _row_matches_engine(key: dict[str, Any], engine: str) -> bool:
    engine_key = str(engine or "").strip().lower()
    if not engine_key:
        return True
    backend = _dict(key.get("backend"))
    provider = _dict(key.get("provider"))
    fields = (
        backend.get("backend_name"),
        backend.get("backend_class"),
        provider.get("resolved_provider_id"),
        provider.get("authoritative_provider_id"),
        provider.get("fallback_provider_id"),
        provider.get("model"),
        key.get("engine_version"),
    )
    return any(engine_key in str(value or "").lower() for value in fields)


def _engine_version_provenance(
    key: dict[str, Any],
    row_engine_version: Any,
) -> str | None:
    if row_engine_version not in (None, ""):
        return str(row_engine_version)
    provider = _dict(key.get("provider"))
    backend = _dict(key.get("backend"))
    for value in (
        key.get("engine_version"),
        provider.get("engine_version"),
        backend.get("backend_version"),
    ):
        if value not in (None, ""):
            return str(value)
    return None


def _replace_cache_identity(key: dict[str, Any], target_corpus_version: str) -> bool:
    changed = False
    for field in ("engine_version", "source_module_digest", "code_version"):
        if field in key:
            key.pop(field, None)
            changed = True
    if key.get("corpus_version") != target_corpus_version:
        key["corpus_version"] = target_corpus_version
        changed = True

    backend = key.get("backend")
    if isinstance(backend, dict):
        if "backend_version" in backend:
            backend.pop("backend_version", None)
            changed = True
        if backend.get("corpus_version") != target_corpus_version:
            backend["corpus_version"] = target_corpus_version
            changed = True

    for section in ("provider", "vapor_pressure_provider"):
        provider = key.get(section)
        if isinstance(provider, dict) and "engine_version" in provider:
            provider.pop("engine_version", None)
            changed = True
    return changed


def _validated_target_corpus_version(target_corpus_version: str | None) -> str:
    target = (target_corpus_version or current_corpus_version()).strip()
    if not target:
        raise SystemExit("target corpus version must be non-empty")
    allowed = frozenset(interoperable_corpus_versions())
    if target not in allowed:
        raise SystemExit(
            "target corpus version is not declared interoperable in "
            f"data/corpus_version.yaml: {target!r}"
        )
    return target


def _needs_rekey(key: dict[str, Any], target_corpus_version: str) -> bool:
    if key.get("corpus_version") != target_corpus_version:
        return True
    if "source_module_digest" in key:
        return True
    if "code_version" in key:
        return True
    if is_legacy_cache_version(str(key.get("engine_version") or "")):
        return True
    if "engine_version" in key:
        return True
    backend = _dict(key.get("backend"))
    provider = _dict(key.get("provider"))
    vapor_provider = _dict(key.get("vapor_pressure_provider"))
    return any(
        "engine_version" in value or "backend_version" in value
        for value in (backend, provider, vapor_provider)
    )


def _ensure_corpus_column(conn: sqlite3.Connection) -> None:
    existing = _table_columns(conn)
    if "corpus_version" not in existing:
        conn.execute(
            f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} ADD COLUMN corpus_version TEXT"
        )


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
    }


def _physics_columns(key: dict[str, Any]) -> dict[str, Any]:
    physics_key = canonical_physics_bucket_key_from_replay_key(key)
    physics_bytes = canonical_json_bytes(physics_key)
    ladder_values = _physics_ladder_values_from_replay_key(key)
    return {
        "physics_bucket_schema_version": str(physics_key.get("schema_version")),
        "physics_bucket_sha256": hashlib.sha256(physics_bytes).hexdigest(),
        "replay_scope_sha256": _replay_scope_hash(physics_key),
        "physics_key_bytes": sqlite3.Binary(physics_bytes),
        "physics_bucket_h40_sha256": ladder_values["h40"]["sha256"],
        "physics_bucket_h40_distance": ladder_values["h40"]["distance"],
        "physics_bucket_h30_sha256": ladder_values["h30"]["sha256"],
        "physics_bucket_h30_distance": ladder_values["h30"]["distance"],
        "physics_bucket_h40c_sha256": ladder_values["h40c"]["sha256"],
        "physics_bucket_h40c_distance": ladder_values["h40c"]["distance"],
        "physics_bucket_h30c_sha256": ladder_values["h30c"]["sha256"],
        "physics_bucket_h30c_distance": ladder_values["h30c"]["distance"],
    }


def _count_rows_needing_rekey(
    conn: sqlite3.Connection,
    *,
    engine: str,
    target_corpus_version: str,
) -> int:
    count = 0
    for (key_bytes,) in conn.execute(
        f"SELECT key_bytes FROM {PT1_EQUILIBRIUM_TABLE}"
    ):
        key = _json_loads(bytes(key_bytes))
        if not _row_matches_engine(key, engine):
            continue
        if _needs_rekey(key, target_corpus_version):
            count += 1
    return count


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_db(conn: sqlite3.Connection, db_path: Path) -> tuple[Path, int]:
    if not conn.in_transaction:
        raise RuntimeError("cache backup requires an active transaction lock")
    source_data_version = int(conn.execute("PRAGMA data_version").fetchone()[0])
    with tempfile.NamedTemporaryFile(
        prefix=f".{db_path.name}.backup-",
        dir=db_path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        snapshot_conn = sqlite3.connect(db_path)
        backup_conn = sqlite3.connect(temporary_path)
        try:
            snapshot_conn.backup(backup_conn)
        finally:
            backup_conn.close()
            snapshot_conn.close()

        if int(conn.execute("PRAGMA data_version").fetchone()[0]) != source_data_version:
            raise RuntimeError("cache database changed while the backup was created")

        backup_digest = _file_sha256(temporary_path)
        backup_path = db_path.with_name(
            f"{db_path.name}.backup-{backup_digest[:16]}"
        )
        try:
            os.link(temporary_path, backup_path)
        except FileExistsError:
            if _file_sha256(backup_path) != backup_digest:
                raise RuntimeError("existing retry backup failed its identity check")
        return backup_path, source_data_version
    finally:
        temporary_path.unlink(missing_ok=True)


def rekey_cache(
    db_path: Path,
    *,
    engine: str = "alphamelts",
    target_corpus_version: str | None = None,
    dry_run: bool = False,
) -> RekeyResult:
    target = _validated_target_corpus_version(target_corpus_version)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if dry_run:
            before = _count_rows_needing_rekey(
                conn,
                engine=engine,
                target_corpus_version=target,
            )
            return RekeyResult(rows_before=before, rows_updated=0, backup_path=None)

        conn.execute("BEGIN IMMEDIATE")
        before = _count_rows_needing_rekey(
            conn,
            engine=engine,
            target_corpus_version=target,
        )
        if before == 0:
            conn.rollback()
            return RekeyResult(rows_before=before, rows_updated=0, backup_path=None)

        backup_path, backup_data_version = _backup_db(conn, db_path)
        if int(conn.execute("PRAGMA data_version").fetchone()[0]) != backup_data_version:
            raise RuntimeError("cache database changed after the backup was created")

        _ensure_corpus_column(conn)
        updated = 0
        table_columns = _table_columns(conn)
        for row in conn.execute(
            f"""
            SELECT key_hash, key_bytes, engine_version
            FROM {PT1_EQUILIBRIUM_TABLE}
            """
        ):
            key = _json_loads(bytes(row["key_bytes"]))
            if not _row_matches_engine(key, engine):
                continue
            if not _needs_rekey(key, target):
                continue
            provenance = _engine_version_provenance(key, row["engine_version"])
            if not _replace_cache_identity(key, target):
                continue
            key_bytes = canonical_json_bytes(key)
            key_hash = hashlib.sha256(key_bytes).hexdigest()
            physics_columns_present = {
                "physics_bucket_schema_version",
                "physics_bucket_sha256",
                "replay_scope_sha256",
                "physics_key_bytes",
                "physics_bucket_h40_sha256",
                "physics_bucket_h40_distance",
                "physics_bucket_h30_sha256",
                "physics_bucket_h30_distance",
                "physics_bucket_h40c_sha256",
                "physics_bucket_h40c_distance",
                "physics_bucket_h30c_sha256",
                "physics_bucket_h30c_distance",
            } & table_columns
            physics = _physics_columns(key) if physics_columns_present else {}
            assignments = [
                "key_bytes = ?",
                "key_sha256 = ?",
                "key_hash = ?",
                "corpus_version = ?",
                "engine_version = ?",
            ]
            values: list[Any] = [
                sqlite3.Binary(key_bytes),
                key_hash,
                key_hash,
                target,
                provenance,
            ]
            for column, value in physics.items():
                if column not in table_columns:
                    continue
                assignments.append(f"{column} = ?")
                values.append(value)
            values.append(row["key_hash"])
            conn.execute(
                f"""
                UPDATE {PT1_EQUILIBRIUM_TABLE}
                SET {", ".join(assignments)}
                WHERE key_hash = ?
                """,
                tuple(values),
            )
            updated += 1

        conn.commit()
        return RekeyResult(
            rows_before=before,
            rows_updated=updated,
            backup_path=backup_path,
        )
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-stamp reduced-real cache rows to a deliberate corpus_version."
        )
    )
    parser.add_argument("cache_sqlite", type=Path, help="Path to cache SQLite DB")
    parser.add_argument(
        "--engine",
        default="alphamelts",
        help="Only re-stamp rows for this backend/provider family",
    )
    parser.add_argument(
        "--target-corpus-version",
        default=None,
        help="Corpus version to stamp; defaults to data/corpus_version.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report rows needing rekey without mutating the database",
    )
    args = parser.parse_args(argv)

    if not args.cache_sqlite.is_file():
        raise SystemExit(f"cache database not found: {args.cache_sqlite}")

    result = rekey_cache(
        args.cache_sqlite,
        engine=args.engine,
        target_corpus_version=args.target_corpus_version,
        dry_run=args.dry_run,
    )
    print(f"rows_needing_rekey_before={result.rows_before}")
    print(f"rows_updated={result.rows_updated}")
    print(f"dry_run={int(args.dry_run)}")
    if result.backup_path is not None:
        print(f"backup_path={result.backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
