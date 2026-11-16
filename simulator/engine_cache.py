"""One process-local HOT SQLite cache for engine outputs.

When an engine or adapter change can alter predictions, increment that
engine's ``ENGINE_CHEMISTRY_VERSION`` entry by hand and append a dated
history entry below the version map. Runtime identity is diagnostic metadata
only; it never changes or expires cache keys.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
import logging
import os
import sqlite3
import threading
import time
import zlib
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from simulator.engine_cache_binary import EngineCacheValue, live_hot, pack, pack_hot, unpack_hot
from simulator.engine_refusals import cacheable_refusal

ENGINE_CHEMISTRY_VERSION = {
    "openimcc": 1,
    "internal-analytical": 1,
    "vaporock": 1,
    "magemin": 2,
    "alphamelts": 2,
    "thermoengine": 2,
}

# Bump only when an engine change can alter predictions. Runtime identity is
# diagnostic and never participates in the cache key.
# alphamelts v1: 2026-11-08 alphaMELTS 2.3.1 Darwin arm64 binary - initial cache version
# alphamelts v2: 2026-11-11 alphaMELTS 2.3.1, b257r landing 982a8d9fa - canonical activity labels, oxide-vapour projection and typed missing-pO2 refusal (18be4a5cd, b4e6e0dda, 959731207)
# thermoengine v1: 2026-11-08 ThermoEngine df7a5f4609116a83bf40a5a352a19e3826a822d9 - initial cache version
# thermoengine v2: 2026-11-11 ThermoEngine df7a5f4609116a83bf40a5a352a19e3826a822d9 - apply configured redox buffer, fO2 offset and Fe3Fet ratio during backend initialization (bd99e209c)
# magemin v1: 2026-11-08 MAGEMin commit 40ed109d07d4246001cab59382596d33fed79086 - initial cache version
# magemin v2: 2026-11-11 MAGEMin binary pin unchanged, b257r landing 982a8d9fa - d-367 mode-vector closure before liquid-fraction and composition extraction (cd48a78fe)
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
    },
    "alphamelts": {
        "identity": "sha256:d91bd8baee106dee03136e7bf16a9e2c6c17d0dda6c978ff1bfd698c0b073a85",
        "date": "2026-11-08",
    },
    "thermoengine": {
        "identity": "version:1.2.0;record-sha256:462a81b3fda92acf74a1e9e38f260dac65232d855340f72307e05a65219c1b27",
        "date": "2026-11-08",
    },
}

OPENIMCC_CANONICAL_OUTPUT_SHA256 = "2eafdcb2139bb1cce9309ceff1aa0b981b609f305406dff8b1d7c3e3002fa69d"

_TABLE = "engine_outputs"
_CACHE_SCHEMA_VERSION = 2
_ENGINE_NUMBERS = {
    name: number for number, name in enumerate((
        "openimcc", "internal-analytical", "vaporock", "magemin",
        "alphamelts", "thermoengine",
    ), start=1)
}
_NATURAL_KEY_LIMIT = 128
ENGINE_CACHE_BOX_RANGES = {
    box: (index * 1_000_000_000_000 + 1, (index + 1) * 1_000_000_000_000 + 1)
    for index, box in enumerate(("laptop", "s1", "s2", "s3", "rpp"))
}
_TIMEOUT_MS = 60_000
_LOCK = threading.RLock()
_CONNECTIONS: dict[tuple[str, bool], sqlite3.Connection] = {}
_CONNECTION_PID: int | None = None
_CONNECTION_PATH: Path | None = None
_CONNECTION_READONLY = False
_COUNTERS: dict[str, dict[str, int]] = {}
_COUNTER_NAMES = (
    "hits",
    "misses",
    "stores",
    "store_error",
    "read_error",
    "conflicts",
)
_MEMORY_CACHE_MAXSIZE = 65536
_MEMORY_CACHE: OrderedDict[tuple[str, bytes, bool], Any] = OrderedDict()
_MEMORY_CACHE_PID = os.getpid()
_RUNTIME_BACKENDS: dict[str, Any] = {}


class _Bloom:
    """RAM-only negative filter; external commits revoke negative answers."""

    def __init__(self, rows: int, version: int) -> None:
        bit_count = 1 << (max(4096, rows * 10) - 1).bit_length()
        self.bits = bytearray(bit_count // 8)
        self.mask = bit_count - 1
        self.version = version
        self.trusted = True
        self.count = 0

    def _positions(self, key: bytes):
        hashed = hash(key)
        stride = (hashed >> 32) | 1
        for index in range(7):
            yield (hashed + index * stride) & self.mask

    def add(self, key: bytes) -> None:
        for bit in self._positions(key):
            self.bits[bit >> 3] |= 1 << (bit & 7)
        self.count += 1

    def maybe_contains(self, key: bytes) -> bool:
        return not self.trusted or all(
            self.bits[bit >> 3] & (1 << (bit & 7)) for bit in self._positions(key)
        )


_BLOOMS: dict[sqlite3.Connection, _Bloom] = {}


def _build_bloom(connection: sqlite3.Connection) -> _Bloom:
    rows = connection.execute(f"SELECT count(*) FROM {_TABLE}").fetchone()[0]
    bloom = _Bloom(rows, connection.execute("PRAGMA data_version").fetchone()[0])
    for (key,) in connection.execute(f"SELECT key FROM {_TABLE}"):
        bloom.add(bytes(key))
    return bloom


def _record_bloom_key(connection: sqlite3.Connection, key: bytes) -> None:
    bloom = _BLOOMS.get(connection)
    if bloom is not None:
        bloom.add(key)
        if bloom.count * 10 > len(bloom.bits) * 8:
            _BLOOMS[connection] = _build_bloom(connection)


def _definitely_absent(connection: sqlite3.Connection, key: bytes) -> bool:
    bloom = _BLOOMS[connection]
    if bloom.maybe_contains(key):
        return False
    if connection.execute("PRAGMA data_version").fetchone()[0] != bloom.version:
        # Imported rows may arrive out of sequence. Never trust a stale negative.
        bloom.trusted = False
        return False
    return True


@dataclass(frozen=True)
class RuntimeIdentityUnknown:
    """A diagnostic identity could not be read from runtime metadata."""

    engine_id: str
    reason: str
    detail: str


_RUNTIME_IDENTITIES: dict[str, str | RuntimeIdentityUnknown] = {}
_CACHE_BYPASSED: ContextVar[bool] = ContextVar(
    "regolith_engine_cache_bypassed", default=False
)
_ENGINE_CACHE_PATH_UNSET = object()
_ENGINE_CACHE_PATH_OVERRIDE: ContextVar[str | None | object] = ContextVar(
    "regolith_engine_cache_path_override",
    default=_ENGINE_CACHE_PATH_UNSET,
)

ENGINE_SETTINGS_PREDICTION_NEUTRAL_REASONS = {
    "openimcc": {
        "warm_worker": "The OpenIMCC battery adapter has no worker scheduling option."
    },
    "alphamelts": {
        "warm_worker": "The opener fixes warm_worker=False; worker reuse changes scheduling only.",
        "constructor:python_bridge": "The battery opener selects transport through canonical mode and does not forward the legacy alias.",
        "constructor:alphamelts": "The battery opener supplies flat settings and does not forward nested legacy config.",
        "constructor:redox_buffer_fallback": "The opener always supplies fO2_buffer, so the lower-priority redox_buffer fallback is unused.",
        "constructor:fe3fet_ratio_fallback": "The opener always supplies Fe3Fet_Liq, so the lower-priority fe3fet_ratio fallback is unused.",
        "constructor:petthermotools_warm_call_timeout_s": "The opener leaves this optional startup timeout at its fixed backend default.",
        "constructor:timeout_s": "The opener leaves the backend subprocess timeout at its fixed default; request timeout is handled by the battery worker.",
        "constructor:require_petthermotools": "The opener does not request this legacy flag; the canonical mode selects transport.",
        "constructor:calc_mode_conflict": "This flag is derived from the projected model and effective calculation mode.",
        "constructor:model_calc_mode": "This mode is derived from the projected model and is carried only to explain uncached conflicts.",
        "constructor:subprocess_model_error": "This diagnostic is derived from the projected model and is used only for uncached refusals.",
    },
    "vaporock": {
        "warm_worker": "The opener fixes warm_worker=False; worker reuse changes scheduling only."
    },
    "magemin": {
        "warm_worker": "The opener fixes warm_worker=False; worker reuse changes scheduling only."
    },
    "thermoengine": {
        "warm_worker": "The ThermoEngine battery opener does not forward worker scheduling.",
        "constructor:mode": "The battery opener selects ThermoEngine by backend name and uses the constructor's fixed thermoengine default mode.",
        "constructor:alphamelts": "The battery opener supplies flat settings and does not forward nested AlphaMELTS config.",
        "constructor:thermoengine": "The battery opener supplies flat settings and does not forward nested ThermoEngine config.",
        "constructor:redox_buffer_fallback": "The opener always supplies fO2_buffer, so the lower-priority redox_buffer fallback is unused.",
        "constructor:fe3fet_ratio_fallback": "The opener always supplies Fe3Fet_Liq, so the lower-priority fe3fet_ratio fallback is unused.",
        "constructor:thermoengine_health_timeout_s": "The battery opener leaves the health timeout at its fixed operational default.",
        "constructor:thermoengine_equilibrate_timeout_s": "The battery opener leaves the equilibrate timeout at its fixed operational default.",
    },
}


class EngineCacheSchemaMismatch(RuntimeError):
    """The configured cache store has columns this reader cannot use."""

    def __init__(
        self,
        table: str,
        expected_columns: Iterable[str],
        found_columns: Iterable[str],
    ) -> None:
        self.table = table
        self.expected_columns = tuple(expected_columns)
        self.found_columns = tuple(found_columns)
        super().__init__(
            f"engine cache schema mismatch in {table}: expected columns "
            f"{self.expected_columns!r}, found {self.found_columns!r}"
        )


class EngineCacheAliasError(ValueError):
    """A synchronization output aliases an input or another output."""


class EngineCacheSettingsError(ValueError):
    """Engine cache settings do not contain the normalized projection inputs."""

    def __init__(self, engine_name: str, missing_fields: Iterable[str]) -> None:
        self.engine_name = engine_name
        self.missing_fields = tuple(missing_fields)
        super().__init__(
            f"engine cache settings for {engine_name!r} are missing normalized "
            f"fields {self.missing_fields!r}; pass normalize_engine_settings(...) "
            "output"
        )


def engine_cache_key(engine_id: str, chem_version: str | int, request: Any) -> bytes:
    if engine_id not in ENGINE_CHEMISTRY_VERSION:
        raise ValueError(f"unknown engine {engine_id!r}")
    version = int(chem_version)
    if not 0 <= version <= 65535:
        raise ValueError("chemistry version must fit u16")
    try:
        payload = pack(request, key=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("engine request must contain finite canonical scalars") from exc
    header = bytes((_ENGINE_NUMBERS[engine_id],)) + version.to_bytes(2, "big")
    if len(payload) + 4 > _NATURAL_KEY_LIMIT:
        return header + b"\xff" + hashlib.blake2b(payload, digest_size=16).digest()
    return header + b"\x00" + payload


def _tuple_to_list_if_needed(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


_ENGINE_CACHE_SETTINGS_PROJECTIONS: dict[str, dict[str, Any]] = {
    "openimcc": {"pack": "melt_pack", "gas_pack": None},
    "alphamelts": {
        "transport": "transport_mode",
        "effective_calc_mode": None,
        "raw_model_name": "model",
        "python_api_model": _tuple_to_list_if_needed,
        "redox_buffer": None,
        "fO2_offset": "fo2_offset",
        "Fe3Fet_ratio": "fe3fet_ratio",
        "configured_vapor_transport_pO2_bar": "vapor_transport_pO2_bar",
        "configured_vapor_transport_floor_bar": "vapor_transport_floor_bar",
    },
    "vaporock": {
        "temperature_units": None,
        "pressure_units": None,
        "vapor_pressure_units": None,
    },
    "magemin": {"database": None, "bridge": None, "fO2_buffer": None},
    "thermoengine": {
        "model": None,
        "configured_vapor_transport_pO2_bar": "vapor_transport_pO2_bar",
        "configured_vapor_transport_floor_bar": "vapor_transport_floor_bar",
        "redox_buffer": None,
        "fO2_offset": "fo2_offset",
        "Fe3Fet_ratio": "fe3fet_ratio",
    },
}


def engine_cache_settings(
    engine_name: str,
    normalized_settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Project prediction inputs from normalized opener settings."""
    if engine_name == "internal-analytical":
        return {}
    try:
        projection = _ENGINE_CACHE_SETTINGS_PROJECTIONS[engine_name]
    except KeyError as exc:
        raise ValueError(f"unknown engine {engine_name!r}") from exc

    if isinstance(normalized_settings, Mapping):
        missing = tuple(
            name for name in projection if name not in normalized_settings
        )
    else:
        missing = tuple(projection)
    if missing:
        raise EngineCacheSettingsError(engine_name, missing)

    return {
        target if isinstance(target, str) else name: (
            target(normalized_settings[name])
            if callable(target)
            else normalized_settings[name]
        )
        for name, target in projection.items()
    }


