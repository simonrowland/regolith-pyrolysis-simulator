"""Optimizer knob-bound saturation diagnostics."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from simulator.optimize.recipe import (
    C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR,
    C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH,
    C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_FLOOR_PER_HR,
    C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_PATHS,
    C5_ALLOW_MRE_VOLTAGE_CAP_PATH,
    KeyPath,
    RecipePatch,
    RecipeSchema,
    _default_setpoint_value,
)


SCHEMA_VERSION = "knob-saturation-v1"
_DURATION_COST_METRICS = ("duration_h", "total_hours")
_ENERGY_COST_METRICS = ("energy_kWh",)


def compute_knob_saturation(
    patch: RecipePatch,
    schema: RecipeSchema,
    *,
    active_objective_metrics: Iterable[str],
    tolerance_fraction: float = 0.01,
) -> Mapping[str, Any]:
    """Report numeric knobs pinned at or near their schema bounds."""

    active_metrics = frozenset(str(metric) for metric in active_objective_metrics)
    rows: list[dict[str, Any]] = []
    for path, raw_value in sorted(patch.values.items()):
        spec = schema.spec_for(path)
        if spec.kind == "categorical":
            continue
        for key, value in _value_rows(path, raw_value):
            row = _knob_row(
                key,
                value,
                path=path,
                kind=spec.kind,
                low=spec.low,
                high=spec.high,
                units=spec.units,
                active_metrics=active_metrics,
                tolerance_fraction=tolerance_fraction,
                source="patched",
            )
            rows.append(row)
    if not patch.values:
        rows.extend(
            _default_bound_rows(
                schema,
                active_metrics=active_metrics,
                tolerance_fraction=tolerance_fraction,
            )
        )

    pinned_count = sum(1 for row in rows if row["pinned"] != "none")
    no_cost_pinned_count = sum(
        1
        for row in rows
        if row["pinned"] != "none" and not row["has_opposing_cost"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "tolerance_fraction": float(tolerance_fraction),
        "pinned_count": pinned_count,
        "no_opposing_cost_pinned_count": no_cost_pinned_count,
        "red_flag": no_cost_pinned_count > 0,
        "knobs": rows,
    }


def _default_bound_rows(
    schema: RecipeSchema,
    *,
    active_metrics: frozenset[str],
    tolerance_fraction: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in sorted(schema.search_allowlist, key=lambda item: item.path):
        if spec.kind == "categorical":
            continue
        default_value = _effective_default_value(spec.path)
        if default_value is _MISSING_DEFAULT:
            continue
        for key, value in _value_rows(spec.path, default_value):
            row = _knob_row(
                key,
                value,
                path=spec.path,
                kind=spec.kind,
                low=spec.low,
                high=spec.high,
                units=spec.units,
                active_metrics=active_metrics,
                tolerance_fraction=tolerance_fraction,
                source="default",
            )
            if row["pinned"] != "none":
                rows.append(row)
    return rows


_MISSING_DEFAULT = object()


def _effective_default_value(path: KeyPath) -> Any:
    if path == C5_ALLOW_MRE_VOLTAGE_CAP_PATH:
        return 0.0
    if path in C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_PATHS:
        return 0.0
    try:
        return _default_setpoint_value(path)
    except Exception:
        return _MISSING_DEFAULT


def _value_rows(path: KeyPath, value: Any) -> tuple[tuple[str, Any], ...]:
    key = _format_path(path)
    if _is_numeric_pair(value):
        return ((f"{key}[0]", value[0]), (f"{key}[1]", value[1]))
    return ((key, value),)


def _knob_row(
    key: str,
    value: Any,
    *,
    path: KeyPath,
    kind: str,
    low: float | None,
    high: float | None,
    units: str,
    active_metrics: frozenset[str],
    tolerance_fraction: float,
    source: str,
) -> dict[str, Any]:
    cost_metrics = _opposing_cost_metrics(path, active_metrics)
    applied_value = _applied_trace_value(path, value)
    row: dict[str, Any] = {
        "key": key,
        "value": applied_value,
        "low": low,
        "high": high,
        "pinned": "none",
        "frac_of_range": None,
        "kind": kind,
        "units": units,
        "has_opposing_cost": bool(cost_metrics),
        "opposing_cost_metrics": list(cost_metrics),
        "source": source,
    }
    if not _same_trace_value(value, applied_value):
        row["requested_value"] = value
        row["applied_value"] = applied_value

    if low is None or high is None:
        row["reason"] = "missing_bounds"
        return row
    low_f = float(low)
    high_f = float(high)
    if high_f <= low_f:
        row["reason"] = "degenerate_range"
        return row

    try:
        numeric_value = float(applied_value)
    except (TypeError, ValueError):
        row["reason"] = "nonfinite_value"
        return row
    if not math.isfinite(numeric_value):
        row["reason"] = "nonfinite_value"
        return row

    span = high_f - low_f
    tolerance = float(tolerance_fraction) * span
    frac = (numeric_value - low_f) / span
    row["frac_of_range"] = frac
    if numeric_value <= low_f + tolerance:
        row["pinned"] = "low"
    elif numeric_value >= high_f - tolerance:
        row["pinned"] = "high"
    return row


def _applied_trace_value(path: KeyPath, value: Any) -> Any:
    if path in C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_PATHS:
        try:
            epsilon = float(value or 0.0)
        except (TypeError, ValueError):
            return value
        if not math.isfinite(epsilon) or epsilon < 0.0:
            return value
        if epsilon <= 0.0:
            return 0.0
        return max(epsilon, C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_FLOOR_PER_HR)
    if path != C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH:
        return value
    try:
        fraction = float(value or 0.0)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(fraction) or fraction < 0.0:
        return value
    if fraction < C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR:
        return 0.0
    return fraction


def _same_trace_value(left: Any, right: Any) -> bool:
    try:
        return math.isclose(
            float(left),
            float(right),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    except (TypeError, ValueError):
        return left == right


def _opposing_cost_metrics(
    path: KeyPath,
    active_metrics: frozenset[str],
) -> tuple[str, ...]:
    path_key = _format_path(path)
    if _is_duration_knob(path):
        return tuple(metric for metric in _DURATION_COST_METRICS if metric in active_metrics)
    if path_key == "campaigns.C5.allow_mre_voltage_cap_V" or (
        path[:2] == ("campaigns", "C5") and path[-1] == "max_voltage_V"
    ):
        return tuple(metric for metric in _ENERGY_COST_METRICS if metric in active_metrics)
    return ()


def _is_duration_knob(path: KeyPath) -> bool:
    leaf = path[-1]
    return leaf == "duration_h" or leaf.startswith("duration_") or leaf.endswith("_duration_h")


def _is_numeric_pair(value: Any) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return False
    if len(value) != 2:
        return False
    return all(_is_number(item) for item in value)


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _format_path(path: KeyPath) -> str:
    return ".".join(str(segment) for segment in path)
