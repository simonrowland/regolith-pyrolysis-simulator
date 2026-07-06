"""Greedy-NN + gated linear interpolation for the cached_interpolated tier."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any

from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.reduced_real_determinism import (
    _composition_items,
    _physics_bucket_consumes_log_fO2,
    _physics_bucket_consumes_pO2_bar,
    _replay_scope_hash,
    canonical_physics_bucket_key_from_replay_key,
    physics_control_rung_error_budget,
)


INTERPOLATION_NEIGHBOR_K = 4
INTERPOLATION_MAX_STANDARDIZED_DISTANCE = 0.05
INTERPOLATION_COMPOSITION_MATCH_EPS = 1.0e-6
INTERPOLATION_PHASE_BOUNDARY_EPS = 0.02
INTERPOLATION_SOLID_FRACTION_MARGIN = 0.02
INTERPOLATION_HELD_OUT_DISAGREEMENT_THRESHOLD = 0.10
INTERPOLATION_COORDINATE_EPS = 1.0e-12
INTERPOLATION_ERROR_TERM = "cached_interpolated_neighbor_disagreement"


def interpolation_feature_spec(key: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    species = sorted(
        {
            species
            for species, _fraction in _composition_items(
                key.get("composition_mol_fraction", [])
            )
        }
    )
    control_names: list[str] = ["T_K"]
    controls = key.get("controls", {})
    if isinstance(controls, Mapping):
        if controls.get("pressure_bar") is not None:
            control_names.append("pressure_bar")
        if _physics_bucket_consumes_pO2_bar(key) and controls.get("pO2_bar") is not None:
            control_names.append("pO2_bar")
        if _physics_bucket_consumes_log_fO2(key) and controls.get("log_fO2") is not None:
            control_names.append("log_fO2")
    return species, control_names


def interpolation_coordinate_vector(
    key: Mapping[str, Any],
    *,
    species_order: Sequence[str],
    control_names: Sequence[str],
) -> list[float]:
    composition = {
        species: float(fraction)
        for species, fraction in _composition_items(
            key.get("composition_mol_fraction", [])
        )
    }
    controls = key.get("controls", {})
    if not isinstance(controls, Mapping):
        controls = {}
    vector = [composition.get(species, 0.0) for species in species_order]
    for control_name in control_names:
        value = controls.get(control_name)
        vector.append(float(value) if value is not None else 0.0)
    return vector


def greedy_nearest_neighbors(
    query_key: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    k: int = INTERPOLATION_NEIGHBOR_K,
    max_distance: float = INTERPOLATION_MAX_STANDARDIZED_DISTANCE,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    species_order, control_names = interpolation_feature_spec(query_key)
    query_vector = interpolation_coordinate_vector(
        query_key,
        species_order=species_order,
        control_names=control_names,
    )
    scored: list[tuple[float, float, float, str, Mapping[str, Any]]] = []
    for candidate in candidates:
        candidate_key = candidate.get("key")
        if not isinstance(candidate_key, Mapping):
            continue
        candidate_vector = interpolation_coordinate_vector(
            candidate_key,
            species_order=species_order,
            control_names=control_names,
        )
        composition_distance = _euclidean_distance(
            query_vector[: len(species_order)],
            candidate_vector[: len(species_order)],
        )
        total_distance = _standardized_distance(
            query_vector,
            candidate_vector,
        )
        scored.append(
            (
                composition_distance,
                total_distance,
                float(candidate_key.get("controls", {}).get("T_K", 0.0) or 0.0),
                str(candidate.get("key_hash", "")),
                candidate,
            )
        )
    scored.sort(key=lambda item: (item[0], item[1], abs(item[2] - query_vector[-len(control_names)]), item[3]))
    neighbors: list[dict[str, Any]] = []
    for composition_distance, total_distance, _temperature, _key_hash, candidate in scored:
        if total_distance > max_distance:
            continue
        enriched = copy.deepcopy(dict(candidate))
        enriched["interpolation_distance"] = total_distance
        enriched["interpolation_composition_distance"] = composition_distance
        neighbors.append(enriched)
        if len(neighbors) >= k:
            break
    return neighbors


def interpolation_validity_gate(
    query_key: Mapping[str, Any],
    neighbors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "accepted": True,
        "refusal_reason": None,
        "neighbor_key_hashes": [str(neighbor.get("key_hash", "")) for neighbor in neighbors],
    }
    if len(neighbors) < 2:
        result["accepted"] = False
        result["refusal_reason"] = "insufficient_neighbors"
        return result

    phase_sets = {
        _phase_assemblage_set(neighbor.get("payload"))
        for neighbor in neighbors
    }
    if len(phase_sets) != 1:
        result["accepted"] = False
        result["refusal_reason"] = "phase_assemblage_mismatch"
        return result

    statuses = {
        _equilibrium_status(neighbor.get("payload"))
        for neighbor in neighbors
    }
    if len(statuses) != 1 or next(iter(statuses)) != "ok":
        result["accepted"] = False
        result["refusal_reason"] = "solver_status_mismatch"
        return result

    for neighbor in neighbors:
        refusal = _phase_boundary_refusal(neighbor.get("payload"))
        if refusal is not None:
            result["accepted"] = False
            result["refusal_reason"] = refusal
            return result

    query_liquid_fraction = _query_liquid_fraction_proxy(query_key, neighbors)
    if query_liquid_fraction is not None:
        margin = INTERPOLATION_SOLID_FRACTION_MARGIN
        if query_liquid_fraction < margin:
            result["accepted"] = False
            result["refusal_reason"] = "solid_fraction_margin"
            return result

    if _physics_bucket_consumes_pO2_bar(query_key):
        for neighbor in neighbors:
            source_key = neighbor.get("key")
            if not isinstance(source_key, Mapping):
                continue
            budget = physics_control_rung_error_budget(
                query_key,
                source_key,
                "h30c",
                source_payload=neighbor.get("payload"),
            )
            if not bool(budget["accepted"]):
                result["accepted"] = False
                result["refusal_reason"] = budget.get("refusal_reason") or "pO2_knee_region"
                return result

    disagreement = _neighbor_disagreement(neighbors)
    result["neighbor_disagreement"] = disagreement
    if disagreement["relative_error_max"] > INTERPOLATION_HELD_OUT_DISAGREEMENT_THRESHOLD:
        result["accepted"] = False
        result["refusal_reason"] = "held_out_disagreement_exceeded"
        return result

    return result


def barycentric_interpolation_weights(
    query_key: Mapping[str, Any],
    neighbors: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if len(neighbors) < 2:
        return None
    species_order, control_names = interpolation_feature_spec(query_key)
    query_vector = interpolation_coordinate_vector(
        query_key,
        species_order=species_order,
        control_names=control_names,
    )
    neighbor_vectors = [
        interpolation_coordinate_vector(
            neighbor["key"],
            species_order=species_order,
            control_names=control_names,
        )
        for neighbor in neighbors
        if isinstance(neighbor.get("key"), Mapping)
    ]
    if len(neighbor_vectors) != len(neighbors):
        return None

    composition_only = all(
        _euclidean_distance(
            query_vector[: len(species_order)],
            neighbor_vector[: len(species_order)],
        )
        <= INTERPOLATION_COMPOSITION_MATCH_EPS
        for neighbor_vector in neighbor_vectors
    )
    if composition_only and len(control_names) >= 1:
        weights = _temperature_bracket_weights(
            query_vector[len(species_order)],
            [
                neighbor_vector[len(species_order)]
                for neighbor_vector in neighbor_vectors
            ],
        )
        if weights is not None:
            return {
                "mode": "along_trajectory_T",
                "weights": weights,
                "inside_hull": True,
            }

    if not _axis_aligned_bracketed(query_vector, neighbor_vectors):
        return None

    dimension = len(query_vector)
    simplex_size = min(len(neighbor_vectors), dimension + 1)
    simplex_vectors = neighbor_vectors[:simplex_size]
    weights = _simplex_barycentric_weights(query_vector, simplex_vectors)
    if weights is None:
        return None
    padded = list(weights) + [0.0] * (len(neighbor_vectors) - len(weights))
    if any(weight < -INTERPOLATION_COORDINATE_EPS for weight in padded):
        return None
    if not math.isclose(sum(padded), 1.0, rel_tol=0.0, abs_tol=1.0e-9):
        return None
    return {
        "mode": "convex_hull_barycentric",
        "weights": padded,
        "inside_hull": True,
    }


def interpolate_equilibrium_payload(
    query_key: Mapping[str, Any],
    neighbors: Sequence[Mapping[str, Any]],
    *,
    weights: Sequence[float],
) -> dict[str, Any]:
    if not neighbors:
        raise ValueError("interpolation requires at least one neighbor")
    reference = neighbors[0].get("payload")
    if not isinstance(reference, Mapping):
        raise ValueError("interpolation neighbor payload missing")
    reference_result = reference.get("equilibrium_result", {})
    if not isinstance(reference_result, Mapping):
        raise ValueError("interpolation neighbor equilibrium_result missing")

    interpolated_result = copy.deepcopy(dict(reference_result))
    controls = query_key.get("controls", {})
    if not isinstance(controls, Mapping):
        controls = {}
    requested_temperature_C = None
    if controls.get("T_K") is not None:
        requested_temperature_C = (
            float(controls.get("T_K")) - CELSIUS_TO_KELVIN_OFFSET
        )
    requested_pressure_bar = None
    if controls.get("pressure_bar") is not None:
        requested_pressure_bar = float(controls.get("pressure_bar"))
    requested_fO2_log = None
    if controls.get("log_fO2") is not None:
        requested_fO2_log = float(controls.get("log_fO2"))

    solved_temperature_C = _weighted_average_scalar(
        neighbors,
        weights,
        "temperature_C",
    )
    solved_pressure_bar = _weighted_average_scalar(
        neighbors,
        weights,
        "pressure_bar",
    )
    solved_fO2_log = _weighted_average_scalar(
        neighbors,
        weights,
        "fO2_log",
    )
    if solved_temperature_C is not None:
        interpolated_result["temperature_C"] = solved_temperature_C
    elif requested_temperature_C is not None:
        interpolated_result["temperature_C"] = requested_temperature_C
    if solved_pressure_bar is not None:
        interpolated_result["pressure_bar"] = solved_pressure_bar
    elif requested_pressure_bar is not None:
        interpolated_result["pressure_bar"] = requested_pressure_bar
    if solved_fO2_log is not None:
        interpolated_result["fO2_log"] = solved_fO2_log
    elif requested_fO2_log is not None:
        interpolated_result["fO2_log"] = requested_fO2_log

    interpolated_result["liquid_fraction"] = _weighted_average_scalar(
        neighbors,
        weights,
        "liquid_fraction",
    )
    interpolated_result["vapor_pressures_Pa"] = _weighted_average_mapping(
        neighbors,
        weights,
        ("equilibrium_result", "vapor_pressures_Pa"),
    )
    interpolated_result["phase_masses_kg"] = _weighted_average_mapping(
        neighbors,
        weights,
        ("equilibrium_result", "phase_masses_kg"),
    )
    interpolated_result["liquid_composition_wt_pct"] = _weighted_average_mapping(
        neighbors,
        weights,
        ("equilibrium_result", "liquid_composition_wt_pct"),
    )
    interpolated_result["activity_coefficients"] = _weighted_average_mapping(
        neighbors,
        weights,
        ("equilibrium_result", "activity_coefficients"),
    )
    interpolated_result["vapor_pressures_source"] = {
        species: "cached_interpolated"
        for species in interpolated_result.get("vapor_pressures_Pa", {})
    }
    interpolated_result["warnings"] = list(reference_result.get("warnings") or [])
    interpolated_result["warnings"].append("cached_interpolated_linear_estimate")
    operating_point_clamped = (
        _operating_point_differs(requested_temperature_C, solved_temperature_C)
        or _operating_point_differs(requested_pressure_bar, solved_pressure_bar)
        or _operating_point_differs(requested_fO2_log, solved_fO2_log)
    )
    if operating_point_clamped:
        diagnostics = dict(interpolated_result.get("diagnostics") or {})
        diagnostics.update({
            "operating_point_clamped": True,
            "operating_point_transport": "reduced_real_cache_interpolation",
            "temperature_clamped": _operating_point_differs(
                requested_temperature_C,
                solved_temperature_C,
            ),
            "pressure_clamped": _operating_point_differs(
                requested_pressure_bar,
                solved_pressure_bar,
            ),
            "fO2_clamped": _operating_point_differs(
                requested_fO2_log,
                solved_fO2_log,
            ),
            "requested_temperature_C": requested_temperature_C,
            "requested_pressure_bar": requested_pressure_bar,
            "requested_fO2_log": requested_fO2_log,
            "solved_temperature_C": solved_temperature_C,
            "solved_pressure_bar": solved_pressure_bar,
            "solved_fO2_log": solved_fO2_log,
            "authoritative_for_requested_conditions": False,
            "authoritative_for_solved_conditions": False,
            "backend_status": "out_of_domain",
            "backend_status_reason": "clamped_operating_point",
        })
        interpolated_result["diagnostics"] = diagnostics
        interpolated_result["status"] = "out_of_domain"

    payload = {
        "equilibrium_result": interpolated_result,
        "last_vapor_pressures_source": dict(
            interpolated_result.get("vapor_pressures_source") or {}
        ),
        "last_vapor_pressure_diagnostic": {
            "source": "cached_interpolated",
            "interpolation_mode": "linear",
        },
    }
    return payload


def estimate_interpolation_error(
    neighbors: Sequence[Mapping[str, Any]],
    interpolated_payload: Mapping[str, Any],
) -> dict[str, Any]:
    disagreement = _neighbor_disagreement(neighbors)
    interpolated = interpolated_payload.get("equilibrium_result", {})
    if not isinstance(interpolated, Mapping):
        interpolated = {}
    held_out_terms: dict[str, float] = {}
    for species in _vapor_species_union(neighbors):
        values = [
            float(_nested_payload_value(neighbor, ("equilibrium_result", "vapor_pressures_Pa", species)) or 0.0)
            for neighbor in neighbors
        ]
        exact_value = float(
            interpolated.get("vapor_pressures_Pa", {}).get(species, 0.0) or 0.0
        )
        scale = max(abs(value) for value in values + [exact_value, 1.0e-30])
        held_out_terms[species] = max(
            abs(value - exact_value) / scale for value in values
        )
    held_out_max = max(held_out_terms.values(), default=0.0)
    return {
        "term": INTERPOLATION_ERROR_TERM,
        "neighbor_disagreement": disagreement,
        "held_out_relative_error_max": held_out_max,
        "held_out_relative_error_by_species": held_out_terms,
    }


def attempt_cached_interpolation(
    query_key: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    neighbors = greedy_nearest_neighbors(query_key, candidates)
    gate = interpolation_validity_gate(query_key, neighbors)
    if not gate["accepted"]:
        return None
    weight_info = barycentric_interpolation_weights(query_key, neighbors)
    if weight_info is None:
        return None
    payload = interpolate_equilibrium_payload(
        query_key,
        neighbors,
        weights=weight_info["weights"],
    )
    error_estimate = estimate_interpolation_error(neighbors, payload)
    return {
        "payload": payload,
        "neighbors": neighbors,
        "gate": gate,
        "weight_info": weight_info,
        "error_estimate": error_estimate,
    }


def replay_scope_for_interpolation(key: Mapping[str, Any]) -> str:
    bucket_key = canonical_physics_bucket_key_from_replay_key(key)
    return _replay_scope_hash(bucket_key)


def _phase_assemblage_set(payload: Any) -> frozenset[str]:
    if not isinstance(payload, Mapping):
        return frozenset()
    result = payload.get("equilibrium_result", {})
    if not isinstance(result, Mapping):
        return frozenset()
    phases = result.get("phases_present") or []
    return frozenset(str(phase) for phase in phases)


def _equilibrium_status(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    result = payload.get("equilibrium_result", {})
    if not isinstance(result, Mapping):
        return ""
    return str(result.get("status") or "").strip().lower()


def _phase_boundary_refusal(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return "phase_boundary_proximity"
    result = payload.get("equilibrium_result", {})
    if not isinstance(result, Mapping):
        return "phase_boundary_proximity"
    liquid_fraction = result.get("liquid_fraction")
    if liquid_fraction is None:
        return None
    try:
        value = float(liquid_fraction)
    except (TypeError, ValueError):
        return "phase_boundary_proximity"
    eps = INTERPOLATION_PHASE_BOUNDARY_EPS
    if value <= eps:
        return "phase_boundary_proximity"
    phase_masses = result.get("phase_masses_kg", {})
    if isinstance(phase_masses, Mapping):
        positive_masses = []
        for mass in phase_masses.values():
            try:
                mass_value = float(mass)
            except (TypeError, ValueError):
                return "phase_boundary_proximity"
            if mass_value > 0.0:
                positive_masses.append(mass_value)
        total = sum(positive_masses)
        if total > 0.0:
            for mass in positive_masses:
                fraction = mass / total
                if len(positive_masses) > 1 and fraction <= eps:
                    return "phase_boundary_proximity"
    return None


def _query_liquid_fraction_proxy(
    query_key: Mapping[str, Any],
    neighbors: Sequence[Mapping[str, Any]],
) -> float | None:
    species_order, control_names = interpolation_feature_spec(query_key)
    query_vector = interpolation_coordinate_vector(
        query_key,
        species_order=species_order,
        control_names=control_names,
    )
    if len(control_names) == 0:
        return None
    query_t = query_vector[len(species_order)]
    weighted = 0.0
    total_weight = 0.0
    for neighbor in neighbors:
        neighbor_key = neighbor.get("key")
        payload = neighbor.get("payload")
        if not isinstance(neighbor_key, Mapping) or not isinstance(payload, Mapping):
            continue
        neighbor_vector = interpolation_coordinate_vector(
            neighbor_key,
            species_order=species_order,
            control_names=control_names,
        )
        neighbor_t = neighbor_vector[len(species_order)]
        liquid_fraction = payload.get("equilibrium_result", {}).get("liquid_fraction")
        if liquid_fraction is None:
            continue
        distance = abs(neighbor_t - query_t)
        weight = 1.0 / max(distance, 1.0e-9)
        weighted += weight * float(liquid_fraction)
        total_weight += weight
    if total_weight <= 0.0:
        return None
    return weighted / total_weight


def _neighbor_disagreement(neighbors: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    species = _vapor_species_union(neighbors)
    relative_errors: list[float] = []
    for species_name in species:
        values = [
            float(
                _nested_payload_value(
                    neighbor,
                    ("equilibrium_result", "vapor_pressures_Pa", species_name),
                )
                or 0.0
            )
            for neighbor in neighbors
        ]
        if not values:
            continue
        scale = max(max(abs(value) for value in values), 1.0e-30)
        mean_value = sum(values) / len(values)
        relative_errors.append(
            max(abs(value - mean_value) / scale for value in values)
        )
    return {
        "relative_error_max": max(relative_errors, default=0.0),
        "relative_error_mean": (
            sum(relative_errors) / len(relative_errors)
            if relative_errors
            else 0.0
        ),
    }


def _vapor_species_union(neighbors: Sequence[Mapping[str, Any]]) -> list[str]:
    species: set[str] = set()
    for neighbor in neighbors:
        payload = neighbor.get("payload")
        if not isinstance(payload, Mapping):
            continue
        result = payload.get("equilibrium_result", {})
        if not isinstance(result, Mapping):
            continue
        vapor = result.get("vapor_pressures_Pa", {})
        if isinstance(vapor, Mapping):
            species.update(str(name) for name in vapor)
    return sorted(species)


def _nested_payload_value(
    neighbor: Mapping[str, Any],
    path: Sequence[str],
) -> Any:
    value: Any = neighbor.get("payload")
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _weighted_average_scalar(
    neighbors: Sequence[Mapping[str, Any]],
    weights: Sequence[float],
    field_name: str,
) -> float | None:
    total_weight = 0.0
    weighted = 0.0
    for neighbor, weight in zip(neighbors, weights, strict=False):
        if weight <= 0.0:
            continue
        payload = neighbor.get("payload")
        if not isinstance(payload, Mapping):
            continue
        result = payload.get("equilibrium_result", {})
        if not isinstance(result, Mapping):
            continue
        value = result.get(field_name)
        if value is None:
            continue
        weighted += float(weight) * float(value)
        total_weight += float(weight)
    if total_weight <= 0.0:
        return None
    return weighted / total_weight


def _weighted_average_mapping(
    neighbors: Sequence[Mapping[str, Any]],
    weights: Sequence[float],
    path: Sequence[str],
) -> dict[str, float]:
    keys: set[str] = set()
    for neighbor in neighbors:
        mapping = _nested_payload_value(neighbor, path)
        if isinstance(mapping, Mapping):
            keys.update(str(key) for key in mapping)
    averaged: dict[str, float] = {}
    for key in sorted(keys):
        total_weight = 0.0
        weighted = 0.0
        for neighbor, weight in zip(neighbors, weights, strict=False):
            if weight <= 0.0:
                continue
            mapping = _nested_payload_value(neighbor, path)
            if not isinstance(mapping, Mapping) or key not in mapping:
                continue
            weighted += float(weight) * float(mapping[key])
            total_weight += float(weight)
        if total_weight > 0.0:
            averaged[key] = weighted / total_weight
    return averaged


def _operating_point_differs(
    requested: float | None,
    solved: float | None,
) -> bool:
    if requested is None or solved is None:
        return False
    return not math.isclose(
        float(requested),
        float(solved),
        rel_tol=0.0,
        abs_tol=1.0e-9,
    )


def _euclidean_distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(
        sum((float(a) - float(b)) ** 2 for a, b in zip(left, right, strict=False))
    )


def _standardized_distance(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    if not left:
        return 0.0
    scales = [max(abs(value), 1.0e-9) for value in left]
    deltas = [
        (float(a) - float(b)) / scale
        for a, b, scale in zip(left, right, scales, strict=False)
    ]
    return math.sqrt(sum(delta * delta for delta in deltas))


def _axis_aligned_bracketed(
    query_vector: Sequence[float],
    neighbor_vectors: Sequence[Sequence[float]],
) -> bool:
    if not neighbor_vectors:
        return False
    dimension = len(query_vector)
    for dim in range(dimension):
        values = [float(vector[dim]) for vector in neighbor_vectors]
        low = min(values)
        high = max(values)
        if float(query_vector[dim]) < low - INTERPOLATION_COORDINATE_EPS:
            return False
        if float(query_vector[dim]) > high + INTERPOLATION_COORDINATE_EPS:
            return False
    return True


def _temperature_bracket_weights(
    query_t: float,
    neighbor_t_values: Sequence[float],
) -> list[float] | None:
    if len(neighbor_t_values) < 2:
        return None
    sorted_pairs = sorted(
        enumerate(neighbor_t_values),
        key=lambda item: float(item[1]),
    )
    for pair_index in range(len(sorted_pairs) - 1):
        left_index, left_t = sorted_pairs[pair_index]
        right_index, right_t = sorted_pairs[pair_index + 1]
        lo_t = float(left_t)
        hi_t = float(right_t)
        if lo_t - INTERPOLATION_COORDINATE_EPS <= query_t <= hi_t + INTERPOLATION_COORDINATE_EPS:
            span = hi_t - lo_t
            if span <= INTERPOLATION_COORDINATE_EPS:
                if math.isclose(query_t, lo_t, abs_tol=INTERPOLATION_COORDINATE_EPS):
                    weights = [0.0] * len(neighbor_t_values)
                    weights[left_index] = 1.0
                    return weights
                return None
            w_right = (query_t - lo_t) / span
            w_left = 1.0 - w_right
            weights = [0.0] * len(neighbor_t_values)
            weights[left_index] = w_left
            weights[right_index] = w_right
            return weights
    return None


def _simplex_barycentric_weights(
    query_vector: Sequence[float],
    simplex_vectors: Sequence[Sequence[float]],
) -> list[float] | None:
    dimension = len(query_vector)
    if len(simplex_vectors) != dimension + 1:
        return None
    v0 = [float(value) for value in simplex_vectors[0]]
    matrix = [
        [
            float(simplex_vectors[row + 1][col]) - v0[col]
            for row in range(dimension)
        ]
        for col in range(dimension)
    ]
    rhs = [float(query_vector[col]) - v0[col] for col in range(dimension)]
    try:
        coeffs = _solve_linear_system(matrix, rhs)
    except ValueError:
        return None
    weights = [1.0 - sum(coeffs)] + coeffs
    if any(weight < -1.0e-8 for weight in weights):
        return None
    total = sum(weights)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1.0e-8):
        return None
    return weights


def _solve_linear_system(
    matrix: Sequence[Sequence[float]],
    rhs: Sequence[float],
) -> list[float]:
    size = len(rhs)
    augmented = [
        [float(matrix[row][col]) for col in range(size)] + [float(rhs[row])]
        for row in range(size)
    ]
    for pivot_row in range(size):
        pivot_value = augmented[pivot_row][pivot_row]
        if abs(pivot_value) <= INTERPOLATION_COORDINATE_EPS:
            raise ValueError("singular interpolation system")
        for col in range(pivot_row, size + 1):
            augmented[pivot_row][col] /= pivot_value
        for row in range(size):
            if row == pivot_row:
                continue
            factor = augmented[row][pivot_row]
            for col in range(pivot_row, size + 1):
                augmented[row][col] -= factor * augmented[pivot_row][col]
    return [augmented[row][size] for row in range(size)]