def _cache_environment_setting(
    name: str,
    default: str | None = None,
) -> str | None:
    return os.environ.get(name, default)


@contextmanager
def _engine_cache_path_scope(path: str | Path | None) -> Iterator[None]:
    """Set a cache path for one score run without changing process environment."""
    token = _ENGINE_CACHE_PATH_OVERRIDE.set(None if path is None else str(path))
    try:
        yield
    finally:
        _ENGINE_CACHE_PATH_OVERRIDE.reset(token)


def _engine_cache_path_scope_state() -> tuple[bool, str | None]:
    """Return the scoped path override for propagation to spawned score workers."""
    path = _ENGINE_CACHE_PATH_OVERRIDE.get()
    if path is _ENGINE_CACHE_PATH_UNSET:
        return False, None
    return True, cast(str | None, path)


def _set_engine_cache_path_scope_state(
    state: tuple[bool, str | None],
) -> None:
    """Install an explicit scope in a newly spawned, single-run worker."""
    configured, path = state
    if configured:
        _ENGINE_CACHE_PATH_OVERRIDE.set(path)


def _configured_engine_cache_path() -> str | None:
    """Return the configured cache path, or None when caching is disabled."""
    path = _ENGINE_CACHE_PATH_OVERRIDE.get()
    if path is not _ENGINE_CACHE_PATH_UNSET:
        return path or None
    return _cache_environment_setting("REGOLITH_ENGINE_CACHE") or None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _distribution_identity(
    engine_id: str,
    names: tuple[str, ...],
) -> str | RuntimeIdentityUnknown:
    for name in names:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        try:
            record = distribution.read_text("RECORD")
        except (OSError, UnicodeDecodeError) as exc:
            return RuntimeIdentityUnknown(
                engine_id,
                "distribution_metadata_unreadable",
                f"RECORD: {exc}",
            )
        if record is not None and not isinstance(record, str):
            return RuntimeIdentityUnknown(
                engine_id,
                "distribution_metadata_invalid",
                "RECORD is not text",
            )
        record_digest = (
            hashlib.sha256(record.encode("utf-8")).hexdigest()
            if record is not None and len(record) <= 2_000_000
            else "unavailable"
        )
        try:
            version = distribution.version
        except (OSError, UnicodeDecodeError) as exc:
            return RuntimeIdentityUnknown(
                engine_id,
                "distribution_metadata_unreadable",
                f"version: {exc}",
            )
        return f"version:{version};record-sha256:{record_digest}"
    return "unavailable"


