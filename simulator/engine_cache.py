"""Exact, content-addressed persistence for battery engine cell outcomes."""

from __future__ import annotations

import errno
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import math
import os
import sqlite3
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

from simulator.canonical import canonical_json_dumps
from simulator.engine_pool import EngineWorkerDied


ENGINE_CHEMISTRY_VERSION: Mapping[str, int] = {
    "openimcc": 1,
    "internal-analytical": 1,
    "vaporock": 1,
    "magemin": 1,
    "alphamelts": 1,
    "thermoengine": 1,
}

# Chemistry-version history. Increment a version only when engine chemistry may change.
# openimcc v1: 2026-11-08 uv.lock pin 66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84 - initial cache population
# internal-analytical v1: 2026-11-08 repository source d5e6b8dc2 - initial cache population
# vaporock v1: 2026-11-08 engine lock pin 0159678a8eefb51dfe3f12bb646dc38507a07230 - initial cache population
# magemin v1: 2026-11-08 engine lock pin 40ed109d07d4246001cab59382596d33fed79086 - initial cache population
# alphamelts v1: 2026-11-08 alphaMELTS 2.3.1 with PetThermoTools pin 74a41ff5685d9c8280b90667bfa43efe97fca87c - initial cache population
# thermoengine v1: 2026-11-08 engine lock pin df7a5f4609116a83bf40a5a352a19e3826a822d9 - initial cache population

ENGINE_CHEMISTRY_DEPENDENCIES: Mapping[str, tuple[str, ...]] = {
    "openimcc": (),
    "internal-analytical": ("openimcc",),
    "vaporock": (),
    "magemin": (),
    "alphamelts": ("vaporock",),
    "thermoengine": ("vaporock",),
}

_BUSY_TIMEOUT_MS = 60_000
_TABLE_NAME = "engine_outputs"
_COUNTER_NAMES = ("hits", "misses", "stores", "transient_skips")
_CALL_KINDS = (
    "equilibrate_cell",
    "evaluate",
    "evaluate_redox",
    "evaluate_gas",
    "residue_vapor_pressure",
)
_LOGGER = logging.getLogger(__name__)
_ENGINE_RUNTIME_MODULES = {
    "openimcc": ("openimcc", "openimcc"),
    "internal-analytical": (
        "simulator.diagnostic_helpers.binary_pot_battery",
        None,
    ),
    "vaporock": ("simulator.melt_backend.vaporock", "vaporock"),
    "magemin": ("simulator.melt_backend.magemin", "magemin"),
    "alphamelts": ("simulator.melt_backend.alphamelts", "petthermotools"),
    "thermoengine": ("simulator.melt_backend.thermoengine", "thermoengine"),
}
_TRANSIENT_RESOURCE_ERRNOS = frozenset(
    value
    for value in (
        errno.EAGAIN,
        errno.ENOMEM,
        errno.ENOSPC,
        errno.EMFILE,
        errno.ENFILE,
        errno.ETIMEDOUT,
        errno.EBUSY,
    )
)
_TRANSIENT_CELL_ENGINE_STATUSES = frozenset(
    {
        "TimeoutError",
        "EngineWorkerTimeout",
        "EngineWorkerDied",
        "MemoryError",
        "ResourceError",
        "OSError",
        "FileNotFoundError",
        "PermissionError",
        "BlockingIOError",
        "ConnectionError",
        "BrokenPipeError",
        "unavailable",
    }
)
_TRANSIENT_BACKEND_FAILURE_DISPOSITIONS = frozenset(
    {"environment", "resource", "worker_died", "timeout", "transient"}
)


class EngineCacheError(RuntimeError):
    """Base error for invalid or conflicting engine cache entries."""


class EngineCacheCollision(EngineCacheError):
    """One exact request key produced two different serialized outcomes."""


class TransientEngineFailure(RuntimeError):
    """Typed signal that an engine call failed transiently and must not cache."""


@runtime_checkable
class _CellOutcome(Protocol):
    status: str
    refusal_reason: str | None
    engine_status: str | None
    engine_reason: str | None


