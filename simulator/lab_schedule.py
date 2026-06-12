"""Runtime validation and interpolation for vacuum-pyrolysis lab schedules.

``schedule_digest`` is the physics identity: normalized points,
``window_semantics``, and schedule caps. Provenance identity stays in evidence
metadata, so raw ``experiment_windows`` wording and per-point citations do not
enter the digest.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import math
from types import MappingProxyType
from typing import Any

from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value


LAB_SCHEDULE_OVERRIDE_KEY = "lab_schedule"
LAB_SCHEDULE_PO2_SETPOINT_KEY = "lab_schedule_pO2_setpoint_mbar"
LAB_SCHEDULE_PRESSURE_FLOOR_MBAR = 1.0e-3
ASSUMED_WITH_SENSITIVITY = "assumption_with_sensitivity_marker"
SUPPORTED_INTERPOLATION = "piecewise_linear"
LAB_SCHEDULE_DEPOSIT_SAMPLE_BASIS = frozenset(
    {
        "hot",
        "after_cooldown",
        "after_re_evaporation",
        "after_boil_back",
        "not_reported",
    }
)


class LabScheduleValidationError(ValueError):
    """Raised when a declared lab schedule cannot be honored at runtime."""


def normalize_lab_schedule(
    raw: Any,
    *,
    required_surface_profiles: Iterable[str] = (),
) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise LabScheduleValidationError("lab_schedule_must_be_mapping")
    schedule = dict(raw)
    schedule_id = str(schedule.get("id", "") or "").strip()
    if not schedule_id:
        raise LabScheduleValidationError("lab_schedule_missing_id")
    duration_h = _required_finite_positive(schedule.get("duration_h"), "duration_h")
    interpolation = str(schedule.get("interpolation", "") or "").strip()
    if interpolation != SUPPORTED_INTERPOLATION:
        raise LabScheduleValidationError(
            "lab_schedule_interpolation_unsupported: "
            f"expected {SUPPORTED_INTERPOLATION!r}, got {interpolation!r}"
        )
    _validate_interpolation_marker(schedule)

    furnace_ceiling_C = _required_finite_positive(
        schedule.get("furnace_ceiling_C"),
        "furnace_ceiling_C",
    )
    melt_temperature_C = _normalize_points(
        schedule.get("melt_temperature_C"),
        duration_h=duration_h,
        field="melt_temperature_C",
    )
    for point in melt_temperature_C:
        if float(point["value"]) > furnace_ceiling_C:
            raise LabScheduleValidationError(
                "lab_schedule_temperature_exceeds_furnace_ceiling: "
                f"{point['value']:g} C > {furnace_ceiling_C:g} C"
            )
    chamber_pressure_mbar = _normalize_points(
        schedule.get("chamber_pressure_mbar"),
        duration_h=duration_h,
        field="chamber_pressure_mbar",
    )
    for point in chamber_pressure_mbar:
        if float(point["value"]) < LAB_SCHEDULE_PRESSURE_FLOOR_MBAR:
            raise LabScheduleValidationError(
                "lab_schedule_pressure_below_implemented_floor: "
                f"{point['value']:g} mbar < {LAB_SCHEDULE_PRESSURE_FLOOR_MBAR:g} mbar"
            )

    gas_boundary = _normalize_gas_boundary(schedule.get("gas_boundary"))
    surface_temperature_C = _normalize_surface_temperatures(
        schedule.get("surface_temperature_C", {}),
        duration_h=duration_h,
        required_surface_profiles=tuple(str(item) for item in required_surface_profiles),
    )
    window_semantics = _normalize_window_semantics_from_schedule(
        schedule,
        duration_h=duration_h,
    )

    normalized = {
        **schedule,
        "id": schedule_id,
        "duration_h": duration_h,
        "interpolation": interpolation,
        "furnace_ceiling_C": furnace_ceiling_C,
        "melt_temperature_C": melt_temperature_C,
        "chamber_pressure_mbar": chamber_pressure_mbar,
        "gas_boundary": gas_boundary,
        "surface_temperature_C": surface_temperature_C,
        "window_semantics": window_semantics,
    }
    return _freeze(normalized)


def lab_schedule_digests(schedule: Mapping[str, Any]) -> dict[str, str]:
    schedule_physics = {
        "duration_h": schedule.get("duration_h"),
        "interpolation": schedule.get("interpolation"),
        "furnace_ceiling_C": schedule.get("furnace_ceiling_C"),
        "melt_temperature_C": schedule.get("melt_temperature_C", ()),
        "chamber_pressure_mbar": schedule.get("chamber_pressure_mbar", ()),
        "surface_temperature_C": schedule.get("surface_temperature_C", {}),
        "window_semantics": schedule.get("window_semantics", {}),
    }
    if "pO2_cover" in schedule:
        schedule_physics["pO2_cover"] = schedule.get("pO2_cover")
    normalized = normalize_canonical_value(schedule_physics)
    gas_boundary = normalize_canonical_value(schedule.get("gas_boundary", {}))
    return {
        "schedule_digest": _sha256_canonical(normalized),
        "gas_boundary_digest": _sha256_canonical(gas_boundary),
    }


def interpolate_schedule_points(
    points: Sequence[Mapping[str, Any]],
    t_h: float,
) -> float:
    if not points:
        raise LabScheduleValidationError("lab_schedule_points_empty")
    t = float(t_h)
    first = points[0]
    if t <= float(first["t_h"]):
        return float(first["value"])
    for left, right in zip(points, points[1:]):
        left_t = float(left["t_h"])
        right_t = float(right["t_h"])
        if t <= right_t:
            span = right_t - left_t
            if span <= 0.0:
                return float(right["value"])
            frac = (t - left_t) / span
            return float(left["value"]) + frac * (
                float(right["value"]) - float(left["value"])
            )
    return float(points[-1]["value"])


def schedule_sample_time_h(schedule: Mapping[str, Any], campaign_hour: int) -> float:
    duration_h = float(schedule["duration_h"])
    return min(duration_h, max(0.0, float(campaign_hour) + 1.0))


def pO2_setpoint_mbar_from_schedule(
    schedule: Mapping[str, Any],
    overrides: Mapping[str, Any],
    total_pressure_mbar: float,
) -> float:
    if LAB_SCHEDULE_PO2_SETPOINT_KEY in overrides:
        return _finite_non_negative(
            overrides[LAB_SCHEDULE_PO2_SETPOINT_KEY],
            LAB_SCHEDULE_PO2_SETPOINT_KEY,
        )
    if "pO2_mbar" in overrides:
        return _finite_non_negative(overrides["pO2_mbar"], "pO2_mbar")
    cover = schedule.get("pO2_cover")
    if isinstance(cover, Mapping) and bool(cover.get("enabled", False)):
        return _finite_non_negative(cover.get("setpoint_mbar"), "pO2_cover.setpoint_mbar")
    gas = schedule.get("gas_boundary", {}).get("background_gas", {})
    if isinstance(gas, Mapping) and str(gas.get("species", "")).strip().upper() == "O2":
        mole_fraction = _finite_non_negative(gas.get("mole_fraction", 1.0), "background_gas.mole_fraction")
        return total_pressure_mbar * mole_fraction
    return 0.0


def pO2_enforcement_row(
    *,
    hour: int,
    schedule: Mapping[str, Any],
    schedule_time_h: float,
    setpoint_mbar: float,
    total_pressure_mbar: float,
) -> Mapping[str, Any]:
    achieved = min(float(setpoint_mbar), float(total_pressure_mbar))
    limited = float(setpoint_mbar) > float(total_pressure_mbar)
    return MappingProxyType(
        {
            "hour": int(hour),
            "schedule_id": str(schedule.get("id", "")),
            "schedule_time_h": float(schedule_time_h),
            "setpoint_mbar": float(setpoint_mbar),
            "achieved_mbar": float(achieved),
            "p_total_mbar": float(total_pressure_mbar),
            "limited_by_total_pressure": bool(limited),
            "status": (
                "clipped_to_total_pressure"
                if limited
                else "achieved_as_setpoint"
            ),
        }
    )


def _validate_interpolation_marker(schedule: Mapping[str, Any]) -> None:
    source_class = str(schedule.get("interpolation_source_class", "") or "").strip()
    if not source_class:
        raise LabScheduleValidationError("lab_schedule_interpolation_missing_source_class")
    if source_class != ASSUMED_WITH_SENSITIVITY:
        return
    if not str(schedule.get("interpolation_citation_id", "") or "").strip():
        raise LabScheduleValidationError("lab_schedule_interpolation_missing_citation")
    if not str(schedule.get("interpolation_extraction_note", "") or "").strip():
        raise LabScheduleValidationError("lab_schedule_interpolation_missing_assumption_note")


def _normalize_points(raw: Any, *, duration_h: float, field: str) -> tuple[Mapping[str, Any], ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise LabScheduleValidationError(f"lab_schedule_{field}_must_be_points")
    if len(raw) < 2:
        raise LabScheduleValidationError(f"lab_schedule_{field}_requires_two_points")
    points: list[dict[str, float]] = []
    previous_t: float | None = None
    expected_unit = _expected_point_unit(field)
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise LabScheduleValidationError(f"lab_schedule_{field}_point_must_be_mapping")
        if "time_h" in item:
            raise LabScheduleValidationError(
                f"lab_schedule_{field}_time_h_alias_unsupported"
            )
        t_h = _required_finite_non_negative(item.get("t_h"), f"{field}[{index}].t_h")
        value = _required_finite_non_negative(item.get("value"), f"{field}[{index}].value")
        unit = _normalize_point_unit(item, field=field, expected_unit=expected_unit)
        if previous_t is not None and t_h <= previous_t:
            raise LabScheduleValidationError(
                f"lab_schedule_{field}_time_arrays_must_be_monotonic"
            )
        previous_t = t_h
        point = {"t_h": t_h, "value": value}
        if unit is not None:
            point["unit"] = unit
        points.append(point)
    if abs(points[0]["t_h"]) > 1.0e-12 or abs(points[-1]["t_h"] - duration_h) > 1.0e-12:
        raise LabScheduleValidationError(
            f"lab_schedule_{field}_time_arrays_must_start_at_0_end_at_duration"
        )
    return tuple(MappingProxyType(point) for point in points)


def _expected_point_unit(field: str) -> str | None:
    if field == "melt_temperature_C" or field.startswith("surface_temperature_C."):
        return "C"
    if field == "chamber_pressure_mbar":
        return "mbar"
    return None


def _normalize_point_unit(
    item: Mapping[str, Any],
    *,
    field: str,
    expected_unit: str | None,
) -> str | None:
    if "unit" not in item:
        if expected_unit is not None:
            raise LabScheduleValidationError(f"lab_schedule_{field}_unit_missing")
        return None
    unit = str(item.get("unit", "") or "").strip()
    if not unit:
        raise LabScheduleValidationError(f"lab_schedule_{field}_unit_missing")
    if expected_unit is None:
        raise LabScheduleValidationError(f"lab_schedule_{field}_unit_unexpected")
    if unit != expected_unit:
        raise LabScheduleValidationError(
            f"lab_schedule_{field}_unit_mismatch: "
            f"expected {expected_unit!r}, got {unit!r}"
        )
    return unit


def _normalize_surface_temperatures(
    raw: Any,
    *,
    duration_h: float,
    required_surface_profiles: tuple[str, ...],
) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise LabScheduleValidationError("lab_schedule_surface_temperature_C_must_be_mapping")
    normalized: dict[str, tuple[Mapping[str, float], ...]] = {}
    for surface_id, points in raw.items():
        normalized[str(surface_id)] = _normalize_points(
            points,
            duration_h=duration_h,
            field=f"surface_temperature_C.{surface_id}",
        )
    missing = sorted(set(required_surface_profiles) - set(normalized))
    if missing:
        # TODO(VPR-P2-lab-geometry): pass deposit-receiving surface profile IDs
        # from lab geometry once that chunk owns the geometry/runtime seam.
        raise LabScheduleValidationError(
            "lab_schedule_missing_surface_temperature: " + ", ".join(missing)
        )
    return MappingProxyType(normalized)


def _normalize_window_semantics_from_schedule(
    schedule: Mapping[str, Any],
    *,
    duration_h: float,
) -> Mapping[str, Any]:
    raw_experiment = schedule.get("experiment_windows")
    bridged = None
    if raw_experiment is not None:
        bridged = _window_semantics_from_experiment_windows(
            raw_experiment,
            duration_h=duration_h,
        )

    if "window_semantics" in schedule and schedule.get("window_semantics") is not None:
        explicit = _normalize_window_semantics(
            schedule.get("window_semantics"),
            duration_h=duration_h,
        )
        if bridged is not None and not _same_window_semantics(explicit, bridged):
            raise LabScheduleValidationError(
                "lab_schedule_experiment_windows_conflict_with_window_semantics"
            )
        return explicit

    if bridged is not None:
        return bridged
    return _normalize_window_semantics({}, duration_h=duration_h)


def _window_semantics_from_experiment_windows(
    raw: Any,
    *,
    duration_h: float,
) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise LabScheduleValidationError("lab_schedule_experiment_windows_must_be_mapping")
    measured = raw.get("measured")
    if not isinstance(measured, Mapping):
        raise LabScheduleValidationError("lab_schedule_experiment_windows_missing_measured")
    measured_start_h = _required_finite_non_negative(
        measured.get("start_h"),
        "experiment_windows.measured.start_h",
    )
    measured_end_h = _required_finite_non_negative(
        measured.get("end_h"),
        "experiment_windows.measured.end_h",
    )

    cooldown = raw.get("cooldown", {})
    if cooldown is None:
        cooldown = {}
    if not isinstance(cooldown, Mapping):
        raise LabScheduleValidationError("lab_schedule_experiment_windows_cooldown_must_be_mapping")
    cooldown_h = _experiment_cooldown_h(cooldown, measured_end_h, duration_h)
    basis = _experiment_deposit_sample_basis(cooldown.get("deposit_sampling"))
    return _normalize_window_semantics(
        {
            "preheat_h": measured_start_h,
            "measured_window_start_h": measured_start_h,
            "measured_window_end_h": measured_end_h,
            "cooldown_h": cooldown_h,
            "deposit_sample_basis": basis,
        },
        duration_h=duration_h,
    )


def _experiment_cooldown_h(
    cooldown: Mapping[str, Any],
    measured_end_h: float,
    duration_h: float,
) -> float:
    if "duration_h" in cooldown:
        return _finite_non_negative(
            cooldown.get("duration_h"),
            "experiment_windows.cooldown.duration_h",
        )
    if "start_h" in cooldown or "end_h" in cooldown:
        start_h = _required_finite_non_negative(
            cooldown.get("start_h"),
            "experiment_windows.cooldown.start_h",
        )
        end_h = _required_finite_non_negative(
            cooldown.get("end_h"),
            "experiment_windows.cooldown.end_h",
        )
        if end_h < start_h:
            raise LabScheduleValidationError(
                "lab_schedule_experiment_windows_cooldown_window_negative"
            )
        return end_h - start_h
    return max(0.0, duration_h - measured_end_h)


def _experiment_deposit_sample_basis(raw: Any) -> str:
    value = str(raw or "not_reported").strip()
    aliases = {
        "hot": "hot",
        "in_situ_hot": "hot",
        "cooldown_or_post_run": "after_cooldown",
        "post_run_cooldown": "after_cooldown",
        "after_cooldown": "after_cooldown",
        "after_re_evaporation": "after_re_evaporation",
        "after_boil_back": "after_boil_back",
        "not_reported": "not_reported",
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise LabScheduleValidationError(
            "lab_schedule_experiment_windows_deposit_sampling_unsupported"
        ) from exc


def _same_window_semantics(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    for key in (
        "preheat_h",
        "measured_window_start_h",
        "measured_window_end_h",
        "cooldown_h",
    ):
        if abs(float(left[key]) - float(right[key])) > 1.0e-12:
            return False
    return str(left["deposit_sample_basis"]) == str(right["deposit_sample_basis"])


def _normalize_window_semantics(
    raw: Any,
    *,
    duration_h: float,
) -> Mapping[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise LabScheduleValidationError("lab_schedule_window_semantics_must_be_mapping")

    preheat_h = _finite_non_negative(raw.get("preheat_h", 0.0), "window_semantics.preheat_h")
    cooldown_h = _finite_non_negative(
        raw.get("cooldown_h", 0.0),
        "window_semantics.cooldown_h",
    )
    measured_start_h = _finite_non_negative(
        raw.get("measured_window_start_h", preheat_h),
        "window_semantics.measured_window_start_h",
    )
    measured_end_h = _finite_non_negative(
        raw.get("measured_window_end_h", duration_h - cooldown_h),
        "window_semantics.measured_window_end_h",
    )

    if measured_start_h < preheat_h:
        raise LabScheduleValidationError(
            "lab_schedule_window_semantics_measured_starts_before_preheat"
        )
    if measured_end_h < measured_start_h:
        raise LabScheduleValidationError(
            "lab_schedule_window_semantics_measured_window_negative"
        )
    if measured_end_h > duration_h:
        raise LabScheduleValidationError(
            "lab_schedule_window_semantics_measured_window_exceeds_duration"
        )
    if measured_end_h + cooldown_h > duration_h + 1.0e-12:
        raise LabScheduleValidationError(
            "lab_schedule_window_semantics_cooldown_exceeds_duration"
        )

    deposit_sample_basis = str(
        raw.get("deposit_sample_basis", "hot") or ""
    ).strip()
    if deposit_sample_basis not in LAB_SCHEDULE_DEPOSIT_SAMPLE_BASIS:
        allowed = ", ".join(sorted(LAB_SCHEDULE_DEPOSIT_SAMPLE_BASIS))
        raise LabScheduleValidationError(
            "lab_schedule_window_semantics_deposit_sample_basis_unsupported: "
            f"expected one of {allowed}, got {deposit_sample_basis!r}"
        )

    return MappingProxyType(
        {
            "preheat_h": preheat_h,
            "measured_window_start_h": measured_start_h,
            "measured_window_end_h": measured_end_h,
            "cooldown_h": cooldown_h,
            "deposit_sample_basis": deposit_sample_basis,
        }
    )


def _normalize_gas_boundary(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise LabScheduleValidationError("lab_schedule_missing_gas_boundary")
    normalized: dict[str, Any] = {}
    normalized["background_gas"] = _normalize_gas_boundary_field(
        raw.get("background_gas"),
        field="background_gas",
        required=("species",),
    )
    normalized["imposed_flow"] = _normalize_gas_boundary_field(
        raw.get("imposed_flow"),
        field="imposed_flow",
        required=("value", "unit"),
    )
    normalized["pressure_control"] = _normalize_gas_boundary_field(
        raw.get("pressure_control"),
        field="pressure_control",
        required=("mode",),
    )
    return MappingProxyType(normalized)


def _normalize_gas_boundary_field(
    raw: Any,
    *,
    field: str,
    required: tuple[str, ...],
) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise LabScheduleValidationError(f"missing_gas_boundary_{field}")
    reported_status = str(raw.get("reported_status", "") or "").strip()
    if reported_status == "not_reported":
        for key in ("source_class", "citation_id", "digest"):
            if not str(raw.get(key, "") or "").strip():
                raise LabScheduleValidationError(
                    f"gas_boundary_not_reported_missing_{key}"
                )
        if not (
            str(raw.get("extraction_note", "") or "").strip()
            or str(raw.get("reason", "") or "").strip()
        ):
            raise LabScheduleValidationError("gas_boundary_not_reported_missing_reason")
        return _freeze(dict(raw))
    for key in required:
        if raw.get(key) in (None, ""):
            raise LabScheduleValidationError(f"missing_gas_boundary_{field}_{key}")
    if not str(raw.get("source_class", "") or "").strip():
        raise LabScheduleValidationError("gas_boundary_missing_source_class")
    if not (
        str(raw.get("source_ref", "") or "").strip()
        or str(raw.get("citation_id", "") or "").strip()
        or str(raw.get("digest", "") or "").strip()
    ):
        raise LabScheduleValidationError("gas_boundary_missing_source_detail")
    if "unit" in raw and not str(raw.get("unit", "") or "").strip():
        raise LabScheduleValidationError("gas_boundary_missing_unit")
    return _freeze(dict(raw))


def _required_finite_positive(value: Any, label: str) -> float:
    numeric = _required_finite_non_negative(value, label)
    if numeric <= 0.0:
        raise LabScheduleValidationError(f"lab_schedule_{label}_must_be_positive")
    return numeric


def _required_finite_non_negative(value: Any, label: str) -> float:
    if value is None:
        raise LabScheduleValidationError(f"lab_schedule_{label}_missing")
    return _finite_non_negative(value, label)


def _finite_non_negative(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise LabScheduleValidationError(f"lab_schedule_{label}_must_be_numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise LabScheduleValidationError(
            f"lab_schedule_{label}_must_be_numeric"
        ) from exc
    if not math.isfinite(numeric):
        raise LabScheduleValidationError(f"lab_schedule_{label}_must_be_finite")
    if numeric < 0.0:
        raise LabScheduleValidationError(f"lab_schedule_{label}_must_be_non_negative")
    return numeric


def _sha256_canonical(value: Any) -> str:
    payload = canonical_json_dumps(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value
