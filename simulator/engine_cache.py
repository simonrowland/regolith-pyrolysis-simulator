"""One process-local HOT/COLD SQLite cache for engine outputs."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import socket
import sqlite3
import threading
import time
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from simulator.canonical import canonical_json_dumps

ENGINE_CHEMISTRY_VERSION = {
    "openimcc": 1,
    "internal-analytical": 1,
    "vaporock": 1,
    "magemin": 1,
    "alphamelts": 1,
    "thermoengine": 1,
}

_TABLE = "engine_outputs"
_COLD_TABLE = "engine_outputs_cold"
_TIMEOUT_MS = 60_000
_LOCK = threading.RLock()
_CONNECTIONS: dict[tuple[str, bool, bool], sqlite3.Connection] = {}
_CONNECTION_PID: int | None = None
_CONNECTION_ROOT: Path | None = None
_CONNECTION_READONLY = False
_COUNTERS: dict[str, dict[str, int]] = {}


@dataclass(frozen=True)
class CacheValue:
    """Engine output split into the consumed HOT fields and unused COLD fields."""

    hot: Any
    cold: Any = None


def engine_cache_key(engine_id: str, chem_version: str | int, request: Any) -> bytes:
    if engine_id not in ENGINE_CHEMISTRY_VERSION:
        raise ValueError(f"unknown engine {engine_id!r}")
    version: str | int = chem_version
    if isinstance(version, str) and version.isdecimal():
        version = int(version)
    try:
        payload = canonical_json_dumps([engine_id, version, request]).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("engine request must be finite, canonical JSON data") from exc
    return hashlib.sha256(payload).digest()


def _close_connections() -> None:
    global _CONNECTION_PID, _CONNECTION_ROOT
    for connection in _CONNECTIONS.values():
        try:
            connection.close()
        except sqlite3.Error:
            pass
    _CONNECTIONS.clear()
    _CONNECTION_PID = os.getpid()
    _CONNECTION_ROOT = None


def _connect(
    path: Path, *, root: Path, readonly: bool, cold: bool
) -> sqlite3.Connection | None:
    global _CONNECTION_PID, _CONNECTION_ROOT, _CONNECTION_READONLY
    pid = os.getpid()
    if _CONNECTION_PID != pid:
        _close_connections()
    if _CONNECTION_ROOT is not None and _CONNECTION_ROOT != root:
        _close_connections()
    if _CONNECTION_ROOT is None:
        _CONNECTION_ROOT = root
        _CONNECTION_READONLY = readonly
    elif _CONNECTION_READONLY != readonly:
        _close_connections()
        _CONNECTION_ROOT = root
        _CONNECTION_READONLY = readonly
    key = (str(path), readonly, cold)
    if key in _CONNECTIONS:
        return _CONNECTIONS[key]
    if readonly:
        if not path.exists():
            return None
        uri = f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=_TIMEOUT_MS / 1000)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            path, timeout=_TIMEOUT_MS / 1000, check_same_thread=False
        )
        connection.execute(f"PRAGMA busy_timeout={_TIMEOUT_MS}")
        deadline = time.monotonic() + _TIMEOUT_MS / 1000
        while True:
            try:
                mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                if str(mode).lower() != "wal":
                    connection.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    connection.close()
                    raise
                if time.monotonic() >= deadline:
                    connection.close()
                    raise
                time.sleep(0.01)
        table = _COLD_TABLE if cold else _TABLE
        columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
        expected = (
            ["key", "value"]
            if cold
            else ["key", "value", "engine", "chem_version", "source_box", "row_seq"]
        )
        if columns and [row[1] for row in columns] != expected:
            connection.execute(f"DROP TABLE {table}")
        if cold:
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {table} "
                "(key BLOB PRIMARY KEY, value BLOB NOT NULL) WITHOUT ROWID"
            )
        else:
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {table} "
                "(key BLOB PRIMARY KEY, value TEXT NOT NULL, engine TEXT NOT NULL, "
                "chem_version TEXT NOT NULL, source_box TEXT NOT NULL, "
                "row_seq INTEGER NOT NULL) WITHOUT ROWID"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS engine_outputs_engine_version "
                "ON engine_outputs(engine, chem_version)"
            )
        connection.commit()
    _CONNECTIONS[key] = connection
    return connection


def engine_cache_counters(*, reset: bool = False) -> dict[str, dict[str, int]]:
    with _LOCK:
        snapshot = {engine: dict(counts) for engine, counts in _COUNTERS.items()}
        if reset:
            _COUNTERS.clear()
        return snapshot


def _record_engine_cache_counters(
    counters: Mapping[str, Mapping[str, int]],
) -> None:
    with _LOCK:
        for engine, values in counters.items():
            target = _COUNTERS.setdefault(engine, {"hits": 0, "misses": 0, "stores": 0})
            for name in target:
                target[name] += int(values.get(name, 0))


def _hot_and_cold(result: Any) -> tuple[Any, Any]:
    if isinstance(result, CacheValue):
        return result.hot, result.cold
    return result, None


def _is_refusal(result: Any) -> bool:
    value = result.hot if isinstance(result, CacheValue) else result
    status = value.get("status") if isinstance(value, Mapping) else getattr(value, "status", None)
    refusal = (
        value.get("refusal_reason")
        if isinstance(value, Mapping)
        else getattr(value, "refusal_reason", None)
    )
    return isinstance(value, BaseException) or refusal is not None or (
        status is not None
        and status not in {"ok", "success", "converged", "non_authoritative"}
    )


def _source_box() -> str:
    return os.environ.get("REGOLITH_ENGINE_CACHE_BOX") or socket.gethostname().split(".", 1)[0]


def _next_row_sequence(connection: sqlite3.Connection, source_box: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(row_seq), 0) + 1 FROM engine_outputs WHERE source_box=?",
        (source_box,),
    ).fetchone()
    return int(row[0])


def cached_engine_call(engine_id: str, request: Any, solve: Any) -> Any:
    path_value = os.environ.get("REGOLITH_ENGINE_CACHE")
    if not path_value:
        return _hot_and_cold(solve())[0]
    mode = os.environ.get("REGOLITH_ENGINE_CACHE_MODE", "").lower()
    if mode not in {"", "readonly"}:
        raise ValueError(f"unknown engine cache mode {mode!r}")
    readonly = mode == "readonly"
    path = Path(path_value).expanduser()
    chem_version = ENGINE_CHEMISTRY_VERSION[engine_id]
    cache_key = engine_cache_key(engine_id, chem_version, request)
    connection = _connect(path, root=path, readonly=readonly, cold=False)
    with _LOCK:
        counts = _COUNTERS.setdefault(engine_id, {"hits": 0, "misses": 0, "stores": 0})
        row = None if connection is None else connection.execute(
            f"SELECT value FROM {_TABLE} WHERE key=?", (cache_key,)
        ).fetchone()
        if row is not None:
            try:
                result = json.loads(row[0])
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
                row = None
            else:
                counts["hits"] += 1
                return result
        counts["misses"] += 1

    result = solve()
    hot, cold = _hot_and_cold(result)
    if _is_refusal(result) or readonly or connection is None:
        return hot
    try:
        hot_value = json.dumps(
            hot, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        )
        cold_value = zlib.compress(pickle.dumps(cold, protocol=5), level=6)
    except (TypeError, ValueError) as exc:
        raise ValueError("engine cache result must have canonical JSON HOT fields") from exc
    source_box = _source_box()
    cold_path = path.with_name(path.name + ".cold")
    with _LOCK:
        connection.execute("BEGIN IMMEDIATE")
        row_seq = _next_row_sequence(connection, source_box)
        connection.execute(
            f"INSERT OR REPLACE INTO {_TABLE} "
            "(key, value, engine, chem_version, source_box, row_seq) VALUES (?, ?, ?, ?, ?, ?)",
            (cache_key, hot_value, engine_id, str(chem_version), source_box, row_seq),
        )
        connection.commit()
        cold_connection = _connect(
            cold_path, root=path, readonly=False, cold=True
        )
        assert cold_connection is not None
        cold_connection.execute(
            f"INSERT OR REPLACE INTO {_COLD_TABLE} (key, value) VALUES (?, ?)",
            (cache_key, sqlite3.Binary(cold_value)),
        )
        cold_connection.commit()
        _COUNTERS[engine_id]["stores"] += 1
    return hot


def load_cache_rows(
    path: str | Path,
    rows: Iterable[tuple[bytes, Any, str, str | int, str, int]],
) -> int:
    database = Path(path).expanduser()
    connection = _connect(database, root=database, readonly=False, cold=False)
    assert connection is not None
    values = []
    for digest, hot, engine, version, source_box, row_seq in rows:
        if not isinstance(digest, bytes) or len(digest) != 32:
            raise ValueError("cache keys must be raw 32-byte SHA-256 digests")
        if engine not in ENGINE_CHEMISTRY_VERSION:
            raise ValueError(f"unknown engine {engine!r}")
        values.append(
            (
                digest,
                json.dumps(hot, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False),
                engine,
                str(version),
                source_box,
                int(row_seq),
            )
        )
    before = connection.total_changes
    connection.executemany(
        f"INSERT OR IGNORE INTO {_TABLE} "
        "(key, value, engine, chem_version, source_box, row_seq) VALUES (?, ?, ?, ?, ?, ?)",
        values,
    )
    connection.commit()
    return connection.total_changes - before


def drop_chemistry_version(engine_id: str, chem_version: str | int) -> int:
    path_value = os.environ.get("REGOLITH_ENGINE_CACHE")
    if not path_value:
        return 0
    path = Path(path_value).expanduser()
    connection = _connect(path, root=path, readonly=False, cold=False)
    assert connection is not None
    cursor = connection.execute(
        f"DELETE FROM {_TABLE} WHERE engine=? AND chem_version=?",
        (engine_id, str(chem_version)),
    )
    connection.commit()
    return cursor.rowcount
