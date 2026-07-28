"""Yu 2025 MRE selectors over the shared reproduction comparator."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from simulator.diagnostic_helpers.reproduction_compare import (
    COMPARISON_ARTIFACT_SCHEMA_VERSION,
    ComparisonRecord,
    compare_values,
    content_digest,
    records_to_markdown,
)


class MREComparisonError(ValueError):
    """Named refusal for invalid MRE comparison inputs."""


@dataclass(frozen=True)
class MREComparisonRun:
    records: tuple[ComparisonRecord, ...]
    qualitative_observations: tuple[Mapping[str, Any], ...]
    unsupported_observables: tuple[Mapping[str, Any], ...]
    recipe_digest: str
    source_digest: str
    result_digest: str
    controls_digest: str
    paper_id: str
    case_id: str
    measurement_id: str

    def as_payload(
        self,
        *,
        sidecar_path: str,
        markdown_path: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": COMPARISON_ARTIFACT_SCHEMA_VERSION,
            "domain": "mre",
            "preset_kind": "mre_reproduction",
            "execution_scope": "literature_reproduction_only",
            "paper_id": self.paper_id,
            "case_id": self.case_id,
            "measurement_id": self.measurement_id,
            "sidecar_path": str(sidecar_path),
            "markdown_path": str(markdown_path),
            "digests": {
                "recipe_sha256": self.recipe_digest,
                "source_sha256": self.source_digest,
                "result_sha256": self.result_digest,
                "controls_sha256": self.controls_digest,
            },
            "records": [record.as_dict() for record in self.records],
            "qualitative_observations": [
                copy.deepcopy(dict(observation))
                for observation in self.qualitative_observations
            ],
            "unsupported_observables": [
                copy.deepcopy(dict(observable))
                for observable in self.unsupported_observables
            ],
        }

    def markdown(self, *, comparison_artifact_path: Path) -> str:
        unsupported_lines = [
            f"- `{row['observable_id']}`: {row['reason']}"
            for row in self.unsupported_observables
        ]
        if not unsupported_lines:
            unsupported_lines = ["- None."]
        return "\n".join(
            [
                f"# MRE literature comparison: {self.paper_id} / {self.case_id}",
                "",
                f"Versioned comparison artifact: `{comparison_artifact_path}`",
                "",
                "Execution origin: `literature-reproduction`.",
                "Cell potential is a replay of the published measured response, "
                "not a model prediction.",
                "Yu's exterior-RGA collected O2 is not the same observable as "
                "source-side ledger O2.",
                "",
                records_to_markdown(self.records),
                "",
                "## Unsupported observables",
                "",
                *unsupported_lines,
                "",
                "## Content digests",
                "",
                f"- Recipe: `sha256:{self.recipe_digest}`",
                f"- Source: `sha256:{self.source_digest}`",
                f"- Result: `sha256:{self.result_digest}`",
                f"- Controls: `sha256:{self.controls_digest}`",
                "",
            ]
        )


def load_mre_observations(path: str | Path) -> dict[str, Any]:
    sidecar_path = Path(path)
    try:
        loaded = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MREComparisonError(
            f"cannot load MRE observations {sidecar_path}: {exc}"
        ) from exc
    return _mapping(loaded, str(sidecar_path))


def evaluate_mre_comparison(
    preset: Mapping[str, Any],
    observations: Mapping[str, Any],
    runtime_result: Mapping[str, Any],
) -> MREComparisonRun:
    recipe = _mapping(preset, "preset")
    sidecar = _mapping(observations, "observations")
    runtime = _mapping(runtime_result, "runtime_result")
    reproduction = _mapping(runtime.get("mre_reproduction"), "mre_reproduction")
    paper_id = _required_text(recipe.get("paper_id"), "paper_id")
    case_id = _required_text(reproduction.get("case_id"), "mre_reproduction.case_id")
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
        "paper_citation.citation_id",
    )
    if source_id != _required_text(
        recipe.get("paper_citation_id"),
        "paper_citation_id",
    ):
        raise MREComparisonError(
            "preset paper_citation_id does not match MRE observation source"
        )
    case = _mapping(
        _mapping(measurement.get("cases"), f"{measurement_id}.cases").get(case_id),
        f"{measurement_id}.cases.{case_id}",
    )
    observation_package = {
        "schema_version": sidecar.get("schema_version"),
        "measurement_id": measurement_id,
        "paper_citation": measurement.get("paper_citation"),
        "case_id": case_id,
        "case": case,
        "unsupported_observables": measurement.get("unsupported_observables"),
    }
    recipe_digest = content_digest(recipe)
    source_digest = content_digest(observation_package)
    result_digest = content_digest(runtime)
    controls_digest = _required_text(
        reproduction.get("controls_digest"),
        "mre_reproduction.controls_digest",
    )

    records: list[ComparisonRecord] = []
    for index, raw_point in enumerate(
        _sequence(case.get("comparison_points"), f"{case_id}.comparison_points")
    ):
        point = _mapping(raw_point, f"{case_id}.comparison_points[{index}]")
        observable_id = _required_text(
            point.get("observable_id"),
            f"{case_id}.comparison_points[{index}].observable_id",
        )
        expected_value = _optional_finite(
            point.get("expected_value"),
            f"{case_id}.{observable_id}.expected_value",
        )
        uncertainty = point.get("uncertainty")
        if expected_value is not None and not isinstance(uncertainty, Mapping):
            raise MREComparisonError(
                f"{case_id}.{observable_id} requires uncertainty"
            )
        actual_value = resolve_mre_observable(observable_id, point, reproduction)
        disposition = str(point.get("disposition") or "compare")
        records.append(
            compare_values(
                case_id=case_id,
                source_id=source_id,
                observable_id=observable_id,
                species=(
                    str(point["species"]) if point.get("species") is not None else None
                ),
                coordinate=_mapping(
                    point.get("coordinate"),
                    f"{case_id}.{observable_id}.coordinate",
                ),
                expected_value=expected_value,
                expected_uncertainty=(
                    dict(uncertainty) if isinstance(uncertainty, Mapping) else None
                ),
                actual_value=actual_value,
                units=_required_text(point.get("units"), f"{observable_id}.units"),
                evidence_scope=_required_text(
                    point.get("evidence_scope"),
                    f"{observable_id}.evidence_scope",
                ),
                source_locator=copy.deepcopy(point.get("source_locator") or {}),
                recipe=recipe,
                observation=observation_package,
                runtime=runtime,
                unsupported_speciation=disposition == "unsupported-speciation",
                assumed_input=disposition == "assumed-input",
                out_of_domain=disposition == "out-of-domain",
            )
        )

    qualitative = tuple(
        _resolve_qualitative_observation(row, reproduction)
        for row in _sequence(
            case.get("qualitative_observations"),
            f"{case_id}.qualitative_observations",
        )
    )
    unsupported = tuple(
        copy.deepcopy(
            _mapping(row, f"{measurement_id}.unsupported_observables[{index}]")
        )
        for index, row in enumerate(
            _sequence(
                measurement.get("unsupported_observables"),
                f"{measurement_id}.unsupported_observables",
            )
        )
    )
    return MREComparisonRun(
        records=tuple(records),
        qualitative_observations=qualitative,
        unsupported_observables=unsupported,
        recipe_digest=recipe_digest,
        source_digest=source_digest,
        result_digest=result_digest,
        controls_digest=controls_digest,
        paper_id=paper_id,
        case_id=case_id,
        measurement_id=measurement_id,
    )


def resolve_mre_observable(
    observable_id: str,
    point: Mapping[str, Any],
    reproduction: Mapping[str, Any],
) -> float | None:
    cumulative = _mapping(reproduction.get("cumulative"), "mre_reproduction.cumulative")
    intervals = _sequence(reproduction.get("intervals"), "mre_reproduction.intervals")
    kind = str(point.get("selector_kind") or observable_id)
    if kind == "mre_applied_charge_C":
        return _finite(cumulative.get("applied_charge_C"))
    if kind == "mre_applied_current_A":
        return _finite(intervals[-1].get("applied_current_A")) if intervals else None
    if kind == "mre_effective_current_A":
        duration_s = sum(_finite(row.get("dt_h")) or 0.0 for row in intervals) * 3600.0
        charge = _finite(cumulative.get("committed_electron_charge_C"))
        return charge / duration_s if charge is not None and duration_s > 0.0 else None
    if kind == "mre_faradaic_efficiency_fraction":
        applied = _finite(cumulative.get("applied_charge_C"))
        committed = _finite(cumulative.get("committed_electron_charge_C"))
        return (
            committed / applied
            if committed is not None and applied is not None and applied > 0.0
            else None
        )
    if kind in {"mre_anode_o2_mass_kg", "collected_rga_o2_mass_kg"}:
        return _finite(cumulative.get("mre_anode_o2_kg"))
    if kind == "cell_potential_V":
        return None
    if kind == "mre_extraction_efficiency_fraction":
        return None
    if kind.startswith("cathodic_eds_atomic_fraction"):
        return None
    if kind in {"outlet_o2_volume_fraction", "hollow_anode_transport_efficiency"}:
        return None
    raise MREComparisonError(f"unsupported MRE selector kind: {kind!r}")


def _resolve_qualitative_observation(
    value: Any,
    reproduction: Mapping[str, Any],
) -> dict[str, Any]:
    row = _mapping(value, "qualitative_observation")
    element = _required_text(row.get("element"), "qualitative_observation.element")
    observed = bool(row.get("observed"))
    metals = _mapping(
        _mapping(reproduction.get("cumulative"), "mre_reproduction.cumulative").get(
            "metals_mol_by_species"
        ),
        "mre_reproduction.cumulative.metals_mol_by_species",
    )
    if element == "P":
        representation_status = "not-representable"
        runtime_present = None
        status = "unsupported-speciation"
    else:
        representation_status = "represented"
        runtime_present = float(metals.get(element, 0.0)) > 1e-12
        comparison = "match" if runtime_present == observed else "mismatch"
        if element == "Mn":
            status = "out-of-domain"
        else:
            status = comparison
    resolved = {
        **copy.deepcopy(row),
        "runtime_present": runtime_present,
        "representation_status": representation_status,
        "status": status,
        "residual": None,
    }
    if element == "Mn":
        resolved.update(
            {
                "authority_disposition": "diagnostic-only",
                "diagnostic_comparison": comparison,
            }
        )
    return resolved


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MREComparisonError(f"{field} must be a mapping")
    return dict(value)


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MREComparisonError(f"{field} must be a sequence")
    return list(value)


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MREComparisonError(f"{field} is required")
    return text


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_finite(value: Any, field: str) -> float | None:
    if value is None:
        return None
    number = _finite(value)
    if number is None:
        raise MREComparisonError(f"{field} must be finite")
    return number
