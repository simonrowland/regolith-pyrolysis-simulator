"""Shared, diagnostic-only literature reproduction comparisons."""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping, Sequence

from simulator.diagnostic_helpers.timeseries_concordance import trapezoid_integral


STANDARD_MOLAR_VOLUME_L_PER_MOL = 22.414
O2_RELEASE_RATE_DEPENDENCY = (
    "w1119dt, review/w1119dt @ 7fce7ebce (not yet landed)"
)

COMPARISON_STATUSES = frozenset(
    {
        "match",
        "mismatch",
        "unsupported-observable",
        "unsupported-speciation",
        "assumed-input",
        "out-of-domain",
        # Declared ordering/bound verdicts (extract-store qualitative claims).
        "ordering-pass",
        "ordering-fail",
        "ordering-not-evaluable",
        # Computed against the same table the engine coefficient cites; shown,
        # never counted as validation (b-134 class).
        "self-agreement-excluded",
    }
)
ORDERING_VERDICT_STATUSES = frozenset(
    {
        "ordering-pass",
        "ordering-fail",
        "ordering-not-evaluable",
    }
)
SCORING_STATUSES = frozenset(
    {
        "match",
        "mismatch",
        "ordering-pass",
        "ordering-fail",
    }
)
COMPARISON_ARTIFACT_SCHEMA_VERSION = 2
COMPARISON_ARTIFACT_DOMAINS = frozenset({"mre", "vacuum_pyrolysis"})


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("comparison digests require finite floats")
    return value


