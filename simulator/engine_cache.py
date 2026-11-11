"""One process-local HOT/COLD SQLite cache for engine outputs."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import pickle
import shutil
import socket
import sqlite3
import threading
import time
import zlib
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
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

# Bump only when an engine change can alter predictions. Runtime identity is
# diagnostic and never participates in the cache key.
# alphamelts v1: 2026-11-08 alphaMELTS 2.3.1 Darwin arm64 binary - initial cache version
# thermoengine v1: 2026-11-08 ThermoEngine df7a5f4609116a83bf40a5a352a19e3826a822d9 - initial cache version
# magemin v1: 2026-11-08 MAGEMin commit 40ed109d07d4246001cab59382596d33fed79086 - initial cache version
# vaporock v1: 2026-11-08 VapoRock 0159678a8eefb51dfe3f12bb646dc38507a07230 - initial cache version
# internal-analytical v1: 2026-11-08 simulator code at d5e6b8dc2 - initial cache version
# openimcc v1: 2026-11-08 OpenIMCC pin 66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84 - initial cache version
ENGINE_CHEMISTRY_VERSION_IDENTITY_AT = {
    "openimcc": {
        "identity": "commit:66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84",
        "date": "2026-11-08",
    },
    "internal-analytical": {
        "identity": "source-sha256:ac03c54243b61890960158318fb40a52972f301e9ac1dc9c381fa3a495818740",
        "date": "2026-11-08",
    },
    "vaporock": {
        "identity": "version:1.0.0;record-sha256:89e860e3d6995f542337748019c4f6198f76c127ce3ca90fbbc2063bd401fc5a",
        "date": "2026-11-08",
    },
    "magemin": {
        "identity": "sha256:6c623e220e5fa10ebdc063f5c8ea80cda52b06593e98d59be677e25de9ee4d28",
        "date": "2026-11-08",
        "stat": {"size": 38281864, "mtime_ns": 1788656204474898883},
    },
    "alphamelts": {
        "identity": "sha256:d91bd8baee106dee03136e7bf16a9e2c6c17d0dda6c978ff1bfd698c0b073a85",
        "date": "2026-11-08",
        "stat": {"size": 3227431, "mtime_ns": 1778385997000000000},
    },
    "thermoengine": {
        "identity": "version:1.2.0;record-sha256:462a81b3fda92acf74a1e9e38f260dac65232d855340f72307e05a65219c1b27",
        "date": "2026-11-08",
    },
}

OPENIMCC_CANONICAL_OUTPUT_SHA256 = "2eafdcb2139bb1cce9309ceff1aa0b981b609f305406dff8b1d7c3e3002fa69d"

_TABLE = "engine_outputs"
_COLD_TABLE = "engine_outputs_cold"
_TIMEOUT_MS = 60_000
_LOCK = threading.RLock()
_CONNECTIONS: dict[tuple[str, bool, bool], sqlite3.Connection] = {}
_CONNECTION_PID: int | None = None
_CONNECTION_ROOT: Path | None = None
_CONNECTION_READONLY = False
_COUNTERS: dict[str, dict[str, int]] = {}
_COUNTER_NAMES = ("hits", "misses", "stores", "store_error", "read_error")
_MEMORY_CACHE_MAXSIZE = 4096
_MEMORY_CACHE: OrderedDict[tuple[str, bytes, bool], Any] = OrderedDict()
_MEMORY_CACHE_PID = os.getpid()
_RUNTIME_BACKENDS: dict[str, Any] = {}
_RUNTIME_IDENTITIES: dict[str, str] = {}
_RUNTIME_WARNED: set[str] = set()
_RUNTIME_OPEN_RECORDED: set[str] = set()


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


def engine_cache_settings(engine_name: str, backend: Any) -> dict[str, Any]:
    """Project only named prediction inputs from an opened backend or its config."""
    if engine_name == "internal-analytical":
        # Residue calls consume prior-call state and are deliberately uncached.
        return {}

    is_config = isinstance(backend, Mapping)
    fields = backend if is_config else vars(backend)

    if engine_name == "openimcc":
        if is_config:
            melt_pack = fields.get("pack") or "default"
            gas_pack = fields.get("gas_pack", "default")
        else:
            identity = fields.get("_identity")
            loaded_pack = identity.get("pack") if isinstance(identity, Mapping) else None
            melt_pack = loaded_pack or fields.get("_pack_name", "default")
            gas_pack = fields.get("_gas_pack_name", "default")
        return {
            "melt_pack": melt_pack,  # Selects melt coefficients and activities.
            "gas_pack": gas_pack,  # Selects gas species thermochemistry.
        }
    if engine_name == "alphamelts":
        if is_config:
            transport_mode = (
                fields.get("transport")
                or fields.get("ambient_calc_mode")
                or os.environ.get("ALPHAMELTS_CALC_MODE")
            )
            model = fields.get("raw_model_name")
            redox_buffer = fields.get("redox_buffer")
            fo2_offset = fields.get("fO2_offset")
            fe3fet_ratio = fields.get("Fe3Fet_ratio")
            api_model = fields.get("python_api_model")
            vapor_pO2 = fields.get("configured_vapor_transport_pO2_bar")
            vapor_floor = fields.get("configured_vapor_transport_floor_bar")
        else:
            transport_mode = fields.get("_mode")
            model = fields.get("_model")
            redox_buffer = fields.get("_redox_buffer")
            fo2_offset = fields.get("_fo2_offset")
            fe3fet_ratio = fields.get("_fe3fet_ratio")
            api_model = fields.get("_python_api_model")
            vapor_pO2 = fields.get("_configured_vapor_transport_pO2_bar")
            vapor_floor = fields.get("_configured_vapor_transport_floor_bar")
        if isinstance(api_model, tuple):
            api_model = list(api_model)
        return {
            "transport_mode": transport_mode,  # Selects the MELTS calculation route.
            "model": model,  # Selects the thermodynamic model.
            "python_api_model": api_model,  # Selects the Python API model revision.
            "redox_buffer": redox_buffer,  # Sets iron redox when no explicit split is given.
            "fo2_offset": fo2_offset,  # Offsets the selected oxygen buffer.
            "fe3fet_ratio": fe3fet_ratio,  # Sets the ferrous/ferric input split.
            "vapor_transport_pO2_bar": vapor_pO2,  # Sets vapor-transport oxygen pressure.
            "vapor_transport_floor_bar": vapor_floor,  # Sets the vapor-transport floor.
        }
    if engine_name == "vaporock":
        if is_config:
            temperature_units = fields.get("temperature_units", "C")
            pressure_units = fields.get("pressure_units", "bar")
            vapor_pressure_units = fields.get("vapor_pressure_units", "bar")
        else:
            temperature_units = fields.get("_temperature_units", "C")
            pressure_units = fields.get("_pressure_units", "bar")
            vapor_pressure_units = fields.get("_vapor_pressure_units", "bar")
        return {
            "temperature_units": temperature_units,  # Converts the input temperature.
            "pressure_units": pressure_units,  # Converts the input pressure.
            "vapor_pressure_units": vapor_pressure_units,  # Converts reported vapor pressure.
        }
    if engine_name == "magemin":
        if is_config:
            database = fields.get("database")
            bridge = fields.get("bridge", fields.get("python_bridge"))
            fO2_buffer = fields.get("fO2_buffer")
        else:
            database = fields.get("_database")
            bridge = fields.get("_bridge")
            config = fields.get("_config")
            fO2_buffer = config.get("fO2_buffer") if isinstance(config, Mapping) else None
        return {
            "database": database,  # Selects the phase database.
            "bridge": bridge,  # Selects the engine execution implementation.
            "fO2_buffer": fO2_buffer,  # Selects the oxygen buffer constraint.
        }
    if engine_name == "thermoengine":
        if is_config:
            model = fields.get("model")
            vapor_pO2 = fields.get("configured_vapor_transport_pO2_bar")
            vapor_floor = fields.get("configured_vapor_transport_floor_bar")
        else:
            model = fields.get("_model")
            vapor_pO2 = fields.get("_configured_vapor_transport_pO2_bar")
            vapor_floor = fields.get("_configured_vapor_transport_floor_bar")
        return {
            "model": model,  # Selects the ThermoEngine thermodynamic model.
            "vapor_transport_pO2_bar": vapor_pO2,  # Sets vapor-transport oxygen pressure.
            "vapor_transport_floor_bar": vapor_floor,  # Sets the vapor-transport floor.
        }
    raise ValueError(f"unknown engine {engine_name!r}")


def _configured_engine_cache_path() -> str | None:
    """Return the configured cache path, or None when caching is disabled."""
    return os.environ.get("REGOLITH_ENGINE_CACHE") or None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _distribution_identity(names: tuple[str, ...]) -> str:
    for name in names:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        record = distribution.read_text("RECORD")
        record_digest = (
            hashlib.sha256(record.encode("utf-8")).hexdigest()
            if record is not None and len(record) <= 2_000_000
            else "unavailable"
        )
        return f"version:{distribution.version};record-sha256:{record_digest}"
    return "unavailable"


def _native_binary_identity(engine_id: str) -> str:
    backend = _RUNTIME_BACKENDS.get(engine_id)
    binary = getattr(backend, "_binary_path", None)
    if binary is None and engine_id == "alphamelts":
        try:
            from simulator.engine_local_config import find_alphamelts_binary

            binary = find_alphamelts_binary()
        except Exception:
            binary = None
    elif binary is None and engine_id == "magemin":
        try:
            from simulator.engine_local_config import configured_magemin_binary_path

            binary = configured_magemin_binary_path()
            if binary is None:
                binary = shutil.which("MAGEMin") or shutil.which("magemin")
        except Exception:
            binary = None
    if binary is None:
        return "unavailable"
    path = Path(binary)
    try:
        stat = path.stat()
        recorded = ENGINE_CHEMISTRY_VERSION_IDENTITY_AT[engine_id]
        recorded_stat = recorded.get("stat")
        if (
            isinstance(recorded_stat, Mapping)
            and recorded_stat.get("size") == stat.st_size
            and recorded_stat.get("mtime_ns") == stat.st_mtime_ns
            and str(recorded["identity"]).startswith("sha256:")
        ):
            return str(recorded["identity"])
        # Hash only when the recorded stat fingerprint changed. This runs at
        # engine open and is memoized for the process, never on request lookup.
        digest = _file_sha256(path)
    except OSError:
        return "unavailable"
    return f"sha256:{digest}"


def _openimcc_dist_identity() -> str:
    try:
        distribution = importlib.metadata.distribution("openimcc")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"
    direct_url = distribution.read_text("direct_url.json")
    if direct_url:
        try:
            commit = json.loads(direct_url).get("vcs_info", {}).get("commit_id")
        except (AttributeError, json.JSONDecodeError):
            commit = None
        if commit:
            return f"commit:{commit}"
    return f"version:{distribution.version}"


def _internal_analytical_identity() -> str:
    backend = _RUNTIME_BACKENDS.get("internal-analytical")
    core = getattr(backend, "_core", backend)
    module_name = type(core).__module__ if core is not None else ""
    module = __import__(module_name, fromlist=["__file__"]) if module_name else None
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return "unavailable"
    path = Path(module_file)
    try:
        return f"source-sha256:{_file_sha256(path)}"
    except OSError:
        return "unavailable"


def _compute_runtime_identity(engine_id: str) -> str:
    try:
        if engine_id in {"alphamelts", "magemin"}:
            return _native_binary_identity(engine_id)
        if engine_id == "openimcc":
            return _openimcc_dist_identity()
        if engine_id == "internal-analytical":
            return _internal_analytical_identity()
        if engine_id == "thermoengine":
            return _distribution_identity(("ThermoEngine", "thermoengine"))
        if engine_id == "vaporock":
            return _distribution_identity(("VapoRock", "vaporock"))
    except Exception:
        return "unavailable"
    return "unavailable"


@lru_cache(maxsize=None)
def runtime_identity(engine_id: str) -> str:
    """Return the one per-process runtime identity captured at engine open."""
    if engine_id not in ENGINE_CHEMISTRY_VERSION:
        raise ValueError(f"unknown engine {engine_id!r}")
    return _compute_runtime_identity(engine_id)


def record_engine_open(engine_id: str, backend: Any | None = None) -> str | None:
    """Capture and report staleness once, outside the request-key path."""
    if not os.environ.get("REGOLITH_ENGINE_CACHE"):
        return None
    if engine_id in _RUNTIME_OPEN_RECORDED:
        return _RUNTIME_IDENTITIES.get(engine_id)
    if backend is not None:
        _RUNTIME_BACKENDS.setdefault(engine_id, backend)
    identity = runtime_identity(engine_id)
    _RUNTIME_OPEN_RECORDED.add(engine_id)
    _RUNTIME_IDENTITIES[engine_id] = identity
    recorded = ENGINE_CHEMISTRY_VERSION_IDENTITY_AT[engine_id]
    stored = str(recorded["identity"])
    if identity != stored and engine_id not in _RUNTIME_WARNED:
        _RUNTIME_WARNED.add(engine_id)
        logging.getLogger(__name__).warning(
            "engine cache: %s runtime changed (stored %s, now %s). "
            "Cached outputs are still used. Bump ENGINE_CHEMISTRY_VERSION['%s'] "
            "ONLY if this change affects chemistry; leave it for non-chemistry patches.",
            engine_id,
            stored,
            identity,
            engine_id,
        )
    return identity


def engine_cache_runtime_identities(*, reset: bool = False) -> dict[str, str]:
    identities = dict(_RUNTIME_IDENTITIES)
    if reset:
        _RUNTIME_IDENTITIES.clear()
        _RUNTIME_WARNED.clear()
        _RUNTIME_OPEN_RECORDED.clear()
    return identities


_NAMESPACE_TAG = "__engine_cache_simple_namespace__"
_TUPLE_TAG = "__engine_cache_tuple__"


def _encode_hot(value: Any) -> Any:
    if isinstance(value, SimpleNamespace):
        return {
            _NAMESPACE_TAG: {
                key: _encode_hot(item) for key, item in vars(value).items()
            }
        }
    if isinstance(value, Mapping):
        return {str(key): _encode_hot(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return {_TUPLE_TAG: [_encode_hot(item) for item in value]}
    if isinstance(value, list):
        return [_encode_hot(item) for item in value]
    return value


def _decode_hot(value: Any) -> Any:
    if isinstance(value, dict):
        fields = value.get(_NAMESPACE_TAG)
        if isinstance(fields, dict) and len(value) == 1:
            return SimpleNamespace(
                **{key: _decode_hot(item) for key, item in fields.items()}
            )
        items = value.get(_TUPLE_TAG)
        if isinstance(items, list) and len(value) == 1:
            return tuple(_decode_hot(item) for item in items)
        return {key: _decode_hot(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_hot(item) for item in value]
    return value


def _cold_with_runtime_identity(engine_id: str, cold: Any) -> Any:
    identity = _RUNTIME_IDENTITIES.get(engine_id)
    if identity is None:
        return cold
    if isinstance(cold, Mapping):
        return {**cold, "_engine_runtime_identity": identity}
    return {"_engine_runtime_identity": identity, "payload": cold}


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


def _prepare_memory_cache() -> None:
    global _MEMORY_CACHE_PID
    pid = os.getpid()
    if _MEMORY_CACHE_PID != pid:
        _MEMORY_CACHE.clear()
        _MEMORY_CACHE_PID = pid


def _remember_memory_value(key: tuple[str, bytes, bool], value: Any) -> None:
    _MEMORY_CACHE[key] = value
    _MEMORY_CACHE.move_to_end(key)
    while len(_MEMORY_CACHE) > _MEMORY_CACHE_MAXSIZE:
        _MEMORY_CACHE.popitem(last=False)


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
                "(key BLOB PRIMARY KEY, value BLOB NOT NULL, engine TEXT NOT NULL, "
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


def _counter_bucket(engine: str) -> dict[str, int]:
    return _COUNTERS.setdefault(engine, dict.fromkeys(_COUNTER_NAMES, 0))


def _record_engine_cache_counters(
    counters: Mapping[str, Mapping[str, int]],
) -> None:
    with _LOCK:
        for engine, values in counters.items():
            target = _counter_bucket(engine)
            for name, value in values.items():
                target.setdefault(name, 0)
                target[name] += int(value)


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
    path_value = _configured_engine_cache_path()
    if not path_value:
        return _hot_and_cold(solve())[0]
    mode = os.environ.get("REGOLITH_ENGINE_CACHE_MODE", "").lower()
    if mode not in {"", "readonly"}:
        raise ValueError(f"unknown engine cache mode {mode!r}")
    readonly = mode == "readonly"
    path = Path(path_value).expanduser()
    chem_version = ENGINE_CHEMISTRY_VERSION[engine_id]
    cache_key = engine_cache_key(engine_id, chem_version, request)
    memory_key = (str(path), cache_key, readonly)
    with _LOCK:
        _prepare_memory_cache()
        counts = _counter_bucket(engine_id)
        if memory_key in _MEMORY_CACHE:
            result = _MEMORY_CACHE[memory_key]
            _MEMORY_CACHE.move_to_end(memory_key)
            counts["hits"] += 1
            return result

    connection = _connect(path, root=path, readonly=readonly, cold=False)
    with _LOCK:
        row = None if connection is None else connection.execute(
            f"SELECT value FROM {_TABLE} WHERE key=?", (cache_key,)
        ).fetchone()
        if row is not None:
            try:
                stored_value = row[0]
                if isinstance(stored_value, str):
                    # Older databases stored uncompressed JSON text.
                    hot_json = stored_value
                else:
                    hot_json = zlib.decompress(stored_value)
                result = _decode_hot(json.loads(hot_json))
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError, zlib.error):
                counts["read_error"] += 1
                row = None
            else:
                counts["hits"] += 1
                _remember_memory_value(memory_key, result)
                return result
        counts["misses"] += 1

    result = solve()
    hot, cold = _hot_and_cold(result)
    if _is_refusal(result) or readonly or connection is None:
        return hot
    try:
        stored_hot = _encode_hot(hot)
        stored_cold = _cold_with_runtime_identity(engine_id, cold)
        hot_value = zlib.compress(
            json.dumps(
                stored_hot,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii"),
            level=9,
        )
        cold_value = zlib.compress(pickle.dumps(stored_cold, protocol=5), level=6)
    except Exception:
        with _LOCK:
            _counter_bucket(engine_id)["store_error"] += 1
        return hot
    source_box = _source_box()
    cold_path = path.with_name(path.name + ".cold")
    cold_connection: sqlite3.Connection | None = None
    try:
        with _LOCK:
            cold_connection = _connect(
                cold_path, root=path, readonly=False, cold=True
            )
            assert cold_connection is not None
            cold_connection.execute("BEGIN IMMEDIATE")
            cold_connection.execute(
                f"INSERT OR REPLACE INTO {_COLD_TABLE} (key, value) VALUES (?, ?)",
                (cache_key, sqlite3.Binary(cold_value)),
            )
            cold_connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            row_seq = _next_row_sequence(connection, source_box)
            connection.execute(
                f"INSERT OR REPLACE INTO {_TABLE} "
                "(key, value, engine, chem_version, source_box, row_seq) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cache_key,
                    sqlite3.Binary(hot_value),
                    engine_id,
                    str(chem_version),
                    source_box,
                    row_seq,
                ),
            )
            connection.commit()
            _counter_bucket(engine_id)["stores"] += 1
    except Exception:
        with _LOCK:
            for failed_connection in (connection, cold_connection):
                if failed_connection is None:
                    continue
                try:
                    failed_connection.rollback()
                except sqlite3.Error:
                    pass
            if cold_connection is not None:
                try:
                    cold_connection.execute(
                        f"DELETE FROM {_COLD_TABLE} WHERE key=?", (cache_key,)
                    )
                    cold_connection.commit()
                except sqlite3.Error:
                    pass
            _counter_bucket(engine_id)["store_error"] += 1
    with _LOCK:
        _prepare_memory_cache()
        _remember_memory_value(memory_key, hot)
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
                sqlite3.Binary(
                    zlib.compress(
                        json.dumps(
                            hot,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=True,
                            allow_nan=False,
                        ).encode("ascii"),
                        level=6,
                    )
                ),
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
