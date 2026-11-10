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
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import ModuleType
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

# Each omitted field has an exact-name reason. Chemistry-bearing objects such
# as _core, _pack, _gas, _package, worker pools, and workers are walked; unknown
# fields stay in the request and can cause harmless extra misses.
_RUNTIME_ONLY_SETTING_REASONS = {
    "_available": "Backend availability affects refusal handling, not successful predictions.",
    "_unavailable_reason": "Backend availability detail affects refusal handling, not successful predictions.",
    "_last_error": "This is the previous call's error state, not an input.",
    "_warnings": "These are diagnostics from initialization or a prior call.",
    "_logger": "Logger state is operational and does not change engine inputs.",
    "logger": "Logger state is operational and does not change engine inputs.",
    "_diagnostic_log": "The diagnostic output destination does not change engine inputs.",
    "_startup_failure_disposition": "This is the previous worker startup outcome.",
    "_last_pressure_authority_warning": "This is a diagnostic from a prior call.",
    "_last_vapor_pressure_diagnostic": "This contains the previous call's vapor output and diagnostics.",
    "_last_vapor_pressures_source": "This contains source labels from the previous vapor-pressure call.",
    "_last_high_t_melt_activity_temperature_K": "This records the previous call's high-temperature activity state.",
    "_last_high_t_melt_activity": "This records the previous call's high-temperature activity output.",
    "_high_t_melt_activity_fallback_steps": "These record the previous call's high-temperature fallback diagnostics.",
    "_chem_kernel": "This per-call kernel is derived from keyed setpoints and request inputs.",
    "atom_ledger": "The direct residue adapter rebuilds this ledger from composition already present in the request.",
    "_last_empty_speciation_cause": "This is a diagnostic from a prior call.",
    "_last_empty_speciation_stats": "This is a diagnostic from a prior call.",
    "_last_empty_speciation_reason": "This is a diagnostic from a prior call.",
    "_last_empty_speciation_error": "This is a diagnostic from a prior call.",
    "_last_silent_zero_notes": "These are diagnostics from a prior call.",
    "_binary_path": "The selected native runtime is diagnostic; chemistry changes use the explicit chemistry version.",
    "binary_path": "The selected native runtime is diagnostic; chemistry changes use the explicit chemistry version.",
    "_engine_root": "The native runtime location is diagnostic; chemistry changes use the explicit chemistry version.",
    "engine_root": "The native runtime location is diagnostic; chemistry changes use the explicit chemistry version.",
    "diagnostic_path": "This path receives logs and does not affect engine inputs.",
    "working_directory": "The process working directory is operational, not a prediction input.",
    "workdir": "The process working directory is operational, not a prediction input.",
    "_engine_version": "Runtime version text is diagnostic; chemistry changes use the explicit chemistry version.",
    "engine_version": "Runtime version text is diagnostic; chemistry changes use the explicit chemistry version.",
    "runtime_identity": "Runtime identity is diagnostic; chemistry changes use the explicit chemistry version.",
    "ready_payload": "The worker handshake reports runtime readiness and is not an engine input.",
    "_transport_close_count": "This counts lifecycle operations and does not affect engine inputs.",
    "_health_timeout_s": "This timeout only bounds runtime health checks.",
    "_equilibrate_timeout_s": "This timeout only bounds worker calls.",
    "_warm_call_timeout_s": "This timeout only bounds worker calls.",
    "_worker_startup_timeout_s": "This timeout only bounds worker startup.",
    "_pool_size": "Worker count changes scheduling, not prediction inputs.",
    "_cache": "This is cache implementation state, not an engine input.",
    "_cache_path": "The cache location does not change engine inputs.",
    "_process": "Process lifecycle state is operational and not an engine input.",
    "process": "Process lifecycle state is operational and not an engine input.",
    "pid": "A process identifier does not change engine inputs.",
    "returncode": "A process exit status describes a completed worker, not an input.",
    "stderr": "Captured process diagnostics do not change engine inputs.",
    "stdout": "Captured process diagnostics do not change engine inputs.",
    "call_timeout_s": "This timeout only bounds worker calls.",
    "startup_timeout_s": "This timeout only bounds worker startup.",
    "warm_call_timeout_s": "This timeout only bounds worker calls.",
    "worker_startup_timeout_s": "This timeout only bounds worker startup.",
    "warm_pool_size": "Worker count changes scheduling, not prediction inputs.",
    "_warm_pool_size": "Worker count changes scheduling, not prediction inputs.",
    "_warm_worker_enabled": "Warm worker use changes scheduling, not prediction inputs.",
    "warm_worker": "Warm worker use changes scheduling, not prediction inputs.",
    "pool_size": "Worker count changes scheduling, not prediction inputs.",
    "worker_count": "Worker count changes scheduling, not prediction inputs.",
    "slots": "Worker count changes scheduling, not prediction inputs.",
    "_pack_name": "The effective pack identity is serialized separately for the loaded OpenIMCC pack.",
    "call_count": "This counter records completed worker calls and does not change engine inputs.",
    "_queue": "Queued requests are transient scheduling state, not backend configuration.",
    "_threads": "Worker threads are transient scheduling state.",
    "_active_requests": "Active requests are transient scheduling state.",
    "_request_workers": "Request-to-worker assignments are transient scheduling state.",
    "_diagnostic_written_futures": "Future diagnostics are transient scheduling state.",
    "_active_requests_lock": "This lock is process synchronization state.",
    "_lifecycle_lock": "This lock is process synchronization state.",
    "_close_complete": "This event is process lifecycle state.",
    "_cancel_pending": "This event is process lifecycle state.",
    "_pool_seq": "This number only names worker threads.",
    "_state_lock": "This lock is process synchronization state.",
    "_pending_reap": "Pending process cleanup is lifecycle state.",
    "_native_output_capture": "Native output capture is diagnostic process state.",
    "_pool_call_abort_event": "This event coordinates a transient worker call.",
    "_active_frame_readers": "Frame reader counts are transient transport state.",
    "_close_after_frame_read": "Deferred connection closes are transient transport state.",
    "_closed": "Pool lifecycle state does not change engine inputs.",
    "_lock": "This lock is process synchronization state.",
    "lock": "This lock is process synchronization state.",
    "_condition": "This condition is process synchronization state.",
    "_condition_lock": "This lock is process synchronization state.",
    "_bootstrap_args[0]": "The executable location is runtime metadata; chemistry changes use the explicit chemistry version.",
    "diagnostic_log_path": "Worker log destinations do not change engine inputs.",
    "_diagnostic_log_path": "Worker log destinations do not change engine inputs.",
    "diagnostic_signal": "Diagnostic signal selection affects reporting, not engine inputs.",
    "watchdog_grace_s": "Worker watchdog timing changes process management, not prediction inputs.",
    "_watchdog_grace_s": "Worker watchdog timing changes process management, not prediction inputs.",
    "_diagnostic_signal": "Diagnostic signal selection affects reporting, not engine inputs.",
    "daemon": "Worker daemon mode changes lifecycle, not prediction inputs.",
    "connection": "The worker connection is transient transport state.",
    "start_count": "Worker start counts describe lifecycle state only.",
    "disabled": "Worker availability changes refusal handling, not successful predictions.",
    "_operation_sequence": "The operation counter names native requests and does not change prediction inputs.",
    "_operation_parent": "The native operation label is diagnostic process metadata.",
    "_operation_roots": "Native operation roots are transient resource cleanup state.",
    "_health_cache": "Cached health checks describe worker availability, not prediction inputs.",
    "__builtins__": "Interpreter builtins are not engine settings.",
    "__file__": "Module source locations are diagnostic; chemistry changes use the explicit chemistry version.",
    "__cached__": "Module bytecode locations are runtime metadata.",
    "__loader__": "The module loader is runtime metadata.",
    "__spec__": "The module import specification is runtime metadata.",
    "__package__": "The module package name is runtime metadata.",
    "_identity.version": "OpenIMCC runtime version text is diagnostic; chemistry changes use the explicit chemistry version.",
    "_identity.pack_version": "The selected pack is keyed by its effective pack name; chemistry changes use the explicit chemistry version.",
    "_identity.pack_digest": "Pack digests are diagnostic; chemistry changes use the explicit chemistry version.",
    "_identity.openimcc_commit": "The runtime commit is diagnostic; chemistry changes use the explicit chemistry version.",
    "_identity.engine_binding_identity": "Binding identity is diagnostic; chemistry changes use the explicit chemistry version.",
    "melt.composition_kg": "The direct residue request keys the composition and rebuilds this melt field.",
    "melt.temperature_C": "The direct residue request keys temperature and assigns it to this melt field.",
    "melt.atmosphere": "The direct residue request keys the oxygen condition and assigns this controlled atmosphere.",
    "melt.pO2_mbar": "The direct residue request keys pO2 and assigns it to this melt field.",
    "melt.p_total_mbar": "The direct residue request keys pressure and assigns it to this melt field.",
    "melt.fO2_log": "The direct residue request keys fO2 and assigns it to this melt field.",
    "melt.melt_fO2_log": "The direct residue request keys fO2 and assigns it to this melt field.",
    "melt.total_mass_kg": "This total is derived from the request composition assigned to the melt.",
    "melt.oxygen_reservoir.melt_intrinsic_fO2_log": "The direct residue request keys fO2 and assigns this oxygen state.",
    "melt.oxygen_reservoir.headspace_ledger_pO2_bar": "The direct residue request keys pO2 and assigns this oxygen state.",
    "melt.oxygen_reservoir.headspace_transport_pO2_bar": "The direct residue request keys pO2 and assigns this oxygen state.",
}


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


