"""Determinism probes for the uncached pass-2 refusal classes (t-1917).

Each ``scripts/probe_t1917_*.py`` entry point takes no arguments and selects
its class from ``SCRIPT_CLASSES``. Run it from the repository root on the
studio that has that engine. Discovery walks a battery score with the warm
cache read-only, keeps up to 50 requests whose cache counter is the target
class, and replays each request three times serially in a fresh process with
the cache off.

The probe does not change the refusal allowlist. A class is cacheable only
after this report says ``all_identical`` and ``all_remained_target``. A
timeout that is not a deterministic full-budget hang stays uncacheable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import pickle
import signal
import socket
import subprocess
import sys
import time
import traceback
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SAMPLE_CAP = 50
REPEAT_COUNT = 3
DISCOVERY_CHUNK = 64

# Measured pass-2 counters. The filename is the no-arg entry point.
SCRIPT_CLASSES: dict[str, tuple[str, str, str]] = {
    "probe_t1917_alphamelts_engine_crash.py": (
        "alphamelts", "refusal", "engine_crash",
    ),
    "probe_t1917_magemin_engine_worker_timeout.py": (
        "magemin", "exception", "EngineWorkerTimeout",
    ),
    "probe_t1917_magemin_engine_not_converged.py": (
        "magemin", "refusal", "engine_not_converged",
    ),
    "probe_t1917_magemin_not_converged.py": (
        "magemin", "refusal", "not_converged",
    ),
    "probe_t1917_thermoengine_nonfinite.py": (
        "thermoengine", "exception", "ThermoEngineNonFiniteField",
    ),
    "probe_t1917_thermoengine_out_of_domain.py": (
        "thermoengine", "exception", "ThermoEngineOutOfDomainError",
    ),
    "probe_t1917_internal_analytical_oxygen_balance.py": (
        "internal-analytical",
        "exception",
        "internal_analytical_oxygen_balance_unavailable",
    ),
}

# Child-process backstop, longer than the engine's own call budget.
# A kill here means that budget did not fire. It is not a cache decision.
_REPLAY_BACKSTOP_S = {
    "alphamelts": 360.0,
    "magemin": 360.0,
    "thermoengine": 600.0,
    "internal-analytical": 900.0,
}

_CHILD_FLAG = "T1917_PROBE_CHILD"
# Set by the patched equilibrate and read by the cache hook after solve()
# returns. Cleared at the start of each cache call so a hit cannot inherit it.
_LAST_TIMEOUT: dict[str, float | None] = {"value": None}


def _counter_key(kind: str, token: str) -> str:
    return f"not_stored:{kind}:{token}"


def _classes_for_engine(engine: str) -> tuple[tuple[str, str], ...]:
    found = []
    for candidate, kind, token in SCRIPT_CLASSES.values():
        if candidate == engine and (kind, token) not in found:
            found.append((kind, token))
    return tuple(found)


def _json_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _token(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 80:
        return None
    if any(character.isspace() for character in value):
        return None
    return value


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _request_identity(request: Mapping[str, Any], call_timeout_s: float | None) -> str:
    payload = json.dumps(
        {"request": _jsonable(request), "call_timeout_s": call_timeout_s},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _named_number(mapping: Mapping[str, Any], name: str) -> float | None:
    for key, value in mapping.items():
        if str(key) == name:
            return _json_float(value)
    return None


def request_features(request: Mapping[str, Any]) -> dict[str, Any]:
    """Input features of one cache request, for the refusal-predicate audit.

    Weight percent from a kilogram composition uses
    ``simulator.accounting.ledger_api.wt_pct_from_kg``. A composition that is
    already weight percent is reported as given, including a stored zero.
    """

    kwargs = request.get("kwargs")
    source = kwargs if isinstance(kwargs, Mapping) else request
    field_name = None
    composition: Mapping[str, Any] | None = None
    for name in (
        "composition_wt_pct",
        "composition_kg",
        "composition_mol",
        "composition",
    ):
        value = source.get(name)
        if isinstance(value, Mapping):
            field_name = name
            composition = value
            break
    wt_pct = None
    sio2 = None
    positive: list[str] = []
    zeros: list[str] = []
    if composition is not None and field_name in {"composition_wt_pct", "composition_kg"}:
        if field_name == "composition_kg":
            from simulator.accounting.ledger_api import wt_pct_from_kg

            wt_pct = wt_pct_from_kg(composition)
        else:
            wt_pct = {
                str(key): number
                for key, value in composition.items()
                if (number := _json_float(value)) is not None
            }
        raw_sio2 = _named_number(composition, "SiO2")
        if raw_sio2 == 0.0:
            sio2 = 0.0
        elif raw_sio2 is not None and wt_pct is not None:
            sio2 = _json_float(wt_pct.get("SiO2"))
        positive = sorted(
            name for name, value in (wt_pct or {}).items() if value > 0.0
        )
        zeros = sorted(
            str(key)
            for key, value in composition.items()
            if _json_float(value) == 0.0
        )
    temperature_c = _json_float(source.get("temperature_C"))
    temperature_k = _json_float(source.get("temperature_K"))
    if temperature_k is None and temperature_c is not None:
        from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET

        temperature_k = temperature_c + CELSIUS_TO_KELVIN_OFFSET
    po2 = source.get("po2")
    if not isinstance(po2, Mapping):
        po2 = source.get("po2_request")
    po2_mode = None
    po2_bar = None
    if isinstance(po2, Mapping):
        mode = po2.get("mode")
        po2_mode = mode if isinstance(mode, str) else None
        po2_bar = _json_float(po2.get("po2_bar"))
    formula = source.get("native_activity_formula")
    return {
        "temperature_K": temperature_k,
        "temperature_C": temperature_c,
        "pressure_bar": _json_float(
            source.get("pressure_bar", source.get("physical_pressure_bar"))
        ),
        "fO2_log": _json_float(source.get("fO2_log")),
        "po2_mode": po2_mode,
        "po2_bar": po2_bar,
        "native_activity_formula": formula if isinstance(formula, str) else None,
        "composition_field": field_name,
        "sio2_wt_pct": sio2,
        "positive_components": positive,
        "zero_components": zeros,
        "wt_pct": wt_pct,
    }


def cache_classification(
    engine: str,
    value: object,
    exc: BaseException | None,
) -> tuple[str, str]:
    """Return the ``(kind, token)`` the cache counter would record."""

    from simulator.engine_cache import EngineCacheValue, _not_stored_exception_label

    if exc is not None:
        return "exception", _not_stored_exception_label(exc)
    if isinstance(value, EngineCacheValue):
        if value.refusal_reason:
            return "refusal", str(value.refusal_reason)
        if not value.cacheable:
            return "cacheable_false", "cacheable_false"
        return "value", "stored"
    if isinstance(value, Mapping):
        reason = value.get("refusal_reason")
        if isinstance(reason, str) and reason:
            return "refusal", reason
        status = value.get("status")
        if isinstance(status, str) and status not in {
            "ok", "success", "converged", "non_authoritative",
        }:
            return "refusal", status
        return "value", "ok"
    if type(value).__name__ == "EquilibriumResult":
        try:
            wrapped = value.to_hot(engine_id=engine)  # type: ignore[attr-defined]
        except Exception as hot_exc:
            return "exception", type(hot_exc).__name__
        return cache_classification(engine, wrapped, None)
    if type(value).__name__ == "EquilibrateCell":
        reason = getattr(value, "refusal_reason", None)
        if isinstance(reason, str) and reason:
            return "refusal", reason
        status = getattr(value, "status", None)
        return ("value", "ok") if status in {None, "ok"} else ("refusal", str(status))
    return "value", type(value).__name__


def replay_kwargs(record: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild equilibrate keyword arguments for one cached backend request.

    The cache key drops ``call_timeout_s`` and stores ``po2_request`` as a
    payload. The replay puts both back so the engine sees the score's call.
    """

    from simulator.diagnostic_helpers.binary_pot_battery import Po2Request

    request = record.get("request")
    kwargs_value = request.get("kwargs") if isinstance(request, Mapping) else None
    if not isinstance(kwargs_value, Mapping):
        raise TypeError("backend cache request has no kwargs")
    kwargs = dict(kwargs_value)
    po2 = kwargs.get("po2_request")
    if isinstance(po2, Mapping) and not isinstance(po2, Po2Request):
        kwargs["po2_request"] = Po2Request(
            mode=str(po2.get("mode")),
            po2_bar=po2.get("po2_bar"),
            cell_material=po2.get("cell_material"),
        )
    timeout = record.get("call_timeout_s")
    if timeout is not None:
        kwargs["call_timeout_s"] = float(timeout)
    return kwargs


