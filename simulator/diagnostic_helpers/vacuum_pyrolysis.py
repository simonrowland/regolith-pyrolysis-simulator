"""Vacuum-pyrolysis literature selectors over shared comparison semantics."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL, parse_formula
from simulator.diagnostic_helpers.reproduction_compare import (
    COMPARISON_ARTIFACT_SCHEMA_VERSION,
    ComparisonRecord,
    compare_values,
    content_digest,
    records_to_markdown,
)


COMPARISON_SCHEMA_VERSION = COMPARISON_ARTIFACT_SCHEMA_VERSION
SUPPORTED_SELECTOR_KINDS = frozenset(
    {
        "final_o2_mass_kg",
        "window_o2_mass_kg",
        "o2_mass_yield_fraction",
        "feed_oxygen_extraction_fraction",
        "non_condensed_mass_loss_fraction",
        "species_time_series_kg_hr",
    }
)
CERTIFICATION_STATUSES = frozenset(
    {"certifiable", "assumed-input", "out-of-domain"}
)
OBSERVATION_STATUSES = frozenset({"reported", "assumed"})
QUALITATIVE_STATUSES = frozenset({"observed"})
REPRESENTATION_STATUSES = frozenset({"represented", "not-representable"})


class VacuumPyrolysisComparisonError(ValueError):
    """Named refusal for invalid vacuum-pyrolysis comparison inputs."""


@dataclass(frozen=True)
class VacuumPyrolysisComparisonRun:
    records: tuple[ComparisonRecord, ...]
    qualitative_observations: tuple[Mapping[str, Any], ...]
    recipe_digest: str
    source_digest: str
    result_digest: str

    def as_payload(
        self,
        *,
        paper_id: str,
        case_id: str,
        preset_kind: str,
        execution_scope: str,
        measurement_id: str,
        sidecar_path: str,
        markdown_path: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            "domain": "vacuum_pyrolysis",
            "preset_kind": str(preset_kind),
            "execution_scope": str(execution_scope),
            "paper_id": str(paper_id),
            "case_id": str(case_id),
            "measurement_id": str(measurement_id),
            "sidecar_path": str(sidecar_path),
            "markdown_path": str(markdown_path),
            "digests": {
                "recipe_sha256": self.recipe_digest,
                "source_sha256": self.source_digest,
                "result_sha256": self.result_digest,
            },
            "records": [record.as_dict() for record in self.records],
            "qualitative_observations": [
                dict(observation)
                for observation in self.qualitative_observations
            ],
            "unsupported_observables": [],
        }

    def markdown(self) -> str:
        return records_to_markdown(self.records)


def load_vacuum_pyrolysis_observations(path: str | Path) -> dict[str, Any]:
    sidecar_path = Path(path)
    try:
        loaded = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise VacuumPyrolysisComparisonError(
            f"cannot load vacuum-pyrolysis observations {sidecar_path}: {exc}"
        ) from exc
    return _mapping(loaded, str(sidecar_path))


def evaluate_vacuum_pyrolysis_comparison(
    preset: Mapping[str, Any],
    observations: Mapping[str, Any],
    runtime_result: Mapping[str, Any],
    *,
    feedstocks: Mapping[str, Any],
) -> VacuumPyrolysisComparisonRun:
    recipe = _mapping(preset, "preset")
    sidecar = _mapping(observations, "observations")
    runtime = _mapping(runtime_result, "runtime_result")
    measurement_id = _required_text(recipe.get("measurement_id"), "measurement_id")
    measurement = _mapping(
        _mapping(sidecar.get("measurements"), "observations.measurements").get(
            measurement_id
        ),
        f"observations.measurements.{measurement_id}",
    )
    source_id = _required_text(
        _mapping(measurement.get("paper_citation"), "paper_citation").get(
            "citation_id"
        ),
        f"{measurement_id}.paper_citation.citation_id",
    )
    if source_id != _required_text(
        recipe.get("paper_citation_id"),
        "paper_citation_id",
    ):
        raise VacuumPyrolysisComparisonError(
            "preset paper_citation_id does not match observation source"
        )

    selectors = _validated_selectors(recipe)
    points = _sequence(
        measurement.get("comparison_points"),
        f"{measurement_id}.comparison_points",
    )
    observation_package = {
        "schema_version": sidecar.get("schema_version"),
        "measurement_id": measurement_id,
        "measurement": measurement,
    }
    recipe_digest = content_digest(recipe)
    source_digest = content_digest(observation_package)
    result_digest = content_digest(runtime)
    runtime_failed = str(runtime.get("status") or "") in {"failed", "refused"}
    recipe_has_assumed_inputs = _contains_assumed_inputs(recipe)

    records: list[ComparisonRecord] = []
    for index, raw_point in enumerate(points):
        point = _mapping(
            raw_point,
            f"{measurement_id}.comparison_points[{index}]",
        )
        observable_id = _required_text(
            point.get("observable_id"),
            f"{measurement_id}.comparison_points[{index}].observable_id",
        )
        selector = selectors.get(observable_id)
        if selector is None:
            raise VacuumPyrolysisComparisonError(
                f"observation references unknown selector: {observable_id}"
            )
        coordinate = _mapping(
            point.get("coordinate"),
            f"{measurement_id}.{observable_id}.coordinate",
        )
        expected_value = _optional_finite_float(
            point.get("expected_value"),
            f"{measurement_id}.{observable_id}.expected_value",
        )
        uncertainty = point.get("uncertainty")
        if expected_value is not None and not isinstance(uncertainty, Mapping):
            raise VacuumPyrolysisComparisonError(
                f"{measurement_id}.{observable_id} requires uncertainty"
            )
        units = _required_text(
            point.get("units"),
            f"{measurement_id}.{observable_id}.units",
        )
        if units != selector["units"]:
            raise VacuumPyrolysisComparisonError(
                f"selector units mismatch for {observable_id}: "
                f"{selector['units']!r} != {units!r}"
            )
        observation_status = _required_text(
            point.get("status"),
            f"{measurement_id}.{observable_id}.status",
        )
        if observation_status not in OBSERVATION_STATUSES:
            raise VacuumPyrolysisComparisonError(
                f"unsupported observation status: {observation_status!r}"
            )

        actual_value, unsupported_speciation = resolve_vacuum_observable(
            selector,
            coordinate,
            runtime,
            feedstocks=feedstocks,
        )
        certification = selector["certification"]
        records.append(
            compare_values(
                case_id=_required_text(recipe.get("paper_id"), "paper_id"),
                source_id=source_id,
                observable_id=observable_id,
                species=selector.get("species"),
                coordinate=coordinate,
                expected_value=expected_value,
                expected_uncertainty=(
                    dict(uncertainty)
                    if isinstance(uncertainty, Mapping)
                    else None
                ),
                actual_value=actual_value,
                units=units,
                evidence_scope=selector["evidence_scope"],
                source_locator=point.get("source_locator") or {},
                recipe=recipe,
                observation=observation_package,
                runtime=runtime,
                unsupported_speciation=unsupported_speciation,
                assumed_input=(
                    certification["status"] == "assumed-input"
                    or observation_status == "assumed"
                    or recipe_has_assumed_inputs
                ),
                out_of_domain=(
                    runtime_failed
                    or certification["status"] == "out-of-domain"
                ),
            )
        )

    qualitative = _validated_qualitative_observations(
        measurement.get("qualitative_comparison_observations", ())
    )
    return VacuumPyrolysisComparisonRun(
        records=tuple(records),
        qualitative_observations=tuple(qualitative),
        recipe_digest=recipe_digest,
        source_digest=source_digest,
        result_digest=result_digest,
    )


def resolve_vacuum_observable(
    selector: Mapping[str, Any],
    coordinate: Mapping[str, Any],
    runtime_result: Mapping[str, Any],
    *,
    feedstocks: Mapping[str, Any],
) -> tuple[float | None, bool]:
    kind = _required_text(selector.get("kind"), "selector.kind")
    if kind not in SUPPORTED_SELECTOR_KINDS:
        raise VacuumPyrolysisComparisonError(
            f"unsupported vacuum-pyrolysis selector kind: {kind!r}"
        )
    runtime = _mapping(runtime_result, "runtime_result")
    rows = _runtime_rows(runtime)
    if kind == "final_o2_mass_kg":
        return _final_o2_mass_kg(rows), False
    if kind == "window_o2_mass_kg":
        return _window_o2_mass_kg(rows, coordinate), False
    if kind == "o2_mass_yield_fraction":
        o2_mass_kg = _window_o2_mass_kg(rows, coordinate)
        initial_mass_kg = _runtime_mass_kg(runtime)
        if o2_mass_kg is None or initial_mass_kg <= 0.0:
            return None, False
        return o2_mass_kg / initial_mass_kg, False
    if kind == "feed_oxygen_extraction_fraction":
        o2_mass_kg = _window_o2_mass_kg(rows, coordinate)
        if o2_mass_kg is None:
            return None, False
        feed_oxygen_kg = _feed_oxygen_kg(runtime, feedstocks)
        if feed_oxygen_kg <= 0.0:
            return None, False
        return o2_mass_kg / feed_oxygen_kg, False
    if kind == "non_condensed_mass_loss_fraction":
        emitted_mass_kg = _non_condensed_mass_kg(runtime)
        initial_mass_kg = _runtime_mass_kg(runtime)
        if emitted_mass_kg is None or initial_mass_kg <= 0.0:
            return None, False
        return emitted_mass_kg / initial_mass_kg, False

    species = _required_text(selector.get("species"), "selector.species")
    actual = _species_time_series_value(
        rows,
        coordinate,
        species=species,
        aggregation=str(selector.get("aggregation") or "point"),
    )
    return actual, actual is None


def _validated_selectors(
    recipe: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    selectors: dict[str, dict[str, Any]] = {}
    for index, raw_selector in enumerate(
        _sequence(recipe.get("measurement_selectors"), "measurement_selectors")
    ):
        selector = dict(_mapping(raw_selector, f"measurement_selectors[{index}]"))
        observable_id = _required_text(
            selector.get("observable_id"),
            f"measurement_selectors[{index}].observable_id",
        )
        kind = _required_text(
            selector.get("kind"),
            f"measurement_selectors[{index}].kind",
        )
        if kind not in SUPPORTED_SELECTOR_KINDS:
            raise VacuumPyrolysisComparisonError(
                f"unsupported vacuum-pyrolysis selector kind: {kind!r}"
            )
        selector["units"] = _required_text(
            selector.get("units"),
            f"measurement_selectors[{index}].units",
        )
        selector["evidence_scope"] = _required_text(
            selector.get("evidence_scope"),
            f"measurement_selectors[{index}].evidence_scope",
        )
        certification = dict(
            _mapping(
                selector.get("certification"),
                f"measurement_selectors[{index}].certification",
            )
        )
        status = _required_text(
            certification.get("status"),
            f"measurement_selectors[{index}].certification.status",
        )
        if status not in CERTIFICATION_STATUSES:
            raise VacuumPyrolysisComparisonError(
                f"unsupported certification status: {status!r}"
            )
        blockers = certification.get("blocked_by", ())
        if status != "certifiable" and (
            not isinstance(blockers, Sequence)
            or isinstance(blockers, (str, bytes))
            or not blockers
        ):
            raise VacuumPyrolysisComparisonError(
                f"{observable_id} non-certifiable selector requires blocked_by"
            )
        certification["status"] = status
        certification["blocked_by"] = [str(item) for item in blockers]
        selector["certification"] = certification
        if observable_id in selectors:
            raise VacuumPyrolysisComparisonError(
                f"duplicate measurement selector: {observable_id}"
            )
        selectors[observable_id] = selector
    return selectors


def _validated_qualitative_observations(raw: Any) -> list[dict[str, Any]]:
    if raw in (None, ()):
        return []
    observations: list[dict[str, Any]] = []
    for index, raw_observation in enumerate(
        _sequence(raw, "qualitative_comparison_observations")
    ):
        observation = dict(
            _mapping(
                raw_observation,
                f"qualitative_comparison_observations[{index}]",
            )
        )
        status = _required_text(
            observation.get("status"),
            f"qualitative_comparison_observations[{index}].status",
        )
        representation = _required_text(
            observation.get("representation_status"),
            f"qualitative_comparison_observations[{index}].representation_status",
        )
        if status not in QUALITATIVE_STATUSES:
            raise VacuumPyrolysisComparisonError(
                f"unsupported qualitative status: {status!r}"
            )
        if representation not in REPRESENTATION_STATUSES:
            raise VacuumPyrolysisComparisonError(
                f"unsupported representation status: {representation!r}"
            )
        forbidden_numeric = {
            "expected_value",
            "actual_value",
            "residual",
            "score",
        }.intersection(observation)
        if forbidden_numeric:
            raise VacuumPyrolysisComparisonError(
                "qualitative observations cannot carry fake numerics: "
                f"{sorted(forbidden_numeric)}"
            )
        _mapping(
            observation.get("coordinate"),
            f"qualitative_comparison_observations[{index}].coordinate",
        )
        observations.append(observation)
    return observations


def _runtime_rows(runtime: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = runtime.get("per_hour_summary")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    return sorted(
        (
            row
            for row in rows
            if isinstance(row, Mapping) and _finite(row.get("hour"))
        ),
        key=lambda row: float(row["hour"]),
    )


def _final_o2_mass_kg(rows: Sequence[Mapping[str, Any]]) -> float | None:
    if not rows:
        return None
    return _optional_finite_float(
        rows[-1].get("O2_source_side_potential_kg_cumulative"),
        "O2_source_side_potential_kg_cumulative",
    )


def _window_o2_mass_kg(
    rows: Sequence[Mapping[str, Any]],
    coordinate: Mapping[str, Any],
) -> float | None:
    start_h, end_h = _coordinate_window(coordinate, rows)
    end_value = _cumulative_o2_at(rows, end_h)
    start_value = _cumulative_o2_at(rows, start_h)
    if end_value is None or start_value is None:
        return None
    return max(0.0, end_value - start_value)


def _cumulative_o2_at(
    rows: Sequence[Mapping[str, Any]],
    time_h: float,
) -> float | None:
    if math.isclose(time_h, 0.0, rel_tol=0.0, abs_tol=1e-12):
        return 0.0
    eligible = [row for row in rows if float(row["hour"]) <= time_h + 1e-12]
    if not eligible:
        return None
    return _optional_finite_float(
        eligible[-1].get("O2_source_side_potential_kg_cumulative"),
        "O2_source_side_potential_kg_cumulative",
    )


def _non_condensed_mass_kg(runtime: Mapping[str, Any]) -> float | None:
    final = runtime.get("final")
    if not isinstance(final, Mapping):
        return None
    outlet = final.get("pump_outlet_by_species_kg")
    if not isinstance(outlet, Mapping):
        return None
    values: list[float] = []
    for species, value in outlet.items():
        values.append(
            max(
                0.0,
                _required_finite_float(
                    value,
                    f"final.pump_outlet_by_species_kg.{species}",
                ),
            )
        )
    return sum(values)


def _species_time_series_value(
    rows: Sequence[Mapping[str, Any]],
    coordinate: Mapping[str, Any],
    *,
    species: str,
    aggregation: str,
) -> float | None:
    if "time_h" in coordinate:
        time_h = _required_finite_float(coordinate["time_h"], "coordinate.time_h")
        selected = [
            row
            for row in rows
            if math.isclose(
                float(row["hour"]),
                time_h,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ]
    else:
        start_h, end_h = _coordinate_window(coordinate, rows)
        selected = [
            row
            for row in rows
            if start_h < float(row["hour"]) <= end_h + 1e-12
        ]
    values = [
        float(row["vapor_species_kg_hr"][species])
        for row in selected
        if isinstance(row.get("vapor_species_kg_hr"), Mapping)
        and species in row["vapor_species_kg_hr"]
        and _finite(row["vapor_species_kg_hr"][species])
    ]
    if not values:
        return None
    if aggregation == "point":
        return values[-1] if len(values) == 1 else None
    if aggregation == "max":
        return max(values)
    if aggregation == "mean":
        return sum(values) / len(values)
    if aggregation == "sum":
        return sum(values)
    raise VacuumPyrolysisComparisonError(
        f"unsupported species time-series aggregation: {aggregation!r}"
    )


def _coordinate_window(
    coordinate: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> tuple[float, float]:
    start_h = _required_finite_float(
        coordinate.get("start_h", 0.0),
        "coordinate.start_h",
    )
    default_end_h = float(rows[-1]["hour"]) if rows else start_h
    end_h = _required_finite_float(
        coordinate.get("end_h", default_end_h),
        "coordinate.end_h",
    )
    if start_h < 0.0 or end_h < start_h:
        raise VacuumPyrolysisComparisonError(
            "coordinate window must satisfy 0 <= start_h <= end_h"
        )
    return start_h, end_h


def _feed_oxygen_kg(
    runtime: Mapping[str, Any],
    feedstocks: Mapping[str, Any],
) -> float:
    metadata = _mapping(runtime.get("run_metadata"), "runtime_result.run_metadata")
    feedstock_id = _required_text(
        metadata.get("feedstock_id"),
        "runtime_result.run_metadata.feedstock_id",
    )
    feedstock = _mapping(
        feedstocks.get(feedstock_id),
        f"feedstocks.{feedstock_id}",
    )
    composition = _mapping(
        feedstock.get("composition_wt_pct"),
        f"feedstocks.{feedstock_id}.composition_wt_pct",
    )
    oxygen_fraction = 0.0
    for species, raw_wt_pct in composition.items():
        wt_pct = _required_finite_float(
            raw_wt_pct,
            f"feedstocks.{feedstock_id}.composition_wt_pct.{species}",
        )
        formula = parse_formula(str(species))
        oxygen_atoms = float(formula.elements.get("O", 0.0))
        if oxygen_atoms <= 0.0:
            continue
        oxygen_mass_fraction = (
            oxygen_atoms * ATOMIC_WEIGHTS_G_PER_MOL["O"]
            / float(formula.molar_mass_g_mol)
        )
        oxygen_fraction += wt_pct / 100.0 * oxygen_mass_fraction
    return _runtime_mass_kg(runtime) * oxygen_fraction


def _runtime_mass_kg(runtime: Mapping[str, Any]) -> float:
    metadata = _mapping(runtime.get("run_metadata"), "runtime_result.run_metadata")
    return _required_finite_float(
        metadata.get("mass_kg"),
        "runtime_result.run_metadata.mass_kg",
    )


def _contains_assumed_inputs(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in {"source_class", "status"} and str(item) in {
                "assumed",
                "assumption_with_sensitivity_marker",
            }:
                return True
            if _contains_assumed_inputs(item):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_assumed_inputs(item) for item in value)
    return False


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise VacuumPyrolysisComparisonError(f"{field} must be a mapping")
    return dict(value)


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise VacuumPyrolysisComparisonError(f"{field} must be a list")
    return list(value)


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise VacuumPyrolysisComparisonError(f"{field} is required")
    return text


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _required_finite_float(value: Any, field: str) -> float:
    result = _optional_finite_float(value, field)
    if result is None:
        raise VacuumPyrolysisComparisonError(f"{field} must be finite")
    return result


def _optional_finite_float(value: Any, field: str) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise VacuumPyrolysisComparisonError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise VacuumPyrolysisComparisonError(f"{field} must be finite")
    return result
