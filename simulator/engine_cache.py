"""One process-local handle for the shared engine-result cache."""

from __future__ import annotations

import hashlib
import os
import pickle
import sqlite3
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from simulator.canonical import canonical_json_dumps

ENGINE_CHEMISTRY_VERSION = {
    "openimcc": 1, "internal-analytical": 1, "vaporock": 1,
    "magemin": 1, "alphamelts": 1, "thermoengine": 1,
}

_TABLE = "engine_outputs"
_TIMEOUT_MS = 60_000
_LOCK = threading.RLock()
_CONNECTION: sqlite3.Connection | None = None
_CONNECTION_PATH: Path | None = None
_CONNECTION_PID: int | None = None
_COUNTERS: dict[str, dict[str, int]] = {}


def _key(engine_id: str, request: Any) -> str:
    if engine_id not in ENGINE_CHEMISTRY_VERSION:
        raise ValueError(f"unknown engine {engine_id!r}")
    try:
        canonical = canonical_json_dumps(
            [engine_id, ENGINE_CHEMISTRY_VERSION[engine_id], request]
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("engine request must be finite, canonical JSON data") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=_TIMEOUT_MS / 1000, check_same_thread=False)
    connection.execute(f"PRAGMA busy_timeout={_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode=WAL")
    columns = connection.execute(f"PRAGMA table_info({_TABLE})").fetchall()
    if columns and [row[1] for row in columns] != ["key", "value"]:
        connection.execute(f"DROP TABLE {_TABLE}")
    connection.execute(f"CREATE TABLE IF NOT EXISTS {_TABLE} (key TEXT PRIMARY KEY, value BLOB NOT NULL)")
    connection.commit()
    return connection


def _connection(path: Path) -> sqlite3.Connection:
    global _CONNECTION, _CONNECTION_PATH, _CONNECTION_PID
    pid = os.getpid()
    with _LOCK:
        if (
            _CONNECTION is not None
            and _CONNECTION_PID == pid
            and _CONNECTION_PATH == path
        ):
            return _CONNECTION
        if _CONNECTION is not None and _CONNECTION_PID == pid:
            _CONNECTION.close()
        _CONNECTION = _open(path)
        _CONNECTION_PATH = path
        _CONNECTION_PID = pid
        return _CONNECTION


def engine_cache_counters(*, reset: bool = False) -> dict[str, dict[str, int]]:
    with _LOCK:
        snapshot = {engine: dict(counts) for engine, counts in _COUNTERS.items()}
        if reset:
            _COUNTERS.clear()
        return snapshot


def cached_engine_call(
    engine_id: str,
    request: Any,
    solve: Callable[[], Any],
) -> Any:
    path_value = os.environ.get("REGOLITH_ENGINE_CACHE")
    if not path_value:
        return solve()
    cache_key = _key(engine_id, request)
    connection = _connection(Path(path_value).expanduser())
    with _LOCK:
        counts = _COUNTERS.setdefault(
            engine_id, {"hits": 0, "misses": 0, "stores": 0}
        )
        row = connection.execute(
            f"SELECT value FROM {_TABLE} WHERE key=?", (cache_key,)
        ).fetchone()
        if row is not None:
            try:
                result = pickle.loads(row[0])
            except Exception:
                pass
            else:
                counts["hits"] += 1
                return result
        counts["misses"] += 1

    result = solve()
    status = result.get("status") if isinstance(result, Mapping) else getattr(result, "status", None)
    refusal = result.get("refusal_reason") if isinstance(result, Mapping) else getattr(result, "refusal_reason", None)
    if isinstance(result, BaseException) or refusal is not None or (
        status is not None and status not in {"ok", "success", "converged"}
    ):
        return result
    value = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
    with _LOCK:
        connection.execute(
            f"INSERT OR REPLACE INTO {_TABLE} (key, value) VALUES (?, ?)",
            (cache_key, sqlite3.Binary(value)),
        )
        connection.commit()
        _COUNTERS[engine_id]["stores"] += 1
    return result
