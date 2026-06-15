"""Post-process diagnostic instruments (additive, cache-neutral)."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

_EPS = 1e-12


def wall_deposit_remobilization_by_segment_species(
    sim: Any,
    *,
    snapshots: Sequence[Any] | None = None,
    cumulative_deposits_kg: Mapping[tuple[str, str], float] | None = None,
    through_hour: int | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Tag whether later segment wall temperatures exceed species condensation thresholds.

    Each row exposes ``thermal_remobilization_threshold_exceeded`` — a boolean
    comparing later max segment ``pipe_segment_temperatures_C`` against the
    ~1 mbar operator-routing condensation setpoint. Pressure, Knudsen number,
    regime factor, and vapor flux are **not** modeled; ``re_evaporated_kg`` is
    always ``None``. This is a thermal threshold flag, not a mass-transfer or
    re-evaporation result.

    Read-only diagnostic: does not mutate ledger, scores, or cache keys.
    """
    if snapshots is None:
        record = getattr(sim, "record", None)
        snapshots = tuple(getattr(record, "snapshots", ()) or ())
    else:
        snapshots = tuple(snapshots)

    if cumulative_deposits_kg is None:
        cumulative_deposits_kg = _cumulative_wall_deposit_kg(
            snapshots,
            through_hour=through_hour,
        )
    else:
        cumulative_deposits_kg = {
            (str(segment), str(species)): float(kg)
            for (segment, species), kg in cumulative_deposits_kg.items()
            if float(kg) > _EPS
        }

    if not cumulative_deposits_kg:
        return {}

    deposit_last_hour = _deposit_last_hour_by_segment_species(
        snapshots,
        through_hour=through_hour,
    )
    history_hours = _operating_history_hours(sim, snapshots)
    condensation_model = getattr(sim, "condensation_model", None)
    instance_temps = getattr(condensation_model, "condensation_temperatures_C", None)
    from simulator.condensation import _species_condensation_temperature_C

    result: dict[str, dict[str, dict[str, Any]]] = {}
    for (segment, species), deposited_kg in cumulative_deposits_kg.items():
        last_hour = deposit_last_hour.get((segment, species))
        later_max_T_C = _later_max_segment_temperature_C(
            segment,
            deposit_last_hour=last_hour,
            history_hours=history_hours,
            through_hour=through_hour,
        )
        condensation_T_C = _species_condensation_temperature_C(
            species,
            temps=instance_temps,
        )
        threshold_exceeded = (
            later_max_T_C is not None
            and later_max_T_C > condensation_T_C
        )
        result.setdefault(segment, {})[species] = {
            "deposited_kg": float(deposited_kg),
            "deposit_last_hour": last_hour,
            "later_max_T_C": later_max_T_C,
            "condensation_T_C": float(condensation_T_C),
            "thermal_remobilization_threshold_exceeded": bool(threshold_exceeded),
            "re_evaporated_kg": None,
            "pressure_and_flux_modeled": False,
        }
    return result


def _cumulative_wall_deposit_kg(
    snapshots: Sequence[Any],
    *,
    through_hour: int | None,
) -> dict[tuple[str, str], float]:
    totals: dict[tuple[str, str], float] = {}
    for snapshot in snapshots:
        hour = _snapshot_hour(snapshot)
        if through_hour is not None and hour > through_hour:
            continue
        raw = getattr(snapshot, "wall_deposit_by_segment_species_delta", None)
        if not isinstance(raw, Mapping):
            continue
        for key, kg in raw.items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            segment, species = str(key[0]), str(key[1])
            amount = _finite_float(kg)
            if amount is None or amount <= _EPS:
                continue
            pair = (segment, species)
            totals[pair] = totals.get(pair, 0.0) + amount
    return totals


def _deposit_last_hour_by_segment_species(
    snapshots: Sequence[Any],
    *,
    through_hour: int | None,
) -> dict[tuple[str, str], int]:
    last_hour: dict[tuple[str, str], int] = {}
    for snapshot in snapshots:
        hour = _snapshot_hour(snapshot)
        if through_hour is not None and hour > through_hour:
            continue
        raw = getattr(snapshot, "wall_deposit_by_segment_species_delta", None)
        if not isinstance(raw, Mapping):
            continue
        for key, kg in raw.items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            amount = _finite_float(kg)
            if amount is None or amount <= _EPS:
                continue
            pair = (str(key[0]), str(key[1]))
            last_hour[pair] = hour
    return last_hour


def _operating_history_hours(
    sim: Any,
    snapshots: Sequence[Any],
) -> list[tuple[int, Mapping[str, Any]]]:
    model = getattr(sim, "condensation_model", None)
    history = tuple(getattr(model, "operating_history", ()) or ())
    snapshot_hours = [_snapshot_hour(snapshot) for snapshot in snapshots]
    resolved: list[tuple[int, Mapping[str, Any]]] = []
    for index, entry in enumerate(history):
        if not isinstance(entry, Mapping):
            continue
        hour = _resolve_operating_history_hour(entry, index, snapshot_hours)
        if hour is None:
            continue
        resolved.append((hour, entry))
    return resolved


def _resolve_operating_history_hour(
    entry: Mapping[str, Any],
    index: int,
    snapshot_hours: Sequence[int],
) -> int | None:
    if "hour" in entry:
        return _positive_int(entry["hour"])
    if "campaign_hour" in entry:
        campaign_hour = _positive_int(entry["campaign_hour"])
        if campaign_hour is not None:
            return campaign_hour
    if index < len(snapshot_hours):
        return int(snapshot_hours[index])
    if snapshot_hours:
        return int(snapshot_hours[-1])
    return index + 1 if index >= 0 else None


def _later_max_segment_temperature_C(
    segment: str,
    *,
    deposit_last_hour: int | None,
    history_hours: Sequence[tuple[int, Mapping[str, Any]]],
    through_hour: int | None,
) -> float | None:
    if deposit_last_hour is None:
        return None
    later_max: float | None = None
    for hour, entry in history_hours:
        if hour <= deposit_last_hour:
            continue
        if through_hour is not None and hour > through_hour:
            continue
        segment_temperatures = entry.get("pipe_segment_temperatures_C", {}) or {}
        if not isinstance(segment_temperatures, Mapping):
            continue
        temperature = _finite_float(segment_temperatures.get(segment))
        if temperature is None:
            continue
        later_max = (
            temperature
            if later_max is None
            else max(later_max, temperature)
        )
    return later_max


def _snapshot_hour(snapshot: Any) -> int:
    hour = getattr(snapshot, "hour", None)
    if hour is None:
        return 0
    return int(hour)


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


__all__ = ["wall_deposit_remobilization_by_segment_species"]