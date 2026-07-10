#!/usr/bin/env python3
"""Incrementally harvest an expedited grind database into a local accumulator."""

from __future__ import annotations

import argparse
import hashlib
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
# Identity / provenance columns rewritten on insert (not copied as-is).
_REWRITE_OUTPUT_COLUMNS = frozenset(
    {"id", "grid_key_id", "source_host", "source_row_id"}
)
# Wall-clock / host provenance excluded from scientific equivalence and
# restore fingerprints so timing noise cannot mint false conflicts or block
# generation-change re-reconcile by canonical key. Still copied on insert.
_EPHEMERAL_OUTPUT_COLUMNS = frozenset(
    {"host", "created_at", "timing_s", "timing_s_repr"}
)
_TERMINAL_PULLED = "pulled"
_TERMINAL_EQUIVALENT = "equivalent"
_TERMINAL_CONFLICT = "conflict"


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


def _ensure_harvest_schema(connection: sqlite3.Connection) -> None:
    state_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='harvest_state'"
    ).fetchone()
    if state_table is not None:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(harvest_state)")
        }
        if "source_database" not in columns or "source_generation" not in columns:
            legacy = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='harvest_state_legacy_v1'"
            ).fetchone()
            if legacy is not None:
                raise RuntimeError("unsafe legacy harvest-state migration is incomplete")
            connection.execute(
                "ALTER TABLE harvest_state RENAME TO harvest_state_legacy_v1"
            )

    pulled_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='harvest_pulled_rows'"
    ).fetchone()
    if pulled_table is not None:
        pulled_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(harvest_pulled_rows)")
        }
        required = {
            "expedited_key",
            "engine_epoch",
            "row_fingerprint",
            "terminal_state",
        }
        if not required.issubset(pulled_columns):
            legacy_pulled = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='harvest_pulled_rows_legacy_v1'"
            ).fetchone()
            if legacy_pulled is not None:
                raise RuntimeError(
                    "unsafe legacy harvest-pulled-rows migration is incomplete"
                )
            # Drop blind id-only receipts: force re-reconcile under fingerprinted
            # receipts rather than trusting content-blind pulled state.
            connection.execute(
                "ALTER TABLE harvest_pulled_rows "
                "RENAME TO harvest_pulled_rows_legacy_v1"
            )

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS harvest_state (
            source_host TEXT NOT NULL,
            source_database TEXT NOT NULL,
            source_generation TEXT NOT NULL,
            source_table TEXT NOT NULL,
            last_seen_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(source_host, source_database, source_generation, source_table)
        );
        CREATE TABLE IF NOT EXISTS harvest_pulled_rows (
            source_host TEXT NOT NULL,
            source_database TEXT NOT NULL,
            source_generation TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_row_id INTEGER NOT NULL,
            expedited_key TEXT NOT NULL,
            engine_epoch INTEGER NOT NULL,
            row_fingerprint TEXT NOT NULL,
            terminal_state TEXT NOT NULL,
            pulled_at TEXT NOT NULL,
            PRIMARY KEY(
                source_host, source_database, source_generation,
                source_table, source_row_id
            )
        );
        CREATE TABLE IF NOT EXISTS harvest_conflicts (
            conflict_id INTEGER PRIMARY KEY,
            source_host TEXT NOT NULL,
            source_database TEXT NOT NULL,
            source_generation TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_row_id INTEGER NOT NULL,
            expedited_key TEXT NOT NULL,
            engine_epoch INTEGER NOT NULL,
            existing_output_id INTEGER NOT NULL,
            existing_row_json TEXT NOT NULL,
            incoming_row_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 1,
            UNIQUE(
                source_host, source_database, source_generation,
                source_table, source_row_id
            )
        );
        """
    )


def _row_json(row: sqlite3.Row | dict[str, Any]) -> str:
    if isinstance(row, sqlite3.Row):
        payload = dict(row)
    else:
        payload = dict(row)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _copyable_output_columns(columns: Sequence[str]) -> tuple[str, ...]:
    """Source columns copied into the accumulator (before id/provenance rewrite)."""
    return tuple(
        column for column in columns if column not in _REWRITE_OUTPUT_COLUMNS
    )


def _scientific_payload_columns(columns: Sequence[str]) -> tuple[str, ...]:
    """Columns that define scientific identity for equivalence / fingerprinting."""
    return tuple(
        column
        for column in columns
        if column not in _REWRITE_OUTPUT_COLUMNS
        and column not in _EPHEMERAL_OUTPUT_COLUMNS
    )


def _row_fingerprint(
    row: sqlite3.Row | dict[str, Any],
    payload_columns: Sequence[str],
) -> str:
    """Stable content identity for a source output row.

    Used to detect restored/recreated databases that preserve generation
    metadata and reuse source_row_id values with different payloads.
    """
    if isinstance(row, sqlite3.Row):
        values = {column: row[column] for column in payload_columns}
        values["expedited_key"] = str(row["expedited_key"])
        values["engine_epoch"] = int(row["engine_epoch"])
    else:
        values = {column: row[column] for column in payload_columns}
        values["expedited_key"] = str(row["expedited_key"])
        values["engine_epoch"] = int(row["engine_epoch"])
    encoded = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _record_conflict(
    connection: sqlite3.Connection,
    *,
    source_host: str,
    source_database: str,
    source_generation: str,
    source_table: str,
    incoming: sqlite3.Row,
    existing: sqlite3.Row,
) -> None:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO harvest_conflicts(
            source_host, source_database, source_generation,
            source_table, source_row_id,
            expedited_key, engine_epoch, existing_output_id,
            existing_row_json, incoming_row_json,
            first_seen_at, last_seen_at, occurrences
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(
            source_host, source_database, source_generation,
            source_table, source_row_id
        )
        DO UPDATE SET
            expedited_key=excluded.expedited_key,
            engine_epoch=excluded.engine_epoch,
            existing_output_id=excluded.existing_output_id,
            existing_row_json=excluded.existing_row_json,
            incoming_row_json=excluded.incoming_row_json,
            last_seen_at=excluded.last_seen_at,
            occurrences=harvest_conflicts.occurrences + 1
        """,
        (
            source_host,
            source_database,
            source_generation,
            source_table,
            int(incoming["id"]),
            str(incoming["expedited_key"]),
            int(incoming["engine_epoch"]),
            int(existing["id"]),
            _row_json(existing),
            _row_json(incoming),
            now,
            now,
        ),
    )


