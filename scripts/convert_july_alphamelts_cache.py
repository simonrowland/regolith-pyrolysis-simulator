"""Classify the July alphaMELTS grid and emit only rows today's adapter still sends.

The July accumulator stored a Kress91-partitioned composition and then launched
alphaMELTS with menu option 3, starting guess 2 (superliquidus at the liquidus).
Today's subprocess adapter does not send that menu. A row is emitted only when
the stored launch is the launch ``AlphaMELTSBackend.equilibrate`` sends now,
the melts file matches, and a replay of that call matches the stored fields.

This script does not hash cache keys and does not write HOT or COLD rows.
The engine lane owns ``simulator/engine_cache.py``.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping
from unittest.mock import patch

from simulator.config import resolve_alphamelts_subprocess_model
from simulator.engine_local_config import find_alphamelts_binary
from simulator.melt_backend.alphamelts import (
    AlphaMELTSBackend,
    serialize_melts_file_temperature_C,
)
from simulator.melt_backend.alphamelts_contract import (
    AlphaMELTSSubprocessRunMode,
)
from simulator.melt_backend.base import (
    REDOX_MODE_NOT_AN_INPUT,
)

_EXEC_T_MARK = "Initial alphaMELTS calculation at:"
_HOT_FIELDS = (
    "status",
    "refusal_reason",
    "liquid_fraction",
    "liquidus_T_C",
    "fO2_log",
    "liquid_density_kg_m3",
    "liquid_viscosity_Pa_s",
    "phases_present",
    "phase_masses_kg",
    "liquid_composition_wt_pct",
    "activity_coefficients",
    "vapor_pressures_Pa",
)


def _normalize_stdin(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").strip("\n") + "\n"


def chemistry_version() -> str | None:
    """The engine-lane constant, when this tree has it. Never invent one."""
    try:
        from simulator.engine_cache import ENGINE_CHEMISTRY_VERSION
    except ImportError:
        return None
    value = ENGINE_CHEMISTRY_VERSION.get("alphamelts")
    if value is None:
        return None
    return str(value)


def binary_sha256() -> str | None:
    path = find_alphamelts_binary()
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_backend() -> AlphaMELTSBackend:
    backend = AlphaMELTSBackend()
    backend._mode = "subprocess"
    backend._binary_path = Path("/nonexistent/alphamelts2-probe")
    backend._engine_path = backend._binary_path
    backend._last_normalization_warnings = []
    return backend


def _capture_launch(backend: AlphaMELTSBackend, call) -> dict[str, Any] | None:
    captured: dict[str, Any] = {}

    def fake_run(args, **kwargs):
        cwd = Path(kwargs["cwd"])
        captured["stdin"] = kwargs.get("input")
        captured["melts"] = (cwd / "input.melts").read_text()
        captured["calc_mode"] = kwargs["env"].get("ALPHAMELTS_CALC_MODE")
        captured["argv"] = list(args)
        return SimpleNamespace(returncode=-6, stdout="", stderr="probe")

    with patch(
        "simulator.melt_backend.alphamelts._run_alphamelts_subprocess",
        fake_run,
    ):
        call()
    if "stdin" not in captured:
        return None
    return captured


def today_run_menus() -> dict[str, str]:
    """Stdin today's subprocess adapter sends, one entry per run mode.

    Read from the adapter by a probe launch. The menu string is not copied here.
    """
    composition = {
        "SiO2": 50.0,
        "Al2O3": 15.0,
        "FeO": 10.0,
        "MgO": 10.0,
        "CaO": 10.0,
        "Na2O": 5.0,
    }
    menus: dict[str, str] = {}
    for run_mode in (
        AlphaMELTSSubprocessRunMode.ISOTHERMAL,
        AlphaMELTSSubprocessRunMode.LIQUIDUS_FINDER,
    ):
        backend = _probe_backend()

        def call(backend=backend, run_mode=run_mode):
            backend._equilibrate_subprocess(
                1400.0,
                composition,
                -9.0,
                1.0,
                total_input_kg=100.0,
                run_mode=run_mode,
            )

        captured = _capture_launch(backend, call)
        if captured is None:
            raise RuntimeError(f"adapter did not launch for {run_mode.value}")
        menus[run_mode.value] = _normalize_stdin(captured["stdin"])
    return menus


def melts_signature(text: str) -> dict[str, Any]:
    """Engine-input identity of a .melts file, using the adapter's T contract."""
    oxides: dict[str, str] = {}
    temperature_C = None
    pressure_token = None
    fo2_path = None
    fo2_offset = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("Initial Composition:"):
            name, weight = line.split(":", 1)[1].split()
            oxides[name] = weight
        elif line.startswith("Initial Temperature:"):
            temperature_C = float(line.split(":", 1)[1])
        elif line.startswith("Initial Pressure:"):
            pressure_token = line.split(":", 1)[1].strip()
        elif line.startswith("Log fO2 Path:"):
            fo2_path = line.split(":", 1)[1].strip()
        elif line.startswith("Log fO2 Offset:"):
            fo2_offset = line.split(":", 1)[1].strip()
    if temperature_C is None or pressure_token is None:
        raise ValueError("melts file lacks temperature or pressure")
    return {
        "oxides": oxides,
        "temperature_contract_C": serialize_melts_file_temperature_C(
            temperature_C
        ),
        "pressure_bar": float(pressure_token),
        "fo2_path": fo2_path,
        "fo2_offset": None if fo2_offset is None else float(fo2_offset),
    }