def _simple_cache_setting(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _setting_drop_reason(path: tuple[Any, ...]) -> str | None:
    if not path:
        return None
    if len(path) > 1 and str(path[-2]) == "_bootstrap_args" and path[-1] == 0:
        return _RUNTIME_ONLY_SETTING_REASONS["_bootstrap_args[0]"]
    path_names = tuple(str(part) for part in path)
    for start in range(len(path_names) - 1):
        scoped_name = ".".join(path_names[start:])
        if scoped_name in _RUNTIME_ONLY_SETTING_REASONS:
            return _RUNTIME_ONLY_SETTING_REASONS[scoped_name]
    name = str(path[-1])
    if len(path) > 1 and str(path[-2]) == "_identity":
        scoped_name = f"_identity.{name}"
        if scoped_name in _RUNTIME_ONLY_SETTING_REASONS:
            return _RUNTIME_ONLY_SETTING_REASONS[scoped_name]
    return _RUNTIME_ONLY_SETTING_REASONS.get(name)


def _setting_type_name(value: Any) -> str:
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def _object_settings(value: Any) -> dict[str, Any] | None:
    try:
        attributes = dict(vars(value))
    except TypeError:
        attributes = {}
    for cls in type(value).__mro__:
        slots = getattr(cls, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for name in slots:
            if name not in {"__dict__", "__weakref__"} and hasattr(value, name):
                attributes[name] = getattr(value, name)
    if attributes or type(value) is object:
        return attributes
    return None


def _settings_value(
    value: Any,
    seen: dict[int, tuple[Any, ...]],
    dropped: dict[tuple[Any, ...], str],
    path: tuple[Any, ...] = (),
) -> Any:
    if isinstance(value, Enum):
        return _settings_value(value.value, seen, dropped, path)
    if _simple_cache_setting(value):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if type(value).__module__.split(".", 1)[0] == "numpy":
        tolist = getattr(value, "tolist", None)
        if callable(tolist):
            return _settings_value(tolist(), seen, dropped, path)
        item = getattr(value, "item", None)
        if callable(item):
            return _settings_value(item(), seen, dropped, path)
    if isinstance(value, ModuleType):
        # A module is executable implementation code; chemistry changes to it
        # are represented by the engine's opt-in chemistry version in the key.
        return {"$module": value.__name__}
    if callable(value):
        return {
            "$callable": f"{getattr(value, '__module__', '')}."
            f"{getattr(value, '__qualname__', type(value).__qualname__)}"
        }

    marker = id(value)
    if marker in seen:
        return {"$ref": list(seen[marker])}
    seen[marker] = path

    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            child_path = (*path, name)
            reason = _setting_drop_reason(child_path)
            if reason is not None:
                dropped[child_path] = reason
                continue
            output[name] = _settings_value(child, seen, dropped, child_path)
        return output
    if isinstance(value, (tuple, list)):
        return [
            _settings_value(child, seen, dropped, (*path, index))
            for index, child in enumerate(value)
        ]
    if isinstance(value, (set, frozenset)):
        ordered = sorted(value, key=repr)
        return [
            _settings_value(child, seen, dropped, (*path, index))
            for index, child in enumerate(ordered)
        ]

    attributes = _object_settings(value)
    if attributes is None:
        raise TypeError(
            f"cannot inspect engine backend setting at {path!r}: "
            f"{_setting_type_name(value)}"
        )
    output = {}
    try:
        effective_pack_name = getattr(value, "_effective_pack_name")
    except AttributeError:
        effective_pack_name = _MISSING
    for name, child in attributes.items():
        child_path = (*path, name)
        if (
            name == "_bootstrap_args"
            and isinstance(child, (tuple, list))
            and child
            and isinstance(child[0], str)
            and Path(child[0]).is_absolute()
        ):
            dropped[(*child_path, 0)] = _RUNTIME_ONLY_SETTING_REASONS[
                "_bootstrap_args[0]"
            ]
            child = (None, *child[1:])
        if name == "_pack_name" and effective_pack_name is not _MISSING:
            dropped[child_path] = _RUNTIME_ONLY_SETTING_REASONS["_pack_name"]
            continue
        reason = _setting_drop_reason(child_path)
        if reason is not None:
            dropped[child_path] = reason
            continue
        output[name] = _settings_value(child, seen, dropped, child_path)
    if effective_pack_name is not _MISSING:
        output["_effective_pack_name"] = _settings_value(
            effective_pack_name, seen, dropped, (*path, "_effective_pack_name")
        )
    if not attributes and not output:
        return {"$type": _setting_type_name(value)}
    return output


_MISSING = object()


def _engine_cache_backend_settings_with_drops(
    backend: Any,
) -> tuple[dict[str, Any], dict[tuple[Any, ...], str]]:
    dropped: dict[tuple[Any, ...], str] = {}
    settings = _settings_value(backend, {}, dropped)
    return (settings if isinstance(settings, dict) else {}), dropped


def engine_cache_backend_settings(backend: Any) -> dict[str, Any]:
    """Collect backend inputs recursively, omitting only named runtime fields."""
    settings, _dropped = _engine_cache_backend_settings_with_drops(backend)
    return settings


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