def _native_binary_identity(engine_id: str) -> str:
    backend = _RUNTIME_BACKENDS.get(engine_id)
    binary = getattr(backend, "_binary_path", None)
    if binary is None:
        return "unavailable"
    path = Path(binary)
    try:
        digest = _file_sha256(path)
    except OSError:
        return "unavailable"
    return f"sha256:{digest}"


def _openimcc_dist_identity() -> str | RuntimeIdentityUnknown:
    try:
        distribution = importlib.metadata.distribution("openimcc")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"
    try:
        direct_url = distribution.read_text("direct_url.json")
    except (OSError, UnicodeDecodeError) as exc:
        return RuntimeIdentityUnknown(
            "openimcc",
            "distribution_metadata_unreadable",
            f"direct_url.json: {exc}",
        )
    if direct_url:
        try:
            payload = json.loads(direct_url)
        except (TypeError, json.JSONDecodeError) as exc:
            return RuntimeIdentityUnknown(
                "openimcc",
                "distribution_metadata_invalid",
                f"direct_url.json: {exc}",
            )
        if not isinstance(payload, Mapping):
            return RuntimeIdentityUnknown(
                "openimcc",
                "distribution_metadata_invalid",
                "direct_url.json must contain an object",
            )
        vcs_info = payload.get("vcs_info", {})
        if not isinstance(vcs_info, Mapping):
            return RuntimeIdentityUnknown(
                "openimcc",
                "distribution_metadata_invalid",
                "direct_url.json vcs_info must contain an object",
            )
        commit = vcs_info.get("commit_id")
        if commit:
            return f"commit:{commit}"
    try:
        return f"version:{distribution.version}"
    except (OSError, UnicodeDecodeError) as exc:
        return RuntimeIdentityUnknown(
            "openimcc",
            "distribution_metadata_unreadable",
            f"version: {exc}",
        )


