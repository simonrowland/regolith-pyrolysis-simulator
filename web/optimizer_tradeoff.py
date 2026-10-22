"""Stored-point view for the optimizer Pareto and trajectory pages.

Shapes rows the result store already wrote. Does not score, rank, or
rewrite a run. Pareto-front membership is the cache key recorded in
``pareto.json`` (``trajectory_cache_key`` when present).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from simulator.optimize.objective import canonical_objective_metric
from simulator.optimize.trajectory import (
    TRAJECTORY_STATUS_AVAILABLE,
    TRAJECTORY_STATUS_UNAVAILABLE,
)

FOM_METRIC = "separated_product_kg_per_furnace_h"
FURNACE_HOUR_METRICS = ("furnace_time_h", "duration_h")
PLOT_FEASIBLE = "feasible"
PLOT_INFEASIBLE = "infeasible"
PLOT_UNAVAILABLE = "unavailable"
# The stored failure_category token, not a second taxonomy.
BACKEND_UNAVAILABLE = "backend_unavailable"
TRAJECTORY_UNAVAILABLE_MESSAGE = "trajectory unavailable for this point"
TRAJECTORY_UNREADABLE_MESSAGE = "trajectory unreadable for this point"
COMPARE_MIN = 2
COMPARE_MAX = 4
PARETO_DOCUMENT_ABSENT = "absent"
PARETO_DOCUMENT_PRESENT = "present"
PARETO_DOCUMENT_UNREADABLE = "unreadable"

_SCALAR_SERIES = (
    ("T_C", "Temperature (T_C)", "°C", "conditions"),
    ("p_total_mbar", "Total pressure (p_total)", "mbar", "conditions"),
    ("pO2_mbar", "Oxygen partial pressure (pO2)", "mbar", "conditions"),
)
_SERIES_GROUP_LABELS = (
    ("conditions", "Conditions"),
    ("vapour", "Vapour"),
    ("capture", "Capture"),
    ("wall", "Wall deposit"),
)
_LIQUID_FE_TAPPED = "liquid_fe_tapped"


def pareto_view(
    *,
    run_id: str,
    rows: Sequence[Mapping[str, Any]],
    pareto_document: Mapping[str, Any] | None,
    pareto_document_status: str,
    x: str | None,
    y: str | None,
) -> dict[str, Any]:
    """One run's evaluated points on the requested objective axes."""

    recorded = _recorded_metrics(rows)
    definitions = _definition_rows(pareto_document)
    primary = _primary_metric(definitions, recorded)
    default_y = _default_y(recorded, primary)
    default_x = _default_x(recorded, default_y)
    selected_x, x_error = _resolve_axis(x, recorded, default_x)
    selected_y, y_error = _resolve_axis(y, recorded, default_y)
    errors = [item for item in (x_error, y_error) if item]
    front = _front_keys(pareto_document)
    points = [
        _point_view(
            row,
            x_metric=selected_x,
            y_metric=selected_y,
            on_front=str(row.get("cache_key") or "") in front,
        )
        for row in rows
    ]
    points.sort(key=lambda item: (item["candidate_id"], item["cache_key"]))
    metrics = [
        _metric_payload(item)
        for item in sorted(
            recorded.values(),
            key=lambda item: (item["ordinal"], item["metric"]),
        )
    ]
    return {
        "run_id": run_id,
        "pareto_document": pareto_document_status,
        "front_note": _front_note(pareto_document_status),
        "metrics": metrics,
        "primary_objective": primary,
        "default_x": default_x,
        "default_y": default_y,
        "x": selected_x,
        "y": selected_y,
        "x_label": _axis_label(selected_x, recorded),
        "y_label": _axis_label(selected_y, recorded),
        "axis_error": "; ".join(errors) or None,
        "points": points,
        "plotted_count": sum(1 for point in points if point["plotted"]),
    }