def signatures_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if left["oxides"] != right["oxides"]:
        return False
    if left["fo2_path"] != right["fo2_path"]:
        return False
    if not math.isclose(
        float(left["temperature_contract_C"]),
        float(right["temperature_contract_C"]),
        rel_tol=0.0,
        abs_tol=0.0,
    ):
        return False
    if not math.isclose(
        float(left["pressure_bar"]),
        float(right["pressure_bar"]),
        rel_tol=0.0,
        abs_tol=0.0,
    ):
        return False
    if left["fo2_offset"] is None or right["fo2_offset"] is None:
        return left["fo2_offset"] is None and right["fo2_offset"] is None
    return math.isclose(
        float(left["fo2_offset"]),
        float(right["fo2_offset"]),
        rel_tol=0.0,
        abs_tol=0.0,
    )


def _executed_temperature_C(stdout: str) -> float | None:
    found = None
    for line in stdout.splitlines():
        if _EXEC_T_MARK not in line:
            continue
        # "Initial alphaMELTS calculation at: P 1.000000 (bars), T 1317.773438 (C)"
        marker = " T "
        start = line.rfind(marker)
        if start < 0:
            continue
        token = line[start + len(marker):].split()[0]
        found = float(token)
    return found


def _calc_mode_line(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if "Calculation mode" in line:
            return line.strip()
    return None


def parse_capture(raw_payload: str) -> dict[str, Any]:
    payload = json.loads(raw_payload)
    captures = payload.get("captures") or []
    if not captures:
        raise ValueError("capture missing")
    capture = captures[0]
    input_file = str(capture.get("input_file") or "")
    return {
        "stdin": _normalize_stdin(str(capture.get("stdin") or "")),
        "input_file": input_file,
        "executed_temperature_C": _executed_temperature_C(
            str(capture.get("stdout") or "")
        ),
        "calc_mode_line": _calc_mode_line(str(capture.get("stdout") or "")),
        "file_has_fo2": "Log fO2" in input_file,
        "payload_fo2_constraint": payload.get("fO2_constraint"),
    }


def _json_object(text: str | None) -> dict[str, Any] | None:
    if text is None or text == "":
        return None
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def request_kwargs_from_row(
    row: Mapping[str, Any],
    capture: Mapping[str, Any],
    run_mode: str,
) -> dict[str, Any]:
    """Public equilibrate kwargs that reproduce this row's melts file."""
    signature = melts_signature(str(capture["input_file"]))
    composition_mol = _json_object(row.get("composition_mol_json"))
    composition_kg = _json_object(row.get("composition_kg_json"))
    account = _json_object(row.get("composition_mol_by_account_json"))
    kwargs: dict[str, Any] = {
        "temperature_C": float(row["temperature_C"]),
        "pressure_bar": float(row["pressure_bar"]),
        "subprocess_run_mode": run_mode,
    }
    if composition_mol is not None:
        kwargs["composition_mol"] = composition_mol
        kwargs["composition_kg"] = None
    elif composition_kg is not None:
        kwargs["composition_kg"] = composition_kg
    else:
        raise ValueError("row has neither composition_mol nor composition_kg")
    if account is not None:
        kwargs["composition_mol_by_account"] = account
    if signature["fo2_path"] is None:
        kwargs["fO2_log"] = None
        kwargs["redox_mode"] = REDOX_MODE_NOT_AN_INPUT
    elif signature["fo2_path"] == "Absolute":
        kwargs["fO2_log"] = float(signature["fo2_offset"])
        kwargs["redox_mode"] = None
    else:
        raise ValueError(
            f"melts fO2 path {signature['fo2_path']!r} is a buffer setting, "
            "not an equilibrate kwarg"
        )
    return kwargs


def capture_today_melts(kwargs: Mapping[str, Any]) -> dict[str, Any] | None:
    backend = _probe_backend()
    call_kwargs = dict(kwargs)

    def call():
        backend.equilibrate(**call_kwargs)

    return _capture_launch(backend, call)


def stored_hot(row: Mapping[str, Any]) -> dict[str, Any]:
    def maybe_json(text: str | None):
        if text is None or text == "":
            return None
        return json.loads(text)

    return {
        "status": row.get("status"),
        "refusal_reason": row.get("refusal_reason"),
        "liquid_fraction": row.get("generic_liquid_fraction"),
        "liquidus_T_C": row.get("generic_liquidus_T_C"),
        "fO2_log": row.get("generic_fO2_log"),
        "liquid_density_kg_m3": row.get("generic_liquid_density_kg_m3"),
        "liquid_viscosity_Pa_s": row.get("generic_liquid_viscosity_Pa_s"),
        "phases_present": maybe_json(row.get("generic_phases_present_json")) or [],
        "phase_masses_kg": maybe_json(row.get("generic_phase_masses_kg_json")) or {},
        "liquid_composition_wt_pct": maybe_json(
            row.get("generic_liquid_composition_wt_pct_json")
        ) or {},
        "activity_coefficients": maybe_json(
            row.get("generic_activity_coefficients_json")
        ) or {},
        "vapor_pressures_Pa": maybe_json(
            row.get("generic_vapor_pressures_Pa_json")
        ) or {},
    }


def result_hot(result: Any) -> dict[str, Any]:
    diagnostics = dict(getattr(result, "diagnostics", None) or {})
    return {
        "status": result.status,
        "refusal_reason": diagnostics.get("backend_status_reason"),
        "liquid_fraction": result.liquid_fraction,
        "liquidus_T_C": result.liquidus_T_C,
        "fO2_log": result.fO2_log,
        "liquid_density_kg_m3": result.liquid_density_kg_m3,
        "liquid_viscosity_Pa_s": result.liquid_viscosity_Pa_s,
        "phases_present": list(result.phases_present),
        "phase_masses_kg": dict(result.phase_masses_kg),
        "liquid_composition_wt_pct": dict(result.liquid_composition_wt_pct),
        "activity_coefficients": dict(result.activity_coefficients),
        "vapor_pressures_Pa": dict(result.vapor_pressures_Pa),
    }


def _values_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right or left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(
            float(left), float(right), rel_tol=0.0, abs_tol=0.0
        )
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            return False
        return all(_values_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return False
        return all(_values_equal(a, b) for a, b in zip(left, right))
    return left == right


def hot_differences(
    stored: Mapping[str, Any], replayed: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    differences: dict[str, dict[str, Any]] = {}
    for field in _HOT_FIELDS:
        if _values_equal(stored.get(field), replayed.get(field)):
            continue
        differences[field] = {
            "stored": stored.get(field),
            "replayed": replayed.get(field),
        }
    return differences


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float in result: {value!r}")
        return value
    if dataclasses.is_dataclass(value):
        return {
            item.name: _jsonable(getattr(value, item.name))
            for item in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    return connection


def convert_database(
    db_path: Path,
    jsonl_path: Path,
    *,
    menus: Mapping[str, str] | None = None,
    replay: Callable[[Mapping[str, Any]], Any] | None = None,
    chem_version: str | None = None,
    settings: Mapping[str, Any] | None = None,
    counts_path: Path | None = None,
) -> dict[str, Any]:
    """Classify every row. Emit a JSONL record only for a replay-equal row."""
    run_menus = dict(menus or today_run_menus())
    menu_to_mode = {_normalize_stdin(stdin): mode for mode, stdin in run_menus.items()}
    connection = connect_readonly(db_path)
    reasons: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    status_reason: Counter[tuple[str, str]] = Counter()
    stdin_counts: Counter[str] = Counter()
    file_fo2 = Counter()
    calc_mode_lines: Counter[str] = Counter()
    executed_deltas: list[float] = []
    emitted = 0
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        columns = {
            row[1]
            for row in connection.execute("pragma table_info(alphamelts_outputs)")
        }
        density_sql = (
            "o.generic_liquid_density_kg_m3"
            if "generic_liquid_density_kg_m3" in columns
            else "NULL AS generic_liquid_density_kg_m3"
        )
        viscosity_sql = (
            "o.generic_liquid_viscosity_Pa_s"
            if "generic_liquid_viscosity_Pa_s" in columns
            else "NULL AS generic_liquid_viscosity_Pa_s"
        )
        query = f"""
            select
                o.id as july_row_id,
                o.status,
                o.refusal_reason,
                o.raw_payload,
                o.generic_liquid_fraction,
                o.generic_liquidus_T_C,
                o.generic_fO2_log,
                {density_sql},
                {viscosity_sql},
                o.generic_phases_present_json,
                o.generic_phase_masses_kg_json,
                o.generic_liquid_composition_wt_pct_json,
                o.generic_activity_coefficients_json,
                o.generic_vapor_pressures_Pa_json,
                k.temperature_C,
                k.pressure_bar,
                k.fO2_log as key_fO2_log,
                k.intended_fO2_log,
                k.composition_mol_json,
                k.composition_kg_json,
                k.composition_mol_by_account_json,
                k.model,
                k.redox_buffer,
                k.fO2_offset,
                k.fe3fet_ratio
            from alphamelts_outputs o
            join grid_keys k on k.id = o.grid_key_id
        """
        with jsonl_path.open("w", encoding="utf-8") as sink:
            for row in connection.execute(query):
                record = dict(row)
                status = str(record["status"])
                by_status[status] += 1
                try:
                    capture = parse_capture(str(record["raw_payload"]))
                except (json.JSONDecodeError, ValueError, KeyError) as exc:
                    reason = f"raw_payload_unreadable:{type(exc).__name__}"
                    reasons[reason] += 1
                    status_reason[(status, reason)] += 1
                    continue
                stdin_counts[capture["stdin"]] += 1
                file_fo2[bool(capture["file_has_fo2"])] += 1
                calc_mode_lines[capture["calc_mode_line"] or "(absent)"] += 1
                executed = capture["executed_temperature_C"]
                if executed is not None:
                    executed_deltas.append(
                        abs(float(executed) - float(record["temperature_C"]))
                    )
                run_mode = menu_to_mode.get(capture["stdin"])
                if run_mode is None:
                    reason = "july_run_menu_not_today"
                    reasons[reason] += 1
                    status_reason[(status, reason)] += 1
                    continue
                try:
                    kwargs = request_kwargs_from_row(record, capture, run_mode)
                    today = capture_today_melts(kwargs)
                except (ValueError, TypeError) as exc:
                    reason = f"request_not_expressible:{type(exc).__name__}"
                    reasons[reason] += 1
                    status_reason[(status, reason)] += 1
                    continue
                if today is None:
                    reason = "today_refuses_before_launch"
                    reasons[reason] += 1
                    status_reason[(status, reason)] += 1
                    continue
                if _normalize_stdin(today["stdin"]) != capture["stdin"]:
                    reason = "july_run_menu_not_today"
                    reasons[reason] += 1
                    status_reason[(status, reason)] += 1
                    continue
                try:
                    same_file = signatures_equal(
                        melts_signature(capture["input_file"]),
                        melts_signature(today["melts"]),
                    )
                except ValueError:
                    same_file = False
                if not same_file:
                    reason = "melts_file_not_today"
                    reasons[reason] += 1
                    status_reason[(status, reason)] += 1
                    continue
                if replay is None:
                    reason = "mappable_not_replayed"
                    reasons[reason] += 1
                    status_reason[(status, reason)] += 1
                    continue
                replayed = replay(kwargs)
                differences = hot_differences(
                    stored_hot(record), result_hot(replayed)
                )
                if differences:
                    reason = "dropped_for_drift"
                    reasons[reason] += 1
                    status_reason[(status, reason)] += 1
                    continue
                version = chem_version
                if version is None:
                    raise RuntimeError(
                        "a row is replay-equal but ENGINE_CHEMISTRY_VERSION "
                        "for alphamelts is not on this tree; refusing to invent one"
                    )
                if settings is None:
                    raise RuntimeError(
                        "a row is replay-equal but adapter settings were not supplied"
                    )
                line = {
                    "engine": "alphamelts",
                    "chem_version": version,
                    "request": _jsonable(kwargs),
                    "settings": _jsonable(dict(settings)),
                    "result": _jsonable(replayed),
                    "provenance": {
                        "july_row_id": record["july_row_id"],
                        "replay_verified": True,
                    },
                }
                sink.write(json.dumps(line, sort_keys=True, separators=(",", ":")))
                sink.write("\n")
                emitted += 1
                reasons["converted"] += 1
                status_reason[(status, "converted")] += 1
    finally:
        connection.close()

    executed_summary: dict[str, Any]
    if executed_deltas:
        ordered = sorted(executed_deltas)
        mid = ordered[len(ordered) // 2]
        executed_summary = {
            "n": len(ordered),
            "min_abs_C": ordered[0],
            "median_abs_C": mid,
            "max_abs_C": ordered[-1],
            "within_0_05_C": sum(value < 0.05 for value in ordered),
            "within_1_C": sum(value < 1.0 for value in ordered),
        }
    else:
        executed_summary = {"n": 0}
    summary = {
        "rows": sum(by_status.values()),
        "emitted": emitted,
        "by_status": dict(by_status),
        "by_reason": dict(reasons),
        "by_status_and_reason": {
            f"{status}|{reason}": count
            for (status, reason), count in sorted(status_reason.items())
        },
        "distinct_stdin": len(stdin_counts),
        "stdin_counts": {
            stdin.replace("\n", "\\n"): count
            for stdin, count in stdin_counts.most_common()
        },
        "file_has_log_fo2": {
            "yes" if key else "no": count for key, count in file_fo2.items()
        },
        "july_stdout_calc_mode": dict(calc_mode_lines),
        "executed_minus_requested_T_C": executed_summary,
        "today_run_menus": {
            mode: stdin.replace("\n", "\\n") for mode, stdin in run_menus.items()
        },
        "calc_mode": resolve_alphamelts_subprocess_model("MELTSv1.0.2")[1],
        "chem_version": chem_version if chem_version is not None else chemistry_version(),
        "binary_sha256": (settings or {}).get("binary_sha256", binary_sha256()),
    }
    if counts_path is not None:
        counts_path.parent.mkdir(parents=True, exist_ok=True)
        counts_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return summary


def _evidence_sample(connection: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Spread `limit` rows across status, intended fO2, and temperature bucket.

    cell_rank 1 of every cell comes first, then rank 2, so a limit above the
    cell count still stays stratified.
    """
    columns = {
        row[1]
        for row in connection.execute("pragma table_info(alphamelts_outputs)")
    }

    def present(name: str) -> str:
        if name in columns:
            return f"o.{name}"
        return f"NULL AS {name}"

    query = f"""
        with ranked as (
            select
                o.id as july_row_id,
                o.status,
                o.refusal_reason,
                o.generic_liquid_fraction,
                o.generic_liquidus_T_C,
                o.generic_fO2_log,
                {present("generic_liquid_density_kg_m3")},
                {present("generic_liquid_viscosity_Pa_s")},
                o.generic_phases_present_json,
                o.generic_phase_masses_kg_json,
                o.generic_liquid_composition_wt_pct_json,
                o.generic_activity_coefficients_json,
                o.generic_vapor_pressures_Pa_json,
                k.temperature_C,
                k.intended_fO2_log,
                k.pressure_bar,
                k.composition_mol_json,
                k.composition_mol_by_account_json,
                row_number() over (
                    partition by o.status, k.intended_fO2_log,
                        case
                            when k.temperature_C < 1100 then 'low'
                            when k.temperature_C < 1500 then 'mid'
                            else 'high'
                        end
                    order by o.id
                ) as cell_rank
            from alphamelts_outputs o
            join grid_keys k on k.id = o.grid_key_id
        )
        select * from ranked
        order by cell_rank, status, intended_fO2_log, temperature_C
        limit ?
    """
    return list(connection.execute(query, (int(limit),)))


def _field_delta(stored: Any, replayed: Any) -> dict[str, Any]:
    if isinstance(stored, (int, float)) and isinstance(replayed, (int, float)):
        if stored is None or replayed is None:
            return {"stored": stored, "replayed": replayed}
        absolute = abs(float(replayed) - float(stored))
        baseline = abs(float(stored))
        return {
            "stored": stored,
            "replayed": replayed,
            "abs": absolute,
            "rel": None if baseline == 0.0 else absolute / baseline,
        }
    return {
        "equal": _values_equal(stored, replayed),
        "stored": stored,
        "replayed": replayed,
    }


def write_evidence(
    db_path: Path,
    evidence_path: Path,
    *,
    limit: int,
    timeout_s: float,
) -> dict[str, Any]:
    """Replay today's isothermal call on a stratified sample. Does not emit rows.

    The sample is not a conversion set. It shows how today's result differs
    from the July row when the launch itself does not map.
    """
    import simulator.melt_backend.alphamelts as alphamelts_module
    from simulator.melt_backend.base import EquilibriumResult

    observed = {"calc_mode_line": None, "env_calc_mode": None}
    real_run = alphamelts_module._run_alphamelts_subprocess

    def recording_run(args, **kwargs):
        result = real_run(args, **kwargs)
        if observed["env_calc_mode"] is None:
            env = kwargs.get("env") or {}
            observed["env_calc_mode"] = env.get("ALPHAMELTS_CALC_MODE")
            for line in (result.stdout or "").splitlines():
                if "Calculation mode" in line:
                    observed["calc_mode_line"] = line.strip()
                    break
        return result

    backend = AlphaMELTSBackend()
    ready = backend.initialize({
        "mode": "subprocess",
        "model": "MELTSv1.0.2",
        "timeout_s": float(timeout_s),
        "fO2_buffer": None,
        "fO2_offset": None,
        "Fe3Fet_Liq": None,
    })
    if not ready:
        raise RuntimeError("today's alphaMELTS subprocess adapter is unavailable")
    connection = connect_readonly(db_path)
    rows = _evidence_sample(connection, limit)
    connection.close()
    comparisons = []
    scalar_fields = (
        "liquid_fraction",
        "liquidus_T_C",
        "fO2_log",
        "liquid_density_kg_m3",
        "liquid_viscosity_Pa_s",
    )
    with patch(
        "simulator.melt_backend.alphamelts._run_alphamelts_subprocess",
        recording_run,
    ):
        for index, row in enumerate(rows, start=1):
            record = dict(row)
            composition_mol = json.loads(record["composition_mol_json"])
            account = json.loads(record["composition_mol_by_account_json"])
            kwargs = {
                "temperature_C": float(record["temperature_C"]),
                "pressure_bar": float(record["pressure_bar"]),
                "composition_mol": composition_mol,
                "composition_mol_by_account": account,
                "fO2_log": None,
                "redox_mode": REDOX_MODE_NOT_AN_INPUT,
                "subprocess_run_mode": AlphaMELTSSubprocessRunMode.ISOTHERMAL.value,
            }
            if index == 1 or index % 25 == 0:
                print(
                    f"evidence {index}/{len(rows)} july_row_id={record['july_row_id']}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                result = backend.equilibrate(**kwargs)
            except Exception as exc:  # noqa: BLE001 - evidence records the failure
                comparisons.append({
                    "july_row_id": record["july_row_id"],
                    "july_status": record["status"],
                    "intended_fO2_log": record["intended_fO2_log"],
                    "temperature_C": record["temperature_C"],
                    "cell_rank": record["cell_rank"],
                    "today_exception": f"{type(exc).__name__}: {exc}",
                })
                continue
            if not isinstance(result, EquilibriumResult):
                raise TypeError(f"adapter returned {type(result).__name__}")
            hot = result_hot(result)
            stored = stored_hot(record)
            fields = {}
            for field in _HOT_FIELDS:
                fields[field] = _field_delta(stored.get(field), hot.get(field))
            comparisons.append({
                "july_row_id": record["july_row_id"],
                "july_status": stored["status"],
                "july_refusal_reason": stored["refusal_reason"],
                "intended_fO2_log": record["intended_fO2_log"],
                "temperature_C": record["temperature_C"],
                "cell_rank": record["cell_rank"],
                "today_status": hot["status"],
                "today_refusal_reason": hot["refusal_reason"],
                "today_temperature_C": result.temperature_C,
                "fields": fields,
                "status_equal": stored["status"] == hot["status"],
                "refusal_equal": stored["refusal_reason"] == hot["refusal_reason"],
            })
    backend.close()
    max_abs: dict[str, float | None] = {}
    max_rel: dict[str, float | None] = {}
    for field in scalar_fields:
        absolutes = []
        relatives = []
        for item in comparisons:
            delta = (item.get("fields") or {}).get(field)
            if not isinstance(delta, dict) or "abs" not in delta:
                continue
            absolutes.append(delta["abs"])
            if delta["rel"] is not None:
                relatives.append(delta["rel"])
        max_abs[field] = max(absolutes) if absolutes else None
        max_rel[field] = max(relatives) if relatives else None
    report = {
        "n": len(comparisons),
        "note": (
            "Replay of today's isothermal not_an_input call on a stratified "
            "draw. A row in this file is a conversion-equality sample only "
            "when the July launch menu and melts file match today's adapter. "
            "Field differences here are not a solver tolerance."
        ),
        "request": (
            "today isothermal, fO2_log=None, redox_mode=not_an_input, "
            "the oxygen policy of the July melts file (no Log fO2 line). "
            "The July menu (option 3, guess 2) is not sent."
        ),
        "today_env_calc_mode": observed["env_calc_mode"],
        "today_stdout_calc_mode": observed["calc_mode_line"],
        "max_abs": max_abs,
        "max_rel": max_rel,
        "status_equal": sum(bool(item.get("status_equal")) for item in comparisons),
        "refusal_equal": sum(
            bool(item.get("refusal_equal")) for item in comparisons
        ),
        "exceptions": sum("today_exception" in item for item in comparisons),
        "rows": comparisons,
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--jsonl", type=Path, required=True)
    parser.add_argument("--counts", type=Path, required=True)
    parser.add_argument("--evidence-sample", type=int, default=0)
    parser.add_argument("--evidence-out", type=Path)
    parser.add_argument("--evidence-timeout-s", type=float, default=15.0)
    args = parser.parse_args(argv)
    summary = convert_database(args.db, args.jsonl, counts_path=args.counts)
    print(json.dumps({
        "rows": summary["rows"],
        "emitted": summary["emitted"],
        "by_reason": summary["by_reason"],
        "executed_minus_requested_T_C": summary["executed_minus_requested_T_C"],
        "file_has_log_fo2": summary["file_has_log_fo2"],
        "july_stdout_calc_mode": summary["july_stdout_calc_mode"],
        "chem_version": summary["chem_version"],
        "binary_sha256": summary["binary_sha256"],
    }, indent=2, sort_keys=True))
    if args.evidence_sample:
        if args.evidence_out is None:
            parser.error("--evidence-sample requires --evidence-out")
        evidence = write_evidence(
            args.db,
            args.evidence_out,
            limit=args.evidence_sample,
            timeout_s=args.evidence_timeout_s,
        )
        print(json.dumps({
            "evidence_n": evidence["n"],
            "evidence_max_abs": evidence["max_abs"],
            "evidence_status_equal": evidence["status_equal"],
            "evidence_refusal_equal": evidence["refusal_equal"],
        }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