def _internal_analytical_identity() -> str:
    backend = _RUNTIME_BACKENDS.get("internal-analytical")
    core = getattr(backend, "_core", backend)
    if core is None:
        return "unavailable"
    try:
        module_file = inspect.getsourcefile(type(core))
    except (OSError, TypeError):
        return "unavailable"
    if not module_file:
        return "unavailable"
    path = Path(module_file)
    try:
        return f"source-sha256:{_file_sha256(path)}"
    except OSError:
        return "unavailable"


def _compute_runtime_identity(engine_id: str) -> str | RuntimeIdentityUnknown:
    if engine_id in {"alphamelts", "magemin"}:
        return _native_binary_identity(engine_id)
    if engine_id == "openimcc":
        return _openimcc_dist_identity()
    if engine_id == "internal-analytical":
        return _internal_analytical_identity()
    if engine_id == "thermoengine":
        return _distribution_identity(
            engine_id, ("ThermoEngine", "thermoengine")
        )
    if engine_id == "vaporock":
        return _distribution_identity(engine_id, ("VapoRock", "vaporock"))
    raise ValueError(f"unknown engine {engine_id!r}")


@lru_cache(maxsize=None)
def runtime_identity(engine_id: str) -> str | RuntimeIdentityUnknown:
    """Return the one per-process runtime identity captured at engine open."""
    return _compute_runtime_identity(engine_id)


def record_engine_open(engine_id: str, backend: Any | None = None) -> str | None:
    """Capture and report staleness once, outside the request-key path."""
    if _configured_engine_cache_path() is None:
        return None
    if engine_id in _RUNTIME_IDENTITIES:
        identity = _RUNTIME_IDENTITIES[engine_id]
        return identity if isinstance(identity, str) else None
    if backend is not None:
        _RUNTIME_BACKENDS.setdefault(engine_id, backend)
    identity = runtime_identity(engine_id)
    if isinstance(identity, RuntimeIdentityUnknown):
        _RUNTIME_IDENTITIES[engine_id] = identity
        logging.getLogger(__name__).warning(
            "engine cache: %s runtime identity unknown (%s; %s)",
            engine_id,
            identity.reason,
            identity.detail,
        )
        return None
    _RUNTIME_IDENTITIES[engine_id] = identity
    recorded = ENGINE_CHEMISTRY_VERSION_IDENTITY_AT[engine_id]
    stored = str(recorded["identity"])
    if identity != stored:
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
    identities = {
        engine: (
            identity
            if isinstance(identity, str)
            else "runtime_identity_unknown:"
            f"{identity.reason}:{identity.detail}"
        )
        for engine, identity in _RUNTIME_IDENTITIES.items()
    }
    if reset:
        _RUNTIME_IDENTITIES.clear()
    return identities


def _encode_hot(value: Any) -> tuple[bytes, bytes]:
    return pack_hot(value)


def _decode_hot(value: bytes, provenance: bytes = b"") -> Any:
    return unpack_hot(value, provenance)


def _close_connections() -> None:
    global _CONNECTION_PID, _CONNECTION_PATH
    for connection in _CONNECTIONS.values():
        try:
            connection.close()
        except sqlite3.Error:
            pass
    _CONNECTIONS.clear()
    _BLOOMS.clear()
    _CONNECTION_PID = os.getpid()
    _CONNECTION_PATH = None


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


def _check_cache_schema(connection: sqlite3.Connection, *, allow_empty: bool = False) -> None:
    expected = ["key", "value", "engine", "chem_version", "source_box", "row_seq", "provenance_id"]
    found = [row[1] for row in connection.execute(f"PRAGMA table_info({_TABLE})")]
    if not found and allow_empty:
        return
    if found != expected:
        raise EngineCacheSchemaMismatch(_TABLE, expected, found)
    namespace = connection.execute("PRAGMA user_version").fetchone()[0]
    if namespace != _CACHE_SCHEMA_VERSION:
        raise EngineCacheSchemaMismatch("user_version", (str(_CACHE_SCHEMA_VERSION),), (str(namespace),))


