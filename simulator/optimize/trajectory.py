"""Compact per-hour trajectory stored with an optimizer point.

One owner for the trajectory schema, the projection from an already-built
run report, and the cache-key reader. This module does not recompute
vapour, capture, or wall physics. It copies report fields and applies
only the unit projections those fields already imply (bar to mbar, kg
this hour to kg/h, tap mol to kg/h).
"""

from __future__ import annotations

from functools import lru_cache
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from simulator.accounting.formulas import resolve_species_formula
from simulator.diagnostics import _flatten_wall_deposit_species_kg
from simulator.runner import _MBAR_TO_BAR

TRAJECTORY_SCHEMA_VERSION = 1
TRAJECTORY_STATUS_AVAILABLE = "available"
TRAJECTORY_STATUS_UNAVAILABLE = "trajectory unavailable"

_REQUIRED_HOUR_KEYS = (
    "hour",
    "campaign",
    "T_C",
    "p_total_mbar",
    "pO2_mbar",
    "vapour_kg_h",
    "capture_kg_h",
    "wall_deposit_kg_h",
)
_HOUR_MAP_KEYS = ("vapour_kg_h", "capture_kg_h", "wall_deposit_kg_h")
_ZERO_RATE = 1.0e-12
_LIQUID_FE_TAPPED = "liquid_fe_tapped"


class TrajectorySchemaError(ValueError):
    """A stored v1 trajectory is missing a required field or has a bad shape."""


def project_trajectory(
    per_hour: Any,
    snapshots: Any = None,
) -> dict[str, Any]:
    """Project one compact trajectory from a run's per-hour report.

    ``per_hour`` rows are the runner summaries already attached to the
    execution. ``snapshots`` supply condenser-stage deltas, which the
    per-hour report does not carry. Missing or non-finite sources become
    null or empty maps. The result is always schema v1 with status
    ``available``, including when the run recorded no hours.
    """

    rows = _as_sequence(per_hour)
    snaps = _as_sequence(snapshots)
    hours: list[dict[str, Any]] = []
    for index, entry in enumerate(rows, start=1):
        if not isinstance(entry, Mapping):
            continue
        snapshot = snaps[index - 1] if index - 1 < len(snaps) else None
        hours.append(_project_hour(entry, snapshot, index))
    return {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "status": TRAJECTORY_STATUS_AVAILABLE,
        "hours": hours,
    }


