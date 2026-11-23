"""Determinism probe for uncached engine refusals (t-1917).

No arguments. Run from the repository root:

    T1917_PROBE_ENGINES=alphamelts,thermoengine python3 tests/t1917_refusal_probe.py

The controller sets REGOLITH_ENGINE_CACHE to the live cache. This probe
forces readonly so a miss cannot write that file. Successes are hits.
A miss still runs the engine. The probe wraps the solve() passed to
cached_engine_call, so it sees the value the not-stored branch labels,
and it records that request. Readonly mode itself only increments
not_stored:readonly; the stored detail is the token the writable branch
would count (not_stored_refusal_label / _not_stored_exception_label).

Discovery walks at most 30000 score rows. Each stored request is replayed
three times in a fresh process with the cache off. The probe exits nonzero
when it stores nothing, or when a not-stored counter in this run moved and
no request was recorded for it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import pickle
import signal
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
DISCOVERY_LIMIT = 30000

_CHILD_FLAG = "T1917_PROBE_CHILD"
_LAST_TIMEOUT: dict[str, float | None] = {"value": None}
_REPLAY_BACKSTOP_S = {
    "alphamelts": 360.0,
    "magemin": 360.0,
    "thermoengine": 600.0,
    "internal-analytical": 900.0,
}
_WATCHED_EXACT = frozenset({
    "not_stored:readonly",
    "not_stored:cacheable_false",
})


def _boot() -> None:
    root = str(Path.cwd())
    if root not in sys.path:
        sys.path.insert(0, root)


def _watched(key: str) -> bool:
    return (
        key.startswith("not_stored:refusal:")
        or key.startswith("not_stored:exception:")
        or key in _WATCHED_EXACT
    )


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
    """Input features of one cache request.

    Kilogram composition uses ``wt_pct_from_kg``. A weight-percent
    composition is reported as stored, including an explicit zero.
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


def configured_cache_path() -> Path:
    """The controller's cache. Missing or unreadable is a loud error.

    Readonly is forced so discovery cannot insert into the live file.
    """

    raw = os.environ.get("REGOLITH_ENGINE_CACHE", "").strip()
    if not raw:
        raise SystemExit(
            "T1917_PROBE_NO_CACHE: REGOLITH_ENGINE_CACHE is not set"
        )
    path = Path(raw).expanduser()
    if not path.is_file():
        raise SystemExit(f"T1917_PROBE_NO_CACHE: {path} is not a file")
    os.environ["REGOLITH_ENGINE_CACHE_MODE"] = "readonly"
    from simulator.engine_cache import _connect

    if _connect(path, readonly=True) is None:
        raise SystemExit(f"T1917_PROBE_NO_CACHE: {path} is not a readable cache")
    return path


def coverage_error(
    deltas: Mapping[str, Mapping[str, int]],
    seen: Mapping[str, Mapping[str, int]],
) -> str | None:
    """Name counters that moved while the hook recorded no request.

    ``deltas`` is the per-engine not-stored increase over the walk.
    ``seen`` counts requests the hook observed for those same keys.
    """

    missing = []
    for engine, counts in sorted(deltas.items()):
        observed = seen.get(engine, {})
        for key, delta in sorted(counts.items()):
            if not isinstance(delta, int) or isinstance(delta, bool) or delta <= 0:
                continue
            if not _watched(key):
                continue
            if int(observed.get(key, 0) or 0) <= 0:
                missing.append(f"{engine}:{key}={delta}")
    if not missing:
        return None
    return (
        "discovery counters moved but no request was recorded: "
        + ", ".join(missing)
    )


def detail_for(solved: object, hot: object, exc: BaseException | None) -> str | None:
    """Writable-branch token for one miss, or None when it is not a refusal."""

    from simulator.engine_cache import (
        EngineCacheValue,
        _not_stored_exception_label,
        not_stored_refusal_label,
    )

    if exc is not None:
        return "exception:" + _not_stored_exception_label(exc)
    if isinstance(solved, EngineCacheValue) and not solved.cacheable:
        return "cacheable_false"
    label = not_stored_refusal_label(solved, hot)
    if label is None:
        return None
    return "refusal:" + label


