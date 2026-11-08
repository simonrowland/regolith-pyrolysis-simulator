"""Stand-in for the battery engine output cache contract.

The cache owner is implementing this module in parallel. This small
implementation keeps the wiring and equivalence tests executable in this
seat and should be replaced by that implementation when the pieces merge.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import shutil
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Mapping


ENGINE_CHEMISTRY_VERSION: Mapping[str, int] = {
    "openimcc": 1,
    "internal-analytical": 1,
    "vaporock": 1,
    "magemin": 1,
    "alphamelts": 1,
    "thermoengine": 1,
}

_TRANSIENT_REASONS = {
    "engine_timeout",
    "engine_crash",
    "engine_unavailable",
    "subprocess_died",
    "worker_startup_failure",
    "resource_error",
}
_CALL_KINDS = ("equilibrate_cell", "evaluate", "evaluate_redox")
_LOGGER = logging.getLogger(__name__)


def _exact_json_value(value: Any) -> Any:
    if isinstance(value, float):
        return {"$float": value.hex()}
    if isinstance(value, Mapping):
        return {str(key): _exact_json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_exact_json_value(item) for item in value]
    if hasattr(value, "as_payload"):
        return _exact_json_value(value.as_payload())
    if hasattr(value, "value") and isinstance(value.value, (str, int)):
        return value.value
    return value


def _cache_key(engine_id: str, request_vector: Mapping[str, Any]) -> str:
    version = ENGINE_CHEMISTRY_VERSION[engine_id]
    body = json.dumps(
        _exact_json_value(
            {
                "engine": engine_id,
                "chemistry_version": version,
                "request": request_vector,
            }
        ),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _runtime_identity(engine_id: str) -> dict[str, Any]:
    packages = {
        "openimcc": ("openimcc",),
        "internal-analytical": (),
        "vaporock": ("vaporock", "VapoRock"),
        "magemin": (),
        "alphamelts": ("alphamelts", "AlphaMELTS"),
        "thermoengine": ("thermoengine",),
    }[engine_id]
    runtime: dict[str, Any] = {}
    for package in packages:
        try:
            runtime["package"] = package
            runtime["version"] = importlib.metadata.version(package)
            spec = importlib.util.find_spec(package)
            if spec is not None and spec.origin:
                path = Path(spec.origin)
                runtime["path"] = str(path)
                try:
                    runtime["mtime_ns"] = path.stat().st_mtime_ns
                except OSError:
                    pass
            break
        except (importlib.metadata.PackageNotFoundError, ImportError, ValueError):
            continue
    executable_names = {
        "magemin": ("MAGEMin", "magemin"),
        "alphamelts": ("alphamelts", "alphaMELTS"),
    }.get(engine_id, ())
    for name in executable_names:
        executable = shutil.which(name)
        if executable:
            path = Path(executable)
            runtime["binary"] = str(path)
            try:
                runtime["binary_mtime_ns"] = path.stat().st_mtime_ns
            except OSError:
                pass
            break
    return runtime


class EngineCache:
    """SQLite cache with process-safe, content-addressed entries."""

    def __init__(self, path: str | Path) -> None:
        self.db_path = Path(path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._counts: dict[str, dict[str, Any]] = {
            engine: {
                **{
                    name: 0
                    for name in ("hits", "misses", "stores", "transient_skips")
                },
                "calls_by_kind": {call: 0 for call in _CALL_KINDS},
            }
            for engine in ENGINE_CHEMISTRY_VERSION
        }
        self._runtime_change_notices: set[str] = set()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS engine_cache ("
                    "cache_key TEXT PRIMARY KEY, engine_id TEXT NOT NULL, "
                    "payload TEXT NOT NULL, runtime_meta TEXT NOT NULL)"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def get(self, key: str) -> Any | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM engine_cache WHERE cache_key=?", (key,)
            ).fetchone()
        return None if row is None else json.loads(str(row[0]))

    def _get_with_runtime(self, key: str) -> tuple[Any, dict[str, Any]] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload, runtime_meta FROM engine_cache WHERE cache_key=?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(str(row[0])), json.loads(str(row[1]))

    def put(self, key: str, payload: Any, runtime_meta: Mapping[str, Any]) -> None:
        payload_json = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO engine_cache "
                    "(cache_key, engine_id, payload, runtime_meta) VALUES (?, ?, ?, ?)",
                    (
                        key,
                        str(payload.get("engine", ""))
                        if isinstance(payload, Mapping)
                        else "",
                        payload_json,
                        json.dumps(dict(runtime_meta), sort_keys=True, separators=(",", ":")),
                    ),
                )

    def _count(self, engine_id: str, counter: str) -> None:
        with self._lock:
            self._counts[engine_id][counter] += 1

    def _count_call(self, engine_id: str, call_kind: str) -> None:
        if call_kind not in _CALL_KINDS:
            raise ValueError(f"unknown engine call kind {call_kind!r}")
        with self._lock:
            self._counts[engine_id]["calls_by_kind"][call_kind] += 1

    @property
    def counters(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                engine: {
                    **{name: value for name, value in counts.items() if name != "calls_by_kind"},
                    "calls_by_kind": dict(counts["calls_by_kind"]),
                }
                for engine, counts in self._counts.items()
            }

    def _runtime_change_notice(
        self, engine_id: str, stored: Mapping[str, Any], current: Mapping[str, Any]
    ) -> None:
        if dict(stored) == dict(current):
            return None
        with self._lock:
            if engine_id in self._runtime_change_notices:
                return None
            self._runtime_change_notices.add(engine_id)
        notice = {
            "kind": "engine_cache_runtime_changed",
            "engine": engine_id,
            "stored": dict(stored),
            "current": dict(current),
        }
        _LOGGER.info(
            "engine_cache_runtime_changed engine=%s stored=%s current=%s",
            engine_id,
            json.dumps(dict(stored), sort_keys=True, separators=(",", ":")),
            json.dumps(dict(current), sort_keys=True, separators=(",", ":")),
            extra={"notice": notice},
        )

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "path": str(self.db_path),
            "by_engine": self.counters,
        }


def _transient(outcome: Any) -> bool:
    if isinstance(outcome, (TimeoutError, MemoryError, OSError)):
        return True
    reason = str(getattr(outcome, "refusal_reason", "") or "")
    engine_status = str(getattr(outcome, "engine_status", "") or "").lower()
    engine_reason = str(getattr(outcome, "engine_reason", "") or "").lower()
    outcome_type = type(outcome).__name__
    refusal_detail = str(getattr(outcome, "reason", "") or "").lower()
    return (
        reason in _TRANSIENT_REASONS
        or outcome_type
        in {
            "ImccNonconvergenceError",
            "RedoxNumericalError",
            "ResidueEngineNonconvergence",
            "ResidueEngineTransportError",
        }
        or any(token in refusal_detail for token in ("engine_timeout", "subprocess_died", "worker_startup", "resource_error"))
        or any(token in engine_status for token in ("timeout", "workerstartup", "resourceerror"))
        or any(token in engine_reason for token in ("worker startup", "resource error", "subprocess died"))
    )


def _from_cache_payload(payload: Any, request_vector: Mapping[str, Any]) -> Any:
    call_kind = request_vector.get("call", "equilibrate_cell")
    if call_kind == "equilibrate_cell":
        from simulator.diagnostic_helpers.binary_pot_battery import EquilibrateCell

        return EquilibrateCell.from_cache_payload(payload)
    if call_kind == "evaluate":
        from simulator.melt_backend.openimcc_cache import OpenImccEvaluationOutcome

        return OpenImccEvaluationOutcome.from_cache_payload(payload)
    if call_kind == "evaluate_redox":
        from simulator.melt_backend.openimcc_cache import OpenImccRedoxOutcome

        return OpenImccRedoxOutcome.from_cache_payload(payload)
    raise ValueError(f"unknown engine call kind {call_kind!r}")


def cached_solve(
    cache: EngineCache | None,
    engine_id: str,
    request_vector: Mapping[str, Any],
    solve_fn: Callable[[], Any],
) -> Any:
    if cache is None:
        return solve_fn()
    key = _cache_key(engine_id, request_vector)
    current_runtime = _runtime_identity(engine_id)
    stored = cache._get_with_runtime(key)
    if stored is not None:
        payload, stored_runtime = stored
        cache._count(engine_id, "hits")
        outcome = _from_cache_payload(payload, request_vector)
        cache._runtime_change_notice(engine_id, stored_runtime, current_runtime)
        return outcome

    cache._count(engine_id, "misses")
    cache._count_call(
        engine_id,
        str(request_vector.get("call", "equilibrate_cell")),
    )
    try:
        outcome = solve_fn()
    except BaseException as exc:
        if _transient(exc):
            cache._count(engine_id, "transient_skips")
        raise
    if _transient(outcome):
        cache._count(engine_id, "transient_skips")
        return outcome
    cache.put(key, outcome.to_cache_payload(), current_runtime)
    cache._count(engine_id, "stores")
    return outcome


__all__ = ("ENGINE_CHEMISTRY_VERSION", "EngineCache", "cached_solve")
