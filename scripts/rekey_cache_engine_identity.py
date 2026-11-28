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

from simulator.reduced_real_determinism import (  # noqa: E402
    PHYSICS_BUCKET_ALL_LADDER_RUNGS,
    _PT1_SOURCE_REQUIRED_TEXT_FIELDS,
    PT1_SUPPORTED_STORE_SCHEMA_VERSIONS,
    PT1_EQUILIBRIUM_TABLE,
    PT1PersistentStoreCorrupt,
    UncacheableKey,
    _admit_stored_row,
    _derive_pt1_key_indexes,
    _physics_ladder_distance_column,
    _physics_ladder_hash_column,
    _pt1_physics_index_columns,
    _pt1_row_key,
    _validate_pt1_key_indexes,
    _sha256,
    cache_row_needs_rekey,
    canonical_json_bytes,
    rekey_engine_version_provenance,
    rekey_row_matches_engine,
    replace_rekey_cache_identity,
    validated_rekey_corpus_version,
)


@dataclass(frozen=True)
class RekeyResult:
    rows_before: int
    rows_updated: int
    backup_path: Path | None = None

    def __iter__(self):
        yield self.rows_before
        yield self.rows_updated


def _physics_columns(key: dict[str, Any]) -> dict[str, Any]:
    """Keep the rekey index hook while delegating its fields to the owner."""

    try:
        derived_indexes = _derive_pt1_key_indexes(key)
    except (TypeError, ValueError, OverflowError, ZeroDivisionError, KeyError) as exc:
        raise PT1PersistentStoreCorrupt(
            "PT-1 stored request key cannot derive physics indexes"
        ) from exc
    return _PhysicsIndexColumns(derived_indexes)


class _PhysicsIndexColumns(dict[str, Any]):
    def __init__(self, indexes: Any) -> None:
        super().__init__(_pt1_physics_index_columns(indexes))
        self.derived_indexes = indexes


def _json_loads(raw: bytes) -> dict[str, Any]:
    loaded = json.loads(raw.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("cache key root must be a JSON object")
    return loaded


def _key_from_stored_row(
    row: sqlite3.Row,
    *,
    current_layout: bool = False,
) -> dict[str, Any]:
    values = dict(row)
    key_hash = str(values.get("key_hash", ""))
    if current_layout:
        admitted = _admit_stored_row(
            row,
            store_kind="pt1",
            validate_store_schema_version=True,
            validate_interpolation_domain=True,
        )
        if admitted is None:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 current-layout row was rejected during rekey: {key_hash}"
            )
        return dict(admitted.entry["key"])
    try:
        stored_key_bytes = values["key_bytes"]
        if not isinstance(stored_key_bytes, (bytes, bytearray, memoryview)):
            raise TypeError("stored key bytes are not a blob")
        raw_key_bytes = bytes(stored_key_bytes)
        key = _pt1_row_key(raw_key_bytes, key_hash=key_hash)
        if canonical_json_bytes(key) != raw_key_bytes:
            raise ValueError("stored key bytes are not canonical")
        computed_hash = _sha256(raw_key_bytes)
        if "key_hash" in values and values["key_hash"] != computed_hash:
            raise ValueError("stored key hash does not match key bytes")
        if "key_sha256" in values and values["key_sha256"] != computed_hash:
            raise ValueError("stored key_sha256 does not match key bytes")
        return dict(key)
    except PT1PersistentStoreCorrupt:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        UnicodeDecodeError,
        RecursionError,
    ) as exc:
        raise PT1PersistentStoreCorrupt(
            f"PT-1 cache row has invalid request key: {key_hash}"
        ) from exc


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


def _has_current_layout(columns: set[str]) -> bool:
    return (
        set(_PT1_SOURCE_REQUIRED_TEXT_FIELDS)
        | {"key_bytes", "payload_bytes", "git_dirty"}
    ) <= columns