def _exception_fields(exc: BaseException) -> dict[str, Any]:
    cause = getattr(exc, "cause", None)
    cause_value = getattr(cause, "value", None)
    return {
        "exception_type": type(exc).__name__,
        "code": _token(getattr(exc, "code", None)),
        "reason_code": _token(getattr(exc, "reason_code", None)),
        "backend_status_reason": _token(getattr(exc, "backend_status_reason", None)),
        "backend_failure_category": _token(
            getattr(exc, "backend_failure_category", None)
        ),
        "cause": cause_value if isinstance(cause_value, str) else None,
        "phase": _token(getattr(exc, "phase", None)),
        "timeout_s": _json_float(getattr(exc, "timeout_s", None)),
        "timed_out": getattr(exc, "timed_out", None) is True,
        "returncode": _optional_int(getattr(exc, "returncode", None)),
    }


def _result_fields(value: object) -> dict[str, Any]:
    diagnostics = getattr(value, "diagnostics", None)
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}
    failure = diagnostics.get("subprocess_failure")
    signal_name = None
    returncode = None
    if isinstance(failure, Mapping):
        signal_name = _token(failure.get("signal"))
        returncode = _optional_int(failure.get("returncode"))
    solver = diagnostics.get("magemin_solver_status")
    fields = {
        "status": _token(getattr(value, "status", None)),
        "refusal_reason": _token(getattr(value, "refusal_reason", None)),
        "engine_status": _token(getattr(value, "engine_status", None)),
        "backend_status_reason": _token(diagnostics.get("backend_status_reason")),
        "backend_failure_reason_code": _token(
            diagnostics.get("backend_failure_reason_code")
        ),
        "backend_failure_category": _token(
            diagnostics.get("backend_failure_category")
        ),
        "backend_failure_disposition": _token(
            diagnostics.get("backend_failure_disposition")
            if diagnostics.get("backend_failure_disposition") is not None
            else getattr(value, "backend_failure_disposition", None)
        ),
        "signal": signal_name,
        "returncode": returncode,
        "exit_signal": _optional_int(getattr(value, "exit_signal", None)),
        "exit_code": _optional_int(getattr(value, "exit_code", None)),
        "magemin_solver_status": solver if isinstance(solver, int) and not isinstance(solver, bool) else None,
    }
    return fields