def cache_classification(
    engine: str,
    value: object,
    exc: BaseException | None,
) -> tuple[str, str]:
    """Replay label, using the same refusal function as the counter."""

    from simulator.engine_cache import live_hot

    if type(value).__name__ == "EquilibriumResult":
        try:
            value = value.to_hot(engine_id=engine)  # type: ignore[attr-defined]
        except Exception as hot_exc:
            return "exception", type(hot_exc).__name__
    detail = detail_for(value, live_hot(value) if value is not None else value, exc)
    if detail is None:
        return "value", type(value).__name__
    kind, _, token = detail.partition(":")
    return kind, token


def replay_kwargs(record: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild equilibrate keyword arguments for one cached backend request."""

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


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _result_fields(value: object) -> dict[str, Any]:
    diagnostics = _field(value, "diagnostics")
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}
    failure = diagnostics.get("subprocess_failure")
    signal_name = None
    returncode = None
    if isinstance(failure, Mapping):
        signal_name = _token(failure.get("signal"))
        returncode = _optional_int(failure.get("returncode"))
    solver = diagnostics.get("magemin_solver_status")
    hot = value.hot if type(value).__name__ == "EngineCacheValue" else value
    return {
        "status": _token(_field(hot, "status")),
        "refusal_reason": _token(
            _field(value, "refusal_reason") or _field(hot, "refusal_reason")
        ),
        "engine_status": _token(_field(hot, "engine_status")),
        "backend_status_reason": _token(diagnostics.get("backend_status_reason")),
        "backend_failure_reason_code": _token(
            diagnostics.get("backend_failure_reason_code")
        ),
        "backend_failure_category": _token(
            diagnostics.get("backend_failure_category")
        ),
        "backend_failure_disposition": _token(
            diagnostics.get("backend_failure_disposition")
        ),
        "signal": signal_name,
        "returncode": returncode,
        "exit_signal": _optional_int(_field(hot, "exit_signal")),
        "exit_code": _optional_int(_field(hot, "exit_code")),
        "magemin_solver_status": (
            solver if isinstance(solver, int) and not isinstance(solver, bool) else None
        ),
    }


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
    body = {key: value for key, value in payload.items() if key != "wall_s"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def _snapshot(engine: str) -> dict[str, int]:
    from simulator.engine_cache import engine_cache_counters

    return dict(engine_cache_counters().get(engine, {}))


def _counter_deltas(
    start: Mapping[str, Mapping[str, int]],
    end: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]]:
    deltas: dict[str, dict[str, int]] = {}
    for engine in sorted(set(start) | set(end)):
        before = start.get(engine, {})
        after = end.get(engine, {})
        change = {}
        for key in set(before) | set(after):
            if not _watched(str(key)):
                continue
            delta = int(after.get(key, 0)) - int(before.get(key, 0))
            if delta:
                change[str(key)] = delta
        if change:
            deltas[engine] = change
    return deltas


def _pickle_ok(value: object) -> bool:
    try:
        pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    except (TypeError, AttributeError, pickle.PicklingError):
        return False
    return True


def _account(
    found: dict[str, dict[str, dict[str, dict[str, Any]]]],
    seen: dict[str, dict[str, int]],
    detail_counts: dict[str, Counter[str]],
    dropped: dict[str, int],
    engine: str,
    request: Mapping[str, Any],
    before: Mapping[str, int],
    captured: Mapping[str, Any],
    result: object,
    wall_s: float,
) -> None:
    after = _snapshot(engine)
    moved = [
        key for key in set(before) | set(after)
        if _watched(key) and after.get(key, 0) - before.get(key, 0) > 0
    ]
    if not moved:
        return
    bucket = seen.setdefault(engine, {})
    for key in moved:
        bucket[key] = bucket.get(key, 0) + (after.get(key, 0) - before.get(key, 0))
    solved = captured.get("solved")
    exc = captured.get("exc")
    if exc is not None and not isinstance(exc, BaseException):
        exc = None
    hot = result if exc is None else solved
    detail = detail_for(solved, hot, exc if isinstance(exc, BaseException) else None)
    if detail is None:
        detail_counts.setdefault(engine, Counter())["other"] += 1
        return
    detail_counts.setdefault(engine, Counter())[detail] += 1
    stored = copy.deepcopy(dict(request))
    if not _pickle_ok(stored):
        dropped["unpickleable"] = dropped.get("unpickleable", 0) + 1
        return
    timeout = _LAST_TIMEOUT["value"]
    identity = _request_identity(stored, timeout)
    per_detail = found.setdefault(engine, {}).setdefault(detail, {})
    if len(per_detail) >= SAMPLE_CAP or identity in per_detail:
        return
    fields = (
        _exception_fields(exc)
        if isinstance(exc, BaseException)
        else _result_fields(solved)
    )
    per_detail[identity] = {
        "engine": engine,
        "detail": detail,
        "counter_keys": sorted(moved),
        "request_key": identity,
        "request": stored,
        "call_timeout_s": timeout,
        "features": request_features(stored),
        "discovery_wall_s": wall_s,
        "discovery_fields": fields,
    }


def observe_cached_call(
    engine: str,
    request: Mapping[str, Any],
    solve: Any,
    found: dict[str, dict[str, dict[str, dict[str, Any]]]],
    seen: dict[str, dict[str, int]],
    detail_counts: dict[str, Counter[str]],
    dropped: dict[str, int],
    *,
    call: Any = None,
) -> Any:
    """Run one cached call and record it when a not-stored counter moves."""

    import simulator.engine_cache as engine_cache

    invoke = call or engine_cache.cached_engine_call
    captured: dict[str, Any] = {}

    def wrapped() -> Any:
        try:
            value = solve()
        except BaseException as exc:
            captured["exc"] = exc
            raise
        captured["solved"] = value
        return value

    _LAST_TIMEOUT["value"] = None
    before = _snapshot(engine)
    started = time.perf_counter()
    try:
        result = invoke(engine, request, wrapped)
    except BaseException:
        _account(
            found, seen, detail_counts, dropped, engine, request, before,
            captured, None, time.perf_counter() - started,
        )
        raise
    _account(
        found, seen, detail_counts, dropped, engine, request, before,
        captured, result, time.perf_counter() - started,
    )
    return result


def _install_timeout_patch(engines: set[str]) -> list[tuple[Any, str, Any]]:
    patches: list[tuple[Any, str, Any]] = []
    classes: dict[str, Any] = {}
    if "alphamelts" in engines:
        from simulator.melt_backend.alphamelts import AlphaMELTSBackend

        classes["alphamelts"] = AlphaMELTSBackend
    if "magemin" in engines:
        from simulator.melt_backend.magemin import MAGEMinBackend

        classes["magemin"] = MAGEMinBackend
    if "thermoengine" in engines:
        from simulator.melt_backend.thermoengine import ThermoEngineBackend

        classes["thermoengine"] = ThermoEngineBackend
    if "internal-analytical" in engines:
        from simulator.diagnostic_helpers.binary_pot_battery import (
            _InternalAnalyticalBatteryBackend,
        )

        classes["internal-analytical"] = _InternalAnalyticalBatteryBackend
    def _bind(original: Any) -> Any:
        def capture_equilibrate(self: Any, *args: Any, **kwargs: Any) -> Any:
            _LAST_TIMEOUT["value"] = _json_float(kwargs.get("call_timeout_s"))
            return original(self, *args, **kwargs)

        return capture_equilibrate

    for backend_class in classes.values():
        original = backend_class.equilibrate
        backend_class.equilibrate = _bind(original)  # type: ignore[method-assign]
        patches.append((backend_class, "equilibrate", original))
    return patches


def _restore(patches: list[tuple[Any, str, Any]]) -> None:
    for owner, name, original in reversed(patches):
        setattr(owner, name, original)


def _discover(
    engines: tuple[str, ...],
) -> tuple[
    dict[str, dict[str, dict[str, dict[str, Any]]]],
    dict[str, dict[str, int]],
    dict[str, dict[str, int]],
    dict[str, dict[str, int]],
    dict[str, int],
    list[str],
]:
    from simulator.battery.score import engines_from_names, load_score_context
    from simulator.battery.score_run import (
        _prepare_score_run,
        _score_store_with_decisions,
    )
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _close_isolated_cell_workers,
        close_battery_engine_handle,
        open_battery_engine,
    )
    import simulator.engine_cache as engine_cache

    notes: list[str] = []
    found: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    seen: dict[str, dict[str, int]] = {}
    detail_counts: dict[str, Counter[str]] = {}
    dropped: dict[str, int] = {}
    handles: dict[str, Any] = {}
    engine_set = set(engines)
    for engine in engines:
        handle = open_battery_engine(engine)
        if not handle.available or handle.backend is None:
            notes.append(
                f"{engine} unavailable: {handle.unavailable_reason}"
            )
            close_battery_engine_handle(handle)
            continue
        handles[engine] = handle
    if not handles:
        raise SystemExit(
            "T1917_PROBE_NO_ENGINE: " + "; ".join(notes or ["no engine opened"])
        )
    patches = _install_timeout_patch(set(handles))
    original_cache = engine_cache.cached_engine_call

    def capture_cache(engine_id: str, request: Any, solve: Any) -> Any:
        if engine_id not in engine_set or not isinstance(request, Mapping):
            return original_cache(engine_id, request, solve)
        return observe_cached_call(
            engine_id, request, solve, found, seen, detail_counts, dropped,
            call=original_cache,
        )

    engine_cache.cached_engine_call = capture_cache
    start = {engine: _snapshot(engine) for engine in handles}
    try:
        try:
            context = load_score_context(Path.cwd())
            prepared = _prepare_score_run(
                context,
                engines=engines_from_names(tuple(handles)),
                rail=None,
                work_id=None,
                limit=DISCOVERY_LIMIT,
                include_diagnostics=True,
                measured_only=False,
            )
            refs = list(prepared.refs)
            print(
                f"discovery refs={len(refs)} engines={sorted(handles)}",
                file=sys.stderr,
            )
            for offset in range(0, len(refs), DISCOVERY_CHUNK):
                try:
                    _score_store_with_decisions(
                        context,
                        engines=engines_from_names(tuple(handles)),
                        _prepared=prepared,
                        _refs=refs[offset:offset + DISCOVERY_CHUNK],
                        handles=handles,
                    )
                except Exception as exc:
                    notes.append(
                        f"discovery stopped at offset {offset}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    traceback.print_exc()
                    break
                stored = {
                    engine: {
                        detail: len(bucket)
                        for detail, bucket in sorted(details.items())
                    }
                    for engine, details in sorted(found.items())
                }
                print(
                    f"discovery offset={offset} stored={stored}",
                    file=sys.stderr,
                )
        except Exception as exc:
            notes.append(f"discovery failed: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    finally:
        engine_cache.cached_engine_call = original_cache
        _restore(patches)
        for handle in handles.values():
            close_battery_engine_handle(handle)
        _close_isolated_cell_workers()
    end = {engine: _snapshot(engine) for engine in handles}
    notes.append(
        "REGOLITH_ENGINE_CACHE_MODE=readonly; "
        "detail is the writable not-stored token"
    )
    flat_counts = {
        engine: dict(counts) for engine, counts in detail_counts.items()
    }
    return found, seen, _counter_deltas(start, end), flat_counts, dropped, notes


def _samples(
    found: Mapping[str, Mapping[str, Mapping[str, dict[str, Any]]]],
) -> list[dict[str, Any]]:
    rows = []
    for engine in sorted(found):
        for detail in sorted(found[engine]):
            bucket = found[engine][detail]
            rows.extend(bucket[key] for key in sorted(bucket))
    return rows


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
    handle = open_battery_engine(engine)
    repeats: list[dict[str, Any]] = []
    try:
        if not handle.available or (
            isinstance(request, Mapping)
            and "kwargs" in request
            and handle.backend is None
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
    record = pickle.loads(Path(os.environ["T1917_PROBE_PAYLOAD"]).read_bytes())
    repeats = _replay_in_process(record)
    Path(os.environ["T1917_PROBE_OUT"]).write_text(
        json.dumps({"repeats": repeats}, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return 0


def _replay_child(record: Mapping[str, Any], scratch: Path, index: int) -> dict[str, Any]:
    payload_path = scratch / f"request-{index:03d}.pkl"
    out_path = scratch / f"result-{index:03d}.json"
    payload_path.write_bytes(
        pickle.dumps(dict(record), protocol=pickle.HIGHEST_PROTOCOL)
    )
    env = dict(os.environ)
    env.pop("REGOLITH_ENGINE_CACHE", None)
    env.pop("REGOLITH_ENGINE_CACHE_MODE", None)
    env.pop("REGOLITH_ENGINE_CACHE_BOX", None)
    env[_CHILD_FLAG] = "1"
    env["T1917_PROBE_PAYLOAD"] = str(payload_path)
    env["T1917_PROBE_OUT"] = str(out_path)
    budget = _REPLAY_BACKSTOP_S.get(str(record["engine"]), 600.0)
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
    return len({outcome_identity(repeat) for repeat in repeats}) == 1


def _remained(repeats: list[Mapping[str, Any]], detail: str) -> bool:
    if len(repeats) != REPEAT_COUNT:
        return False
    return all(
        f"{repeat.get('cache_kind')}:{repeat.get('cache_token')}" == detail
        for repeat in repeats
    )


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
    median = (
        ordered[mid]
        if len(ordered) % 2
        else (ordered[mid - 1] + ordered[mid]) / 2.0
    )
    return {
        "n": len(values),
        "min": ordered[0],
        "median": median,
        "max": ordered[-1],
        "mean": sum(values) / len(values),
    }


def _field_census(samples: list[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for sample in samples:
        fields = sample.get("discovery_fields")
        if not isinstance(fields, Mapping):
            continue
        value = fields.get(field)
        if value is None:
            continue
        counts[str(value)] += 1
    return dict(sorted(counts.items()))


def _class_report(samples: list[dict[str, Any]]) -> dict[str, Any]:
    walls = [
        wall
        for sample in samples
        for repeat in sample.get("repeats", [])
        if isinstance(repeat, Mapping)
        and (wall := _json_float(repeat.get("wall_s"))) is not None
    ]
    temperatures = [
        value
        for sample in samples
        if (value := _json_float((sample.get("features") or {}).get("temperature_K")))
        is not None
    ]
    pressures = [
        value
        for sample in samples
        if (value := _json_float((sample.get("features") or {}).get("pressure_bar")))
        is not None
    ]
    fo2 = [
        value
        for sample in samples
        if (value := _json_float((sample.get("features") or {}).get("fO2_log")))
        is not None
    ]
    sio2 = [
        value
        for sample in samples
        if (value := _json_float((sample.get("features") or {}).get("sio2_wt_pct")))
        is not None
    ]
    component_sets: Counter[str] = Counter()
    for sample in samples:
        components = (sample.get("features") or {}).get("positive_components")
        if isinstance(components, list):
            component_sets[" ".join(str(name) for name in components)] += 1
    return {
        "engine": samples[0]["engine"],
        "detail": samples[0]["detail"],
        "found": len(samples),
        "all_identical": all(sample["identical"] for sample in samples),
        "all_remained_target": all(sample["remained_target"] for sample in samples),
        "wall_s": _wall_stats(walls),
        "signal_counts": _field_census(samples, "signal"),
        "cause_counts": _field_census(samples, "cause"),
        "phase_counts": _field_census(samples, "phase"),
        "status_counts": _field_census(samples, "status"),
        "engine_status_counts": _field_census(samples, "engine_status"),
        "code_counts": _field_census(samples, "code"),
        "exception_type_counts": _field_census(samples, "exception_type"),
        "component_set_counts": dict(component_sets.most_common()),
        "sio2_wt_pct": _range(sio2),
        "temperature_K": _range(temperatures),
        "pressure_bar": _range(pressures),
        "fO2_log": _range(fo2),
        "samples": samples,
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


def _probe_engines() -> tuple[str, ...]:
    raw = os.environ.get("T1917_PROBE_ENGINES", "").strip()
    if not raw:
        raise SystemExit(
            "T1917_PROBE_NO_ENGINES: set T1917_PROBE_ENGINES to a comma-separated list"
        )
    engines = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not engines:
        raise SystemExit("T1917_PROBE_NO_ENGINES: engine list is empty")
    return engines


def _finish(
    *,
    engines: tuple[str, ...],
    cache_path: Path,
    found: Mapping[str, Mapping[str, Mapping[str, dict[str, Any]]]],
    seen: Mapping[str, Mapping[str, int]],
    deltas: Mapping[str, Mapping[str, int]],
    detail_counts: Mapping[str, Mapping[str, int]],
    notes: list[str],
) -> int:
    import tempfile

    samples = _samples(found)
    error = coverage_error(deltas, seen)
    if error is not None:
        notes.append(error)
    scratch_parent = os.environ.get("TMPDIR")
    with tempfile.TemporaryDirectory(prefix="t1917-probe-", dir=scratch_parent) as temporary:
        scratch = Path(temporary)
        for index, sample in enumerate(samples):
            print(
                f"replay {index + 1}/{len(samples)} {sample['detail']}",
                file=sys.stderr,
            )
            child = _replay_child(sample, scratch, index)
            repeats = child["repeats"]
            sample["repeats"] = repeats
            sample["identical"] = _same(repeats)
            sample["remained_target"] = _remained(repeats, str(sample["detail"]))
            sample["process_returncode"] = child["process_returncode"]
            sample["probe_backstop"] = child["probe_backstop"]
            if child["process_returncode"] not in {0, None} or child["probe_backstop"]:
                sample["stderr_tail"] = child["stderr_tail"]
            sample.pop("request", None)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sample in samples:
        grouped.setdefault((str(sample["engine"]), str(sample["detail"])), []).append(sample)
    classes = [
        _class_report(grouped[key]) for key in sorted(grouped)
    ]
    sha = _sha()
    report = {
        "probe": "t1917",
        "sha": sha,
        "engines": list(engines),
        "cache_path": str(cache_path),
        "discovery_limit": DISCOVERY_LIMIT,
        "counter_deltas": deltas,
        "detail_counts": detail_counts,
        "seen_counters": seen,
        "coverage_error": error,
        "notes": notes,
        "classes": classes,
    }
    directory = _keep_dir()
    if directory is not None:
        slug = "-".join(engines)
        report_path = directory / f"t1917-report-{slug}-{sha[:12]}.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        print(f"T1917_PROBE_PATH {report_path}", file=sys.stderr)
    print(
        "T1917_PROBE_COUNTERS "
        + json.dumps(deltas, sort_keys=True, default=str)
    )
    for row in classes:
        wall = row["wall_s"] or {}
        print(
            "T1917_PROBE_SUMMARY "
            f"engine={row['engine']} detail={row['detail']} "
            f"found={row['found']} seen={detail_counts.get(row['engine'], {}).get(row['detail'])} "
            f"all_identical={row['all_identical']} "
            f"all_remained_target={row['all_remained_target']} "
            f"wall_median_s={wall.get('median')}"
        )
    if not classes:
        print(
            "T1917_PROBE_EMPTY no refusal or exception request was recorded. "
            f"counters={json.dumps(deltas, sort_keys=True)} "
            f"details={json.dumps(detail_counts, sort_keys=True)}",
            file=sys.stderr,
        )
    print("T1917_PROBE_REPORT")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    if error is not None or not classes:
        return 2
    return 0


def main() -> int:
    _boot()
    if os.environ.get(_CHILD_FLAG) == "1":
        try:
            return _child_main()
        except Exception:
            traceback.print_exc()
            return 1
    if len(sys.argv) > 1:
        print("this probe takes no arguments", file=sys.stderr)
        return 2
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ.setdefault(name, "1")
    engines = _probe_engines()
    cache_path = configured_cache_path()
    found, seen, deltas, detail_counts, dropped, notes = _discover(engines)
    if dropped:
        notes.append(
            f"unpickleable requests dropped: {dropped.get('unpickleable', 0)}"
        )
    return _finish(
        engines=engines,
        cache_path=cache_path,
        found=found,
        seen=seen,
        deltas=deltas,
        detail_counts=detail_counts,
        notes=notes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