def _initialize_cache(connection: sqlite3.Connection) -> None:
    connection.execute(f"PRAGMA user_version={_CACHE_SCHEMA_VERSION}")
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {_TABLE} "
        "(key BLOB PRIMARY KEY, value BLOB NOT NULL, engine TEXT NOT NULL, "
        "chem_version TEXT NOT NULL, source_box TEXT NOT NULL, "
        "row_seq INTEGER NOT NULL, provenance_id INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS provenance (id INTEGER PRIMARY KEY, value BLOB NOT NULL UNIQUE)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS box_sequences (source_box TEXT PRIMARY KEY, next_seq INTEGER NOT NULL) WITHOUT ROWID"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS engine_outputs_box_seq ON engine_outputs(source_box, row_seq)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS engine_outputs_engine_version "
        "ON engine_outputs(engine, chem_version)"
    )
    connection.commit()


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection | None:
    global _CONNECTION_PID, _CONNECTION_PATH, _CONNECTION_READONLY
    pid = os.getpid()
    if _CONNECTION_PID != pid:
        _close_connections()
    if _CONNECTION_PATH is not None and _CONNECTION_PATH != path:
        _close_connections()
    if _CONNECTION_PATH is None:
        _CONNECTION_PATH = path
        _CONNECTION_READONLY = readonly
    elif _CONNECTION_READONLY != readonly:
        _close_connections()
        _CONNECTION_PATH = path
        _CONNECTION_READONLY = readonly
    key = (str(path), readonly)
    if key in _CONNECTIONS:
        return _CONNECTIONS[key]
    if readonly:
        if not path.exists():
            return None
        connection = sqlite3.connect(_cache_uri(path), uri=True, timeout=_TIMEOUT_MS / 1000, check_same_thread=False)
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
    try:
        _check_cache_schema(connection, allow_empty=True)
        if not readonly:
            _initialize_cache(connection)
    except Exception:
        connection.close()
        raise
    _CONNECTIONS[key] = connection
    _BLOOMS[connection] = _build_bloom(connection)
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


def _is_refusal(result: Any) -> bool:
    value = result
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


def _insert_cache_row_if_absent(
    connection: sqlite3.Connection,
    digest: bytes,
    value: bytes,
    engine: str,
    chem_version: str,
    source_box: str,
    row_seq: int | None = None,
    *,
    provenance: bytes = b"",
) -> tuple[bool, bool]:
    """Insert one output only when absent; report whether existing bytes conflict."""
    row = connection.execute(
        f"SELECT value, provenance_id FROM {_TABLE} WHERE key=?", (digest,)
    ).fetchone()
    if row is not None:
        existing_provenance = _read_provenance(connection, row[1])
        return False, bytes(row[0]) != bytes(value) or existing_provenance != provenance
    provenance_id = 0
    if provenance:
        connection.execute("INSERT OR IGNORE INTO provenance(value) VALUES (?)", (provenance,))
        provenance_id = connection.execute("SELECT id FROM provenance WHERE value=?", (provenance,)).fetchone()[0]
    if row_seq is None:
        row_seq = _next_row_seq(connection, source_box)
    elif source_box in ENGINE_CACHE_BOX_RANGES:
        _advance_box_sequence(connection, source_box, row_seq)
    connection.execute(
        f"INSERT INTO {_TABLE} "
        "(key, value, engine, chem_version, source_box, row_seq, provenance_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            digest,
            sqlite3.Binary(value),
            engine,
            chem_version,
            source_box,
            row_seq,
            provenance_id,
        ),
    )
    return True, False


def _advance_box_sequence(connection: sqlite3.Connection, source_box: str, row_seq: int) -> None:
    first, stop = ENGINE_CACHE_BOX_RANGES[source_box]
    if not first <= row_seq < stop:
        raise ValueError(f"row sequence is outside the {source_box!r} range")
    connection.execute("INSERT OR IGNORE INTO box_sequences VALUES (?,?)", (source_box, first))
    connection.execute(
        "UPDATE box_sequences SET next_seq=? WHERE source_box=? AND next_seq<=?",
        (row_seq + 1, source_box, row_seq),
    )


def _next_row_seq(connection: sqlite3.Connection, source_box: str) -> int:
    first, stop = ENGINE_CACHE_BOX_RANGES[source_box]
    connection.execute("INSERT OR IGNORE INTO box_sequences VALUES (?,?)", (source_box, first))
    row = connection.execute(
        "UPDATE box_sequences SET next_seq=next_seq+1 WHERE source_box=? AND next_seq<? RETURNING next_seq-1",
        (source_box, stop),
    ).fetchone()
    if row is None:
        raise ValueError(f"row sequence range exhausted for {source_box!r}")
    return int(row[0])


def _read_provenance(connection: sqlite3.Connection, provenance_id: int) -> bytes:
    if not provenance_id:
        return b""
    row = connection.execute("SELECT value FROM provenance WHERE id=?", (provenance_id,)).fetchone()
    if row is None:
        raise ValueError("missing cache provenance")
    return bytes(row[0])


def _initialize_cold(connection: sqlite3.Connection, schema: str = "main") -> None:
    connection.execute(f"CREATE TABLE IF NOT EXISTS {schema}.engine_values "
                       "(key BLOB PRIMARY KEY, value BLOB NOT NULL) WITHOUT ROWID")