def _mark_consumed(
    connection: sqlite3.Connection,
    *,
    source_host: str,
    source_database: str,
    source_generation: str,
    source_table: str,
    source_row_id: int,
    expedited_key: str,
    engine_epoch: int,
    row_fingerprint: str,
    terminal_state: str,
) -> None:
    connection.execute(
        """
        INSERT INTO harvest_pulled_rows(
            source_host, source_database, source_generation,
            source_table, source_row_id,
            expedited_key, engine_epoch, row_fingerprint,
            terminal_state, pulled_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(
            source_host, source_database, source_generation,
            source_table, source_row_id
        )
        DO UPDATE SET
            expedited_key=excluded.expedited_key,
            engine_epoch=excluded.engine_epoch,
            row_fingerprint=excluded.row_fingerprint,
            terminal_state=excluded.terminal_state,
            pulled_at=excluded.pulled_at
        """,
        (
            source_host,
            source_database,
            source_generation,
            source_table,
            source_row_id,
            expedited_key,
            engine_epoch,
            row_fingerprint,
            terminal_state,
            utc_now(),
        ),
    )


def harvest_snapshot(
    source_path: str | Path,
    accumulator_path: str | Path,
    *,
    source_host: str,
    source_database: str | None = None,
    table: str = ALLOWED_TABLE,
    limit: int | None = None,
) -> dict[str, int]:
    if table != ALLOWED_TABLE:
        raise ValueError(f"unsupported harvest table: {table}")
    resolved_source = Path(source_path).resolve()
    source_identity = str(source_database or resolved_source)
    source_uri = f"file:{resolved_source}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    source.row_factory = sqlite3.Row
    try:
        variant = _metadata(source, "schema_variant")
        if variant != SCHEMA_VARIANT:
            raise ValueError(
                f"source schema variant mismatch: {variant!r} != {SCHEMA_VARIANT!r}"
            )
        source_generation = _metadata(source, "database_id")
        if source_generation is None:
            created_at = _metadata(source, "created_at")
            if created_at is None:
                raise ValueError("source database identity metadata is missing")
            source_generation = f"created-at:{created_at}"
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
            _ensure_harvest_schema(target)
            state = target.execute(
                "SELECT last_seen_id FROM harvest_state "
                "WHERE source_host = ? AND source_database = ? "
                "AND source_generation = ? AND source_table = ?",
                (source_host, source_identity, source_generation, table),
            ).fetchone()
            last_seen = int(state[0]) if state is not None else 0
            total_source = int(
                source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            source_output_columns = table_columns(source, table)
            copyable_columns = _copyable_output_columns(source_output_columns)
            payload_columns = _scientific_payload_columns(source_output_columns)

            # Receipts are bound to source generation. Content-blind id skips
            # are refused: a receipt is only trusted when its stored fingerprint
            # still matches the live source row (restore/id-reuse detection).
            receipts: dict[int, sqlite3.Row] = {
                int(row["source_row_id"]): row
                for row in target.execute(
                    "SELECT source_row_id, expedited_key, engine_epoch, "
                    "row_fingerprint, terminal_state FROM harvest_pulled_rows "
                    "WHERE source_host = ? AND source_database = ? "
                    "AND source_generation = ? AND source_table = ?",
                    (source_host, source_identity, source_generation, table),
                )
            }

            pending_ids: list[int] = []
            for (source_id,) in source.execute(
                f'SELECT id FROM "{table}" ORDER BY id'
            ):
                source_id = int(source_id)
                receipt = receipts.get(source_id)
                if receipt is None:
                    pending_ids.append(source_id)
                    continue
                live = source.execute(
                    f'SELECT * FROM "{table}" WHERE id = ?', (source_id,)
                ).fetchone()
                if live is None:
                    continue
                live_fp = _row_fingerprint(live, payload_columns)
                if live_fp != str(receipt["row_fingerprint"]):
                    # Same generation + reused id with different content: the
                    # source was restored/rewritten. Re-reconcile by canonical
                    # key / payload, not by source_row_id alone.
                    pending_ids.append(source_id)
            if limit is not None:
                pending_ids = pending_ids[: int(limit)]
            rows = [
                source.execute(
                    f'SELECT * FROM "{table}" WHERE id = ?', (source_id,)
                ).fetchone()
                for source_id in pending_ids
            ]

            source_input_columns = table_columns(source, "grid_keys")
            target_input_columns = set(table_columns(target, "grid_keys"))
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
            equivalent = 0
            max_seen = last_seen
            for output_row in rows:
                if output_row is None:
                    raise RuntimeError("source output disappeared during harvest")
                source_id = int(output_row["id"])
                fingerprint = _row_fingerprint(output_row, payload_columns)
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
                # Canonical key is identity. Raw source ids are preferred only
                # when free; after a restore (fingerprint mismatch) an old id
                # may carry a different key — never abort before key reconcile.
                local_input = target.execute(
                    "SELECT id, canonical_vector FROM grid_keys "
                    "WHERE expedited_key = ?",
                    (input_row["expedited_key"],),
                ).fetchone()
                if local_input is None:
                    input_values = {
                        column: input_row[column]
                        for column in source_input_columns
                    }
                    preferred_grid_id = int(input_row["id"])
                    grid_id_holder = target.execute(
                        "SELECT expedited_key FROM grid_keys WHERE id = ?",
                        (preferred_grid_id,),
                    ).fetchone()
                    rank_holder = target.execute(
                        "SELECT expedited_key FROM grid_keys "
                        "WHERE batch_id = ? AND shuffle_rank = ?",
                        (
                            int(input_row["batch_id"]),
                            int(input_row["shuffle_rank"]),
                        ),
                    ).fetchone()
                    # Restore can reuse raw id and/or (batch, shuffle_rank) under
                    # a new key. Prefer source placement only when free; otherwise
                    # remap so the new key still lands (id identity is void).
                    id_taken = grid_id_holder is not None
                    rank_taken = (
                        rank_holder is not None
                        and str(rank_holder["expedited_key"])
                        != str(input_row["expedited_key"])
                    )
                    if id_taken or rank_taken:
                        if id_taken:
                            input_values = {
                                key: value
                                for key, value in input_values.items()
                                if key != "id"
                            }
                        if rank_taken:
                            free_rank = int(
                                target.execute(
                                    "SELECT COALESCE(MAX(shuffle_rank), -1) + 1 "
                                    "FROM grid_keys WHERE batch_id = ?",
                                    (int(input_row["batch_id"]),),
                                ).fetchone()[0]
                            )
                            input_values["shuffle_rank"] = free_rank
                        _insert(target, "grid_keys", input_values)
                    else:
                        _insert_or_ignore(target, "grid_keys", input_values)
                    local_input = target.execute(
                        "SELECT id, canonical_vector FROM grid_keys "
                        "WHERE expedited_key = ?",
                        (input_row["expedited_key"],),
                    ).fetchone()
                if (
                    local_input is None
                    or local_input["canonical_vector"]
                    != input_row["canonical_vector"]
                ):
                    raise RuntimeError(
                        f"expedited-key collision while harvesting "
                        f"{input_row['expedited_key']}"
                    )

                existing_output = target.execute(
                    f'SELECT * FROM "{table}" '
                    "WHERE expedited_key = ? AND engine_epoch = ?",
                    (
                        output_row["expedited_key"],
                        int(output_row["engine_epoch"]),
                    ),
                ).fetchone()
                if existing_output is not None:
                    same_payload = all(
                        existing_output[column] == output_row[column]
                        for column in payload_columns
                    )
                    if same_payload:
                        equivalent += 1
                        _mark_consumed(
                            target,
                            source_host=source_host,
                            source_database=source_identity,
                            source_generation=source_generation,
                            source_table=table,
                            source_row_id=source_id,
                            expedited_key=str(output_row["expedited_key"]),
                            engine_epoch=int(output_row["engine_epoch"]),
                            row_fingerprint=fingerprint,
                            terminal_state=_TERMINAL_EQUIVALENT,
                        )
                        max_seen = max(max_seen, source_id)
                        continue
                    _record_conflict(
                        target,
                        source_host=source_host,
                        source_database=source_identity,
                        source_generation=source_generation,
                        source_table=table,
                        incoming=output_row,
                        existing=existing_output,
                    )
                    conflicts += 1
                    # Settled conflict: advance the --limit window so later
                    # source rows are not permanently starved, while the
                    # conflicts table remains the loud terminal record.
                    _mark_consumed(
                        target,
                        source_host=source_host,
                        source_database=source_identity,
                        source_generation=source_generation,
                        source_table=table,
                        source_row_id=source_id,
                        expedited_key=str(output_row["expedited_key"]),
                        engine_epoch=int(output_row["engine_epoch"]),
                        row_fingerprint=fingerprint,
                        terminal_state=_TERMINAL_CONFLICT,
                    )
                    max_seen = max(max_seen, source_id)
                    continue

                # New canonical key: pull as a new row. If the preferred source
                # id is already held by a different key, remap the local id and
                # audit the reuse — do not abort or discard the new science.
                output_values = {
                    column: output_row[column] for column in copyable_columns
                }
                output_values["grid_key_id"] = int(local_input["id"])
                output_values["source_host"] = source_host
                output_values["source_row_id"] = source_id
                existing_by_id = target.execute(
                    f'SELECT * FROM "{table}" WHERE id = ?',
                    (source_id,),
                ).fetchone()
                if existing_by_id is not None:
                    # Audit: same raw id, different key (restore / id reuse).
                    # Distinct from payload conflicts (same key, different body):
                    # both rows are retained; only the local PK is remapped.
                    _record_conflict(
                        target,
                        source_host=source_host,
                        source_database=source_identity,
                        source_generation=source_generation,
                        source_table=table,
                        incoming=output_row,
                        existing=existing_by_id,
                    )
                    # Leave output_values["id"] unset → AUTOINCREMENT remaps.
                else:
                    output_values["id"] = source_id
                _insert(target, table, output_values)
                inserted += 1
                _mark_consumed(
                    target,
                    source_host=source_host,
                    source_database=source_identity,
                    source_generation=source_generation,
                    source_table=table,
                    source_row_id=source_id,
                    expedited_key=str(output_row["expedited_key"]),
                    engine_epoch=int(output_row["engine_epoch"]),
                    row_fingerprint=fingerprint,
                    terminal_state=_TERMINAL_PULLED,
                )
                max_seen = max(max_seen, source_id)

            target.execute(
                "INSERT INTO harvest_state("
                "source_host, source_database, source_generation, source_table, "
                "last_seen_id, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT("
                "source_host, source_database, source_generation, source_table"
                ") DO UPDATE SET "
                "last_seen_id=excluded.last_seen_id, updated_at=excluded.updated_at",
                (
                    source_host,
                    source_identity,
                    source_generation,
                    table,
                    max_seen,
                    utc_now(),
                ),
            )
            target.commit()
            accumulator_total = int(
                target.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            # Conflicts are a distinct terminal state: never inflate `pulled`.
            pulled = inserted + equivalent
            return {
                "last_seen_before": last_seen,
                "last_seen_after": max_seen,
                "attempted": len(rows),
                "pulled": pulled,
                "inserted": inserted,
                "equivalent": equivalent,
                "canonical_conflicts_recorded": conflicts,
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


def _insert(
    connection: sqlite3.Connection,
    table: str,
    values: dict[str, Any],
) -> sqlite3.Cursor:
    columns = tuple(values)
    quoted = ",".join(f'"{column}"' for column in columns)
    placeholders = ",".join("?" for _ in columns)
    return connection.execute(
        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
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
            source_database=str(args.source_db.resolve()),
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
                source_database=args.remote_db,
                table=args.table,
                limit=args.limit,
            )
    print(json.dumps(summary, sort_keys=True))
    # Unresolved conflicts this run are a distinct terminal failure for cron.
    if int(summary.get("canonical_conflicts_recorded", 0)) > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