def content_digest(value: Any) -> str:
    """Return a stable SHA-256 digest of canonical JSON content."""

    payload = json.dumps(
        _jsonable(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_comparison_artifact(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate schema-v2 artifacts and upgrade chunk-A schema-v1 payloads."""

    if not isinstance(value, Mapping):
        raise ValueError("comparison artifact must be a mapping")
    artifact = _jsonable(value)
    try:
        schema_version = int(artifact.get("schema_version"))
    except (TypeError, ValueError) as exc:
        raise ValueError("comparison artifact schema_version must be 1 or 2") from exc
    if schema_version == 1:
        measurement_id = str(artifact.get("measurement_id") or "").strip()
        if not measurement_id:
            raise ValueError("legacy comparison artifact requires measurement_id")
        upgraded = {
            "schema_version": COMPARISON_ARTIFACT_SCHEMA_VERSION,
            "domain": "vacuum_pyrolysis",
            "preset_kind": "faithful_with_remediation_twin",
            "execution_scope": "vacuum_pyrolysis",
            "paper_id": measurement_id,
            "case_id": "legacy_unspecified",
            **artifact,
            "schema_version": COMPARISON_ARTIFACT_SCHEMA_VERSION,
            "unsupported_observables": list(
                artifact.get("unsupported_observables") or []
            ),
        }
        return upgraded
    if schema_version != COMPARISON_ARTIFACT_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported comparison artifact schema_version: {schema_version}"
        )
    required_text_fields = (
        "domain",
        "preset_kind",
        "execution_scope",
        "paper_id",
        "case_id",
        "measurement_id",
        "sidecar_path",
        "markdown_path",
    )
    for field in required_text_fields:
        if not str(artifact.get(field) or "").strip():
            raise ValueError(f"comparison artifact requires {field}")
    if artifact["domain"] not in COMPARISON_ARTIFACT_DOMAINS:
        raise ValueError(f"unsupported comparison artifact domain: {artifact['domain']!r}")
    if not isinstance(artifact.get("digests"), Mapping):
        raise ValueError("comparison artifact requires digests")
    for field in ("records", "qualitative_observations", "unsupported_observables"):
        if not isinstance(artifact.get(field), list):
            raise ValueError(f"comparison artifact {field} must be a list")
    return artifact


@dataclass(frozen=True)
class ComparisonRecord:
    """Minimum comparison record owned by the reproduction chain contract."""

    case_id: str
    source_id: str
    observable_id: str
    species: str | None
    coordinate: Mapping[str, Any]
    expected_value: float | None
    expected_uncertainty: Mapping[str, Any] | None
    actual_value: float | None
    units: str
    residual: float | None
    status: str
    evidence_scope: str
    source_locator: Mapping[str, Any] | str
    recipe_digest: str
    observation_digest: str
    runtime_digest: str

    def __post_init__(self) -> None:
        if self.status not in COMPARISON_STATUSES:
            raise ValueError(f"unsupported comparison status: {self.status!r}")
        if not self.coordinate:
            raise ValueError("temperature/time/window coordinate is required")
        if self.expected_value is not None and not math.isfinite(self.expected_value):
            raise ValueError("expected_value must be finite")
        if self.actual_value is not None and not math.isfinite(self.actual_value):
            raise ValueError("actual_value must be finite")
        if self.residual is not None and not math.isfinite(self.residual):
            raise ValueError("residual must be finite")

    def as_dict(self) -> dict[str, Any]:
        """Serialize with the chain contract's literal coordinate field."""

        return {
            "case_id": self.case_id,
            "source_id": self.source_id,
            "observable_id": self.observable_id,
            "species": self.species,
            "temperature/time/window": dict(self.coordinate),
            "expected_value": self.expected_value,
            "expected_uncertainty": (
                dict(self.expected_uncertainty)
                if self.expected_uncertainty is not None
                else None
            ),
            "actual_value": self.actual_value,
            "units": self.units,
            "residual": self.residual,
            "status": self.status,
            "evidence_scope": self.evidence_scope,
            "source_locator": self.source_locator,
            "recipe_digest": self.recipe_digest,
            "observation_digest": self.observation_digest,
            "runtime_digest": self.runtime_digest,
        }


class TimeSeriesComparisonRefusal(ValueError):
    """Typed refusal when a time-series comparison lacks usable runtime data."""

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        dependency: str | None = None,
    ) -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        self.dependency = dependency
        message = f"{self.reason_code}: {self.detail}"
        if dependency is not None:
            message = f"{message}; dependency: {dependency}"
        super().__init__(message)


def _validated_uncertainty(
    uncertainty: Mapping[str, Any],
) -> tuple[str, float]:
    kind = str(uncertainty.get("kind") or "")
    value = uncertainty.get("value")
    if value is None:
        raise ValueError("numeric observations require uncertainty.value")
    tolerance = float(value)
    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise ValueError("uncertainty.value must be finite and non-negative")
    if kind not in {"absolute", "relative_fraction", "log10_decades"}:
        raise ValueError(f"unsupported uncertainty kind: {kind!r}")
    return kind, tolerance


def o2_release_molar_flow_to_ppm(
    o2_release_mol_min: float | None,
    *,
    argon_flow_nl_min: float,
) -> float:
    """Convert released O2 molar flow to ppm by mole in the Ar carrier."""

    if o2_release_mol_min is None:
        raise TimeSeriesComparisonRefusal(
            "o2_release_rate_missing",
            "runtime has no per-step O2 release molar-flow sample",
            dependency=O2_RELEASE_RATE_DEPENDENCY,
        )
    o2_rate = float(o2_release_mol_min)
    argon_flow = float(argon_flow_nl_min)
    if not math.isfinite(o2_rate) or o2_rate < 0.0:
        raise ValueError("O2 release molar flow must be finite and non-negative")
    if not math.isfinite(argon_flow) or argon_flow <= 0.0:
        raise ValueError("argon NL/min flow must be finite and positive")

    # The Robinot preset supplies 0.3 NL/min Ar; at 0 °C and 1 atm,
    # n_dot_Ar = 0.3 / 22.414 mol/min. With both outlet streams in mol/min,
    # x_O2 = n_dot_O2 / (n_dot_Ar + n_dot_O2), and ppm = x_O2 * 1e6.
    n_dot_ar = argon_flow / STANDARD_MOLAR_VOLUME_L_PER_MOL
    x_o2 = o2_rate / (n_dot_ar + o2_rate)
    return x_o2 * 1_000_000.0


def compare_o2_release_time_series(
    *,
    case_id: str,
    source_id: str,
    observable_id: str,
    observation_points: Sequence[Mapping[str, Any]],
    runtime_rate_samples: Sequence[Mapping[str, Any]] | None,
    argon_flow_nl_min: float,
    evidence_scope: str,
    source_locator: Mapping[str, Any] | str,
    recipe: Mapping[str, Any],
    observation: Mapping[str, Any],
    runtime: Mapping[str, Any],
    species: str = "O2",
) -> tuple[ComparisonRecord, ...]:
    """Compare observed O2 ppm with per-step runtime release rates."""

    if not runtime_rate_samples:
        raise TimeSeriesComparisonRefusal(
            "o2_release_rate_series_missing",
            "runtime exposes no per-step O2 release samples at the observation resolution",
            dependency=O2_RELEASE_RATE_DEPENDENCY,
        )
    runtime_ppm_samples = []
    for index, sample in enumerate(runtime_rate_samples):
        if not isinstance(sample, Mapping):
            raise ValueError(f"runtime_rate_samples[{index}] must be a mapping")
        time_min = sample.get("time_min")
        rate = sample.get("o2_release_mol_min")
        runtime_ppm_samples.append(
            {
                "time_min": time_min,
                "value": o2_release_molar_flow_to_ppm(
                    rate,
                    argon_flow_nl_min=argon_flow_nl_min,
                ),
            }
        )
    return compare_time_series(
        case_id=case_id,
        source_id=source_id,
        observable_id=observable_id,
        species=species,
        observation_points=observation_points,
        runtime_samples=runtime_ppm_samples,
        series_semantics="rate",
        units="ppm",
        evidence_scope=evidence_scope,
        source_locator=source_locator,
        recipe=recipe,
        observation=observation,
        runtime=runtime,
    )


def _compare_pointwise_observations(
    observations: Sequence[tuple[float, float, Mapping[str, Any] | None, Any]],
    samples: Sequence[tuple[float, float | None]],
    *,
    case_id: str,
    source_id: str,
    observable_id: str,
    species: str | None,
    coordinate_key: str,
    units: str,
    evidence_scope: str,
    source_locator: Mapping[str, Any] | str,
    recipe: Mapping[str, Any],
    observation: Mapping[str, Any],
    runtime: Mapping[str, Any],
    coordinate_unit: str = "min",
    unsupported_speciation: bool = False,
    assumed_input: bool = False,
    ungraded: bool = False,
    out_of_domain: bool = False,
) -> tuple[tuple[ComparisonRecord, ...], list[float]]:
    """Interpolate runtime values and build records for normalized points."""

    sample_times = [time for time, _ in samples]
    actual_values = [
        _interpolate_runtime_sample(
            time,
            samples,
            sample_times,
            coordinate_unit=coordinate_unit,
        )
        for time, _, _, _ in observations
    ]

    records = []
    for (time, expected, uncertainty, point_locator), actual in zip(
        observations, actual_values
    ):
        records.append(
            compare_values(
                case_id=case_id,
                source_id=source_id,
                observable_id=observable_id,
                species=species,
                coordinate={coordinate_key: time},
                expected_value=expected,
                expected_uncertainty=uncertainty,
                actual_value=actual,
                units=units,
                evidence_scope=evidence_scope,
                source_locator=_series_point_locator(source_locator, point_locator),
                recipe=recipe,
                observation=observation,
                runtime=runtime,
                unsupported_speciation=unsupported_speciation,
                assumed_input=assumed_input,
                ungraded=ungraded,
                out_of_domain=out_of_domain,
            )
        )
    return tuple(records), actual_values


def compare_time_series(
    *,
    case_id: str,
    source_id: str,
    observable_id: str,
    observation_points: Sequence[Mapping[str, Any]],
    runtime_samples: Sequence[Mapping[str, Any]],
    series_semantics: str,
    units: str,
    evidence_scope: str,
    source_locator: Mapping[str, Any] | str,
    recipe: Mapping[str, Any],
    observation: Mapping[str, Any],
    runtime: Mapping[str, Any],
    species: str | None = None,
    unsupported_speciation: bool = False,
    assumed_input: bool = False,
    out_of_domain: bool = False,
) -> tuple[ComparisonRecord, ...]:
    """Compare a trajectory pointwise and, for rates, by signed time integral.

    Runtime values are linearly interpolated onto observation times. The
    interpolation requires bracketing samples, never extrapolates, and refuses
    explicit missing values instead of replacing them with zero. Integral
    uncertainty is a fully correlated sum of pointwise absolute bounds, so
    digitization or analyzer systematics do not shrink as if independent.
    """

    observations = _normalise_observation_points(observation_points)
    samples = _normalise_runtime_samples(runtime_samples)
    if series_semantics not in {"rate", "cumulative"}:
        raise TimeSeriesComparisonRefusal(
            "unsupported_series_semantics",
            f"series semantics {series_semantics!r} must be rate or cumulative",
        )
    if len(observations) < 2:
        raise ValueError("time-series integral requires at least two observations")
    point_records, actual_values = _compare_pointwise_observations(
        observations,
        samples,
        case_id=case_id,
        source_id=source_id,
        observable_id=observable_id,
        species=species,
        coordinate_key="time_min",
        units=units,
        evidence_scope=evidence_scope,
        source_locator=source_locator,
        recipe=recipe,
        observation=observation,
        runtime=runtime,
        unsupported_speciation=unsupported_speciation,
        assumed_input=assumed_input,
        out_of_domain=out_of_domain,
    )
    records = list(point_records)

    if series_semantics == "rate":
        times = [time_min for time_min, _, _, _ in observations]
        expected_values = [expected for _, expected, _, _ in observations]
        observed_curve = list(zip(times, expected_values))
        runtime_curve = list(zip(times, actual_values))
        integral_uncertainty = _integral_uncertainty_bound(observations)
        records.append(
            compare_values(
                case_id=case_id,
                source_id=source_id,
                observable_id=f"{observable_id}:integral",
                species=species,
                coordinate={
                    "window_start_min": times[0],
                    "window_end_min": times[-1],
                },
                expected_value=trapezoid_integral(
                    observed_curve,
                    allow_negative_values=True,
                ),
                expected_uncertainty={
                    "kind": "absolute",
                    "value": integral_uncertainty,
                },
                actual_value=trapezoid_integral(
                    runtime_curve,
                    allow_negative_values=True,
                ),
                units=f"{units} min",
                evidence_scope=f"{evidence_scope}; signed trapezoidal rate integral",
                source_locator=_series_point_locator(
                    source_locator,
                    {"integral_of": observable_id, "method": "trapezoid"},
                ),
                recipe=recipe,
                observation=observation,
                runtime=runtime,
                unsupported_speciation=unsupported_speciation,
                assumed_input=assumed_input,
                out_of_domain=out_of_domain,
            )
        )
    return tuple(records)


def _series_point_locator(
    series_locator: Mapping[str, Any] | str,
    point_locator: Mapping[str, Any] | str | None,
) -> Mapping[str, Any] | str:
    if point_locator is None:
        return series_locator
    if isinstance(series_locator, Mapping) and isinstance(point_locator, Mapping):
        return {**series_locator, **point_locator}
    return point_locator


def _normalise_observation_points(
    points: Sequence[Mapping[str, Any]],
    *,
    coordinate_key: str = "time_min",
    ungraded: bool = False,
) -> list[tuple[float, float, Mapping[str, Any] | None, Any]]:
    normalised = []
    for index, point in enumerate(points):
        if not isinstance(point, Mapping):
            raise ValueError(f"observation_points[{index}] must be a mapping")
        coordinate = float(point[coordinate_key])
        expected_value = float(point["expected_value"])
        uncertainty = point.get("expected_uncertainty")
        if not math.isfinite(coordinate):
            raise ValueError(
                f"observation_points[{index}].{coordinate_key} must be finite"
            )
        if not math.isfinite(expected_value):
            raise ValueError(f"observation_points[{index}].expected_value must be finite")
        if uncertainty is None and not ungraded:
            raise ValueError(
                f"observation_points[{index}] requires cited expected_uncertainty"
            )
        if uncertainty is not None and not isinstance(uncertainty, Mapping):
            raise ValueError(
                f"observation_points[{index}] requires cited expected_uncertainty"
            )
        if uncertainty is not None:
            _validated_uncertainty(uncertainty)
        normalised.append(
            (
                coordinate,
                expected_value,
                dict(uncertainty) if uncertainty is not None else None,
                point.get("source_locator"),
            )
        )
    normalised.sort(key=lambda row: row[0])
    if len({coordinate for coordinate, _, _, _ in normalised}) != len(normalised):
        raise ValueError("observation times must be unique")
    return normalised


def _normalise_runtime_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    coordinate_key: str = "time_min",
    coordinate_unit: str = "min",
) -> list[tuple[float, float]]:
    if not samples:
        raise TimeSeriesComparisonRefusal(
            "runtime_samples_missing", "runtime returned no time-series samples"
        )
    normalised = []
    for index, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            raise ValueError(f"runtime_samples[{index}] must be a mapping")
        coordinate = float(sample[coordinate_key])
        value = sample.get("value")
        if not math.isfinite(coordinate):
            raise ValueError(
                f"runtime_samples[{index}].{coordinate_key} must be finite"
            )
        if value is None:
            raise TimeSeriesComparisonRefusal(
                "runtime_sample_missing",
                f"runtime sample at {coordinate:g} {coordinate_unit} has no value",
            )
        actual = float(value)
        if not math.isfinite(actual):
            raise ValueError(f"runtime_samples[{index}].value must be finite")
        normalised.append((coordinate, actual))
    normalised.sort(key=lambda row: row[0])
    if len({coordinate for coordinate, _ in normalised}) != len(normalised):
        raise ValueError("runtime sample times must be unique")
    return normalised


def _interpolate_runtime_sample(
    coordinate: float,
    samples: Sequence[tuple[float, float]],
    sample_times: Sequence[float],
    *,
    coordinate_unit: str = "min",
) -> float:
    right = bisect_right(sample_times, coordinate)
    if right and sample_times[right - 1] == coordinate:
        return samples[right - 1][1]
    if right == 0 or right == len(samples):
        raise TimeSeriesComparisonRefusal(
            "runtime_time_coverage_missing",
            f"runtime does not bracket observation time {coordinate:g} {coordinate_unit}",
        )
    left_time, left_value = samples[right - 1]
    right_time, right_value = samples[right]
    fraction = (coordinate - left_time) / (right_time - left_time)
    return left_value + fraction * (right_value - left_value)


def _integral_uncertainty_bound(
    observations: Sequence[tuple[float, float, Mapping[str, Any], Any]],
) -> float:
    weights = [0.0] * len(observations)
    for index, (left, right) in enumerate(zip(observations, observations[1:])):
        half_interval = (right[0] - left[0]) / 2.0
        weights[index] += half_interval
        weights[index + 1] += half_interval
    bound = 0.0
    for weight, (_, expected, uncertainty, _) in zip(weights, observations):
        kind, value = _validated_uncertainty(uncertainty)
        if kind == "absolute":
            absolute = value
        elif kind == "relative_fraction":
            absolute = abs(expected) * value
        else:
            if expected <= 0.0:
                raise ValueError(
                    "log10 uncertainty requires positive integral observations"
                )
            absolute = max(
                expected * (10.0**value - 1.0),
                expected * (1.0 - 10.0 ** (-value)),
            )
        bound += weight * absolute
    return bound


def _matches_uncertainty(
    expected: float,
    actual: float,
    uncertainty: Mapping[str, Any],
) -> bool:
    kind, tolerance = _validated_uncertainty(uncertainty)
    if kind == "absolute":
        return abs(actual - expected) <= tolerance
    if kind == "relative_fraction":
        return abs(actual - expected) <= abs(expected) * tolerance
    if kind == "log10_decades":
        if expected <= 0.0 or actual <= 0.0:
            return False
        return abs(math.log10(actual / expected)) <= tolerance
    raise AssertionError(f"validated uncertainty kind is unhandled: {kind!r}")


def compare_values(
    *,
    case_id: str,
    source_id: str,
    observable_id: str,
    species: str | None,
    coordinate: Mapping[str, Any],
    expected_value: float | None,
    expected_uncertainty: Mapping[str, Any] | None,
    actual_value: float | None,
    units: str,
    evidence_scope: str,
    source_locator: Mapping[str, Any] | str,
    recipe: Mapping[str, Any],
    observation: Mapping[str, Any],
    runtime: Mapping[str, Any],
    unsupported_speciation: bool = False,
    assumed_input: bool = False,
    ungraded: bool = False,
    out_of_domain: bool = False,
    status_override: str | None = None,
) -> ComparisonRecord:
    """Compare one independent observation to one runtime value."""

    residual = (
        float(actual_value) - float(expected_value)
        if actual_value is not None and expected_value is not None
        else None
    )
    if (
        expected_value is not None
        and expected_uncertainty is None
        and not ungraded
    ):
        raise ValueError("numeric observations require cited uncertainty")
    if ungraded and (expected_value is None or expected_uncertainty is not None):
        raise ValueError(
            "ungraded comparisons require a numeric observation without cited uncertainty"
        )
    if expected_value is not None and expected_uncertainty is not None:
        _validated_uncertainty(expected_uncertainty)
    if status_override is not None:
        if status_override not in COMPARISON_STATUSES:
            raise ValueError(f"unsupported comparison status: {status_override!r}")
        if status_override in SCORING_STATUSES:
            # A scoring verdict grades an observation against its cited band, so it
            # needs both: a numeric observation AND a valid comparator band. An empty
            # or metadata-only band (e.g. {"temperature_K": 5.0}) is not a band, and
            # an absent observation has nothing to grade.
            if expected_value is None or expected_uncertainty is None:
                raise ValueError("scoring status overrides require cited uncertainty")
            _validated_uncertainty(expected_uncertainty)
        status = status_override
    elif out_of_domain:
        status = "out-of-domain"
    elif unsupported_speciation:
        status = "unsupported-speciation"
    elif expected_value is None or actual_value is None:
        status = "unsupported-observable"
    elif ungraded or assumed_input:
        status = "assumed-input"
    else:
        status = (
            "match"
            if _matches_uncertainty(
                float(expected_value),
                float(actual_value),
                expected_uncertainty,
            )
            else "mismatch"
        )

    return ComparisonRecord(
        case_id=str(case_id),
        source_id=str(source_id),
        observable_id=str(observable_id),
        species=str(species) if species is not None else None,
        coordinate=dict(coordinate),
        expected_value=(
            float(expected_value) if expected_value is not None else None
        ),
        expected_uncertainty=(
            dict(expected_uncertainty)
            if expected_uncertainty is not None
            else None
        ),
        actual_value=float(actual_value) if actual_value is not None else None,
        units=str(units),
        residual=residual,
        status=status,
        evidence_scope=str(evidence_scope),
        source_locator=source_locator,
        recipe_digest=content_digest(recipe),
        observation_digest=content_digest(observation),
        runtime_digest=content_digest(runtime),
    )


def records_to_json(
    records: Sequence[ComparisonRecord],
    *,
    indent: int = 2,
) -> str:
    return json.dumps(
        [record.as_dict() for record in records],
        allow_nan=False,
        ensure_ascii=True,
        indent=indent,
        sort_keys=False,
    )


def _markdown_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value)


def records_to_markdown(records: Sequence[ComparisonRecord]) -> str:
    """Return a concise residual table suitable for saved reports."""

    lines = [
        "| case | observable | species | temperature/time/window | expected | "
        "actual | residual | units | status | evidence | source |",
        "|---|---|---|---|---:|---:|---:|---|---|---|---|",
    ]
    for record in records:
        locator = _markdown_value(record.source_locator).replace("|", "\\|")
        coordinate = _markdown_value(record.coordinate).replace("|", "\\|")
        lines.append(
            f"| {record.case_id} | {record.observable_id} | "
            f"{record.species or '-'} | {coordinate} | "
            f"{_markdown_value(record.expected_value)} | "
            f"{_markdown_value(record.actual_value)} | "
            f"{_markdown_value(record.residual)} | {record.units} | "
            f"{record.status} | {record.evidence_scope} | {locator} |"
        )
    return "\n".join(lines)