def compare_model(
    *,
    run_id: str,
    selections: Sequence[Mapping[str, Any]],
    x_metric: str | None,
    y_metric: str | None,
    series_id: str | None,
) -> dict[str, Any]:
    """Overlay one series for the already chosen points."""

    available_hours = [
        item["hours"]
        for item in selections
        if item.get("hours") is not None
    ]
    catalog = series_catalog(available_hours) if available_hours else []
    selected, series_error = _resolve_series(series_id, catalog)
    points = []
    for item in selections:
        message = _trajectory_message(
            str(item.get("trajectory_status") or TRAJECTORY_STATUS_UNAVAILABLE)
        )
        hours = item.get("hours")
        series_points = None
        if message is None and selected is not None and isinstance(hours, Sequence):
            series_points = series_points_for(hours, selected)
        objectives = _objective_map(item.get("objectives"))
        points.append({
            "cache_key": str(item.get("cache_key") or ""),
            "candidate_id": str(item.get("candidate_id") or ""),
            "label": _point_label(
                str(item.get("candidate_id") or ""),
                str(item.get("cache_key") or ""),
                objectives,
                (x_metric, y_metric),
            ),
            "message": message,
            "series_points": series_points,
            "key_objectives": _key_objectives(objectives, (x_metric, y_metric)),
        })
    selected_meta = next(
        (item for item in catalog if item["id"] == selected),
        None,
    )
    chart = any(point["series_points"] is not None for point in points)
    return {
        "run_id": run_id,
        "series_id": selected,
        "series_label": None if selected_meta is None else selected_meta["label"],
        "series_error": series_error,
        "series": catalog,
        "series_groups": _series_groups(catalog),
        "chart": chart and series_error is None,
        "points": points,
    }


def trajectory_chart_model(
    trajectory: Mapping[str, Any] | None,
    *,
    series_id: str | None,
    unreadable: bool = False,
) -> dict[str, Any]:
    """Detail-page trajectory, or a typed unavailable/unreadable message."""

    if unreadable:
        return _empty_trajectory(TRAJECTORY_UNREADABLE_MESSAGE, "unreadable")
    status = (
        str(trajectory.get("status"))
        if isinstance(trajectory, Mapping) and trajectory.get("status") is not None
        else TRAJECTORY_STATUS_UNAVAILABLE
    )
    if status != TRAJECTORY_STATUS_AVAILABLE or not isinstance(trajectory, Mapping):
        return _empty_trajectory(
            TRAJECTORY_UNAVAILABLE_MESSAGE,
            TRAJECTORY_STATUS_UNAVAILABLE,
        )
    hours = trajectory.get("hours")
    if not isinstance(hours, list):
        hours = []
    catalog = series_catalog([hours])
    selected, series_error = _resolve_series(series_id, catalog)
    selected_meta = next(
        (item for item in catalog if item["id"] == selected),
        None,
    )
    show = series_error is None and selected is not None
    return {
        "status": TRAJECTORY_STATUS_AVAILABLE,
        "message": None,
        "schema_version": trajectory.get("schema_version"),
        "series_error": series_error,
        "series_id": selected,
        "series_label": None if selected_meta is None else selected_meta["label"],
        "series": catalog,
        "series_groups": _series_groups(catalog),
        "series_points": series_points_for(hours, selected) if show else None,
        "chart": {
            "series_label": None if selected_meta is None else selected_meta["label"],
            "y_title": None if selected_meta is None else selected_meta["label"],
            "series_points": series_points_for(hours, selected) if show else [],
        } if show else None,
    }


def series_catalog(hour_groups: Sequence[Sequence[Any]]) -> list[dict[str, str]]:
    """Scalar conditions plus whatever species and stages the hours recorded."""

    catalog = [
        {
            "id": field,
            "label": label,
            "units": units,
            "group": group,
        }
        for field, label, units, group in _SCALAR_SERIES
    ]
    vapour: set[str] = set()
    capture: set[str] = set()
    wall: set[str] = set()
    for hours in hour_groups:
        for hour in hours:
            if not isinstance(hour, Mapping):
                continue
            vapour.update(_map_keys(hour.get("vapour_kg_h")))
            capture.update(_map_keys(hour.get("capture_kg_h")))
            wall.update(_map_keys(hour.get("wall_deposit_kg_h")))
    for species in sorted(vapour):
        catalog.append({
            "id": f"vapour_kg_h:{species}",
            "label": f"Vapour kg/h · {species}",
            "units": "kg/h",
            "group": "vapour",
        })
    for stage in sorted(capture):
        if stage == _LIQUID_FE_TAPPED:
            label = "Capture kg/h · liquid Fe tapped"
        else:
            label = f"Capture kg/h · stage {stage}"
        catalog.append({
            "id": f"capture_kg_h:{stage}",
            "label": label,
            "units": "kg/h",
            "group": "capture",
        })
    for species in sorted(wall):
        catalog.append({
            "id": f"wall_deposit_kg_h:{species}",
            "label": f"Wall deposit kg/h · {species}",
            "units": "kg/h",
            "group": "wall",
        })
    return catalog