def _json_value(value: Any, *, path: str = "value") -> Any:
    """Copy supported JSON values while rejecting lossy key/value coercions."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        # json.dumps uses Python's shortest round-trip float representation.
        # That preserves the exact binary float; no quantization is applied.
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(name, str) for name in value):
            raise TypeError(f"{path} mapping keys must be strings")
        return {
            name: _json_value(value[name], path=f"{path}.{name}")
            for name in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [
            _json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(
        f"{path} contains unsupported JSON value {type(value).__name__}"
    )


def _canonical_json(value: Any) -> str:
    return canonical_json_dumps(_json_value(value))


def key(
    engine_id: str,
    request_vector: Mapping[str, Any],
) -> str:
    """Return the exact SHA-256 identity of one engine request vector."""
    if engine_id not in ENGINE_CHEMISTRY_VERSION:
        raise ValueError(f"unknown battery engine {engine_id!r}")
    if not isinstance(request_vector, Mapping):
        raise TypeError("request_vector must be a mapping")
    identity = {
        "engine": engine_id,
        "chemistry_version": ENGINE_CHEMISTRY_VERSION[engine_id],
        "request": request_vector,
    }
    dependency_versions: dict[str, int] = {}
    for dependency in ENGINE_CHEMISTRY_DEPENDENCIES[engine_id]:
        if dependency not in ENGINE_CHEMISTRY_VERSION:
            raise ValueError(f"unknown battery engine dependency {dependency!r}")
        if dependency == engine_id:
            raise ValueError(f"engine {engine_id!r} cannot depend on itself")
        dependency_versions[dependency] = ENGINE_CHEMISTRY_VERSION[dependency]
    if dependency_versions:
        identity["dependency_chemistry_versions"] = dependency_versions
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


@lru_cache(maxsize=len(ENGINE_CHEMISTRY_VERSION))
def runtime_identity(engine_id: str) -> dict[str, Any]:
    """Describe the installed Python runtime selected for a battery engine."""
    try:
        module_name, distribution = _ENGINE_RUNTIME_MODULES[engine_id]
    except KeyError as exc:
        raise ValueError(f"unknown battery engine {engine_id!r}") from exc

    package_version: str | None = None
    if distribution is not None:
        try:
            package_version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            pass

    module_path = None
    module_mtime_ns = None
    try:
        spec = importlib.util.find_spec(module_name)
        origin = None if spec is None else spec.origin
        if origin and origin not in {"built-in", "frozen"}:
            path = Path(origin).resolve()
            module_path = str(path)
            try:
                module_mtime_ns = path.stat().st_mtime_ns
            except OSError:
                pass
    except (ImportError, ModuleNotFoundError, ValueError):
        pass

    identity = {
        "module": module_name,
        "package_version": package_version,
        "module_path": module_path,
        "module_mtime_ns": module_mtime_ns,
    }
    if engine_id in {"alphamelts", "magemin"}:
        identity["binary"] = _binary_runtime_identity(engine_id)
    elif engine_id == "thermoengine":
        identity["native_library"] = _thermoengine_native_library_identity()
    return identity


def _thermoengine_native_library_path() -> Path | None:
    from simulator.engine_local_config import (
        _thermoengine_phaseobjc_library_path,
    )

    return _thermoengine_phaseobjc_library_path()


def _thermoengine_native_library_identity() -> dict[str, Any] | None:
    try:
        path = _thermoengine_native_library_path()
        if path is None:
            return None
        resolved = Path(path).expanduser().resolve()
        try:
            modified_ns = resolved.stat().st_mtime_ns
        except OSError:
            modified_ns = None
        return {"path": str(resolved), "mtime_ns": modified_ns}
    except Exception:  # noqa: BLE001 - runtime metadata never gates cache use
        return None


@lru_cache(maxsize=2)
def _binary_runtime_identity(engine_id: str) -> dict[str, Any] | None:
    if engine_id == "alphamelts":
        from simulator.engine_local_config import find_alphamelts_binary

        binary_path = find_alphamelts_binary()
    elif engine_id == "magemin":
        from simulator.melt_backend.magemin import MAGEMinBackend

        binary_path = MAGEMinBackend()._resolve_binary_path()
    else:
        raise ValueError(f"engine {engine_id!r} has no binary runtime identity")
    if binary_path is None:
        return None
    resolved = Path(binary_path).expanduser().resolve()
    try:
        modified_ns = resolved.stat().st_mtime_ns
    except OSError:
        modified_ns = None
    return {"path": str(resolved), "mtime_ns": modified_ns}


def is_transient_failure(outcome: object) -> bool:
    """Identify failures whose cell result can change on a later engine call."""
    if isinstance(outcome, TransientEngineFailure):
        return True
    if isinstance(outcome, (TimeoutError, MemoryError)):
        return True
    if isinstance(outcome, OSError):
        return outcome.errno in _TRANSIENT_RESOURCE_ERRNOS

    if isinstance(outcome, EngineWorkerDied):
        return True

    if not isinstance(outcome, _CellOutcome):
        return False
    disposition = getattr(outcome, "backend_failure_disposition", None)
    if disposition is None:
        diagnostics = getattr(outcome, "diagnostics", None)
        if isinstance(diagnostics, Mapping):
            disposition = diagnostics.get("backend_failure_disposition")
    if str(disposition or "").lower() in _TRANSIENT_BACKEND_FAILURE_DISPOSITIONS:
        return True
    exit_code = getattr(outcome, "exit_code", None)
    exit_signal = getattr(outcome, "exit_signal", None)
    engine_reason = str(outcome.engine_reason or "")
    return (
        outcome.refusal_reason == "engine_timeout"
        or outcome.refusal_reason == "unavailable"
        or outcome.engine_status in _TRANSIENT_CELL_ENGINE_STATUSES
        or outcome.engine_reason == "subprocess_died"
        or (
            outcome.refusal_reason == "engine_crash"
            and (
                exit_code is not None
                or exit_signal is not None
                or engine_reason.startswith("isolated worker exit_code=")
            )
        )
    )


class EngineCache:
    """WAL-backed exact cache safe for concurrent battery shard writers."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.db_path = Path(path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._defer_runtime_notices = False
        self._counter_lock = threading.Lock()
        self._tripwire_lock = threading.Lock()
        self._runtime_change_noticed: set[str] = set()
        self._runtime_changes: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self._counters = {
            engine_id: {
                **{name: 0 for name in _COUNTER_NAMES},
                "calls_by_kind": {call_kind: 0 for call_kind in _CALL_KINDS},
            }
            for engine_id in ENGINE_CHEMISTRY_VERSION
        }
        self._initialize()

    @property
    def counters(self) -> dict[str, dict[str, Any]]:
        """Return a detached snapshot of per-engine work-unit counters."""
        with self._counter_lock:
            return {
                engine_id: {
                    **{
                        name: value
                        for name, value in values.items()
                        if name != "calls_by_kind"
                    },
                    "calls_by_kind": dict(values["calls_by_kind"]),
                }
                for engine_id, values in self._counters.items()
            }

    @property
    def runtime_changes(
        self,
    ) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
        with self._tripwire_lock:
            return {
                engine_id: (dict(stored), dict(current))
                for engine_id, (stored, current) in self._runtime_changes.items()
            }

    def _count(self, engine_id: str, counter: str) -> None:
        with self._counter_lock:
            self._counters[engine_id][counter] += 1

    def _count_call(self, engine_id: str, call_kind: str) -> None:
        if call_kind not in _CALL_KINDS:
            raise ValueError(f"unknown engine call kind {call_kind!r}")
        with self._counter_lock:
            self._counters[engine_id]["calls_by_kind"][call_kind] += 1

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.db_path,
            timeout=_BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            connection.row_factory = sqlite3.Row
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    runtime_meta_json TEXT NOT NULL
                ) WITHOUT ROWID
                """
            )

    def _get_entry(self, cache_key: str) -> tuple[Any, dict[str, Any]] | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload_json, runtime_meta_json FROM {_TABLE_NAME} "
                "WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"]), json.loads(row["runtime_meta_json"])

    def get(self, cache_key: str) -> Any | None:
        """Return the serialized payload for a key, or ``None`` on a miss."""
        entry = self._get_entry(cache_key)
        return None if entry is None else entry[0]

    def put(
        self,
        cache_key: str,
        payload: Any,
        runtime_meta: Mapping[str, Any],
    ) -> None:
        """Insert once; repeated writes are idempotent and verify payload bytes."""
        if not isinstance(cache_key, str) or not cache_key:
            raise ValueError("cache_key must be a non-empty string")
        if not isinstance(runtime_meta, Mapping):
            raise TypeError("runtime_meta must be a mapping")
        payload_json = _canonical_json(payload)
        runtime_meta_json = _canonical_json(runtime_meta)
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    f"INSERT INTO {_TABLE_NAME} "
                    "(cache_key, payload_json, runtime_meta_json) VALUES (?, ?, ?) "
                    "ON CONFLICT(cache_key) DO NOTHING",
                    (cache_key, payload_json, runtime_meta_json),
                )
                row = connection.execute(
                    f"SELECT payload_json FROM {_TABLE_NAME} WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if row is None:
                    raise EngineCacheError("cache insert did not produce a row")
                if row["payload_json"] != payload_json:
                    raise EngineCacheCollision(
                        f"engine output payload differs for content key {cache_key}"
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _notice_runtime_change(
        self,
        engine_id: str,
        stored: Mapping[str, Any],
        current: Mapping[str, Any],
    ) -> None:
        if _canonical_json(stored) == _canonical_json(current):
            return
        with self._tripwire_lock:
            if engine_id in self._runtime_change_noticed:
                return
            self._runtime_change_noticed.add(engine_id)
        if self._defer_runtime_notices:
            with self._tripwire_lock:
                self._runtime_changes[engine_id] = (dict(stored), dict(current))
            return
        notice = {
            "kind": "engine_cache_runtime_changed",
            "engine": engine_id,
            "stored": dict(stored),
            "current": dict(current),
        }
        _LOGGER.info(
            "%s runtime changed (stored %s, now %s). Cached outputs are still used. "
            "Bump ENGINE_CHEMISTRY_VERSION['%s'] ONLY if this change affects chemistry; "
            "leave it for non-chemistry patches.",
            engine_id,
            _canonical_json(stored),
            _canonical_json(current),
            engine_id,
            extra={"notice": notice},
        )


def _from_cache_payload(payload: Any) -> Any:
    from simulator.diagnostic_helpers.binary_pot_battery import EquilibrateCell

    return EquilibrateCell.from_cache_payload(payload)


def _from_cache_call_payload(payload: Any, call_kind: str) -> Any:
    if call_kind == "equilibrate_cell":
        return _from_cache_payload(payload)
    if call_kind == "evaluate":
        from simulator.melt_backend.openimcc_cache import OpenImccEvaluationOutcome

        return OpenImccEvaluationOutcome.from_cache_payload(payload)
    if call_kind == "evaluate_redox":
        from simulator.melt_backend.openimcc_cache import OpenImccRedoxOutcome

        return OpenImccRedoxOutcome.from_cache_payload(payload)
    if call_kind == "evaluate_gas":
        from simulator.melt_backend.openimcc_cache import OpenImccGasOutcome

        return OpenImccGasOutcome.from_cache_payload(payload)
    if call_kind == "residue_vapor_pressure":
        from simulator.diagnostic_helpers.binary_pot_battery import (
            InternalAnalyticalVaporPressureOutcome,
        )

        return InternalAnalyticalVaporPressureOutcome.from_cache_payload(payload)
    raise ValueError(f"unknown engine call kind {call_kind!r}")


def cached_solve(
    cache: EngineCache | None,
    engine_id: str,
    request_vector: Mapping[str, Any],
    solve_fn: Callable[[], Any],
) -> Any:
    """Reuse an exact cached cell outcome, storing only stable results."""
    if cache is None:
        return solve_fn()

    cache_key = key(engine_id, request_vector)
    call_kind = str(request_vector.get("call", "equilibrate_cell"))
    if call_kind not in _CALL_KINDS:
        raise ValueError(f"unknown engine call kind {call_kind!r}")
    entry = cache._get_entry(cache_key)
    if entry is not None:
        payload, stored_runtime = entry
        cache._count(engine_id, "hits")
        try:
            current_runtime = runtime_identity(engine_id)
        except Exception:
            current_runtime = None
        if current_runtime is not None:
            cache._notice_runtime_change(engine_id, stored_runtime, current_runtime)
        return _from_cache_call_payload(payload, call_kind)

    cache._count(engine_id, "misses")
    cache._count_call(engine_id, call_kind)
    try:
        current_runtime = runtime_identity(engine_id)
    except Exception:
        current_runtime = {}
    try:
        outcome = solve_fn()
    except BaseException as exc:
        if is_transient_failure(exc):
            cache._count(engine_id, "transient_skips")
        raise

    if is_transient_failure(outcome):
        cache._count(engine_id, "transient_skips")
        return outcome

    to_payload = getattr(outcome, "to_cache_payload", None)
    if not callable(to_payload):
        raise TypeError("engine outcome must provide to_cache_payload()")
    cache.put(cache_key, to_payload(), current_runtime)
    cache._count(engine_id, "stores")
    return outcome
