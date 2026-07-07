"""Dual concordance metrics for time-series validation harnesses.

The integral metric is the headline yield-trust surface: cumulative extracted
mass per species, anchored to inventory when available. The time-series metric
is the process-fidelity surface: pointwise pressure/rate trajectory agreement.

The integral score is expected to be tighter than the rate/process score only
when the integral basis is actual cumulative extracted mass or an explicit
inventory-normalized total. That is the owner asymptote case: cumulative yields
can converge to the same final inventory even while instantaneous rate or
pressure trajectories disagree. A pressure-like trajectory is not a valid
headline-yield basis unless the caller explicitly marks it as rate-like.

SC-50 consumer note: the report fields are reserved for the t-110 harness
surface. This module names the consumer but does not wire t-110; that wiring
lands with the t-110 chunk.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Curve = Sequence[tuple[float, float]]
SeriesBySpecies = Mapping[str, Curve]
ScalarBySpecies = Mapping[str, float]


@dataclass(frozen=True)
class SpeciesDualConcordance:
    species: str
    observed_integral: float
    model_integral: float
    integral_anchor: float
    integral_absolute_error: float
    integral_relative_error: float
    integral_score: float
    integral_fold_error: float | None
    time_series_points: int
    time_series_score: float | None
    time_series_error_factor: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "species": self.species,
            "observed_integral": self.observed_integral,
            "model_integral": self.model_integral,
            "integral_anchor": self.integral_anchor,
            "integral_absolute_error": self.integral_absolute_error,
            "integral_relative_error": self.integral_relative_error,
            "integral_score": self.integral_score,
            "integral_fold_error": self.integral_fold_error,
            "time_series_points": self.time_series_points,
            "time_series_score": self.time_series_score,
            "time_series_error_factor": self.time_series_error_factor,
        }


@dataclass(frozen=True)
class DualConcordanceReport:
    species: tuple[SpeciesDualConcordance, ...]
    integral_score: float
    integral_relative_error: float
    time_series_score: float | None
    time_series_error_factor: float | None

    @property
    def headline_yield_score(self) -> float:
        return self.integral_score

    @property
    def process_fidelity_score(self) -> float | None:
        return self.time_series_score

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline_yield_score": self.headline_yield_score,
            "process_fidelity_score": self.process_fidelity_score,
            "integral": {
                "score": self.integral_score,
                "relative_error": self.integral_relative_error,
            },
            "time_series": {
                "score": self.time_series_score,
                "error_factor": self.time_series_error_factor,
            },
            "species": [item.as_dict() for item in self.species],
        }


def dual_concordance(
    *,
    observed_trajectory: SeriesBySpecies | None = None,
    model_trajectory: SeriesBySpecies | None = None,
    observed_cumulative: SeriesBySpecies | None = None,
    model_cumulative: SeriesBySpecies | None = None,
    observed_integral: ScalarBySpecies | None = None,
    model_integral: ScalarBySpecies | None = None,
    inventory: ScalarBySpecies | None = None,
    trajectory_integral_is_rate: bool = False,
) -> DualConcordanceReport:
    """Compute separate integral and time-series concordance scores.

    Integral totals are selected per species in this order: explicit integral,
    final cumulative value, then trapezoid integral of the trajectory only when
    ``trajectory_integral_is_rate`` is true. This lets a saturated
    cumulative-yield series stay inventory-anchored even when its instantaneous
    rate/pressure trajectory is a poor process-fidelity match, while failing
    loud when a pressure-like trajectory would otherwise be integrated as yield.
    """

    species_names = _species_names(
        observed_trajectory,
        model_trajectory,
        observed_cumulative,
        model_cumulative,
        observed_integral,
        model_integral,
    )
    if not species_names:
        raise ValueError("dual concordance requires at least one species")

    species_reports = tuple(
        _species_concordance(
            species,
            observed_trajectory=observed_trajectory,
            model_trajectory=model_trajectory,
            observed_cumulative=observed_cumulative,
            model_cumulative=model_cumulative,
            observed_integral=observed_integral,
            model_integral=model_integral,
            inventory=inventory,
            trajectory_integral_is_rate=trajectory_integral_is_rate,
        )
        for species in species_names
    )

    total_anchor = sum(item.integral_anchor for item in species_reports)
    total_integral_error = sum(item.integral_absolute_error for item in species_reports)
    if total_anchor <= 0.0:
        raise ValueError("integral anchor must be positive")
    integral_relative_error = total_integral_error / total_anchor
    integral_score = _score_from_relative_error(integral_relative_error)

    trajectory_items = [item for item in species_reports if item.time_series_score is not None]
    if trajectory_items:
        total_points = sum(item.time_series_points for item in trajectory_items)
        mean_log_factor = sum(
            math.log(_positive(item.time_series_error_factor, f"time-series factor {item.species}"))
            * item.time_series_points
            for item in trajectory_items
        ) / total_points
        time_series_error_factor = math.exp(mean_log_factor)
        time_series_score = 1.0 / time_series_error_factor
    else:
        time_series_score = None
        time_series_error_factor = None

    return DualConcordanceReport(
        species=species_reports,
        integral_score=integral_score,
        integral_relative_error=integral_relative_error,
        time_series_score=time_series_score,
        time_series_error_factor=time_series_error_factor,
    )


def trapezoid_integral(points: Curve) -> float:
    normalised = _normalise_curve(points, field_name="integral")
    if len(normalised) == 1:
        return 0.0
    total = 0.0
    for (t0, y0), (t1, y1) in zip(normalised, normalised[1:]):
        total += (t1 - t0) * (y0 + y1) / 2.0
    return total


def _species_concordance(
    species: str,
    *,
    observed_trajectory: SeriesBySpecies | None,
    model_trajectory: SeriesBySpecies | None,
    observed_cumulative: SeriesBySpecies | None,
    model_cumulative: SeriesBySpecies | None,
    observed_integral: ScalarBySpecies | None,
    model_integral: ScalarBySpecies | None,
    inventory: ScalarBySpecies | None,
    trajectory_integral_is_rate: bool,
) -> SpeciesDualConcordance:
    obs_total = _integral_for_species(
        species,
        explicit=observed_integral,
        cumulative=observed_cumulative,
        trajectory=observed_trajectory,
        label="observed",
        trajectory_integral_is_rate=trajectory_integral_is_rate,
    )
    model_total = _integral_for_species(
        species,
        explicit=model_integral,
        cumulative=model_cumulative,
        trajectory=model_trajectory,
        label="model",
        trajectory_integral_is_rate=trajectory_integral_is_rate,
    )
    anchor = _integral_anchor(species, obs_total, model_total, inventory)
    absolute_error = abs(model_total - obs_total)
    relative_error = absolute_error / anchor
    time_series_score, time_series_error_factor, time_series_points = (
        _trajectory_concordance(species, observed_trajectory, model_trajectory)
    )
    return SpeciesDualConcordance(
        species=species,
        observed_integral=obs_total,
        model_integral=model_total,
        integral_anchor=anchor,
        integral_absolute_error=absolute_error,
        integral_relative_error=relative_error,
        integral_score=_score_from_relative_error(relative_error),
        integral_fold_error=_fold_error(obs_total, model_total),
        time_series_points=time_series_points,
        time_series_score=time_series_score,
        time_series_error_factor=time_series_error_factor,
    )


def _species_names(*series_maps: Mapping[str, Any] | None) -> tuple[str, ...]:
    names: set[str] = set()
    for series_map in series_maps:
        if series_map is not None:
            names.update(series_map)
    return tuple(sorted(names))


def _integral_for_species(
    species: str,
    *,
    explicit: ScalarBySpecies | None,
    cumulative: SeriesBySpecies | None,
    trajectory: SeriesBySpecies | None,
    label: str,
    trajectory_integral_is_rate: bool,
) -> float:
    if explicit is not None and species in explicit:
        return _finite_nonnegative(float(explicit[species]), f"{label} integral {species}")
    if cumulative is not None and species in cumulative:
        return _final_curve_value(cumulative[species], field_name=f"{label} cumulative {species}")
    if trajectory is not None and species in trajectory:
        if not trajectory_integral_is_rate:
            raise ValueError(
                f"{label} integral source for species {species!r} requires explicit cumulative "
                "mass, explicit integral, or trajectory_integral_is_rate=True"
            )
        return trapezoid_integral(trajectory[species])
    raise ValueError(f"missing {label} integral source for species {species!r}")


def _integral_anchor(
    species: str,
    observed: float,
    model: float,
    inventory: ScalarBySpecies | None,
) -> float:
    if inventory is not None and species in inventory:
        return _positive(float(inventory[species]), f"inventory {species}")
    if observed > 0.0:
        return observed
    if model > 0.0:
        return model
    return 1.0


def _trajectory_concordance(
    species: str,
    observed_trajectory: SeriesBySpecies | None,
    model_trajectory: SeriesBySpecies | None,
) -> tuple[float | None, float | None, int]:
    has_observed = observed_trajectory is not None and species in observed_trajectory
    has_model = model_trajectory is not None and species in model_trajectory
    if has_observed != has_model:
        raise ValueError(f"incomplete time-series pair for species {species!r}")
    if not has_observed:
        return None, None, 0

    observed = _normalise_curve(
        observed_trajectory[species], field_name=f"observed trajectory {species}"
    )
    model = _normalise_curve(model_trajectory[species], field_name=f"model trajectory {species}")
    if not observed:
        raise ValueError(f"empty observed trajectory for species {species!r}")
    log_factors = []
    for time_s, observed_value in observed:
        model_value = _interpolate(model, time_s, field_name=f"model trajectory {species}")
        log_factors.append(abs(math.log(_ratio_for_factor(model_value, observed_value))))
    mean_log_factor = sum(log_factors) / len(log_factors)
    error_factor = math.exp(mean_log_factor)
    score = 1.0 / error_factor
    return score, error_factor, len(log_factors)


def _normalise_curve(points: Curve, *, field_name: str) -> tuple[tuple[float, float], ...]:
    normalised = tuple(
        sorted(
            (
                (
                    _finite_number(float(time_s), f"{field_name} time"),
                    _finite_nonnegative(float(value), f"{field_name} value"),
                )
                for time_s, value in points
            ),
            key=lambda item: item[0],
        )
    )
    if not normalised:
        raise ValueError(f"{field_name} requires at least one point")
    previous_time: float | None = None
    for time_s, _value in normalised:
        if previous_time is not None and time_s <= previous_time:
            raise ValueError(f"{field_name} times must be unique")
        previous_time = time_s
    return normalised


def _final_curve_value(points: Curve, *, field_name: str) -> float:
    return _normalise_curve(points, field_name=field_name)[-1][1]


def _interpolate(
    points: tuple[tuple[float, float], ...],
    time_s: float,
    *,
    field_name: str,
) -> float:
    if time_s < points[0][0] or time_s > points[-1][0]:
        raise ValueError(f"{field_name} does not bracket t={time_s}")
    for existing_time, value in points:
        if existing_time == time_s:
            return value
    for (t0, y0), (t1, y1) in zip(points, points[1:]):
        if t0 <= time_s <= t1:
            fraction = (time_s - t0) / (t1 - t0)
            return y0 + fraction * (y1 - y0)
    raise ValueError(f"{field_name} does not bracket t={time_s}")


def _ratio_for_factor(model_value: float, observed_value: float) -> float:
    if model_value == 0.0 and observed_value == 0.0:
        return 1.0
    if model_value <= 0.0 or observed_value <= 0.0:
        raise ValueError("time-series concordance needs positive nonzero values")
    return model_value / observed_value


def _score_from_relative_error(relative_error: float) -> float:
    return max(0.0, 1.0 - relative_error)


def _factor_from_score(score: float) -> float:
    if score <= 0.0:
        return math.inf
    return 1.0 / score


def _fold_error(observed: float, model: float) -> float | None:
    if observed == 0.0 and model == 0.0:
        return 1.0
    if observed <= 0.0 or model <= 0.0:
        return None
    ratio = model / observed
    return max(ratio, 1.0 / ratio)


def _positive(value: float, label: str) -> float:
    value = _finite_number(value, label)
    if value <= 0.0:
        raise ValueError(f"{label} must be positive")
    return value


def _finite_nonnegative(value: float, label: str) -> float:
    value = _finite_number(value, label)
    if value < 0.0:
        raise ValueError(f"{label} must be nonnegative")
    return value


def _finite_number(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value