def series_points_for(hours: Sequence[Any], series_id: str) -> list[dict[str, Any]]:
    field, sub = _split_series_id(series_id)
    rows = []
    for hour in hours:
        if not isinstance(hour, Mapping):
            continue
        if sub is None:
            value = hour.get(field)
        else:
            raw = hour.get(field)
            value = raw.get(sub) if isinstance(raw, Mapping) else None
        rows.append({
            "hour": hour.get("hour"),
            "value": value if _is_finite_number(value) else None,
        })
    return rows


def _empty_trajectory(message: str, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "schema_version": None,
        "series_error": None,
        "series_id": None,
        "series_label": None,
        "series": [],
        "series_groups": [],
        "series_points": None,
        "chart": None,
    }


def _point_view(
    row: Mapping[str, Any],
    *,
    x_metric: str | None,
    y_metric: str | None,
    on_front: bool,
) -> dict[str, Any]:
    objectives = _objective_map(row.get("objectives"))
    x_value = None if x_metric is None else objectives.get(x_metric)
    y_value = None if y_metric is None else objectives.get(y_metric)
    failure = row.get("failure_category")
    failure_text = str(failure) if failure not in (None, "") else ""
    feasible = bool(row.get("feasible"))
    if x_value is None or y_value is None:
        plot_status = PLOT_UNAVAILABLE
        plotted = False
    elif failure_text == BACKEND_UNAVAILABLE:
        plot_status = PLOT_UNAVAILABLE
        plotted = True
    elif feasible:
        plot_status = PLOT_FEASIBLE
        plotted = True
    else:
        plot_status = PLOT_INFEASIBLE
        plotted = True
    cache_key = str(row.get("cache_key") or "")
    candidate_id = str(row.get("candidate_id") or "")
    trajectory_status = str(
        row.get("trajectory_status") or TRAJECTORY_STATUS_UNAVAILABLE
    )
    return {
        "cache_key": cache_key,
        "candidate_id": candidate_id,
        "feasible": feasible,
        "failure_category": failure_text or None,
        "plot_status": plot_status,
        "plotted": plotted,
        "on_pareto_front": on_front,
        "trajectory_status": trajectory_status,
        "trajectory_message": _trajectory_message(trajectory_status),
        "x": x_value,
        "y": y_value,
        "label": _point_label(
            candidate_id,
            cache_key,
            objectives,
            (x_metric, y_metric),
        ),
        "objectives": objectives,
    }


def _trajectory_message(status: str) -> str | None:
    if status == TRAJECTORY_STATUS_AVAILABLE:
        return None
    if status == "unreadable":
        return TRAJECTORY_UNREADABLE_MESSAGE
    return TRAJECTORY_UNAVAILABLE_MESSAGE


def _recorded_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    recorded: dict[str, dict[str, Any]] = {}
    for row in rows:
        for item in _objective_records(row.get("objectives")):
            metric = item["metric"]
            current = recorded.get(metric)
            if current is None or item["ordinal"] < current["ordinal"]:
                recorded[metric] = {
                    "metric": metric,
                    "sense": item["sense"],
                    "units": item["units"],
                    "ordinal": item["ordinal"],
                }
    return recorded


