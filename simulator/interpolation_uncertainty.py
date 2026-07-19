"""Diagnostic interpolation uncertainty vectors for reduced-real cache points."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from types import MappingProxyType
from typing import Any


SCHEMA_VERSION = "interpolation_uncertainty_vector.v1"
RANKED_TABLE_SCHEMA_VERSION = "interpolation_uncertainty_ranked_tables.v1"
FEASIBILITY_VERDICT_SCHEMA_VERSION = "interpolation_feasibility_verdict.v1"
FLOAT_DTYPE = "float64"

CACHE_DISTANCE = "cache_distance"
NONLINEARITY_GENERAL = "nonlinearity_general"
THRESHOLD_STRADDLE = "threshold_straddle"

FEASIBLE = "feasible"
INFEASIBLE = "infeasible"
INDETERMINATE = "indeterminate"

DEFAULT_RANK_LIMIT = 28
UNCALIBRATED_MARGIN_ERROR_INFLATION = 2.0

# Existing reduced-real interpolation boundary buffer:
# simulator.reduced_real_cache_interpolation.INTERPOLATION_PHASE_BOUNDARY_EPS = 0.02.
# t-125 v1 reuses that shipped local coefficient as the liquid-fraction
# boundary buffer until t-126 provides registry-backed surfaces.
LIQUID_FRACTION_BOUNDARY_BUFFER = 0.02

# data/vapor_pressures.yaml records Na boiling_point_C=883 from the
# NIST Chemistry WebBook SRD 69 Rodebush and Walters 1930 sodium row (C7440235).
# data/vapor_pressures.yaml records K boiling_point_C=759 from the
# NIST Chemistry WebBook SRD 69 Fiock and Rodebush 1926 potassium row (C7440097).
ALKALI_BOILING_THRESHOLDS_K = MappingProxyType(
    {
        "Na": {
            "threshold_K": 883.0 + 273.15,
            "source": "data/vapor_pressures.yaml NIST WebBook SRD 69 sodium row",
        },
        "K": {
            "threshold_K": 759.0 + 273.15,
            "source": "data/vapor_pressures.yaml NIST WebBook SRD 69 potassium row",
        },
    }
)


def build_interpolation_uncertainty_vector(
    query_key: Mapping[str, Any],
    neighbors: Sequence[Mapping[str, Any]],
    *,
    weight_info: Mapping[str, Any] | None = None,
    error_estimate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return source-side orthogonal uncertainty components for one eval point.

    The returned vector is diagnostic-only. It is intentionally independent of
    EvalSpec/cache-key/replay-key payloads and does not gate interpolation.
    """

    cache_distance = _cache_distance_component(query_key, neighbors)
    nonlinearity = _nonlinearity_component(query_key, neighbors)
    threshold = _threshold_straddle_component(query_key, neighbors)
    components = {
        CACHE_DISTANCE: cache_distance,
        NONLINEARITY_GENERAL: nonlinearity,
        THRESHOLD_STRADDLE: threshold,
    }
    vector = {
        name: component["value"]
        for name, component in components.items()
        if isinstance(component.get("value"), float)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "dtype": FLOAT_DTYPE,
        "vector": vector,
        "components": components,
        "component_order": [
            CACHE_DISTANCE,
            NONLINEARITY_GENERAL,
            THRESHOLD_STRADDLE,
        ],
        "weight_mode": str((weight_info or {}).get("mode", "")),
        "error_estimate": _compact_error_estimate(error_estimate or {}),
        "calibration_status": "uncalibrated_wide_band",
        "notes": [
            "diagnostic_only_no_cache_key_or_gate_effect",
            "consumer_side_fold_required",
        ],
    }