def fingerprint(
    engine: str,
    value: object,
    exc: BaseException | None,
    wall_s: float,
) -> dict[str, Any]:
    kind, token = cache_classification(engine, value, exc)
    payload: dict[str, Any] = {
        "cache_kind": kind,
        "cache_token": token,
        "wall_s": wall_s,
    }
    if exc is not None:
        payload.update(_exception_fields(exc))
    else:
        payload.update(_result_fields(value))
    return payload


def outcome_identity(payload: Mapping[str, Any]) -> str:
    """Stable identity of one repeat. Wall time is not part of it."""

    body = {key: value for key, value in payload.items() if key != "wall_s"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def _range(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"min": ordered[0], "max": ordered[-1]}


def _wall_stats(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2.0
    return {
        "n": len(values),
        "min": ordered[0],
        "median": median,
        "max": ordered[-1],
        "mean": sum(values) / len(values),
    }


def _sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip()


def _keep_dir() -> Path | None:
    path = Path.home() / "repos" / "engine-probes" / "keep"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path


def _sidecar_path(engine: str, sha: str) -> Path | None:
    directory = _keep_dir()
    if directory is None:
        return None
    return directory / f"t1917-misses-{engine}-{sha[:12]}.pkl"


def _box_name() -> str | None:
    host = socket.gethostname().lower()
    for number in ("1", "2", "3"):
        if f"mac-studio-256-{number}" in host:
            return f"s{number}"
    return None


def _find_warm_cache() -> Path | None:
    home = Path.home()
    box = _box_name()
    candidates = []
    if box is not None:
        candidates.append(home / "engine-cache" / f"{box}-7714e73c.db")
        candidates.append(home / "engine-cache" / f"{box}.db")
    candidates.extend((
        home / "repos" / "regolith-cache" / "engine-cache.sqlite",
        home / "Repos" / "regolith-cache" / "engine-cache.sqlite",
    ))
    for path in candidates:
        if path.is_file():
            return path
    return None


def _enable_readonly_cache() -> str | None:
    path = _find_warm_cache()
    if path is None:
        os.environ.pop("REGOLITH_ENGINE_CACHE", None)
        os.environ.pop("REGOLITH_ENGINE_CACHE_MODE", None)
        return None
    os.environ["REGOLITH_ENGINE_CACHE"] = str(path)
    os.environ["REGOLITH_ENGINE_CACHE_MODE"] = "readonly"
    os.environ.pop("REGOLITH_ENGINE_CACHE_BOX", None)
    try:
        from simulator.engine_cache import _connect

        connection = _connect(path, readonly=True)
    except Exception as exc:
        print(f"warm cache unreadable ({path}): {exc}", file=sys.stderr)
        os.environ.pop("REGOLITH_ENGINE_CACHE", None)
        os.environ.pop("REGOLITH_ENGINE_CACHE_MODE", None)
        return None
    if connection is None:
        os.environ.pop("REGOLITH_ENGINE_CACHE", None)
        os.environ.pop("REGOLITH_ENGINE_CACHE_MODE", None)
        return None
    return str(path)


def _snapshot(engine: str) -> dict[str, int]:
    from simulator.engine_cache import engine_cache_counters

    return dict(engine_cache_counters().get(engine, {}))


def _pickle_ok(value: object) -> bool:
    try:
        pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    except (TypeError, AttributeError, pickle.PicklingError):
        return False
    return True


def _remember(
    found: dict[tuple[str, str], dict[str, dict[str, Any]]],
    engine: str,
    request: Mapping[str, Any],
    before: Mapping[str, int],
    wall_s: float,
    call_timeout_s: float | None,
) -> None:
    after = _snapshot(engine)
    matched = [
        (kind, token)
        for kind, token in _classes_for_engine(engine)
        if after.get(_counter_key(kind, token), 0) - before.get(_counter_key(kind, token), 0) > 0
    ]
    if not matched:
        return
    stored = copy.deepcopy(dict(request))
    if not _pickle_ok(stored):
        return
    identity = _request_identity(stored, call_timeout_s)
    for kind, token in matched:
        bucket = found.setdefault((kind, token), {})
        if len(bucket) >= SAMPLE_CAP or identity in bucket:
            continue
        bucket[identity] = {
            "engine": engine,
            "kind": kind,
            "token": token,
            "request_key": identity,
            "request": stored,
            "call_timeout_s": call_timeout_s,
            "features": request_features(stored),
            "discovery_wall_s": wall_s,
        }


def _install_hooks(
    engine: str,
    found: dict[tuple[str, str], dict[str, dict[str, Any]]],
) -> list[tuple[Any, str, Any]]:
    import simulator.engine_cache as engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _InternalAnalyticalBatteryBackend,
    )
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    from simulator.melt_backend.magemin import MAGEMinBackend
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    patches: list[tuple[Any, str, Any]] = []
    classes = {
        "alphamelts": AlphaMELTSBackend,
        "magemin": MAGEMinBackend,
        "thermoengine": ThermoEngineBackend,
        "internal-analytical": _InternalAnalyticalBatteryBackend,
    }
    backend_class = classes[engine]
    original_equilibrate = backend_class.equilibrate

    def capture_equilibrate(self: Any, *args: Any, **kwargs: Any) -> Any:
        _LAST_TIMEOUT["value"] = _json_float(kwargs.get("call_timeout_s"))
        return original_equilibrate(self, *args, **kwargs)

    backend_class.equilibrate = capture_equilibrate  # type: ignore[method-assign]
    patches.append((backend_class, "equilibrate", original_equilibrate))

    original_cache = engine_cache.cached_engine_call

    def capture_cache(engine_id: str, request: Any, solve: Any) -> Any:
        if engine_id != engine or not isinstance(request, Mapping):
            return original_cache(engine_id, request, solve)
        _LAST_TIMEOUT["value"] = None
        before = _snapshot(engine_id)
        started = time.perf_counter()
        try:
            result = original_cache(engine_id, request, solve)
        except BaseException:
            _remember(
                found, engine_id, request, before,
                time.perf_counter() - started, _LAST_TIMEOUT["value"],
            )
            raise
        _remember(
            found, engine_id, request, before,
            time.perf_counter() - started, _LAST_TIMEOUT["value"],
        )
        return result

    engine_cache.cached_engine_call = capture_cache
    patches.append((engine_cache, "cached_engine_call", original_cache))
    return patches


def _restore(patches: list[tuple[Any, str, Any]]) -> None:
    for owner, name, original in reversed(patches):
        setattr(owner, name, original)


def _discovery_complete(
    engine: str,
    found: Mapping[tuple[str, str], Mapping[str, Any]],
) -> bool:
    return all(
        len(found.get((kind, token), {})) >= SAMPLE_CAP
        for kind, token in _classes_for_engine(engine)
    )


def _discover(
    engine: str,
) -> tuple[dict[tuple[str, str], dict[str, dict[str, Any]]], list[str], bool]:
    from simulator.battery.score import engines_from_names, load_score_context
    from simulator.battery.score_run import (
        _prepare_score_run,
        _score_store_with_decisions,
    )
    from simulator.diagnostic_helpers.binary_pot_battery import (
        close_battery_engine_handle,
        open_battery_engine,
    )

    notes: list[str] = []
    found: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    finished = False
    patches = _install_hooks(engine, found)
    handle = None
    try:
        handle = open_battery_engine(engine)
        if not handle.available or handle.backend is None:
            notes.append(
                f"{engine} unavailable during discovery: {handle.unavailable_reason}"
            )
        else:
            context = load_score_context(Path.cwd())
            prepared = _prepare_score_run(
                context,
                engines=engines_from_names([engine]),
                rail=None,
                work_id=None,
                limit=None,
                include_diagnostics=True,
                measured_only=False,
            )
            refs = list(prepared.refs)
            print(f"discovery refs={len(refs)} engine={engine}", file=sys.stderr)
            for offset in range(0, len(refs), DISCOVERY_CHUNK):
                try:
                    _score_store_with_decisions(
                        context,
                        engines=engines_from_names([engine]),
                        _prepared=prepared,
                        _refs=refs[offset:offset + DISCOVERY_CHUNK],
                        handles={engine: handle},
                    )
                except Exception as exc:
                    notes.append(
                        f"discovery stopped at offset {offset}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    traceback.print_exc()
                    break
                counts = {
                    f"{kind}:{token}": len(bucket)
                    for (kind, token), bucket in sorted(found.items())
                }
                print(
                    f"discovery offset={offset} samples={counts}",
                    file=sys.stderr,
                )
                if _discovery_complete(engine, found):
                    finished = True
                    break
            else:
                finished = True
    finally:
        _restore(patches)
        if handle is not None:
            close_battery_engine_handle(handle)
        from simulator.diagnostic_helpers.binary_pot_battery import (
            _close_isolated_cell_workers,
        )

        _close_isolated_cell_workers()
    return found, notes, finished


def _load_sidecar(
    path: Path,
    sha: str,
    engine: str,
) -> dict[tuple[str, str], dict[str, dict[str, Any]]] | None:
    if not path.is_file():
        return None
    try:
        payload = pickle.loads(path.read_bytes())
    except (OSError, pickle.PickleError, EOFError):
        return None
    if not isinstance(payload, dict) or payload.get("sha") != sha:
        return None
    if payload.get("engine") != engine or payload.get("complete") is not True:
        return None
    classes = payload.get("classes")
    if not isinstance(classes, dict):
        return None
    found: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for key, bucket in classes.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not isinstance(bucket, dict)
        ):
            return None
        found[(str(key[0]), str(key[1]))] = bucket
    return found


def _write_sidecar(
    path: Path,
    sha: str,
    engine: str,
    found: Mapping[tuple[str, str], Mapping[str, dict[str, Any]]],
) -> None:
    payload = {
        "sha": sha,
        "engine": engine,
        "complete": True,
        "classes": {key: dict(bucket) for key, bucket in found.items()},
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
    temporary.replace(path)


def _samples_for(
    found: Mapping[tuple[str, str], Mapping[str, dict[str, Any]]],
    kind: str,
    token: str,
) -> list[dict[str, Any]]:
    bucket = found.get((kind, token), {})
    ordered = [bucket[key] for key in sorted(bucket)]
    return ordered[:SAMPLE_CAP]


def _call_record(handle: Any, record: Mapping[str, Any]) -> Any:
    request = record["request"]
    if not isinstance(request, Mapping):
        raise TypeError("probe request is not a mapping")
    if "kwargs" in request:
        return handle.backend.equilibrate(**replay_kwargs(record))
    from simulator.diagnostic_helpers.binary_pot_battery import (
        BinaryPot,
        Po2Request,
        _equilibrate_cell_uncached,
    )

    po2_payload = request.get("po2")
    if not isinstance(po2_payload, Mapping):
        po2_payload = {"mode": "engine_default", "po2_bar": None}
    composition = request.get("composition_wt_pct")
    if not isinstance(composition, Mapping):
        raise TypeError("isolated cache request has no composition_wt_pct")
    timeout = record.get("call_timeout_s")
    return _equilibrate_cell_uncached(
        handle,
        BinaryPot(
            pot_id="t1917-probe",
            kato_1993_table4_system=None,
            why="t1917 determinism probe",
            composition_wt_pct=dict(composition),
        ),
        temperature_K=float(request["temperature_K"]),
        po2=Po2Request(
            mode=str(po2_payload.get("mode")),
            po2_bar=po2_payload.get("po2_bar"),
            cell_material=po2_payload.get("cell_material"),
        ),
        timeout_s=None if timeout is None else float(timeout),
        isolated=True,
        qualification=False,
        physical_pressure_bar=request.get("physical_pressure_bar"),
        native_activity_formula=request.get("native_activity_formula"),
        _cache_engine_call=False,
    )


def _replay_in_process(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _close_isolated_cell_workers,
        close_battery_engine_handle,
        open_battery_engine,
    )

    engine = str(record["engine"])
    request = record.get("request")
    # The score opens the default battery handle. The cache projection of
    # those settings is not an opener payload, so replay uses the same opener.
    handle = open_battery_engine(engine)
    repeats: list[dict[str, Any]] = []
    try:
        if not handle.available or (
            isinstance(request, Mapping) and "kwargs" in request and handle.backend is None
        ):
            for _ in range(REPEAT_COUNT):
                repeats.append({
                    "cache_kind": "unavailable",
                    "cache_token": "backend_unavailable",
                    "wall_s": 0.0,
                    "detail": handle.unavailable_reason,
                })
            return repeats
        for _ in range(REPEAT_COUNT):
            started = time.perf_counter()
            try:
                value = _call_record(handle, record)
                exc: BaseException | None = None
            except Exception as caught:
                value = None
                exc = caught
            repeats.append(fingerprint(
                engine, value, exc, time.perf_counter() - started,
            ))
    finally:
        close_battery_engine_handle(handle)
        _close_isolated_cell_workers()
    return repeats


def _child_main() -> int:
    os.environ.pop("REGOLITH_ENGINE_CACHE", None)
    os.environ.pop("REGOLITH_ENGINE_CACHE_MODE", None)
    os.environ.pop("REGOLITH_ENGINE_CACHE_BOX", None)
    payload_path = Path(os.environ["T1917_PROBE_PAYLOAD"])
    out_path = Path(os.environ["T1917_PROBE_OUT"])
    record = pickle.loads(payload_path.read_bytes())
    repeats = _replay_in_process(record)
    out_path.write_text(
        json.dumps({"repeats": repeats}, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return 0


def _replay_child(record: Mapping[str, Any], scratch: Path, index: int) -> dict[str, Any]:
    payload_path = scratch / f"request-{index:03d}.pkl"
    out_path = scratch / f"result-{index:03d}.json"
    payload_path.write_bytes(pickle.dumps(dict(record), protocol=pickle.HIGHEST_PROTOCOL))
    env = dict(os.environ)
    env.pop("REGOLITH_ENGINE_CACHE", None)
    env.pop("REGOLITH_ENGINE_CACHE_MODE", None)
    env.pop("REGOLITH_ENGINE_CACHE_BOX", None)
    env[_CHILD_FLAG] = "1"
    env["T1917_PROBE_PAYLOAD"] = str(payload_path)
    env["T1917_PROBE_OUT"] = str(out_path)
    budget = _REPLAY_BACKSTOP_S[str(record["engine"])]
    process = subprocess.Popen(
        [sys.executable, str(Path(sys.argv[0]).resolve())],
        cwd=str(Path.cwd()),
        env=env,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _stdout, stderr = process.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _stdout, stderr = process.communicate()
        return {
            "process_returncode": process.returncode,
            "probe_backstop": True,
            "stderr_tail": (stderr or "")[-500:],
            "repeats": [],
        }
    repeats: list[dict[str, Any]] = []
    if out_path.is_file():
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict) and isinstance(loaded.get("repeats"), list):
            repeats = loaded["repeats"]
    return {
        "process_returncode": process.returncode,
        "probe_backstop": False,
        "stderr_tail": (stderr or "")[-500:],
        "repeats": repeats,
    }


def _same(repeats: list[Mapping[str, Any]]) -> bool:
    if len(repeats) != REPEAT_COUNT:
        return False
    identities = [outcome_identity(repeat) for repeat in repeats]
    return len(set(identities)) == 1


def _remained(repeats: list[Mapping[str, Any]], kind: str, token: str) -> bool:
    if len(repeats) != REPEAT_COUNT:
        return False
    return all(
        repeat.get("cache_kind") == kind and repeat.get("cache_token") == token
        for repeat in repeats
    )


def _timeout_assessment(samples: list[Mapping[str, Any]]) -> str:
    if not samples:
        return "no_samples"
    if not all(sample.get("identical") and sample.get("remained_target") for sample in samples):
        return "unstable"
    budgets: list[float] = []
    walls: list[float] = []
    phases: set[str | None] = set()
    for sample in samples:
        repeats = sample.get("repeats")
        if not isinstance(repeats, list):
            return "unstable"
        for repeat in repeats:
            if not isinstance(repeat, Mapping):
                return "unstable"
            budget = _json_float(repeat.get("timeout_s"))
            wall = _json_float(repeat.get("wall_s"))
            if budget is None or wall is None:
                return "missing_timeout_s"
            budgets.append(budget)
            walls.append(wall)
            phase = repeat.get("phase")
            phases.add(phase if isinstance(phase, str) else None)
    if len(set(budgets)) != 1 or len(phases) != 1:
        return "unstable_budget"
    budget = budgets[0]
    if all(wall >= 0.8 * budget for wall in walls):
        return "deterministic_full_budget"
    return "deterministic_but_wall_below_budget"


def _census(samples: list[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for sample in samples:
        repeats = sample.get("repeats")
        if not isinstance(repeats, list) or not repeats:
            continue
        first = repeats[0]
        if not isinstance(first, Mapping):
            continue
        value = first.get(field)
        if value is None:
            continue
        counts[str(value)] += 1
    return dict(sorted(counts.items()))


def _build_report(
    *,
    engine: str,
    kind: str,
    token: str,
    sha: str,
    cache_path: str | None,
    discovery: str,
    samples: list[dict[str, Any]],
    notes: list[str],
) -> dict[str, Any]:
    walls = [
        wall
        for sample in samples
        for repeat in sample.get("repeats", [])
        if isinstance(repeat, Mapping)
        and (wall := _json_float(repeat.get("wall_s"))) is not None
    ]
    discovery_walls = [
        wall
        for sample in samples
        if (wall := _json_float(sample.get("discovery_wall_s"))) is not None
    ]
    sio2 = [
        value
        for sample in samples
        if (value := _json_float((sample.get("features") or {}).get("sio2_wt_pct"))) is not None
    ]
    temperatures = [
        value
        for sample in samples
        if (value := _json_float((sample.get("features") or {}).get("temperature_K"))) is not None
    ]
    pressures = [
        value
        for sample in samples
        if (value := _json_float((sample.get("features") or {}).get("pressure_bar"))) is not None
    ]
    fo2 = [
        value
        for sample in samples
        if (value := _json_float((sample.get("features") or {}).get("fO2_log"))) is not None
    ]
    component_sets: Counter[str] = Counter()
    for sample in samples:
        features = sample.get("features") or {}
        components = features.get("positive_components")
        if isinstance(components, list):
            component_sets[" ".join(str(name) for name in components)] += 1
    report: dict[str, Any] = {
        "probe": "t1917",
        "sha": sha,
        "engine": engine,
        "kind": kind,
        "token": token,
        "sample_cap": SAMPLE_CAP,
        "found": len(samples),
        "cache_path": cache_path,
        "discovery": discovery,
        "replay": "one fresh process per request, 3 serial calls, cache off",
        "all_identical": bool(samples) and all(sample["identical"] for sample in samples),
        "all_remained_target": bool(samples) and all(
            sample["remained_target"] for sample in samples
        ),
        "wall_s": _wall_stats(walls),
        "discovery_wall_s": _wall_stats(discovery_walls),
        "signal_counts": _census(samples, "signal"),
        "exit_signal_counts": _census(samples, "exit_signal"),
        "cause_counts": _census(samples, "cause"),
        "phase_counts": _census(samples, "phase"),
        "component_set_counts": dict(component_sets.most_common()),
        "sio2_wt_pct": _range(sio2),
        "temperature_K": _range(temperatures),
        "pressure_bar": _range(pressures),
        "fO2_log": _range(fo2),
        "notes": notes,
        "samples": samples,
    }
    if token == "EngineWorkerTimeout":
        report["timeout_assessment"] = _timeout_assessment(samples)
        report["notes"] = [
            *notes,
            (
                "timeout_assessment is evidence only. "
                "deterministic_full_budget means every repeat burned at least "
                "80% of the same typed timeout. This probe does not cache it."
            ),
        ]
    return report


def _engine_lock(engine: str, sha: str) -> Any:
    """Serialize probes for one engine. A second class waits, then reuses the sidecar."""

    import fcntl

    directory = _keep_dir()
    if directory is None:
        return None
    handle = (directory / f"t1917-{engine}-{sha[:12]}.lock").open("a", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _unlock(handle: Any) -> None:
    if handle is None:
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


def _select_samples(
    engine: str,
    kind: str,
    token: str,
    sha: str,
    notes: list[str],
) -> tuple[list[dict[str, Any]], str]:
    sidecar = _sidecar_path(engine, sha)
    if sidecar is not None:
        loaded = _load_sidecar(sidecar, sha, engine)
        if loaded is not None:
            notes.append(f"reused discovery sidecar {sidecar}")
            return _samples_for(loaded, kind, token), "sidecar"
    found, discovery_notes, finished = _discover(engine)
    notes.extend(discovery_notes)
    if sidecar is not None and finished:
        _write_sidecar(sidecar, sha, engine, found)
        notes.append(f"wrote discovery sidecar {sidecar}")
    elif sidecar is not None:
        notes.append("discovery did not finish; sidecar not written")
    return _samples_for(found, kind, token), "score"


def main(*, engine: str, kind: str, token: str) -> int:
    if os.environ.get(_CHILD_FLAG) == "1":
        try:
            return _child_main()
        except Exception:
            traceback.print_exc()
            return 1
    if len(sys.argv) > 1:
        print("this probe takes no arguments", file=sys.stderr)
        return 2
    root = Path.cwd()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ.setdefault(name, "1")
    sha = _sha()
    notes: list[str] = []
    lock = _engine_lock(engine, sha)
    try:
        cache_path = _enable_readonly_cache()
        if cache_path is None:
            notes.append("no readable warm cache; discovery will recompute successes")
        samples, discovery = _select_samples(engine, kind, token, sha, notes)
        import tempfile

        scratch_parent = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(prefix="t1917-probe-", dir=scratch_parent) as temporary:
            scratch = Path(temporary)
            for index, sample in enumerate(samples):
                print(
                    f"replay {index + 1}/{len(samples)} {token}",
                    file=sys.stderr,
                )
                child = _replay_child(sample, scratch, index)
                repeats = child["repeats"]
                sample["repeats"] = repeats
                sample["identical"] = _same(repeats)
                sample["remained_target"] = _remained(repeats, kind, token)
                sample["process_returncode"] = child["process_returncode"]
                sample["probe_backstop"] = child["probe_backstop"]
                if child["process_returncode"] not in {0, None} or child["probe_backstop"]:
                    sample["stderr_tail"] = child["stderr_tail"]
                sample.pop("request", None)
        report = _build_report(
            engine=engine,
            kind=kind,
            token=token,
            sha=sha,
            cache_path=cache_path,
            discovery=discovery,
            samples=samples,
            notes=notes,
        )
    finally:
        _unlock(lock)
    directory = _keep_dir()
    if directory is not None:
        report_path = directory / f"t1917-report-{engine}-{token}-{sha[:12]}.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        print(f"T1917_PROBE_PATH {report_path}", file=sys.stderr)
    wall = report["wall_s"] or {}
    print(
        "T1917_PROBE_SUMMARY "
        f"engine={engine} kind={kind} token={token} found={report['found']} "
        f"all_identical={report['all_identical']} "
        f"all_remained_target={report['all_remained_target']} "
        f"wall_median_s={wall.get('median')} "
        f"timeout_assessment={report.get('timeout_assessment')}"
    )
    print("T1917_PROBE_REPORT")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if samples else 2


def main_for_script(filename: str) -> int:
    try:
        engine, kind, token = SCRIPT_CLASSES[filename]
    except KeyError:
        print(f"unknown t-1917 probe {filename}", file=sys.stderr)
        return 2
    return main(engine=engine, kind=kind, token=token)


if __name__ == "__main__":
    print(
        "run one scripts/probe_t1917_*.py from the repository root",
        file=sys.stderr,
    )
    raise SystemExit(2)