def _objective_records(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    records = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        metric = item.get("metric")
        if not isinstance(metric, str) or not metric:
            continue
        value = item.get("value")
        if item.get("value_status") == PLOT_UNAVAILABLE:
            value = None
        elif not _is_finite_number(value):
            value = None
        else:
            value = float(value)
        records.append({
            "metric": canonical_objective_metric(metric),
            "sense": str(item.get("sense") or "maximize"),
            "units": str(item.get("units") or ""),
            "ordinal": _ordinal(item.get("ordinal"), index),
            "value": value,
        })
    return records


def _objective_map(raw: Any) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for item in _objective_records(raw):
        values[item["metric"]] = item["value"]
    return values


def _definition_rows(document: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(document, Mapping):
        return []
    raw = document.get("objectives")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _primary_metric(
    definitions: Sequence[Mapping[str, Any]],
    recorded: Mapping[str, Mapping[str, Any]],
) -> str | None:
    ordered = sorted(
        definitions,
        key=lambda item: (
            _ordinal(item.get("ordinal"), 0),
            str(item.get("metric") or ""),
        ),
    )
    for item in ordered:
        metric = item.get("metric")
        if not isinstance(metric, str) or not metric:
            continue
        canonical = canonical_objective_metric(metric)
        if canonical in recorded:
            return canonical
    if not recorded:
        return None
    return min(recorded, key=lambda metric: (recorded[metric]["ordinal"], metric))


def _default_y(
    recorded: Mapping[str, Mapping[str, Any]],
    primary: str | None,
) -> str | None:
    if FOM_METRIC in recorded:
        return FOM_METRIC
    return primary


def _default_x(
    recorded: Mapping[str, Mapping[str, Any]],
    y_metric: str | None,
) -> str | None:
    for metric in FURNACE_HOUR_METRICS:
        if metric in recorded and metric != y_metric:
            return metric
    for metric in sorted(recorded, key=lambda item: (recorded[item]["ordinal"], item)):
        if metric != y_metric:
            return metric
    return y_metric


def _resolve_axis(
    requested: str | None,
    recorded: Mapping[str, Mapping[str, Any]],
    default: str | None,
) -> tuple[str | None, str | None]:
    if requested is None or not str(requested).strip():
        return default, None
    canonical = canonical_objective_metric(requested.strip())
    if canonical in recorded:
        return canonical, None
    return default, f"unknown objective metric: {requested.strip()}"


def _resolve_series(
    requested: str | None,
    catalog: Sequence[Mapping[str, str]],
) -> tuple[str | None, str | None]:
    ids = [item["id"] for item in catalog]
    if not ids:
        return None, None
    if requested is None or not str(requested).strip():
        if "T_C" in ids:
            return "T_C", None
        return ids[0], None
    text = requested.strip()
    if text in ids:
        return text, None
    return None, f"unknown trajectory series: {text}"


def _front_keys(document: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(document, Mapping):
        return set()
    raw = document.get("pareto")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return set()
    keys: set[str] = set()
    for member in raw:
        if not isinstance(member, Mapping):
            continue
        linked = member.get("trajectory_cache_key")
        if not isinstance(linked, str) or not linked:
            linked = member.get("cache_key")
        if isinstance(linked, str) and linked:
            keys.add(linked)
    return keys


def _front_note(status: str) -> str | None:
    if status == PARETO_DOCUMENT_ABSENT:
        return "No pareto.json for this run; no front is marked."
    if status == PARETO_DOCUMENT_UNREADABLE:
        return "pareto.json could not be read; no front is marked."
    return None


def _metric_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "metric": item["metric"],
        "sense": item["sense"],
        "units": item["units"],
        "ordinal": item["ordinal"],
        "label": _axis_label(item["metric"], {item["metric"]: item}),
    }


def _axis_label(metric: str | None, recorded: Mapping[str, Mapping[str, Any]]) -> str | None:
    if metric is None:
        return None
    item = recorded.get(metric)
    if item is None:
        return metric
    units = str(item.get("units") or "")
    sense = str(item.get("sense") or "maximize")
    if units:
        return f"{metric} ({sense}, {units})"
    return f"{metric} ({sense})"


def _point_label(
    candidate_id: str,
    cache_key: str,
    objectives: Mapping[str, float | None],
    metrics: Sequence[str | None],
) -> str:
    parts = [candidate_id or cache_key]
    seen: set[str] = set()
    for metric in metrics:
        if metric is None or metric in seen:
            continue
        seen.add(metric)
        value = objectives.get(metric)
        rendered = "unavailable" if value is None else format(value, ".6g")
        parts.append(f"{metric}={rendered}")
    return " · ".join(parts)


def _key_objectives(
    objectives: Mapping[str, float | None],
    metrics: Sequence[str | None],
) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    for metric in metrics:
        if metric is None or metric in seen:
            continue
        seen.add(metric)
        rows.append({"metric": metric, "value": objectives.get(metric)})
    return rows


def _series_groups(catalog: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    groups = []
    for key, label in _SERIES_GROUP_LABELS:
        series = [dict(item) for item in catalog if item["group"] == key]
        if series:
            groups.append({"id": key, "label": label, "series": series})
    return groups


def _map_keys(raw: Any) -> set[str]:
    if not isinstance(raw, Mapping):
        return set()
    return {str(key) for key in raw}


def _split_series_id(series_id: str) -> tuple[str, str | None]:
    if ":" not in series_id:
        return series_id, None
    field, sub = series_id.split(":", 1)
    return field, sub


def _ordinal(value: Any, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return value


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))