def trajectory_stored_bytes(trajectory: Mapping[str, Any]) -> int:
    """Byte size of the trajectory object as the results store encodes JSON."""

    encoded = json.dumps(
        trajectory,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return len(encoded)


def decode_stored_trajectory(value: Any) -> dict[str, Any]:
    """Return a stored trajectory, or the unavailable marker.

    A missing document, a non-object, or an unknown schema version is
    unavailable and does not raise. A v1 document marked available that
    drops a required hour field raises ``TrajectorySchemaError``.
    """

    if not isinstance(value, Mapping):
        return _unavailable()
    if value.get("schema_version") != TRAJECTORY_SCHEMA_VERSION:
        return _unavailable()
    if value.get("status") != TRAJECTORY_STATUS_AVAILABLE:
        if "status" not in value or "hours" not in value:
            raise TrajectorySchemaError(
                "trajectory v1 is missing status or hours"
            )
        return _unavailable()
    if "hours" not in value:
        raise TrajectorySchemaError("trajectory v1 is missing hours")
    hours = value.get("hours")
    if not isinstance(hours, list):
        raise TrajectorySchemaError("trajectory hours is not a list")
    normalized: list[dict[str, Any]] = []
    for index, hour in enumerate(hours):
        if not isinstance(hour, Mapping):
            raise TrajectorySchemaError(
                f"trajectory hour {index} is not an object"
            )
        missing = [key for key in _REQUIRED_HOUR_KEYS if key not in hour]
        if missing:
            raise TrajectorySchemaError(
                f"trajectory hour {index} missing {', '.join(missing)}"
            )
        for key in _HOUR_MAP_KEYS:
            if not isinstance(hour[key], Mapping):
                raise TrajectorySchemaError(
                    f"trajectory hour {index} {key} is not an object"
                )
            _require_number_map(hour[key], f"trajectory hour {index} {key}")
        if not isinstance(hour["hour"], int) or isinstance(hour["hour"], bool):
            raise TrajectorySchemaError(
                f"trajectory hour {index} hour is not an integer"
            )
        campaign = hour["campaign"]
        if campaign is not None and not isinstance(campaign, str):
            raise TrajectorySchemaError(
                f"trajectory hour {index} campaign is not a string"
            )
        for key in ("T_C", "p_total_mbar", "pO2_mbar"):
            _require_optional_number(hour[key], f"trajectory hour {index} {key}")
        normalized.append({key: hour[key] for key in _REQUIRED_HOUR_KEYS})
    return {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "status": TRAJECTORY_STATUS_AVAILABLE,
        "hours": normalized,
    }


def read_trajectory(store: Any, cache_key: str) -> dict[str, Any]:
    """Return the trajectory stored for one results-store cache key.

    Lookup is by cache key only. An old row with no trajectory field, a
    missing row, or an unreadable blob returns the unavailable marker.
    A v1 trajectory that drops a required field raises
    ``TrajectorySchemaError``.
    """

    if not isinstance(cache_key, str) or not cache_key:
        return _unavailable()
    connect = getattr(store, "_connect", None)
    if not callable(connect):
        raise TypeError("trajectory reader requires a result store")
    with connect() as connection:
        row = connection.execute(
            "SELECT result_blob FROM results WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if row is None:
        return _unavailable()
    raw = row["result_blob"]
    try:
        blob = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError, ValueError):
        return _unavailable()
    if not isinstance(blob, Mapping) or "trajectory" not in blob:
        return _unavailable()
    return decode_stored_trajectory(blob["trajectory"])


def _project_hour(
    entry: Mapping[str, Any],
    snapshot: Any,
    index: int,
) -> dict[str, Any]:
    duration_h = _duration_h(snapshot)
    capture = _capture_kg_h(snapshot, duration_h)
    tapped = _liquid_fe_tapped_kg_h(entry, duration_h)
    if tapped is not None:
        capture[_LIQUID_FE_TAPPED] = tapped
        capture = _sorted_map(capture)
    return {
        "hour": _hour_index(entry, index),
        "campaign": _campaign(entry),
        "T_C": _optional_rounded(entry, "T_C"),
        "p_total_mbar": _mbar_from_bar_field(entry, "P_total_bar"),
        "pO2_mbar": _mbar_from_bar_field(entry, "pO2_bar"),
        "vapour_kg_h": _kg_h_map(entry.get("vapor_species_kg_hr")),
        "capture_kg_h": capture,
        "wall_deposit_kg_h": _wall_deposit_kg_h(entry, duration_h),
    }


def _wall_deposit_kg_h(entry: Mapping[str, Any], duration_h: float) -> dict[str, float]:
    rate = entry.get("wall_deposit_rate_by_species_kg_h")
    if isinstance(rate, Mapping):
        return _kg_h_map(rate)
    # The runner emits kg this hour. kg/h is that committed delta divided
    # by the snapshot duration, not a second deposition model.
    flattened = _flatten_wall_deposit_species_kg(entry.get("wall_deposit_delta_kg") or {})
    rates: dict[str, float] = {}
    for species, kg in flattened.items():
        rounded = _rounded(kg / duration_h)
        if rounded is None or abs(rounded) <= _ZERO_RATE:
            continue
        rates[str(species)] = rounded
    return _sorted_map(rates)


def _capture_kg_h(snapshot: Any, duration_h: float) -> dict[str, float]:
    delta = _snapshot_value(snapshot, "condensed_by_stage_species_delta")
    if not isinstance(delta, Mapping):
        return {}
    totals: dict[str, float] = {}
    for key, raw in delta.items():
        parsed = _stage_species_key(key)
        if parsed is None:
            continue
        stage_key = _stage_token(parsed[0])
        if stage_key is None:
            continue
        numeric = _finite(raw)
        if numeric is None:
            continue
        totals[stage_key] = totals.get(stage_key, 0.0) + numeric
    rates: dict[str, float] = {}
    for stage_key, kg in totals.items():
        rounded = _rounded(kg / duration_h)
        if rounded is None or abs(rounded) <= _ZERO_RATE:
            continue
        rates[stage_key] = rounded
    return _sorted_map(rates)


def _liquid_fe_tapped_kg_h(
    entry: Mapping[str, Any],
    duration_h: float,
) -> float | None:
    redox = entry.get("fe_redox_split")
    if not isinstance(redox, Mapping):
        return None
    partition = redox.get("native_fe_partition")
    if not isinstance(partition, Mapping) or "native_fe_tap_mol" not in partition:
        return None
    mol = _finite(partition.get("native_fe_tap_mol"))
    if mol is None or abs(mol) <= _ZERO_RATE:
        return None
    # Unit projection of the committed tap (mol this hour -> kg/h).
    kg_h = mol * _fe_molar_mass_kg_per_mol() / duration_h
    rounded = _rounded(kg_h)
    if rounded is None or abs(rounded) <= _ZERO_RATE:
        return None
    return rounded


@lru_cache(maxsize=1)
def _fe_molar_mass_kg_per_mol() -> float:
    return float(resolve_species_formula("Fe").molar_mass_kg_per_mol())


def _mbar_from_bar_field(entry: Mapping[str, Any], key: str) -> float | None:
    if key not in entry:
        return None
    numeric = _finite(entry.get(key))
    if numeric is None:
        return None
    # Report pressures are bar. mbar = bar / _MBAR_TO_BAR, the runner's factor.
    return _rounded(numeric / _MBAR_TO_BAR)


def _kg_h_map(raw: Any) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    rates: dict[str, float] = {}
    for key, value in raw.items():
        numeric = _finite(value)
        if numeric is None or abs(numeric) <= _ZERO_RATE:
            continue
        rounded = _rounded(numeric)
        if rounded is None or abs(rounded) <= _ZERO_RATE:
            continue
        rates[str(key)] = rounded
    return _sorted_map(rates)


def _optional_rounded(entry: Mapping[str, Any], key: str) -> float | None:
    if key not in entry:
        return None
    return _rounded(entry.get(key))


def _rounded(value: Any) -> float | None:
    numeric = _finite(value)
    if numeric is None:
        return None
    return float(format(numeric, ".6g"))


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return numeric


def _duration_h(snapshot: Any) -> float:
    raw = _snapshot_value(snapshot, "duration_h")
    numeric = _finite(raw)
    if numeric is None or numeric <= 0.0:
        return 1.0
    return numeric


def _snapshot_value(snapshot: Any, key: str) -> Any:
    if snapshot is None:
        return None
    if isinstance(snapshot, Mapping):
        return snapshot.get(key)
    return getattr(snapshot, key, None)


def _hour_index(entry: Mapping[str, Any], fallback: int) -> int:
    raw = entry.get("hour", fallback)
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and math.isfinite(raw) and raw.is_integer():
        return int(raw)
    return fallback


def _campaign(entry: Mapping[str, Any]) -> str | None:
    raw = entry.get("campaign")
    if raw is None:
        return None
    name = getattr(raw, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(raw, str):
        return raw
    return str(raw)


def _stage_species_key(key: Any) -> tuple[Any, Any] | None:
    if isinstance(key, (tuple, list)) and len(key) == 2:
        return key[0], key[1]
    return None


def _stage_token(stage: Any) -> str | None:
    if isinstance(stage, bool):
        return None
    if isinstance(stage, int):
        return str(stage)
    if isinstance(stage, float) and math.isfinite(stage) and stage.is_integer():
        return str(int(stage))
    if isinstance(stage, str) and stage.strip():
        return stage.strip()
    return None


def _sorted_map(rates: Mapping[str, float]) -> dict[str, float]:
    return {key: rates[key] for key in sorted(rates)}


def _as_sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _unavailable() -> dict[str, Any]:
    return {
        "status": TRAJECTORY_STATUS_UNAVAILABLE,
        "schema_version": None,
        "hours": None,
    }


def _require_number_map(value: Mapping[str, Any], label: str) -> None:
    for key, item in value.items():
        if not _is_json_number(item):
            raise TrajectorySchemaError(f"{label}[{key!s}] is not a finite number")


def _require_optional_number(value: Any, label: str) -> None:
    if value is None:
        return
    if not _is_json_number(value):
        raise TrajectorySchemaError(f"{label} is not a finite number")


def _is_json_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return True
