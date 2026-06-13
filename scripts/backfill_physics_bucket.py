#!/usr/bin/env python3
"""Backfill PT-1 physics-bucket cache columns from stored exact replay keys."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.reduced_real_determinism import (  # noqa: E402
    PHYSICS_BUCKET_SCHEMA_VERSION,
    PHYSICS_BUCKET_LADDER_RUNGS,
    PT1_EQUILIBRIUM_TABLE,
    canonical_physics_ladder_bucket_key_from_replay_key,
    canonical_json_bytes,
    canonical_physics_bucket_key_from_replay_key,
    physics_ladder_bucket_distance_from_replay_key,
)


DEFAULT_DB = REPO_ROOT / "docs-private" / "recipe-db" / "reduced-real.db"
PHYSICS_COLUMNS = {
    "physics_bucket_schema_version": "TEXT",
    "physics_bucket_sha256": "TEXT",
    "replay_scope_sha256": "TEXT",
    "physics_key_bytes": "BLOB",
    "physics_bucket_h40_sha256": "TEXT",
    "physics_bucket_h40_distance": "REAL",
    "physics_bucket_h30_sha256": "TEXT",
    "physics_bucket_h30_distance": "REAL",
}


@dataclass(frozen=True)
class PhysicsBucketValues:
    schema_version: str
    physics_bucket_sha256: str
    replay_scope_sha256: str
    physics_key_bytes: bytes
    physics_bucket_h40_sha256: str
    physics_bucket_h40_distance: float
    physics_bucket_h30_sha256: str
    physics_bucket_h30_distance: float


@dataclass
class BackfillStats:
    db: str
    dry_run: bool
    total_rows: int = 0
    distinct_physics_bucket_sha256: int = 0
    distinct_physics_bucket_h40_sha256: int = 0
    distinct_physics_bucket_h30_sha256: int = 0
    rows_already_backfilled: int = 0
    rows_needing_backfill: int = 0
    rows_updated: int = 0
    conflicts: int = 0
    invalid_rows: int = 0
    missing_columns_before: tuple[str, ...] = ()
    batch_size: int = 0

    @property
    def reuse_fraction(self) -> float:
        if self.total_rows == 0:
            return 0.0
        return (self.total_rows - self.distinct_physics_bucket_sha256) / self.total_rows

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["collapse"] = (
            f"{self.total_rows}->{self.distinct_physics_bucket_sha256}"
        )
        data["reuse_fraction"] = self.reuse_fraction
        return data


def run_backfill(
    db_path: Path,
    *,
    dry_run: bool,
    batch_size: int = 1000,
) -> BackfillStats:
    db_path = Path(db_path)
    stats = BackfillStats(
        db=str(db_path),
        dry_run=dry_run,
        batch_size=batch_size,
    )
    with _connect(db_path, readonly=dry_run) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn):
            raise RuntimeError(f"missing table: {PT1_EQUILIBRIUM_TABLE}")
        columns = _table_columns(conn)
        stats.missing_columns_before = tuple(
            column for column in PHYSICS_COLUMNS if column not in columns
        )
        if not dry_run:
            _ensure_backfill_columns(conn, columns)
            columns = _table_columns(conn)

        distinct_physics_hashes: set[str] = set()
        distinct_ladder_hashes: dict[str, set[str]] = {
            rung_tag: set()
            for rung_tag, _sig_figs in PHYSICS_BUCKET_LADDER_RUNGS
        }
        cursor = conn.execute(_select_rows_sql(columns))
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            updates: list[tuple[Any, ...]] = []
            for row in rows:
                stats.total_rows += 1
                try:
                    values = _physics_values_from_key_bytes(row["key_bytes"])
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
                    stats.invalid_rows += 1
                    raise RuntimeError(
                        f"invalid replay key bytes for row {row['key_hash']}"
                    ) from None
                distinct_physics_hashes.add(values.physics_bucket_sha256)
                distinct_ladder_hashes["h40"].add(values.physics_bucket_h40_sha256)
                distinct_ladder_hashes["h30"].add(values.physics_bucket_h30_sha256)
                state = _backfill_state(row, values)
                if state == "conflict":
                    stats.conflicts += 1
                    raise RuntimeError(
                        "existing physics-bucket columns conflict for row "
                        f"{row['key_hash']}"
                    )
                if state == "complete":
                    stats.rows_already_backfilled += 1
                    continue
                stats.rows_needing_backfill += 1
                if not dry_run:
                    updates.append(
                        (
                            values.schema_version,
                            values.physics_bucket_sha256,
                            values.replay_scope_sha256,
                            sqlite3.Binary(values.physics_key_bytes),
                            values.physics_bucket_h40_sha256,
                            values.physics_bucket_h40_distance,
                            values.physics_bucket_h30_sha256,
                            values.physics_bucket_h30_distance,
                            row["key_hash"],
                        )
                    )
            if updates:
                update_cursor = conn.executemany(
                    f"""
                    UPDATE {PT1_EQUILIBRIUM_TABLE}
                    SET physics_bucket_schema_version = ?,
                        physics_bucket_sha256 = ?,
                        replay_scope_sha256 = ?,
                        physics_key_bytes = ?,
                        physics_bucket_h40_sha256 = ?,
                        physics_bucket_h40_distance = ?,
                        physics_bucket_h30_sha256 = ?,
                        physics_bucket_h30_distance = ?
                    WHERE key_hash = ?
                      AND (
                          physics_bucket_schema_version IS NULL
                          OR physics_bucket_sha256 IS NULL
                          OR replay_scope_sha256 IS NULL
                          OR physics_key_bytes IS NULL
                          OR physics_bucket_h40_sha256 IS NULL
                          OR physics_bucket_h40_distance IS NULL
                          OR physics_bucket_h30_sha256 IS NULL
                          OR physics_bucket_h30_distance IS NULL
                      )
                    """,
                    updates,
                )
                stats.rows_updated += int(update_cursor.rowcount)
        stats.distinct_physics_bucket_sha256 = len(distinct_physics_hashes)
        stats.distinct_physics_bucket_h40_sha256 = len(
            distinct_ladder_hashes["h40"]
        )
        stats.distinct_physics_bucket_h30_sha256 = len(
            distinct_ladder_hashes["h30"]
        )
    return stats


def _connect(db_path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{quote(str(db_path.resolve()))}?mode=ro"
        return sqlite3.connect(uri, uri=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (PT1_EQUILIBRIUM_TABLE,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
    }


def _ensure_backfill_columns(conn: sqlite3.Connection, columns: set[str]) -> None:
    for name, column_type in PHYSICS_COLUMNS.items():
        if name not in columns:
            conn.execute(
                f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} ADD COLUMN {name} {column_type}"
            )
    conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{PT1_EQUILIBRIUM_TABLE}_physics
        ON {PT1_EQUILIBRIUM_TABLE}(
            artifact,
            physics_bucket_schema_version,
            physics_bucket_sha256,
            replay_scope_sha256
        )
        """
    )
    for rung_tag, _sig_figs in PHYSICS_BUCKET_LADDER_RUNGS:
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{PT1_EQUILIBRIUM_TABLE}_{rung_tag}
            ON {PT1_EQUILIBRIUM_TABLE}(
                artifact,
                replay_scope_sha256,
                physics_bucket_{rung_tag}_sha256,
                physics_bucket_{rung_tag}_distance,
                key_hash
            )
            """
        )


def _select_rows_sql(columns: set[str]) -> str:
    optional = [
        column if column in columns else f"NULL AS {column}"
        for column in PHYSICS_COLUMNS
    ]
    return f"""
        SELECT
            key_hash,
            key_bytes,
            {", ".join(optional)}
        FROM {PT1_EQUILIBRIUM_TABLE}
        ORDER BY key_hash
    """


def _physics_values_from_key_bytes(key_bytes: Any) -> PhysicsBucketValues:
    key = json.loads(_blob_bytes(key_bytes).decode("utf-8"))
    if not isinstance(key, Mapping):
        raise ValueError("replay key must decode to a JSON object")
    physics_key = canonical_physics_bucket_key_from_replay_key(key)
    physics_key_bytes = canonical_json_bytes(physics_key)
    replay_scope = physics_key.get("replay_scope", {})
    ladder_values = {}
    for rung_tag, _sig_figs in PHYSICS_BUCKET_LADDER_RUNGS:
        rung_key = canonical_physics_ladder_bucket_key_from_replay_key(
            key,
            rung_tag,
        )
        ladder_values[rung_tag] = {
            "sha256": _sha256(canonical_json_bytes(rung_key)),
            "distance": physics_ladder_bucket_distance_from_replay_key(
                key,
                rung_tag,
            ),
        }
    return PhysicsBucketValues(
        schema_version=str(
            physics_key.get("schema_version", PHYSICS_BUCKET_SCHEMA_VERSION)
        ),
        physics_bucket_sha256=_sha256(physics_key_bytes),
        replay_scope_sha256=_sha256(canonical_json_bytes(replay_scope)),
        physics_key_bytes=physics_key_bytes,
        physics_bucket_h40_sha256=str(ladder_values["h40"]["sha256"]),
        physics_bucket_h40_distance=float(ladder_values["h40"]["distance"]),
        physics_bucket_h30_sha256=str(ladder_values["h30"]["sha256"]),
        physics_bucket_h30_distance=float(ladder_values["h30"]["distance"]),
    )


def _backfill_state(row: sqlite3.Row, values: PhysicsBucketValues) -> str:
    actual = {
        "physics_bucket_schema_version": row["physics_bucket_schema_version"],
        "physics_bucket_sha256": row["physics_bucket_sha256"],
        "replay_scope_sha256": row["replay_scope_sha256"],
        "physics_key_bytes": row["physics_key_bytes"],
        "physics_bucket_h40_sha256": row["physics_bucket_h40_sha256"],
        "physics_bucket_h40_distance": row["physics_bucket_h40_distance"],
        "physics_bucket_h30_sha256": row["physics_bucket_h30_sha256"],
        "physics_bucket_h30_distance": row["physics_bucket_h30_distance"],
    }
    expected = {
        "physics_bucket_schema_version": values.schema_version,
        "physics_bucket_sha256": values.physics_bucket_sha256,
        "replay_scope_sha256": values.replay_scope_sha256,
        "physics_key_bytes": values.physics_key_bytes,
        "physics_bucket_h40_sha256": values.physics_bucket_h40_sha256,
        "physics_bucket_h40_distance": values.physics_bucket_h40_distance,
        "physics_bucket_h30_sha256": values.physics_bucket_h30_sha256,
        "physics_bucket_h30_distance": values.physics_bucket_h30_distance,
    }
    complete = True
    for name, expected_value in expected.items():
        actual_value = actual[name]
        if actual_value is None:
            complete = False
            continue
        if name == "physics_key_bytes":
            actual_value = _blob_bytes(actual_value)
        if name.endswith("_distance"):
            actual_value = float(actual_value)
        if actual_value != expected_value:
            return "conflict"
    return "complete" if complete else "missing"


def _blob_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _print_stats(stats: BackfillStats, *, json_output: bool) -> None:
    data = stats.to_dict()
    if json_output:
        print(json.dumps(data, sort_keys=True))
        return
    print(f"db={data['db']}")
    print(f"mode={'dry-run' if data['dry_run'] else 'backfill'}")
    print(f"rows_total={data['total_rows']}")
    print(f"distinct_physics_bucket_sha256={data['distinct_physics_bucket_sha256']}")
    print(
        "distinct_physics_bucket_h40_sha256="
        f"{data['distinct_physics_bucket_h40_sha256']}"
    )
    print(
        "distinct_physics_bucket_h30_sha256="
        f"{data['distinct_physics_bucket_h30_sha256']}"
    )
    print(f"collapse={data['collapse']}")
    print(f"reuse_fraction={data['reuse_fraction']:.6f}")
    print(f"rows_already_backfilled={data['rows_already_backfilled']}")
    print(f"rows_needing_backfill={data['rows_needing_backfill']}")
    print(f"rows_updated={data['rows_updated']}")
    print(
        "missing_columns_before="
        + ",".join(data["missing_columns_before"])
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be positive")
    stats = run_backfill(
        args.db,
        dry_run=bool(args.dry_run),
        batch_size=int(args.batch_size),
    )
    _print_stats(stats, json_output=bool(args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