def interpolation_uncertainty_points_from_replay_sequence(
    replay_sequence: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for index, event in enumerate(replay_sequence):
        uncertainty = event.get("interpolation_uncertainty")
        if not isinstance(uncertainty, Mapping):
            continue
        points.append(
            {
                "sequence_index": index,
                "point_id": _point_id(event, index),
                "artifact": event.get("artifact"),
                "cache_state": event.get("cache_state"),
                "uncertainty": _plain_mapping(uncertainty),
            }
        )
    return points


def ranked_table_drain(
    points: Sequence[Mapping[str, Any]],
    *,
    limit: int = DEFAULT_RANK_LIMIT,
) -> dict[str, Any]:
    """Minimal progressive-jpeg read API: drain table 1, then table 2."""

    limit = max(0, int(limit))
    normalized = [_rankable_point(point) for point in points]
    threshold_points = [
        point for point in normalized if _threshold_straddles(point["uncertainty"])
    ]
    threshold_points.sort(key=_stable_point_key)
    threshold_ids = {str(point["point_id"]) for point in threshold_points}
    numeric_points = [
        point
        for point in normalized
        if str(point["point_id"]) not in threshold_ids
    ]
    numeric_points.sort(key=_numeric_rank_key)

    selected: list[dict[str, Any]] = []
    for point in threshold_points:
        if len(selected) >= limit:
            break
        selected.append({**point, "ranked_table": THRESHOLD_STRADDLE})
    if len(selected) < limit:
        for point in numeric_points:
            if len(selected) >= limit:
                break
            selected.append({**point, "ranked_table": NONLINEARITY_GENERAL})

    return {
        "schema_version": RANKED_TABLE_SCHEMA_VERSION,
        "limit": limit,
        "tables": [
            {
                "name": THRESHOLD_STRADDLE,
                "count": len(threshold_points),
                "point_ids": [point["point_id"] for point in threshold_points],
            },
            {
                "name": NONLINEARITY_GENERAL,
                "count": len(numeric_points),
                "point_ids": [point["point_id"] for point in numeric_points],
            },
        ],
        "selected": selected,
    }


def feasibility_verdict_with_interpolation_uncertainty(
    margins: Mapping[str, Any],
    uncertainty: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(uncertainty, Mapping):
        return None
    clean_margins = {
        str(gate): margin
        for gate, margin in margins.items()
        if margin is not None and hasattr(margin, "margin")
    }
    if not clean_margins:
        return None
    if _threshold_straddles(uncertainty):
        return {
            "schema_version": FEASIBILITY_VERDICT_SCHEMA_VERSION,
            "verdict": INDETERMINATE,
            "reason": "threshold_straddle_non_interpolable",
            "margin_error_source": THRESHOLD_STRADDLE,
        }
    error_bound = _uncalibrated_margin_error_bound(uncertainty)
    if error_bound is None:
        return None
    closest_gate, closest_margin = min(
        (
            (gate, abs(float(margin.margin)))
            for gate, margin in clean_margins.items()
        ),
        key=lambda item: (item[1], item[0]),
    )
    if closest_margin <= error_bound:
        verdict = INDETERMINATE
        reason = "gate_margin_inside_interpolation_error"
    else:
        all_feasible = all(bool(getattr(margin, "feasible", False)) for margin in clean_margins.values())
        verdict = FEASIBLE if all_feasible else INFEASIBLE
        reason = "gate_margin_exceeds_interpolation_error"
    return {
        "schema_version": FEASIBILITY_VERDICT_SCHEMA_VERSION,
        "verdict": verdict,
        "reason": reason,
        "closest_gate": closest_gate,
        "closest_abs_margin": float(closest_margin),
        "uncalibrated_margin_error_bound": float(error_bound),
        "margin_error_source": "consumer_side_uncalibrated_wide_band",
    }


def feasibility_verdict_from_reduced_real_cache(
    margins: Mapping[str, Any],
    reduced_real_cache: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(reduced_real_cache, Mapping):
        return None
    drain = reduced_real_cache.get("interpolation_uncertainty_ranked_table_drain")
    if not isinstance(drain, Mapping):
        return None
    selected = drain.get("selected")
    if not isinstance(selected, Sequence) or isinstance(selected, (str, bytes)):
        return None
    verdicts = []
    for point in selected:
        if not isinstance(point, Mapping):
            continue
        verdict = feasibility_verdict_with_interpolation_uncertainty(
            margins,
            point.get("uncertainty"),
        )
        if verdict is not None:
            verdicts.append({**verdict, "point_id": point.get("point_id")})
    if not verdicts:
        return None
    if any(verdict["verdict"] == INDETERMINATE for verdict in verdicts):
        chosen = next(verdict for verdict in verdicts if verdict["verdict"] == INDETERMINATE)
    elif any(verdict["verdict"] == INFEASIBLE for verdict in verdicts):
        chosen = next(verdict for verdict in verdicts if verdict["verdict"] == INFEASIBLE)
    else:
        chosen = verdicts[0]
    return {
        "schema_version": FEASIBILITY_VERDICT_SCHEMA_VERSION,
        "verdict": chosen["verdict"],
        "reason": chosen["reason"],
        "point_id": chosen.get("point_id"),
        "closest_gate": chosen.get("closest_gate"),
        "closest_abs_margin": chosen.get("closest_abs_margin"),
        "uncalibrated_margin_error_bound": chosen.get(
            "uncalibrated_margin_error_bound"
        ),
        "margin_error_source": chosen.get("margin_error_source"),
        "points_considered": len(verdicts),
        "diagnostic_only": True,
    }


def _cache_distance_component(
    query_key: Mapping[str, Any],
    neighbors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    distances = [
        _finite_float(neighbor.get("interpolation_distance"))
        for neighbor in neighbors
    ]
    distances = [value for value in distances if value is not None]
    if not distances:
        distances = _computed_neighbor_distances(query_key, neighbors)
    if not distances:
        return {
            "kind": "scalar",
            "value": None,
            "status": "insufficient_neighbors",
            "units": "standardized_distance",
            "source": "simulator.reduced_real_cache_interpolation._standardized_distance",
        }
    return {
        "kind": "scalar",
        "value": float(max(distances)),
        "status": "available",
        "units": "standardized_distance",
        "source": "simulator.reduced_real_cache_interpolation._standardized_distance",
        "max_neighbor_distance": float(max(distances)),
        "mean_neighbor_distance": float(sum(distances) / len(distances)),
    }


def _nonlinearity_component(
    query_key: Mapping[str, Any],
    neighbors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    q_t = _temperature_k(query_key)
    rows = []
    for neighbor in neighbors:
        key = neighbor.get("key")
        if not isinstance(key, Mapping):
            continue
        t_k = _temperature_k(key)
        payload = neighbor.get("payload")
        if t_k is None or not isinstance(payload, Mapping):
            continue
        outputs = _curvature_outputs(payload)
        if outputs:
            rows.append((t_k, outputs))
    distinct_t = sorted({t_k for t_k, _outputs in rows})
    if q_t is None or len(distinct_t) < 3:
        return {
            "kind": "scalar",
            "value": None,
            "status": "insufficient_neighborhood",
            "units": "dimensionless_relative_curvature",
            "active_subspace": ["T_K"],
            "source": "weighted_quadratic_fit_v1",
        }

    output_names = sorted(set().union(*(outputs.keys() for _t, outputs in rows)))
    scores: dict[str, float] = {}
    span = max(max(abs(t_k - q_t) for t_k, _outputs in rows), 1.0)
    x_values = [(t_k - q_t) / span for t_k, _outputs in rows]
    weights = [1.0 / max(abs(x), 0.05) for x in x_values]
    for output_name in output_names:
        y_rows = [
            (x, outputs[output_name], weight)
            for x, (_t, outputs), weight in zip(x_values, rows, weights, strict=False)
            if output_name in outputs
        ]
        if len(y_rows) < 3:
            continue
        pressure_log = output_name.startswith("log10_vapor_pressure_Pa:")
        y_center = (
            sum(y for _x, y, _w in y_rows) / len(y_rows)
            if pressure_log
            else 0.0
        )
        # Only log(P) has a unit-dependent additive offset. Center that surface
        # and normalize by local variation; retain relative-level scaling for
        # dimensionless outputs such as liquid fraction.
        y_scale = max(
            max(
                abs(y - y_center) if pressure_log else abs(y)
                for _x, y, _w in y_rows
            ),
            1.0 if pressure_log else 1.0e-30,
        )
        coeffs = _weighted_quadratic_coefficients(
            [x for x, _y, _w in y_rows],
            [(y - y_center) / y_scale for _x, y, _w in y_rows],
            [w for _x, _y, w in y_rows],
        )
        if coeffs is None:
            continue
        _c0, _c1, c2 = coeffs
        max_x = max(abs(x) for x, _y, _w in y_rows)
        scores[output_name] = float(abs(c2) * max_x * max_x)
    if not scores:
        return {
            "kind": "scalar",
            "value": None,
            "status": "insufficient_neighborhood",
            "units": "dimensionless_relative_curvature",
            "active_subspace": ["T_K"],
            "source": "weighted_quadratic_fit_v1",
        }
    return {
        "kind": "scalar",
        "value": float(max(scores.values())),
        "status": "available",
        "units": "dimensionless_relative_curvature",
        "active_subspace": ["T_K"],
        "source": "weighted_quadratic_fit_v1",
        "output_scores": scores,
    }


def _threshold_straddle_component(
    query_key: Mapping[str, Any],
    neighbors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    surfaces: list[dict[str, Any]] = []
    liquid_values = []
    for neighbor in neighbors:
        payload = neighbor.get("payload")
        if not isinstance(payload, Mapping):
            continue
        value = _liquid_fraction(payload)
        if value is not None:
            liquid_values.append(value)
    if liquid_values:
        solidus_threshold = LIQUID_FRACTION_BOUNDARY_BUFFER
        liquidus_threshold = 1.0 - LIQUID_FRACTION_BOUNDARY_BUFFER
        if _straddles(liquid_values, solidus_threshold):
            surfaces.append(
                {
                    "surface": "solidus_liquid_fraction_proxy",
                    "threshold": float(solidus_threshold),
                    "source": "existing reduced-real phase-boundary buffer 0.02",
                }
            )
        if _straddles(liquid_values, liquidus_threshold):
            surfaces.append(
                {
                    "surface": "liquidus_liquid_fraction_proxy",
                    "threshold": float(liquidus_threshold),
                    "source": "existing reduced-real phase-boundary buffer 0.02",
                }
            )

    species = _composition_species(query_key)
    neighbor_temperatures = [
        value
        for value in (
            _temperature_k(neighbor.get("key"))
            for neighbor in neighbors
            if isinstance(neighbor.get("key"), Mapping)
        )
        if value is not None
    ]
    for alkali, row in ALKALI_BOILING_THRESHOLDS_K.items():
        if not _alkali_present(alkali, species):
            continue
        threshold = float(row["threshold_K"])
        if _straddles(neighbor_temperatures, threshold):
            surfaces.append(
                {
                    "surface": f"{alkali}_normal_boiling_point",
                    "threshold_K": threshold,
                    "source": row["source"],
                }
            )
    return {
        "kind": "categorical",
        "value": 1.0 if surfaces else 0.0,
        "status": "non_interpolable" if surfaces else "clear",
        "units": "categorical_flag",
        "surfaces": surfaces,
        "source": "t-125 v1 local discontinuity list",
    }


def _compact_error_estimate(error_estimate: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not error_estimate:
        return result
    for key in (
        "term",
        "held_out_relative_error_max",
    ):
        if key in error_estimate:
            result[key] = _plain_value(error_estimate[key])
    disagreement = error_estimate.get("neighbor_disagreement")
    if isinstance(disagreement, Mapping):
        result["neighbor_disagreement"] = {
            str(key): _plain_value(value)
            for key, value in disagreement.items()
        }
    return result


def _uncalibrated_margin_error_bound(uncertainty: Mapping[str, Any]) -> float | None:
    components = uncertainty.get("components")
    if not isinstance(components, Mapping):
        return None
    values = []
    for name in (CACHE_DISTANCE, NONLINEARITY_GENERAL):
        component = components.get(name)
        if isinstance(component, Mapping):
            value = _finite_float(component.get("value"))
            if value is not None:
                values.append(value)
    estimate = uncertainty.get("error_estimate")
    if isinstance(estimate, Mapping):
        held_out = _finite_float(estimate.get("held_out_relative_error_max"))
        if held_out is not None:
            values.append(held_out)
    if not values:
        return None
    return float(max(values) * UNCALIBRATED_MARGIN_ERROR_INFLATION)


def _rankable_point(point: Mapping[str, Any]) -> dict[str, Any]:
    uncertainty = point.get("uncertainty", point)
    if not isinstance(uncertainty, Mapping):
        uncertainty = {}
    return {
        "point_id": point.get("point_id", ""),
        "sequence_index": point.get("sequence_index"),
        "artifact": point.get("artifact"),
        "cache_state": point.get("cache_state"),
        "uncertainty": _plain_mapping(uncertainty),
        "rank_value": _numeric_rank_value(uncertainty),
    }


def _threshold_straddles(uncertainty: Mapping[str, Any]) -> bool:
    components = uncertainty.get("components")
    if not isinstance(components, Mapping):
        return False
    threshold = components.get(THRESHOLD_STRADDLE)
    if not isinstance(threshold, Mapping):
        return False
    return str(threshold.get("status")) == "non_interpolable"


def _numeric_rank_value(uncertainty: Mapping[str, Any]) -> float:
    components = uncertainty.get("components")
    if not isinstance(components, Mapping):
        return 0.0
    for name in (NONLINEARITY_GENERAL, CACHE_DISTANCE):
        component = components.get(name)
        if isinstance(component, Mapping):
            value = _finite_float(component.get("value"))
            if value is not None:
                return value
    return 0.0


def _numeric_rank_key(point: Mapping[str, Any]) -> tuple[float, str]:
    return (-float(point.get("rank_value", 0.0) or 0.0), str(point.get("point_id", "")))


def _stable_point_key(point: Mapping[str, Any]) -> tuple[int, str]:
    sequence = point.get("sequence_index")
    sequence_value = int(sequence) if isinstance(sequence, int) else 10**12
    return (sequence_value, str(point.get("point_id", "")))


def _point_id(event: Mapping[str, Any], index: int) -> str:
    for key in ("hash", "cache_key", "key_hash"):
        value = event.get(key)
        if value:
            return str(value)
    return f"replay-{index}"


def _computed_neighbor_distances(
    query_key: Mapping[str, Any],
    neighbors: Sequence[Mapping[str, Any]],
) -> list[float]:
    query_vector = _coordinate_vector(query_key)
    if not query_vector:
        return []
    distances: list[float] = []
    for neighbor in neighbors:
        key = neighbor.get("key")
        if not isinstance(key, Mapping):
            continue
        vector = _coordinate_vector(key)
        if len(vector) != len(query_vector):
            continue
        distances.append(_standardized_distance(query_vector, vector))
    return distances


def _coordinate_vector(key: Mapping[str, Any]) -> list[float]:
    species = _composition_species(key)
    composition = dict(_composition_items(key.get("composition_mol_fraction", [])))
    vector = [float(composition.get(name, 0.0)) for name in species]
    controls = key.get("controls", {})
    if not isinstance(controls, Mapping):
        controls = {}
    for name in ("T_K", "pressure_bar", "pO2_bar", "log_fO2"):
        value = _finite_float(controls.get(name))
        if value is not None:
            vector.append(value)
    return vector


def _standardized_distance(left: Sequence[float], right: Sequence[float]) -> float:
    scales = [max(abs(value), 1.0e-9) for value in left]
    return math.sqrt(
        sum(
            ((float(a) - float(b)) / scale) ** 2
            for a, b, scale in zip(left, right, scales, strict=False)
        )
    )


def _composition_species(key: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        species
        for species, _fraction in _composition_items(
            key.get("composition_mol_fraction", [])
        )
    )


def _composition_items(value: Any) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    for item in value or []:
        if isinstance(item, Mapping):
            species = item.get("species")
            fraction = item.get("mol_fraction")
        else:
            species, fraction = item
        items.append((str(species), float(fraction)))
    return sorted(items)


def _temperature_k(key: Any) -> float | None:
    if not isinstance(key, Mapping):
        return None
    controls = key.get("controls", {})
    if not isinstance(controls, Mapping):
        return None
    return _finite_float(controls.get("T_K"))


def _liquid_fraction(payload: Mapping[str, Any]) -> float | None:
    result = payload.get("equilibrium_result", {})
    if not isinstance(result, Mapping):
        return None
    return _finite_float(result.get("liquid_fraction"))


def _curvature_outputs(payload: Mapping[str, Any]) -> dict[str, float]:
    result = payload.get("equilibrium_result", {})
    if not isinstance(result, Mapping):
        return {}
    outputs: dict[str, float] = {}
    liquid = _finite_float(result.get("liquid_fraction"))
    if liquid is not None:
        outputs["liquid_fraction"] = liquid
    vapor = result.get("vapor_pressures_Pa")
    if isinstance(vapor, Mapping):
        for species, pressure in vapor.items():
            value = _finite_float(pressure)
            if value is not None and value > 0.0:
                outputs[f"log10_vapor_pressure_Pa:{species}"] = math.log10(value)
    return outputs


def _weighted_quadratic_coefficients(
    x_values: Sequence[float],
    y_values: Sequence[float],
    weights: Sequence[float],
) -> tuple[float, float, float] | None:
    matrix = [[0.0 for _ in range(3)] for _ in range(3)]
    rhs = [0.0, 0.0, 0.0]
    for x, y, weight in zip(x_values, y_values, weights, strict=False):
        basis = [1.0, float(x), float(x) * float(x)]
        for row in range(3):
            rhs[row] += float(weight) * basis[row] * float(y)
            for col in range(3):
                matrix[row][col] += float(weight) * basis[row] * basis[col]
    return _solve_3x3(matrix, rhs)


def _solve_3x3(
    matrix: Sequence[Sequence[float]],
    rhs: Sequence[float],
) -> tuple[float, float, float] | None:
    augmented = [
        [float(matrix[row][col]) for col in range(3)] + [float(rhs[row])]
        for row in range(3)
    ]
    for pivot in range(3):
        pivot_row = max(range(pivot, 3), key=lambda row: abs(augmented[row][pivot]))
        if abs(augmented[pivot_row][pivot]) <= 1.0e-12:
            return None
        if pivot_row != pivot:
            augmented[pivot], augmented[pivot_row] = augmented[pivot_row], augmented[pivot]
        pivot_value = augmented[pivot][pivot]
        for col in range(pivot, 4):
            augmented[pivot][col] /= pivot_value
        for row in range(3):
            if row == pivot:
                continue
            factor = augmented[row][pivot]
            for col in range(pivot, 4):
                augmented[row][col] -= factor * augmented[pivot][col]
    return (augmented[0][3], augmented[1][3], augmented[2][3])


def _straddles(values: Sequence[float], threshold: float) -> bool:
    if not values:
        return False
    return min(values) <= threshold <= max(values)


def _alkali_present(alkali: str, species: Sequence[str]) -> bool:
    prefixes = (alkali, f"{alkali}2", f"{alkali}Cl", f"{alkali}F")
    return any(name == alkali or name.startswith(prefixes) for name in species)


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _plain_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _plain_value(item) for key, item in value.items()}


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _plain_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else str(value)
    return value