def _store_cold(path: Path, key: bytes, values: Mapping[str, Any]) -> None:
    if not values:
        return
    cold_path = str(path) + ".cold"
    connection_key = (cold_path, False)
    connection = _CONNECTIONS.get(connection_key)
    if connection is None:
        connection = sqlite3.connect(cold_path, timeout=_TIMEOUT_MS / 1000, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        _initialize_cold(connection)
        _CONNECTIONS[connection_key] = connection
    value = zlib.compress(pack(values), level=6)
    connection.execute("INSERT OR IGNORE INTO engine_values VALUES (?,?)", (key, value))
    connection.commit()


def cached_engine_call(engine_id: str, request: Any, solve: Any) -> Any:
    if _CACHE_BYPASSED.get():
        return live_hot(solve())
    path_value = _configured_engine_cache_path()
    if not path_value:
        return live_hot(solve())
    mode = (_cache_environment_setting("REGOLITH_ENGINE_CACHE_MODE", "") or "").lower()
    if mode not in {"", "readonly"}:
        raise ValueError(f"unknown engine cache mode {mode!r}")
    readonly = mode == "readonly"
    source_box = _cache_environment_setting("REGOLITH_ENGINE_CACHE_BOX", "") or ""
    if not readonly:
        if not source_box.strip():
            raise ValueError("REGOLITH_ENGINE_CACHE_BOX must be set in read-write mode")
        if source_box not in ENGINE_CACHE_BOX_RANGES:
            raise ValueError(f"engine cache box {source_box!r} has no configured row range")
    chem_version = ENGINE_CHEMISTRY_VERSION[engine_id]
    cache_key = engine_cache_key(engine_id, chem_version, request)
    memory_key = (path_value, cache_key, readonly)
    with _LOCK:
        _prepare_memory_cache()
        counts = _counter_bucket(engine_id)
        if memory_key in _MEMORY_CACHE:
            result = _MEMORY_CACHE[memory_key]
            _MEMORY_CACHE.move_to_end(memory_key)
            counts["hits"] += 1
            return result

    path = Path(path_value).expanduser()
    try:
        connection = _connect(path, readonly=readonly)
    except (sqlite3.Error, OSError):
        with _LOCK:
            counts["read_error"] += 1
            counts["misses"] += 1
        return live_hot(solve())
    with _LOCK:
        try:
            row = None if connection is None or _definitely_absent(connection, cache_key) else connection.execute(
                f"SELECT value, provenance_id FROM {_TABLE} WHERE key=?", (cache_key,)
            ).fetchone()
        except sqlite3.Error:
            counts["read_error"] += 1
            row = None
        if row is not None:
            try:
                result = _decode_hot(row[0], _read_provenance(connection, row[1]))
            except (TypeError, ValueError, UnicodeDecodeError):
                counts["read_error"] += 1
                row = None
            else:
                counts["hits"] += 1
                _remember_memory_value(memory_key, result)
                return result
        counts["misses"] += 1

    solved = solve()
    hot = live_hot(solved)
    if (_is_refusal(hot) and not cacheable_refusal(hot)) or readonly or connection is None:
        return hot
    try:
        if not isinstance(solved, EngineCacheValue) and (
            isinstance(hot, Mapping) and any(isinstance(item, (Mapping, list, tuple)) for item in hot.values())
            or isinstance(hot, (list, tuple))
        ):
            raise TypeError("result has no declared HOT projection")
        hot_value, provenance = _encode_hot(solved.hot if isinstance(solved, EngineCacheValue) else solved)
    except (TypeError, ValueError):
        with _LOCK:
            counts = _counter_bucket(engine_id)
            name = f"uncacheable:{type(hot).__name__}"
            counts[name] = counts.get(name, 0) + 1
        return hot
    try:
        with _LOCK:
            connection.execute("BEGIN IMMEDIATE")
            inserted, conflict = _insert_cache_row_if_absent(
                connection,
                cache_key,
                hot_value,
                engine_id,
                str(chem_version),
                source_box,
                provenance=provenance,
            )
            # Snapshot locks on HOT must also cover completion of its COLD data.
            if inserted and isinstance(solved, EngineCacheValue):
                _store_cold(path, cache_key, solved.cold)
            connection.commit()
            if inserted:
                _record_bloom_key(connection, cache_key)
            counts = _counter_bucket(engine_id)
            if inserted:
                counts["stores"] += 1
            if conflict:
                counts["conflicts"] += 1
    except Exception:
        with _LOCK:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            _counter_bucket(engine_id)["store_error"] += 1
    with _LOCK:
        _prepare_memory_cache()
        _remember_memory_value(memory_key, hot)
    return hot


@contextmanager
def _without_engine_cache():
    """Bypass nested engine cache calls for an uncached parent calculation."""
    token = _CACHE_BYPASSED.set(True)
    try:
        yield
    finally:
        _CACHE_BYPASSED.reset(token)


def load_cache_rows(
    path: str | Path,
    rows: Iterable[tuple[bytes, Any, str, str | int, str, int]],
) -> int:
    database = Path(path).expanduser()
    connection = _connect(database, readonly=False)
    assert connection is not None
    values = []
    for digest, hot, engine, version, source_box, row_seq in rows:
        if not isinstance(digest, bytes) or not 4 <= len(digest) <= _NATURAL_KEY_LIMIT:
            raise ValueError("cache keys must be natural packed keys or 16-byte request digests")
        if engine not in ENGINE_CHEMISTRY_VERSION:
            raise ValueError(f"unknown engine {engine!r}")
        payload = hot.hot if isinstance(hot, EngineCacheValue) else hot
        if _is_refusal(payload) and not cacheable_refusal(live_hot(payload)):
            raise ValueError("refusal code is not cacheable")
        encoded, provenance = _encode_hot(payload)
        values.append(
            (
                digest,
                encoded,
                engine,
                str(version),
                source_box,
                int(row_seq),
                provenance,
            )
        )
    inserted_count = 0
    conflicts: dict[str, int] = {}
    connection.execute("BEGIN IMMEDIATE")
    try:
        for digest, value, engine, version, source_box, row_seq, provenance in values:
            inserted, conflict = _insert_cache_row_if_absent(
                connection,
                digest,
                value,
                engine,
                version,
                source_box,
                row_seq,
                provenance=provenance,
            )
            inserted_count += int(inserted)
            if inserted:
                _record_bloom_key(connection, digest)
            if conflict:
                conflicts[engine] = conflicts.get(engine, 0) + 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    if conflicts:
        _record_engine_cache_counters(
            {engine: {"conflicts": count} for engine, count in conflicts.items()}
        )
    return inserted_count


def _cache_pair(path: Path) -> tuple[Path, Path]:
    return path, Path(str(path) + ".cold")


def _refuse_cache_aliases(outputs: Iterable[Path], inputs: Iterable[Path]) -> None:
    outputs, inputs = list(outputs), list(inputs)
    for index, output in enumerate(outputs):
        for other in inputs + outputs[:index]:
            if output.resolve() == other.resolve() or (
                output.exists() and other.exists() and output.samefile(other)
            ):
                raise EngineCacheAliasError(f"cache output aliases another file: {output} -> {other}")


def _cache_uri(path: Path, mode: str = "ro") -> str:
    return f"file:{quote(str(path.resolve()), safe='/')}?mode={mode}"


def _check_cold_schema(connection: sqlite3.Connection) -> None:
    expected = ["key", "value"]
    found = [row[1] for row in connection.execute("PRAGMA cold.table_info(engine_values)")]
    if found != expected:
        raise EngineCacheSchemaMismatch("cold.engine_values", expected, found)
    # The lean COLD format has always used SQLite's default namespace (0).
    namespace = connection.execute("PRAGMA cold.user_version").fetchone()[0]
    if namespace != 0:
        raise EngineCacheSchemaMismatch("cold.user_version", ("0",), (str(namespace),))


@contextmanager
def _read_cache(path: Path):
    with closing(sqlite3.connect(_cache_uri(path), uri=True, timeout=_TIMEOUT_MS / 1000)) as connection:
        cold = _cache_pair(path)[1]
        has_cold = cold.exists()
        if has_cold:
            connection.execute("ATTACH DATABASE ? AS cold", (_cache_uri(cold),))
        connection.execute("BEGIN")
        _check_cache_schema(connection)
        # Pin both read views, including schemas needed to interpret the HOT bytes.
        connection.execute("SELECT id,value FROM provenance LIMIT 0")
        connection.execute("SELECT source_box,next_seq FROM box_sequences LIMIT 0")
        if has_cold:
            _check_cold_schema(connection)
        yield connection, has_cold


def _cache_space(connection: sqlite3.Connection, schema: str, table: str) -> dict[str, Any]:
    count = connection.execute(f"SELECT count(*) FROM {schema}.{table}").fetchone()[0]
    size = (connection.execute(f"PRAGMA {schema}.page_count").fetchone()[0]
            * connection.execute(f"PRAGMA {schema}.page_size").fetchone()[0])
    return {"rows": count, "database_bytes": size, "bytes_per_row": size / count if count else None}


def engine_cache_stats(path: Path) -> dict[str, Any]:
    """Allocated SQLite bytes, including pages currently in WAL, per stored row."""
    with _read_cache(path) as (connection, has_cold):
        return {
            "file": str(path),
            "hot": _cache_space(connection, "main", _TABLE),
            "cold": _cache_space(connection, "cold", "engine_values") if has_cold else {
                "rows": 0, "database_bytes": 0, "bytes_per_row": None,
            },
        }


def snapshot_cache(live: Path, out: Path) -> dict[str, Any]:
    """Back up a live HOT/COLD pair into exclusively new, standalone files."""
    live, out = live.expanduser(), out.expanduser()
    _refuse_cache_aliases(_cache_pair(out), _cache_pair(live))
    for path in _cache_pair(out):
        if path.exists() or path.is_symlink():
            raise FileExistsError(str(path))
    with ExitStack() as stack:
        # Serialize with the runtime's HOT transaction while pinning both views.
        # Release the lock before the potentially large backup; readers retain WAL snapshots.
        with closing(sqlite3.connect(_cache_uri(live, "rw"), uri=True, timeout=_TIMEOUT_MS / 1000)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            source, has_cold = stack.enter_context(_read_cache(live))
            lock.rollback()
        for name, path in (("main", out), ("cold", _cache_pair(out)[1])):
            if name == "cold" and not has_cold:
                continue
            with path.open("xb"):
                pass
            with closing(sqlite3.connect(path)) as destination:
                source.backup(destination, name=name)
                destination.execute("PRAGMA journal_mode=DELETE")
        if _cache_pair(out)[1].exists():
            with closing(sqlite3.connect(_cache_pair(out)[1])) as cold:
                cold.execute("ATTACH DATABASE ? AS hot", (_cache_uri(out),))
                # A failed live solve may have committed COLD before rolling back HOT.
                cold.execute("DELETE FROM engine_values WHERE NOT EXISTS ("
                             "SELECT 1 FROM hot.engine_outputs h WHERE h.key=engine_values.key)")
                cold.commit()
    return engine_cache_stats(out)


def merge_cache_files(into: Path, inputs: list[Path], *, vacuum: bool = False) -> dict[str, Any]:
    """Accumulate new per-box sequences without replacing a natural-key value."""
    into = into.expanduser()
    inputs = [path.expanduser() for path in inputs]
    report_path = Path(str(into) + ".conflicts.jsonl")
    _refuse_cache_aliases((*_cache_pair(into), report_path),
                         [part for path in inputs for part in _cache_pair(path)])
    counts = dict.fromkeys(("read", "inserted", "already_present", "conflicts"), 0)
    with ExitStack() as stack:
        # All inputs are validated before creating a master or a conflict report.
        sources = [stack.enter_context(_read_cache(path)) for path in inputs]
        into.parent.mkdir(parents=True, exist_ok=True)
        destination = stack.enter_context(closing(sqlite3.connect(into, timeout=_TIMEOUT_MS / 1000, uri=True)))
        _check_cache_schema(destination, allow_empty=True)
        cold_path = _cache_pair(into)[1]
        cold_exists = cold_path.exists()
        destination.execute("ATTACH DATABASE ? AS cold", (str(cold_path),))
        if cold_exists:
            _check_cold_schema(destination)
        _initialize_cache(destination)
        destination.execute("PRAGMA journal_mode=WAL")
        destination.execute("PRAGMA cold.journal_mode=WAL")
        _initialize_cold(destination, "cold")
        destination.execute("CREATE TABLE IF NOT EXISTS sync_watermarks "
                            "(source_box TEXT PRIMARY KEY, row_seq INTEGER NOT NULL) WITHOUT ROWID")
        destination.commit()
        report = stack.enter_context(report_path.open("a", encoding="utf-8"))
        destination.execute("BEGIN IMMEDIATE")
        try:
            for source, has_cold in sources:
                for box, next_seq in source.execute("SELECT source_box,next_seq FROM box_sequences"):
                    if box not in ENGINE_CACHE_BOX_RANGES:
                        raise ValueError(f"unknown cache source box {box!r}")
                    watermark = destination.execute(
                        "SELECT row_seq FROM sync_watermarks WHERE source_box=?", (box,),
                    ).fetchone()
                    last = watermark[0] if watermark else 0
                    cursor = source.execute(
                        "SELECT e.key,e.value,e.engine,e.chem_version,e.row_seq,COALESCE(p.value,X'') "
                        "FROM engine_outputs e LEFT JOIN provenance p ON p.id=e.provenance_id "
                        "WHERE e.source_box=? AND e.row_seq>? ORDER BY e.row_seq", (box, last),
                    )
                    for key, value, engine, version, seq, provenance in cursor:
                        counts["read"] += 1
                        incoming_cold = source.execute(
                            "SELECT value FROM cold.engine_values WHERE key=?", (key,),
                        ).fetchone() if has_cold else None
                        kept_cold = destination.execute("SELECT value FROM cold.engine_values WHERE key=?", (key,)).fetchone()
                        inserted, conflict = _insert_cache_row_if_absent(
                            destination, key, value, engine, version, box, seq, provenance=provenance,
                        )
                        conflict = conflict or bool(incoming_cold and kept_cold and incoming_cold != kept_cold)
                        counts["inserted"] += int(inserted)
                        counts["conflicts"] += int(conflict)
                        counts["already_present"] += int(not inserted and not conflict)
                        if conflict:
                            kept_box, kept_seq = destination.execute(
                                "SELECT source_box,row_seq FROM engine_outputs WHERE key=?", (key,),
                            ).fetchone()
                            report.write(json.dumps({
                                "key": key.hex(), "master": {"source_box": kept_box, "row_seq": kept_seq},
                                "input": {"source_box": box, "row_seq": seq},
                            }, sort_keys=True) + "\n")
                        elif incoming_cold:
                            destination.execute("INSERT OR IGNORE INTO cold.engine_values VALUES (?,?)", (key, incoming_cold[0]))
                        last = seq
                    if last > (watermark[0] if watermark else 0):
                        destination.execute(
                            "INSERT INTO sync_watermarks VALUES (?,?) ON CONFLICT(source_box) "
                            "DO UPDATE SET row_seq=excluded.row_seq", (box, last),
                        )
                    # Seeded boxes must never reuse sequences, including deduplicated rows.
                    if next_seq > ENGINE_CACHE_BOX_RANGES[box][0]:
                        _advance_box_sequence(destination, box, next_seq - 1)
            report.flush()
            destination.commit()
        except Exception:
            destination.rollback()
            raise
        if vacuum:
            destination.execute("VACUUM main")
            destination.execute("VACUUM cold")
    return {**counts, **engine_cache_stats(into), "conflict_report": str(report_path), "vacuumed": vacuum}


def drop_chemistry_version(engine_id: str, chem_version: str | int) -> int:
    path_value = _configured_engine_cache_path()
    if not path_value:
        return 0
    path = Path(path_value).expanduser()
    connection = _connect(path, readonly=False)
    assert connection is not None
    cursor = connection.execute(
        f"DELETE FROM {_TABLE} WHERE engine=? AND chem_version=?",
        (engine_id, str(chem_version)),
    )
    connection.commit()
    with _LOCK:
        for memory_key in tuple(_MEMORY_CACHE):
            if memory_key[0] == path_value:
                del _MEMORY_CACHE[memory_key]
    return cursor.rowcount
