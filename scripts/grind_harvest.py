#!/usr/bin/env python3
"""Incrementally harvest an expedited grind database into a local accumulator."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.grid_pregrind_writer import (  # noqa: E402
    GridCacheWriter,
    SCHEMA_VARIANT,
    table_columns,
    utc_now,
)


DEFAULT_ACCUMULATOR = (
    REPO_ROOT / "docs-private/recipe-db/grind-alphamelts-accumulator.db"
)
ALLOWED_TABLE = "alphamelts_outputs"


def _validate_host(host: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", host):
        raise ValueError(f"unsafe SSH host: {host!r}")
    return host


def remote_snapshot(host: str, remote_db: str, destination: Path) -> None:
    host = _validate_host(host)
    remote_copy = f"/tmp/grid-pregrind-harvest-{uuid.uuid4().hex}.db"
    backup_command = shlex.join(
        ["sqlite3", remote_db, ".timeout 30000", f".backup {remote_copy}"]
    )
    cleanup_command = shlex.join(["rm", "-f", "--", remote_copy])
    try:
        subprocess.run(["ssh", host, backup_command], check=True)
        subprocess.run(
            ["scp", "-q", f"{host}:{remote_copy}", str(destination)],
            check=True,
        )
    finally:
        subprocess.run(
            ["ssh", host, cleanup_command],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _metadata(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?", (key,)
    ).fetchone()
    return None if row is None else str(row[0])


def harvest_snapshot(
    source_path: str | Path,
    accumulator_path: str | Path,
    *,
    source_host: str,
    table: str = ALLOWED_TABLE,
    limit: int | None = None,
) -> dict[str, int]:
    if table != ALLOWED_TABLE:
        raise ValueError(f"unsupported harvest table: {table}")
    source_uri = f"file:{Path(source_path).resolve()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    source.row_factory = sqlite3.Row
    try:
        variant = _metadata(source, "schema_variant")
        if variant != SCHEMA_VARIANT:
            raise ValueError(
                f"source schema variant mismatch: {variant!r} != {SCHEMA_VARIANT!r}"
            )
        with GridCacheWriter(accumulator_path) as accumulator:
            target = accumulator.connection
            for registry_row in source.execute(
                "SELECT block_base, source_label FROM id_block_registry"
            ):
                _insert_or_ignore(
                    target,
                    "id_block_registry",
                    {
                        "block_base": int(registry_row["block_base"]),
                        "source_label": str(registry_row["source_label"]),
                    },
                )
                local_registry = target.execute(
                    "SELECT source_label FROM id_block_registry WHERE block_base = ?",
                    (int(registry_row["block_base"]),),
                ).fetchone()
                if local_registry["source_label"] != registry_row["source_label"]:
                    raise RuntimeError(
                        f"id-block registry collision at {registry_row['block_base']}"
                    )
            target.execute(
                """
                CREATE TABLE IF NOT EXISTS harvest_state (
                    source_host TEXT NOT NULL,
                    source_table TEXT NOT NULL,
                    last_seen_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(source_host, source_table)
                )
                """
            )
            state = target.execute(
                "SELECT last_seen_id FROM harvest_state "
                "WHERE source_host = ? AND source_table = ?",
                (source_host, table),
            ).fetchone()
            last_seen = int(state[0]) if state is not None else 0
            total_source = int(
                source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            query = f'SELECT * FROM "{table}" WHERE id > ? ORDER BY id'
            parameters: tuple[Any, ...] = (last_seen,)
            if limit is not None:
                query += " LIMIT ?"
                parameters = (last_seen, int(limit))
            rows = list(source.execute(query, parameters))

            source_input_columns = table_columns(source, "grid_keys")
            target_input_columns = set(table_columns(target, "grid_keys"))
            source_output_columns = table_columns(source, table)
            target_output_columns = set(table_columns(target, table))
            missing_input = set(source_input_columns) - target_input_columns
            missing_output = set(source_output_columns) - target_output_columns
            if missing_input or missing_output:
                raise ValueError(
                    "accumulator schema would lose source columns: "
                    f"input={sorted(missing_input)}, output={sorted(missing_output)}"
                )

            inserted = 0
            conflicts = 0
            max_seen = last_seen
            for output_row in rows:
                source_id = int(output_row["id"])
                input_row = source.execute(
                    "SELECT * FROM grid_keys WHERE id = ?",
                    (int(output_row["grid_key_id"]),),
                ).fetchone()
                if input_row is None:
                    raise RuntimeError(
                        f"source output {source_id} references missing input row"
                    )
                batch_row = source.execute(
                    "SELECT * FROM batches WHERE batch_id = ?",
                    (int(input_row["batch_id"]),),
                ).fetchone()
                if batch_row is None:
                    raise RuntimeError(
                        f"source grid key {input_row['id']} references missing batch"
                    )
                batch_values = dict(batch_row)
                _insert_or_ignore(target, "batches", batch_values)
                local_batch = target.execute(
                    "SELECT label, kind, seed, params_json FROM batches "
                    "WHERE batch_id = ?",
                    (int(batch_row["batch_id"]),),
                ).fetchone()
                if local_batch is None or tuple(local_batch) != (
                    batch_row["label"],
                    batch_row["kind"],
                    batch_row["seed"],
                    batch_row["params_json"],
                ):
                    raise RuntimeError(
                        f"batch-id collision while harvesting {batch_row['batch_id']}"
                    )
                input_values = {
                    column: input_row[column] for column in source_input_columns
                }
                existing_grid_id = target.execute(
                    "SELECT expedited_key FROM grid_keys WHERE id = ?",
                    (int(input_row["id"]),),
                ).fetchone()
                if (
                    existing_grid_id is not None
                    and existing_grid_id["expedited_key"] != input_row["expedited_key"]
                ):
                    raise RuntimeError(
                        f"raw grid-key id collision at {input_row['id']}"
                    )
                _insert_or_ignore(target, "grid_keys", input_values)
                local_input = target.execute(
                    "SELECT id, canonical_vector FROM grid_keys "
                    "WHERE expedited_key = ?",
                    (input_row["expedited_key"],),
                ).fetchone()
                if (
                    local_input is None
                    or local_input["canonical_vector"] != input_row["canonical_vector"]
                ):
                    raise RuntimeError(
                        f"expedited-key collision while harvesting {input_row['expedited_key']}"
                    )
                output_values = {
                    column: output_row[column]
                    for column in source_output_columns
                    if column not in {"id", "grid_key_id"}
                }
                existing_output_id = target.execute(
                    "SELECT expedited_key FROM alphamelts_outputs WHERE id = ?",
                    (source_id,),
                ).fetchone()
                if (
                    existing_output_id is not None
                    and existing_output_id["expedited_key"]
                    != output_row["expedited_key"]
                ):
                    raise RuntimeError(f"raw output id collision at {source_id}")
                output_values["id"] = source_id
                output_values["grid_key_id"] = int(local_input["id"])
                output_values["source_host"] = source_host
                output_values["source_row_id"] = source_id
                cursor = _insert_or_ignore(target, table, output_values)
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    conflicts += 1
                max_seen = max(max_seen, source_id)

            target.execute(
                "INSERT INTO harvest_state(source_host, source_table, "
                "last_seen_id, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(source_host, source_table) DO UPDATE SET "
                "last_seen_id=excluded.last_seen_id, updated_at=excluded.updated_at",
                (source_host, table, max_seen, utc_now()),
            )
            target.commit()
            accumulator_total = int(
                target.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            return {
                "last_seen_before": last_seen,
                "last_seen_after": max_seen,
                "pulled": len(rows),
                "inserted": inserted,
                "canonical_conflicts_skipped": conflicts,
                "source_total": total_source,
                "accumulator_total": accumulator_total,
            }
    finally:
        source.close()


def _insert_or_ignore(
    connection: sqlite3.Connection,
    table: str,
    values: dict[str, Any],
) -> sqlite3.Cursor:
    columns = tuple(values)
    quoted = ",".join(f'"{column}"' for column in columns)
    placeholders = ",".join("?" for _ in columns)
    return connection.execute(
        f'INSERT OR IGNORE INTO "{table}" ({quoted}) VALUES ({placeholders})',
        tuple(values[column] for column in columns),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-db", type=Path)
    source.add_argument("--host")
    result.add_argument("--remote-db")
    result.add_argument("--accumulator", type=Path, default=DEFAULT_ACCUMULATOR)
    result.add_argument("--table", default=ALLOWED_TABLE)
    result.add_argument("--limit", type=int)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be >= 1")
    if args.host and not args.remote_db:
        raise SystemExit("--remote-db is required with --host")
    source_host = args.host or "local"
    if args.source_db is not None:
        summary = harvest_snapshot(
            args.source_db,
            args.accumulator,
            source_host=source_host,
            table=args.table,
            limit=args.limit,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="grid-pregrind-harvest-") as temp:
            snapshot = Path(temp) / "snapshot.db"
            remote_snapshot(args.host, args.remote_db, snapshot)
            summary = harvest_snapshot(
                snapshot,
                args.accumulator,
                source_host=source_host,
                table=args.table,
                limit=args.limit,
            )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