def _count_rows_needing_rekey(
    conn: sqlite3.Connection,
    *,
    engine: str,
    target_corpus_version: str,
) -> int:
    count = 0
    table_columns = _table_columns(conn)
    current_layout = _has_current_layout(table_columns)
    selection = "*" if current_layout else "key_bytes"
    for row in conn.execute(
        f"SELECT {selection} FROM {PT1_EQUILIBRIUM_TABLE}"
    ):
        key = _key_from_stored_row(row, current_layout=current_layout)
        if not rekey_row_matches_engine(key, engine):
            continue
        if cache_row_needs_rekey(key, target_corpus_version):
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
    target = validated_rekey_corpus_version(target_corpus_version)

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
        current_layout = _has_current_layout(table_columns)
        physics_index_columns = {
            "physics_bucket_schema_version",
            "physics_bucket_sha256",
            "replay_scope_sha256",
            "physics_key_bytes",
            *(
                column
                for rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS
                for column in (
                    _physics_ladder_hash_column(rung_tag),
                    _physics_ladder_distance_column(rung_tag),
                )
            ),
        }
        physics_columns_present = physics_index_columns & table_columns
        if current_layout:
            selection = "*"
        else:
            selected_columns = ["key_hash", "key_bytes", "engine_version"]
            selected_columns.extend(
                column
                for column in (
                    "key_sha256",
                    "store_schema_version",
                    "artifact",
                )
                if column in table_columns
            )
            selected_columns.extend(sorted(physics_columns_present))
            selection = ", ".join(
                f'"{column}"' for column in selected_columns
            )
        for row in conn.execute(
            f"SELECT {selection} FROM {PT1_EQUILIBRIUM_TABLE}"
        ):
            row_values = dict(row)
            key = _key_from_stored_row(row, current_layout=current_layout)
            if not rekey_row_matches_engine(key, engine):
                continue
            if not cache_row_needs_rekey(key, target):
                continue
            provenance = rekey_engine_version_provenance(
                key, row["engine_version"]
            )
            if not replace_rekey_cache_identity(key, target):
                continue
            old_key_hash = str(row["key_hash"])
            row_columns = set(row.keys())
            artifact = row["artifact"] if "artifact" in row_columns else None
            legacy_layout = (
                row_values.get("store_schema_version")
                not in PT1_SUPPORTED_STORE_SCHEMA_VERSIONS
            )
            physics_columns = (
                _physics_columns(key) if physics_columns_present else {}
            )
            derived_indexes = getattr(physics_columns, "derived_indexes", None)
            candidate_row = dict(row_values)
            key_bytes = canonical_json_bytes(key)
            key_hash = _sha256(key_bytes)
            candidate_row.update(
                {
                    "key_hash": key_hash,
                    "key_bytes": sqlite3.Binary(key_bytes),
                    "corpus_version": target,
                    "engine_version": provenance,
                }
            )
            if "key_sha256" in table_columns:
                candidate_row["key_sha256"] = key_hash
            candidate_row.update(
                {
                    column: value
                    for column, value in physics_columns.items()
                    if column in table_columns
                }
            )
            try:
                if derived_indexes is not None:
                    _validate_pt1_key_indexes(
                        candidate_row,
                        artifact=artifact if isinstance(artifact, str) else None,
                        key=key,
                        key_hash=key_hash,
                        expected_replay_scope_sha256=(
                            derived_indexes.replay_scope_sha256
                            if "replay_scope_sha256" in table_columns
                            else None
                        ),
                        check_key_dependencies=not legacy_layout,
                        require_indexable=bool(physics_columns_present),
                        derived_indexes=derived_indexes,
                    )
            except UncacheableKey as exc:
                raise PT1PersistentStoreCorrupt(
                    "PT-1 stored request key is not indexable during rekey: "
                    f"{old_key_hash}"
                ) from exc
            except PT1PersistentStoreCorrupt:
                raise
            except (TypeError, ValueError, OverflowError, ZeroDivisionError, KeyError) as exc:
                raise PT1PersistentStoreCorrupt(
                    "PT-1 stored request key cannot derive indexes during rekey: "
                    f"{old_key_hash}"
                ) from exc
            update_columns = tuple(
                column
                for column in (
                    "key_bytes",
                    "key_sha256",
                    "key_hash",
                    "corpus_version",
                    "engine_version",
                    *sorted(physics_columns_present),
                )
                if column in candidate_row
            )
            assignments = [f'"{column}" = ?' for column in update_columns]
            values = [candidate_row[column] for column in update_columns]
            values.append(old_key_hash)
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
